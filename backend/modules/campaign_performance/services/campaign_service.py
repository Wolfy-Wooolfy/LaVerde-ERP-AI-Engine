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
from datetime import datetime, timedelta, timezone
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
# Window primitives are REUSED VERBATIM from the Level-2 timeline service (single
# source of truth for Cairo bucketing, the custom-range contract, and dynamic
# legacy-migration detection) — never re-declared here. timeline_service does not
# import campaign_service, so this one-way import introduces no cycle.
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

_CACHE_KEY_PREFIX = "campaign_performance:overview"
_CACHE_KEY_PREFIX_WINDOWED = "campaign_performance:windowed"
_CACHE_KEY_PREFIX_GRAND = "campaign_performance:grand_totals"
# The grand-totals aggregate read_groups the WHOLE population (~147k leads, one RPC
# seen at ~28s live) and the pinned block loads on every page view, yet the figures
# only change ~daily. Hold it for an hour (vs the default 60s) — the cache key is
# already Cairo-date-stamped, so it still refreshes at most once per Cairo day.
_GRAND_TTL_SECONDS = 60 * 60
_CAIRO_TZ = ZoneInfo("Africa/Cairo")
_PAGE = 5000

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


def _legacy_days_domain(legacy_days: set[str]) -> Optional[list]:
    """Build a POSITIVE Odoo domain matching every lead created on any legacy
    migration day, as the OR of each Cairo day's [day_start, next_day_start) UTC
    range.

    Each "YYYY-MM-DD" Cairo day is turned into its UTC half-open bounds with the
    SAME Cairo→UTC handling the timeline/windowed fetch uses (build the Cairo-aware
    first-instant of the day and of the next day, then .astimezone(UTC)). The ranges
    are OR-ed in Odoo prefix (polish) notation — positive ranges, no negation. The
    DST-safe next-day boundary is built from a fresh date (not timedelta on an aware
    datetime), identical to the live verification's _day_bounds_utc.

    Returns None when there are no legacy days (nothing to exclude) so the caller can
    skip the RPC entirely (migration counts then collapse to zero).
    """
    days = sorted(legacy_days)
    if not days:
        return None
    ranges: list = []
    for d in days:
        day = datetime.strptime(d, "%Y-%m-%d").date()
        nxt = day + timedelta(days=1)
        lo = datetime(day.year, day.month, day.day, tzinfo=_CAIRO_TZ)
        hi = datetime(nxt.year, nxt.month, nxt.day, tzinfo=_CAIRO_TZ)
        lo_str = lo.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        hi_str = hi.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        ranges.append(["&", ("create_date", ">=", lo_str), ("create_date", "<", hi_str)])
    domain_out: list = ["|"] * (len(ranges) - 1)
    for rng in ranges:
        domain_out.extend(rng)
    return domain_out


