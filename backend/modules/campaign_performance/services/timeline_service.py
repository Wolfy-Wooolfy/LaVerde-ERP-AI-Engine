"""
Campaign Performance — per-campaign TIMELINE (Level 2, period-level, read-only).

Drill into ONE campaign and see its leads grouped over Cairo-local months: a
lightweight volume trend (trend_months) plus a full 4-group funnel + a DERIVED
maturation state per recent month (window_months). Mirrors campaign_service.py:
read-only assert at entry, injected client, Cairo-local Python regrouping, the
same 60s in-memory cache, and explicit-raise reconciliation that survives -O.

Data discipline (identical population basis to Level 1 / marketing_attribution):
  - active_test=False on every lead RPC — archived/Lost leads are counted.
  - "Arose that month" = grouped by create_date, Cairo-local (ZoneInfo) via
    search_read + Python regroup, NOT read_group raw-UTC bucketing (Decision 5.10).
  - The legacy Nov-2025 CRM migration is EXCLUDED. It is detected DYNAMICALLY as
    the set of Cairo days holding >= LEGACY_DAY_MIN leads (reproduces discovery
    §F.1) — never hardcoded to dates — and dropped from every period/trend/header.

Maturation caveat (discovery §F.5): Odoo keeps no per-stage history and no
date_won, so there is NO true conversion-over-time curve. A month's funnel is the
CURRENT stage breakdown of the leads that arose that month; maturation_state is a
DERIVED heuristic (month age + جديد share), not a measurement.

The per-campaign media-buyer header reuses buyer.derive_buyer_status with the SAME
all-time both-set slice the Level-1 row uses, so the header matches Level 1 1:1.

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
from backend.modules.campaign_performance import domain
from backend.modules.campaign_performance.domain import (
    BUYER_FIELD,
    CAMPAIGN_FIELD,
    GROUP_NEW,
    GROUP_ORDER,
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

_CACHE_KEY_PREFIX = "campaign_performance:timeline"
# Legacy migration days are a STABLE historical fact — scan once and reuse for a
# day, not every 60s. Keyed by Cairo date so it still rolls over at Cairo midnight.
_LEGACY_CACHE_KEY = "campaign_performance:legacy_days"
_LEGACY_TTL_SECONDS = 24 * 60 * 60

_CAIRO_TZ = ZoneInfo("Africa/Cairo")
_PAGE = 5000

# All counts include archived leads (board analysis must include Lost/closed).
_CTX_ALL = {"active_test": False}


class CampaignNotFoundError(LaVerdeERPError):
    """Raised when the requested campaign_id resolves to no utm.campaign record.

    The endpoint maps this to HTTP 404 (distinct from a 503 RPC failure). It is
    re-raised through the RPC try-block, never wrapped in OdooQueryError.
    """


class InvalidTimelineRangeError(LaVerdeERPError):
    """Raised when an explicit custom start_month..end_month range is invalid.

    Covers: exactly one of the two provided (must be both-or-neither), a malformed
    value (not Cairo-local "YYYY-MM"), start_month after end_month, or a span wider
    than domain.MAX_CUSTOM_SPAN_MONTHS. Raised BEFORE any RPC. The JSON API maps it
    to HTTP 422; the HTML page silently falls back to the `months` preset (it never
    422s a hand-edited URL). Never wrapped in OdooQueryError.
    """


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


def _to_cairo(dt_str) -> datetime:
    """Odoo UTC-naive datetime string -> Cairo-local aware datetime (Decision 5.10)."""
    return (
        datetime.strptime(str(dt_str), "%Y-%m-%d %H:%M:%S")
        .replace(tzinfo=timezone.utc)
        .astimezone(_CAIRO_TZ)
    )


def _shift_months(dt: datetime, months: int) -> datetime:
    """Return dt moved by `months` (negative = back), snapped to the 1st at 00:00."""
    total = (dt.year * 12 + (dt.month - 1)) + months
    year, month = divmod(total, 12)
    return dt.replace(
        year=year, month=month + 1, day=1, hour=0, minute=0, second=0, microsecond=0
    )


def _month_str(dt: datetime) -> str:
    return dt.strftime("%Y-%m")


def _parse_month(value: str) -> datetime:
    """Parse a Cairo-local "YYYY-MM" into its first-day 00:00 Cairo-aware datetime.

    Raises ValueError (via strptime) on any malformed value — caught and re-raised
    as InvalidTimelineRangeError by _resolve_custom_window.
    """
    dt = datetime.strptime(str(value), "%Y-%m")
    return dt.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0, tzinfo=_CAIRO_TZ
    )


def _month_range(start: datetime, end: datetime) -> list[str]:
    """Inclusive list of Cairo "YYYY-MM" from start month to end month (oldest→newest)."""
    months: list[str] = []
    cur = start
    while cur <= end:
        months.append(_month_str(cur))
        cur = _shift_months(cur, 1)
    return months


def _resolve_custom_window(
    start_month: Optional[str], end_month: Optional[str]
) -> Optional[tuple[datetime, datetime]]:
    """Validate an optional explicit custom range; return (start_dt, end_dt) or None.

    Returns None when NEITHER bound is given (→ the trailing `months` preset path,
    unchanged). Otherwise validates and returns the two Cairo first-of-month
    datetimes. Single source of truth for BOTH routes, so the HTML and JSON paths
    agree on what is valid (they differ only in how the error is surfaced).

    Raises InvalidTimelineRangeError when:
      - exactly one of the two is provided (both-or-neither),
      - either value is malformed (not "YYYY-MM"),
      - start_month is after end_month,
      - the inclusive span exceeds domain.MAX_CUSTOM_SPAN_MONTHS.
    """
    if start_month is None and end_month is None:
        return None
    if start_month is None or end_month is None:
        raise InvalidTimelineRangeError(
            "Provide BOTH start_month and end_month (as 'YYYY-MM'), or neither."
        )
    try:
        start_dt = _parse_month(start_month)
        end_dt = _parse_month(end_month)
    except (ValueError, TypeError) as exc:
        raise InvalidTimelineRangeError(
            f"start_month/end_month must be Cairo-local 'YYYY-MM' "
            f"(got {start_month!r}..{end_month!r})."
        ) from exc
    if start_dt > end_dt:
        raise InvalidTimelineRangeError(
            f"start_month ({_month_str(start_dt)}) is after end_month "
            f"({_month_str(end_dt)})."
        )
    span = _month_age(_month_str(start_dt), _month_str(end_dt)) + 1
    if span > domain.MAX_CUSTOM_SPAN_MONTHS:
        raise InvalidTimelineRangeError(
            f"Custom range span is {span} months; the maximum is "
            f"{domain.MAX_CUSTOM_SPAN_MONTHS}."
        )
    return start_dt, end_dt


def _month_age(period_month: str, current_month: str) -> int:
    """Whole Cairo months from `period_month` up to `current_month` (current == 0)."""
    py, pm = (int(x) for x in period_month.split("-"))
    cy, cm = (int(x) for x in current_month.split("-"))
    return (cy * 12 + cm) - (py * 12 + pm)


def _outcomes(group_counts: dict[str, int], total: int, label: str) -> list[dict]:
    """Build the 4-group outcomes list (count + %), asserting reconciliation.

    Mirrors campaign_service._outcomes: raises RuntimeError (not assert — survives
    python -O) if the 4 groups do not sum to total, refusing an inconsistent funnel.
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


