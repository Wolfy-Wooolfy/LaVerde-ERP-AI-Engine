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
from datetime import date, datetime, time as dt_time, timezone
from zoneinfo import ZoneInfo
from typing import Optional

from loguru import logger

from backend.core.exceptions import OdooQueryError, ReadOnlyViolationError, UnknownProjectError
from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient
from backend.modules.collections.services import cache as _cache

# Methods that must never appear in ALLOWED_METHODS.
_FORBIDDEN_WRITE_METHODS = frozenset({"create", "write", "unlink"})

_MODEL = "rs.installment"
_CACHE_KEY_PREFIX = "kpi:late_uncollected"
_CACHE_KEY_PREFIX_KPI1 = "kpi:total_portfolio_value"
_CACHE_KEY_PREFIX_KPI5 = "kpi:late_uncollected_by_project"
_CACHE_KEY_PREFIX_KPI3 = "kpi:pending_check_exposure"
_CACHE_KEY_PREFIX_KPI6 = "kpi:collection_trend_6m"

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

# Odoo returns date:month groupby values as English month names (e.g. "December 2025").
# Reverse-index calendar.month_name so we can parse them back to a month number.
_MONTH_NAME_TO_NUM: dict[str, int] = {
    name: i for i, name in enumerate(calendar.month_name) if name
}

# La Verde operates in Egypt (UTC+2, Africa/Cairo, no DST since 2014). Decision 5.9.
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
    whose due date is before today, and returns SUM(due_amount) via read_group.

    Return shape::

        {
            "value":           float,  # EGP total due_amount
            "currency":        "EGP",
            "record_count":    int,    # matched installment count
            "as_of":           str,    # ISO 8601 UTC datetime of the query
            "cache_status":    str,    # "fresh" or "cached"
            "rpc_duration_ms": int,    # 0 if served from cache
            "domain":          list,   # exact domain used (paste into Odoo for debugging)
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
            args=[domain, ["due_amount"], []],
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
    value = float(row.get("due_amount") or 0.0)
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

    Egypt is UTC+2 (Africa/Cairo). A record displayed as "01/12/2025 00:00:00"
    Egypt time is stored as "2025-11-30 22:00:00" UTC — a naive >= '2025-12-01'
    filter would exclude it. Decision 5.9.
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

    t0 = time.monotonic()
    try:
        rows = await _client.execute_kw(
            _PAYMENT_HEADER_MODEL,
            "read_group",
            args=[domain, ["amount"], ["date:month"]],
            kwargs={"lazy": False},
        )
    except Exception as exc:
        raise OdooQueryError(
            f"read_group on {_PAYMENT_HEADER_MODEL} (groupby date:month) failed: {exc}"
        ) from exc
    finally:
        if client is None:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        f"Odoo read_group on {_PAYMENT_HEADER_MODEL} (date:month) in {rpc_ms}ms"
        f" | cache_key={cache_key}"
    )

    if rpc_ms > 5000:
        logger.warning(
            "KPI 6 read_group on %s took %dms — exceeds 5s performance threshold (Decision 5.4)",
            _PAYMENT_HEADER_MODEL, rpc_ms,
        )

    # Parse Odoo rows into YYYY-MM keyed lookup.
    # Odoo returns date:month groupby value as e.g. "December 2025" (English full name + year).
    by_month: dict[str, dict] = {}
    for row in rows:
        odoo_key = row.get("date:month")
        if not odoo_key:
            continue
        parts = str(odoo_key).rsplit(" ", 1)
        if len(parts) != 2:
            logger.warning("KPI 6: unexpected date:month key format %r — skipping row", odoo_key)
            continue
        month_name, year_str = parts
        month_num = _MONTH_NAME_TO_NUM.get(month_name)
        if month_num is None:
            logger.warning("KPI 6: unrecognised month name %r in key %r — skipping", month_name, odoo_key)
            continue
        try:
            year = int(year_str)
        except ValueError:
            logger.warning("KPI 6: non-integer year %r in key %r — skipping", year_str, odoo_key)
            continue
        ym = f"{year:04d}-{month_num:02d}"
        by_month[ym] = {
            "amount": float(row.get("amount") or 0.0),
            "count":  int(row.get("__count") or 0),
        }

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
