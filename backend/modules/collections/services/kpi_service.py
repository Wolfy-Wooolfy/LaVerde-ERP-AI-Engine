"""
Collections KPI service — business logic for all 6 MVP KPIs.

Data source: rs.installment (42,970 records as of 2026-05-14) via the
shared read-only OdooClient. All methods are async; no method ever calls
create, write, or unlink.

Session 1 scope: get_late_uncollected() (KPI 2 — Late Uncollected).
KPIs 1, 3, 4, 5, 6 are implemented in future sessions.
"""

import calendar
import time
from collections import defaultdict
from datetime import date, datetime, time as dt_time, timezone
from zoneinfo import ZoneInfo
from typing import Optional

from loguru import logger

from backend.core.exceptions import OdooQueryError, ReadOnlyViolationError, UnknownProjectError
from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient
from backend.modules.collections.services import cache as _cache
from backend.modules.collections.installment_type_names import (
    INSTALLMENT_TYPE_NAMES_AR,
    get_type_name_ar,
    _UNKNOWN_TYPE_AR,
)

# Methods that must never appear in ALLOWED_METHODS.
_FORBIDDEN_WRITE_METHODS = frozenset({"create", "write", "unlink"})

_MODEL = "rs.installment"
_CACHE_KEY_PREFIX = "kpi:late_uncollected"
_CACHE_KEY_PREFIX_KPI1 = "kpi:total_portfolio_value"
_CACHE_KEY_PREFIX_KPI5 = "kpi:late_uncollected_by_project"
_CACHE_KEY_PREFIX_KPI3 = "kpi:pending_check_exposure"
_CACHE_KEY_PREFIX_KPI6 = "kpi:collection_trend_6m"
_CACHE_KEY_PREFIX_KPI4  = "kpi:collection_rate"
_CACHE_KEY_PREFIX_KPI5B = "kpi:collection_rate_by_project"
_CACHE_KEY_PREFIX_KPI7 = "kpi:expected_forecast"

# KPI 6 uses the payment installment header model (Decision 5.6).
# The HEADER.date field is user-entered (cash receipt date); HEADER.amount
# is proven identity-equal to SUM(LINE.amount) — D0 Part 2 Finding B.
_PAYMENT_HEADER_MODEL = "rs.account.payment.installment"
_CACHE_TTL_KPI6 = 3600  # 1 hour — trend data is stable within a session

# Arabic month labels (Decision 5.5 — hardcoded, no babel dependency).
_ARABIC_MONTHS: dict[int, str] = {
    1: "يناير",   2: "فبراير",  3: "مارس",    4: "أبريل",
    5: "مايو",    6: "يونيو",   7: "يوليو",   8: "أغسطس",
    9: "سبتمبر", 10: "أكتوبر", 11: "نوفمبر", 12: "ديسمبر",
}

# La Verde operates in Egypt (Africa/Cairo — UTC+2 Nov-Apr, UTC+3 May-Oct per
# tzdata 2025.2; Egypt re-introduced DST in 2023). ZoneInfo handles transitions
# automatically. Decision 5.9.
_LA_VERDE_TZ = ZoneInfo("Africa/Cairo")
_UTC_TZ = ZoneInfo("UTC")

# Phase 2 confirmed project IDs and clean display names (MODULE_2_DISCOVERY_PHASE_2.md §6).
# Odoo returns "Project#New Capital" etc.; we expose clean names to API consumers.
# If read_group returns an ID not in this dict, UnknownProjectError is raised — a signal
# that a new Odoo project has appeared and needs a code update here.
_PROJECT_NAMES: dict[int, str] = {
    1: "New Capital",
    2: "Cassette",
    3: "La puerta",
}


def _assert_read_only() -> None:
    """Defense-in-depth: abort if any write method has leaked into ALLOWED_METHODS."""
    violations = ALLOWED_METHODS & _FORBIDDEN_WRITE_METHODS
    if violations:
        raise ReadOnlyViolationError(
            f"ALLOWED_METHODS contains forbidden write method(s): {sorted(violations)}. "
            "The Odoo client is no longer strictly read-only. Halting before any RPC."
        )


def _build_late_domain(today: str) -> list:
    # Immutable — Candidate C, three-clause form validated in
    # MODULE_2_DISCOVERY_PHASE_2.md §3. Do not modify without Khaled's approval.
    return [
        ("state", "=", "post"),
        ("payment_state", "in", ["unpaid", "partial"]),
        ("date", "<", today),
    ]


