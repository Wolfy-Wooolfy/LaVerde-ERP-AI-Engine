"""
Endpoint tests for HR KPI D — GET /api/v1/hr/kpi/department-cost.

Uses FastAPI TestClient with get_department_cost patched — no Odoo connection.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_current_user
from backend.core.exceptions import OdooQueryError
from backend.main import app

_URL = "/api/v1/hr/kpi/department-cost"

_MOCK_DATA = {
    "rows": [
        {
            "department_id": 5,
            "department_name": "Finance",
            "running_contract_count": 18,
            "total_wage": 270000.0,
        },
        {
            "department_id": 6,
            "department_name": "Sales",
            "running_contract_count": 14,
            "total_wage": 175000.0,
        },
        {
            "department_id": None,
            "department_name": "Other (small departments)",
            "running_contract_count": 19,
            "total_wage": 190000.0,
        },
    ],
    "grand_total_wage": 635000.0,
    "total_running_contracts": 51,
    "currency": "EGP",
    "basis": "monthly",
    "reference_date": "2026-06-07",
    "as_of": "2026-06-07T10:00:00+00:00",
    "cache_status": "fresh",
    "rpc_duration_ms": 87,
}

_MOCK_DATA_SUPPRESSED = {
    "rows": [
        {
            "department_id": 5,
            "department_name": "Finance",
            "running_contract_count": 18,
            "total_wage": 270000.0,
        },
        {
            "department_id": None,
            "department_name": "Other (small departments)",
            "running_contract_count": 2,
            "total_wage": None,   # suppressed: pool count 2 < k=3
        },
    ],
    "grand_total_wage": 285000.0,
    "total_running_contracts": 20,
    "currency": "EGP",
    "basis": "monthly",
    "reference_date": "2026-06-07",
    "as_of": "2026-06-07T10:00:00+00:00",
    "cache_status": "fresh",
    "rpc_duration_ms": 45,
}


@pytest.fixture
def client() -> TestClient:
    app.dependency_overrides[get_current_user] = lambda: "testadmin"
    c = TestClient(app, raise_server_exceptions=True)
    yield c
    app.dependency_overrides.pop(get_current_user, None)


# ── Test 1 — 200 + all top-level keys present ─────────────────────────────────


def test_department_cost_returns_200_and_all_keys(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.hr.get_department_cost",
        new=AsyncMock(return_value=_MOCK_DATA),
    ):
        r = client.get(_URL)

    assert r.status_code == 200
    body = r.json()
    for key in (
        "rows",
        "grand_total_wage",
        "total_running_contracts",
        "currency",
        "basis",
        "reference_date",
        "as_of",
        "cache_status",
        "rpc_duration_ms",
    ):
        assert key in body, f"Response missing key: {key!r}"
    assert body["currency"] == "EGP"
    assert body["basis"] == "monthly"
    assert body["total_running_contracts"] == 51


# ── Test 2 — rows is a list of dicts with the required fields ─────────────────


def test_rows_have_required_fields(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.hr.get_department_cost",
        new=AsyncMock(return_value=_MOCK_DATA),
    ):
        r = client.get(_URL)

    assert r.status_code == 200
    rows = r.json()["rows"]
    assert isinstance(rows, list)
    assert len(rows) == 3
    for row in rows:
        assert set(row.keys()) == {
            "department_id", "department_name",
            "running_contract_count", "total_wage",
        }, f"Unexpected row keys: {set(row.keys())}"


# ── Test 3 — grand_total and total_running_contracts present and correct ──────


def test_grand_total_and_total_running_contracts_present(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.hr.get_department_cost",
        new=AsyncMock(return_value=_MOCK_DATA),
    ):
        r = client.get(_URL)

    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["grand_total_wage"], float)
    assert body["grand_total_wage"] == 635000.0
    assert isinstance(body["total_running_contracts"], int)
    assert body["total_running_contracts"] == 51


# ── Test 4 — Cache-Control and X-Cache-Status headers present ─────────────────


def test_cache_control_and_x_cache_status_headers(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.hr.get_department_cost",
        new=AsyncMock(return_value=_MOCK_DATA),
    ):
        r = client.get(_URL)

    assert r.status_code == 200
    assert "private, max-age=60" in r.headers.get("cache-control", "")
    assert r.headers.get("x-cache-status") == "fresh"


# ── Test 5 — X-Cache-Status reflects cached status ───────────────────────────


def test_cache_status_cached_reflected_in_header(client: TestClient) -> None:
    cached_data = {**_MOCK_DATA, "cache_status": "cached", "rpc_duration_ms": 0}
    with patch(
        "backend.api.v1.endpoints.hr.get_department_cost",
        new=AsyncMock(return_value=cached_data),
    ):
        r = client.get(_URL)

    assert r.status_code == 200
    assert r.headers.get("x-cache-status") == "cached"


# ── Test 6 — OdooQueryError → 503 ────────────────────────────────────────────


def test_odoo_query_error_returns_503(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.hr.get_department_cost",
        new=AsyncMock(side_effect=OdooQueryError("connection refused")),
    ):
        r = client.get(_URL)

    assert r.status_code == 503
    assert r.json()["error"]["code"] == "odoo_unavailable"


# ── Test 7 — Unexpected exception → 500 ──────────────────────────────────────


def test_unexpected_exception_returns_500(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.hr.get_department_cost",
        new=AsyncMock(side_effect=RuntimeError("unexpected")),
    ):
        r = client.get(_URL)

    assert r.status_code == 500
    assert r.json()["error"]["code"] == "internal_error"


# ── Test 8 — Suppressed total_wage serializes as JSON null ───────────────────


def test_suppressed_total_wage_serializes_as_null(client: TestClient) -> None:
    """
    Guards the privacy-suppression path end-to-end: when the service returns
    a DepartmentCostRow with total_wage=None (k-anon pool count < 3),
    the response_model must serialize it as JSON null — not omit the field,
    not serialize as 0, not raise a validation error.
    """
    with patch(
        "backend.api.v1.endpoints.hr.get_department_cost",
        new=AsyncMock(return_value=_MOCK_DATA_SUPPRESSED),
    ):
        r = client.get(_URL)

    assert r.status_code == 200
    body = r.json()

    other = next(
        (row for row in body["rows"] if row["department_name"] == "Other (small departments)"),
        None,
    )
    assert other is not None, "Other row must be present in the serialized response"
    assert "total_wage" in other, "total_wage key must be present even when suppressed"
    assert other["total_wage"] is None, (
        f"Suppressed total_wage must serialize as JSON null; got {other['total_wage']!r}"
    )
    # grand_total_wage is always a float, never suppressed
    assert isinstance(body["grand_total_wage"], float)
    assert body["grand_total_wage"] == 285000.0
