"""
Marketing Attribution — per-MEDIA-BUYER TIMELINE (Slice 3, period-level, read-only).

The exact mirror of the campaign_performance per-campaign timeline, but the drill-in
subject is ONE media buyer (not a campaign). A board user drills into a buyer and
sees that buyer's leads grouped over Cairo-local months: a lightweight volume trend
(trend_months) plus a full 4-group funnel + a DERIVED maturation state per recent
month (window_months). The Nov-2025 legacy migration is EXCLUDED (same windowing
rule as the campaign timeline).

REUSE, NO DUPLICATION — this service is a thin composition over the shipped helpers:
  - Cairo bucketing, the custom-range contract, the maturation heuristic, the
    reconciling 4-group builder, the paged fetch, and dynamic legacy-day detection
    are IMPORTED VERBATIM from campaign_performance.services.timeline_service.
  - The campaign→buyer MAP + the >=90% confirmed GATE are IMPORTED from this module's
    attribution_service (_build_campaign_map / _dominant / _gate_attributing) — the
    SAME all-time map the dashboard uses — so a buyer's identity, its attributing
    campaign set, and its windowed funnel totals match the /windowed board 1:1.
  - The 4-group stage classification is this module's domain.classify_stage.
No campaign-timeline logic is generalized or modified; this is a parallel reader.

Data discipline (identical to Level 1 / the campaign timeline):
  - active_test=False on every lead RPC — archived/Lost leads are counted.
  - "Arose that month" = grouped by create_date, Cairo-local (ZoneInfo) via
    search_read + Python regroup (Decision 5.10), NOT read_group raw-UTC bucketing.
  - A buyer's funnel attributes ALL leads of its attributing campaigns to that buyer
    (the SAME inference as get_attribution_overview[_windowed]; A6).
  - The legacy Nov-2025 migration is detected DYNAMICALLY (Cairo days holding
    >= LEGACY_DAY_MIN leads) and dropped from every period/trend/header.

Maturation caveat (discovery §F.5): Odoo keeps no per-stage history and no date_won,
so a month's funnel is the CURRENT stage breakdown of the leads that AROSE that month;
maturation_state is a DERIVED heuristic (month age + جديد share), not a measurement.

No method ever calls create / write / unlink. _assert_read_only() runs at entry.
"""

import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from loguru import logger

