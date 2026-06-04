"""
Unit tests for HR KPI service — get_payroll_risk_dashboard (KPI C).

Re-foundation 2026-06-04: buckets now cover ALL Running-contract employees
regardless of hr.employee.active flag. active_without_contract bucket removed.
Archived-running employees are bucketed and counted in archived_with_running_count
metadata. active=True employees with no Running contract surface in
active_flag_no_running_* metadata fields only.

OdooClient is fully mocked; no live Odoo connection is made.
Live verification: scripts/verify_kpi_c_payroll_risk_live.py (D4).

Baselines (discover_payroll_risk_shape.py 2026-06-04):
  total_employed               == 115
  archived_with_running_count  == 13
  active_flag_no_running_count == 34 (exit_gap=23, incoming=0, data_gap=11)
  expiring_45d                 ≈ 113
  expired                      == 0  (expected — alert if > 0)

Reference date pinned to 2026-06-02 in all date-sensitive tests.

RPC order in get_payroll_risk_dashboard():
  RPC 1 : search_read(hr.contract, state='open')          → contract_records
  RPC 2 : search_read(hr.employee, active=True)           → emp_records
  RPC 3 : search_read(hr.contract, emp_id in awc_ids)     → awc_contract_records
          (conditional — only when awc_emp_ids is non-empty)
  RPC 4a: read_group expired dept breakdown               (conditional)
  RPC 4b: read_group expiring_45d dept breakdown          (conditional)
"""

from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.core.exceptions import OdooQueryError, ReadOnlyViolationError
from backend.modules.hr.services import cache as _cache
from backend.modules.hr.services.kpi_service import (
    _BUCKET_LABELS,
    _CACHE_KEY_PREFIX_PAYROLL_RISK,
    get_payroll_risk_dashboard,
)

# ── Fixed reference date ───────────────────────────────────────────────────────

_FIXED_REF = date(2026, 6, 2)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _contract(contract_id: int, emp_id: int, date_end) -> dict:
    """Build a mock Running-contract record. date_end: ISO date string or False."""
    return {
        "id": contract_id,
        "employee_id": [emp_id, f"Employee {emp_id}"],
        "date_end": date_end,
    }


def _awc_contract(emp_id: int, state: str) -> dict:
    """Build a mock AWC-RPC contract record (any state)."""
    return {"employee_id": [emp_id, f"Employee {emp_id}"], "state": state}


def _make_client(
    contracts: list,
    emp_ids: list,
    awc_contracts=None,        # None = RPC 3 not expected (awc_emp_ids empty)
    expired_dept=None,         # None = RPC 4a not expected (expired bucket empty)
    expiring_45d_dept=None,    # None = RPC 4b not expected (expiring_45d bucket empty)
) -> MagicMock:
    """
    Build a mock OdooClient whose execute_kw side_effect list matches the RPC
    order in get_payroll_risk_dashboard:

      Response 0 : search_read(hr.contract, state='open')         → contracts
      Response 1 : search_read(hr.employee, active=True)          → employees
      Response 2 : search_read(hr.contract, emp_id in awc_ids)    → awc_contracts
                   (appended only when awc_contracts is not None)
      Response 3 : read_group expired dept                        → expired_dept
                   (appended only when expired_dept is not None)
      Response 4 : read_group expiring_45d dept                   → expiring_45d_dept
                   (appended only when expiring_45d_dept is not None)

    If a conditional RPC is not provided but the service tries to fire it,
    AsyncMock raises StopAsyncIteration, surfacing the unexpected call as a
    test failure.
    """
    responses: list = [
        contracts,
        [{"id": eid} for eid in emp_ids],
    ]
    if awc_contracts is not None:
        responses.append(awc_contracts)
    if expired_dept is not None:
        responses.append(expired_dept)
    if expiring_45d_dept is not None:
        responses.append(expiring_45d_dept)
    client = MagicMock()
    client.execute_kw = AsyncMock(side_effect=responses)
    return client


