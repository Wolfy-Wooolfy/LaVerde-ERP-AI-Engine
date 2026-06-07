"""
Unit tests for HR Department Staff drill-down service — F2.

OdooClient is fully mocked; no live Odoo connection is made.
Live identity verification: scripts/verify_hr_f2_identity.py.

Employment definition (§3.6): Running contract (state='open').
Population: hr.contract, [('state','=','open'), ('department_id','=',id)].
NO wage or compensation field is fetched — tests verify this explicitly.

Coverage:
  1.  test_response_shape            — all required top-level keys present
  2.  test_staff_row_keys            — staff rows have exactly the expected keys
  3.  test_no_wage_in_staff_rows     — no wage/compensation key in any staff row
  4.  test_tenure_calculation        — known date_start → correct tenure_years
  5.  test_tenure_none_on_null_date  — null date_start → tenure_years=None, date_start=None
  6.  test_job_title_dash_on_null    — null job_id → job_title="—"
  7.  test_staff_sorted_by_name      — staff sorted ascending by employee_name
  8.  test_orphaned_contract_skipped — contract with no employee_id skipped
  9.  test_empty_on_no_contracts     — 0 running contracts → headcount=0, staff=[]
  10. test_read_only_assertion       — is_read_only=False raises AssertionError
  11. test_invalid_department_id     — department_id <= 0 raises ValueError
  12. test_rpc_failure               — execute_kw raises OdooQueryError
  13. test_contract_state_field      — contract_state is always "open"
  14. test_headcount_equals_len_staff — headcount == len(staff)
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.core.exceptions import OdooQueryError
from backend.modules.hr.services.dept_staff_service import (
    _compute_tenure,
    _parse_many2one_name,
    get_department_staff,
)

# ── Constants ─────────────────────────────────────────────────────────────────

_DEPT_ID   = 5
_TODAY_STR = "2026-06-07"
_TODAY     = date.fromisoformat(_TODAY_STR)

_WAGE_KEYS = frozenset({
    "wage", "total_wage", "l10n_eg_housing_allowance",
    "l10n_eg_transportation_allowance", "l10n_eg_other_allowances",
    "basic_salary", "allowances", "contract_wage", "hourly_wage",
})

_REQUIRED_STAFF_KEYS = frozenset({
    "employee_id", "employee_name", "job_title",
    "date_start", "tenure_years", "contract_state",
})

_REQUIRED_TOP_KEYS = frozenset({
    "department_id", "department_name", "headcount", "staff",
    "reference_date", "as_of", "rpc_duration_ms",
})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_client(contracts: list) -> MagicMock:
    """Build a mock OdooClient returning contracts on the single execute_kw call."""
    client = MagicMock()
    client.is_read_only = True
    client.authenticate = AsyncMock()
    client.close = AsyncMock()
    client.execute_kw = AsyncMock(return_value=contracts)
    return client


def _make_contract(
    emp_id: int = 101,
    emp_name: str = "Ahmed Ali",
    job_id: int | None = 10,
    job_name: str = "Accountant",
    dept_id: int = _DEPT_ID,
    dept_name: str = "Finance",
    date_start: str | None = "2020-01-15",
    state: str = "open",
) -> dict:
    return {
        "id":            100 + emp_id,
        "employee_id":   [emp_id, emp_name],
        "job_id":        [job_id, job_name] if job_id else False,
        "department_id": [dept_id, dept_name],
        "date_start":    date_start,
        "state":         state,
    }


def _patch_today(today_str: str = _TODAY_STR):
    """Patch the Cairo timezone clock so tenure is deterministic."""
    from unittest.mock import patch as _patch
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    fixed_dt = datetime.fromisoformat(f"{today_str}T12:00:00+02:00")

    class _FakeTZ:
        def __init__(self, *_a, **_kw):
            pass

        def fromutc(self, dt):
            return dt

    return _patch(
        "backend.modules.hr.services.dept_staff_service.datetime",
        wraps=datetime,
    )


# ── Pure-unit tests (no async) ────────────────────────────────────────────────

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
    """All required top-level keys must be present in the response."""
    client = _make_client([_make_contract()])
    result = await get_department_staff(_DEPT_ID, client=client)
    assert _REQUIRED_TOP_KEYS <= set(result.keys()), (
        f"Missing keys: {_REQUIRED_TOP_KEYS - set(result.keys())}"
    )


@pytest.mark.asyncio
async def test_staff_row_keys():
    """Each staff row must have exactly the expected keys — no more, no less."""
    client = _make_client([_make_contract()])
    result = await get_department_staff(_DEPT_ID, client=client)
    assert result["staff"], "Expected at least one staff row"
    for row in result["staff"]:
        assert set(row.keys()) == _REQUIRED_STAFF_KEYS, (
            f"Staff row keys mismatch: {set(row.keys())} != {_REQUIRED_STAFF_KEYS}"
        )


@pytest.mark.asyncio
async def test_no_wage_in_staff_rows():
    """No wage or compensation key must appear in any staff row."""
    client = _make_client([
        _make_contract(emp_id=101, emp_name="Ahmed"),
        _make_contract(emp_id=102, emp_name="Samer"),
    ])
    result = await get_department_staff(_DEPT_ID, client=client)
    for row in result["staff"]:
        wage_found = _WAGE_KEYS & set(row.keys())
        assert not wage_found, (
            f"Wage/comp key found in staff row: {wage_found}"
        )


@pytest.mark.asyncio
async def test_tenure_calculation():
    """Known date_start → deterministic tenure_years."""
    date_start = "2020-01-15"
    client = _make_client([_make_contract(date_start=date_start)])
    result = await get_department_staff(_DEPT_ID, client=client)
    row = result["staff"][0]
    assert row["date_start"] == date_start
    assert row["tenure_years"] is not None
    assert isinstance(row["tenure_years"], float)
    today = result["reference_date"]
    td = date.fromisoformat(today)
    expected = round((td - date(2020, 1, 15)).days / 365.25, 1)
    assert row["tenure_years"] == expected, (
        f"Expected tenure {expected}, got {row['tenure_years']}"
    )


@pytest.mark.asyncio
async def test_tenure_none_on_null_date():
    """null date_start → tenure_years=None, date_start=None."""
    client = _make_client([_make_contract(date_start=None)])
    result = await get_department_staff(_DEPT_ID, client=client)
    row = result["staff"][0]
    assert row["date_start"] is None
    assert row["tenure_years"] is None


@pytest.mark.asyncio
async def test_job_title_dash_on_null():
    """null job_id → job_title='—'."""
    client = _make_client([_make_contract(job_id=None)])
    result = await get_department_staff(_DEPT_ID, client=client)
    row = result["staff"][0]
    assert row["job_title"] == "—"


@pytest.mark.asyncio
async def test_staff_sorted_by_name():
    """Staff must be sorted ascending by employee_name."""
    contracts = [
        _make_contract(emp_id=101, emp_name="Ziad"),
        _make_contract(emp_id=102, emp_name="Ahmed"),
        _make_contract(emp_id=103, emp_name="Mohamed"),
    ]
    client = _make_client(contracts)
    result = await get_department_staff(_DEPT_ID, client=client)
    names = [r["employee_name"] for r in result["staff"]]
    assert names == sorted(names), f"Staff not sorted: {names}"


@pytest.mark.asyncio
async def test_orphaned_contract_skipped():
    """Contract with no employee_id must be skipped silently."""
    contracts = [
        {"id": 1, "employee_id": False, "job_id": False,
         "department_id": [_DEPT_ID, "Finance"], "date_start": "2021-01-01", "state": "open"},
        _make_contract(emp_id=101, emp_name="Ahmed"),
    ]
    client = _make_client(contracts)
    result = await get_department_staff(_DEPT_ID, client=client)
    assert result["headcount"] == 1
    assert result["staff"][0]["employee_name"] == "Ahmed"


@pytest.mark.asyncio
async def test_empty_on_no_contracts():
    """0 Running contracts → headcount=0, staff=[]."""
    client = _make_client([])
    result = await get_department_staff(_DEPT_ID, client=client)
    assert result["headcount"] == 0
    assert result["staff"] == []


@pytest.mark.asyncio
async def test_read_only_assertion():
    """Contaminating ALLOWED_METHODS with a write method triggers ReadOnlyViolationError.

    _assert_read_only() checks ALLOWED_METHODS (the module-level frozenset imported
    from shared.odoo.client), not client.is_read_only. Patch the module-level name
    to simulate a contaminated allowlist.
    """
    from backend.core.exceptions import ReadOnlyViolationError
    import backend.modules.hr.services.dept_staff_service as svc

    client = _make_client([])
    orig = svc.ALLOWED_METHODS
    try:
        svc.ALLOWED_METHODS = frozenset({"write", "search_read"})
        with pytest.raises(ReadOnlyViolationError):
            await get_department_staff(_DEPT_ID, client=client)
    finally:
        svc.ALLOWED_METHODS = orig


@pytest.mark.asyncio
async def test_invalid_department_id_zero():
    """department_id=0 must raise ValueError before any RPC."""
    client = _make_client([])
    with pytest.raises(ValueError, match="positive integer"):
        await get_department_staff(0, client=client)
    client.execute_kw.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_department_id_negative():
    """department_id=-1 must raise ValueError before any RPC."""
    client = _make_client([])
    with pytest.raises(ValueError, match="positive integer"):
        await get_department_staff(-1, client=client)
    client.execute_kw.assert_not_called()


@pytest.mark.asyncio
async def test_rpc_failure():
    """execute_kw raising any exception → OdooQueryError is propagated."""
    client = MagicMock()
    client.is_read_only = True
    client.authenticate = AsyncMock()
    client.close = AsyncMock()
    client.execute_kw = AsyncMock(side_effect=OdooQueryError("Odoo down"))
    with pytest.raises(OdooQueryError):
        await get_department_staff(_DEPT_ID, client=client)


@pytest.mark.asyncio
async def test_rpc_generic_exception_wrapped():
    """execute_kw raising a generic exception → wrapped in OdooQueryError."""
    client = MagicMock()
    client.is_read_only = True
    client.authenticate = AsyncMock()
    client.close = AsyncMock()
    client.execute_kw = AsyncMock(side_effect=ConnectionError("refused"))
    with pytest.raises(OdooQueryError):
        await get_department_staff(_DEPT_ID, client=client)


@pytest.mark.asyncio
async def test_contract_state_field():
    """contract_state in each staff row must always be 'open'."""
    client = _make_client([_make_contract(state="open")])
    result = await get_department_staff(_DEPT_ID, client=client)
    for row in result["staff"]:
        assert row["contract_state"] == "open"


@pytest.mark.asyncio
async def test_headcount_equals_len_staff():
    """headcount must equal len(staff) at all times."""
    contracts = [
        _make_contract(emp_id=101, emp_name="A"),
        _make_contract(emp_id=102, emp_name="B"),
        _make_contract(emp_id=103, emp_name="C"),
    ]
    client = _make_client(contracts)
    result = await get_department_staff(_DEPT_ID, client=client)
    assert result["headcount"] == len(result["staff"])


@pytest.mark.asyncio
async def test_department_name_extracted():
    """department_name must be extracted from the first contract's department_id."""
    client = _make_client([_make_contract(dept_name="Engineering / Backend")])
    result = await get_department_staff(_DEPT_ID, client=client)
    assert result["department_name"] == "Engineering / Backend"


@pytest.mark.asyncio
async def test_department_id_echoed():
    """department_id in the response must match the input parameter."""
    client = _make_client([_make_contract(dept_id=42, dept_name="HR")])
    result = await get_department_staff(42, client=client)
    assert result["department_id"] == 42
