"""
HR KPI service — business logic for Module 5 KPIs.

Data source: hr.employee, hr.contract, hr.department, hr.job via the shared
read-only OdooClient. All methods are async. No method ever calls
create, write, or unlink.

M5-S1 scope: get_headcount() (KPI A).
"""

import time
from datetime import date, datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from loguru import logger

from backend.core.exceptions import OdooQueryError, ReadOnlyViolationError
from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient
from backend.modules.hr.services import cache as _cache

_FORBIDDEN_WRITE_METHODS = frozenset({"create", "write", "unlink"})

_MODEL = "hr.employee"
_CACHE_KEY_PREFIX_HEADCOUNT = "kpi:hr:headcount"

_NO_DEPT_LABEL = "بدون إدارة"   # بدون إدارة  (wrapped in parens below)
_NO_JOB_LABEL  = "بدون وظيفة"   # بدون وظيفة  (wrapped in parens below)

# Display strings shown in the response
_NO_DEPT_DISPLAY = f"({_NO_DEPT_LABEL})"
_NO_JOB_DISPLAY  = f"({_NO_JOB_LABEL})"


def _assert_read_only() -> None:
    """Defense-in-depth: abort if any write method has leaked into ALLOWED_METHODS."""
    violations = ALLOWED_METHODS & _FORBIDDEN_WRITE_METHODS
    if violations:
        raise ReadOnlyViolationError(
            f"ALLOWED_METHODS contains forbidden write method(s): {sorted(violations)}. "
            "The Odoo client is no longer strictly read-only. Halting before any RPC."
        )


async def get_headcount(client: Optional[OdooClient] = None) -> dict:
    """Return KPI A — Headcount.

    4 RPCs against hr.employee:
      RPC 1 — search_count([('active','=',True)])  -> total_active
      RPC 2 — search_count([('active','=',False)]) -> total_inactive
      RPC 3 — read_group([('active','=',True)], ['department_id'],
                         ['department_id'], lazy=False)
               -> by_department (active only; null bucket included)
      RPC 4 — read_group([('active','=',True)], ['job_id'],
                         ['job_id'], lazy=False)
               -> by_job (active only; null bucket included)

    Null-department employees appear as a single "(بدون إدارة)" bucket
    with department_id=None. Same rule for job. This ensures the breakdown
    sums equal total_active and matches the Odoo UI count.

    Sorting: count DESC, then name ASC for stable ordering on equal counts.

    Baselines (discovery canonical run 2026-05-28T13:43:49Z):
      total_active   == 136
      total_inactive == 24
      len(by_department) == 24  (includes null-dept bucket, discovery S3.3)
      len(by_job)        == 67  (includes null-job bucket, discovery S3.4)

    Raises:
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated.
        OdooQueryError: if any of the 4 Odoo RPCs fails.
    """
    _assert_read_only()

    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_HEADCOUNT)
    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"Cache hit: {cache_key}")
        return {**cached, "cache_status": "cached", "rpc_duration_ms": 0}

    logger.info(f"Cache miss: {cache_key} — querying Odoo (4 RPCs)")

    _client = client if client is not None else OdooClient()

    t0 = time.monotonic()
    try:
        # RPC 1 — total active
        total_active = int(await _client.execute_kw(
            _MODEL,
            "search_count",
            args=[[("active", "=", True)]],
        ))

        # RPC 2 — total inactive
        total_inactive = int(await _client.execute_kw(
            _MODEL,
            "search_count",
            args=[[("active", "=", False)]],
        ))

        # RPC 3 — department breakdown (active only)
        dept_rows = await _client.execute_kw(
            _MODEL,
            "read_group",
            args=[[("active", "=", True)], ["department_id"], ["department_id"]],
            kwargs={"lazy": False},
        )

        # RPC 4 — job breakdown (active only)
        job_rows = await _client.execute_kw(
            _MODEL,
            "read_group",
            args=[[("active", "=", True)], ["job_id"], ["job_id"]],
            kwargs={"lazy": False},
        )

    except Exception as exc:
        raise OdooQueryError(
            f"get_headcount() RPC on {_MODEL} failed: {exc}"
        ) from exc
    finally:
        if client is None:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        f"HR headcount: 4 RPCs on {_MODEL} in {rpc_ms}ms | cache_key={cache_key}"
    )

    # Parse department breakdown — null dept_id → null bucket
    by_department = []
    for row in dept_rows:
        dept_raw = row.get("department_id")
        if isinstance(dept_raw, (list, tuple)) and dept_raw:
            dept_id   = int(dept_raw[0])
            dept_name = str(dept_raw[1])
        else:
            dept_id   = None
            dept_name = _NO_DEPT_DISPLAY
        by_department.append({
            "department_id":   dept_id,
            "department_name": dept_name,
            "count":           int(row.get("__count") or 0),
        })

    # Sort: count DESC, then department_name ASC for stable tie-breaking
    by_department.sort(key=lambda r: (-r["count"], r["department_name"]))

    # Parse job breakdown — null job_id → null bucket
    by_job = []
    for row in job_rows:
        job_raw = row.get("job_id")
        if isinstance(job_raw, (list, tuple)) and job_raw:
            job_id   = int(job_raw[0])
            job_name = str(job_raw[1])
        else:
            job_id   = None
            job_name = _NO_JOB_DISPLAY
        by_job.append({
            "job_id":   job_id,
            "job_name": job_name,
            "count":    int(row.get("__count") or 0),
        })

    by_job.sort(key=lambda r: (-r["count"], r["job_name"]))

    result: dict = {
        "total_active":    total_active,
        "total_inactive":  total_inactive,
        "by_department":   by_department,
        "by_job":          by_job,
        "as_of":           datetime.now(timezone.utc).isoformat(),
        "cache_status":    "fresh",
        "rpc_duration_ms": rpc_ms,
    }

    _cache.set(cache_key, result)
    return result


