"""
Unit tests for HR KPI service — get_headcount (KPI A).

Re-foundation 2026-06-03: headcount is now derived from Running contracts
(hr.contract state='open'), NOT from hr.employee.active.

Mock shape: side_effect=[running_contracts, draft_contracts, active_emp_records]
  running_contracts  — list of dicts with employee_id, department_id, job_id
  draft_contracts    — list of dicts with employee_id
  active_emp_records — list of dicts with id

Live verification: scripts/verify_kpi_a_headcount_live.py (D4).
Baselines (live run 2026-06-03T08:22:41Z, post Dev-fix):
  headcount              == 115
  incoming_count         == 0
  active_flag_count      == 136
  active_without_running == 34
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.core.exceptions import OdooQueryError, ReadOnlyViolationError
from backend.modules.hr.services import cache as _cache
from backend.modules.hr.services.kpi_service import (
    _CACHE_KEY_PREFIX_HEADCOUNT,
    _NO_DEPT_DISPLAY,
    _NO_JOB_DISPLAY,
    get_headcount,
)

# ── Shared mock data (happy path) ─────────────────────────────────────────────
#
# 4 Running contracts → headcount=4 (employees 1-4)
# 1 Draft contract   → incoming_count=1 (employee 5, not in headcount)
# 5 Active employees → active_flag_count=5; employee 6 has no Running contract
#                      → active_without_running=1

_RUNNING_CONTRACTS = [
    {"employee_id": [1, "Alice"], "department_id": [10, "Sales"],    "job_id": [20, "Manager"]},
    {"employee_id": [2, "Bob"],   "department_id": [10, "Sales"],    "job_id": [21, "Analyst"]},
    {"employee_id": [3, "Carol"], "department_id": [11, "Finance"],  "job_id": [20, "Manager"]},
    {"employee_id": [4, "Dan"],   "department_id": False,             "job_id": False},
]

_DRAFT_CONTRACTS = [
    {"employee_id": [5, "Eve"]},
]

_ACTIVE_EMP_RECORDS = [
    {"id": 1}, {"id": 2}, {"id": 3}, {"id": 4},
    {"id": 6},  # active=True but no Running contract → active_without_running
]


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def fresh_cache():
    _cache.clear()
    yield
    _cache.clear()


@pytest.fixture
def mock_client():
    """3-RPC mock: running_contracts, draft_contracts, active_emp_records."""
    return _make_client(_RUNNING_CONTRACTS, _DRAFT_CONTRACTS, _ACTIVE_EMP_RECORDS)


def _make_client(running, draft, active):
    client = MagicMock()
    client.execute_kw = AsyncMock(side_effect=[running, draft, active])
    return client


# ── Test 1 — Happy path: headcount from Running contracts ─────────────────────


async def test_happy_path_headcount_from_running_contracts(mock_client):
    result = await get_headcount(client=mock_client)

    assert result["headcount"] == 4
    assert set(result.keys()) == {
        "headcount", "by_department", "by_job",
        "incoming_count", "active_flag_count", "active_without_running",
        "reference_date", "as_of", "cache_status", "rpc_duration_ms",
    }
    assert result["cache_status"] == "fresh"
    assert result["rpc_duration_ms"] >= 0
    assert isinstance(result["as_of"], str)


# ── Test 2 — Distinct counting: same employee_id in two Running contracts ──────


async def test_distinct_counting_same_employee_two_contracts():
    running = [
        {"employee_id": [1, "Alice"], "department_id": [10, "Sales"], "job_id": [20, "Manager"]},
        {"employee_id": [1, "Alice"], "department_id": [10, "Sales"], "job_id": [20, "Manager"]},
    ]
    client = _make_client(running, [], [{"id": 1}])

    result = await get_headcount(client=client)

    assert result["headcount"] == 1, (
        "Two Running contracts for the same employee_id must count as one employee"
    )
    assert sum(r["count"] for r in result["by_department"]) == 1
    assert sum(r["count"] for r in result["by_job"]) == 1


# ── Test 3 — by_department grouping and null bucket ───────────────────────────


async def test_by_department_grouping_and_null_bucket(mock_client):
    result = await get_headcount(client=mock_client)

    by_dept = result["by_department"]
    dept_names = [r["department_name"] for r in by_dept]

    assert _NO_DEPT_DISPLAY in dept_names, "Null-dept bucket must appear in by_department"

    sales_row = next(r for r in by_dept if r.get("department_id") == 10)
    assert sales_row["department_name"] == "Sales"
    assert sales_row["count"] == 2

    null_row = next(r for r in by_dept if r["department_id"] is None)
    assert null_row["department_name"] == _NO_DEPT_DISPLAY
    assert null_row["count"] == 1


# ── Test 4 — by_job grouping and null bucket ──────────────────────────────────


async def test_by_job_grouping_and_null_bucket(mock_client):
    result = await get_headcount(client=mock_client)

    by_job = result["by_job"]
    job_names = [r["job_name"] for r in by_job]

    assert _NO_JOB_DISPLAY in job_names, "Null-job bucket must appear in by_job"

    manager_row = next(r for r in by_job if r.get("job_id") == 20)
    assert manager_row["job_name"] == "Manager"
    assert manager_row["count"] == 2

    null_row = next(r for r in by_job if r["job_id"] is None)
    assert null_row["job_name"] == _NO_JOB_DISPLAY
    assert null_row["count"] == 1


# ── Test 5 — incoming_count from draft contracts ──────────────────────────────


async def test_incoming_count_from_draft_contracts(mock_client):
    result = await get_headcount(client=mock_client)

    assert result["incoming_count"] == 1
    assert result["headcount"] == 4, "Draft employee must NOT be counted in headcount"


# ── Test 6 — active_flag_count metadata present ───────────────────────────────


async def test_active_flag_count_metadata_present(mock_client):
    result = await get_headcount(client=mock_client)

    assert result["active_flag_count"] == 5


# ── Test 7 — active_without_running computed correctly ───────────────────────


async def test_active_without_running_computed(mock_client):
    result = await get_headcount(client=mock_client)

    # active_emp_ids = {1,2,3,4,6}; running_emp_ids = {1,2,3,4}; diff = {6}
    assert result["active_without_running"] == 1


# ── Test 8 — Sanity invariant: sum(by_department) == headcount ───────────────


async def test_sanity_invariant_dept_sum_equals_headcount(mock_client):
    result = await get_headcount(client=mock_client)

    dept_sum = sum(r["count"] for r in result["by_department"])
    assert dept_sum == result["headcount"], (
        f"by_department counts must sum to headcount={result['headcount']}, got {dept_sum}"
    )


# ── Test 9 — Sanity invariant: sum(by_job) == headcount ──────────────────────


async def test_sanity_invariant_job_sum_equals_headcount(mock_client):
    result = await get_headcount(client=mock_client)

    job_sum = sum(r["count"] for r in result["by_job"])
    assert job_sum == result["headcount"], (
        f"by_job counts must sum to headcount={result['headcount']}, got {job_sum}"
    )


# ── Test 10 — All-zero: no Running contracts ──────────────────────────────────


async def test_all_zero_no_running_contracts():
    client = _make_client([], [], [])

    result = await get_headcount(client=client)

    assert result["headcount"] == 0
    assert result["by_department"] == []
    assert result["by_job"] == []
    assert result["incoming_count"] == 0
    assert result["active_flag_count"] == 0
    assert result["active_without_running"] == 0
    assert result["cache_status"] == "fresh"


# ── Test 11 — Cache hit: second call uses cache, execute_kw called only 3× ───


async def test_cache_hit_second_call(mock_client):
    result1 = await get_headcount(client=mock_client)
    result2 = await get_headcount(client=mock_client)

    assert mock_client.execute_kw.call_count == 3, (
        "execute_kw must fire exactly 3 times on the first call; "
        "the second call must be served from cache"
    )
    assert result1["cache_status"] == "fresh"
    assert result2["cache_status"] == "cached"
    assert result2["rpc_duration_ms"] == 0
    assert result2["headcount"] == result1["headcount"]


# ── Test 12 — Read-only guard fires before any RPC ───────────────────────────


async def test_read_only_guard_fires_before_rpc(monkeypatch, mock_client):
    monkeypatch.setattr(
        "backend.modules.hr.services.kpi_service.ALLOWED_METHODS",
        frozenset({"search_read", "write"}),
    )

    with pytest.raises(ReadOnlyViolationError):
        await get_headcount(client=mock_client)

    mock_client.execute_kw.assert_not_called()


# ── Test 13 — RPC failure raises OdooQueryError ──────────────────────────────


async def test_rpc_failure_raises_odoo_query_error(mock_client):
    mock_client.execute_kw.side_effect = RuntimeError("connection refused")

    with pytest.raises(OdooQueryError):
        await get_headcount(client=mock_client)


# ── Test 14 — RPC failure writes no cache entry ──────────────────────────────


async def test_rpc_failure_writes_no_cache_entry(mock_client):
    mock_client.execute_kw.side_effect = RuntimeError("timeout")

    with pytest.raises(OdooQueryError):
        await get_headcount(client=mock_client)

    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_HEADCOUNT)
    assert _cache.get(cache_key) is None, "A failed RPC must not leave a cache entry"


# ── Test 15 — Cairo reference_date present and ISO-formatted ─────────────────


async def test_cairo_reference_date_present(mock_client):
    result = await get_headcount(client=mock_client)

    ref = result["reference_date"]
    assert isinstance(ref, str)
    parsed = date.fromisoformat(ref)
    assert str(parsed) == ref


# ── Test 16 — Mixed realistic: Running + Draft + active-flag metadata ─────────


async def test_mixed_realistic_running_and_draft_and_metadata():
    running = [
        {"employee_id": [10, "Ahmad"],   "department_id": [1, "HR"],      "job_id": [5, "Director"]},
        {"employee_id": [11, "Mona"],    "department_id": [1, "HR"],      "job_id": [6, "Specialist"]},
        {"employee_id": [12, "Khaled"],  "department_id": [2, "Finance"], "job_id": [5, "Director"]},
        {"employee_id": [13, "Sara"],    "department_id": [2, "Finance"], "job_id": False},
        {"employee_id": [14, "Youssef"], "department_id": False,           "job_id": [6, "Specialist"]},
    ]
    draft = [
        {"employee_id": [15, "Nour"]},
        {"employee_id": [15, "Nour"]},   # duplicate — must still count as 1 incoming
    ]
    active = [
        {"id": 10}, {"id": 11}, {"id": 12}, {"id": 13}, {"id": 14},
        {"id": 16},  # active=True but no Running contract
        {"id": 17},  # active=True but no Running contract
    ]

    client = _make_client(running, draft, active)
    result = await get_headcount(client=client)

    assert result["headcount"] == 5
    assert result["incoming_count"] == 1, "Duplicate draft rows for same employee must count once"
    assert result["active_flag_count"] == 7
    assert result["active_without_running"] == 2

    dept_sum = sum(r["count"] for r in result["by_department"])
    job_sum  = sum(r["count"] for r in result["by_job"])
    assert dept_sum == 5
    assert job_sum  == 5

    # Sort: DESC count, then ASC name on ties.
    # HR: count=2, "HR"; Finance: count=2, "Finance" → Finance < HR alphabetically → Finance first
    dept_names = [r["department_name"] for r in result["by_department"]]
    assert dept_names[0] == "Finance"
    assert dept_names[1] == "HR"
    assert _NO_DEPT_DISPLAY in dept_names

    # Director: count=2, "Director"; Specialist: count=2, "Specialist" → Director < Specialist → Director first
    job_names = [r["job_name"] for r in result["by_job"]]
    assert job_names[0] == "Director"
    assert job_names[1] == "Specialist"
    assert _NO_JOB_DISPLAY in job_names
