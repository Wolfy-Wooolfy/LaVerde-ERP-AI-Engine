"""
Endpoint integration tests for Collections KPI endpoints.

GET /api/v1/collections/kpi/late-uncollected       — KPI 2
GET /api/v1/collections/kpi/total-portfolio-value  — KPI 1

Uses FastAPI TestClient with service functions patched — no Odoo connection.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.core.exceptions import OdooQueryError
from backend.main import app

_AUTH = ("testadmin", "testpass")
_URL = "/api/v1/collections/kpi/late-uncollected"

_MOCK_DATA = {
    "value": 312_604_879.40,
    "currency": "EGP",
    "record_count": 1971,
    "as_of": "2026-05-16T10:00:00+00:00",
    "cache_status": "fresh",
    "rpc_duration_ms": 42,
    "domain": [
        ["state", "=", "post"],
        ["payment_state", "in", ["unpaid", "partial"]],
        ["date", "<", "2026-05-16"],
    ],
}


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=True)


# ── Test 8a — 200 + JSON shape ────────────────────────────────────────────────


def test_get_returns_200_and_all_keys(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_late_uncollected",
        new=AsyncMock(return_value=_MOCK_DATA),
    ):
        r = client.get(_URL, auth=_AUTH)

    assert r.status_code == 200
    body = r.json()
    for key in ("value", "currency", "record_count", "as_of",
                "cache_status", "rpc_duration_ms", "domain"):
        assert key in body, f"Response missing key: {key!r}"


# ── Test 8b — Response headers ────────────────────────────────────────────────


def test_response_has_cache_control_and_x_cache_status(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_late_uncollected",
        new=AsyncMock(return_value=_MOCK_DATA),
    ):
        r = client.get(_URL, auth=_AUTH)

    assert r.status_code == 200
    assert "private" in r.headers.get("cache-control", "")
    assert "max-age=60" in r.headers.get("cache-control", "")
    assert r.headers.get("x-cache-status") == "fresh"


def test_x_cache_status_reflects_cached_when_served_from_cache(
    client: TestClient,
) -> None:
    cached_data = {**_MOCK_DATA, "cache_status": "cached", "rpc_duration_ms": 0}
    with patch(
        "backend.api.v1.endpoints.collections.get_late_uncollected",
        new=AsyncMock(return_value=cached_data),
    ):
        r = client.get(_URL, auth=_AUTH)

    assert r.headers.get("x-cache-status") == "cached"


# ── Test 8c — 503 on OdooQueryError ──────────────────────────────────────────


def test_odoo_unavailable_returns_503_with_error_shape(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_late_uncollected",
        new=AsyncMock(side_effect=OdooQueryError("Odoo is down")),
    ):
        r = client.get(_URL, auth=_AUTH)

    assert r.status_code == 503
    body = r.json()
    assert "error" in body
    assert body["error"]["code"] == "odoo_unavailable"
    assert isinstance(body["error"]["message"], str)


# ── Test 8d — 405 on POST ─────────────────────────────────────────────────────


def test_post_returns_405(client: TestClient) -> None:
    r = client.post(_URL, auth=_AUTH)
    assert r.status_code == 405


# ══════════════════════════════════════════════════════════════════════════════
# KPI 1 — Total Portfolio Value endpoint tests
# ══════════════════════════════════════════════════════════════════════════════

_URL_KPI1 = "/api/v1/collections/kpi/total-portfolio-value"

_MOCK_DATA_KPI1 = {
    "value": 6_123_549_625.23,
    "currency": "EGP",
    "record_count": 42_443,
    "as_of": "2026-05-16T15:54:23+00:00",
    "cache_status": "fresh",
    "rpc_duration_ms": 609,
    "domain": [["state", "=", "post"]],
}


# ── Test K1-8a — 200 + JSON shape ────────────────────────────────────────────


def test_kpi1_get_returns_200_and_all_keys(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_total_portfolio_value",
        new=AsyncMock(return_value=_MOCK_DATA_KPI1),
    ):
        r = client.get(_URL_KPI1, auth=_AUTH)

    assert r.status_code == 200
    body = r.json()
    for key in ("value", "currency", "record_count", "as_of",
                "cache_status", "rpc_duration_ms", "domain"):
        assert key in body, f"Response missing key: {key!r}"


# ── Test K1-8b — Response headers ────────────────────────────────────────────


def test_kpi1_response_has_cache_control_and_x_cache_status(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_total_portfolio_value",
        new=AsyncMock(return_value=_MOCK_DATA_KPI1),
    ):
        r = client.get(_URL_KPI1, auth=_AUTH)

    assert r.status_code == 200
    assert "private" in r.headers.get("cache-control", "")
    assert "max-age=60" in r.headers.get("cache-control", "")
    assert r.headers.get("x-cache-status") == "fresh"


# ── Test K1-8c — 503 on OdooQueryError ───────────────────────────────────────


def test_kpi1_odoo_unavailable_returns_503(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_total_portfolio_value",
        new=AsyncMock(side_effect=OdooQueryError("Odoo is down")),
    ):
        r = client.get(_URL_KPI1, auth=_AUTH)

    assert r.status_code == 503
    body = r.json()
    assert "error" in body
    assert body["error"]["code"] == "odoo_unavailable"
    assert isinstance(body["error"]["message"], str)


# ── Test K1-8d — 405 on POST ──────────────────────────────────────────────────


def test_kpi1_post_returns_405(client: TestClient) -> None:
    r = client.post(_URL_KPI1, auth=_AUTH)
    assert r.status_code == 405