async def get_late_uncollected(client: Optional[OdooClient] = None) -> dict:
    """Return KPI 2 — Late Uncollected.

    Queries rs.installment for posted, partially-paid-or-unpaid installments
    whose due date is before today, and returns SUM(amount) − SUM(actual_paid_amount)
    plus the cheques subset annotation via Alternative B (Decision 10.2).

    Formula: PATH A (Decision 11.13, Session 12) — reverses Decision 10.1 PATH C.
    Pre-implementation discovery confirmed safe on live Odoo (commit 14600f3,
    2026-05-20): H2 identity exact to 0.000000 EGP on the Late subset.

    A single read_group call aggregates all monetary fields simultaneously
    (amount, paid_amount, x_studio_actual_paid_amount, total_due_amount) — 1 RPC
    total, C3 constraint preserved.

    Value formula (PATH A, Decision 11.13):
        value = SUM(amount) − SUM(x_studio_actual_paid_amount)

    Identity cross-check: value ≈ SUM(total_due_amount). Tiered thresholds:
        delta < 1 EGP: no flag; 1–999 EGP: INFO log; ≥ 1000 EGP: WARNING + flag.

    Cheques formula (Alternative B, Decision 10.2 / Decision 9.1 analog):
        cheques_in_pipeline = max(SUM(paid_amount) - SUM(x_studio_actual_paid_amount), 0)
    cheques_record_count is null (Alternative B limitation — per-installment
    count unavailable via read_group net formula).

    data_quality_warning priority: "negative_cheques" > "kpi2_identity_mismatch".

    Return shape::

        {
            "value":                     float,  # EGP SUM(amount) − SUM(actual_paid_amount)
            "currency":                  "EGP",
            "record_count":              int,    # matched installment count
            "cheques_in_pipeline":       float,  # EGP uncashed cheques (Alt B)
            "cheques_record_count":      None,   # Alt B limitation
            "drill_down_domain":         list,   # Candidate C domain (= domain)
            "cheques_drill_down_domain": None,   # Alt B limitation
            "as_of":                     str,    # ISO 8601 UTC datetime of the query
            "cache_status":              str,    # "fresh" or "cached"
            "rpc_duration_ms":           int,    # 0 if served from cache
            "domain":                    list,   # legacy field — same value as drill_down_domain
            "data_quality_warning":      str | None,  # "negative_cheques", "kpi2_identity_mismatch", or null
        }

    Raises:
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated with a write method.
        OdooQueryError: if the Odoo RPC fails for any reason (network, auth, RPC error).
    """
    _assert_read_only()

    # Decision 1.7: prefer Odoo server date. ALLOWED_METHODS exposes no
    # server-date method, so falling back to UTC date per Khaled's
    # authorization (MODULE_2_IMPLEMENTATION_DECISIONS.md §1.7).
    # Using _cache.today_str() so the domain date and cache key date are
    # always identical — tests patch a single function to control both.
    today = _cache.today_str()

    domain = _build_late_domain(today)
    cache_key = _cache.make_key(_CACHE_KEY_PREFIX)

    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"Cache hit: {cache_key}")
        return {**cached, "cache_status": "cached", "rpc_duration_ms": 0}

    logger.info(f"Cache miss: {cache_key} — querying Odoo")

    _client = client if client is not None else OdooClient()

    t0 = time.monotonic()
    try:
        rows = await _client.execute_kw(
            _MODEL,
            "read_group",
            args=[domain, ["amount", "paid_amount", "x_studio_actual_paid_amount", "total_due_amount"], []],
            kwargs={"lazy": False},
        )
    except Exception as exc:
        raise OdooQueryError(
            f"read_group on {_MODEL} failed: {exc}"
        ) from exc
    finally:
        if client is None:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)
    logger.info(f"Odoo read_group on {_MODEL} in {rpc_ms}ms | cache_key={cache_key}")

    row = rows[0] if rows else {}
    amount_sum   = float(row.get("amount") or 0.0)
    count        = int(row.get("__count") or 0)
    paid_amount  = float(row.get("paid_amount") or 0.0)
    actual_paid  = float(row.get("x_studio_actual_paid_amount") or 0.0)
    total_due    = float(row.get("total_due_amount") or 0.0)
    value        = amount_sum - actual_paid

    cheques_raw         = paid_amount - actual_paid
    cheques_in_pipeline = max(cheques_raw, 0.0)

    # "negative_cheques" takes priority over "kpi2_identity_mismatch" (Risk 3, §6).
    data_quality_warning: Optional[str] = None
    if cheques_raw < 0:
        logger.warning(
            "KPI 2: negative cheques_raw — paid_amount < x_studio_actual_paid_amount. "
            "Data quality anomaly in Odoo Studio fields."
        )
        data_quality_warning = "negative_cheques"
    else:
        identity_delta = abs(value - total_due)
        if identity_delta < 1.0:
            pass
        elif identity_delta < 1000.0:
            logger.info(
                "KPI 2: PATH A identity micro-drift — value=%.2f, total_due=%.2f, "
                "delta=%.2f EGP (acceptable, < 1,000 EGP threshold).",
                value, total_due, identity_delta,
            )
        else:
            logger.warning(
                "KPI 2: PATH A identity mismatch — value=%.2f, total_due=%.2f, "
                "delta=%.2f EGP (>= 1,000 EGP threshold).",
                value, total_due, identity_delta,
            )
            data_quality_warning = "kpi2_identity_mismatch"

    result: dict = {
        "value":                     value,
        "currency":                  "EGP",
        "record_count":              count,
        "cheques_in_pipeline":       cheques_in_pipeline,
        "cheques_record_count":      None,
        "drill_down_domain":         list(domain),
        "cheques_drill_down_domain": None,
        "as_of":                     datetime.now(timezone.utc).isoformat(),
        "cache_status":              "fresh",
        "rpc_duration_ms":           rpc_ms,
        "domain":                    list(domain),
        "data_quality_warning":      data_quality_warning,
    }

    _cache.set(cache_key, result)
    return result


async def get_total_portfolio_value(client: Optional[OdooClient] = None) -> dict:
    """Return KPI 1 — Total Portfolio Value.

    Queries rs.installment filtered to state='post' (posted installments only)
    and returns SUM(amount) via read_group.

    Domain: [('state', '=', 'post')] — excludes draft (19 records) and
    cancelled (508 records) installments that exist in rs.installment but
    are excluded by Odoo's "All Installments" view. See Decision 2.4.

    Return shape::

        {
            "value":           float,  # EGP total amount across posted installments
            "currency":        "EGP",
            "record_count":    int,    # posted installment count (~42,443)
            "as_of":           str,    # ISO 8601 UTC datetime of the query
            "cache_status":    str,    # "fresh" or "cached"
            "rpc_duration_ms": int,    # 0 if served from cache
            "domain":          list,   # [('state', '=', 'post')]
        }

    Raises:
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated with a write method.
        OdooQueryError: if the Odoo RPC fails for any reason (network, auth, RPC error).
    """
    _assert_read_only()

    domain: list = [("state", "=", "post")]
    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_KPI1)

    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"Cache hit: {cache_key}")
        return {**cached, "cache_status": "cached", "rpc_duration_ms": 0}

    logger.info(f"Cache miss: {cache_key} — querying Odoo")

    _client = client if client is not None else OdooClient()

    t0 = time.monotonic()
    try:
        rows = await _client.execute_kw(
            _MODEL,
            "read_group",
            args=[domain, ["amount"], []],
            kwargs={"lazy": False},
        )
    except Exception as exc:
        raise OdooQueryError(
            f"read_group on {_MODEL} failed: {exc}"
        ) from exc
    finally:
        if client is None:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)
    logger.info(f"Odoo read_group on {_MODEL} in {rpc_ms}ms | cache_key={cache_key}")

    row = rows[0] if rows else {}
    value = float(row.get("amount") or 0.0)
    count = int(row.get("__count") or 0)

    result: dict = {
        "value": value,
        "currency": "EGP",
        "record_count": count,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "cache_status": "fresh",
        "rpc_duration_ms": rpc_ms,
        "domain": domain,
    }

    _cache.set(cache_key, result)
    return result


async def get_pending_check_exposure(client: Optional[OdooClient] = None) -> dict:
    """Return KPI 3 — Pending Check Exposure.

    Queries rs.installment filtered to state='post' and aggregates both
    SUM(paid_amount) and SUM(x_studio_actual_paid_amount) in a single
    read_group call. The derived value (paid - actual) represents checks
    received from customers not yet cashed by the bank.

    Domain: [('state', '=', 'post')] — excludes draft and cancelled
    installments. Cancelled records carry historical paid_amount from
    pre-cancellation cheque submissions; those pending amounts are not
    part of the active collection pipeline. See Decision 4.1.

    Return shape::

        {
            "value":                float,       # EGP, paid_amount_sum - actual_paid_sum
            "currency":             "EGP",
            "record_count":         int,         # posted installments matched (~42,443)
            "as_of":                str,         # ISO 8601 UTC datetime
            "cache_status":         str,         # "fresh" | "cached"
            "rpc_duration_ms":      int,         # 0 if served from cache
            "domain":               list,        # [('state', '=', 'post')]
            "paid_amount_sum":      float,       # SUM(paid_amount)
            "actual_paid_sum":      float,       # SUM(x_studio_actual_paid_amount)
            "derivation_note":      str,         # "value = paid_amount_sum - actual_paid_sum"
            "data_quality_warning": str | None,  # "value_is_negative" or None (Decision 4.4)
        }

    Raises:
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated with a write method.
        OdooQueryError: if the Odoo RPC fails for any reason (network, auth, RPC error).
    """
    _assert_read_only()

    domain: list = [("state", "=", "post")]
    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_KPI3)

    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"Cache hit: {cache_key}")
        return {**cached, "cache_status": "cached", "rpc_duration_ms": 0}

    logger.info(f"Cache miss: {cache_key} — querying Odoo")

    _client = client if client is not None else OdooClient()

    t0 = time.monotonic()
    try:
        rows = await _client.execute_kw(
            _MODEL,
            "read_group",
            args=[domain, ["paid_amount", "x_studio_actual_paid_amount"], []],
            kwargs={"lazy": False},
        )
    except Exception as exc:
        raise OdooQueryError(
            f"read_group on {_MODEL} failed: {exc}"
        ) from exc
    finally:
        if client is None:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)
    logger.info(f"Odoo read_group on {_MODEL} in {rpc_ms}ms | cache_key={cache_key}")

    row = rows[0] if rows else {}
    paid_amount_sum = float(row.get("paid_amount") or 0.0)
    actual_paid_sum = float(row.get("x_studio_actual_paid_amount") or 0.0)
    count = int(row.get("__count") or 0)
    value = paid_amount_sum - actual_paid_sum

    data_quality_warning: Optional[str] = None
    if value < 0:
        # Decision 4.4 (Option A): return as-is, log warning, set flag.
        # A negative derived value indicates x_studio_actual_paid_amount exceeds
        # paid_amount — a Studio field computation anomaly (Phase 2 §8 Finding 8b).
        logger.warning(
            "KPI 3 derived value is negative: paid_amount_sum=%s, "
            "actual_paid_sum=%s, value=%s. This indicates a data "
            "quality anomaly in Odoo Studio fields — see Decision 1.4 "
            "and Phase 2 §8 Finding 8b.",
            paid_amount_sum, actual_paid_sum, value,
        )
        data_quality_warning = "value_is_negative"

    result: dict = {
        "value": value,
        "currency": "EGP",
        "record_count": count,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "cache_status": "fresh",
        "rpc_duration_ms": rpc_ms,
        "domain": domain,
        "paid_amount_sum": paid_amount_sum,
        "actual_paid_sum": actual_paid_sum,
        "derivation_note": "value = paid_amount_sum - actual_paid_sum",
        "data_quality_warning": data_quality_warning,
    }

    _cache.set(cache_key, result)
    return result


