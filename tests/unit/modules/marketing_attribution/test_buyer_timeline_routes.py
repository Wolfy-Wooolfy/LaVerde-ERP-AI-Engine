"""
Endpoint tests for Marketing Attribution — the per-media-buyer TIMELINE JSON route
GET /api/v1/marketing-attribution/buyer/{buyer_id}/timeline.

Uses FastAPI TestClient with get_buyer_timeline patched — no Odoo connection. Confirms
the response shape, the cache headers, the 404 (buyer_not_found) / 422 (invalid_range,
bad months, bad buyer_id) / 503 / 500 error mapping, and the RBAC gate (401 / 403 /
200). Mirrors test_windowed_routes.py (same module) and the campaign-timeline route
tests (same /timeline contract).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_current_user
from backend.auth.models import UserRecord
from backend.core.exceptions import OdooQueryError
from backend.main import app
from backend.modules.campaign_performance.services.timeline_service import (
    InvalidTimelineRangeError,
)
from backend.modules.marketing_attribution.services.buyer_timeline_service import (
    BuyerNotFoundError,
)

_PATCH = "backend.api.v1.endpoints.marketing_attribution.get_buyer_timeline"


def _url(buyer_id) -> str:
    return f"/api/v1/marketing-attribution/buyer/{buyer_id}/timeline"


_TESTADMIN_RECORD = UserRecord(
    username="testadmin", password_hash="", modules=["*"],
    is_admin=True, is_active=True,
    created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
)
_SCOPED_RECORD = UserRecord(
    username="scoped", password_hash="", modules=["marketing_attribution"],
    is_admin=False, is_active=True,
    created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
)
_OTHER_MODULE_RECORD = UserRecord(
    username="other", password_hash="", modules=["crm"],
    is_admin=False, is_active=True,
    created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
)


def _outcomes(new=0, intr=0, won=0, nores=0) -> list[dict]:
    total = new + intr + won + nores
    pct = lambda n: round(100.0 * n / total, 2) if total else 0.0
    return [
        {"group": "جديد", "count": new, "pct": pct(new)},
        {"group": "مهتم", "count": intr, "pct": pct(intr)},
        {"group": "اشترى", "count": won, "pct": pct(won)},
        {"group": "بلا نتيجة", "count": nores, "pct": pct(nores)},
    ]


_MOCK_TIMELINE = {
    "header": {
        "buyer_id": 101,
        "buyer_name": "Ahmed Aymen",
        "total_leads_in_window": 12,
        "attributing_campaign_count": 1,
        "attributing_campaign_ids": [1],
    },
    "trend": [
        {"month": "2026-01", "lead_count": 0},
        {"month": "2026-02", "lead_count": 2},
        {"month": "2026-03", "lead_count": 0},
        {"month": "2026-04", "lead_count": 3},
        {"month": "2026-05", "lead_count": 4},
        {"month": "2026-06", "lead_count": 5},
    ],
    "periods": [
        {"month": "2026-04", "lead_count": 3, "outcomes": _outcomes(new=3), "maturation_state": "neglected"},
        {"month": "2026-05", "lead_count": 4, "outcomes": _outcomes(new=4), "maturation_state": "too_early"},
        {"month": "2026-06", "lead_count": 5, "outcomes": _outcomes(new=4, won=1), "maturation_state": "too_early"},
    ],
    "window_months": 3,
    "trend_months": 6,
    "window_start_month": "2026-04",
    "window_end_month": "2026-06",
    "is_custom_range": False,
    "legacy_days_excluded": ["2025-11-15", "2025-11-16", "2025-11-26"],
    "reference_date": "2026-06-18",
    "as_of": "2026-06-18T10:00:00+00:00",
    "config_warnings": [],
    "integrity_alerts": [],
    "cache_status": "fresh",
    "rpc_duration_ms": 41,
}


def _client_with(record: UserRecord) -> TestClient:
    app.dependency_overrides[get_current_user] = lambda: record.username
    mock_repo = MagicMock()
    mock_repo.get_user.return_value = record
    app.state.user_repo = mock_repo
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture
def client() -> TestClient:
    c = _client_with(_TESTADMIN_RECORD)
    yield c
    app.dependency_overrides.pop(get_current_user, None)
    if hasattr(app.state, "user_repo"):
        del app.state.user_repo


# ── 200 + shape + cache headers ────────────────────────────────────────────────


def test_buyer_timeline_returns_200_and_shape(client: TestClient) -> None:
    with patch(_PATCH, new=AsyncMock(return_value=_MOCK_TIMELINE)):
        r = client.get(_url(101), params={"months": 3})

    assert r.status_code == 200
    body = r.json()
    for key in (
        "header", "trend", "periods", "window_months", "trend_months",
        "window_start_month", "window_end_month", "is_custom_range",
        "legacy_days_excluded", "reference_date", "as_of", "config_warnings",
        "integrity_alerts", "cache_status", "rpc_duration_ms",
    ):
        assert key in body, f"Response missing key: {key!r}"
    assert body["header"]["buyer_id"] == 101
    assert body["header"]["buyer_name"] == "Ahmed Aymen"
    assert body["header"]["attributing_campaign_ids"] == [1]
    assert len(body["trend"]) == 6
    assert [p["month"] for p in body["periods"]] == ["2026-04", "2026-05", "2026-06"]
    assert body["periods"][0]["outcomes"][0]["group"] == "جديد"
    assert "private, max-age=60" in r.headers.get("cache-control", "")
    assert r.headers.get("x-cache-status") == "fresh"


# ── error mapping ──────────────────────────────────────────────────────────────


def test_buyer_timeline_404_when_buyer_unknown(client: TestClient) -> None:
    with patch(_PATCH, new=AsyncMock(side_effect=BuyerNotFoundError("no such buyer"))):
        r = client.get(_url(999999))
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "buyer_not_found"


def test_buyer_timeline_503_on_odoo_error(client: TestClient) -> None:
    with patch(_PATCH, new=AsyncMock(side_effect=OdooQueryError("connection refused"))):
        r = client.get(_url(101))
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "odoo_unavailable"


def test_buyer_timeline_500_on_unexpected_error(client: TestClient) -> None:
    with patch(_PATCH, new=AsyncMock(side_effect=RuntimeError("boom"))):
        r = client.get(_url(101))
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "internal_error"


def test_buyer_timeline_422_on_bad_months(client: TestClient) -> None:
    """months out of [1, 12] is rejected by FastAPI query validation (no handler call)."""
    r = client.get(_url(101), params={"months": 99})
    assert r.status_code == 422


def test_buyer_timeline_422_on_bad_buyer_id(client: TestClient) -> None:
    """buyer_id <= 0 (Path gt=0) and a non-numeric buyer_id are 422 (no handler call)."""
    assert client.get(_url(0)).status_code == 422
    assert client.get(_url("abc")).status_code == 422


# ── custom range ───────────────────────────────────────────────────────────────


def test_buyer_timeline_custom_range_returns_200_and_forwards(client: TestClient) -> None:
    custom = {**_MOCK_TIMELINE, "is_custom_range": True,
              "window_start_month": "2025-11", "window_end_month": "2026-02"}
    with patch(_PATCH, new=AsyncMock(return_value=custom)) as m:
        r = client.get(
            _url(101),
            params={"start_month": "2025-11", "end_month": "2026-02"},
        )
    assert r.status_code == 200
    assert r.json()["is_custom_range"] is True
    assert m.await_args.kwargs["start_month"] == "2025-11"
    assert m.await_args.kwargs["end_month"] == "2026-02"


def test_buyer_timeline_custom_invalid_range_returns_422(client: TestClient) -> None:
    """An invalid custom range surfaces as 422 invalid_range. Validation happens BEFORE
    any RPC, so the REAL service path is exercised (no Odoo)."""
    for params in [
        {"start_month": "2026-06", "end_month": "2026-01"},  # start > end
        {"start_month": "2026-01"},                          # partial
        {"start_month": "2026-13", "end_month": "2026-01"},  # malformed
        {"start_month": "2000-01", "end_month": "2099-12"},  # over cap
    ]:
        r = client.get(_url(101), params=params)
        assert r.status_code == 422, params
        assert r.json()["error"]["code"] == "invalid_range", params


def test_buyer_timeline_custom_invalid_range_maps_service_error(client: TestClient) -> None:
    """Belt-and-suspenders: a service InvalidTimelineRangeError maps to 422."""
    with patch(_PATCH, new=AsyncMock(side_effect=InvalidTimelineRangeError("bad range"))):
        r = client.get(
            _url(101),
            params={"start_month": "2026-01", "end_month": "2026-02"},
        )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_range"


# ── RBAC gate ──────────────────────────────────────────────────────────────────


def test_buyer_timeline_401_when_unauthenticated() -> None:
    c = TestClient(app, raise_server_exceptions=True)
    r = c.get(_url(101))
    assert r.status_code == 401


def test_buyer_timeline_403_without_module_grant() -> None:
    c = _client_with(_OTHER_MODULE_RECORD)
    try:
        r = c.get(_url(101))
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "MODULE_ACCESS_DENIED"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        if hasattr(app.state, "user_repo"):
            del app.state.user_repo


def test_buyer_timeline_200_with_scoped_module_grant() -> None:
    c = _client_with(_SCOPED_RECORD)
    try:
        with patch(_PATCH, new=AsyncMock(return_value=_MOCK_TIMELINE)):
            r = c.get(_url(101))
        assert r.status_code == 200
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        if hasattr(app.state, "user_repo"):
            del app.state.user_repo
