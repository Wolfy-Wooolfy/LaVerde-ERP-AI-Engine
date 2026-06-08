"""
HR KPI service — business logic for Module 5 KPIs.

Data source: hr.employee, hr.contract, hr.department, hr.job via the shared
read-only OdooClient. All methods are async. No method ever calls
create, write, or unlink.

KPI A (re-foundation 2026-06-03): get_headcount() rebuilt on Running contracts.
KPI B (re-foundation 2026-06-03): get_tenure_distribution() rebuilt on net
  accumulated service (sum of worked periods) for Running-contract employees.
Employment = holding a contract in state='open'. hr.employee.active is NOT an
employment signal. See §3.6 in HR_CLUSTER_DISCOVERY.md.
"""

import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
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
_RUNNING_STATE = "open"

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
_BAND_LABELS = ["lt1y", "y1_3", "y3_5", "y5_10", "y10plus"]


def _tenure_years(fcd: date, today: date) -> int:
    # Use anniversary method, not (days / 365.25), because tenure in
    # years is a calendar concept (the date of the Nth anniversary of
    # hire), not an elapsed-duration concept. Anniversary method gives
    # the count of completed anniversary years on `today`. The
    # alternative (days/365.25 with floor) understates by 1 in every
    # non-leap year because 365 / 365.25 = 0.9979... < 1, so an
    # employee at their 1-year anniversary on a non-leap year would
    # land in "lt1y".
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
        return "lt1y"
    if years < 3:
        return "y1_3"
    if years < 5:
        return "y3_5"
    if years < 10:
        return "y5_10"
    return "y10plus"


