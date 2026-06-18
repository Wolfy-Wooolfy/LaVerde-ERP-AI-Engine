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
# Window primitives + the dated-window contract are REUSED VERBATIM from the campaign
# performance windowing (single source of truth for Cairo bucketing, the custom-range
# validation contract, and dynamic legacy-migration detection) — never re-declared
# here. One-way import: campaign_performance imports marketing_attribution.domain (not
# this service), so this introduces NO cycle. The buyer→campaign attribution map stays
# all-time (RPC 4 below); only the LEADS feeding the funnel are windowed.
from backend.modules.campaign_performance.domain import (
    DEFAULT_WINDOW,
    WINDOW_CUSTOM,
    WINDOW_PRESET_MONTHS,
)
# The all-time grand-coverage footer mirrors the campaign grand-totals footer
# (f8f27bf): the incl/excl-migration split is built from the SAME positive
# legacy-days OR-domain (_legacy_days_domain) and the SAME reconciling group+pct
# helper (_outcomes). Both are reused VERBATIM so the migration slice and the
# funnel shape are byte-identical to the campaign page (single source of truth);
# campaign_service does not import this service, so this one-way import adds no cycle.
from backend.modules.campaign_performance.services.campaign_service import (
    _GRAND_TTL_SECONDS,
    _legacy_days_domain,
    _outcomes,
)
from backend.modules.campaign_performance.services.timeline_service import (
    InvalidTimelineRangeError,
    _month_range,
    _month_str,
    _resolve_custom_window,
    _shift_months,
    _to_cairo,
    get_legacy_migration_days,
)
from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient

# Methods that must never appear in ALLOWED_METHODS.
_FORBIDDEN_WRITE_METHODS = frozenset({"create", "write", "unlink"})

_LEAD_MODEL = "crm.lead"
_CAMPAIGN_MODEL = "utm.campaign"
_STAGE_MODEL = "crm.stage"

_CACHE_KEY_PREFIX = "marketing_attribution:overview"
_CACHE_KEY_PREFIX_WINDOWED = "marketing_attribution:windowed"
# The pinned all-time grand-coverage footer re-reads the cached overview plus two
# tiny migration read-aggregations, yet loads on every page view while the figures
# only change ~daily. Hold it for an hour (the SAME TTL the campaign grand-totals
# footer uses, imported as the single source of truth). The cache key is Cairo-date
# stamped, so it still refreshes at most once per Cairo day.
_CACHE_KEY_PREFIX_GRAND = "marketing_attribution:grand_coverage"
_CAIRO_TZ = ZoneInfo("Africa/Cairo")
_PAGE = 5000

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


def _build_campaign_map(both_set_rows: list) -> dict[int, dict]:
    """Derive campaign_map[cid] = {both_set, buyers Counter, buyer_names} from the
    BOTH-SET read_group rows (leads with both campaign_id AND media_buyer_id). The
    single source of truth for the campaign→buyer map, shared by the all-time overview
    and the windowed view (so a buyer's mapping never differs between them)."""
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
    return campaign_map


def _dominant(
    campaign_map: dict[int, dict], cid: int
) -> tuple[Optional[int], Optional[str], int, int]:
    """(buyer_id, buyer_name, dominant_count, both_set_count) for a campaign."""
    entry = campaign_map.get(cid)
    if not entry or not entry["buyers"]:
        return None, None, 0, 0
    bid, dom_cnt = entry["buyers"].most_common(1)[0]
    return bid, entry["buyer_names"].get(bid), dom_cnt, entry["both_set"]


