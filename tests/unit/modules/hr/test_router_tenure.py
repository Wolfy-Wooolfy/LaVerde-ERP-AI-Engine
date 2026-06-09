"""
Endpoint tests for HR KPI B — GET /api/v1/hr/kpi/tenure-distribution.

Uses FastAPI TestClient with get_tenure_distribution patched — no Odoo connection.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_current_user
from backend.core.exceptions import OdooQueryError
from backend.main import app

_URL = "/api/v1/hr/kpi/tenure-distribution"

_MOCK_DATA = {
    "bands": [
        {"band": "lt1y",   "count": 12},
        {"band": "y1_3",   "count": 45},
        {"band": "y3_5",   "count": 38},
        {"band": "y5_10",  "count": 30},
        {"band": "y10plus","count":  8},
    ],
    "missing_date_count": 3,
    "total_employed": 136,
    "reference_date": "2026-05-29",
    "as_of": "2026-05-29T10:00:00+00:00",
    "cache_status": "fresh",
    "rpc_duration_ms": 74,
}


@pytest.fixture
def client() -> TestClient:
    app.dependency_overrides[get_current_user] = lambda: "testadmin"
    c = TestClient(app, raise_server_exceptions=True)
    yield c
    app.dependency_overrides.pop(get_current_user, None)


# ── Test 1 — 200 + all keys present ──────────────────────────────────────────


def test_tenure_distribution_returns_200_and_all_keys(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.hr.get_tenure_distribution",
        new=AsyncMock(return_value=_MOCK_DATA),
    ):
        r = client.get(_URL)

    assert r.status_code == 200
    body = r.json()
    for key in ("bands", "missing_date_count", "total_employed",
                "reference_date", "as_of", "cache_status", "rpc_duration_ms"):
        assert key in body, f"Response missing key: {key!r}"
    assert body["total_employed"] == 136
    assert body["missing_date_count"] == 3


# ── Test 2 — Cache-Control and X-Cache-Status headers ────────────────────────


def test_response_has_cache_headers(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.hr.get_tenure_distribution",
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
        "backend.api.v1.endpoints.hr.get_tenure_distribution",
        new=AsyncMock(return_value=cached_data),
    ):
        r = client.get(_URL)

    assert r.status_code == 200
    assert r.headers.get("x-cache-status") == "cached"


# ── Test 4 — OdooQueryError → 503 ────────────────────────────────────────────


def test_odoo_query_error_returns_503(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.hr.get_tenure_distribution",
        new=AsyncMock(side_effect=OdooQueryError("connection refused")),
    ):
        r = client.get(_URL)

    assert r.status_code == 503
    assert r.json()["error"]["code"] == "odoo_unavailable"


# ── Test 5 — Unexpected exception → 500 ──────────────────────────────────────


def test_unexpected_exception_returns_500(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.hr.get_tenure_distribution",
        new=AsyncMock(side_effect=RuntimeError("unexpected")),
    ):
        r = client.get(_URL)

    assert r.status_code == 500
    assert r.json()["error"]["code"] == "internal_error"


# ── Test 6 — 5 bands always present in serialized response ───────────────────


def test_five_bands_present_in_serialized_response(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.hr.get_tenure_distribution",
        new=AsyncMock(return_value=_MOCK_DATA),
    ):
        r = client.get(_URL)

    assert r.status_code == 200
    bands = r.json()["bands"]
    assert len(bands) == 5
    band_labels = [b["band"] for b in bands]
    assert band_labels == ["lt1y", "y1_3", "y3_5", "y5_10", "y10plus"]


# ── Test 7 — 5 bands present even when some counts are zero ──────────────────


def test_five_bands_present_when_some_counts_are_zero(client: TestClient) -> None:
    sparse_data = {
        **_MOCK_DATA,
        "bands": [
            {"band": "lt1y",   "count": 0},
            {"band": "y1_3",   "count": 0},
            {"band": "y3_5",   "count": 0},
            {"band": "y5_10",  "count": 136},
            {"band": "y10plus","count": 0},
        ],
        "missing_date_count": 0,
        "total_employed": 136,
    }
    with patch(
        "backend.api.v1.endpoints.hr.get_tenure_distribution",
        new=AsyncMock(return_value=sparse_data),
    ):
        r = client.get(_URL)

    assert r.status_code == 200
    bands = r.json()["bands"]
    assert len(bands) == 5
    assert all("band" in b and "count" in b for b in bands)


# ── Test 8 — missing_date_count present and non-negative ─────────────────────


def test_missing_date_count_present_and_non_negative(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.hr.get_tenure_distribution",
        new=AsyncMock(return_value=_MOCK_DATA),
    ):
        r = client.get(_URL)

    assert r.status_code == 200
    body = r.json()
    assert "missing_date_count" in body
    assert body["missing_date_count"] >= 0


# ── Test 9 — Sanity invariant holds in serialized response ───────────────────


def test_sanity_invariant_holds_in_serialized_response(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.hr.get_tenure_distribution",
        new=AsyncMock(return_value=_MOCK_DATA),
    ):
        r = client.get(_URL)

    assert r.status_code == 200
    body = r.json()
    band_sum = sum(b["count"] for b in body["bands"])
    assert band_sum + body["missing_date_count"] == body["total_employed"], (
        f"band_sum ({band_sum}) + missing ({body['missing_date_count']}) "
        f"must == total_employed ({body['total_employed']})"
    )