async def get_late_uncollected_by_project(client: Optional[OdooClient] = None) -> dict:
    """Return KPI 5 (sub-metric 1) — Late Uncollected per project.

    Queries rs.installment with the same three-clause Candidate C domain as KPI 2,
    grouped by project_id. Returns SUM(due_amount) and record count for each of the
    3 active projects, always in order: New Capital (1), Cassette (2), La puerta (3).

    If read_group returns fewer than 3 projects (e.g., one project has zero late
    records), the missing project is zero-padded in the response (Decision 3.4).

    Return shape::

        {
            "projects": [
                {
                    "project_id":       int,
                    "project_name":     str,
                    "late_uncollected": float,  # EGP, SUM(due_amount)
                    "record_count":     int,
                },
                # ... ordered by project_id ascending: 1, 2, 3
            ],
            "total_late_uncollected": float,  # EGP, sum across all 3 projects
            "total_record_count":     int,
            "currency":               "EGP",
            "as_of":                  str,     # ISO 8601 UTC datetime
            "cache_status":           str,     # "fresh" | "cached"
            "rpc_duration_ms":        int,     # 0 if cached
            "domain":                 list,    # exact three-clause domain
        }

    Raises:
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated with a write method.
        OdooQueryError: if the Odoo RPC fails for any reason.
        UnknownProjectError: if read_group returns a project_id not in _PROJECT_NAMES,
            signalling a new Odoo project that requires a code update.
    """
    _assert_read_only()

    today = _cache.today_str()
    domain = _build_late_domain(today)
    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_KPI5)

    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"Cache hit: {cache_key}")
        return {**cached, "cache_status": "cached", "rpc_duration_ms": 0}

    logger.info(f"Cache miss: {cache_key} — querying Odoo")

    _client = client if client is not None else OdooClient()

    t0 = time.monotonic()
    try:
        rows = await _client.execute_kw(
            _MODEL,
            "read_group",
            args=[domain, ["due_amount"], ["project_id"]],
            kwargs={"lazy": False},
        )
    except Exception as exc:
        raise OdooQueryError(
            f"read_group on {_MODEL} (groupby project_id) failed: {exc}"
        ) from exc
    finally:
        if client is None:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        f"Odoo read_group on {_MODEL} (groupby project_id) in {rpc_ms}ms"
        f" | cache_key={cache_key}"
    )

    # Build per-project lookup from Odoo response.
    # read_group returns project_id as [id, display_name] for many2one fields.
    per_project: dict[int, dict] = {}
    for row in rows:
        proj_raw = row.get("project_id")
        if isinstance(proj_raw, (list, tuple)) and len(proj_raw) == 2:
            proj_id = int(proj_raw[0])
        else:
            proj_id = int(proj_raw) if proj_raw else 0

        if proj_id not in _PROJECT_NAMES:
            raise UnknownProjectError(
                f"read_group returned unexpected project_id={proj_id}. "
                "A new Odoo project has appeared. Add it to _PROJECT_NAMES and re-deploy."
            )

        per_project[proj_id] = {
            "project_id": proj_id,
            "project_name": _PROJECT_NAMES[proj_id],
            "late_uncollected": float(row.get("due_amount") or 0.0),
            "record_count": int(row.get("__count") or 0),
        }

    # Build ordered list with zero-padding for any missing project (Decision 3.4).
    projects = []
    for pid in sorted(_PROJECT_NAMES):
        if pid in per_project:
            projects.append(per_project[pid])
        else:
            logger.info(
                f"Project {pid} ({_PROJECT_NAMES[pid]}) absent from read_group — zero-padding"
            )
            projects.append({
                "project_id": pid,
                "project_name": _PROJECT_NAMES[pid],
                "late_uncollected": 0.0,
                "record_count": 0,
            })

    total_late = sum(p["late_uncollected"] for p in projects)
    total_count = sum(p["record_count"] for p in projects)

    result: dict = {
        "projects": projects,
        "total_late_uncollected": total_late,
        "total_record_count": total_count,
        "currency": "EGP",
        "as_of": datetime.now(timezone.utc).isoformat(),
        "cache_status": "fresh",
        "rpc_duration_ms": rpc_ms,
        "domain": domain,
    }

    _cache.set(cache_key, result)
    return result


def _tz_period_bounds(period_start: date, period_end: date) -> tuple[str, str]:
    """Convert local-time period boundaries to UTC datetime strings for Odoo domain.

    Egypt observes DST (UTC+2 Nov-Apr, UTC+3 May-Oct per tzdata 2025.2).
    A record displayed as "01/12/2025 00:00:00" Egypt time (winter, UTC+2) is
    stored as "2025-11-30 22:00:00" UTC — a naive >= '2025-12-01' filter would
    exclude it. ZoneInfo handles DST transitions automatically. Decision 5.9.
    """
    start_local = datetime.combine(period_start, dt_time.min, tzinfo=_LA_VERDE_TZ)
    end_local   = datetime.combine(period_end, dt_time(23, 59, 59), tzinfo=_LA_VERDE_TZ)
    start_utc = start_local.astimezone(_UTC_TZ)
    end_utc   = end_local.astimezone(_UTC_TZ)
    return (
        start_utc.strftime("%Y-%m-%d %H:%M:%S"),
        end_utc.strftime("%Y-%m-%d %H:%M:%S"),
    )