def _freeze_ref_date(monkeypatch, ref_date: date = _FIXED_REF) -> None:
    """Patch datetime.now in kpi_service to return a fixed Cairo date + UTC string.

    get_payroll_risk_dashboard calls datetime.now exactly twice per fresh run:
      1st call (inside try): datetime.now(_CAIRO_TZ).date()  → Cairo date
      2nd call (in result):  datetime.now(timezone.utc).isoformat() → UTC string
    """
    cairo_mock = MagicMock()
    cairo_mock.date.return_value = ref_date

    utc_mock = MagicMock()
    utc_mock.isoformat.return_value = f"{ref_date.isoformat()}T00:00:00+00:00"

    mock_dt = MagicMock()
    mock_dt.now.side_effect = [cairo_mock, utc_mock]

    monkeypatch.setattr("backend.modules.hr.services.kpi_service.datetime", mock_dt)


def _bucket_counts(result: dict) -> dict:
    return {b["label"]: b["count"] for b in result["buckets"]}


# ── Date-neutral happy-path data ──────────────────────────────────────────────
#
# Both employees have Running contracts → awc_emp_ids = {} → no RPC 3.
# date_end=False (open_ended) and a date far in the future (beyond_135d)
# so bucket assignment never depends on the system clock.

_NEUTRAL_CONTRACTS = [
    _contract(1, 101, False),          # open_ended
    _contract(2, 102, "2100-01-01"),   # beyond_135d — safe for decades
]
_NEUTRAL_EMP_IDS = [101, 102]          # both have Running contracts → no awc RPC


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def fresh_cache():
    _cache.clear()
    yield
    _cache.clear()


# ── Test 1 — Happy path: response shape ───────────────────────────────────────


async def test_happy_path_returns_expected_shape():
    client = _make_client(_NEUTRAL_CONTRACTS, _NEUTRAL_EMP_IDS)
    result = await get_payroll_risk_dashboard(client=client)

    assert set(result.keys()) == {
        "buckets",
        "department_breakdown_expired",
        "department_breakdown_expiring_45d",
        "archived_with_running_count",
        "active_flag_no_running_count",
        "active_flag_no_running_exit_gap",
        "active_flag_no_running_incoming",
        "active_flag_no_running_data_gap",
        "total_employed",
        "reference_date",
        "as_of",
        "cache_status",
        "rpc_duration_ms",
    }
    assert result["cache_status"] == "fresh"
    assert result["rpc_duration_ms"] >= 0
    assert isinstance(result["reference_date"], str)
    assert isinstance(result["as_of"], str)
    assert isinstance(result["buckets"], list)


# ── Test 2 — Six buckets always present in fixed order ────────────────────────


async def test_six_buckets_present_in_fixed_order():
    client = _make_client(_NEUTRAL_CONTRACTS, _NEUTRAL_EMP_IDS)
    result = await get_payroll_risk_dashboard(client=client)

    buckets = result["buckets"]
    assert len(buckets) == 6
    for i, label in enumerate(_BUCKET_LABELS):
        assert buckets[i]["label"] == label, (
            f"Bucket #{i} must be {label!r}, got {buckets[i]['label']!r}"
        )
    # active_without_contract must never appear
    labels = [b["label"] for b in buckets]
    assert "active_without_contract" not in labels


# ── Test 3 — Active employee with no Running contract goes to metadata ─────────


async def test_active_without_running_goes_to_metadata_not_bucket(monkeypatch):
    _freeze_ref_date(monkeypatch)
    # emp 202 has a Running contract; emp 201 does not → awc
    contracts = [_contract(10, 202, "2100-01-01")]
    awc_contracts = []   # emp 201 has no contracts at all → data_gap
    client = _make_client(contracts, [201, 202], awc_contracts=awc_contracts)
    result = await get_payroll_risk_dashboard(client=client)

    counts = _bucket_counts(result)
    # awc employee NOT in any bucket
    assert "active_without_contract" not in counts
    assert sum(counts.values()) == 1      # only emp 202 in buckets
    assert counts["beyond_135d"] == 1
    # awc employee surfaces as metadata
    assert result["active_flag_no_running_count"] == 1
    assert result["active_flag_no_running_data_gap"] == 1
    assert result["active_flag_no_running_exit_gap"] == 0
    assert result["active_flag_no_running_incoming"] == 0
    # total_employed counts only Running-contract employees
    assert result["total_employed"] == 1


