"""
Endpoint tests for HR KPI C — GET /api/v1/hr/kpi/payroll-risk-dashboard.

Uses FastAPI TestClient with get_payroll_risk_dashboard patched — no Odoo connection.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.core.exceptions import OdooQueryError
from backend.main import app

_AUTH = ("testadmin", "testpass")
_URL = "/api/v1/hr/kpi/payroll-risk-dashboard"

_MOCK_DATA = {
    "buckets": [
        {"label": "active_without_contract", "count": 17},
        {"label": "expired",                 "count":  0},
        {"label": "expiring_45d",            "count": 114},
        {"label": "expiring_90d",            "count":  0},
        {"label": "expiring_135d",           "count":  0},
        {"label": "beyond_135d",             "count":  4},
        {"label": "open_ended",              "count":  1},
    ],
    "department_breakdown_expired":      [],
    "department_breakdown_expiring_45d": [
        {"department_id": 5, "department_name": "Finance", "count": 18},
        {"department_id": 6, "department_name": "Sales",   "count": 96},
    ],
    "orphan_contracts_count": 17,
    "total_active": 136,
    "reference_date": "2026-05-29",
    "as_of": "2026-05-29T10:00:00+00:00",
    "cache_status": "fresh",
    "rpc_duration_ms": 112,
}

_BUCKET_LABELS = [
    "active_without_contract",
    "expired",
    "expiring_45d",
    "expiring_90d",
    "expiring_135d",
    "beyond_135d",
    "open_ended",
]


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=True)


# ── Test 1 — 200 + all keys present ──────────────────────────────────────────


def test_payroll_risk_dashboard_returns_200_and_all_keys(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.hr.get_payroll_risk_dashboard",
        new=AsyncMock(return_value=_MOCK_DATA),
    ):
        r = client.get(_URL, auth=_AUTH)

    assert r.status_code == 200
    body = r.json()
    for key in (
        "buckets", "department_breakdown_expired", "department_breakdown_expiring_45d",
        "orphan_contracts_count", "total_active", "reference_date",
        "as_of", "cache_status", "rpc_duration_ms",
    ):
        assert key in body, f"Response missing key: {key!r}"
    assert body["total_active"] == 136
    assert body["orphan_contracts_count"] == 17


# ── Test 2 — 7 buckets always present in fixed order ─────────────────────────


def test_seven_buckets_present_in_fixed_order(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.hr.get_payroll_risk_dashboard",
        new=AsyncMock(return_value=_MOCK_DATA),
    ):
        r = client.get(_URL, auth=_AUTH)

    assert r.status_code == 200
    buckets = r.json()["buckets"]
    assert len(buckets) == 7
    for i, label in enumerate(_BUCKET_LABELS):
        assert buckets[i]["label"] == label, (
            f"Bucket #{i} must be {label!r}, got {buckets[i]['label']!r}"
        )


# ── Test 3 — Department breakdown fields present ──────────────────────────────


def test_department_breakdown_fields_present(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.hr.get_payroll_risk_dashboard",
        new=AsyncMock(return_value=_MOCK_DATA),
    ):
        r = client.get(_URL, auth=_AUTH)

    assert r.status_code == 200
    body = r.json()
    assert "department_breakdown_expired" in body
    assert "department_breakdown_expiring_45d" in body
    assert isinstance(body["department_breakdown_expired"], list)
    assert isinstance(body["department_breakdown_expiring_45d"], list)


# ── Test 4 — orphan_contracts_count present and non-negative ─────────────────


def test_orphan_contracts_count_present_and_non_negative(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.hr.get_payroll_risk_dashboard",
        new=AsyncMock(return_value=_MOCK_DATA),
    ):
        r = client.get(_URL, auth=_AUTH)

    assert r.status_code == 200
    body = r.json()
    assert "orphan_contracts_count" in body
    assert body["orphan_contracts_count"] >= 0


# ── Test 5 — Sanity invariant in serialized response ─────────────────────────


def test_sanity_invariant_holds_in_serialized_response(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.hr.get_payroll_risk_dashboard",
        new=AsyncMock(return_value=_MOCK_DATA),
    ):
        r = client.get(_URL, auth=_AUTH)

    assert r.status_code == 200
    body = r.json()
    bucket_sum = sum(b["count"] for b in body["buckets"])
    assert bucket_sum == body["total_active"], (
        f"sum(buckets) ({bucket_sum}) must == total_active ({body['total_active']})"
    )


# ── Test 6 — Cache-Control and X-Cache-Status headers ────────────────────────


def test_response_has_cache_headers(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.hr.get_payroll_risk_dashboard",
        new=AsyncMock(return_value=_MOCK_DATA),
    ):
        r = client.get(_URL, auth=_AUTH)

    assert r.status_code == 200
    assert "private, max-age=60" in r.headers.get("cache-control", "")
    assert r.headers.get("x-cache-status") == "fresh"


# ── Test 7 — X-Cache-Status reflects cached status ───────────────────────────


def test_cache_status_cached_reflected_in_header(client: TestClient) -> None:
    cached_data = {**_MOCK_DATA, "cache_status": "cached", "rpc_duration_ms": 0}
    with patch(
        "backend.api.v1.endpoints.hr.get_payroll_risk_dashboard",
        new=AsyncMock(return_value=cached_data),
    ):
        r = client.get(_URL, auth=_AUTH)

    assert r.status_code == 200
    assert r.headers.get("x-cache-status") == "cached"


# ── Test 8 — OdooQueryError → 503 ────────────────────────────────────────────


def test_odoo_query_error_returns_503(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.hr.get_payroll_risk_dashboard",
        new=AsyncMock(side_effect=OdooQueryError("connection refused")),
    ):
        r = client.get(_URL, auth=_AUTH)

    assert r.status_code == 503
    assert r.json()["error"]["code"] == "odoo_unavailable"


# ── Test 9 — Unexpected exception → 500 ──────────────────────────────────────


def test_unexpected_exception_returns_500(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.hr.get_payroll_risk_dashboard",
        new=AsyncMock(side_effect=RuntimeError("unexpected")),
    ):
        r = client.get(_URL, auth=_AUTH)

    assert r.status_code == 500
    assert r.json()["error"]["code"] == "internal_error"