async def get_campaign_grand_totals(
    client: Optional[OdooClient] = None,
    legacy_days: Optional[set[str]] = None,
    now_cairo: Optional[datetime] = None,
) -> dict:
    """Return the window-INDEPENDENT grand-totals funnel for the whole population.

    Two all-time 4-group funnels, GROUPED (never a row fetch — no 130k-row scan):
      - incl: ALL leads (active_test=False) INCLUDING the Nov-2025 legacy migration —
        the full population funnel (ties 1:1 to the overview's aggregate funnel and
        total_leads_population).
      - excl: the same with the legacy migration SUBTRACTED per group — what the
        ongoing, non-migration funnel looks like.

    Both are built from read_group-by-stage_id counts classified through the SAME
    stage_info + classify_stage the overview uses, so the group shape matches the row
    funnels exactly. The migration slice is one extra read_group restricted to the
    legacy days' UTC ranges (see _legacy_days_domain); excl[g] = incl[g] −
    migration[g], with every excl[g] guarded >= 0 (explicit raise — survives -O).

    Args:
        client: optional injected OdooClient (tests pass a mock; production opens and
            closes its own).
        legacy_days: optional injected migration-day set (tests) — bypasses the live
            detection RPC entirely.
        now_cairo: optional injected Cairo-local "now" (tests) — pins reference_date.

    The result is cached ONLY for the default configuration (no legacy/now overrides)
    under its own key, the same pattern as the overview/windowed services.

    Returns:
        {
          "incl": {"total": int, "groups": [{group, count, pct}, ...]},
          "excl": {"total": int, "groups": [{group, count, pct}, ...]},
          "migration_total": int,
          "legacy_days": [str, ...],
          "reference_date": str, "as_of": str,
          "cache_status": str, "rpc_duration_ms": int,
        }

    Raises:
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated.
        OdooQueryError: if any Odoo RPC fails.
        RuntimeError: if a funnel fails to reconcile, or any excl group would go
            negative (migration count exceeds the all-time count for that group).
    """
    _assert_read_only()

    default_config = legacy_days is None and now_cairo is None

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
        # ── RPC 1 — crm.stage id+name+is_won (outcome group mapping) ──────────
        stages = await _client.execute_kw(
            _STAGE_MODEL,
            "search_read",
            args=[[]],
            kwargs={"fields": ["id", "name", "is_won"]},
        )

        # ── RPC 2 — ALL leads grouped by stage_id (incl. migration + archived) ─
        all_by_stage = await _client.execute_kw(
            _LEAD_MODEL,
            "read_group",
            args=[[], ["stage_id"], ["stage_id"]],
            kwargs={"context": _CTX_ALL, "lazy": False},
        )

        # ── RPC 3 — legacy migration days (cached long; injectable for tests) ──
        resolved_legacy = (
            set(legacy_days) if legacy_days is not None
            else await get_legacy_migration_days(_client)
        )

        # ── RPC 4 — migration leads grouped by stage_id (legacy days ONLY) ─────
        # Domain = OR of the legacy days' UTC ranges (positive). Skipped entirely
        # when no legacy days were detected (migration then collapses to zero).
        migration_domain = _legacy_days_domain(resolved_legacy)
        if migration_domain is not None:
            migration_by_stage = await _client.execute_kw(
                _LEAD_MODEL,
                "read_group",
                args=[migration_domain, ["stage_id"], ["stage_id"]],
                kwargs={"context": _CTX_ALL, "lazy": False},
            )
        else:
            migration_by_stage = []
    except ReadOnlyViolationError:
        raise
    except Exception as exc:
        raise OdooQueryError(
            f"get_campaign_grand_totals() RPC failed: {exc}"
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

    def _classify_by_stage(rows: list[dict]) -> tuple[dict[str, int], int]:
        groups = {g: 0 for g in GROUP_ORDER}
        total = 0
        for r in rows:
            cnt = int(r.get("__count") or 0)
            total += cnt
            sid, _ = _m2o(r.get("stage_id"))   # stage_id may be False -> None -> جديد
            groups[classify_stage(sid, stage_info)] += cnt
        return groups, total

    incl_groups, incl_total = _classify_by_stage(all_by_stage)
    migration_groups, migration_total = _classify_by_stage(migration_by_stage)

    # ── excl = incl − migration, per group, guarded non-negative ──────────────
    excl_groups: dict[str, int] = {}
    for g in GROUP_ORDER:
        diff = incl_groups[g] - migration_groups[g]
        if diff < 0:
            raise RuntimeError(
                f"Grand-totals reconciliation FAILED for group {g!r}: migration "
                f"{migration_groups[g]} exceeds all-time {incl_groups[g]} "
                f"(excl would be {diff} < 0). Refusing an inconsistent funnel."
            )
        excl_groups[g] = diff
    excl_total = incl_total - migration_total

    logger.info(
        f"Campaign grand totals: incl={incl_total:,} migration={migration_total:,} "
        f"excl={excl_total:,} | legacy_days={len(resolved_legacy)} | RPCs in {rpc_ms}ms "
        f"| cache_key={cache_key}"
    )

    result: dict = {
        "incl": {
            "total": incl_total,
            "groups": _outcomes(incl_groups, incl_total, "grand_totals incl migration"),
        },
        "excl": {
            "total": excl_total,
            "groups": _outcomes(excl_groups, excl_total, "grand_totals excl migration"),
        },
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


async def _fetch_all_windowed(
    client: OdooClient, dom: list, fields: list[str]
) -> list[dict]:
    """search_read the whole domain in pages of _PAGE, ordered by id.

    The SAME paged-fetch pattern timeline_service uses; kept local so the windowed
    list owns its fetch (its domain carries the create_date bounds).
    """
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


async def get_campaign_performance_windowed(
    client: Optional[OdooClient] = None,
    window: str = domain.DEFAULT_WINDOW,
    start_month: Optional[str] = None,
    end_month: Optional[str] = None,
    confirmed_campaigns: Optional[frozenset[str]] = None,
    denylist_campaigns: Optional[frozenset[str]] = None,
    legacy_days: Optional[set[str]] = None,
    now_cairo: Optional[datetime] = None,
) -> dict:
    """Return the per-campaign Level-1 list SCOPED to a Cairo time window.

    Same per-campaign funnel + 5-state media-buyer cell as the all-time overview,
    but every figure is restricted to the leads that AROSE in the window (Cairo
    create_date), the legacy Nov-2025 migration EXCLUDED (consistent with the
    timeline). Lists EVERY campaign with >=1 windowed lead individually — no
    long-tail roll-up, no volume threshold — and hides zero-activity campaigns. The
    media-buyer cell stays the ALL-TIME both-set status (identical to the list and
    the timeline header), so a campaign's buyer label never shifts with the window.

    Window resolution:
      - An explicit, valid start_month/end_month range OVERRIDES `window` and drives
        a custom window (is_custom_range=True), validated by the SAME
        _resolve_custom_window contract the timeline uses.
      - Otherwise `window` is a DATED preset key in domain.WINDOW_PRESET_MONTHS
        (e.g. "current", "last3"), a trailing span ending at the current Cairo month.
      - The "all" window is NOT handled here — callers route it to
        get_campaign_performance_overview() (the shipped, un-windowed path).

    Args mirror get_campaign_performance_overview plus:
        window: a dated preset key (domain.WINDOW_PRESET_MONTHS). Ignored when a
            custom range is given. Defaults to domain.DEFAULT_WINDOW.
        start_month / end_month: optional Cairo-local "YYYY-MM" custom range
            (both-or-neither — see _resolve_custom_window).
        legacy_days / now_cairo: optional test injections (bypass the legacy RPC /
            pin "now") — same as the timeline service.

    Returns a dict matching schemas.CampaignPerformanceWindowed.

    Raises:
        InvalidTimelineRangeError: the custom start_month/end_month range is invalid.
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated.
        OdooQueryError: if any Odoo RPC fails.
        RuntimeError: if a funnel fails to reconcile, or the windowed population fails
            the listed + junk + no-campaign identity.
    """
    _assert_read_only()

    # Custom range validated BEFORE any RPC (both-or-neither, span <= cap). None →
    # the dated preset path.
    custom_window = _resolve_custom_window(start_month, end_month)
    is_custom_range = custom_window is not None

    if not is_custom_range and window not in domain.WINDOW_PRESET_MONTHS:
        raise InvalidTimelineRangeError(
            f"window must be one of {sorted(domain.WINDOW_PRESET_MONTHS)} "
            f"or a custom start_month/end_month range — got {window!r}."
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
        span = domain.WINDOW_PRESET_MONTHS[window]
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
    # exact Cairo bucketing/legacy-day drop happens per-lead below — these bounds are
    # only a fetch filter. Lower = window start; upper = first day after window end.
    lower_str = start_dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    upper_str = _shift_months(end_dt, 1).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

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

        # ── RPC 2 — crm.stage id+name+is_won (outcome group mapping) ──────────
        stages = await _client.execute_kw(
            _STAGE_MODEL,
            "search_read",
            args=[[]],
            kwargs={"fields": ["id", "name", "is_won"]},
        )

        # ── RPC 3 — THE windowed query: leads in [lower .. upper) (paged) ─────
        # ONE search_read of create_date + campaign_id + stage_id, regrouped in
        # Python by Cairo month (legacy days dropped) — the discovery-recommended
        # single-query path (1 RPC for the whole list, vs N per-campaign).
        windowed_leads = await _fetch_all_windowed(
            _client,
            [("create_date", ">=", lower_str), ("create_date", "<", upper_str)],
            ["create_date", CAMPAIGN_FIELD, "stage_id"],
        )

        # ── RPC 4 — ALL-TIME both-set slice by (campaign, buyer) ──────────────
        # Unbounded (no date filter) so the media-buyer cell == the all-time list.
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
            f"get_campaign_performance_windowed() RPC failed: {exc}"
        ) from exc
    finally:
        if client is None:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)

    # ── name<->id maps + gate resolution (same pattern as the all-time path) ──
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

    # ── regroup windowed leads by Cairo month, dropping legacy days ───────────
    funnel: dict[Optional[int], dict[str, int]] = defaultdict(
        lambda: {g: 0 for g in GROUP_ORDER}
    )
    lead_count: dict[Optional[int], int] = defaultdict(int)
    windowed_population = 0
    for r in windowed_leads:
        cd = r.get("create_date")
        if not cd:
            continue
        cairo = _to_cairo(cd)
        if cairo.strftime("%Y-%m-%d") in resolved_legacy:
            continue                                   # drop the legacy migration
        if _month_str(cairo) not in window_month_set:  # exact-bound / over-fetch guard
            continue
        cid, _ = _m2o(r.get(CAMPAIGN_FIELD))           # cid is None for the no-campaign bucket
        sid, _ = _m2o(r.get("stage_id"))               # stage_id may be False -> None -> جديد
        funnel[cid][classify_stage(sid, stage_info)] += 1
        lead_count[cid] += 1
        windowed_population += 1

    # ── per-campaign dominant buyer from ALL-TIME both-set (RPC 4) ────────────
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

    def _status_and_buyer(cid: int) -> tuple:
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

    # ── active real campaigns (>=1 windowed lead): list ALL, sort by volume ───
    active_real_ids = [c for c in lead_count if c is not None and c not in junk_ids]
    ranked = sorted(active_real_ids, key=lambda c: (-lead_count[c], id_to_name.get(c, "")))

    campaigns_out: list[dict] = []
    for cid in ranked:
        status, bid, bname, conc, both = _status_and_buyer(cid)
        campaigns_out.append(
            {
                "campaign_id": cid,
                "campaign_name": id_to_name.get(cid, f"id={cid}"),
                "lead_count": lead_count[cid],
                "outcomes": _outcomes(funnel[cid], lead_count[cid], f"campaign id={cid} (windowed)"),
                "attribution_status": status,
                "media_buyer_id": bid,
                "media_buyer_name": bname,
                "concentration": conc,
                "both_set_count": both,
            }
        )

    for alert in integrity_alerts:
        logger.error(alert)

    # ── data-quality buckets (windowed; NOT list rows) ────────────────────────
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
            "outcomes": _outcomes(junk_groups, junk_leads, "junk None campaign (windowed)"),
        }

    no_campaign_out: Optional[dict] = None
    no_campaign_leads = lead_count.get(None, 0)
    if no_campaign_leads:
        no_campaign_out = {
            "label": "(no campaign)",
            "campaign_ids": [],
            "lead_count": no_campaign_leads,
            "outcomes": _outcomes(funnel[None], no_campaign_leads, "no-campaign bucket (windowed)"),
        }

    # ── windowed population identity (explicit raise; survives -O) ────────────
    listed_leads = sum(lead_count[c] for c in active_real_ids)
    recon_total = listed_leads + junk_leads + no_campaign_leads
    if recon_total != windowed_population:
        raise RuntimeError(
            f"Windowed population reconciliation FAILED: listed {listed_leads} + junk "
            f"{junk_leads} + no_campaign {no_campaign_leads} = {recon_total} != windowed "
            f"population {windowed_population}."
        )

    logger.info(
        f"Campaign performance (windowed): window={window_tag} "
        f"[{window_months_list[0]}..{window_months_list[-1]}] | active={len(active_real_ids)} "
        f"windowed_leads={windowed_population:,} | legacy_days={len(resolved_legacy)} | "
        f"RPCs in {rpc_ms}ms | alerts={len(integrity_alerts)} warnings={len(config_warnings)} | "
        f"cache_key={cache_key}"
    )

    result: dict = {
        "campaigns": campaigns_out,
        "data_quality": {"junk_none": junk_out, "no_campaign": no_campaign_out},
        "total_leads_population": windowed_population,
        "active_campaign_count": len(active_real_ids),
        "window": domain.WINDOW_CUSTOM if is_custom_range else window,
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
