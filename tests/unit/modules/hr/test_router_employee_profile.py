"""
Endpoint tests for HR F3 — GET /api/v1/hr/employee/{employee_id}.

Uses FastAPI TestClient with get_employee_profile patched.
No Odoo connection is made.

Auth: authenticated session (session cookie via get_current_user) + the "hr" module
grant (require_module_api("hr")) — endpoint returns employee name (PII).
Cache-Control: private, no-store — never max-age.

Coverage:
  1.  test_200_and_all_keys           — valid employee → 200 + all 13 schema keys
  2.  test_no_wage_in_response        — explicit wage/comp key absence check
  3.  test_400_on_zero_id             — /employee/0 → 400
  4.  test_404_on_none_profile        — service returns None → 404 + employee_not_found code
  5.  test_503_on_odoo_error          — OdooQueryError from service → 503
  6.  test_500_on_unexpected_error    — RuntimeError from service → 500
  7.  test_cache_control_no_store     — Cache-Control: private, no-store; no max-age
  8.  test_no_x_cache_status_header   — no X-Cache-Status (PII endpoint, no caching)
  9.  test_401_without_auth           — unauthenticated request → 401
  10. test_contract_status_is_running — contract_status == "Running"
  11. test_is_open_ended_present      — is_open_ended key is bool
  12. test_open_ended_true_null_end   — is_open_ended=True → contract_end is None
  13. test_400_error_code             — 400 error body has code: invalid_employee_id
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

_URL = "/api/v1/hr/employee/1057"

_REQUIRED_TOP_KEYS = frozenset({
    "employee_id", "name", "job_title", "department_name", "manager_name",
    "hire_date", "tenure_years", "contract_status", "contract_end",
    "is_open_ended", "location", "as_of", "rpc_duration_ms",
})

_WAGE_KEYS = frozenset({
    "wage", "total_wage", "l10n_eg_housing_allowance",
    "l10n_eg_transportation_allowance", "l10n_eg_other_allowances",
    "basic_salary", "allowances", "contract_wage", "hourly_wage",
})

_MOCK_PROFILE = {
    "employee_id":     1057,
    "name":            "Test Employee",
    "job_title":       "Accountant",
    "department_name": "Finance",
    "manager_name":    "Senior Manager",
    "hire_date":       "2020-01-15",
    "tenure_years":    6.4,
    "contract_status": "Running",
    "contract_end":    None,
    "is_open_ended":   True,
    "location":        "Cairo Office",
    "as_of":           "2026-06-07T10:00:00+00:00",
    "rpc_duration_ms": 42,
}

_MOCK_PROFILE_WITH_END = {
    **_MOCK_PROFILE,
    "contract_end":  "2026-12-31",
    "is_open_ended": False,
}


def _patch_profile(return_value=_MOCK_PROFILE):
    return patch(
        "backend.api.v1.endpoints.hr.get_employee_profile",
        new=AsyncMock(return_value=return_value),
    )


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
    with _patch_profile():
        r = client.get(_URL)
    assert r.status_code == 200
    body = r.json()
    missing = _REQUIRED_TOP_KEYS - set(body.keys())
    assert not missing, f"Response missing keys: {missing}"
    extra = set(body.keys()) - _REQUIRED_TOP_KEYS
    assert not extra, f"Response has unexpected keys: {extra}"


# ── Test 2 — no wage in response ─────────────────────────────────────────────

def test_no_wage_in_response(client: TestClient) -> None:
    with _patch_profile():
        r = client.get(_URL)
    assert r.status_code == 200
    found = _WAGE_KEYS & set(r.json().keys())
    assert not found, f"Wage/comp key in profile response: {found}"


# ── Test 3 — 400 on zero id ───────────────────────────────────────────────────

def test_400_on_zero_id(client: TestClient) -> None:
    r = client.get("/api/v1/hr/employee/0")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_employee_id"


# ── Test 4 — 404 when service returns None ────────────────────────────────────

def test_404_on_none_profile(client: TestClient) -> None:
    with _patch_profile(return_value=None):
        r = client.get(_URL)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "employee_not_found"


# ── Test 5 — 503 on Odoo error ────────────────────────────────────────────────

def test_503_on_odoo_error(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.hr.get_employee_profile",
        new=AsyncMock(side_effect=OdooQueryError("Odoo down")),
    ):
        r = client.get(_URL)
    assert r.status_code == 503


# ── Test 6 — 500 on unexpected error ─────────────────────────────────────────

def test_500_on_unexpected_error(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.hr.get_employee_profile",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        r = client.get(_URL)
    assert r.status_code == 500


# ── Test 7 — Cache-Control: private, no-store ─────────────────────────────────

def test_cache_control_no_store(client: TestClient) -> None:
    with _patch_profile():
        r = client.get(_URL)
    assert r.status_code == 200
    cc = r.headers.get("cache-control", "")
    assert "private"   in cc, f"Expected 'private' in Cache-Control: {cc!r}"
    assert "no-store"  in cc, f"Expected 'no-store' in Cache-Control: {cc!r}"
    assert "max-age" not in cc, f"Must NOT have max-age in Cache-Control: {cc!r}"


# ── Test 8 — no X-Cache-Status header ────────────────────────────────────────

def test_no_x_cache_status_header(client: TestClient) -> None:
    with _patch_profile():
        r = client.get(_URL)
    assert r.status_code == 200
    assert "x-cache-status" not in r.headers, (
        "PII endpoint must not expose X-Cache-Status"
    )


# ── Test 9 — 401 without auth ─────────────────────────────────────────────────

def test_401_without_auth() -> None:
    c = TestClient(app, raise_server_exceptions=True)
    with _patch_profile():
        r = c.get(_URL)  # no session
    assert r.status_code == 401, (
        f"Expected 401 for unauthenticated PII request, got {r.status_code}"
    )


# ── Test 10 — contract_status is "Running" ────────────────────────────────────

def test_contract_status_is_running(client: TestClient) -> None:
    with _patch_profile():
        r = client.get(_URL)
    assert r.status_code == 200
    assert r.json()["contract_status"] == "Running"


# ── Test 11 — is_open_ended is bool ──────────────────────────────────────────

def test_is_open_ended_present(client: TestClient) -> None:
    with _patch_profile():
        r = client.get(_URL)
    assert r.status_code == 200
    val = r.json()["is_open_ended"]
    assert isinstance(val, bool), f"is_open_ended must be bool, got {type(val)}"


# ── Test 12 — open-ended: is_open_ended=True + contract_end=None ─────────────

def test_open_ended_true_null_end(client: TestClient) -> None:
    with _patch_profile(_MOCK_PROFILE):
        r = client.get(_URL)
    assert r.status_code == 200
    body = r.json()
    assert body["is_open_ended"] is True
    assert body["contract_end"] is None


# ── Test 13 — 400 error body has expected code ───────────────────────────────

def test_400_error_code(client: TestClient) -> None:
    r = client.get("/api/v1/hr/employee/0")
    assert r.status_code == 400
    body = r.json()
    assert "error" in body
    assert body["error"]["code"] == "invalid_employee_id"
    assert "message" in body["error"]