# ── Test 4 — Bucket: date_end = yesterday is expired ─────────────────────────


async def test_bucket_date_end_yesterday_is_expired(monkeypatch):
    _freeze_ref_date(monkeypatch)
    yesterday = (_FIXED_REF - timedelta(days=1)).isoformat()   # delta = -1
    dept_rows = [{"department_id": [5, "HR"], "__count": 1}]
    contracts = [_contract(20, 301, yesterday)]
    client = _make_client(contracts, [301], expired_dept=dept_rows)
    result = await get_payroll_risk_dashboard(client=client)

    counts = _bucket_counts(result)
    assert counts["expired"] == 1
    assert counts["expiring_45d"] == 0


# ── Test 5 — Bucket: date_end = today is expiring_45d ────────────────────────


async def test_bucket_date_end_today_is_expiring_45d(monkeypatch):
    _freeze_ref_date(monkeypatch)
    today_s = _FIXED_REF.isoformat()                           # delta = 0
    dept_rows = [{"department_id": [5, "HR"], "__count": 1}]
    contracts = [_contract(30, 401, today_s)]
    client = _make_client(contracts, [401], expiring_45d_dept=dept_rows)
    result = await get_payroll_risk_dashboard(client=client)

    counts = _bucket_counts(result)
    assert counts["expiring_45d"] == 1
    assert counts["expired"] == 0


# ── Test 6 — Bucket: date_end = today+45 is expiring_45d (boundary) ──────────


async def test_bucket_date_end_today_plus_45_is_expiring_45d(monkeypatch):
    _freeze_ref_date(monkeypatch)
    plus_45 = (_FIXED_REF + timedelta(days=45)).isoformat()   # delta = 45
    dept_rows = [{"department_id": [5, "HR"], "__count": 1}]
    contracts = [_contract(31, 501, plus_45)]
    client = _make_client(contracts, [501], expiring_45d_dept=dept_rows)
    result = await get_payroll_risk_dashboard(client=client)

    counts = _bucket_counts(result)
    assert counts["expiring_45d"] == 1
    assert counts["expiring_90d"] == 0


# ── Test 7 — Bucket: date_end = today+46 is expiring_90d ─────────────────────


async def test_bucket_date_end_today_plus_46_is_expiring_90d(monkeypatch):
    _freeze_ref_date(monkeypatch)
    plus_46 = (_FIXED_REF + timedelta(days=46)).isoformat()   # delta = 46
    contracts = [_contract(40, 601, plus_46)]
    client = _make_client(contracts, [601])
    result = await get_payroll_risk_dashboard(client=client)

    counts = _bucket_counts(result)
    assert counts["expiring_90d"] == 1
    assert counts["expiring_45d"] == 0


# ── Test 8 — Bucket: date_end = today+91 is expiring_135d ────────────────────


async def test_bucket_date_end_today_plus_91_is_expiring_135d(monkeypatch):
    _freeze_ref_date(monkeypatch)
    plus_91 = (_FIXED_REF + timedelta(days=91)).isoformat()   # delta = 91
    contracts = [_contract(50, 701, plus_91)]
    client = _make_client(contracts, [701])
    result = await get_payroll_risk_dashboard(client=client)

    counts = _bucket_counts(result)
    assert counts["expiring_135d"] == 1
    assert counts["expiring_90d"] == 0


# ── Test 9 — Bucket: date_end = today+136 is beyond_135d ─────────────────────


async def test_bucket_date_end_today_plus_136_is_beyond_135d(monkeypatch):
    _freeze_ref_date(monkeypatch)
    plus_136 = (_FIXED_REF + timedelta(days=136)).isoformat() # delta = 136
    contracts = [_contract(60, 801, plus_136)]
    client = _make_client(contracts, [801])
    result = await get_payroll_risk_dashboard(client=client)

    counts = _bucket_counts(result)
    assert counts["beyond_135d"] == 1
    assert counts["expiring_135d"] == 0


