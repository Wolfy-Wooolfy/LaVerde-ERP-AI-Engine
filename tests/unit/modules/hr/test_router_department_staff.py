"""
Endpoint tests for HR F2 — GET /api/v1/hr/department/{department_id}.

Uses FastAPI TestClient with get_department_staff and get_department_cost patched.
No Odoo connection is made.

Auth: same Basic-Auth as sibling HR KPI endpoints (no endpoint-level Depends).
Cache-Control: private, no-store — PII response; never max-age.

Coverage:
  1.  test_200_and_all_keys          — valid dept → 200 + all schema keys present
  2.  test_staff_row_keys_exact      — staff rows have exactly the expected fields
  3.  test_no_wage_in_staff_rows     — explicit wage/comp key absence check
  4.  test_dept_aggregates_present   — total_wage / pct / avg keys present and typed
  5.  test_pct_and_avg_computed      — pct_of_total_payroll and avg_cost_per_head correct
  6.  test_404_on_empty_staff        — dept with no Running staff → 404
  7.  test_400_on_zero_dept_id       — department_id=0 in URL → 400
  8.  test_503_on_odoo_error         — OdooQueryError from service → 503
  9.  test_500_on_unexpected_error   — RuntimeError from service → 500
  10. test_cache_control_no_store    — response header is private, no-store
  11. test_no_x_cache_status_header  — no X-Cache-Status (PII endpoint, no caching)
  12. test_total_wage_null_when_missing — dept not in KPI D rows → total_wage=None
  13. test_currency_and_basis        — currency="EGP", basis="monthly"
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_current_user
from backend.auth.models import UserRecord
from backend.core.exceptions import OdooQueryError
from backend.main import app

_TESTADMIN_RECORD = UserRecord(
    username="testadmin", password_hash="", modules=["*"],
    is_admin=True, is_active=True,
    created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
)

_URL = "/api/v1/hr/department/5"

_REQUIRED_TOP_KEYS = frozenset({
    "department_id", "department_name", "headcount",
    "total_wage", "pct_of_total_payroll", "avg_cost_per_head",
    "staff", "currency", "basis", "reference_date", "as_of", "rpc_duration_ms",
})

_REQUIRED_STAFF_KEYS = frozenset({
    "employee_id", "employee_name", "job_title",
    "date_start", "tenure_years", "contract_state",
})

_WAGE_KEYS = frozenset({
    "wage", "total_wage", "l10n_eg_housing_allowance",
    "l10n_eg_transportation_allowance", "l10n_eg_other_allowances",
    "basic_salary", "allowances", "contract_wage", "hourly_wage",
})

_MOCK_STAFF_DATA = {
    "department_id":   5,
    "department_name": "Finance",
    "headcount":       3,
    "staff": [
        {
            "employee_id":    101,
            "employee_name":  "Ahmed Ali",
            "job_title":      "Accountant",
            "date_start":     "2020-01-15",
            "tenure_years":   6.4,
            "contract_state": "open",
        },
        {
            "employee_id":    102,
            "employee_name":  "Mohamed Salem",
            "job_title":      "Senior Accountant",
            "date_start":     "2018-03-01",
            "tenure_years":   8.3,
            "contract_state": "open",
        },
        {
            "employee_id":    103,
            "employee_name":  "Sara Hassan",
            "job_title":      "—",
            "date_start":     None,
            "tenure_years":   None,
            "contract_state": "open",
        },
    ],
    "reference_date":  "2026-06-07",
    "as_of":           "2026-06-07T10:00:00+00:00",
    "rpc_duration_ms": 42,
}

_MOCK_COST_DATA = {
    "rows": [
        {
            "department_id":          5,
            "department_name":        "Finance",
            "running_contract_count": 3,
            "total_wage":             33000.0,
        },
        {
            "department_id":          6,
            "department_name":        "Sales",
            "running_contract_count": 4,
            "total_wage":             40000.0,
        },
        {
            "department_id":          None,
            "department_name":        "Other (small departments)",
            "running_contract_count": 5,
            "total_wage":             None,
        },
    ],
    "grand_total_wage":        635000.0,
    "total_running_contracts": 12,
    "currency":                "EGP",
    "basis":                   "monthly",
    "reference_date":          "2026-06-07",
    "as_of":                   "2026-06-07T10:00:00+00:00",
    "cache_status":            "fresh",
    "rpc_duration_ms":         15,
}

_MOCK_EMPTY_STAFF = {
    "department_id":   999,
    "department_name": "",
    "headcount":       0,
    "staff":           [],
    "reference_date":  "2026-06-07",
    "as_of":           "2026-06-07T10:00:00+00:00",
    "rpc_duration_ms": 10,
}


def _patch_both(staff_return=_MOCK_STAFF_DATA, cost_return=_MOCK_COST_DATA):
    """Context manager: patch both service calls used by the endpoint."""
    from contextlib import ExitStack
    stack = ExitStack()
    stack.enter_context(patch(
        "backend.api.v1.endpoints.hr.get_department_staff",
        new=AsyncMock(return_value=staff_return),
    ))
    stack.enter_context(patch(
        "backend.api.v1.endpoints.hr.get_department_cost",
        new=AsyncMock(return_value=cost_return),
    ))
    return stack


@pytest.fixture
def client() -> TestClient:
    app.dependency_overrides[get_current_user] = lambda: "testadmin"
    mock_repo = MagicMock()
    mock_repo.get_user.return_value = _TESTADMIN_RECORD
    app.state.user_repo = mock_repo
    c = TestClient(app, raise_server_exceptions=True)
    yield c
    app.dependency_overrides.pop(get_current_user, None)
    if hasattr(app.state, "user_repo"):
        del app.state.user_repo


# ── Test 1 — 200 + all schema keys ───────────────────────────────────────────


def test_200_and_all_keys(client: TestClient) -> None:
    with _patch_both():
        r = client.get(_URL)
    assert r.status_code == 200
    body = r.json()
    missing = _REQUIRED_TOP_KEYS - set(body.keys())
    assert not missing, f"Response missing keys: {missing}"


# ── Test 2 — staff row keys exact ────────────────────────────────────────────


def test_staff_row_keys_exact(client: TestClient) -> None:
    with _patch_both():
        r = client.get(_URL)
    assert r.status_code == 200
    for row in r.json()["staff"]:
        assert set(row.keys()) == _REQUIRED_STAFF_KEYS, (
            f"Unexpected staff row keys: {set(row.keys())}"
        )


# ── Test 3 — no wage in staff rows ───────────────────────────────────────────


def test_no_wage_in_staff_rows(client: TestClient) -> None:
    with _patch_both():
        r = client.get(_URL)
    assert r.status_code == 200
    for row in r.json()["staff"]:
        found = _WAGE_KEYS & set(row.keys())
        assert not found, f"Wage/comp key in staff row: {found}"


# ── Test 4 — dept aggregates present ─────────────────────────────────────────


def test_dept_aggregates_present(client: TestClient) -> None:
    with _patch_both():
        r = client.get(_URL)
    assert r.status_code == 200
    body = r.json()
    assert "total_wage" in body
    assert "pct_of_total_payroll" in body
    assert "avg_cost_per_head" in body
    assert body["total_wage"] == 33000.0
    assert isinstance(body["pct_of_total_payroll"], float)
    assert isinstance(body["avg_cost_per_head"], float)


# ── Test 5 — pct and avg computed correctly ───────────────────────────────────


def test_pct_and_avg_computed(client: TestClient) -> None:
    with _patch_both():
        r = client.get(_URL)
    body = r.json()
    expected_pct = round(33000.0 / 635000.0 * 100, 1)
    expected_avg = round(33000.0 / 3, 0)
    assert body["pct_of_total_payroll"] == expected_pct
    assert body["avg_cost_per_head"] == expected_avg


# ── Test 6 — 404 on empty staff ──────────────────────────────────────────────


def test_404_on_empty_staff(client: TestClient) -> None:
    with _patch_both(staff_return=_MOCK_EMPTY_STAFF):
        r = client.get("/api/v1/hr/department/999")
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "department_not_found"


# ── Test 7 — 400 on zero dept id ─────────────────────────────────────────────


def test_400_on_zero_dept_id(client: TestClient) -> None:
    r = client.get("/api/v1/hr/department/0")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_department_id"


# ── Test 8 — 503 on Odoo error ───────────────────────────────────────────────


def test_503_on_odoo_error(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.hr.get_department_staff",
        new=AsyncMock(side_effect=OdooQueryError("Odoo down")),
    ), patch(
        "backend.api.v1.endpoints.hr.get_department_cost",
        new=AsyncMock(return_value=_MOCK_COST_DATA),
    ):
        r = client.get(_URL)
    assert r.status_code == 503


# ── Test 9 — 500 on unexpected error ─────────────────────────────────────────


def test_500_on_unexpected_error(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.hr.get_department_staff",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ), patch(
        "backend.api.v1.endpoints.hr.get_department_cost",
        new=AsyncMock(return_value=_MOCK_COST_DATA),
    ):
        r = client.get(_URL)
    assert r.status_code == 500


# ── Test 10 — Cache-Control: private, no-store ────────────────────────────────


def test_cache_control_no_store(client: TestClient) -> None:
    with _patch_both():
        r = client.get(_URL)
    assert r.status_code == 200
    cc = r.headers.get("cache-control", "")
    assert "private" in cc, f"Expected 'private' in Cache-Control: {cc!r}"
    assert "no-store" in cc, f"Expected 'no-store' in Cache-Control: {cc!r}"
    assert "max-age" not in cc, f"Must NOT have max-age in Cache-Control: {cc!r}"


# ── Test 11 — no X-Cache-Status header ───────────────────────────────────────


def test_no_x_cache_status_header(client: TestClient) -> None:
    with _patch_both():
        r = client.get(_URL)
    assert r.status_code == 200
    assert "x-cache-status" not in r.headers, (
        "PII endpoint must not expose X-Cache-Status"
    )


# ── Test 12 — total_wage null when dept not in KPI D rows ────────────────────


def test_total_wage_null_when_missing(client: TestClient) -> None:
    cost_without_dept5 = {
        **_MOCK_COST_DATA,
        "rows": [r for r in _MOCK_COST_DATA["rows"] if r["department_id"] != 5],
    }
    with _patch_both(cost_return=cost_without_dept5):
        r = client.get(_URL)
    assert r.status_code == 200
    body = r.json()
    assert body["total_wage"] is None
    assert body["pct_of_total_payroll"] is None
    assert body["avg_cost_per_head"] is None


# ── Test 13 — currency and basis ─────────────────────────────────────────────


def test_currency_and_basis(client: TestClient) -> None:
    with _patch_both():
        r = client.get(_URL)
    assert r.status_code == 200
    body = r.json()
    assert body["currency"] == "EGP"
    assert body["basis"] == "monthly"


# ── Test 14 — 401 when no auth supplied ──────────────────────────────────────


def test_401_when_no_auth() -> None:
    """PII endpoint must reject unauthenticated requests with 401.

    The sibling HR KPI endpoints (/kpi/*) were unprotected prior to 2026-06-09.
    They are now protected by Depends(get_current_user) as part of the
    security hotfix applied across Collections, Customer Accounts, and HR KPIs.
    This endpoint has always been explicitly protected because it returns employee names.
    """
    c = TestClient(app, raise_server_exceptions=True)
    with _patch_both():
        r = c.get(_URL)  # no session
    assert r.status_code == 401, (
        f"Expected 401 for unauthenticated PII request, got {r.status_code}"
    )
