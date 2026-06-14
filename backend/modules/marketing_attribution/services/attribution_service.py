"""
Marketing Attribution service — campaign-driven media-buyer attribution (read-only).

Data source: crm.lead, utm.campaign, crm.stage via the shared read-only
OdooClient. Every query uses context={'active_test': False} so archived
(Lost/closed) leads are included — board-level outcome analysis must count the
poor outcomes, not hide them (§3.6; discovery §2: archived = 58.6% of leads).

No method ever calls create / write / unlink. _assert_read_only() runs at entry.

Algorithm (LOCKED — §3.2/§3.3/§3.4/§3.5/§3.7; amendments A1/A3/A6/A7):
  1. Resolve the two configured gates (campaign NAMES) to campaign id SETS
     against the live utm.campaign table. A name may match >1 record -> union
     all ids and warn (A3). A name matching 0 records -> warn and ignore.
  2. Derive the campaign->buyer map from BOTH-SET leads (campaign_id AND
     media_buyer_id set): dominant buyer = the media_buyer_id with the most
     both-set leads in that campaign; concentration = dominant / both-set total.
     qualifies(C) iff concentration >= 0.90 (integer-exact at the boundary).
  3. GATE (A1): attributing_ids = { C in confirmed_ids : qualifies(C)
     AND C not in denylist_ids }. A confirmed campaign that fails qualifies()
     or resolves into the denylist is NOT attributed and raises an integrity
     alert (locked-decision drift — surfaced loudly, logged at ERROR).
  4. Attribute ALL leads of each attributing campaign to its DERIVED dominant
     buyer — regardless of whether the individual lead has media_buyer_id set
     (this produces the inferred attribution on top of the recorded; A6).
  5. Per buyer: total attributed + the 4-group outcome breakdown (§3.7), counts
     reconciled to the total with an explicit raise (A7; survives python -O).
  6. Pending campaigns (§3.5): qualify, not denied, not confirmed -> surfaced,
     NOT attributed.
"""

import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from loguru import logger

from backend.core.exceptions import OdooQueryError, ReadOnlyViolationError
from backend.modules.marketing_attribution import domain
from backend.modules.marketing_attribution.domain import (
    BUYER_FIELD,
    CAMPAIGN_FIELD,
    GROUP_ORDER,
    classify_stage,
)
from backend.modules.marketing_attribution.services import cache as _cache
from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient

# Methods that must never appear in ALLOWED_METHODS.
_FORBIDDEN_WRITE_METHODS = frozenset({"create", "write", "unlink"})

_LEAD_MODEL = "crm.lead"
_CAMPAIGN_MODEL = "utm.campaign"
_STAGE_MODEL = "crm.stage"

_CACHE_KEY_PREFIX = "marketing_attribution:overview"
_CAIRO_TZ = ZoneInfo("Africa/Cairo")

# All counts include archived leads (board attribution must include Lost/closed).
_CTX_ALL = {"active_test": False}


def _assert_read_only() -> None:
    """Defense-in-depth: abort if any write method has leaked into ALLOWED_METHODS."""
    violations = ALLOWED_METHODS & _FORBIDDEN_WRITE_METHODS
    if violations:
        raise ReadOnlyViolationError(
            f"ALLOWED_METHODS contains forbidden write method(s): {sorted(violations)}. "
            "The Odoo client is no longer strictly read-only. Halting before any RPC."
        )