# ── Test 10 — Bucket: date_end = False is open_ended ─────────────────────────


async def test_bucket_false_date_end_is_open_ended(monkeypatch):
    _freeze_ref_date(monkeypatch)
    contracts = [_contract(70, 901, False)]
    client = _make_client(contracts, [901])
    result = await get_payroll_risk_dashboard(client=client)

    counts = _bucket_counts(result)
    assert counts["open_ended"] == 1
    assert counts["expired"] == 0
    assert counts["expiring_45d"] == 0


# ── Test 11 — Archived-running employee IS bucketed and counted in metadata ───


async def test_archived_running_employee_is_bucketed_and_in_metadata(monkeypatch):
    _freeze_ref_date(monkeypatch)
    # emp 9999: NOT in active set (archived) but has Running contract → bucketed
    # emp 1001: active, has Running contract → bucketed
    # date_end=2026-06-30 at _FIXED_REF 2026-06-02 → delta=28 → expiring_45d
    contracts = [
        _contract(80, 9999, "2026-06-30"),  # archived emp → expiring_45d
        _contract(81, 1001, "2100-01-01"),  # active emp → beyond_135d
    ]
    dept_45d = [{"department_id": [5, "Sales"], "__count": 1}]
    # awc_emp_ids = {1001} - {9999, 1001} = {} → no RPC 3
    client = _make_client(contracts, [1001], expiring_45d_dept=dept_45d)
    result = await get_payroll_risk_dashboard(client=client)

    counts = _bucket_counts(result)
    # Archived emp IS bucketed (not excluded)
    assert counts["expiring_45d"] == 1        # emp 9999
    assert counts["beyond_135d"] == 1         # emp 1001
    assert sum(counts.values()) == 2          # both employed employees in buckets
    # Metadata signals
    assert result["archived_with_running_count"] == 1   # emp 9999
    assert result["active_flag_no_running_count"] == 0  # no awc employees
    # Sanity invariant
    assert result["total_employed"] == 2


# ── Test 12 — Sanity invariant: sum(6 buckets) == total_employed ──────────────


async def test_sanity_invariant_bucket_sum_equals_total_employed(monkeypatch):
    _freeze_ref_date(monkeypatch)
    dept_rows = [{"department_id": [7, "Sales"], "__count": 1}]
    contracts = [
        _contract(91, 2001, _FIXED_REF.isoformat()),   # expiring_45d (delta=0)
        _contract(92, 2002, "2100-01-01"),              # beyond_135d
        _contract(93, 2003, False),                     # open_ended
        _contract(94, 2099, "2100-01-01"),              # archived emp → beyond_135d (bucketed)
    ]
    emp_ids = [2001, 2002, 2003, 2004]    # emp 2004 has no contract → awc
    awc_contracts = []                    # emp 2004 has no contract records → data_gap
    client = _make_client(
        contracts, emp_ids,
        awc_contracts=awc_contracts,
        expiring_45d_dept=dept_rows,
    )
    result = await get_payroll_risk_dashboard(client=client)

    bucket_sum = sum(b["count"] for b in result["buckets"])
    # sum(6 buckets) == total_employed == 4 (emps 2001, 2002, 2003, 2099)
    assert bucket_sum == result["total_employed"], (
        f"sum(buckets) ({bucket_sum}) must == total_employed ({result['total_employed']})"
    )
    assert result["total_employed"] == 4
    # Metadata
    assert result["archived_with_running_count"] == 1    # emp 2099
    assert result["active_flag_no_running_count"] == 1   # emp 2004
    assert result["active_flag_no_running_data_gap"] == 1


# ── Test 13 — Department breakdown only for expired + expiring_45d ────────────