def _gate_attributing(
    confirmed_ids: set[int],
    denylist_ids: set[int],
    campaign_map: dict[int, dict],
    id_to_name: dict[int, str],
) -> tuple[set[int], list[str]]:
    """Apply the attribution GATE (A1) — shared by the all-time overview and the
    windowed view so both attribute EXACTLY the same campaigns.

    attributing_ids = { C in confirmed_ids : qualifies(C) AND C not in denylist }.
    A confirmed campaign that resolves into the denylist, has no both-set leads, or
    fails the >=90% concentration gate is NOT attributed and yields a LOUD
    integrity-alert string (locked-decision drift). The caller logs the alerts.
    Returns (attributing_ids, integrity_alerts)."""
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
        bid, bname, dom_cnt, both = _dominant(campaign_map, cid)
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
    return attributing_ids, integrity_alerts


async def _fetch_all_windowed(
    client: OdooClient, dom: list, fields: list[str]
) -> list[dict]:
    """search_read the whole domain in pages of _PAGE, ordered by id — the SAME paged
    pattern the campaign windowing uses for its single windowed lead fetch."""
    rows, offset = [], 0
    while True:
        page = await client.execute_kw(
            _LEAD_MODEL,
            "search_read",
            args=[dom],
            kwargs={
                "fields": fields,
                "order": "id",
                "limit": _PAGE,
                "offset": offset,
                "context": _CTX_ALL,
            },
        )
        rows.extend(page)
        if len(page) < _PAGE:
            break
        offset += _PAGE
    return rows


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
        #                      "buyer_names": {id: name}} — shared with the windowed view.
        campaign_map = _build_campaign_map(both_set_rows)

        # ── GATE (A1) — compute attributing_ids BEFORE the attribution RPC ────
        attributing_ids, integrity_alerts = _gate_attributing(
            confirmed_ids, denylist_ids, campaign_map, id_to_name
        )
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
        bid, bname, _, _ = _dominant(campaign_map, cid)
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
        bid, bname, dom_cnt, both = _dominant(campaign_map, cid)
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
        bid, bname, dom_cnt, both = _dominant(campaign_map, cid)
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


