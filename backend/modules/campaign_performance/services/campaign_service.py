"""
Campaign Performance service — per-campaign funnel (Level 1, read-only).

Data source: crm.lead, utm.campaign, crm.stage via the shared read-only
OdooClient. Every query uses context={'active_test': False} so archived
(Lost/closed) leads are included — board-level outcome analysis must count the
poor outcomes, not hide them. Population is byte-identical to
marketing_attribution.

No method ever calls create / write / unlink. _assert_read_only() runs at entry.

Algorithm (Level 1):
  1. Resolve the CONFIRMED / DENYLIST gate NAMES (imported from
     marketing_attribution) to campaign id SETS against the live utm.campaign
     table (a name may match >1 record -> union + warn; 0 records -> warn).
     Resolve the junk JUNK_CAMPAIGN_NAMES ("None") to its id(s).
  2. lead_count per campaign + total population (read_group by campaign_id).
  3. Per-campaign 4-group funnel (read_group by campaign_id+stage_id, classified
     via the IMPORTED classify_stage so the stage-group counts are identical to
     the shipped module and reconcile 1:1).
  4. Per-campaign dominant media buyer + concentration from BOTH-SET leads (leads
     with BOTH campaign_id AND media_buyer_id) — the SAME both-set grouping +
     integer-exact concentration the shipped module uses to build its confirmed
     set (RPC 2 there). DISPLAY rule (§7.1, amended):
        denylist                                  -> excluded_channel (buyer suppressed)
        no both-set leads                          -> no_buyer
        confirmed set AND >=90% AND >=min sample   -> confirmed (buyer shown)
        >= floor (50%)  AND >=min sample           -> dominant   (buyer shown)
        otherwise (has buyer leads)                -> mixed       (no single buyer)
     A confirmed campaign that no longer holds >=90% raises an integrity alert
     (locked-decision drift) and is shown as non-confirmed.
  5. Sort real campaigns by lead volume desc; rows at/above min_lead_threshold are
     listed individually, the rest roll into one aggregated long_tail. The junk
     "None" campaign and the no-campaign bucket (campaign_id=False) are surfaced
     as DATA-QUALITY flags, never as list rows.
  6. Reconcile (explicit raise; survives python -O): every funnel's 4 groups sum
     to its lead_count, and listed + long_tail + junk + no_campaign == population.

This is a DISPLAY view; it does NOT change marketing_attribution's strict
attribution metric.
"""

import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from loguru import logger

from backend.core.exceptions import OdooQueryError, ReadOnlyViolationError
from backend.modules.campaign_performance import domain
from backend.modules.campaign_performance.domain import (
    BUYER_FIELD,
    CAMPAIGN_FIELD,
    DEFAULT_MIN_LEAD_THRESHOLD,
    GROUP_ORDER,
    JUNK_CAMPAIGN_NAMES,
    classify_stage,
)
from backend.modules.campaign_performance.services import cache as _cache
from backend.modules.campaign_performance.services.buyer import derive_buyer_status
from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient

# Methods that must never appear in ALLOWED_METHODS.
_FORBIDDEN_WRITE_METHODS = frozenset({"create", "write", "unlink"})

_LEAD_MODEL = "crm.lead"
_CAMPAIGN_MODEL = "utm.campaign"
_STAGE_MODEL = "crm.stage"

_CACHE_KEY_PREFIX = "campaign_performance:overview"
_CAIRO_TZ = ZoneInfo("Africa/Cairo")

# All counts include archived leads (board analysis must include Lost/closed).
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


def _outcomes(group_counts: dict[str, int], total: int, label: str) -> list[dict]:
    """Build the 4-group outcomes list (count + %), asserting reconciliation (A7).

    Raises RuntimeError (not assert — survives python -O) if the 4 groups do not
    sum to total, refusing to return an inconsistent funnel.
    """
    group_sum = sum(group_counts.get(g, 0) for g in GROUP_ORDER)
    if group_sum != total:
        raise RuntimeError(
            f"Outcome reconciliation FAILED for {label}: group sum {group_sum} "
            f"!= total {total}. Refusing to return an inconsistent funnel."
        )
    return [
        {
            "group": g,
            "count": group_counts.get(g, 0),
            "pct": round(100.0 * group_counts.get(g, 0) / total, 2) if total else 0.0,
        }
        for g in GROUP_ORDER
    ]