def _maturation_state(
    new_count: int, total: int, period_month: str, current_month: str
) -> str:
    """Derive the maturation heuristic for a month (NOT a measurement — §F.5).

    new_pct = جديد share of the month. age = whole Cairo months to the current
    month (current == 0). too_early if young & still-raw; neglected if old &
    still-raw; otherwise (incl. zero-lead months) normal.
    """
    if total <= 0:
        return "normal"
    new_pct = 100.0 * new_count / total
    age = _month_age(period_month, current_month)
    high = new_pct >= domain.MATURATION_NEW_PCT_HIGH
    if high and age <= domain.MATURATION_YOUNG_MAX_AGE:
        return "too_early"
    if high and age >= domain.MATURATION_NEGLECTED_MIN_AGE:
        return "neglected"
    return "normal"


async def _fetch_all(client: OdooClient, model: str, dom: list, fields: list[str]) -> list[dict]:
    """search_read the whole domain in pages of _PAGE, ordered by id (reused pattern)."""
    rows, offset = [], 0
    while True:
        page = await client.execute_kw(
            model,
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


async def get_legacy_migration_days(client: OdooClient) -> set[str]:
    """Return the set of Cairo-local days (YYYY-MM-DD) that are bulk-migration scale
    (>= LEGACY_DAY_MIN leads) — the dynamic reproduction of discovery §F.1.

    Cached with a LONG TTL (>= 1 day): the migration is a stable historical fact, so
    we do NOT re-scan the whole population every 60s. The key is Cairo-date scoped,
    so it still rolls over at Cairo midnight like the rest of the module cache.
    """
    cache_key = _cache.make_key(_LEGACY_CACHE_KEY)
    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"Legacy-days cache hit: {cache_key}")
        return cached

    rows = await _fetch_all(client, _LEAD_MODEL, [], ["create_date"])
    by_day: Counter = Counter()
    for r in rows:
        cd = r.get("create_date")
        if cd:
            by_day[_to_cairo(cd).strftime("%Y-%m-%d")] += 1
    days = {d for d, c in by_day.items() if c >= domain.LEGACY_DAY_MIN}
    _cache.set(cache_key, days, ttl=_LEGACY_TTL_SECONDS)
    logger.info(
        f"Legacy migration days detected (>= {domain.LEGACY_DAY_MIN:,} leads/Cairo-day): "
        f"{sorted(days)}"
    )
    return days