async def get_collection_trend_6m(client: Optional[OdooClient] = None) -> dict:
    """Return KPI 6 — 6-Month Collection Trend.

    Queries rs.account.payment.installment (HEADER model) for posted payment
    records whose user-entered date falls within the trailing 6 calendar months,
    grouped by date:month. Returns exactly 6 entries oldest-first, zero-padding
    months with no data (Decision 3.4 zero-padding extended to KPI 6).

    Architecture note (Decision 5.6): the LINE model (rs.account.payment.
    installment.line) cannot be used because Odoo's ORM does not support
    :month granularity groupby on related fields (payment_id.date:month).
    HEADER.amount is identity-equal to SUM(LINE.amount) — D0 Part 2 Finding B.

    Empty months in the current data period are expected (Decision 5.7):
    operations staff are entering historical payments retroactively. Zero bars
    are truthful, not bugs.

    Return shape::

        {
            "months": [
                {
                    "month":        str,    # YYYY-MM, oldest first
                    "label_en":     str,    # e.g. "Dec 2025"
                    "label_ar":     str,    # e.g. "ديسمبر"  (Decision 5.5)
                    "amount":       float,  # EGP collected this month
                    "record_count": int,    # payment header count
                },
                # ... exactly 6 entries
            ],
            "total_6m":           float,   # EGP, sum across all 6 months
            "total_record_count": int,
            "average_monthly":    float,   # total_6m / 6 (includes zero months)
            "period_start":       str,     # YYYY-MM-DD (first day of oldest month)
            "period_end":         str,     # YYYY-MM-DD (today)
            "currency":           "EGP",
            "as_of":              str,     # ISO 8601 UTC datetime
            "cache_status":       str,     # "fresh" | "cached"
            "cache_ttl_seconds":  int,     # 3600
            "rpc_duration_ms":    int,     # 0 if cached
            "domain":             list,    # exact domain used
        }

    Raises:
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated with a write method.
        OdooQueryError: if the Odoo RPC fails for any reason (network, auth, RPC error).
    """
    _assert_read_only()

    today = date.fromisoformat(_cache.today_str())

    # Trailing 6 calendar months (stdlib only — Decision 5.4, no python-dateutil).
    # Example: today = 2026-05-17 → start_month = 0 → wraps → period_start = 2025-12-01
    start_month = today.month - 5
    start_year = today.year
    if start_month <= 0:
        start_month += 12
        start_year -= 1
    period_start = date(start_year, start_month, 1)
    period_end = today

    start_utc_str, end_utc_str = _tz_period_bounds(period_start, period_end)
    domain: list = [
        ("state", "=", "post"),
        ("date", ">=", start_utc_str),   # Decision 5.9: UTC boundary for Egypt UTC+2
        ("date", "<=", end_utc_str),
    ]

    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_KPI6)

    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"Cache hit: {cache_key}")
        return {**cached, "cache_status": "cached", "rpc_duration_ms": 0}

    logger.info(f"Cache miss: {cache_key} — querying Odoo")

    _client = client if client is not None else OdooClient()

    # Note: Odoo's read_group groups by raw stored UTC value, which places
    # Egypt-local-midnight records in the previous UTC month. We use search_read
    # + Python regrouping in Egypt local time to produce identity-equal results
    # with the Odoo UI. See Decision 5.10.
    t0 = time.monotonic()
    try:
        records = await _client.execute_kw(
            _PAYMENT_HEADER_MODEL,
            "search_read",
            args=[domain, ["date", "amount"]],
            kwargs={},
        )
    except Exception as exc:
        raise OdooQueryError(
            f"search_read on {_PAYMENT_HEADER_MODEL} failed: {exc}"
        ) from exc
    finally:
        if client is None:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        f"Odoo search_read on {_PAYMENT_HEADER_MODEL} returned {len(records)} records "
        f"in {rpc_ms}ms | cache_key={cache_key}"
    )

    if rpc_ms > 5000:
        logger.warning(
            "KPI 6 search_read on %s took %dms — exceeds 5s performance threshold (Decision 5.4)",
            _PAYMENT_HEADER_MODEL, rpc_ms,
        )

    # Group records by Egypt local month. Odoo returns date as "YYYY-MM-DD HH:MM:SS" UTC.
    by_month: defaultdict = defaultdict(lambda: {"amount": 0.0, "count": 0})
    for rec in records:
        raw_date = rec.get("date")
        if not raw_date:
            logger.warning("KPI 6: record missing date — skipping: id=%s", rec.get("id"))
            continue
        try:
            utc_dt = datetime.strptime(str(raw_date), "%Y-%m-%d %H:%M:%S").replace(tzinfo=_UTC_TZ)
        except ValueError:
            logger.warning("KPI 6: unparseable date %r — skipping", raw_date)
            continue
        local_dt = utc_dt.astimezone(_LA_VERDE_TZ)
        ym = local_dt.strftime("%Y-%m")
        by_month[ym]["amount"] += float(rec.get("amount") or 0.0)
        by_month[ym]["count"]  += 1

    # Build ordered 6-entry list, zero-padding months absent from Odoo response.
    month_entries: list[dict] = []
    y, m = period_start.year, period_start.month
    while (y, m) <= (period_end.year, period_end.month):
        ym   = f"{y:04d}-{m:02d}"
        data = by_month.get(ym, {"amount": 0.0, "count": 0})
        month_entries.append({
            "month":        ym,
            "label_en":     f"{calendar.month_abbr[m]} {y}",
            "label_ar":     _ARABIC_MONTHS[m],
            "amount":       data["amount"],
            "record_count": data["count"],
        })
        m += 1
        if m > 12:
            m = 1
            y += 1

    total_6m        = sum(e["amount"]       for e in month_entries)
    total_count     = sum(e["record_count"] for e in month_entries)
    average_monthly = total_6m / 6  # always 6 entries

    result: dict = {
        "months":             month_entries,
        "total_6m":           total_6m,
        "total_record_count": total_count,
        "average_monthly":    average_monthly,
        "period_start":       period_start.isoformat(),
        "period_end":         period_end.isoformat(),
        "currency":           "EGP",
        "as_of":              datetime.now(timezone.utc).isoformat(),
        "cache_status":       "fresh",
        "cache_ttl_seconds":  _CACHE_TTL_KPI6,
        "rpc_duration_ms":    rpc_ms,
        "domain":             domain,
    }

    _cache.set(cache_key, result, ttl=_CACHE_TTL_KPI6)
    return result