async def get_attribution_grand_coverage(
    client: Optional[OdooClient] = None,
    confirmed_campaigns: Optional[frozenset[str]] = None,
    denylist_campaigns: Optional[frozenset[str]] = None,
    legacy_days: Optional[set[str]] = None,
    now_cairo: Optional[datetime] = None,
) -> dict:
    """Return the window-INDEPENDENT all-time ATTRIBUTION-coverage footer.

    The buyer-page parallel of the campaign grand-totals footer (f8f27bf): two
    all-time lines that stay CONSTANT regardless of the window switcher, so the
    full-scale attribution picture is always on screen beneath whatever period the
    buyer list is scoped to. Each line = an attributed total + a coverage % + the
    aggregate 4-group attributed funnel:

      - incl: the shipped all-time attribution INCLUDING the Nov-2025 migration —
        attributed / population (ties 1:1 to get_attribution_overview's
        total_attributed / total_leads_population / attribution_pct).
      - excl: the same with the legacy migration SUBTRACTED — attributed-excl /
        population-excl, the ongoing (non-migration) attribution coverage.

    Cheap & GROUPED (never a row scan). The incl side REUSES the cached overview:
    its per-buyer outcomes Σ to the incl attributed funnel, and the attributing
    campaign ids come from its confirmed_campaigns. Only the migration slice costs
    extra RPCs — one read_group on (attributing campaigns AND the legacy days' UTC
    ranges) by stage_id (the migration-attributed funnel, classified through the
    SAME stage_info + classify_stage), and one search_count on the legacy days' UTC
    ranges (the total migration population). excl-attributed[g] = incl[g] −
    migration-attributed[g], guarded >= 0 (explicit raise — survives -O);
    population_excl = population − migration_total.

    Args:
        client: optional injected OdooClient (tests pass a mock; production opens
            and closes its own — the reused overview shares this same client so it
            does NOT open or close a second connection).
        confirmed_campaigns / denylist_campaigns: optional gate overrides for tests,
            forwarded to get_attribution_overview so the incl side reflects the same
            gate. When provided the cache is bypassed (a test config never poisons
            the production cache key).
        legacy_days: optional injected migration-day set (tests) — bypasses the live
            detection RPC entirely.
        now_cairo: optional injected Cairo-local "now" (tests) — pins reference_date.

    The result is cached ONLY for the default configuration (no overrides) under its
    own key with the SAME 1h TTL as the campaign grand-totals footer.

    Returns:
        {
          "incl": {"attributed_total": int, "population": int,
                   "coverage_pct": float, "groups": [{group, count, pct}, ...]},
          "excl": {"attributed_total": int, "population": int,
                   "coverage_pct": float, "groups": [{group, count, pct}, ...]},
          "migration_attributed_total": int,
          "migration_total": int,
          "legacy_days": [str, ...],
          "reference_date": str, "as_of": str,
          "cache_status": str, "rpc_duration_ms": int,
        }

    Raises:
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated.
        OdooQueryError: if any Odoo RPC fails.
        RuntimeError: if a funnel fails to reconcile, or any excl group would go
            negative (migration-attributed exceeds the all-time attributed count).
    """
    _assert_read_only()

    default_config = (
        confirmed_campaigns is None
        and denylist_campaigns is None
        and legacy_days is None
        and now_cairo is None
    )

    ref = now_cairo if now_cairo is not None else datetime.now(_CAIRO_TZ)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=_CAIRO_TZ)
    cairo_today = ref.date()

    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_GRAND)
    if default_config:
        cached = _cache.get(cache_key)
        if cached is not None:
            logger.debug(f"Cache hit: {cache_key}")
            return {**cached, "cache_status": "cached", "rpc_duration_ms": 0}
        logger.info(f"Cache miss: {cache_key} — querying Odoo")

    _client = client if client is not None else OdooClient()

    t0 = time.monotonic()
    try:
        # ── incl side — REUSE the cached overview (same gate, same population) ──
        # Passing _client (not None) means the overview never opens/closes its own
        # connection; we own _client and close it once in finally.
        overview = await get_attribution_overview(
            client=_client,
            confirmed_campaigns=confirmed_campaigns,
            denylist_campaigns=denylist_campaigns,
        )
        attributing_ids = sorted(
            {int(c["campaign_id"]) for c in overview["confirmed_campaigns"]}
        )

        # ── stages — classify the migration-attributed slice (shared mapping) ──
        stages = await _client.execute_kw(
            _STAGE_MODEL,
            "search_read",
            args=[[]],
            kwargs={"fields": ["id", "name", "is_won"]},
        )

        # ── legacy migration days (cached long; injectable for tests) ──────────
        resolved_legacy = (
            set(legacy_days) if legacy_days is not None
            else await get_legacy_migration_days(_client)
        )
        legacy_domain = _legacy_days_domain(resolved_legacy)

        # ── migration-ATTRIBUTED funnel: (attributing campaigns) AND legacy days ─
        # Positive OR-domain (no negation), AND-ed with the attributing-campaign
        # filter. Skipped when nothing attributes or no legacy day was detected.
        migration_attr_rows: list = []
        if legacy_domain is not None and attributing_ids:
            migration_attr_domain = [
                "&",
                (CAMPAIGN_FIELD, "in", attributing_ids),
                *legacy_domain,
            ]
            migration_attr_rows = await _client.execute_kw(
                _LEAD_MODEL,
                "read_group",
                args=[migration_attr_domain, ["stage_id"], ["stage_id"]],
                kwargs={"context": _CTX_ALL, "lazy": False},
            )

        # ── total MIGRATION population: ALL leads on the legacy days ────────────
        migration_total = 0
        if legacy_domain is not None:
            migration_total = await _client.execute_kw(
                _LEAD_MODEL,
                "search_count",
                args=[legacy_domain],
                kwargs={"context": _CTX_ALL},
            )
    except ReadOnlyViolationError:
        raise
    except Exception as exc:
        raise OdooQueryError(
            f"get_attribution_grand_coverage() RPC failed: {exc}"
        ) from exc
    finally:
        if client is None:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)

    # ── stage info for classify_stage (identical to the overview) ─────────────
    stage_info: dict[int, dict] = {}
    for s in stages:
        stage_info[int(s["id"])] = {
            "name": str(s.get("name") or ""),
            "is_won": bool(s.get("is_won")),
        }

    # ── incl-attributed funnel = Σ the overview's per-buyer outcomes ───────────
    incl_attr_groups = {g: 0 for g in GROUP_ORDER}
    for b in overview["buyers"]:
        for o in b["outcomes"]:
            incl_attr_groups[o["group"]] += int(o["count"])
    incl_attr_total = int(overview["total_attributed"])
    population = int(overview["total_leads_population"])

    # ── migration-attributed funnel (classified by stage, shared mapping) ──────
    migration_attr_groups = {g: 0 for g in GROUP_ORDER}
    migration_attr_total = 0
    for r in migration_attr_rows:
        cnt = int(r.get("__count") or 0)
        migration_attr_total += cnt
        sid, _ = _m2o(r.get("stage_id"))   # stage_id may be False -> None -> جديد
        migration_attr_groups[classify_stage(sid, stage_info)] += cnt

    # ── excl = incl − migration-attributed, per group, guarded non-negative ────
    excl_attr_groups: dict[str, int] = {}
    for g in GROUP_ORDER:
        diff = incl_attr_groups[g] - migration_attr_groups[g]
        if diff < 0:
            raise RuntimeError(
                f"Grand-coverage reconciliation FAILED for group {g!r}: "
                f"migration-attributed {migration_attr_groups[g]} exceeds all-time "
                f"attributed {incl_attr_groups[g]} (excl would be {diff} < 0). "
                f"Refusing an inconsistent funnel."
            )
        excl_attr_groups[g] = diff
    excl_attr_total = incl_attr_total - migration_attr_total
    population_excl = population - migration_total

    coverage_incl = (
        round(100.0 * incl_attr_total / population, 2) if population else 0.0
    )
    coverage_excl = (
        round(100.0 * excl_attr_total / population_excl, 2)
        if population_excl else 0.0
    )

    logger.info(
        f"Marketing attribution grand coverage: incl={incl_attr_total:,}/"
        f"{population:,} ({coverage_incl:.1f}%) migration_attr={migration_attr_total:,} "
        f"migration_total={migration_total:,} | excl={excl_attr_total:,}/"
        f"{population_excl:,} ({coverage_excl:.1f}%) | legacy_days={len(resolved_legacy)} "
        f"| RPCs in {rpc_ms}ms | cache_key={cache_key}"
    )

    result: dict = {
        "incl": {
            "attributed_total": incl_attr_total,
            "population": population,
            "coverage_pct": coverage_incl,
            "groups": _outcomes(
                incl_attr_groups, incl_attr_total, "grand_coverage incl migration"
            ),
        },
        "excl": {
            "attributed_total": excl_attr_total,
            "population": population_excl,
            "coverage_pct": coverage_excl,
            "groups": _outcomes(
                excl_attr_groups, excl_attr_total, "grand_coverage excl migration"
            ),
        },
        "migration_attributed_total": migration_attr_total,
        "migration_total": migration_total,
        "legacy_days": sorted(resolved_legacy),
        "reference_date": cairo_today.isoformat(),
        "as_of": datetime.now(timezone.utc).isoformat(),
        "cache_status": "fresh",
        "rpc_duration_ms": rpc_ms,
    }

    if default_config:
        _cache.set(cache_key, result, ttl=_GRAND_TTL_SECONDS)
    return result