async def get_campaign_timeline(
    client: Optional[OdooClient] = None,
    campaign_id: Optional[int] = None,
    window_months: int = domain.DEFAULT_WINDOW_MONTHS,
    trend_months: int = domain.DEFAULT_TREND_MONTHS,
    start_month: Optional[str] = None,
    end_month: Optional[str] = None,
    confirmed_campaigns: Optional[frozenset[str]] = None,
    denylist_campaigns: Optional[frozenset[str]] = None,
    legacy_days: Optional[set[str]] = None,
    now_cairo: Optional[datetime] = None,
) -> dict:
    """Return the per-campaign timeline (Level 2) for one campaign.

    Args:
        client: optional injected OdooClient (tests pass a mock; production opens
            and closes its own).
        campaign_id: the utm.campaign id to drill into (required, > 0).
        window_months: # of trailing Cairo months reported with a full funnel —
            the `months` preset. IGNORED when an explicit custom range is given.
        trend_months: # of trailing Cairo months in the volume trend (fixed by the
            endpoint to DEFAULT_TREND_MONTHS). The trend always ends at the
            window's END month (current month for a preset; end_month for a custom
            range), so the preset path is byte-for-byte unchanged.
        start_month / end_month: optional explicit Cairo-local "YYYY-MM" range. When
            BOTH are given and valid, they OVERRIDE window_months and define the
            inclusive [start_month .. end_month] funnel window; window_months in the
            result then reports the derived month count. Both-or-neither — see
            _resolve_custom_window for the validation contract.
        confirmed_campaigns / denylist_campaigns: optional gate overrides for tests.
        legacy_days: optional injected migration-day set (tests) — bypasses the
            live detection RPC entirely.
        now_cairo: optional injected Cairo-local "now" for deterministic windows.

    The result is cached ONLY for the default configuration (no gate/legacy/now
    overrides), keyed by campaign_id + window (preset months OR custom start_end) +
    trend, so a custom window never collides with a preset window and a test config
    never poisons the production cache — same pattern as the Level-1 service.

    Returns a dict matching schemas.CampaignTimeline (incl. is_custom_range).

    Raises:
        CampaignNotFoundError: campaign_id resolves to no utm.campaign record.
        InvalidTimelineRangeError: the custom start_month/end_month range is invalid.
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated.
        OdooQueryError: if any Odoo RPC fails.
        RuntimeError: if any month's 4 group counts fail to reconcile.
    """
    _assert_read_only()

    if campaign_id is None or int(campaign_id) <= 0:
        raise CampaignNotFoundError(f"campaign_id must be a positive int, got {campaign_id!r}")
    campaign_id = int(campaign_id)
    window_months = int(window_months)
    trend_months = int(trend_months)

    # Optional explicit custom range — validated BEFORE any RPC. None → the trailing
    # `months` preset path (unchanged). A tuple → (start_dt, end_dt) Cairo month
    # starts that OVERRIDE window_months.
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
    # preset (so that path stays byte-for-byte), the end_month for a custom range.
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

    # Cache key carries the window TAG (preset `w{n}` OR custom `c{start}_{end}`) so a
    # custom window never collides with a preset months window.
    cache_key = _cache.make_key(
        f"{_CACHE_KEY_PREFIX}:{campaign_id}:{window_tag}:t{trend_months}"
    )
    if default_config:
        cached = _cache.get(cache_key)
        if cached is not None:
            logger.debug(f"Cache hit: {cache_key}")
            return {**cached, "cache_status": "cached", "rpc_duration_ms": 0}
        logger.info(f"Cache miss: {cache_key} — querying Odoo")

    # Odoo create_date fetch bounds (Cairo month-starts → UTC; Odoo stores UTC-naive).
    # LOWER bound = the earliest month any figure needs (the funnel window OR the
    # trend, whichever reaches further back). For a custom range we ALSO set an UPPER
    # bound (first day after end_month) so a historical window does not drag in every
    # lead up to today. The preset path keeps its single lower bound, unchanged.
    # Bucketing stays DST-correct via per-lead Cairo regrouping below — these bounds
    # are only a coarse fetch filter, never the bucketing basis.
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

    t0 = time.monotonic()
    try:
        # ── RPC a — utm.campaign id+name (resolve gates + this campaign's name) ─
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

        if campaign_id not in id_to_name:
            raise CampaignNotFoundError(
                f"campaign_id={campaign_id} resolves to no utm.campaign record."
            )
        campaign_name = id_to_name[campaign_id]

        # ── RPC b — crm.stage id+name+is_won (outcome group mapping) ───────────
        stages = await _client.execute_kw(
            _STAGE_MODEL,
            "search_read",
            args=[[]],
            kwargs={"fields": ["id", "name", "is_won"]},
        )

        # ── RPC c — this campaign's leads in the date window (paged) ───────────
        # Preset: lower-bounded only. Custom: also upper-bounded at end_month+1.
        lead_domain = [(CAMPAIGN_FIELD, "=", campaign_id), ("create_date", ">=", bound_str)]
        if upper_str is not None:
            lead_domain.append(("create_date", "<", upper_str))
        leads = await _fetch_all(
            _client,
            _LEAD_MODEL,
            lead_domain,
            ["create_date", "stage_id"],
        )

        # ── RPC d — this campaign's ALL-TIME both-set slice by media buyer ─────
        # Unbounded (no create_date filter) so the header buyer == the Level-1 row.
        both_set_rows = await _client.execute_kw(
            _LEAD_MODEL,
            "read_group",
            args=[
                [(CAMPAIGN_FIELD, "=", campaign_id), (BUYER_FIELD, "!=", False)],
                [BUYER_FIELD],
                [BUYER_FIELD],
            ],
            kwargs={"context": _CTX_ALL, "lazy": False},
        )

        # ── RPC e — legacy migration days (cached long; injectable for tests) ──
        resolved_legacy = (
            set(legacy_days) if legacy_days is not None
            else await get_legacy_migration_days(_client)
        )
    except (ReadOnlyViolationError, CampaignNotFoundError):
        raise
    except Exception as exc:
        raise OdooQueryError(f"get_campaign_timeline() RPC failed: {exc}") from exc
    finally:
        if client is None:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)

    # ── resolve the gate name→id sets (same _resolve pattern as Level 1) ──────
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

    # ── stage info for classify_stage (identical to Level 1) ──────────────────
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
    # trend_months_list and period_months_list were resolved with the window above.
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
                "outcomes": _outcomes(counts, total, f"campaign id={campaign_id} month {m}"),
                "maturation_state": _maturation_state(
                    counts.get(GROUP_NEW, 0), total, m, current_month
                ),
            }
        )

    total_leads_in_window = sum(p["lead_count"] for p in periods)

    # ── header media buyer (shared rule; same all-time both-set as Level 1) ───
    buyers: Counter = Counter()
    buyer_names: dict[int, Optional[str]] = {}
    both_set_count = 0
    for r in both_set_rows:
        bid, bname = _m2o(r.get(BUYER_FIELD))
        if bid is None:
            continue
        cnt = int(r.get("__count") or 0)
        buyers[bid] += cnt
        buyer_names[bid] = bname
        both_set_count += cnt

    status, buyer_id, buyer_name, concentration, both_out, alert = derive_buyer_status(
        campaign_id,
        campaign_name if campaign_name else campaign_id,
        buyers,
        buyer_names,
        both_set_count,
        is_confirmed=campaign_id in confirmed_ids,
        is_denylisted=campaign_id in denylist_ids,
    )
    integrity_alerts: list[str] = []
    if alert is not None:
        integrity_alerts.append(alert)
        logger.error(alert)

    window_desc = (
        f"custom {period_months_list[0]}..{period_months_list[-1]} ({window_months}mo)"
        if is_custom_range else f"{window_months}mo"
    )
    logger.info(
        f"Campaign timeline: id={campaign_id} {campaign_name!r} | "
        f"window={window_desc} trend={trend_months}mo | "
        f"window_leads={total_leads_in_window:,} status={status} | "
        f"legacy_days={len(resolved_legacy)} | RPCs in {rpc_ms}ms | "
        f"alerts={len(integrity_alerts)} warnings={len(config_warnings)}"
    )

    result: dict = {
        "header": {
            "campaign_id": campaign_id,
            "campaign_name": campaign_name,
            "total_leads_in_window": total_leads_in_window,
            "attribution_status": status,
            "media_buyer_id": buyer_id,
            "media_buyer_name": buyer_name,
            "concentration": concentration,
            "both_set_count": both_out,
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