async def test_department_breakdown_returned_only_for_expired_and_expiring_45d(monkeypatch):
    _freeze_ref_date(monkeypatch)
    yesterday = (_FIXED_REF - timedelta(days=1)).isoformat()
    today_s   = _FIXED_REF.isoformat()
    contracts = [
        _contract(100, 3001, yesterday),    # expired → breakdown expected
        _contract(101, 3002, today_s),      # expiring_45d → breakdown expected
        _contract(102, 3003, "2100-01-01"), # beyond_135d → NO breakdown
    ]
    expired_dept = [{"department_id": [10, "Dept A"], "__count": 1}]
    exp45_dept   = [{"department_id": [11, "Dept B"], "__count": 1}]
    # awc_emp_ids = {} (all 3 emps have Running contracts) → no RPC 3
    client = _make_client(
        contracts, [3001, 3002, 3003],
        expired_dept=expired_dept,
        expiring_45d_dept=exp45_dept,
    )
    result = await get_payroll_risk_dashboard(client=client)

    assert len(result["department_breakdown_expired"]) == 1
    assert result["department_breakdown_expired"][0]["department_name"] == "Dept A"
    assert len(result["department_breakdown_expiring_45d"]) == 1
    assert result["department_breakdown_expiring_45d"][0]["department_name"] == "Dept B"
    assert _bucket_counts(result)["beyond_135d"] == 1


# ── Test 14 — No extra RPC when actionable buckets are empty ─────────────────


async def test_department_breakdown_empty_and_no_extra_rpc_when_actionable_buckets_empty(monkeypatch):
    _freeze_ref_date(monkeypatch)
    contracts = [
        _contract(110, 4001, "2100-01-01"),  # beyond_135d
        _contract(111, 4002, False),          # open_ended
    ]
    # awc_emp_ids = {} (both emps have contracts) → no RPC 3
    # expired=0, expiring_45d=0 → no RPC 4a, 4b
    client = _make_client(contracts, [4001, 4002])   # only 2 responses provided
    result = await get_payroll_risk_dashboard(client=client)

    assert client.execute_kw.call_count == 2, (
        "Only 2 RPCs expected when awc, expired, and expiring_45d are all empty; "
        f"got {client.execute_kw.call_count}"
    )
    assert result["department_breakdown_expired"] == []
    assert result["department_breakdown_expiring_45d"] == []


# ── Test 15 — Cache hit: second call does not fire any RPC ───────────────────


async def test_cache_hit_second_call_execute_kw_count_unchanged():
    client = _make_client(_NEUTRAL_CONTRACTS, _NEUTRAL_EMP_IDS)
    result1 = await get_payroll_risk_dashboard(client=client)
    calls_after_first = client.execute_kw.call_count

    result2 = await get_payroll_risk_dashboard(client=client)

    assert client.execute_kw.call_count == calls_after_first, (
        "Second call must be served from cache — execute_kw must not be called again"
    )
    assert result1["cache_status"] == "fresh"
    assert result2["cache_status"] == "cached"
    assert result2["rpc_duration_ms"] == 0
    assert result2["total_employed"] == result1["total_employed"]
    assert result2["buckets"] == result1["buckets"]


# ── Test 16 — Read-only guard fires before any RPC ───────────────────────────


async def test_read_only_guard_raises_before_any_rpc(monkeypatch):
    monkeypatch.setattr(
        "backend.modules.hr.services.kpi_service.ALLOWED_METHODS",
        frozenset({"read_group", "write"}),
    )
    client = MagicMock()
    client.execute_kw = AsyncMock()

    with pytest.raises(ReadOnlyViolationError):
        await get_payroll_risk_dashboard(client=client)

    client.execute_kw.assert_not_called()


# ── Test 17 — RPC failure raises OdooQueryError ───────────────────────────────


async def test_rpc_failure_raises_odoo_query_error():
    client = MagicMock()
    client.execute_kw = AsyncMock(side_effect=RuntimeError("connection refused"))

    with pytest.raises(OdooQueryError):
        await get_payroll_risk_dashboard(client=client)


# ── Test 18 — RPC failure writes no cache entry ──────────────────────────────


async def test_rpc_failure_writes_no_cache_entry(monkeypatch):
    _freeze_ref_date(monkeypatch)
    client = MagicMock()
    client.execute_kw = AsyncMock(side_effect=RuntimeError("timeout"))

    with pytest.raises(OdooQueryError):
        await get_payroll_risk_dashboard(client=client)

    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_PAYROLL_RISK)
    assert _cache.get(cache_key) is None, "A failed RPC must not write a cache entry"