async def get_attribution_overview_windowed(
    client: Optional[OdooClient] = None,
    window: str = DEFAULT_WINDOW,
    start_month: Optional[str] = None,
    end_month: Optional[str] = None,
    confirmed_campaigns: Optional[frozenset[str]] = None,
    denylist_campaigns: Optional[frozenset[str]] = None,
    legacy_days: Optional[set[str]] = None,
    now_cairo: Optional[datetime] = None,
) -> dict:
    """Return the per-media-buyer attribution overview SCOPED to a Cairo time window.

    Same campaign-driven attribution as the all-time overview, but every funnel is
    restricted to the leads that AROSE in the window (Cairo create_date), the legacy
    Nov-2025 migration EXCLUDED (consistent with the campaign windowing + timeline).
    The campaign→buyer MAP stays ALL-TIME (derived from the all-time both-set slice +
    the >=90% confirmed gate via the SHARED _build_campaign_map / _gate_attributing), so
    a buyer's mapping never shifts with the window — only the LEADS feeding the funnel
    are windowed. Lists every buyer with >=1 attributed windowed lead, sorted by
    windowed volume; surfaces an UNATTRIBUTED bucket (windowed leads in campaigns with
    no confirmed buyer — unmapped/denylisted channels) so the windowed coverage is
    honest. The "all" window is NOT handled here — callers route it to
    get_attribution_overview() (the shipped un-windowed path, migration included).

    Window resolution:
      - A valid explicit start_month/end_month range OVERRIDES `window` and drives a
        custom window (is_custom_range=True), validated by the SAME
        _resolve_custom_window contract the campaign windowing uses.
      - Otherwise `window` is a dated preset key in WINDOW_PRESET_MONTHS ("current" /
        "last3"), a trailing span ending at the current Cairo month.

    Args mirror get_attribution_overview plus the window params and the test injections
    (legacy_days / now_cairo) the campaign windowing exposes. The result is cached ONLY
    for the default configuration (no gate/legacy/now overrides), keyed by the window
    tag, so a custom window never collides with a preset and a test config never poisons
    the production cache — same pattern as the campaign windowing.

    Returns a dict matching schemas.MarketingAttributionWindowed.

    Raises:
        InvalidTimelineRangeError: the custom start_month/end_month range is invalid.
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated.
        OdooQueryError: if any Odoo RPC fails.
        RuntimeError: if a buyer/unattributed funnel fails to reconcile, or the windowed
            attributed + unattributed identity fails.
    """
    _assert_read_only()

    # Custom range validated BEFORE any RPC (both-or-neither, span <= cap). None → the
    # dated preset path.
    custom_window = _resolve_custom_window(start_month, end_month)
    is_custom_range = custom_window is not None

    if not is_custom_range and window not in WINDOW_PRESET_MONTHS:
        raise InvalidTimelineRangeError(
            f"window must be one of {sorted(WINDOW_PRESET_MONTHS)} or a custom "
            f"start_month/end_month range — got {window!r}."
        )

    default_config = (
        confirmed_campaigns is None
        and denylist_campaigns is None
        and legacy_days is None
        and now_cairo is None
    )
    confirmed_names = (
        confirmed_campaigns if confirmed_campaigns is not None
        else domain.CONFIRMED_BUYER_CAMPAIGNS
    )
    denylist_names = (
        denylist_campaigns if denylist_campaigns is not None
        else domain.DENYLIST_CAMPAIGNS
    )

    ref = now_cairo if now_cairo is not None else datetime.now(_CAIRO_TZ)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=_CAIRO_TZ)
    cairo_today = ref.date()
    current_month_start = ref.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # ── resolve the window's [start_dt .. end_dt] Cairo month bounds ──────────
    if is_custom_range:
        start_dt, end_dt = custom_window
        window_tag = f"c{_month_str(start_dt)}_{_month_str(end_dt)}"
    else:
        span = WINDOW_PRESET_MONTHS[window]
        end_dt = current_month_start
        start_dt = _shift_months(current_month_start, -(span - 1))
        window_tag = window
    window_months_list = _month_range(start_dt, end_dt)
    window_month_set = set(window_months_list)
    window_months = len(window_months_list)

    cache_key = _cache.make_key(f"{_CACHE_KEY_PREFIX_WINDOWED}:{window_tag}")
    if default_config:
        cached = _cache.get(cache_key)
        if cached is not None:
            logger.debug(f"Cache hit: {cache_key}")
            return {**cached, "cache_status": "cached", "rpc_duration_ms": 0}
        logger.info(f"Cache miss: {cache_key} — querying Odoo")

    # Coarse UTC fetch bounds (Cairo month-starts → UTC; Odoo stores UTC-naive). The
    # exact Cairo bucketing / legacy-day drop happens per-lead below — these bounds are
    # only a fetch filter. Lower = window start; upper = first day after window end.
    lower_str = start_dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    upper_str = _shift_months(end_dt, 1).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

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

        # ── RPC 2 — crm.stage id+name+is_won (outcome group mapping) ──────────
        stages = await _client.execute_kw(
            _STAGE_MODEL,
            "search_read",
            args=[[]],
            kwargs={"fields": ["id", "name", "is_won"]},
        )

        # ── RPC 3 — THE windowed query: leads in [lower .. upper) (paged) ─────
        # ONE search_read of create_date + campaign_id + stage_id, regrouped in
        # Python by Cairo month (legacy days dropped) — the SAME single-query path
        # the campaign windowing uses (1 RPC for the whole list, vs N per buyer).
        windowed_leads = await _fetch_all_windowed(
            _client,
            [("create_date", ">=", lower_str), ("create_date", "<", upper_str)],
            ["create_date", CAMPAIGN_FIELD, "stage_id"],
        )

        # ── RPC 4 — ALL-TIME both-set slice by (campaign, buyer) ──────────────
        # Unbounded (no date filter) so the campaign→buyer map == the all-time view.
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

        # ── RPC 5 — legacy migration days (cached long; injectable for tests) ─
        resolved_legacy = (
            set(legacy_days) if legacy_days is not None
            else await get_legacy_migration_days(_client)
        )
    except ReadOnlyViolationError:
        raise
    except Exception as exc:
        raise OdooQueryError(
            f"get_attribution_overview_windowed() RPC failed: {exc}"
        ) from exc
    finally:
        if client is None:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)

    # ── name<->id maps + gate resolution (same _resolve pattern as the overview) ──
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

    # ── stage info + is_won names (RPC 2) ─────────────────────────────────────
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

    # ── campaign→buyer map + GATE (ALL-TIME; identical to the overview) ───────
    campaign_map = _build_campaign_map(both_set_rows)
    attributing_ids, integrity_alerts = _gate_attributing(
        confirmed_ids, denylist_ids, campaign_map, id_to_name
    )
    for alert in integrity_alerts:
        logger.error(alert)

    # ── regroup windowed leads by Cairo month, dropping legacy days ───────────
    # Each attributing campaign's windowed leads attribute to its DERIVED dominant
    # buyer (the all-time map); everything else falls into the UNATTRIBUTED bucket
    # (no campaign, junk, denylisted, pending, or unmapped channels).
    buyer_agg: dict[int, dict] = {}     # bid -> {name, total, groups, campaign_ids}
    unattributed_groups = {g: 0 for g in GROUP_ORDER}
    unattributed_total = 0
    windowed_population = 0
    for r in windowed_leads:
        cd = r.get("create_date")
        if not cd:
            continue
        cairo = _to_cairo(cd)
        if cairo.strftime("%Y-%m-%d") in resolved_legacy:
            continue                                    # drop the legacy migration
        if _month_str(cairo) not in window_month_set:   # exact-bound / over-fetch guard
            continue
        cid, _ = _m2o(r.get(CAMPAIGN_FIELD))            # cid is None for the no-campaign bucket
        sid, _ = _m2o(r.get("stage_id"))                # stage_id may be False -> None -> جديد
        group = classify_stage(sid, stage_info)
        windowed_population += 1
        if cid is not None and cid in attributing_ids:
            bid, bname, _, _ = _dominant(campaign_map, cid)
            b = buyer_agg.setdefault(
                bid,
                {"name": bname, "total": 0,
                 "groups": {g: 0 for g in GROUP_ORDER}, "campaign_ids": set()},
            )
            b["total"] += 1
            b["groups"][group] += 1
            b["campaign_ids"].add(cid)
        else:
            unattributed_total += 1
            unattributed_groups[group] += 1

    # ── build buyer rows (windowed funnels), reconcile, sort by windowed volume ─
    buyers: list[dict] = []
    total_attributed = 0
    for bid, b in buyer_agg.items():
        total = b["total"]
        group_sum = sum(b["groups"].values())
        if group_sum != total:
            raise RuntimeError(
                f"Windowed outcome reconciliation FAILED for buyer {b['name']!r} "
                f"(id={bid}): group sum {group_sum} != total {total}. "
                f"Refusing to return inconsistent attribution."
            )
        buyers.append(
            {
                "buyer_id": bid,
                "buyer_name": b["name"] or "",
                "total_attributed": total,
                "outcomes": [
                    {
                        "group": g,
                        "count": b["groups"][g],
                        "pct": round(100.0 * b["groups"][g] / total, 2) if total else 0.0,
                    }
                    for g in GROUP_ORDER
                ],
                "campaign_ids": sorted(b["campaign_ids"]),
            }
        )
        total_attributed += total
    buyers.sort(key=lambda x: (-x["total_attributed"], x["buyer_name"]))

    # ── unattributed bucket (windowed coverage honesty) — reconcile ───────────
    unatt_sum = sum(unattributed_groups.values())
    if unatt_sum != unattributed_total:
        raise RuntimeError(
            f"Windowed unattributed reconciliation FAILED: group sum {unatt_sum} "
            f"!= total {unattributed_total}."
        )
    unattributed = {
        "lead_count": unattributed_total,
        "outcomes": [
            {
                "group": g,
                "count": unattributed_groups[g],
                "pct": round(100.0 * unattributed_groups[g] / unattributed_total, 2)
                if unattributed_total else 0.0,
            }
            for g in GROUP_ORDER
        ],
    }

    # ── windowed population identity (explicit raise; survives -O) ────────────
    if total_attributed + unattributed_total != windowed_population:
        raise RuntimeError(
            f"Windowed population reconciliation FAILED: attributed "
            f"{total_attributed} + unattributed {unattributed_total} != windowed "
            f"population {windowed_population}."
        )

    coverage_pct = (
        round(100.0 * total_attributed / windowed_population, 2)
        if windowed_population else 0.0
    )

    logger.info(
        f"Marketing attribution (windowed): window={window_tag} "
        f"[{window_months_list[0]}..{window_months_list[-1]}] | buyers={len(buyers)} "
        f"attributed={total_attributed:,} unattributed={unattributed_total:,} "
        f"windowed_pop={windowed_population:,} coverage={coverage_pct:.1f}% | "
        f"legacy_days={len(resolved_legacy)} | RPCs in {rpc_ms}ms | "
        f"alerts={len(integrity_alerts)} warnings={len(config_warnings)} | "
        f"cache_key={cache_key}"
    )

    result: dict = {
        "buyers": buyers,
        "unattributed": unattributed,
        "total_leads_population": windowed_population,
        "total_attributed": total_attributed,
        "coverage_pct": coverage_pct,
        "window": WINDOW_CUSTOM if is_custom_range else window,
        "is_custom_range": is_custom_range,
        "window_months": window_months,
        "window_start_month": window_months_list[0],
        "window_end_month": window_months_list[-1],
        "legacy_days_excluded": sorted(resolved_legacy),
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
