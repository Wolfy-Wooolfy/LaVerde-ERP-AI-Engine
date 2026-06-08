"""
Unit tests for HR Employee Profile drill-down service — F3.

OdooClient is fully mocked; no live Odoo connection is made.
Live verification: scripts/verify_hr_f3_employee_profile_live.py.

Employment definition: Running contract (state='open').
Fields sourced from contract: department_id, job_id, date_start, date_end, state.
Fields sourced from employee: name, parent_id, work_location_id.
NO wage or compensation field is fetched — tests verify this explicitly.

Coverage:
  1.  test_response_shape                — all 13 required keys present
  2.  test_no_wage_key_in_response       — no wage/compensation key in profile dict
  3.  test_read_only_assertion           — contaminated ALLOWED_METHODS → ReadOnlyViolationError
  4.  test_no_open_contract_returns_none — no Running contract → service returns None (no RPC 2)
  5.  test_employee_missing_returns_none — RPC 1 ok, RPC 2 empty → None (defensive guard)
  6.  test_name_trimmed                  — name with whitespace → stripped in output
  7.  test_manager_name_trimmed          — manager_name with trailing space → stripped
  8.  test_manager_name_null_when_absent — parent_id=False → manager_name=None
  9.  test_tenure_calculation            — known date_start → correct tenure_years
  10. test_tenure_none_on_null_date      — null date_start → tenure_years=None, hire_date=None
  11. test_open_ended_handling           — date_end=False → is_open_ended=True, contract_end=None
  12. test_contract_end_populated        — date_end="2026-12-31" → is_open_ended=False, correct end
  13. test_job_title_dash_on_null_job    — job_id=False → job_title="—"
  14. test_department_from_contract      — department_name sourced from contract, not employee
  15. test_contract_status_always_running — contract_status is always "Running"
  16. test_invalid_employee_id_zero      — id=0 raises ValueError; no RPC fired
  17. test_invalid_employee_id_negative  — id=-1 raises ValueError; no RPC fired
  18. test_rpc_failure_contract          — execute_kw raises OdooQueryError → propagated
  19. test_rpc_generic_exc_wrapped       — execute_kw raises ConnectionError → OdooQueryError
  20. test_location_null_when_absent     — work_location_id=False → location=None
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.core.exceptions import OdooQueryError
from backend.modules.hr.services.employee_profile_service import (
    _compute_tenure,
    _parse_many2one_name,
    get_employee_profile,
)

# ── Constants ─────────────────────────────────────────────────────────────────

_EMP_ID    = 1057
_TODAY_STR = "2026-06-07"
_TODAY     = date.fromisoformat(_TODAY_STR)

_WAGE_KEYS = frozenset({
    "wage", "total_wage", "l10n_eg_housing_allowance",
    "l10n_eg_transportation_allowance", "l10n_eg_other_allowances",
    "basic_salary", "allowances", "contract_wage", "hourly_wage",
})

_REQUIRED_PROFILE_KEYS = frozenset({
    "employee_id", "name", "job_title", "department_name", "manager_name",
    "hire_date", "tenure_years", "contract_status", "contract_end",
    "is_open_ended", "location", "as_of", "rpc_duration_ms",
})


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_contract(
    emp_id: int = _EMP_ID,
    dept_id: int = 5,
    dept_name: str = "Finance",
    job_id: int | None = 10,
    job_name: str = "Accountant",
    date_start: str | None = "2020-01-15",
    date_end: str | bool = False,
    state: str = "open",
) -> dict:
    return {
        "id":            100,
        "employee_id":   [emp_id, f"Employee {emp_id}"],
        "department_id": [dept_id, dept_name],
        "job_id":        [job_id, job_name] if job_id else False,
        "date_start":    date_start,
        "date_end":      date_end,
        "state":         state,
    }


def _make_employee(
    emp_id: int = _EMP_ID,
    name: str = "Test Employee",
    manager_id: int | None = 999,
    manager_name: str | None = "The Manager",
    location_id: int | None = 1,
    location_name: str | None = "Cairo Office",
) -> dict:
    return {
        "id":               emp_id,
        "name":             name,
        "parent_id":        [manager_id, manager_name] if (manager_id and manager_name) else False,
        "work_location_id": [location_id, location_name] if (location_id and location_name) else False,
    }


def _make_client_2rpc(contracts: list, employees: list) -> MagicMock:
    """Mock for 2-RPC flow: first execute_kw call → contracts, second → employees."""
    client = MagicMock()
    client.is_read_only = True
    client.authenticate = AsyncMock()
    client.close = AsyncMock()
    client.execute_kw = AsyncMock(side_effect=[contracts, employees])
    return client


def _make_client_fail(exc: Exception) -> MagicMock:
    """Mock whose execute_kw always raises exc."""
    client = MagicMock()
    client.is_read_only = True
    client.authenticate = AsyncMock()
    client.close = AsyncMock()
    client.execute_kw = AsyncMock(side_effect=exc)
    return client


# ── Pure-unit helpers ─────────────────────────────────────────────────────────

def test_parse_many2one_name_with_list():
    assert _parse_many2one_name([5, "Finance"]) == "Finance"


def test_parse_many2one_name_with_false():
    assert _parse_many2one_name(False) == ""


def test_parse_many2one_name_empty_list():
    assert _parse_many2one_name([]) == ""


def test_compute_tenure_known_date():
    ds = "2020-01-15"
    today = date(2026, 6, 7)
    result = _compute_tenure(ds, today)
    expected = round((today - date(2020, 1, 15)).days / 365.25, 1)
    assert result == expected


def test_compute_tenure_null_input():
    assert _compute_tenure(None, _TODAY) is None
    assert _compute_tenure(False, _TODAY) is None
    assert _compute_tenure("", _TODAY) is None


def test_compute_tenure_invalid_input():
    assert _compute_tenure("not-a-date", _TODAY) is None


# ── Async service tests ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_response_shape():
    """All 13 required keys must be present in the response dict."""
    client = _make_client_2rpc([_make_contract()], [_make_employee()])
    result = await get_employee_profile(_EMP_ID, client=client)
    assert result is not None
    assert _REQUIRED_PROFILE_KEYS == set(result.keys()), (
        f"Key mismatch — extra: {set(result.keys()) - _REQUIRED_PROFILE_KEYS}, "
        f"missing: {_REQUIRED_PROFILE_KEYS - set(result.keys())}"
    )


@pytest.mark.asyncio
async def test_no_wage_key_in_response():
    """No wage or compensation key must appear anywhere in the profile dict."""
    client = _make_client_2rpc([_make_contract()], [_make_employee()])
    result = await get_employee_profile(_EMP_ID, client=client)
    assert result is not None
    wage_found = _WAGE_KEYS & set(result.keys())
    assert not wage_found, f"Wage/comp key found in profile: {wage_found}"


@pytest.mark.asyncio
async def test_read_only_assertion():
    """Contaminating ALLOWED_METHODS with a write method triggers ReadOnlyViolationError."""
    from backend.core.exceptions import ReadOnlyViolationError
    import backend.modules.hr.services.employee_profile_service as svc

    client = _make_client_2rpc([], [])
    orig = svc.ALLOWED_METHODS
    try:
        svc.ALLOWED_METHODS = frozenset({"write", "search_read"})
        with pytest.raises(ReadOnlyViolationError):
            await get_employee_profile(_EMP_ID, client=client)
    finally:
        svc.ALLOWED_METHODS = orig


@pytest.mark.asyncio
async def test_no_open_contract_returns_none():
    """No Running contract → service returns None and does NOT fire RPC 2."""
    client = _make_client_2rpc(contracts=[], employees=[])
    result = await get_employee_profile(_EMP_ID, client=client)
    assert result is None
    assert client.execute_kw.call_count == 1, (
        "RPC 2 must not fire when RPC 1 returns no contract"
    )


@pytest.mark.asyncio
async def test_employee_missing_returns_none():
    """Contract found but employee record absent → None (defensive guard)."""
    client = _make_client_2rpc(contracts=[_make_contract()], employees=[])
    result = await get_employee_profile(_EMP_ID, client=client)
    assert result is None
    assert client.execute_kw.call_count == 2


@pytest.mark.asyncio
async def test_name_trimmed():
    """Leading/trailing whitespace in name must be stripped."""
    client = _make_client_2rpc(
        [_make_contract()],
        [_make_employee(name="  Padded Name  ")],
    )
    result = await get_employee_profile(_EMP_ID, client=client)
    assert result is not None
    assert result["name"] == "Padded Name"


@pytest.mark.asyncio
async def test_manager_name_trimmed():
    """Trailing whitespace in manager display name must be stripped (live data has this)."""
    client = _make_client_2rpc(
        [_make_contract()],
        [_make_employee(manager_name="Manager With Space ")],
    )
    result = await get_employee_profile(_EMP_ID, client=client)
    assert result is not None
    assert result["manager_name"] == "Manager With Space"


@pytest.mark.asyncio
async def test_manager_name_null_when_absent():
    """parent_id=False → manager_name must be None, not empty string."""
    client = _make_client_2rpc(
        [_make_contract()],
        [_make_employee(manager_id=None, manager_name=None)],
    )
    result = await get_employee_profile(_EMP_ID, client=client)
    assert result is not None
    assert result["manager_name"] is None


@pytest.mark.asyncio
async def test_tenure_calculation():
    """Known date_start → deterministic tenure_years using the F2 formula."""
    date_start = "2020-01-15"
    client = _make_client_2rpc([_make_contract(date_start=date_start)], [_make_employee()])
    result = await get_employee_profile(_EMP_ID, client=client)
    assert result is not None
    assert result["hire_date"] == date_start
    assert result["tenure_years"] is not None
    today = date.fromisoformat(result["as_of"][:10])
    expected = round((today - date(2020, 1, 15)).days / 365.25, 1)
    assert result["tenure_years"] == expected, (
        f"Expected tenure {expected}, got {result['tenure_years']}"
    )


@pytest.mark.asyncio
async def test_tenure_none_on_null_date():
    """date_start=None → hire_date=None, tenure_years=None."""
    client = _make_client_2rpc([_make_contract(date_start=None)], [_make_employee()])
    result = await get_employee_profile(_EMP_ID, client=client)
    assert result is not None
    assert result["hire_date"] is None
    assert result["tenure_years"] is None


@pytest.mark.asyncio
async def test_open_ended_handling():
    """date_end=False → is_open_ended=True, contract_end=None."""
    client = _make_client_2rpc([_make_contract(date_end=False)], [_make_employee()])
    result = await get_employee_profile(_EMP_ID, client=client)
    assert result is not None
    assert result["is_open_ended"] is True
    assert result["contract_end"] is None


@pytest.mark.asyncio
async def test_contract_end_populated():
    """date_end set → is_open_ended=False, contract_end is the ISO date string."""
    end = "2026-12-31"
    client = _make_client_2rpc([_make_contract(date_end=end)], [_make_employee()])
    result = await get_employee_profile(_EMP_ID, client=client)
    assert result is not None
    assert result["is_open_ended"] is False
    assert result["contract_end"] == end


@pytest.mark.asyncio
async def test_job_title_dash_on_null_job():
    """job_id=False on contract → job_title must be '—'."""
    client = _make_client_2rpc([_make_contract(job_id=None)], [_make_employee()])
    result = await get_employee_profile(_EMP_ID, client=client)
    assert result is not None
    assert result["job_title"] == "—"


@pytest.mark.asyncio
async def test_department_from_contract():
    """department_name is sourced from the contract's department_id, not hr.employee."""
    client = _make_client_2rpc(
        [_make_contract(dept_name="Contract Department")],
        [_make_employee()],
    )
    result = await get_employee_profile(_EMP_ID, client=client)
    assert result is not None
    assert result["department_name"] == "Contract Department"