# ── Test 19 — Cairo TZ used for reference_date ───────────────────────────────


async def test_cairo_tz_used_for_reference_date(monkeypatch):
    fixed_cairo = date(2026, 1, 15)
    _freeze_ref_date(monkeypatch, ref_date=fixed_cairo)
    # empty data; awc_emp_ids = {} → no RPC 3 or dept breakdown RPCs
    client = _make_client([], [])

    result = await get_payroll_risk_dashboard(client=client)

    assert result["reference_date"] == "2026-01-15", (
        f"reference_date must be Cairo TZ date 2026-01-15, got {result['reference_date']!r}"
    )


# ── Test 20 — Mixed realistic dataset partitions correctly ────────────────────


async def test_mixed_realistic_dataset_partitions_correctly(monkeypatch):
    """
    All 6 buckets populated simultaneously, plus archived-running employees
    and active-without-running metadata.

    Active emp IDs: 1001-1013 (13 total).
    awc: emps 1001, 1002 — no Running contract (data_gap=2, exit_gap=0).
    Archived+Running: 9001 (expired), 9002 (expiring_45d), 9003 (open_ended).

    Expected bucket counts (archived-running bucketed alongside active):
      expired      : 1003, 1004, 9001 = 3
      expiring_45d : 1005, 1006, 9002 = 3
      expiring_90d : 1007, 1008       = 2
      expiring_135d: 1009, 1010       = 2
      beyond_135d  : 1011, 1012       = 2
      open_ended   : 1013, 9003       = 2
      total_employed = 14
    """
    _freeze_ref_date(monkeypatch)
    ref = _FIXED_REF
    active_emp_ids = list(range(1001, 1014))   # 13 active employees

    contracts = [
        # expired (delta < 0) — active emps
        _contract(201, 1003, (ref - timedelta(days=1)).isoformat()),
        _contract(202, 1004, (ref - timedelta(days=30)).isoformat()),
        # expiring_45d (0 <= delta <= 45) — active emps
        _contract(203, 1005, ref.isoformat()),
        _contract(204, 1006, (ref + timedelta(days=45)).isoformat()),
        # expiring_90d (46 <= delta <= 90) — active emps
        _contract(205, 1007, (ref + timedelta(days=46)).isoformat()),
        _contract(206, 1008, (ref + timedelta(days=90)).isoformat()),
        # expiring_135d (91 <= delta <= 135) — active emps
        _contract(207, 1009, (ref + timedelta(days=91)).isoformat()),
        _contract(208, 1010, (ref + timedelta(days=135)).isoformat()),
        # beyond_135d (delta >= 136) — active emps
        _contract(209, 1011, (ref + timedelta(days=136)).isoformat()),
        _contract(210, 1012, (ref + timedelta(days=200)).isoformat()),
        # open_ended — active emp
        _contract(211, 1013, False),
        # Archived+Running: NOT in active set → bucketed, counted in archived_with_running
        _contract(301, 9001, (ref - timedelta(days=5)).isoformat()),   # expired
        _contract(302, 9002, ref.isoformat()),                          # expiring_45d (delta=0)
        _contract(303, 9003, False),                                    # open_ended
    ]

    # awc: emps 1001, 1002 have no contracts at all → data_gap=2
    awc_contracts = []

    # Dept breakdowns cover 3 contracts each (active + archived-running)
    expired_dept = [{"department_id": [20, "Sales"],   "__count": 3}]
    exp45_dept   = [{"department_id": [21, "Finance"], "__count": 3}]

    client = _make_client(
        contracts, active_emp_ids,
        awc_contracts=awc_contracts,
        expired_dept=expired_dept,
        expiring_45d_dept=exp45_dept,
    )
    result = await get_payroll_risk_dashboard(client=client)

    counts = _bucket_counts(result)

    # Per-bucket counts (archived-running bucketed alongside active employees)
    assert counts["expired"]       == 3, f"got {counts['expired']}"
    assert counts["expiring_45d"]  == 3, f"got {counts['expiring_45d']}"
    assert counts["expiring_90d"]  == 2, f"got {counts['expiring_90d']}"
    assert counts["expiring_135d"] == 2, f"got {counts['expiring_135d']}"
    assert counts["beyond_135d"]   == 2, f"got {counts['beyond_135d']}"
    assert counts["open_ended"]    == 2, f"got {counts['open_ended']}"
    assert "active_without_contract" not in counts

    # Archived-running metadata
    assert result["archived_with_running_count"] == 3   # 9001, 9002, 9003

    # awc metadata
    assert result["active_flag_no_running_count"]    == 2   # 1001, 1002
    assert result["active_flag_no_running_data_gap"] == 2
    assert result["active_flag_no_running_exit_gap"] == 0
    assert result["active_flag_no_running_incoming"] == 0

    # Sanity invariant: sum(6 buckets) == total_employed
    bucket_sum = sum(b["count"] for b in result["buckets"])
    assert bucket_sum == result["total_employed"] == 14, (
        f"sum(buckets)={bucket_sum}, total_employed={result['total_employed']}, expected 14"
    )

    # Department breakdowns
    assert len(result["department_breakdown_expired"]) == 1
    assert result["department_breakdown_expired"][0]["department_name"] == "Sales"
    assert len(result["department_breakdown_expiring_45d"]) == 1
    assert result["department_breakdown_expiring_45d"][0]["department_name"] == "Finance"


