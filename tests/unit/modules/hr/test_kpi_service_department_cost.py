"""
Unit tests for HR KPI service — get_department_cost (KPI D).

Employment definition: Running contract (state='open'). See §3.6.
Wage source: hr.contract.wage (monthly EGP). See §3.8 W1-W4.

k-anonymity rule: departments with distinct employee count < 3 are pooled
into "Other (small departments)". If the pool itself < 3, total_wage is
null (suppressed); grand_total_wage is always returned.

OdooClient is fully mocked; no live Odoo connection is made.
Live verification: scripts/verify_kpi_d_department_cost_live.py (D4).

RPC order in get_department_cost():
  RPC 1 (only): search_read(hr.contract, state='open',
                            fields=['department_id','wage','employee_id'])
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.core.exceptions import OdooQueryError, ReadOnlyViolationError
from backend.modules.hr.services import cache as _cache
from backend.modules.hr.services.kpi_service import (
    _CACHE_KEY_PREFIX_DEPT_COST,
    _OTHER_DEPT_LABEL,
    get_department_cost,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _contract(
    contract_id: int,
    emp_id: int,
    dept_id: int | None,
    dept_name: str,
    wage: float,
) -> dict:
    """Build a mock Running-contract record."""
    return {
        "id": contract_id,
        "employee_id": [emp_id, f"Employee {emp_id}"],
        "department_id": [dept_id, dept_name] if dept_id is not None else False,
        "wage": wage,
    }


def _make_client(contracts: list) -> MagicMock:
    """Build a mock OdooClient returning contracts on the single execute_kw call."""
    client = MagicMock()
    client.execute_kw = AsyncMock(return_value=contracts)
    return client


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def fresh_cache():
    _cache.clear()
    yield
    _cache.clear()


# ── Test 1 — Happy path: response shape ──────────────────────────────────────


async def test_happy_path_returns_expected_shape():
    contracts = [
        _contract(1, 101, 5, "Finance", 10000.0),
        _contract(2, 102, 5, "Finance", 12000.0),
        _contract(3, 103, 5, "Finance", 11000.0),
        _contract(4, 104, 6, "Sales",    9000.0),
        _contract(5, 105, 6, "Sales",    8500.0),
        _contract(6, 106, 6, "Sales",    9500.0),
    ]
    client = _make_client(contracts)
    result = await get_department_cost(client=client)

    assert set(result.keys()) == {
        "rows", "grand_total_wage", "total_running_contracts",
        "currency", "basis", "reference_date", "as_of",
        "cache_status", "rpc_duration_ms",
    }
    assert result["currency"] == "EGP"
    assert result["basis"] == "monthly"
    assert result["cache_status"] == "fresh"
    assert result["rpc_duration_ms"] >= 0
    assert isinstance(result["grand_total_wage"], float)
    assert isinstance(result["rows"], list)
    assert isinstance(result["reference_date"], str)
    assert isinstance(result["as_of"], str)
    for row in result["rows"]:
        assert set(row.keys()) == {
            "department_id", "department_name",
            "running_contract_count", "total_wage",
        }


# ── Test 2 — All depts above threshold: no Other row ─────────────────────────


async def test_all_depts_above_threshold_no_other_row():
    contracts = [
        _contract(1, 101, 5, "Finance", 10000.0),
        _contract(2, 102, 5, "Finance", 12000.0),
        _contract(3, 103, 5, "Finance", 11000.0),
        _contract(4, 104, 6, "Sales",    9000.0),
        _contract(5, 105, 6, "Sales",    8500.0),
        _contract(6, 106, 6, "Sales",    9500.0),
    ]
    client = _make_client(contracts)
    result = await get_department_cost(client=client)

    names = {r["department_name"] for r in result["rows"]}
    assert _OTHER_DEPT_LABEL not in names
    assert len(result["rows"]) == 2
    assert result["total_running_contracts"] == 6

    finance = next(r for r in result["rows"] if r["department_name"] == "Finance")
    assert finance["running_contract_count"] == 3
    assert finance["total_wage"] == 33000.0

    sales = next(r for r in result["rows"] if r["department_name"] == "Sales")
    assert sales["running_contract_count"] == 3
    assert sales["total_wage"] == 27000.0


# ── Test 3 — Small depts pooled into Other; pool count >= 3 → wage returned ──


async def test_small_dept_below_threshold_pooled_into_other():
    """
    HR (2 employees) and Fleet (2 employees) are each below k=3.
    Combined pool = 4 >= 3 → Other row with total_wage returned (not suppressed).
    """
    contracts = [
        # Finance: 3 employees → normal row
        _contract(1, 101, 5, "Finance", 10000.0),
        _contract(2, 102, 5, "Finance", 12000.0),
        _contract(3, 103, 5, "Finance", 11000.0),
        # HR: 2 employees → pooled
        _contract(4, 201, 7, "HR",    8000.0),
        _contract(5, 202, 7, "HR",    7500.0),
        # Fleet: 2 employees → pooled
        _contract(6, 301, 8, "Fleet", 6000.0),
        _contract(7, 302, 8, "Fleet", 5500.0),
    ]
    client = _make_client(contracts)
    result = await get_department_cost(client=client)

    names = {r["department_name"] for r in result["rows"]}
    assert "Finance" in names
    assert "HR" not in names
    assert "Fleet" not in names
    assert _OTHER_DEPT_LABEL in names

    other = next(r for r in result["rows"] if r["department_name"] == _OTHER_DEPT_LABEL)
    assert other["department_id"] is None
    assert other["running_contract_count"] == 4   # 2+2; pool >= 3 → wage returned
    assert other["total_wage"] == 27000.0          # 8000+7500+6000+5500

    assert result["total_running_contracts"] == 7


# ── Test 4 — wage=0 contributor: counted as employee, adds 0 to SUM ──────────


async def test_wage_zero_contributor_included_in_count_and_zero_in_sum():
    """§3.8 W1: the one wage=0 contract is in-progress data entry, not an error.
    It must be counted in running_contract_count and contribute 0 to total_wage."""
    contracts = [
        _contract(1, 101, 5, "Finance", 10000.0),
        _contract(2, 102, 5, "Finance", 12000.0),
        _contract(3, 103, 5, "Finance",     0.0),  # wage=0; no special handling
    ]
    client = _make_client(contracts)
    result = await get_department_cost(client=client)

    assert result["total_running_contracts"] == 3

    finance = next(r for r in result["rows"] if r["department_name"] == "Finance")
    assert finance["running_contract_count"] == 3    # emp 103 counted
    assert finance["total_wage"] == 22000.0           # 10000+12000+0
    assert result["grand_total_wage"] == 22000.0


# ── Test 5 — Population reconciles; distinct employee counting ────────────────


async def test_population_reconciles_sum_rows_to_grand_total():
    """
    sum(row.running_contract_count) == total_running_contracts.
    grand_total_wage == SUM over all contracts (both contracts for emp 999 counted).
    Employee 999 has 2 open contracts in Sales; counted once in running_contract_count.
    """
    contracts = [
        # Finance: 3 distinct employees
        _contract(1, 101, 5, "Finance", 10000.0),
        _contract(2, 102, 5, "Finance", 12000.0),
        _contract(3, 103, 5, "Finance", 11000.0),
        # Sales: emp 999 has 2 contracts → distinct count = 3, wage summed over both
        _contract(4, 999, 6, "Sales",   9000.0),
        _contract(5, 999, 6, "Sales",   8000.0),  # same emp_id; counted once
        _contract(6, 104, 6, "Sales",   8500.0),
        _contract(7, 105, 6, "Sales",   9500.0),
    ]
    client = _make_client(contracts)
    result = await get_department_cost(client=client)

    # Distinct employees: {101,102,103,999,104,105} = 6 (not 7 raw contracts)
    assert result["total_running_contracts"] == 6

    sales = next(r for r in result["rows"] if r["department_name"] == "Sales")
    assert sales["running_contract_count"] == 3   # {999,104,105} — emp 999 once

    # sum of all row counts == total_running_contracts
    row_count_sum = sum(r["running_contract_count"] for r in result["rows"])
    assert row_count_sum == result["total_running_contracts"]

    # grand_total_wage = SUM over all 7 contracts (both emp-999 contracts counted)
    expected_grand = 10000.0 + 12000.0 + 11000.0 + 9000.0 + 8000.0 + 8500.0 + 9500.0
    assert result["grand_total_wage"] == expected_grand  # 68000.0


# ── Test 6 — Other pool < 3: total_wage suppressed (null) ────────────────────


async def test_other_pool_below_threshold_suppresses_total_wage():
    """HR has 2 employees → pool count = 2 < k=3 → Other row exists but total_wage=null."""
    contracts = [
        # Finance: 3 employees → normal row
        _contract(1, 101, 5, "Finance", 10000.0),
        _contract(2, 102, 5, "Finance", 12000.0),
        _contract(3, 103, 5, "Finance", 11000.0),
        # HR: 2 employees → pool count=2 < 3 → suppress
        _contract(4, 201, 7, "HR", 8000.0),
        _contract(5, 202, 7, "HR", 7500.0),
    ]
    client = _make_client(contracts)
    result = await get_department_cost(client=client)

    other = next(
        (r for r in result["rows"] if r["department_name"] == _OTHER_DEPT_LABEL),
        None,
    )
    assert other is not None, "Other row must be present even when suppressed"
    assert other["running_contract_count"] == 2
    assert other["total_wage"] is None   # suppressed: pool count 2 < k=3


# ── Test 7 — grand_total_wage always float, even when Other suppressed ────────


async def test_grand_total_always_float_even_when_other_suppressed():
    """grand_total_wage is always a float regardless of k-anon suppression."""
    contracts = [
        _contract(1, 101, 5, "Finance", 10000.0),
        _contract(2, 102, 5, "Finance", 12000.0),
        _contract(3, 103, 5, "Finance", 11000.0),
        _contract(4, 201, 7, "HR",       8000.0),  # pool=1 < 3 → suppress Other
    ]
    client = _make_client(contracts)
    result = await get_department_cost(client=client)

    assert isinstance(result["grand_total_wage"], float)
    assert result["grand_total_wage"] == 41000.0   # 10000+12000+11000+8000

    other = next(r for r in result["rows"] if r["department_name"] == _OTHER_DEPT_LABEL)
    assert other["total_wage"] is None              # suppressed
    assert isinstance(result["grand_total_wage"], float)  # still float


# ── Test 8 — Cache hit: second call fires no RPC ─────────────────────────────


async def test_cache_hit_second_call_no_additional_rpc():
    contracts = [
        _contract(1, 101, 5, "Finance", 10000.0),
        _contract(2, 102, 5, "Finance", 12000.0),
        _contract(3, 103, 5, "Finance", 11000.0),
    ]
    client = _make_client(contracts)
    result1 = await get_department_cost(client=client)
    calls_after_first = client.execute_kw.call_count

    result2 = await get_department_cost(client=client)

    assert client.execute_kw.call_count == calls_after_first, (
        "Second call must be served from cache — execute_kw must not be called again"
    )
    assert result1["cache_status"] == "fresh"
    assert result2["cache_status"] == "cached"
    assert result2["rpc_duration_ms"] == 0
    assert result2["grand_total_wage"] == result1["grand_total_wage"]
    assert result2["rows"] == result1["rows"]


# ── Test 9 — Read-only guard fires before any RPC ────────────────────────────


async def test_read_only_guard_raises_before_any_rpc(monkeypatch):
    monkeypatch.setattr(
        "backend.modules.hr.services.kpi_service.ALLOWED_METHODS",
        frozenset({"read_group", "write"}),
    )
    client = MagicMock()
    client.execute_kw = AsyncMock()

    with pytest.raises(ReadOnlyViolationError):
        await get_department_cost(client=client)

    client.execute_kw.assert_not_called()


# ── Test 10 — RPC failure raises OdooQueryError ──────────────────────────────


async def test_rpc_failure_raises_odoo_query_error():
    client = MagicMock()
    client.execute_kw = AsyncMock(side_effect=RuntimeError("connection refused"))

    with pytest.raises(OdooQueryError):
        await get_department_cost(client=client)


# ── Test 11 — RPC failure writes no cache entry ──────────────────────────────


async def test_rpc_failure_writes_no_cache_entry():
    client = MagicMock()
    client.execute_kw = AsyncMock(side_effect=RuntimeError("timeout"))

    with pytest.raises(OdooQueryError):
        await get_department_cost(client=client)

    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_DEPT_COST)
    assert _cache.get(cache_key) is None, "A failed RPC must not write a cache entry"


# ── Test 12 — No-department contract counted and pooled into Other ────────────


async def test_contract_with_no_department_lands_in_no_dept_bucket():
    """
    A contract with department_id=False (missing department assignment) is
    counted in total_running_contracts and included in grand_total_wage.
    With 2 such employees (< k=3), they pool into the Other row with
    total_wage suppressed — verifying no-dept bucket obeys k-anon pooling.
    """
    contracts = [
        # Finance: 3 employees → normal row
        _contract(1, 101, 5, "Finance", 10000.0),
        _contract(2, 102, 5, "Finance", 12000.0),
        _contract(3, 103, 5, "Finance", 11000.0),
        # No department (dept_id=None → department_id=False): 2 employees → pooled
        _contract(4, 201, None, "(بدون إدارة)", 8000.0),
        _contract(5, 202, None, "(بدون إدارة)", 7000.0),
    ]
    client = _make_client(contracts)
    result = await get_department_cost(client=client)

    # Both no-dept employees counted in the population total
    assert result["total_running_contracts"] == 5

    # Grand total includes no-dept wages (never suppressed at the aggregate level)
    assert result["grand_total_wage"] == 48000.0  # 33000+8000+7000

    # No-dept bucket (2 employees) is below k=3 → pooled into Other
    names = {r["department_name"] for r in result["rows"]}
    assert _OTHER_DEPT_LABEL in names

    other = next(r for r in result["rows"] if r["department_name"] == _OTHER_DEPT_LABEL)
    assert other["running_contract_count"] == 2
    assert other["total_wage"] is None  # suppressed: pool count 2 < k=3
