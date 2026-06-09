"""
Endpoint tests for HR KPI A — GET /api/v1/hr/kpi/headcount.

Uses FastAPI TestClient with get_headcount patched — no Odoo connection.
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

_URL = "/api/v1/hr/kpi/headcount"

_MOCK_DATA = {
    "headcount": 115,
    "by_department": [
        {"department_id": 10, "department_name": "Finance", "count": 18},
        {"department_id": None, "department_name": "(بدون إدارة)", "count": 4},
    ],
    "by_job": [
        {"job_id": 20, "job_name": "Senior Sales Executive", "count": 15},
        {"job_id": None, "job_name": "(بدون وظيفة)", "count": 3},
    ],
    "incoming_count": 0,
    "active_flag_count": 136,
    "active_without_running": 34,
    "reference_date": "2026-06-03",
    "as_of": "2026-06-03T08:22:41+00:00",
    "cache_status": "fresh",
    "rpc_duration_ms": 85,
}


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


# ── Test 1 — 200 + JSON shape ─────────────────────────────────────────────────


def test_headcount_returns_200_and_all_keys(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.hr.get_headcount",
        new=AsyncMock(return_value=_MOCK_DATA),
    ):
        r = client.get(_URL)

    assert r.status_code == 200
    body = r.json()
    for key in ("headcount", "by_department", "by_job",
                "incoming_count", "active_flag_count", "active_without_running",
                "reference_date", "as_of", "cache_status", "rpc_duration_ms"):
        assert key in body, f"Response missing key: {key!r}"
    assert body["headcount"] == 115
    assert body["active_flag_count"] == 136
    assert body["active_without_running"] == 34
    assert body["incoming_count"] == 0


# ── Test 2 — Cache-Control and X-Cache-Status headers ────────────────────────


def test_response_has_cache_headers(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.hr.get_headcount",
        new=AsyncMock(return_value=_MOCK_DATA),
    ):
        r = client.get(_URL)

    assert r.status_code == 200
    assert "private, max-age=60" in r.headers.get("cache-control", "")
    assert r.headers.get("x-cache-status") == "fresh"


# ── Test 3 — X-Cache-Status reflects cached status ───────────────────────────


def test_cache_status_cached_reflected_in_header(client: TestClient) -> None:
    cached_data = {**_MOCK_DATA, "cache_status": "cached", "rpc_duration_ms": 0}
    with patch(
        "backend.api.v1.endpoints.hr.get_headcount",
        new=AsyncMock(return_value=cached_data),
    ):
        r = client.get(_URL)

    assert r.status_code == 200
    assert r.headers.get("x-cache-status") == "cached"


# ── Test 4 — OdooQueryError → 503 ────────────────────────────────────────────


def test_odoo_query_error_returns_503(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.hr.get_headcount",
        new=AsyncMock(side_effect=OdooQueryError("connection refused")),
    ):
        r = client.get(_URL)

    assert r.status_code == 503
    assert r.json()["error"]["code"] == "odoo_unavailable"


# ── Test 5 — Unexpected exception → 500 ──────────────────────────────────────


def test_unexpected_exception_returns_500(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.hr.get_headcount",
        new=AsyncMock(side_effect=RuntimeError("unexpected")),
    ):
        r = client.get(_URL)

    assert r.status_code == 500
    assert r.json()["error"]["code"] == "internal_error"


# ── Test 6 — Null department_id serialized as null (not dropped) ──────────────


def test_null_department_id_serialized_as_null(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.hr.get_headcount",
        new=AsyncMock(return_value=_MOCK_DATA),
    ):
        r = client.get(_URL)

    assert r.status_code == 200
    by_dept = r.json()["by_department"]
    null_rows = [row for row in by_dept if row["department_id"] is None]
    assert len(null_rows) == 1, "Null-dept bucket must be present in serialized response"
    assert null_rows[0]["count"] == 4


# ── Test 7 — Null job_id serialized as null (not dropped) ────────────────────


def test_null_job_id_serialized_as_null(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.hr.get_headcount",
        new=AsyncMock(return_value=_MOCK_DATA),
    ):
        r = client.get(_URL)

    assert r.status_code == 200
    by_job = r.json()["by_job"]
    null_rows = [row for row in by_job if row["job_id"] is None]
    assert len(null_rows) == 1, "Null-job bucket must be present in serialized response"
    assert null_rows[0]["count"] == 3


# ── Test 8 — 401 when no auth supplied ───────────────────────────────────────


def test_401_when_no_auth() -> None:
    """HR KPI endpoints must reject unauthenticated requests with 401.

    Added 2026-06-09 as part of the security hotfix that wired
    Depends(get_current_user) onto headcount, tenure-distribution,
    payroll-risk-dashboard, and department-cost.
    No service patch needed — auth is checked before the handler body runs.
    """
    c = TestClient(app, raise_server_exceptions=True)
    r = c.get(_URL)  # no session
    assert r.status_code == 401, (
        f"Expected 401 for unauthenticated HR KPI request, got {r.status_code}"
    )