# ── Test 21 — AWC exit_gap classification ────────────────────────────────────


async def test_awc_exit_gap_classification(monkeypatch):
    """Active emp with only close/cancel contracts → exit_gap metadata."""
    _freeze_ref_date(monkeypatch)
    contracts = [_contract(10, 202, "2100-01-01")]   # emp 202 employed
    # emp 201: active, has a close contract → exit_gap
    awc_contracts = [_awc_contract(201, "close")]
    client = _make_client(contracts, [201, 202], awc_contracts=awc_contracts)
    result = await get_payroll_risk_dashboard(client=client)

    assert result["active_flag_no_running_count"]    == 1
    assert result["active_flag_no_running_exit_gap"] == 1
    assert result["active_flag_no_running_data_gap"] == 0
    assert result["active_flag_no_running_incoming"] == 0
    assert result["total_employed"] == 1


# ── Test 22 — AWC incoming classification ────────────────────────────────────


async def test_awc_incoming_classification(monkeypatch):
    """Active emp with draft contract → incoming metadata (NOT exit_gap)."""
    _freeze_ref_date(monkeypatch)
    contracts = [_contract(10, 202, "2100-01-01")]   # emp 202 employed
    # emp 201: active, has a draft contract → incoming (not yet started)
    awc_contracts = [_awc_contract(201, "draft")]
    client = _make_client(contracts, [201, 202], awc_contracts=awc_contracts)
    result = await get_payroll_risk_dashboard(client=client)

    assert result["active_flag_no_running_count"]    == 1
    assert result["active_flag_no_running_incoming"] == 1
    assert result["active_flag_no_running_exit_gap"] == 0
    assert result["active_flag_no_running_data_gap"] == 0
    assert result["total_employed"] == 1


# ── Test 23 — AWC three-way split in a single dataset ────────────────────────


async def test_awc_three_way_split(monkeypatch):
    """exit_gap + incoming + data_gap each classified correctly in one dataset."""
    _freeze_ref_date(monkeypatch)
    contracts = [_contract(10, 300, "2100-01-01")]   # emp 300 employed
    # emp 201: close contract → exit_gap
    # emp 202: draft contract → incoming
    # emp 203: no contracts at all → data_gap
    awc_contracts = [
        _awc_contract(201, "close"),
        _awc_contract(202, "draft"),
    ]
    client = _make_client(
        contracts, [201, 202, 203, 300], awc_contracts=awc_contracts
    )
    result = await get_payroll_risk_dashboard(client=client)

    assert result["active_flag_no_running_count"]    == 3
    assert result["active_flag_no_running_exit_gap"] == 1
    assert result["active_flag_no_running_incoming"] == 1
    assert result["active_flag_no_running_data_gap"] == 1
    assert result["total_employed"] == 1   # only emp 300 is employed