async def get_tenure_distribution(client: Optional[OdooClient] = None) -> dict:
    """Return KPI B — Tenure Distribution (re-foundation 2026-06-03).

    Employment: Running contract (state='open') — NOT hr.employee.active.
    See §3.6 in HR_CLUSTER_DISCOVERY.md.

    Tenure: net accumulated service = sum of all worked periods across ALL
    hr.contract records for each Running-contract employee, with overlaps
    clamped and gaps naturally excluded (§3.7 D2).

    1 RPC:
      search_read(hr.contract, [],
                  ['employee_id', 'state', 'date_start', 'date_end'],
                  context={'active_test': False})
      Returns all contracts (all states). active_test=False required: 13
      Running contracts belong to archived employees (active=False) and would
      be silently dropped without it, losing 13 employed people.

    Algorithm (per Running-contract employee):
      1. Collect ALL contract records for this employee (any state).
      2. Per contract: start = date_start; end = date_end or cairo_today.
         Non-Running contracts with null date_start: skip (uncomputable period).
         Running contract with null date_start: missing_date_count, skip employee.
      3. Sort periods by start. Merge overlaps: if next.start < prev.end, extend
         prev.end = max(prev.end, next.end). Gaps are naturally absent from the sum.
      4. total_days = sum((end - start).days) over merged periods.
      5. virtual_start = cairo_today - timedelta(days=total_days).
         _tenure_years(virtual_start, cairo_today) gives completed anniversary
         years in the accumulated service. For a single-contract employee:
         virtual_start == date_start (total_days == cairo_today - date_start
         so cairo_today - total_days == date_start). The existing banding logic
         is identical to the prior implementation for this case; returning
         employees compute correctly with no special-casing required.

    Baselines (2026-06-03, post Dev-fix):
      Running contracts: 115; null date_start on Running: 0; null date_end: 1.
      All 115 employed employees hold exactly 1 contract today — the general
      net-accumulated logic reduces to today - date_start for each, matching
      the prior first_contract_date implementation. General logic handles future
      returning employees with no code change.

    Raises:
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated.
        OdooQueryError: if the Odoo RPC fails.
    """
    _assert_read_only()

    cairo_today = datetime.now(_CAIRO_TZ).date()
    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_TENURE)
    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"Cache hit: {cache_key}")
        return {**cached, "cache_status": "cached", "rpc_duration_ms": 0}

    logger.info(f"Cache miss: {cache_key} — querying Odoo (1 RPC)")

    _client = client if client is not None else OdooClient()

    t0 = time.monotonic()
    try:
        all_contracts: list[dict] = await _client.execute_kw(
            _CONTRACT_MODEL,
            "search_read",
            args=[[]],
            kwargs={
                "fields": ["employee_id", "state", "date_start", "date_end"],
                "context": {"active_test": False},
            },
        )
    except Exception as exc:
        raise OdooQueryError(
            f"get_tenure_distribution() RPC on {_CONTRACT_MODEL} failed: {exc}"
        ) from exc
    finally:
        if client is None:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        f"HR tenure distribution: 1 RPC on {_CONTRACT_MODEL} in {rpc_ms}ms "
        f"| cache_key={cache_key}"
    )

    # ── Group all contracts by employee_id ────────────────────────────────────
    contracts_by_emp: dict[int, list[dict]] = defaultdict(list)
    for c in all_contracts:
        emp_raw = c.get("employee_id")
        if isinstance(emp_raw, (list, tuple)) and emp_raw:
            eid = int(emp_raw[0])
        elif emp_raw and emp_raw is not False:
            eid = int(emp_raw)
        else:
            continue
        contracts_by_emp[eid].append(c)

    # Distinct employees with at least one Running contract — true headcount
    running_emp_ids: set[int] = {
        eid
        for eid, cs in contracts_by_emp.items()
        if any(c.get("state") == _RUNNING_STATE for c in cs)
    }

    # ── Classify each Running-contract employee ───────────────────────────────
    band_counts: dict[str, int] = {label: 0 for label in _BAND_LABELS}
    missing_date_count = 0

    for eid in running_emp_ids:
        periods: list[tuple[date, date]] = []
        running_null_start = False

        for c in contracts_by_emp[eid]:
            ds_raw = c.get("date_start")
            if not ds_raw:
                if c.get("state") == _RUNNING_STATE:
                    running_null_start = True
                continue  # skip this period (non-Running with null start: uncomputable)
            ds = date.fromisoformat(str(ds_raw))
            de_raw = c.get("date_end")
            de = date.fromisoformat(str(de_raw)) if de_raw else cairo_today
            periods.append((ds, de))

        if running_null_start:
            missing_date_count += 1
            continue

        if not periods:
            missing_date_count += 1
            continue

        periods.sort(key=lambda p: p[0])

        # Merge overlapping intervals. Any period whose start falls before the
        # last merged period's end overlaps — advance its effective start to
        # the merged end. Gaps between non-overlapping periods are naturally
        # absent from the sum (they occupy no merged interval).
        merged: list[list[date]] = [list(periods[0])]
        for (start, end) in periods[1:]:
            if start < merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])

        total_days = sum((end - start).days for (start, end) in merged)

        # Map total worked days back to a concrete date so _tenure_years() —
        # which expects a hire date — applies correctly to accumulated service.
        # For a single-contract employee virtual_start == date_start exactly.
        virtual_start = cairo_today - timedelta(days=total_days)
        band_counts[_assign_band(_tenure_years(virtual_start, cairo_today))] += 1

    bands = [{"band": label, "count": band_counts[label]} for label in _BAND_LABELS]
    total_employed = sum(b["count"] for b in bands) + missing_date_count

    result: dict = {
        "bands":              bands,
        "missing_date_count": missing_date_count,
        "total_employed":     total_employed,
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
    """Return KPI C — Payroll Risk Dashboard (re-foundation 2026-06-04).

    Employment: Running contract (state='open') — NOT hr.employee.active.
    See §3.6 in HR_CLUSTER_DISCOVERY.md.

    RPCs:
      RPC 1 — search_read(hr.contract, [('state','=','open')],
               fields=['id','employee_id','date_end'])
               → 115 Running contracts. active_test is inert on hr.contract
                 (Item 0, discover_payroll_risk_shape.py 2026-06-04); no flag
                 added — all 115 returned regardless of employee archive flag.
      RPC 2 — search_read(hr.employee, [('active','=',True)],
               fields=['id'])
               → 136 active employee IDs; used for metadata only, NOT as an
                 employment gate or bucket filter.
      RPC 3 — (conditional: only when awc_emp_ids is non-empty)
               search_read(hr.contract,
                 [('employee_id','in',list(awc_emp_ids))],
                 fields=['employee_id','state'])
               → all contracts (any state) for active-but-no-running employees;
                 used to classify exit_gap / incoming / data_gap metadata.
      RPC 4a — read_group for expired dept breakdown (conditional)
      RPC 4b — read_group for expiring_45d dept breakdown (conditional)

    Bucket thresholds (delta = (date_end − cairo_today).days):
      expired       : delta < 0
      expiring_45d  : 0 <= delta <= 45
      expiring_90d  : 46 <= delta <= 90
      expiring_135d : 91 <= delta <= 135
      beyond_135d   : delta >= 136
      open_ended    : date_end = False/null

    All 115 Running-contract employees are bucketed regardless of the
    hr.employee.active flag. The 13 archived-running employees (§3.6 Issue 1)
    are employed — their archive flag is stale data. They are counted in
    archived_with_running_count as a data-quality signal only.

    active=True employees with no Running contract are NOT employed and are
    excluded from all buckets. They surface in active_flag_no_running_*
    metadata fields (data-quality signals).

    Sanity invariant (by construction):
      sum(6 bucket counts) == total_employed == len(running_emp_ids)
      total_employed must equal KPI A headcount (both count distinct
      Running-contract employees).

    Distinct-employee discipline: running_emp_ids is built inside the loop;
    `if emp_id in running_emp_ids: continue` before bucket assignment ensures
    each employee is counted exactly once. Handles the zero-today-but-possible
    case of two Running contracts on the same employee_id without crashing.

    Department breakdown reads department_id from hr.contract (the contract is
    the source of truth for the employed population, consistent with KPI A).

    Baselines (discover_payroll_risk_shape.py 2026-06-04):
      total_employed               == 115
      archived_with_running_count  == 13
      active_flag_no_running_count == 34  (exit_gap=23, incoming=0, data_gap=11)
      expiring_45d                 ≈ 113  (101 active + 12 archived-running)
      expiring_135d                == 1   (2026-09-08 outlier)
      open_ended                   == 1   (archived-running emp 2960, null date_end)
      expired                      == 0   (alert if > 0 — auto-flip holds post-fix)

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
        # RPC 1 — all Running contracts (id + employee_id + date_end; no PII)
        # active_test is inert on hr.contract (Item 0, 2026-06-04) — no flag added
        contract_records = await _client.execute_kw(
            _CONTRACT_MODEL,
            "search_read",
            args=[[("state", "=", "open")]],
            kwargs={"fields": ["id", "employee_id", "date_end"]},
        )

        # RPC 2 — active employee IDs (for metadata only; NOT an employment gate)
        active_emp_records = await _client.execute_kw(
            _MODEL,
            "search_read",
            args=[[("active", "=", True)]],
            kwargs={"fields": ["id"]},
        )

        cairo_today = datetime.now(_CAIRO_TZ).date()
        active_emp_ids: set[int] = {int(r["id"]) for r in active_emp_records}

        running_emp_ids:          set[int]       = set()
        archived_running_emp_ids: set[int]       = set()
        bucket_counts:            dict[str, int] = {label: 0 for label in _BUCKET_LABELS}
        expired_ids:              list[int]      = []
        expiring_45d_ids:         list[int]      = []

        for c in contract_records:
            emp_raw = c.get("employee_id")
            if isinstance(emp_raw, (list, tuple)) and emp_raw:
                emp_id = int(emp_raw[0])
            else:
                emp_id = int(emp_raw) if emp_raw else 0
            if not emp_id:
                continue

            # Track archive-flag status for metadata (does NOT gate bucketing)
            if emp_id not in active_emp_ids:
                archived_running_emp_ids.add(emp_id)

            # Distinct-employee guard: bucket each employee exactly once
            if emp_id in running_emp_ids:
                continue
            running_emp_ids.add(emp_id)

            cid          = int(c["id"])
            date_end_raw = c.get("date_end")

            if not date_end_raw:
                bucket_counts["open_ended"] += 1
            else:
                delta = (date.fromisoformat(str(date_end_raw)) - cairo_today).days
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

        # ── active_flag_no_running metadata ──────────────────────────────────
        awc_emp_ids                  = active_emp_ids - running_emp_ids
        active_flag_no_running_count = len(awc_emp_ids)
        exit_gap                     = 0
        incoming                     = 0
        data_gap                     = 0

        if awc_emp_ids:
            # RPC 3 — all contracts (any state) for active-but-no-running employees
            awc_contract_records = await _client.execute_kw(
                _CONTRACT_MODEL,
                "search_read",
                args=[[("employee_id", "in", list(awc_emp_ids))]],
                kwargs={"fields": ["employee_id", "state"]},
            )
            awc_states_by_emp: dict[int, set[str]] = defaultdict(set)
            for c in awc_contract_records:
                emp_raw = c.get("employee_id")
                if isinstance(emp_raw, (list, tuple)) and emp_raw:
                    eid = int(emp_raw[0])
                elif emp_raw and emp_raw is not False:
                    eid = int(emp_raw)
                else:
                    continue
                s = c.get("state")
                if s:
                    awc_states_by_emp[eid].add(s)

            for eid in awc_emp_ids:
                states = awc_states_by_emp.get(eid, set())
                if not states:
                    data_gap += 1
                elif "draft" in states:
                    incoming += 1       # new hire pending activation
                else:
                    exit_gap += 1       # only close/cancel — departed, unarchived

        # ── Conditional department breakdown RPCs ─────────────────────────────
        if expired_ids:
            # RPC 4a — department breakdown for expired bucket
            expired_dept_rows_raw = await _client.execute_kw(
                _CONTRACT_MODEL,
                "read_group",
                args=[[("id", "in", expired_ids)], ["department_id"], ["department_id"]],
                kwargs={"lazy": False},
            )

        if expiring_45d_ids:
            # RPC 4b — department breakdown for expiring_45d bucket
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

    buckets        = [{"label": label, "count": bucket_counts[label]} for label in _BUCKET_LABELS]
    total_employed = sum(b["count"] for b in buckets)   # == len(running_emp_ids) by construction

    result: dict = {
        "buckets":                           buckets,
        "department_breakdown_expired":      _parse_dept_rows(expired_dept_rows_raw),
        "department_breakdown_expiring_45d": _parse_dept_rows(expiring_45d_dept_rows_raw),
        "archived_with_running_count":       len(archived_running_emp_ids),
        "active_flag_no_running_count":      active_flag_no_running_count,
        "active_flag_no_running_exit_gap":   exit_gap,
        "active_flag_no_running_incoming":   incoming,
        "active_flag_no_running_data_gap":   data_gap,
        "total_employed":                    total_employed,
        "reference_date":                    cairo_today.isoformat(),
        "as_of":                             datetime.now(timezone.utc).isoformat(),
        "cache_status":                      "fresh",
        "rpc_duration_ms":                   rpc_ms,
    }

    _cache.set(cache_key, result)
    return result


# ── KPI D — Department Payroll Cost ──────────────────────────────────────────

_CACHE_KEY_PREFIX_DEPT_COST = "kpi:hr:department_cost"
_DEPT_COST_K_ANON_MIN = 3
_OTHER_DEPT_LABEL = "Other (small departments)"


async def get_department_cost(client: Optional[OdooClient] = None) -> dict:
    """Return KPI D — Department Payroll Cost (2026-06-07).

    Employment: Running contract (state='open') — NOT hr.employee.active.
    See §3.6 in HR_CLUSTER_DISCOVERY.md.

    1 RPC:
      search_read(hr.contract, [('state','=','open')],
                  fields=['department_id', 'wage', 'employee_id'])
      active_test omitted: all open contracts carry active=True (Item 0,
      discover_payroll_risk_shape.py 2026-06-04); the context flag filters
      hr.contract.active only — it never inspects employee.active.
      Omitting keeps the population provably identical to KPI A/C.

    Aggregation:
      SUM(wage) over all open contracts grouped by department_id.
      running_contract_count per department = distinct employee_id count
      (apples-to-apples with KPI A headcount; surfaces multi-contract
      anomalies without crashing). Contracts without employee_id skipped.
      wage=0 contributor: counts as 1 employee, adds 0 to SUM (§3.8 W1).
      grand_total_wage = SUM(wage) over all open contracts.
      total_running_contracts = distinct employee_ids (== KPI A headcount).

    k-anonymity (§3.8 W3), threshold = _DEPT_COST_K_ANON_MIN = 3:
      Departments whose distinct employee count < 3 are POOLED into
      "Other (small departments)" (summed wage + union of employee sets).
      If the combined pool count < 3: total_wage for the Other row is
      returned as null (suppressed). grand_total_wage is always returned
      (115 employees >= 3 — safe by construction).

    Baseline (2026-06-03 post-fix): 115 Running contracts, 21 departments.

    Raises:
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated.
        OdooQueryError: if the Odoo RPC fails.
    """
    _assert_read_only()

    cairo_today = datetime.now(_CAIRO_TZ).date()
    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_DEPT_COST)
    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"Cache hit: {cache_key}")
        return {**cached, "cache_status": "cached", "rpc_duration_ms": 0}

    logger.info(f"Cache miss: {cache_key} — querying Odoo (1 RPC)")

    _client = client if client is not None else OdooClient()

    t0 = time.monotonic()
    try:
        # active_test omitted: all open contracts carry active=True (Item 0,
        # 2026-06-04); flag filters hr.contract.active only, never employee.active.
        # Population is provably identical to KPI A/C.
        contracts = await _client.execute_kw(
            _CONTRACT_MODEL,
            "search_read",
            args=[[("state", "=", "open")]],
            kwargs={"fields": ["department_id", "wage", "employee_id"]},
        )
    except Exception as exc:
        raise OdooQueryError(
            f"get_department_cost() RPC on {_CONTRACT_MODEL} failed: {exc}"
        ) from exc
    finally:
        if client is None:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        f"HR department cost: 1 RPC on {_CONTRACT_MODEL} in {rpc_ms}ms "
        f"| cache_key={cache_key}"
    )

    # ── Aggregate SUM(wage) and distinct employee set per department ──────────
    dept_wages:   dict[tuple, float]    = defaultdict(float)
    dept_emp_ids: dict[tuple, set[int]] = defaultdict(set)
    grand_total_wage: float             = 0.0
    all_emp_ids:  set[int]              = set()

    for c in contracts:
        emp_raw = c.get("employee_id")
        if isinstance(emp_raw, (list, tuple)) and emp_raw:
            emp_id = int(emp_raw[0])
        elif emp_raw and emp_raw is not False:
            emp_id = int(emp_raw)
        else:
            continue  # skip orphaned contracts (no employee_id)

        dept_raw = c.get("department_id")
        if isinstance(dept_raw, (list, tuple)) and len(dept_raw) >= 2:
            dept_key: tuple = (int(dept_raw[0]), str(dept_raw[1]))
        else:
            dept_key = (None, _NO_DEPT_DISPLAY)

        wage = float(c.get("wage") or 0)

        dept_wages[dept_key]   += wage
        grand_total_wage       += wage
        dept_emp_ids[dept_key].add(emp_id)
        all_emp_ids.add(emp_id)

    total_running_contracts = len(all_emp_ids)

    # ── k-anonymity pooling ───────────────────────────────────────────────────
    normal_rows:    list[dict] = []
    pooled_wage:    float      = 0.0
    pooled_emp_ids: set[int]   = set()

    for (dept_id, dept_name), emp_ids in dept_emp_ids.items():
        if len(emp_ids) >= _DEPT_COST_K_ANON_MIN:
            normal_rows.append({
                "department_id":          dept_id,
                "department_name":        dept_name,
                "running_contract_count": len(emp_ids),
                "total_wage":             dept_wages[(dept_id, dept_name)],
            })
        else:
            pooled_wage    += dept_wages[(dept_id, dept_name)]
            pooled_emp_ids |= emp_ids

    pooled_count = len(pooled_emp_ids)
    if pooled_count > 0:
        other_wage: float | None = (
            pooled_wage if pooled_count >= _DEPT_COST_K_ANON_MIN else None
        )
        normal_rows.append({
            "department_id":          None,
            "department_name":        _OTHER_DEPT_LABEL,
            "running_contract_count": pooled_count,
            "total_wage":             other_wage,
        })

    # Highest wage first; suppressed Other (total_wage=None) treated as 0 → sorts last
    normal_rows.sort(key=lambda r: (-(r["total_wage"] or 0.0), r["department_name"]))

    result: dict = {
        "rows":                    normal_rows,
        "grand_total_wage":        grand_total_wage,
        "total_running_contracts": total_running_contracts,
        "currency":                "EGP",
        "basis":                   "monthly",
        "reference_date":          cairo_today.isoformat(),
        "as_of":                   datetime.now(timezone.utc).isoformat(),
        "cache_status":            "fresh",
        "rpc_duration_ms":         rpc_ms,
    }

    _cache.set(cache_key, result)
    return result