async def get_collection_rate_mtd_ytd(client: Optional[OdooClient] = None) -> dict:
    """Return KPI 4 — Collection Rate MTD & YTD.

    Four sequential read_group RPCs (2 per period):
      Q1 — MTD numerator  : rs.account.payment.installment, state='post', UTC datetime bounds
      Q2 — MTD denominator: rs.installment, state='post', ISO date bounds
      Q3 — YTD numerator  : rs.account.payment.installment, state='post', UTC datetime bounds
      Q4 — YTD denominator: rs.installment, state='post', ISO date bounds

    Formula (Decision 6.1):
      rate_percent = SUM(HEADER.amount) / SUM(rs.installment.amount) * 100

    Denominator uses rs.installment.amount (contractual face value), NOT due_amount
    (remaining balance). Using due_amount would make the ratio self-referential and
    time-unstable: the numerator's own success would shrink the denominator, making the
    rate artificially high as more payments are received.

    Zero denominator → rate_percent: None (Decision 6.3). Frontend renders "—".
    YTD period: calendar year Jan 1 to today (Decision 6.2, pending Finance confirmation).

    Return shape::

        {
            "mtd": {
                "numerator_egp":    float,        # SUM(HEADER.amount) in MTD period
                "denominator_egp":  float,        # SUM(rs.installment.amount) in MTD period
                "rate_percent":     float | None, # None if denominator == 0 (Decision 6.3)
                "period_start":     str,          # YYYY-MM-DD (first day of month)
                "period_end":       str,          # YYYY-MM-DD (today)
                "record_count_num": int,          # HEADER records in period
                "record_count_den": int,          # rs.installment records in period
            },
            "ytd": {
                "numerator_egp":    float,
                "denominator_egp":  float,
                "rate_percent":     float | None,
                "period_start":     str,          # YYYY-01-01 (Jan 1, calendar year)
                "period_end":       str,          # YYYY-MM-DD (today)
                "record_count_num": int,
                "record_count_den": int,
            },
            "ytd_period_assumption": str,   # "calendar_year" (Decision 6.2)
            "currency":             "EGP",
            "as_of":                str,    # ISO 8601 UTC datetime
            "cache_status":         str,    # "fresh" | "cached"
            "rpc_duration_ms":      int,    # total across 4 RPCs, 0 if cached
        }

    Raises:
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated with a write method.
        OdooQueryError: if any of the 4 Odoo RPCs fails.
    """
    _assert_read_only()

    today = date.fromisoformat(_cache.today_str())

    # MTD: first day of current month → today
    mtd_start = date(today.year, today.month, 1)
    mtd_end = today

    # YTD: Jan 1 of current calendar year → today (Decision 6.2)
    ytd_start = date(today.year, 1, 1)
    ytd_end = today

    # Numerator: HEADER.date is datetime in UTC → convert Egypt-local bounds to UTC (Decision 5.9)
    mtd_start_utc, mtd_end_utc = _tz_period_bounds(mtd_start, mtd_end)
    ytd_start_utc, ytd_end_utc = _tz_period_bounds(ytd_start, ytd_end)

    # Denominator: rs.installment.date is a plain date field — ISO strings only, no conversion
    mtd_start_iso = mtd_start.isoformat()
    mtd_end_iso   = mtd_end.isoformat()
    ytd_start_iso = ytd_start.isoformat()
    ytd_end_iso   = ytd_end.isoformat()

    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_KPI4)

    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"Cache hit: {cache_key}")
        return {**cached, "cache_status": "cached", "rpc_duration_ms": 0}

    logger.info(f"Cache miss: {cache_key} — querying Odoo (4 RPCs)")

    _client = client if client is not None else OdooClient()

    t0 = time.monotonic()
    try:
        # Q1 — MTD numerator (HEADER, UTC datetime bounds)
        mtd_num_domain: list = [
            ("state", "=", "post"),
            ("date", ">=", mtd_start_utc),
            ("date", "<=", mtd_end_utc),
        ]
        mtd_num_rows = await _client.execute_kw(
            _PAYMENT_HEADER_MODEL,
            "read_group",
            args=[mtd_num_domain, ["amount"], []],
            kwargs={"lazy": False},
        )

        # Q2 — MTD denominator (rs.installment, plain date bounds)
        mtd_den_domain: list = [
            ("state", "=", "post"),
            ("date", ">=", mtd_start_iso),
            ("date", "<=", mtd_end_iso),
        ]
        mtd_den_rows = await _client.execute_kw(
            _MODEL,
            "read_group",
            args=[mtd_den_domain, ["amount"], []],
            kwargs={"lazy": False},
        )

        # Q3 — YTD numerator (HEADER, UTC datetime bounds)
        ytd_num_domain: list = [
            ("state", "=", "post"),
            ("date", ">=", ytd_start_utc),
            ("date", "<=", ytd_end_utc),
        ]
        ytd_num_rows = await _client.execute_kw(
            _PAYMENT_HEADER_MODEL,
            "read_group",
            args=[ytd_num_domain, ["amount"], []],
            kwargs={"lazy": False},
        )

        # Q4 — YTD denominator (rs.installment, plain date bounds)
        ytd_den_domain: list = [
            ("state", "=", "post"),
            ("date", ">=", ytd_start_iso),
            ("date", "<=", ytd_end_iso),
        ]
        ytd_den_rows = await _client.execute_kw(
            _MODEL,
            "read_group",
            args=[ytd_den_domain, ["amount"], []],
            kwargs={"lazy": False},
        )

    except Exception as exc:
        raise OdooQueryError(
            f"KPI 4 read_group failed: {exc}"
        ) from exc
    finally:
        if client is None:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        f"KPI 4: 4 read_group RPCs completed in {rpc_ms}ms | cache_key={cache_key}"
    )
    if rpc_ms > 5000:
        logger.warning(
            "KPI 4: total RPC time %dms exceeds 5s performance threshold", rpc_ms
        )

    def _extract(rows: list) -> tuple[float, int]:
        row = rows[0] if rows else {}
        return float(row.get("amount") or 0.0), int(row.get("__count") or 0)

    def _rate(num: float, den: float) -> Optional[float]:
        if den == 0.0:
            return None  # Decision 6.3: zero denominator → None, frontend renders "—"
        return num / den * 100

    mtd_num, mtd_num_count = _extract(mtd_num_rows)
    mtd_den, mtd_den_count = _extract(mtd_den_rows)
    ytd_num, ytd_num_count = _extract(ytd_num_rows)
    ytd_den, ytd_den_count = _extract(ytd_den_rows)

    result: dict = {
        "mtd": {
            "numerator_egp":    mtd_num,
            "denominator_egp":  mtd_den,
            "rate_percent":     _rate(mtd_num, mtd_den),
            "period_start":     mtd_start.isoformat(),
            "period_end":       mtd_end.isoformat(),
            "record_count_num": mtd_num_count,
            "record_count_den": mtd_den_count,
        },
        "ytd": {
            "numerator_egp":    ytd_num,
            "denominator_egp":  ytd_den,
            "rate_percent":     _rate(ytd_num, ytd_den),
            "period_start":     ytd_start.isoformat(),
            "period_end":       ytd_end.isoformat(),
            "record_count_num": ytd_num_count,
            "record_count_den": ytd_den_count,
        },
        "ytd_period_assumption": "calendar_year",
        "currency":              "EGP",
        "as_of":                 datetime.now(timezone.utc).isoformat(),
        "cache_status":          "fresh",
        "rpc_duration_ms":       rpc_ms,
    }

    _cache.set(cache_key, result)
    return result


