"""
HR KPI service — business logic for Module 5 KPIs.

Data source: hr.employee, hr.contract, hr.department, hr.job via the shared
read-only OdooClient. All methods are async. No method ever calls
create, write, or unlink.

M5-S1 scope: get_headcount() (KPI A).
"""

import time
from datetime import datetime, timezone
from typing import Optional

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