def _m2o(value) -> tuple[Optional[int], Optional[str]]:
    """Render an Odoo many2one [id, name] (or False) as (id, name) or (None, None)."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return int(value[0]), str(value[1])
    return None, None


def _qualifies(dominant_count: int, both_set_count: int) -> bool:
    """concentration >= 0.90, integer-exact at the boundary (9/10 qualifies)."""
    if both_set_count <= 0:
        return False
    return dominant_count * 100 >= both_set_count * 90


async def get_attribution_overview(
    client: Optional[OdooClient] = None,
    confirmed_campaigns: Optional[frozenset[str]] = None,
    denylist_campaigns: Optional[frozenset[str]] = None,
) -> dict:
    """Return the campaign-driven media-buyer attribution overview.

    Args:
        client: optional injected OdooClient (tests pass a mock; production opens
            and closes its own).
        confirmed_campaigns / denylist_campaigns: optional gate overrides for
            tests. When BOTH are None the production domain constants are used
            and the result is cached; when either is provided the cache is
            bypassed (so a test config never poisons the production cache key).

    Returns a dict matching schemas.MarketingAttributionOverview.

    Raises:
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated.
        OdooQueryError: if any Odoo RPC fails.
        RuntimeError: if a buyer's 4 group counts fail to reconcile to the total
            (A7 — explicit raise so it survives python -O).
    """
    _assert_read_only()

    default_config = confirmed_campaigns is None and denylist_campaigns is None
    confirmed_names = (
        confirmed_campaigns if confirmed_campaigns is not None
        else domain.CONFIRMED_BUYER_CAMPAIGNS
    )
    denylist_names = (
        denylist_campaigns if denylist_campaigns is not None
        else domain.DENYLIST_CAMPAIGNS
    )

    cairo_today = datetime.now(_CAIRO_TZ).date()
    cache_key = _cache.make_key(_CACHE_KEY_PREFIX)
    if default_config:
        cached = _cache.get(cache_key)
        if cached is not None:
            logger.debug(f"Cache hit: {cache_key}")
            return {**cached, "cache_status": "cached", "rpc_duration_ms": 0}
        logger.info(f"Cache miss: {cache_key} — querying Odoo")

    _client = client if client is not None else OdooClient()

    t0 = time.monotonic()
    try:
        # ── RPC 1 — utm.campaign id+name (resolve the gates) ──────────────────
        campaigns = await _client.execute_kw(
            _CAMPAIGN_MODEL,
            "search_read",
            args=[[]],
            kwargs={"fields": ["id", "name"], "context": _CTX_ALL},
        )

        # ── RPC 2 — BOTH-SET leads, grouped by (campaign_id, media_buyer_id) ──
        both_set_rows = await _client.execute_kw(
            _LEAD_MODEL,
            "read_group",
            args=[
                [(CAMPAIGN_FIELD, "!=", False), (BUYER_FIELD, "!=", False)],
                [CAMPAIGN_FIELD, BUYER_FIELD],
                [CAMPAIGN_FIELD, BUYER_FIELD],
            ],
            kwargs={"context": _CTX_ALL, "lazy": False},
        )

        # ── RPC 3 — ALL leads grouped by campaign_id (lead_count + population) ─
        all_by_campaign_rows = await _client.execute_kw(
            _LEAD_MODEL,
            "read_group",
            args=[[], [CAMPAIGN_FIELD], [CAMPAIGN_FIELD]],
            kwargs={"context": _CTX_ALL, "lazy": False},
        )

        # ── RPC 4 — crm.stage id+name+is_won (outcome group mapping) ──────────
        stages = await _client.execute_kw(
            _STAGE_MODEL,
            "search_read",
            args=[[]],
            kwargs={"fields": ["id", "name", "is_won"]},
        )

        # ── Build name->ids, id->name (A3: a name may match >1 record) ────────
        name_to_ids: dict[str, list[int]] = defaultdict(list)
        id_to_name: dict[int, str] = {}
        for c in campaigns:
            cid = int(c["id"])
            cname = str(c.get("name") or "")
            id_to_name[cid] = cname
            name_to_ids[cname].append(cid)

        config_warnings: list[str] = []

        def _resolve(names: frozenset[str], label: str) -> set[int]:
            resolved: set[int] = set()
            for nm in sorted(names):
                ids = name_to_ids.get(nm, [])
                if not ids:
                    config_warnings.append(
                        f"{label} campaign name {nm!r} did not resolve to any "
                        f"utm.campaign record — ignored."
                    )
                    continue
                if len(ids) > 1:
                    config_warnings.append(
                        f"{label} campaign name {nm!r} matched {len(ids)} "
                        f"utm.campaign records (ids {sorted(ids)}) — all included."
                    )
                resolved.update(ids)
            return resolved

        confirmed_ids = _resolve(confirmed_names, "Confirmed")
        denylist_ids = _resolve(denylist_names, "Denylist")

        # ── Lead count per campaign + total population (from RPC 3) ───────────
        lead_count_by_campaign: dict[int, int] = {}
        total_leads_population = 0
        for r in all_by_campaign_rows:
            cnt = int(r.get("__count") or 0)
            total_leads_population += cnt
            cid, _ = _m2o(r.get(CAMPAIGN_FIELD))
            if cid is not None:
                lead_count_by_campaign[cid] = lead_count_by_campaign.get(cid, 0) + cnt

        # ── Derive the campaign->buyer map (from RPC 2) ───────────────────────
        # campaign_map[cid] = {"both_set": int, "buyers": Counter(id->cnt),
        #                      "buyer_names": {id: name}}
        campaign_map: dict[int, dict] = {}
        for r in both_set_rows:
            cid, _ = _m2o(r.get(CAMPAIGN_FIELD))
            if cid is None:
                continue
            bid, bname = _m2o(r.get(BUYER_FIELD))
            if bid is None:
                continue
            cnt = int(r.get("__count") or 0)
            entry = campaign_map.setdefault(
                cid, {"both_set": 0, "buyers": Counter(), "buyer_names": {}}
            )
            entry["both_set"] += cnt
            entry["buyers"][bid] += cnt
            entry["buyer_names"][bid] = bname

        def _dominant(cid: int) -> tuple[Optional[int], Optional[str], int, int]:
            """(buyer_id, buyer_name, dominant_count, both_set_count) for a campaign."""
            entry = campaign_map.get(cid)
            if not entry or not entry["buyers"]:
                return None, None, 0, 0
            bid, dom_cnt = entry["buyers"].most_common(1)[0]
            return bid, entry["buyer_names"].get(bid), dom_cnt, entry["both_set"]

        # ── GATE (A1) — compute attributing_ids BEFORE the attribution RPC ────
        integrity_alerts: list[str] = []
        attributing_ids: set[int] = set()
        for cid in sorted(confirmed_ids):
            cname = id_to_name.get(cid, f"id={cid}")
            if cid in denylist_ids:
                integrity_alerts.append(
                    f"INTEGRITY: confirmed campaign {cname!r} (id={cid}) also "
                    f"resolves into the DENYLIST — NOT attributed. Locked-decision "
                    f"drift: a campaign cannot be both confirmed and denied."
                )
                continue
            bid, bname, dom_cnt, both = _dominant(cid)
            if both == 0:
                integrity_alerts.append(
                    f"INTEGRITY: confirmed campaign {cname!r} (id={cid}) has NO "
                    f"both-set leads — cannot verify >=90% concentration. "
                    f"NOT attributed."
                )
                continue
            if not _qualifies(dom_cnt, both):
                pct = 100.0 * dom_cnt / both
                integrity_alerts.append(
                    f"INTEGRITY: confirmed campaign {cname!r} (id={cid}) "
                    f"concentration {pct:.1f}% < 90% (dominant buyer {bname!r}, "
                    f"{dom_cnt}/{both} both-set) — NOT attributed. Locked decision "
                    f"says a confirmed campaign must hold >=90%."
                )
                continue
            attributing_ids.add(cid)

        for alert in integrity_alerts:
            logger.error(alert)

        # ── RPC 5 — attribution: leads grouped by (campaign_id, stage_id) ─────
        # Filtered to the GATED set (A1). Skipped entirely if nothing attributes.
        attrib_rows: list = []
        if attributing_ids:
            attrib_rows = await _client.execute_kw(
                _LEAD_MODEL,
                "read_group",
                args=[
                    [(CAMPAIGN_FIELD, "in", sorted(attributing_ids))],
                    [CAMPAIGN_FIELD, "stage_id"],
                    [CAMPAIGN_FIELD, "stage_id"],
                ],
                kwargs={"context": _CTX_ALL, "lazy": False},
            )
    except ReadOnlyViolationError:
        raise
    except Exception as exc:
        raise OdooQueryError(
            f"get_attribution_overview() RPC failed: {exc}"
        ) from exc
    finally:
        if client is None:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        f"Marketing attribution: RPCs in {rpc_ms}ms | "
        f"confirmed={len(confirmed_ids)} attributing={len(attributing_ids)} "
        f"pending=? alerts={len(integrity_alerts)} | cache_key={cache_key}"
    )

    # ── Stage info for group mapping (from RPC 4) ─────────────────────────────
    stage_info: dict[int, dict] = {}
    is_won_stage_names: list[str] = []
    for s in stages:
        sid = int(s["id"])
        sname = str(s.get("name") or "")
        is_won = bool(s.get("is_won"))
        stage_info[sid] = {"name": sname, "is_won": is_won}
        if is_won:
            is_won_stage_names.append(sname)
    is_won_stage_names.sort()

    # ── Aggregate attribution per derived dominant buyer (from RPC 5) ─────────
    # buyer_agg[bid] = {"name", "total", "groups": {g:int}, "campaign_ids": set}
    buyer_agg: dict[int, dict] = {}
    for r in attrib_rows:
        cid, _ = _m2o(r.get(CAMPAIGN_FIELD))
        if cid is None or cid not in attributing_ids:
            continue
        bid, bname, _, _ = _dominant(cid)
        if bid is None:
            continue
        sid, _ = _m2o(r.get("stage_id"))   # stage_id may be False -> None -> جديد
        cnt = int(r.get("__count") or 0)
        group = classify_stage(sid, stage_info)
        b = buyer_agg.setdefault(
            bid,
            {
                "name": bname,
                "total": 0,
                "groups": {g: 0 for g in GROUP_ORDER},
                "campaign_ids": set(),
            },
        )
        b["total"] += cnt
        b["groups"][group] += cnt
        b["campaign_ids"].add(cid)

    # ── Build BuyerRow list with reconciliation (A7) ──────────────────────────
    buyers: list[dict] = []
    total_attributed = 0
    for bid, b in buyer_agg.items():
        total = b["total"]
        group_sum = sum(b["groups"].values())
        if group_sum != total:
            raise RuntimeError(
                f"Outcome reconciliation FAILED for buyer {b['name']!r} "
                f"(id={bid}): group sum {group_sum} != total {total}. "
                f"Refusing to return inconsistent attribution."
            )
        outcomes = [
            {
                "group": g,
                "count": b["groups"][g],
                "pct": round(100.0 * b["groups"][g] / total, 2) if total else 0.0,
            }
            for g in GROUP_ORDER
        ]
        buyers.append(
            {
                "buyer_id": bid,
                "buyer_name": b["name"] or "",
                "total_attributed": total,
                "outcomes": outcomes,
                "campaign_ids": sorted(b["campaign_ids"]),
            }
        )
        total_attributed += total
    buyers.sort(key=lambda x: (-x["total_attributed"], x["buyer_name"]))

    # ── Confirmed-campaign detail (transparency / A2 / A5 support) ────────────
    confirmed_campaigns_out: list[dict] = []
    for cid in sorted(attributing_ids):
        bid, bname, dom_cnt, both = _dominant(cid)
        confirmed_campaigns_out.append(
            {
                "campaign_id": cid,
                "campaign_name": id_to_name.get(cid, f"id={cid}"),
                "dominant_buyer_id": bid if bid is not None else 0,
                "dominant_buyer_name": bname or "",
                "concentration": round(100.0 * dom_cnt / both, 2) if both else 0.0,
                "both_set_count": both,
                "lead_count": lead_count_by_campaign.get(cid, 0),
            }
        )
    confirmed_campaigns_out.sort(key=lambda x: (-x["lead_count"], x["campaign_name"]))

    # ── Pending campaigns (§3.5): qualify, not denied, not confirmed ──────────
    pending_campaigns_out: list[dict] = []
    for cid, entry in campaign_map.items():
        if cid in confirmed_ids or cid in denylist_ids:
            continue
        bid, bname, dom_cnt, both = _dominant(cid)
        if both == 0 or not _qualifies(dom_cnt, both):
            continue
        pending_campaigns_out.append(
            {
                "campaign_id": cid,
                "campaign_name": id_to_name.get(cid, f"id={cid}"),
                "dominant_buyer_id": bid if bid is not None else 0,
                "dominant_buyer_name": bname or "",
                "concentration": round(100.0 * dom_cnt / both, 2),
                "both_set_count": both,
                "lead_count": lead_count_by_campaign.get(cid, 0),
            }
        )
    pending_campaigns_out.sort(key=lambda x: (-x["lead_count"], x["campaign_name"]))

    attribution_pct = (
        round(100.0 * total_attributed / total_leads_population, 2)
        if total_leads_population else 0.0
    )

    result: dict = {
        "buyers": buyers,
        "confirmed_campaigns": confirmed_campaigns_out,
        "pending_campaigns": pending_campaigns_out,
        "total_leads_population": total_leads_population,
        "total_attributed": total_attributed,
        "attribution_pct": attribution_pct,
        "is_won_stage_names": is_won_stage_names,
        "config_warnings": config_warnings,
        "integrity_alerts": integrity_alerts,
        "reference_date": cairo_today.isoformat(),
        "as_of": datetime.now(timezone.utc).isoformat(),
        "cache_status": "fresh",
        "rpc_duration_ms": rpc_ms,
    }

    if default_config:
        _cache.set(cache_key, result)
    return result