async def get_collection_rate_by_project(client: Optional[OdooClient] = None) -> dict:
    """Return KPI 5b — Collection Rate per Project (MTD & YTD).

    Branch A architecture (Decision 7.1): project_id is a direct field on
    rs.account.payment.installment. Four read_group RPCs grouped by project_id:
      Q1 — MTD numerator  : HEADER, state='post', UTC datetime bounds, groupby project_id
      Q2 — MTD denominator: rs.installment, state='post', ISO date bounds, groupby project_id
      Q3 — YTD numerator  : HEADER, state='post', UTC datetime bounds, groupby project_id
      Q4 — YTD denominator: rs.installment, state='post', ISO date bounds, groupby project_id

    Always returns all 3 projects, zero-padding missing ones (Decision 3.4 extended).
    Zero denominator per project → rate_percent: None (Decision 6.3).
    YTD: calendar year Jan 1 → today (Decision 6.2).

    Return shape::

        {
            "mtd": {
                "projects": [
                    {
                        "project_id":        int,
                        "project_name":      str,
                        "numerator_egp":     float,
                        "denominator_egp":   float,
                        "rate_percent":      float | None,
                        "record_count_num":  int,
                        "record_count_den":  int,
                    },
                    # 3 entries, ordered by project_id ascending: 1, 2, 3
                ],
                "total_numerator_egp":   float,
                "total_denominator_egp": float,
                "total_rate_percent":    float | None,
                "period_start":          str,   # YYYY-MM-DD
                "period_end":            str,   # YYYY-MM-DD
            },
            "ytd": { ... same shape ... },
            "ytd_period_assumption": "calendar_year",
            "currency":              "EGP",
            "as_of":                 str,    # ISO 8601 UTC datetime
            "cache_status":          str,    # "fresh" | "cached"
            "rpc_duration_ms":       int,    # total across 4 RPCs, 0 if cached
        }

    Raises:
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated.
        OdooQueryError: if any of the 4 Odoo RPCs fails.
        UnknownProjectError: if read_group returns a project_id not in _PROJECT_NAMES.
    """
    _assert_read_only()

    today = date.fromisoformat(_cache.today_str())

    mtd_start = date(today.year, today.month, 1)
    mtd_end   = today
    ytd_start = date(today.year, 1, 1)
    ytd_end   = today

    mtd_start_utc, mtd_end_utc = _tz_period_bounds(mtd_start, mtd_end)
    ytd_start_utc, ytd_end_utc = _tz_period_bounds(ytd_start, ytd_end)
    mtd_start_iso = mtd_start.isoformat()
    mtd_end_iso   = mtd_end.isoformat()
    ytd_start_iso = ytd_start.isoformat()
    ytd_end_iso   = ytd_end.isoformat()

    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_KPI5B)

    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"Cache hit: {cache_key}")
        return {**cached, "cache_status": "cached", "rpc_duration_ms": 0}

    logger.info(f"Cache miss: {cache_key} — querying Odoo (4 RPCs)")

    _client = client if client is not None else OdooClient()

    t0 = time.monotonic()
    try:
        # Q1 — MTD numerator per project (HEADER, UTC datetime bounds)
        q1_domain: list = [
            ("state", "=", "post"),
            ("date",  ">=", mtd_start_utc),
            ("date",  "<=", mtd_end_utc),
        ]
        q1_rows = await _client.execute_kw(
            _PAYMENT_HEADER_MODEL, "read_group",
            args=[q1_domain, ["amount"], ["project_id"]],
            kwargs={"lazy": False},
        )

        # Q2 — MTD denominator per project (rs.installment, ISO date bounds)
        q2_domain: list = [
            ("state", "=", "post"),
            ("date",  ">=", mtd_start_iso),
            ("date",  "<=", mtd_end_iso),
        ]
        q2_rows = await _client.execute_kw(
            _MODEL, "read_group",
            args=[q2_domain, ["amount"], ["project_id"]],
            kwargs={"lazy": False},
        )

        # Q3 — YTD numerator per project (HEADER, UTC datetime bounds)
        q3_domain: list = [
            ("state", "=", "post"),
            ("date",  ">=", ytd_start_utc),
            ("date",  "<=", ytd_end_utc),
        ]
        q3_rows = await _client.execute_kw(
            _PAYMENT_HEADER_MODEL, "read_group",
            args=[q3_domain, ["amount"], ["project_id"]],
            kwargs={"lazy": False},
        )

        # Q4 — YTD denominator per project (rs.installment, ISO date bounds)
        q4_domain: list = [
            ("state", "=", "post"),
            ("date",  ">=", ytd_start_iso),
            ("date",  "<=", ytd_end_iso),
        ]
        q4_rows = await _client.execute_kw(
            _MODEL, "read_group",
            args=[q4_domain, ["amount"], ["project_id"]],
            kwargs={"lazy": False},
        )

    except Exception as exc:
        raise OdooQueryError(
            f"KPI 5b read_group failed: {exc}"
        ) from exc
    finally:
        if client is None:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        f"KPI 5b: 4 read_group RPCs completed in {rpc_ms}ms | cache_key={cache_key}"
    )
    if rpc_ms > 5000:
        logger.warning(
            "KPI 5b: total RPC time %dms exceeds 5s performance threshold", rpc_ms
        )

    def _parse_rows(rows: list) -> dict[int, tuple[float, int]]:
        """Parse groupby-project_id rows → {project_id: (amount, count)}.

        Skips null project_id rows (project_id=False).
        Raises UnknownProjectError for IDs absent from _PROJECT_NAMES.
        """
        out: dict[int, tuple[float, int]] = {}
        for row in rows:
            proj_raw = row.get("project_id")
            if not proj_raw or proj_raw is False:
                continue
            if isinstance(proj_raw, (list, tuple)) and len(proj_raw) == 2:
                proj_id = int(proj_raw[0])
            else:
                proj_id = int(proj_raw)
            if proj_id not in _PROJECT_NAMES:
                raise UnknownProjectError(
                    f"KPI 5b read_group returned unexpected project_id={proj_id}. "
                    "A new Odoo project has appeared. Add it to _PROJECT_NAMES and re-deploy."
                )
            out[proj_id] = (
                float(row.get("amount") or 0.0),
                int(row.get("__count") or 0),
            )
        return out

    def _build_period(
        num_map: dict[int, tuple[float, int]],
        den_map: dict[int, tuple[float, int]],
        period_start: date,
        period_end: date,
    ) -> dict:
        projects = []
        for pid in sorted(_PROJECT_NAMES.keys()):
            num_amt, num_ct = num_map.get(pid, (0.0, 0))
            den_amt, den_ct = den_map.get(pid, (0.0, 0))
            if pid not in num_map:
                logger.info(
                    "KPI 5b: project %d (%s) absent from numerator read_group — zero-padded",
                    pid, _PROJECT_NAMES[pid],
                )
            if pid not in den_map:
                logger.info(
                    "KPI 5b: project %d (%s) absent from denominator read_group — zero-padded",
                    pid, _PROJECT_NAMES[pid],
                )
            rate_val: Optional[float] = (
                None if den_amt == 0.0 else num_amt / den_amt * 100
            )
            projects.append({
                "project_id":       pid,
                "project_name":     _PROJECT_NAMES[pid],
                "numerator_egp":    num_amt,
                "denominator_egp":  den_amt,
                "rate_percent":     rate_val,
                "record_count_num": num_ct,
                "record_count_den": den_ct,
            })

        total_num = sum(p["numerator_egp"]   for p in projects)
        total_den = sum(p["denominator_egp"] for p in projects)

        return {
            "projects":              projects,
            "total_numerator_egp":   total_num,
            "total_denominator_egp": total_den,
            "total_rate_percent":    None if total_den == 0.0 else total_num / total_den * 100,
            "period_start":          period_start.isoformat(),
            "period_end":            period_end.isoformat(),
        }

    mtd_num_map = _parse_rows(q1_rows)
    mtd_den_map = _parse_rows(q2_rows)
    ytd_num_map = _parse_rows(q3_rows)
    ytd_den_map = _parse_rows(q4_rows)

    result: dict = {
        "mtd":                   _build_period(mtd_num_map, mtd_den_map, mtd_start, mtd_end),
        "ytd":                   _build_period(ytd_num_map, ytd_den_map, ytd_start, ytd_end),
        "ytd_period_assumption": "calendar_year",
        "currency":              "EGP",
        "as_of":                 datetime.now(timezone.utc).isoformat(),
        "cache_status":          "fresh",
        "rpc_duration_ms":       rpc_ms,
    }

    _cache.set(cache_key, result)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# KPI 7 — Expected Collections Forecast