@pytest.mark.asyncio
async def test_contract_status_always_running():
    """contract_status must always be 'Running' (filter guarantees state='open')."""
    client = _make_client_2rpc([_make_contract()], [_make_employee()])
    result = await get_employee_profile(_EMP_ID, client=client)
    assert result is not None
    assert result["contract_status"] == "Running"


@pytest.mark.asyncio
async def test_invalid_employee_id_zero():
    """employee_id=0 must raise ValueError before any RPC."""
    client = _make_client_2rpc([], [])
    with pytest.raises(ValueError, match="positive integer"):
        await get_employee_profile(0, client=client)
    client.execute_kw.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_employee_id_negative():
    """employee_id=-1 must raise ValueError before any RPC."""
    client = _make_client_2rpc([], [])
    with pytest.raises(ValueError, match="positive integer"):
        await get_employee_profile(-1, client=client)
    client.execute_kw.assert_not_called()


@pytest.mark.asyncio
async def test_rpc_failure_contract():
    """execute_kw raising OdooQueryError → propagated as-is."""
    client = _make_client_fail(OdooQueryError("Odoo down"))
    with pytest.raises(OdooQueryError):
        await get_employee_profile(_EMP_ID, client=client)


@pytest.mark.asyncio
async def test_rpc_generic_exc_wrapped():
    """execute_kw raising a generic exception → wrapped in OdooQueryError."""
    client = _make_client_fail(ConnectionError("refused"))
    with pytest.raises(OdooQueryError):
        await get_employee_profile(_EMP_ID, client=client)


@pytest.mark.asyncio
async def test_location_null_when_absent():
    """work_location_id=False → location must be None, not empty string."""
    client = _make_client_2rpc(
        [_make_contract()],
        [_make_employee(location_id=None, location_name=None)],
    )
    result = await get_employee_profile(_EMP_ID, client=client)
    assert result is not None
    assert result["location"] is None