async def get_campaign_performance_overview(
    client: Optional[OdooClient] = None,
    min_lead_threshold: Optional[int] = None,
    confirmed_campaigns: Optional[frozenset[str]] = None,
    denylist_campaigns: Optional[frozenset[str]] = None,
) -> dict:
    """Return the per-campaign performance overview (Level 1).

    Args:
        client: optional injected OdooClient (tests pass a mock; production opens
            and closes its own).
        min_lead_threshold: optional long-tail volume cut override.
        confirmed_campaigns / denylist_campaigns: optional gate overrides for
            tests. When ALL three overrides are None the production domain
            constants + default threshold are used and the result is cached; when
            any is provided the cache is bypassed (so a test config never poisons
            the production cache key — same pattern as the shipped module).

    Returns a dict matching schemas.CampaignPerformanceOverview.

    Raises:
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated.
        OdooQueryError: if any Odoo RPC fails.
        RuntimeError: if any funnel's 4 group counts fail to reconcile to its total.
    """
    _assert_read_only()

    default_config = (
        min_lead_threshold is None
        and confirmed_campaigns is None
        and denylist_campaigns is None
    )
    threshold = DEFAULT_MIN_LEAD_THRESHOLD if min_lead_threshold is None else int(min_lead_threshold)
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
        # ── RPC 1 — utm.campaign id+name (resolve gates + junk label) ─────────
        campaigns = await _client.execute_kw(
            _CAMPAIGN_MODEL,
            "search_read",
            args=[[]],
            kwargs={"fields": ["id", "name"], "context": _CTX_ALL},
        )

        # ── RPC 2 — ALL leads grouped by campaign_id (lead_count + population) ─
        all_by_campaign_rows = await _client.execute_kw(
            _LEAD_MODEL,
            "read_group",
            args=[[], [CAMPAIGN_FIELD], [CAMPAIGN_FIELD]],
            kwargs={"context": _CTX_ALL, "lazy": False},
        )

        # ── RPC 3 — ALL leads grouped by (campaign_id, stage_id) (the funnel) ─
        by_campaign_stage_rows = await _client.execute_kw(
            _LEAD_MODEL,
            "read_group",
            args=[[], [CAMPAIGN_FIELD, "stage_id"], [CAMPAIGN_FIELD, "stage_id"]],
            kwargs={"context": _CTX_ALL, "lazy": False},
        )

        # ── RPC 4 — crm.stage id+name+is_won (outcome group mapping) ──────────
        stages = await _client.execute_kw(
            _STAGE_MODEL,
            "search_read",
            args=[[]],
            kwargs={"fields": ["id", "name", "is_won"]},
        )

        # ── RPC 5 — BOTH-SET leads grouped by (campaign_id, media_buyer_id) ───
        # The SAME grouping the shipped module uses to derive dominant buyers.
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
    except ReadOnlyViolationError:
        raise
    except Exception as exc:
        raise OdooQueryError(
            f"get_campaign_performance_overview() RPC failed: {exc}"
        ) from exc
    finally:
        if client is None:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)

    # ── Build name<->id maps (a name may match >1 record) ─────────────────────
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
    junk_ids = {cid for cid, nm in id_to_name.items() if nm in JUNK_CAMPAIGN_NAMES}

    # ── lead_count per campaign + total population (RPC 2) ────────────────────
    lead_count: dict[Optional[int], int] = {}
    total_leads_population = 0
    for r in all_by_campaign_rows:
        cnt = int(r.get("__count") or 0)
        total_leads_population += cnt
        cid, _ = _m2o(r.get(CAMPAIGN_FIELD))   # cid is None for the no-campaign bucket
        lead_count[cid] = lead_count.get(cid, 0) + cnt

    # ── stage info + is_won names (RPC 4) ─────────────────────────────────────
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

    # ── per-campaign 4-group funnel (RPC 3 + imported classify_stage) ─────────
    funnel: dict[Optional[int], dict[str, int]] = defaultdict(
        lambda: {g: 0 for g in GROUP_ORDER}
    )
    for r in by_campaign_stage_rows:
        cid, _ = _m2o(r.get(CAMPAIGN_FIELD))
        sid, _ = _m2o(r.get("stage_id"))       # stage_id may be False -> None -> جديد
        cnt = int(r.get("__count") or 0)
        funnel[cid][classify_stage(sid, stage_info)] += cnt

    # ── per-campaign dominant buyer from BOTH-SET leads (RPC 5) ───────────────
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

    integrity_alerts: list[str] = []

    def _status_and_buyer(cid: int) -> tuple[str, Optional[int], Optional[str], Optional[float], int]:
        """Determine (status, buyer_id, buyer_name, concentration, both_set_count)
        for a campaign via the shared buyer.derive_buyer_status helper (§7.1).
        Collects any confirmed-drift alert it returns into integrity_alerts."""
        entry = campaign_map.get(cid)
        if entry:
            buyers, buyer_names, both = entry["buyers"], entry["buyer_names"], entry["both_set"]
        else:
            buyers, buyer_names, both = Counter(), {}, 0
        status, bid, bname, conc, both_out, alert = derive_buyer_status(
            cid,
            id_to_name.get(cid, cid),
            buyers,
            buyer_names,
            both,
            is_confirmed=cid in confirmed_ids,
            is_denylisted=cid in denylist_ids,
        )
        if alert is not None:
            integrity_alerts.append(alert)
        return status, bid, bname, conc, both_out

    # ── classify every real campaign (also surfaces confirmed-drift alerts) ───
    all_ids_with_leads = [c for c in lead_count if c is not None]
    real_ids = [c for c in all_ids_with_leads if c not in junk_ids]
    status_map: dict[int, tuple] = {cid: _status_and_buyer(cid) for cid in sorted(real_ids)}

    # confirmed campaigns entirely absent from the population (no leads) — drift.
    for cid in sorted(confirmed_ids):
        if cid not in lead_count:
            integrity_alerts.append(
                f"INTEGRITY: confirmed campaign {id_to_name.get(cid, cid)!r} "
                f"(id={cid}) has NO leads in the population — cannot display."
            )
    for alert in integrity_alerts:
        logger.error(alert)

    # ── rank real campaigns by lead volume; split at the threshold ────────────
    ranked = sorted(real_ids, key=lambda c: (-lead_count[c], id_to_name.get(c, "")))
    listed_ids = [c for c in ranked if lead_count[c] >= threshold]
    tail_ids = [c for c in ranked if lead_count[c] < threshold]

    campaigns_out: list[dict] = []
    for cid in listed_ids:
        status, bid, bname, conc, both = status_map[cid]
        campaigns_out.append(
            {
                "campaign_id": cid,
                "campaign_name": id_to_name.get(cid, f"id={cid}"),
                "lead_count": lead_count[cid],
                "outcomes": _outcomes(funnel[cid], lead_count[cid], f"campaign id={cid}"),
                "attribution_status": status,
                "media_buyer_id": bid,
                "media_buyer_name": bname,
                "concentration": conc,
                "both_set_count": both,
            }
        )

    # ── long tail (aggregate of below-threshold campaigns) ────────────────────
    long_tail_out: Optional[dict] = None
    long_tail_leads = 0
    if tail_ids:
        tail_groups = {g: 0 for g in GROUP_ORDER}
        for cid in tail_ids:
            for g in GROUP_ORDER:
                tail_groups[g] += funnel[cid][g]
            long_tail_leads += lead_count[cid]
        long_tail_out = {
            "campaign_count": len(tail_ids),
            "lead_count": long_tail_leads,
            "outcomes": _outcomes(tail_groups, long_tail_leads, "long_tail"),
        }

    # ── data-quality buckets (NOT list rows) ──────────────────────────────────
    junk_present = [c for c in junk_ids if c in lead_count]
    junk_out: Optional[dict] = None
    junk_leads = 0
    if junk_present:
        junk_groups = {g: 0 for g in GROUP_ORDER}
        for cid in junk_present:
            for g in GROUP_ORDER:
                junk_groups[g] += funnel[cid][g]
            junk_leads += lead_count[cid]
        junk_out = {
            "label": "None",
            "campaign_ids": sorted(junk_present),
            "lead_count": junk_leads,
            "outcomes": _outcomes(junk_groups, junk_leads, "junk None campaign"),
        }

    no_campaign_out: Optional[dict] = None
    no_campaign_leads = lead_count.get(None, 0)
    if no_campaign_leads:
        no_campaign_out = {
            "label": "(no campaign)",
            "campaign_ids": [],
            "lead_count": no_campaign_leads,
            "outcomes": _outcomes(funnel[None], no_campaign_leads, "no-campaign bucket"),
        }

    # ── global reconciliation (explicit raise) ────────────────────────────────
    listed_leads = sum(lead_count[c] for c in listed_ids)
    recon_total = listed_leads + long_tail_leads + junk_leads + no_campaign_leads
    if recon_total != total_leads_population:
        raise RuntimeError(
            f"Population reconciliation FAILED: listed {listed_leads} + long_tail "
            f"{long_tail_leads} + junk {junk_leads} + no_campaign {no_campaign_leads} "
            f"= {recon_total} != population {total_leads_population}."
        )

    logger.info(
        f"Campaign performance: {len(real_ids)} real campaigns "
        f"(listed={len(listed_ids)} tail={len(tail_ids)}) | RPCs in {rpc_ms}ms | "
        f"alerts={len(integrity_alerts)} warnings={len(config_warnings)} | "
        f"cache_key={cache_key}"
    )

    result: dict = {
        "campaigns": campaigns_out,
        "long_tail": long_tail_out,
        "data_quality": {"junk_none": junk_out, "no_campaign": no_campaign_out},
        "min_lead_threshold": threshold,
        "total_leads_population": total_leads_population,
        "total_campaigns_with_leads": len(all_ids_with_leads),
        "listed_campaign_count": len(listed_ids),
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