# Stage 1 — Phase 1 (Module 2 Refactor)
# ══════════════════════════════════════════════════════════════════════════════

# Canonical bucket order — must not change without updating _BUCKET_NAMES everywhere.
_BUCKET_NAMES: tuple[str, ...] = (
    "this_month", "this_quarter", "this_half", "this_year"
)


def _compute_bucket_ends(today: date) -> dict[str, date]:
    """Return the Cairo-local end date for each of the 4 KPI 7 calendar buckets.

    All arithmetic is pure calendar math on plain date objects. ZoneInfo is
    NOT used here — it is used upstream when computing today_cairo. The caller
    is responsible for passing a Cairo-local date. Decision 9.2.

    Quarter boundaries: Q1 ends Mar 31, Q2 ends Jun 30, Q3 ends Sep 30, Q4 ends Dec 31.
    Half boundaries:   H1 ends Jun 30 (months 1-6), H2 ends Dec 31 (months 7-12).

    Edge case — nesting collapse in May/Jun 2026:
      this_quarter (Q2 ends Jun 30) == this_half (H1 ends Jun 30) → correct.

    Edge case — Dec 31:
      All 4 buckets collapse to Dec 31 → correct (test_kpi7_year_end_full_collapse).
    """
    _, last_day = calendar.monthrange(today.year, today.month)
    end_of_month = date(today.year, today.month, last_day)

    quarter_idx = (today.month - 1) // 3          # 0=Q1, 1=Q2, 2=Q3, 3=Q4
    end_q_month = (quarter_idx + 1) * 3           # 3, 6, 9, or 12
    _, end_q_day = calendar.monthrange(today.year, end_q_month)
    end_of_quarter = date(today.year, end_q_month, end_q_day)

    end_h_month = 6 if today.month <= 6 else 12
    _, end_h_day = calendar.monthrange(today.year, end_h_month)
    end_of_half = date(today.year, end_h_month, end_h_day)

    end_of_year = date(today.year, 12, 31)

    return {
        "this_month":   end_of_month,
        "this_quarter": end_of_quarter,
        "this_half":    end_of_half,
        "this_year":    end_of_year,
    }


async def _fetch_bucket(
    client: OdooClient,
    today_str: str,
    bucket_end_str: str,
) -> tuple[float, int, float, float, float]:
    """Fetch one KPI 7 bucket via 2 sequential read_group RPCs.

    RPC 1 — amount + due_amount aggregate (bucket total + record count).
    RPC 2 — cheques aggregate using Alternative B formula (Decision 9.1):
              cheques_raw = SUM(paid_amount) - SUM(x_studio_actual_paid_amount)

    Returns (amount, count, due_amount, cheques_clamped, cheques_raw).
    cheques_clamped = max(cheques_raw, 0.0).
    cheques_raw < 0 signals a data quality anomaly; the caller sets
    data_quality_warning = "negative_cheques".

    Domain (KD-1 / KD-2 compliant, Decision 9.2):
        state='post', payment_state IN [unpaid, partial], date >= today, date <= bucket_end
    No UTC conversion — rs.installment.date is a plain date field (D0.3).
    """
    domain = [
        ("state", "=", "post"),
        ("payment_state", "in", ["unpaid", "partial"]),
        ("date", ">=", today_str),
        ("date", "<=", bucket_end_str),
    ]

    # RPC 1 — amount + due_amount
    amount_rows = await client.execute_kw(
        _MODEL,
        "read_group",
        args=[domain, ["amount", "due_amount"], []],
        kwargs={"lazy": False},
    )
    amount_row = amount_rows[0] if amount_rows else {}
    amount     = float(amount_row.get("amount") or 0.0)
    count      = int(amount_row.get("__count") or 0)
    due_amount = float(amount_row.get("due_amount") or 0.0)

    # RPC 2 — cheques in pipeline (Alternative B, per Decision 9.1)
    cheque_rows = await client.execute_kw(
        _MODEL,
        "read_group",
        args=[domain, ["paid_amount", "x_studio_actual_paid_amount"], []],
        kwargs={"lazy": False},
    )
    cheque_row  = cheque_rows[0] if cheque_rows else {}
    cheques_raw = (
        float(cheque_row.get("paid_amount") or 0.0)
        - float(cheque_row.get("x_studio_actual_paid_amount") or 0.0)
    )

    return amount, count, due_amount, max(cheques_raw, 0.0), cheques_raw


async def _fetch_bucket_type_breakdown(
    client: OdooClient,
    today_str: str,
    bucket_end_str: str,
    bucket_total_amount: float,
) -> list[dict]:
    """Fetch by-type amount breakdown for one KPI 7 bucket.  1 read_group RPC.

    Groups the same domain as _fetch_bucket by installment_type_id.
    Returns a list of {installment_type_id, installment_type_name_ar, amount,
    record_count}, sorted by amount descending (Choice 4أ).

    Identity-equal assertion: sum of entry amounts == bucket_total_amount.
    Zero-count entries are excluded (read_group natural behaviour; assertion
    added per Khaled's Gate 1 note).

    Raises OdooQueryError on RPC failure.
    Raises AssertionError if breakdown sum != bucket_total_amount.
    Raises ValueError if any type_id is not in INSTALLMENT_TYPE_NAMES_AR
        (would expose a raw Odoo name to Board output — hard stop per Choice 2ج).
    """
    domain = [
        ("state", "=", "post"),
        ("payment_state", "in", ["unpaid", "partial"]),
        ("date", ">=", today_str),
        ("date", "<=", bucket_end_str),
    ]
    rows = await client.execute_kw(
        _MODEL,
        "read_group",
        args=[domain, ["amount"], ["installment_type_id"]],
        kwargs={"lazy": False},
    )

    entries = []
    for row in rows:
        count = int(row.get("__count") or 0)
        if count == 0:
            continue  # guard: skip zero-count entries (should not occur via read_group)

        type_raw = row.get("installment_type_id")
        type_id  = (
            int(type_raw[0]) if isinstance(type_raw, (list, tuple)) and type_raw
            else (int(type_raw) if type_raw else 0)
        )

        # Choice 2ج: every type shown to the Board must have a reviewed Arabic name.
        if type_id not in INSTALLMENT_TYPE_NAMES_AR:
            raise ValueError(
                f"installment_type_id={type_id} is not in INSTALLMENT_TYPE_NAMES_AR. "
                "A new type has appeared in Odoo that has not been reviewed. "
                "Add it to installment_type_names.py before re-deploying."
            )

        entries.append({
            "installment_type_id":      type_id,
            "installment_type_name_ar": get_type_name_ar(type_id),
            "amount":                   float(row.get("amount") or 0.0),
            "record_count":             count,
        })

    # Sort by amount descending (Choice 4أ — no extra RPC; done in Python).
    entries.sort(key=lambda e: e["amount"], reverse=True)

    # Identity-equal assertion: breakdown must sum exactly to bucket total.
    breakdown_sum = sum(e["amount"] for e in entries)
    assert abs(breakdown_sum - bucket_total_amount) < 0.01, (
        f"type_breakdown sum {breakdown_sum:.2f} != bucket total "
        f"{bucket_total_amount:.2f} (delta={breakdown_sum - bucket_total_amount:.2f}). "
        "This is a real data integrity finding — do not adjust to make it pass."
    )

    return entries


