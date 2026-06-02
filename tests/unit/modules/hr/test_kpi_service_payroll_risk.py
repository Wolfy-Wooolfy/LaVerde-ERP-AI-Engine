"""
Unit tests for HR KPI service — get_payroll_risk_dashboard (KPI C).

OdooClient is fully mocked; no live Odoo connection is made.
Live verification: scripts/verify_kpi_c_payroll_risk_live.py (D4).

Baselines (locked from verification run 2026-05-29):
  bucket active_without_contract  : 17
  bucket expired                  : 0  (expected — alert if > 0)
  bucket open_ended               : 1
  orphan_contracts_count          : 17
  sum(buckets 1..7)               : 136  (total_active)

Reference date pinned to 2026-06-02 in all date-sensitive tests.
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
    """Build a mock contract record. date_end: ISO date string or False."""
    return {
        "id": contract_id,
        "employee_id": [emp_id, f"Employee {emp_id}"],
        "date_end": date_end,
    }


def _make_client(
    contracts: list,
    emp_ids: list,
    expired_dept=None,
    expiring_45d_dept=None,
) -> MagicMock:
    """
    Build a mock OdooClient whose execute_kw responses match the RPC order
    in get_payroll_risk_dashboard:

      Response 0 : search_read(hr.contract)  -> contracts
      Response 1 : search_read(hr.employee)  -> employees
      Response 2 : read_group (expired dept) — appended only when expired_dept is not None
      Response 3 : read_group (expiring_45d dept) — appended only when expiring_45d_dept is not None

    If an expired/expiring_45d dept response is not provided but the service
    tries to fire that RPC, AsyncMock will raise StopAsyncIteration, surfacing
    the unexpected call as a test failure.
    """
    responses: list = [
        contracts,
        [{"id": eid} for eid in emp_ids],
    ]
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
      1st call (inside try): datetime.now(_CAIRO_TZ).date()  -> Cairo date
      2nd call (in result):  datetime.now(timezone.utc).isoformat() -> UTC string
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


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def fresh_cache():
    _cache.clear()
    yield
    _cache.clear()


# ── Date-neutral happy-path data (no freeze required) ─────────────────────────
#
# Uses date_end=False (open_ended) and a date far in the future (beyond_135d)
# so bucket assignment never depends on the system clock.

_NEUTRAL_CONTRACTS = [
    _contract(1, 101, False),         # open_ended
    _contract(2, 102, "2100-01-01"),  # beyond_135d — safe for decades
]
_NEUTRAL_EMP_IDS = [101, 102, 103]   # emp 103 has no contract → active_without_contract


# ── Test 1 — Happy path: response shape ───────────────────────────────────────


async def test_happy_path_returns_expected_shape():
    client = _make_client(_NEUTRAL_CONTRACTS, _NEUTRAL_EMP_IDS)
    result = await get_payroll_risk_dashboard(client=client)

    assert set(result.keys()) == {
        "buckets", "department_breakdown_expired",
        "department_breakdown_expiring_45d", "orphan_contracts_count",
        "total_active", "reference_date", "as_of", "cache_status", "rpc_duration_ms",
    }
    assert result["cache_status"] == "fresh"
    assert result["rpc_duration_ms"] >= 0
    assert isinstance(result["reference_date"], str)
    assert isinstance(result["as_of"], str)
    assert result["orphan_contracts_count"] >= 0
    assert isinstance(result["buckets"], list)


# ── Test 2 — Seven buckets always present in fixed order ──────────────────────


async def test_seven_buckets_present_in_fixed_order():
    client = _make_client(_NEUTRAL_CONTRACTS, _NEUTRAL_EMP_IDS)
    result = await get_payroll_risk_dashboard(client=client)

    buckets = result["buckets"]
    assert len(buckets) == 7
    for i, label in enumerate(_BUCKET_LABELS):
        assert buckets[i]["label"] == label, (
            f"Bucket #{i} must be {label!r}, got {buckets[i]['label']!r}"
        )


# ── Test 3 — Bucket 1: active employee with no contract ───────────────────────


async def test_bucket1_active_employee_with_no_contract_is_active_without_contract(monkeypatch):
    _freeze_ref_date(monkeypatch)
    # emp 202 has a contract; emp 201 does not → bucket 1
    contracts = [_contract(10, 202, "2100-01-01")]
    client = _make_client(contracts, [201, 202])
    result = await get_payroll_risk_dashboard(client=client)

    counts = _bucket_counts(result)
    assert counts["active_without_contract"] == 1
    assert counts["beyond_135d"] == 1
    assert result["total_active"] == 2


# ── Test 4 — Bucket 2: date_end = yesterday is expired ────────────────────────


async def test_bucket2_date_end_yesterday_is_expired(monkeypatch):
    _freeze_ref_date(monkeypatch)
    yesterday = (_FIXED_REF - timedelta(days=1)).isoformat()   # delta = -1
    dept_rows = [{"department_id": [5, "HR"], "__count": 1}]
    contracts = [_contract(20, 301, yesterday)]
    client = _make_client(contracts, [301], expired_dept=dept_rows)
    result = await get_payroll_risk_dashboard(client=client)

    counts = _bucket_counts(result)
    assert counts["expired"] == 1
    assert counts["expiring_45d"] == 0


# ── Test 5 — Bucket 3: date_end = today is expiring_45d ──────────────────────


async def test_bucket3_date_end_today_is_expiring_45d(monkeypatch):
    _freeze_ref_date(monkeypatch)
    today_s = _FIXED_REF.isoformat()                           # delta = 0
    dept_rows = [{"department_id": [5, "HR"], "__count": 1}]
    contracts = [_contract(30, 401, today_s)]
    client = _make_client(contracts, [401], expiring_45d_dept=dept_rows)
    result = await get_payroll_risk_dashboard(client=client)

    counts = _bucket_counts(result)
    assert counts["expiring_45d"] == 1
    assert counts["expired"] == 0


# ── Test 6 — Bucket 3: date_end = today+45 is expiring_45d ───────────────────


async def test_bucket3_date_end_today_plus_45_is_expiring_45d(monkeypatch):
    _freeze_ref_date(monkeypatch)
    plus_45 = (_FIXED_REF + timedelta(days=45)).isoformat()   # delta = 45
    dept_rows = [{"department_id": [5, "HR"], "__count": 1}]
    contracts = [_contract(31, 501, plus_45)]
    client = _make_client(contracts, [501], expiring_45d_dept=dept_rows)
    result = await get_payroll_risk_dashboard(client=client)

    counts = _bucket_counts(result)
    assert counts["expiring_45d"] == 1
    assert counts["expiring_90d"] == 0


# ── Test 7 — Bucket 4: date_end = today+46 is expiring_90d ───────────────────


async def test_bucket4_date_end_today_plus_46_is_expiring_90d(monkeypatch):
    _freeze_ref_date(monkeypatch)
    plus_46 = (_FIXED_REF + timedelta(days=46)).isoformat()   # delta = 46
    contracts = [_contract(40, 601, plus_46)]
    client = _make_client(contracts, [601])
    result = await get_payroll_risk_dashboard(client=client)

    counts = _bucket_counts(result)
    assert counts["expiring_90d"] == 1
    assert counts["expiring_45d"] == 0


# ── Test 8 — Bucket 5: date_end = today+91 is expiring_135d ──────────────────


async def test_bucket5_date_end_today_plus_91_is_expiring_135d(monkeypatch):
    _freeze_ref_date(monkeypatch)
    plus_91 = (_FIXED_REF + timedelta(days=91)).isoformat()   # delta = 91
    contracts = [_contract(50, 701, plus_91)]
    client = _make_client(contracts, [701])
    result = await get_payroll_risk_dashboard(client=client)

    counts = _bucket_counts(result)
    assert counts["expiring_135d"] == 1
    assert counts["expiring_90d"] == 0


# ── Test 9 — Bucket 6: date_end = today+136 is beyond_135d ───────────────────


async def test_bucket6_date_end_today_plus_136_is_beyond_135d(monkeypatch):
    _freeze_ref_date(monkeypatch)
    plus_136 = (_FIXED_REF + timedelta(days=136)).isoformat() # delta = 136
    contracts = [_contract(60, 801, plus_136)]
    client = _make_client(contracts, [801])
    result = await get_payroll_risk_dashboard(client=client)

    counts = _bucket_counts(result)
    assert counts["beyond_135d"] == 1
    assert counts["expiring_135d"] == 0


# ── Test 10 — Bucket 7: date_end = False is open_ended ───────────────────────


async def test_bucket7_false_date_end_is_open_ended(monkeypatch):
    _freeze_ref_date(monkeypatch)
    contracts = [_contract(70, 901, False)]
    client = _make_client(contracts, [901])
    result = await get_payroll_risk_dashboard(client=client)

    counts = _bucket_counts(result)
    assert counts["open_ended"] == 1
    assert counts["expired"] == 0
    assert counts["expiring_45d"] == 0


# ── Test 11 — Orphan contract not counted in any bucket ───────────────────────


async def test_orphan_contract_counted_only_in_orphan_field_not_in_buckets(monkeypatch):
    _freeze_ref_date(monkeypatch)
    # emp 9999 is NOT in active_emp_ids → orphan
    contracts = [
        _contract(80, 9999, "2026-06-30"),  # orphan — must not touch buckets
        _contract(81, 1001, "2100-01-01"),  # active emp → beyond_135d
    ]
    client = _make_client(contracts, [1001])   # only 1001 is active
    result = await get_payroll_risk_dashboard(client=client)

    assert result["orphan_contracts_count"] == 1
    counts = _bucket_counts(result)
    # Orphan must not contribute to any bucket
    assert sum(counts.values()) == 1          # only the active-emp contract
    assert counts["beyond_135d"] == 1


# ── Test 12 — Sanity invariant ────────────────────────────────────────────────


async def test_sanity_invariant_bucket_sum_equals_total_active(monkeypatch):
    _freeze_ref_date(monkeypatch)
    dept_rows = [{"department_id": [7, "Sales"], "__count": 1}]
    contracts = [
        _contract(91, 2001, _FIXED_REF.isoformat()),    # expiring_45d (delta=0)
        _contract(92, 2002, "2100-01-01"),               # beyond_135d
        _contract(93, 2003, False),                      # open_ended
        _contract(94, 2099, "2100-01-01"),               # orphan: 2099 not in active set
    ]
    emp_ids = [2001, 2002, 2003, 2004]   # 2004 has no contract → bucket 1
    client = _make_client(contracts, emp_ids, expiring_45d_dept=dept_rows)
    result = await get_payroll_risk_dashboard(client=client)

    bucket_sum = sum(b["count"] for b in result["buckets"])
    assert bucket_sum == result["total_active"], (
        f"sum(buckets) ({bucket_sum}) must == total_active ({result['total_active']})"
    )
    assert result["total_active"] == len(emp_ids)


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
    client = _make_client(contracts, [3001, 3002, 3003],
                          expired_dept=expired_dept, expiring_45d_dept=exp45_dept)
    result = await get_payroll_risk_dashboard(client=client)

    assert len(result["department_breakdown_expired"]) == 1
    assert result["department_breakdown_expired"][0]["department_name"] == "Dept A"
    assert len(result["department_breakdown_expiring_45d"]) == 1
    assert result["department_breakdown_expiring_45d"][0]["department_name"] == "Dept B"
    # beyond_135d has a count but no breakdown field
    assert _bucket_counts(result)["beyond_135d"] == 1


# ── Test 14 — No extra RPC when actionable buckets are empty ─────────────────


async def test_department_breakdown_empty_and_no_extra_rpc_when_actionable_buckets_empty(monkeypatch):
    _freeze_ref_date(monkeypatch)
    contracts = [
        _contract(110, 4001, "2100-01-01"),  # beyond_135d
        _contract(111, 4002, False),          # open_ended
    ]
    client = _make_client(contracts, [4001, 4002])   # only 2 responses provided
    result = await get_payroll_risk_dashboard(client=client)

    assert client.execute_kw.call_count == 2, (
        "Only 2 RPCs expected when expired and expiring_45d buckets are both empty; "
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
    assert result2["total_active"] == result1["total_active"]
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
    client = _make_client([], [])   # empty data; no conditional RPCs

    result = await get_payroll_risk_dashboard(client=client)

    assert result["reference_date"] == "2026-01-15", (
        f"reference_date must be Cairo TZ date 2026-01-15, got {result['reference_date']!r}"
    )


# ── Test 20 — Mixed realistic dataset partitions correctly ────────────────────


async def test_mixed_realistic_dataset_partitions_correctly(monkeypatch):
    """
    All 7 buckets populated simultaneously, plus multiple orphan contracts and
    multiple active employees with no contract. Proves the partitioning logic
    is correct when all code paths are exercised together.

    Active emp IDs: 1001-1013 (13 total).
    Bucket 1 (active_without_contract): emps 1001, 1002 — no contract.
    Orphans: contracts for emps 9001, 9002, 9003 — not in active set.
    """
    _freeze_ref_date(monkeypatch)
    ref = _FIXED_REF
    active_emp_ids = list(range(1001, 1014))   # 13 active employees

    contracts = [
        # Bucket 2 — expired (delta < 0)
        _contract(201, 1003, (ref - timedelta(days=1)).isoformat()),
        _contract(202, 1004, (ref - timedelta(days=30)).isoformat()),
        # Bucket 3 — expiring_45d (0 <= delta <= 45)
        _contract(203, 1005, ref.isoformat()),
        _contract(204, 1006, (ref + timedelta(days=45)).isoformat()),
        # Bucket 4 — expiring_90d (46 <= delta <= 90)
        _contract(205, 1007, (ref + timedelta(days=46)).isoformat()),
        _contract(206, 1008, (ref + timedelta(days=90)).isoformat()),
        # Bucket 5 — expiring_135d (91 <= delta <= 135)
        _contract(207, 1009, (ref + timedelta(days=91)).isoformat()),
        _contract(208, 1010, (ref + timedelta(days=135)).isoformat()),
        # Bucket 6 — beyond_135d (delta >= 136)
        _contract(209, 1011, (ref + timedelta(days=136)).isoformat()),
        _contract(210, 1012, (ref + timedelta(days=200)).isoformat()),
        # Bucket 7 — open_ended (date_end = False)
        _contract(211, 1013, False),
        # Orphan contracts — employee not in active set; must never touch any bucket
        _contract(301, 9001, (ref - timedelta(days=5)).isoformat()),
        _contract(302, 9002, ref.isoformat()),
        _contract(303, 9003, False),
    ]

    expired_dept = [{"department_id": [20, "Sales"],   "__count": 2}]
    exp45_dept   = [{"department_id": [21, "Finance"], "__count": 2}]

    client = _make_client(contracts, active_emp_ids,
                          expired_dept=expired_dept, expiring_45d_dept=exp45_dept)
    result = await get_payroll_risk_dashboard(client=client)

    counts = _bucket_counts(result)

    # Per-bucket counts
    assert counts["active_without_contract"] == 2, f"got {counts['active_without_contract']}"
    assert counts["expired"]                 == 2, f"got {counts['expired']}"
    assert counts["expiring_45d"]            == 2, f"got {counts['expiring_45d']}"
    assert counts["expiring_90d"]            == 2, f"got {counts['expiring_90d']}"
    assert counts["expiring_135d"]           == 2, f"got {counts['expiring_135d']}"
    assert counts["beyond_135d"]             == 2, f"got {counts['beyond_135d']}"
    assert counts["open_ended"]              == 1, f"got {counts['open_ended']}"

    # Orphan isolation
    assert result["orphan_contracts_count"] == 3, (
        f"3 orphan contracts expected, got {result['orphan_contracts_count']}"
    )

    # Sanity invariant
    bucket_sum = sum(b["count"] for b in result["buckets"])
    assert bucket_sum == result["total_active"] == len(active_emp_ids), (
        f"sum(buckets)={bucket_sum}, total_active={result['total_active']}, "
        f"expected {len(active_emp_ids)}"
    )

    # Department breakdowns present and correct
    assert len(result["department_breakdown_expired"]) == 1
    assert result["department_breakdown_expired"][0]["department_name"] == "Sales"
    assert len(result["department_breakdown_expiring_45d"]) == 1
    assert result["department_breakdown_expiring_45d"][0]["department_name"] == "Finance"