from backend.core.exceptions import (
    LaVerdeERPError,
    OdooQueryError,
    ReadOnlyViolationError,
)
from backend.modules.marketing_attribution import domain
from backend.modules.marketing_attribution.domain import (
    BUYER_FIELD,
    CAMPAIGN_FIELD,
    GROUP_NEW,
    GROUP_ORDER,
    classify_stage,
)
from backend.modules.marketing_attribution.services import cache as _cache
# The campaign→buyer MAP + GATE are the SAME all-time helpers the dashboard uses,
# so this buyer page's identity / attributing set / funnel totals match /windowed.
from backend.modules.marketing_attribution.services.attribution_service import (
    _build_campaign_map,
    _dominant,
    _gate_attributing,
)
# Window primitives + the timeline mechanics are REUSED VERBATIM from the campaign
# performance timeline (single source of truth for Cairo bucketing, the custom-range
# validation contract, the maturation heuristic, the reconciling 4-group builder, the
# paged fetch, and dynamic legacy-migration detection) — never re-declared here.
# One-way import: campaign_performance never imports this module's services, so no cycle.
from backend.modules.campaign_performance.domain import (
    DEFAULT_TREND_MONTHS,
    DEFAULT_WINDOW_MONTHS,
)
from backend.modules.campaign_performance.services.timeline_service import (
    InvalidTimelineRangeError,
    _fetch_all,
    _m2o,
    _maturation_state,
    _month_range,
    _month_str,
    _outcomes,
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

_CACHE_KEY_PREFIX = "marketing_attribution:buyer_timeline"

_CAIRO_TZ = ZoneInfo("Africa/Cairo")

# All counts include archived leads (board analysis must include Lost/closed).
_CTX_ALL = {"active_test": False}


class BuyerNotFoundError(LaVerdeERPError):
    """Raised when buyer_id is not the dominant buyer of any ATTRIBUTING campaign.

    A buyer "exists" for this page only if at least one confirmed, >=90%, non-denied
    campaign attributes to it (the same gate the dashboard applies). The endpoint maps
    this to HTTP 404 (distinct from a 503 RPC failure); it is re-raised through the RPC
    try-block, never wrapped in OdooQueryError. Mirrors CampaignNotFoundError.
    """


def _assert_read_only() -> None:
    """Defense-in-depth: abort if any write method has leaked into ALLOWED_METHODS.

    Defined locally (like every sibling service) so the read-only guard is patchable
    at THIS module's boundary and the abort happens before any RPC is issued.
    """
    violations = ALLOWED_METHODS & _FORBIDDEN_WRITE_METHODS
    if violations:
        raise ReadOnlyViolationError(
            f"ALLOWED_METHODS contains forbidden write method(s): {sorted(violations)}. "
            "The Odoo client is no longer strictly read-only. Halting before any RPC."
        )


async def get_buyer_timeline(
    client: Optional[OdooClient] = None,
    buyer_id: Optional[int] = None,
    window_months: int = DEFAULT_WINDOW_MONTHS,
    trend_months: int = DEFAULT_TREND_MONTHS,
    start_month: Optional[str] = None,
    end_month: Optional[str] = None,
    confirmed_campaigns: Optional[frozenset[str]] = None,
    denylist_campaigns: Optional[frozenset[str]] = None,
    legacy_days: Optional[set[str]] = None,
    now_cairo: Optional[datetime] = None,
) -> dict:
    """Return the per-media-buyer timeline (Slice 3) for one media buyer.

    Args:
        client: optional injected OdooClient (tests pass a mock; production opens
            and closes its own).
        buyer_id: the res.users id of the media buyer to drill into (required, > 0).
            Must be the DERIVED dominant buyer of >=1 attributing campaign, else 404.
        window_months: # of trailing Cairo months reported with a full funnel —
            the `months` preset. IGNORED when an explicit custom range is given.
        trend_months: # of trailing Cairo months in the volume trend (fixed by the
            endpoint to DEFAULT_TREND_MONTHS). The trend always ends at the window's
            END month (current month for a preset; end_month for a custom range).
        start_month / end_month: optional explicit Cairo-local "YYYY-MM" range. When
            BOTH are given and valid, they OVERRIDE window_months and define the
            inclusive [start_month .. end_month] funnel window. Both-or-neither — the
            validation contract is _resolve_custom_window (shared with the campaign
            timeline, so HTML/JSON agree on what is valid).
        confirmed_campaigns / denylist_campaigns: optional gate overrides for tests.
        legacy_days: optional injected migration-day set (tests) — bypasses the live
            detection RPC entirely.
        now_cairo: optional injected Cairo-local "now" for deterministic windows.

    The campaign→buyer MAP + the GATE are ALWAYS all-time (the >=90% confirmed gate),
    derived via the SHARED _build_campaign_map / _gate_attributing, so a buyer's
    identity and attributing campaign set never shift with the window — only the LEADS
    feeding the funnel are windowed. A buyer's windowed funnel totals therefore
    reconcile 1:1 with that buyer's row on the /windowed board view.

    The result is cached ONLY for the default configuration (no gate/legacy/now
    overrides), keyed by buyer_id + window (preset `w{n}` OR custom `c{start}_{end}`)
    + trend, so a custom window never collides with a preset and a test config never
    poisons the production cache — same pattern as the campaign timeline.

    Returns a dict matching schemas.BuyerTimeline.

    Raises:
        BuyerNotFoundError: buyer_id attributes from no campaign (HTTP 404).
        InvalidTimelineRangeError: the custom start_month/end_month range is invalid.
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated.
        OdooQueryError: if any Odoo RPC fails.
        RuntimeError: if any month's 4 group counts fail to reconcile.
    """
    _assert_read_only()

    if buyer_id is None or int(buyer_id) <= 0:
        raise BuyerNotFoundError(f"buyer_id must be a positive int, got {buyer_id!r}")
    buyer_id = int(buyer_id)
    window_months = int(window_months)
    trend_months = int(trend_months)

    # Optional explicit custom range — validated BEFORE any RPC. None → the trailing
    # `months` preset path. A tuple → (start_dt, end_dt) Cairo month starts that
    # OVERRIDE window_months.
    custom_window = _resolve_custom_window(start_month, end_month)
    is_custom_range = custom_window is not None

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
    current_month_start = ref.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    current_month = _month_str(current_month_start)

    # ── resolve the funnel window (preset OR explicit custom range) ───────────
    # The trend ALWAYS ends at the window's END month — the current month for a
    # preset, the end_month for a custom range (identical to the campaign timeline).
    if is_custom_range:
        range_start_start, window_end_start = custom_window
        period_months_list = _month_range(range_start_start, window_end_start)
        window_months = len(period_months_list)               # derived month count
        window_tag = f"c{_month_str(range_start_start)}_{_month_str(window_end_start)}"
    else:
        window_end_start = current_month_start
        period_months_list = [
            _month_str(_shift_months(current_month_start, -i))
            for i in range(window_months - 1, -1, -1)
        ]
        window_tag = f"w{window_months}"

    trend_months_list = [
        _month_str(_shift_months(window_end_start, -i))
        for i in range(trend_months - 1, -1, -1)
    ]

    cache_key = _cache.make_key(
        f"{_CACHE_KEY_PREFIX}:{buyer_id}:{window_tag}:t{trend_months}"
    )
    if default_config:
        cached = _cache.get(cache_key)
        if cached is not None:
            logger.debug(f"Cache hit: {cache_key}")
            return {**cached, "cache_status": "cached", "rpc_duration_ms": 0}
        logger.info(f"Cache miss: {cache_key} — querying Odoo")

    # Odoo create_date fetch bounds (Cairo month-starts → UTC; Odoo stores UTC-naive).
    # LOWER = the earliest month any figure needs (funnel window OR trend, whichever
    # reaches further back). Custom ranges ALSO set an UPPER bound (first day after
    # end_month). Bucketing stays DST-correct via per-lead Cairo regrouping below —
    # these bounds are only a coarse fetch filter. Identical to the campaign timeline.
    if is_custom_range:
        trend_earliest = _shift_months(window_end_start, -(trend_months - 1))
        bound_cairo = min(range_start_start, trend_earliest)
        upper_cairo = _shift_months(window_end_start, 1)
        upper_str = upper_cairo.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    else:
        back = max(window_months, trend_months)
        bound_cairo = _shift_months(current_month_start, -back)
        upper_str = None
    bound_str = bound_cairo.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    _client = client if client is not None else OdooClient()

    config_warnings: list[str] = []
    t0 = time.monotonic()
    try:
        # ── RPC a — utm.campaign id+name (resolve gates) ──────────────────────
        campaigns = await _client.execute_kw(
            _CAMPAIGN_MODEL,
            "search_read",
            args=[[]],
            kwargs={"fields": ["id", "name"], "context": _CTX_ALL},
        )
        id_to_name: dict[int, str] = {}
        name_to_ids: dict[str, list[int]] = defaultdict(list)
        for c in campaigns:
            cid = int(c["id"])
            cname = str(c.get("name") or "")
            id_to_name[cid] = cname
            name_to_ids[cname].append(cid)

        # ── RPC b — crm.stage id+name+is_won (outcome group mapping) ──────────
        stages = await _client.execute_kw(
            _STAGE_MODEL,
            "search_read",
            args=[[]],
            kwargs={"fields": ["id", "name", "is_won"]},
        )

        # ── RPC c — ALL-TIME both-set slice by (campaign, buyer) ──────────────
        # Unbounded (no date filter) so the campaign→buyer map == the dashboard's.
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

        # ── resolve the gate name→id sets (same _resolve pattern as the dashboard) ─
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

        # ── campaign→buyer map + GATE (ALL-TIME; identical to the dashboard) ──
        campaign_map = _build_campaign_map(both_set_rows)
        attributing_ids, integrity_alerts = _gate_attributing(
            confirmed_ids, denylist_ids, campaign_map, id_to_name
        )

        # ── resolve THIS buyer's attributing campaigns (its funnel's lead source) ─
        buyer_campaign_ids: list[int] = []
        buyer_name: Optional[str] = None
        for cid in sorted(attributing_ids):
            bid, bname, _, _ = _dominant(campaign_map, cid)
            if bid == buyer_id:
                buyer_campaign_ids.append(cid)
                if buyer_name is None:
                    buyer_name = bname
        if not buyer_campaign_ids:
            # Not the dominant buyer of any attributing campaign → 404 (not a 503).
            raise BuyerNotFoundError(
                f"buyer_id={buyer_id} attributes from no confirmed campaign "
                f"(>=90% gate) — no buyer timeline."
            )

        # ── RPC d — this buyer's attributing-campaign leads in the window (paged) ─
        # Preset: lower-bounded only. Custom: also upper-bounded at end_month+1.
        lead_domain = [
            (CAMPAIGN_FIELD, "in", buyer_campaign_ids),
            ("create_date", ">=", bound_str),
        ]
        if upper_str is not None:
            lead_domain.append(("create_date", "<", upper_str))
        leads = await _fetch_all(
            _client,
            _LEAD_MODEL,
            lead_domain,
            ["create_date", "stage_id"],
        )

        # ── RPC e — legacy migration days (cached long; injectable for tests) ──
        resolved_legacy = (
            set(legacy_days) if legacy_days is not None
            else await get_legacy_migration_days(_client)
        )
    except (ReadOnlyViolationError, BuyerNotFoundError):
        raise
    except Exception as exc:
        raise OdooQueryError(f"get_buyer_timeline() RPC failed: {exc}") from exc
    finally:
        if client is None:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)

    for alert in integrity_alerts:
        logger.error(alert)

    # ── stage info for classify_stage (identical to the dashboard / Level 1) ──
    stage_info: dict[int, dict] = {}
    for s in stages:
        stage_info[int(s["id"])] = {
            "name": str(s.get("name") or ""),
            "is_won": bool(s.get("is_won")),
        }

    # ── regroup leads by Cairo month, dropping legacy-migration days ──────────
    month_counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {g: 0 for g in GROUP_ORDER}
    )
    month_total: Counter = Counter()
    for r in leads:
        cd = r.get("create_date")
        if not cd:
            continue
        cairo = _to_cairo(cd)
        if cairo.strftime("%Y-%m-%d") in resolved_legacy:
            continue                                   # drop the legacy migration
        month = _month_str(cairo)
        sid, _ = _m2o(r.get("stage_id"))               # stage_id may be False -> None -> جديد
        month_counts[month][classify_stage(sid, stage_info)] += 1
        month_total[month] += 1

    # ── trend (trend_months ending at the window end, oldest→newest, 0-filled) ─
    trend = [{"month": m, "lead_count": month_total.get(m, 0)} for m in trend_months_list]

    # ── periods (the window months, oldest→newest, full funnel + maturation) ───
    periods: list[dict] = []
    for m in period_months_list:
        counts = month_counts.get(m, {g: 0 for g in GROUP_ORDER})
        total = month_total.get(m, 0)
        periods.append(
            {
                "month": m,
                "lead_count": total,
                "outcomes": _outcomes(counts, total, f"buyer id={buyer_id} month {m}"),
                "maturation_state": _maturation_state(
                    counts.get(GROUP_NEW, 0), total, m, current_month
                ),
            }
        )

    total_leads_in_window = sum(p["lead_count"] for p in periods)

    window_desc = (
        f"custom {period_months_list[0]}..{period_months_list[-1]} ({window_months}mo)"
        if is_custom_range else f"{window_months}mo"
    )
    logger.info(
        f"Buyer timeline: id={buyer_id} {buyer_name!r} | "
        f"campaigns={len(buyer_campaign_ids)} | window={window_desc} "
        f"trend={trend_months}mo | window_leads={total_leads_in_window:,} | "
        f"legacy_days={len(resolved_legacy)} | RPCs in {rpc_ms}ms | "
        f"alerts={len(integrity_alerts)} warnings={len(config_warnings)}"
    )

    result: dict = {
        "header": {
            "buyer_id": buyer_id,
            "buyer_name": buyer_name or "",
            "total_leads_in_window": total_leads_in_window,
            "attributing_campaign_count": len(buyer_campaign_ids),
            "attributing_campaign_ids": buyer_campaign_ids,
        },
        "trend": trend,
        "periods": periods,
        "window_months": window_months,
        "trend_months": trend_months,
        "window_start_month": period_months_list[0],
        "window_end_month": period_months_list[-1],
        "is_custom_range": is_custom_range,
        "legacy_days_excluded": sorted(resolved_legacy),
        "reference_date": cairo_today.isoformat(),
        "as_of": datetime.now(timezone.utc).isoformat(),
        "config_warnings": config_warnings,
        "integrity_alerts": integrity_alerts,
        "cache_status": "fresh",
        "rpc_duration_ms": rpc_ms,
    }

    if default_config:
        _cache.set(cache_key, result)
    return result
