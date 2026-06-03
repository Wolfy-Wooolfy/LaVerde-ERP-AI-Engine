"""
HR KPI service — business logic for Module 5 KPIs.

Data source: hr.employee, hr.contract, hr.department, hr.job via the shared
read-only OdooClient. All methods are async. No method ever calls
create, write, or unlink.

KPI A (re-foundation 2026-06-03): get_headcount() rebuilt on Running contracts.
Employment = holding a contract in state='open'. hr.employee.active is NOT an
employment signal. See §3.6 in HR_CLUSTER_DISCOVERY.md.
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

_MODEL          = "hr.employee"
_CONTRACT_MODEL = "hr.contract"
_CAIRO_TZ       = ZoneInfo("Africa/Cairo")

_CACHE_KEY_PREFIX_HEADCOUNT = "kpi:hr:headcount"

_NO_DEPT_LABEL   = "بدون إدارة"
_NO_JOB_LABEL    = "بدون وظيفة"
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
    """Return KPI A — Headcount (re-foundation 2026-06-03).

    Employment definition: an employee is employed at La Verde IFF they hold
    a contract in state='open' (Running). hr.employee.active is an archive/UI
    flag — NOT an employment signal. See §3.6 in HR_CLUSTER_DISCOVERY.md.

    3 RPCs:
      RPC 1 — search_read(hr.contract, [('state','=','open')],
                          fields=['employee_id','department_id','job_id'])
               → Running contracts; headcount + all breakdown logic derives here.
      RPC 2 — search_read(hr.contract, [('state','=','draft')],
                          fields=['employee_id'])
               → incoming_count (New/draft bucket; separate from headcount).
      RPC 3 — search_read(hr.employee, [('active','=',True)], fields=['id'])
               → active_flag_count + active_without_running (search_read used
                 instead of search_count because the ID set is needed for both
                 metrics — one RPC covers both).

    Distinct-employee counting: a per-employee dict (keyed by employee_id) is
    built before grouping, guaranteeing sum(by_department) == headcount ==
    sum(by_job) by construction. Overwrite-on-duplicate handles the edge case
    of two Running contracts on the same employee_id without crashing.

    Baselines (live run 2026-06-03T08:22:41Z — post Dev-fix):
      headcount              == 115  (distinct Running-contract employees)
      incoming_count         == 0
      active_flag_count      == 136  (divergence indicator: NOT headcount)
      active_without_running == 34   (exit-gap 23 + data-gap 11)
      sum(by_department)     == 115
      sum(by_job)            == 115

    Raises:
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated.
        OdooQueryError: if any of the 3 Odoo RPCs fails.
    """
    _assert_read_only()

    cairo_today = datetime.now(_CAIRO_TZ).date()
    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_HEADCOUNT)
    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"Cache hit: {cache_key}")
        return {**cached, "cache_status": "cached", "rpc_duration_ms": 0}

    logger.info(f"Cache miss: {cache_key} — querying Odoo (3 RPCs)")

    _client = client if client is not None else OdooClient()

    t0 = time.monotonic()
    try:
        # RPC 1 — all Running contracts (employee_id + dept + job only; no PII)
        running_contracts = await _client.execute_kw(
            _CONTRACT_MODEL,
            "search_read",
            args=[[("state", "=", "open")]],
            kwargs={"fields": ["employee_id", "department_id", "job_id"]},
        )

        # RPC 2 — draft (New/incoming) contracts
        draft_contracts = await _client.execute_kw(
            _CONTRACT_MODEL,
            "search_read",
            args=[[("state", "=", "draft")]],
            kwargs={"fields": ["employee_id"]},
        )

        # RPC 3 — active employee IDs (search_read instead of search_count:
        #         the ID set is needed for both active_flag_count and
        #         active_without_running — one RPC covers both metrics)
        active_emp_records = await _client.execute_kw(
            _MODEL,
            "search_read",
            args=[[("active", "=", True)]],
            kwargs={"fields": ["id"]},
        )

    except Exception as exc:
        raise OdooQueryError(
            f"get_headcount() RPC failed: {exc}"
        ) from exc
    finally:
        if client is None:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        f"HR headcount: 3 RPCs in {rpc_ms}ms | cache_key={cache_key}"
    )

    # ── Build per-employee dicts ───────────────────────────────────────────────
    # One entry per distinct employee_id; overwrite on duplicate (correct
    # by the no-dup-running structural invariant; safe if violated).

    emp_dept: dict[int, tuple[int | None, str]] = {}
    emp_job:  dict[int, tuple[int | None, str]] = {}

    for c in running_contracts:
        emp_raw = c.get("employee_id")
        if isinstance(emp_raw, (list, tuple)) and emp_raw:
            eid = int(emp_raw[0])
        elif emp_raw and emp_raw is not False:
            eid = int(emp_raw)
        else:
            continue

        dept_raw = c.get("department_id")
        if isinstance(dept_raw, (list, tuple)) and len(dept_raw) >= 2:
            dept_id, dept_name = int(dept_raw[0]), str(dept_raw[1])
        else:
            dept_id, dept_name = None, _NO_DEPT_DISPLAY

        job_raw = c.get("job_id")
        if isinstance(job_raw, (list, tuple)) and len(job_raw) >= 2:
            job_id, job_name = int(job_raw[0]), str(job_raw[1])
        else:
            job_id, job_name = None, _NO_JOB_DISPLAY

        emp_dept[eid] = (dept_id, dept_name)
        emp_job[eid]  = (job_id,  job_name)

    headcount = len(emp_dept)

    # ── by_department (count distinct employees per dept) ─────────────────────
    dept_counter: dict[tuple[int | None, str], int] = {}
    for dept_id, dept_name in emp_dept.values():
        key = (dept_id, dept_name)
        dept_counter[key] = dept_counter.get(key, 0) + 1

    by_department = [
        {"department_id": did, "department_name": dname, "count": cnt}
        for (did, dname), cnt in dept_counter.items()
    ]
    by_department.sort(key=lambda r: (-r["count"], r["department_name"]))

    # ── by_job (count distinct employees per job) ─────────────────────────────
    job_counter: dict[tuple[int | None, str], int] = {}
    for job_id, job_name in emp_job.values():
        key = (job_id, job_name)
        job_counter[key] = job_counter.get(key, 0) + 1

    by_job = [
        {"job_id": jid, "job_name": jname, "count": cnt}
        for (jid, jname), cnt in job_counter.items()
    ]
    by_job.sort(key=lambda r: (-r["count"], r["job_name"]))

    # ── incoming_count (distinct employees with draft contracts) ──────────────
    incoming_emp_ids: set[int] = set()
    for c in draft_contracts:
        emp_raw = c.get("employee_id")
        if isinstance(emp_raw, (list, tuple)) and emp_raw:
            incoming_emp_ids.add(int(emp_raw[0]))
        elif emp_raw and emp_raw is not False:
            incoming_emp_ids.add(int(emp_raw))
    incoming_count = len(incoming_emp_ids)

    # ── active flag metadata ──────────────────────────────────────────────────
    running_emp_ids: set[int] = set(emp_dept.keys())
    active_emp_ids:  set[int] = {int(r["id"]) for r in active_emp_records}
    active_flag_count       = len(active_emp_ids)
    active_without_running  = len(active_emp_ids - running_emp_ids)

    result: dict = {
        "headcount":              headcount,
        "by_department":          by_department,
        "by_job":                 by_job,
        "incoming_count":         incoming_count,
        "active_flag_count":      active_flag_count,
        "active_without_running": active_without_running,
        "reference_date":         cairo_today.isoformat(),
        "as_of":                  datetime.now(timezone.utc).isoformat(),
        "cache_status":           "fresh",
        "rpc_duration_ms":        rpc_ms,
    }

    _cache.set(cache_key, result)
    return result


# ── KPI B — Tenure Distribution ───────────────────────────────────────────────

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


# ── KPI C — Payroll Risk Dashboard ───────────────────────────────────────────

_CACHE_KEY_PREFIX_PAYROLL_RISK = "kpi:hr:payroll_risk"
_BUCKET_LABELS = [
    "active_without_contract",
    "expired",
    "expiring_45d",
    "expiring_90d",
    "expiring_135d",
    "beyond_135d",
    "open_ended",
]


def _parse_dept_rows(rows: list) -> list[dict]:
    result = []
    for row in rows:
        dept_raw = row.get("department_id")
        if isinstance(dept_raw, (list, tuple)) and dept_raw:
            dept_id   = int(dept_raw[0])
            dept_name = str(dept_raw[1])
        else:
            dept_id   = None
            dept_name = _NO_DEPT_DISPLAY
        result.append({
            "department_id":   dept_id,
            "department_name": dept_name,
            "count":           int(row.get("__count") or 0),
        })
    result.sort(key=lambda r: (-r["count"], r["department_name"]))
    return result


async def get_payroll_risk_dashboard(client: Optional[OdooClient] = None) -> dict:
    """Return KPI C — Payroll Risk Dashboard.

    RPCs:
      RPC 1 — search_read(hr.contract, [('state','=','open')],
               fields=['id','employee_id','date_end'])
               -> all running contracts (~153: 136 active-emp + 17 orphan)
      RPC 2 — search_read(hr.employee, [('active','=',True)],
               fields=['id'])
               -> all 136 active employee IDs
      RPC 3a — read_group(hr.contract, [('id','in',expired_ids)],
                ['department_id'], ['department_id'], lazy=False)
                -> only when expired bucket is non-empty
      RPC 3b — read_group(hr.contract, [('id','in',expiring_45d_ids)],
                ['department_id'], ['department_id'], lazy=False)
                -> only when expiring_45d bucket is non-empty

    Bucket thresholds (delta = (date_end - cairo_today).days):
      active_without_contract : employee in active set, not in any running contract
      expired                 : delta < 0
      expiring_45d            : 0 <= delta <= 45
      expiring_90d            : 46 <= delta <= 90
      expiring_135d           : 91 <= delta <= 135
      beyond_135d             : delta >= 136
      open_ended              : date_end = False

    Orphan contracts (running contracts whose employee is not active) are
    counted separately in orphan_contracts_count and never touch the 7 buckets.

    Sanity invariant: sum(bucket counts) == len(active_emp_ids) == total_active.

    Baselines (verified 2026-05-29):
      active_without_contract == 17  (onboarding limbo — by-design)
      expired                 == 0   (alert if > 0)
      open_ended              == 1
      orphan_contracts_count  == 17  (paperwork debt from exit workflow)
      total_active            == 136

    Raises:
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated.
        OdooQueryError: if any Odoo RPC fails.
    """
    _assert_read_only()

    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_PAYROLL_RISK)
    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"Cache hit: {cache_key}")
        return {**cached, "cache_status": "cached", "rpc_duration_ms": 0}

    logger.info(f"Cache miss: {cache_key} — querying Odoo (2+ RPCs)")

    _client = client if client is not None else OdooClient()

    expired_dept_rows_raw:      list = []
    expiring_45d_dept_rows_raw: list = []

    t0 = time.monotonic()
    try:
        # RPC 1 — all running contracts (id + employee_id + date_end only; no PII)
        contract_records = await _client.execute_kw(
            _CONTRACT_MODEL,
            "search_read",
            args=[[("state", "=", "open")]],
            kwargs={"fields": ["id", "employee_id", "date_end"]},
        )

        # RPC 2 — all active employee IDs
        emp_records = await _client.execute_kw(
            _MODEL,
            "search_read",
            args=[[("active", "=", True)]],
            kwargs={"fields": ["id"]},
        )

        # ── Python classification (no RPCs) ───────────────────────────────
        cairo_today = datetime.now(_CAIRO_TZ).date()
        active_emp_ids: set[int] = {int(r["id"]) for r in emp_records}

        orphan_count:     int            = 0
        covered_emp_ids:  set[int]       = set()
        bucket_counts:    dict[str, int] = {label: 0 for label in _BUCKET_LABELS}
        expired_ids:      list[int]      = []
        expiring_45d_ids: list[int]      = []

        for c in contract_records:
            emp_raw = c.get("employee_id")
            if isinstance(emp_raw, (list, tuple)) and emp_raw:
                emp_id = int(emp_raw[0])
            else:
                emp_id = int(emp_raw) if emp_raw else 0

            if emp_id not in active_emp_ids:
                orphan_count += 1
                continue

            covered_emp_ids.add(emp_id)
            date_end_raw = c.get("date_end")

            if not date_end_raw:
                bucket_counts["open_ended"] += 1
            else:
                delta = (date.fromisoformat(str(date_end_raw)) - cairo_today).days
                cid = int(c["id"])
                if delta < 0:
                    bucket_counts["expired"] += 1
                    expired_ids.append(cid)
                elif delta <= 45:
                    bucket_counts["expiring_45d"] += 1
                    expiring_45d_ids.append(cid)
                elif delta <= 90:
                    bucket_counts["expiring_90d"] += 1
                elif delta <= 135:
                    bucket_counts["expiring_135d"] += 1
                else:
                    bucket_counts["beyond_135d"] += 1

        bucket_counts["active_without_contract"] = len(active_emp_ids - covered_emp_ids)

        # ── Conditional department breakdown RPCs ─────────────────────────
        if expired_ids:
            # RPC 3a — department breakdown for expired bucket
            expired_dept_rows_raw = await _client.execute_kw(
                _CONTRACT_MODEL,
                "read_group",
                args=[[("id", "in", expired_ids)], ["department_id"], ["department_id"]],
                kwargs={"lazy": False},
            )

        if expiring_45d_ids:
            # RPC 3b — department breakdown for expiring_45d bucket
            expiring_45d_dept_rows_raw = await _client.execute_kw(
                _CONTRACT_MODEL,
                "read_group",
                args=[[("id", "in", expiring_45d_ids)], ["department_id"], ["department_id"]],
                kwargs={"lazy": False},
            )

    except Exception as exc:
        raise OdooQueryError(
            f"get_payroll_risk_dashboard() failed: {exc}"
        ) from exc
    finally:
        if client is None:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        f"HR payroll risk dashboard: RPCs in {rpc_ms}ms | cache_key={cache_key}"
    )

    buckets      = [{"label": label, "count": bucket_counts[label]} for label in _BUCKET_LABELS]
    total_active = sum(b["count"] for b in buckets)

    result: dict = {
        "buckets":                           buckets,
        "department_breakdown_expired":      _parse_dept_rows(expired_dept_rows_raw),
        "department_breakdown_expiring_45d": _parse_dept_rows(expiring_45d_dept_rows_raw),
        "orphan_contracts_count":            orphan_count,
        "total_active":                      total_active,
        "reference_date":                    cairo_today.isoformat(),
        "as_of":                             datetime.now(timezone.utc).isoformat(),
        "cache_status":                      "fresh",
        "rpc_duration_ms":                   rpc_ms,
    }

    _cache.set(cache_key, result)
    return result