async def get_expected_collections_forecast(
    odoo_client: Optional[OdooClient] = None,
) -> dict:
    """Return KPI 7 — Expected Collections Forecast.

    Four forward-looking calendar buckets (this_month ⊆ this_quarter ⊆
    this_half ⊆ this_year), each reporting installment amounts due from
    today (Cairo-local) through the bucket end date inclusive.

    Bucket boundaries are computed in Africa/Cairo timezone for today's
    date; all domain values use plain ISO date strings since
    rs.installment.date is type 'date', not 'datetime' (D0.3, Decision 9.2).

    Cheques formula (Alternative B, Decision 9.1):
        cheques_in_pipeline = max(SUM(paid_amount) - SUM(x_studio_actual_paid_amount), 0)
    cheques_record_count is null (Alternative B limitation — per-installment
    count unavailable via read_group net formula).

    Cache key uses the Cairo-local date so the cache invalidates at
    Cairo midnight, not UTC midnight (Decision 9.3).

    12 RPCs per uncached call (2 amount RPCs + 1 cheques_count RPC per bucket × 4 buckets),
    60-second TTL (Decision 9.4; cheques_record_count added Stage 5, Decision 14.6).

    Return shape::

        {
            "buckets": {
                "this_month":   { bucket fields ... },
                "this_quarter": { bucket fields ... },
                "this_half":    { bucket fields ... },
                "this_year":    { bucket fields ... },
            },
            "currency":              "EGP",
            "today_cairo":           "YYYY-MM-DD",   # Cairo-local date string
            "cache_status":          "fresh" | "cached",
            "rpc_duration_ms":       int,             # 0 if cached
            "data_quality_warning":  str | None,      # "negative_cheques" or null
        }

    Each bucket shape::

        {
            "bucket":                    str,   # bucket name
            "period_start":              str,   # today_cairo ISO
            "period_end":                str,   # bucket end date ISO
            "amount":                    float, # SUM(amount) EGP
            "record_count":              int,
            "due_amount":                float, # SUM(due_amount) EGP
            "cheques_in_pipeline":       float, # clamped to >= 0
            "cheques_record_count":      int,   # count of installments with pending cheque (Stage 5, Decision 14.6)
            "drill_down_domain":         list,  # 4-clause Odoo domain
            "cheques_drill_down_domain": None,  # Alt B limitation
        }

    Raises:
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated.
        OdooQueryError: if any of the 8 Odoo RPCs fails.
    """
    _assert_read_only()

    # Compute today in Cairo local time (Decision 9.3).
    # ZoneInfo handles DST automatically (UTC+2 Nov-Apr, UTC+3 May-Oct).
    today_cairo = datetime.now(_LA_VERDE_TZ).date()
    today_str   = today_cairo.isoformat()

    bucket_ends = _compute_bucket_ends(today_cairo)

    # Cairo-local date in the key so cache invalidates at Cairo midnight (Decision 9.3).
    cache_key = f"{_CACHE_KEY_PREFIX_KPI7}:{today_str}"

    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"Cache hit: {cache_key}")
        return {**cached, "cache_status": "cached", "rpc_duration_ms": 0}

    logger.info(f"Cache miss: {cache_key} — querying Odoo (16 RPCs)")

    _client = odoo_client if odoo_client is not None else OdooClient()

    t0 = time.monotonic()
    try:
        raw: dict[str, tuple[float, int, float, float, float]] = {}
        for bname in _BUCKET_NAMES:
            raw[bname] = await _fetch_bucket(
                _client, today_str, bucket_ends[bname].isoformat()
            )
        # 4 search_count RPCs for cheques_record_count (Decision 14.6, Stage 5).
        # Uses check_pending_amount > 0 (stored monetary field, Decision 4.5/9.1).
        cheques_counts: dict[str, int] = {}
        for bname in _BUCKET_NAMES:
            cheques_counts[bname] = int(await _client.execute_kw(
                _MODEL,
                "search_count",
                args=[[
                    ("state", "=", "post"),
                    ("payment_state", "in", ["unpaid", "partial"]),
                    ("date", ">=", today_str),
                    ("date", "<=", bucket_ends[bname].isoformat()),
                    ("check_pending_amount", ">", 0),
                ]],
            ))
        # 4 read_group RPCs — by-type breakdown per bucket (Stage 7, Choice 3ب).
        # _fetch_bucket_type_breakdown asserts identity-equal and rejects unknown type IDs.
        type_breakdowns: dict[str, list[dict]] = {}
        for bname in _BUCKET_NAMES:
            bucket_amount = raw[bname][0]  # index 0 = amount
            type_breakdowns[bname] = await _fetch_bucket_type_breakdown(
                _client, today_str, bucket_ends[bname].isoformat(), bucket_amount
            )
    except Exception as exc:
        raise OdooQueryError(f"KPI 7 read_group failed: {exc}") from exc
    finally:
        if odoo_client is None:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        f"KPI 7: 16 RPCs completed in {rpc_ms}ms | cache_key={cache_key}"
    )

    # Check for data quality anomaly — negative raw cheques (Decision 4.4 analog).
    # raw[b][4] is cheques_raw (before clamping).
    has_negative_cheques = any(raw[b][4] < 0 for b in _BUCKET_NAMES)
    if has_negative_cheques:
        logger.warning(
            "KPI 7: negative cheques_raw detected — paid_amount < x_studio_actual_paid_amount "
            "in one or more buckets. This is a data quality anomaly in Odoo Studio fields."
        )
    data_quality_warning: Optional[str] = (
        "negative_cheques" if has_negative_cheques else None
    )

    buckets: dict = {}
    for bname in _BUCKET_NAMES:
        amount, count, due_amount, cheques_clamped, _ = raw[bname]
        bucket_end_str = bucket_ends[bname].isoformat()
        buckets[bname] = {
            "bucket":       bname,
            "period_start": today_str,
            "period_end":   bucket_end_str,
            "amount":       amount,
            "record_count": count,
            "due_amount":   due_amount,
            "cheques_in_pipeline":       cheques_clamped,
            "cheques_record_count":      cheques_counts[bname],
            "drill_down_domain": [
                ["state", "=", "post"],
                ["payment_state", "in", ["unpaid", "partial"]],
                ["date", ">=", today_str],
                ["date", "<=", bucket_end_str],
            ],
            "cheques_drill_down_domain": None,
            "type_breakdown":            type_breakdowns[bname],
        }

    result: dict = {
        "buckets":              buckets,
        "currency":             "EGP",
        "today_cairo":          today_str,
        "cache_status":         "fresh",
        "rpc_duration_ms":      rpc_ms,
        "data_quality_warning": data_quality_warning,
    }

    _cache.set(cache_key, result)
    return result