# ── KPI B — Tenure Distribution ───────────────────────────────────────────────

_CAIRO_TZ = ZoneInfo("Africa/Cairo")
_CACHE_KEY_PREFIX_TENURE = "kpi:hr:tenure"
_BAND_LABELS = ["<1y", "1-3y", "3-5y", "5-10y", "10+y"]


def _tenure_years(fcd: date, today: date) -> int:
    # Use anniversary method, not (days / 365.25), because tenure in
    # years is a calendar concept (the date of the Nth anniversary of
    # hire), not an elapsed-duration concept. Anniversary method gives
    # the count of completed anniversary years on `today`. The
    # alternative (days/365.25 with floor) understates by 1 in every
    # non-leap year because 365 / 365.25 = 0.9979... < 1, so an
    # employee at their 1-year anniversary on a non-leap year would
    # land in <1y.
    #
    # Feb 29 hires: on non-leap years, anniversary fires on Mar 1,
    # not Feb 28 (conservative — Feb 28 has not "reached" Feb 29
    # under tuple comparison). Acceptable behavior for an HR KPI;
    # documented for clarity.
    years = today.year - fcd.year
    if (today.month, today.day) < (fcd.month, fcd.day):
        years -= 1
    return years


def _assign_band(years: int) -> str:
    if years < 1:
        return "<1y"
    if years < 3:
        return "1-3y"
    if years < 5:
        return "3-5y"
    if years < 10:
        return "5-10y"
    return "10+y"


async def get_tenure_distribution(client: Optional[OdooClient] = None) -> dict:
    """Return KPI B — Tenure Distribution.

    2 RPCs against hr.employee:
      RPC 1 — search_read([('active','=',True),('first_contract_date','!=',False)],
                          fields=['id','first_contract_date'])
               -> records for band computation
      RPC 2 — search_count([('active','=',True),('first_contract_date','=',False)])
               -> missing_date_count

    total_active = len(rpc1_records) + missing_date_count  (Python, not a 3rd RPC)

    Bands (half-open, anniversary method — see _tenure_years):
      "<1y"   : tenure_years < 1
      "1-3y"  : 1 <= tenure_years < 3
      "3-5y"  : 3 <= tenure_years < 5
      "5-10y" : 5 <= tenure_years < 10
      "10+y"  : tenure_years >= 10

    Reference date: today in Africa/Cairo — using UTC would place an employee
    who started on today's Cairo date in the wrong band at 22:00+ UTC.

    Cache key: kpi:hr:tenure:{cairo_date} (TTL 60s, date-scoped by make_key).

    Baselines (discovery canonical run 2026-05-28T13:43:49Z):
      total_active == 136
      first_contract_date range (active): 2017-12-26 → 2025-11-17
      No band before 2017; "10+y" count depends on run date.

    Raises:
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated.
        OdooQueryError: if any Odoo RPC fails.
    """
    _assert_read_only()

    cairo_today = datetime.now(_CAIRO_TZ).date()
    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_TENURE)
    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"Cache hit: {cache_key}")
        return {**cached, "cache_status": "cached", "rpc_duration_ms": 0}

    logger.info(f"Cache miss: {cache_key} — querying Odoo (2 RPCs)")

    _client = client if client is not None else OdooClient()

    t0 = time.monotonic()
    try:
        # RPC 1 — active employees with a contract date (for band computation)
        records = await _client.execute_kw(
            _MODEL,
            "search_read",
            args=[[("active", "=", True), ("first_contract_date", "!=", False)]],
            kwargs={"fields": ["id", "first_contract_date"]},
        )

        # RPC 2 — active employees missing first_contract_date
        missing_date_count = int(await _client.execute_kw(
            _MODEL,
            "search_count",
            args=[[("active", "=", True), ("first_contract_date", "=", False)]],
        ))

    except Exception as exc:
        raise OdooQueryError(
            f"get_tenure_distribution() RPC on {_MODEL} failed: {exc}"
        ) from exc
    finally:
        if client is None:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        f"HR tenure distribution: 2 RPCs on {_MODEL} in {rpc_ms}ms | cache_key={cache_key}"
    )

    # Classify each record — IDs used only to count; no PII leaves this function
    band_counts: dict[str, int] = {label: 0 for label in _BAND_LABELS}
    for rec in records:
        fcd_raw = rec.get("first_contract_date")
        if not fcd_raw:
            continue   # domain excludes False; guard against unexpected None
        fcd = date.fromisoformat(str(fcd_raw))
        band_counts[_assign_band(_tenure_years(fcd, cairo_today))] += 1

    bands = [{"band": label, "count": band_counts[label]} for label in _BAND_LABELS]
    total_active = sum(b["count"] for b in bands) + missing_date_count

    result: dict = {
        "bands":              bands,
        "missing_date_count": missing_date_count,
        "total_active":       total_active,
        "reference_date":     cairo_today.isoformat(),
        "as_of":              datetime.now(timezone.utc).isoformat(),
        "cache_status":       "fresh",
        "rpc_duration_ms":    rpc_ms,
    }

    _cache.set(cache_key, result)
    return result
