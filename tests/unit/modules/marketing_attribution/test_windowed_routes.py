"""
Endpoint tests for Marketing Attribution — GET /api/v1/marketing-attribution/windowed.

Uses FastAPI TestClient with get_attribution_overview_windowed patched — no Odoo
connection. Confirms the windowed response shape, the cache headers, the 422 contract
on an invalid custom range (shared with the campaign windowing), the 503/500 error
mapping, and the RBAC gate (401 / 403 / 200).
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

_URL = "/api/v1/marketing-attribution/windowed"

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

_MOCK_WINDOWED = {
    "buyers": [
        {
            "buyer_id": 101,
            "buyer_name": "Ahmed Aymen",
            "total_attributed": 336,
            "outcomes": [
                {"group": "جديد", "count": 95, "pct": 28.27},
                {"group": "مهتم", "count": 60, "pct": 17.86},
                {"group": "اشترى", "count": 0, "pct": 0.0},
                {"group": "بلا نتيجة", "count": 181, "pct": 53.87},
            ],
            "campaign_ids": [1],
        },
    ],
    "unattributed": {
        "lead_count": 168,
        "outcomes": [
            {"group": "جديد", "count": 100, "pct": 59.52},
            {"group": "مهتم", "count": 40, "pct": 23.81},
            {"group": "اشترى", "count": 0, "pct": 0.0},
            {"group": "بلا نتيجة", "count": 28, "pct": 16.67},
        ],
    },
    "total_leads_population": 1246,
    "total_attributed": 1078,
    "coverage_pct": 86.52,
    "window": "current",
    "is_custom_range": False,
    "window_months": 1,
    "window_start_month": "2026-06",
    "window_end_month": "2026-06",
    "legacy_days_excluded": ["2025-11-15", "2025-11-16", "2025-11-26"],
    "is_won_stage_names": ["Reservation"],
    "config_warnings": [],
    "integrity_alerts": [],
    "reference_date": "2026-06-18",
    "as_of": "2026-06-18T10:00:00+00:00",
    "cache_status": "fresh",
    "rpc_duration_ms": 109,
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


# ── 200 + shape ───────────────────────────────────────────────────────────────


def test_windowed_returns_200_and_keys(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.marketing_attribution.get_attribution_overview_windowed",
        new=AsyncMock(return_value=_MOCK_WINDOWED),
    ):
        r = client.get(_URL, params={"window": "current"})

    assert r.status_code == 200
    body = r.json()
    for key in (
        "buyers", "unattributed", "total_leads_population", "total_attributed",
        "coverage_pct", "window", "is_custom_range", "window_months",
        "window_start_month", "window_end_month", "legacy_days_excluded",
        "is_won_stage_names", "config_warnings", "integrity_alerts",
        "reference_date", "as_of", "cache_status", "rpc_duration_ms",
    ):
        assert key in body, f"Response missing key: {key!r}"
    assert body["coverage_pct"] == 86.52
    assert body["buyers"][0]["buyer_name"] == "Ahmed Aymen"
    assert body["unattributed"]["lead_count"] == 168


def test_windowed_has_cache_headers(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.marketing_attribution.get_attribution_overview_windowed",
        new=AsyncMock(return_value=_MOCK_WINDOWED),
    ):
        r = client.get(_URL)
    assert r.status_code == 200
    assert "private, max-age=60" in r.headers.get("cache-control", "")
    assert r.headers.get("x-cache-status") == "fresh"


# ── error mapping ─────────────────────────────────────────────────────────────


def test_windowed_invalid_range_returns_422(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.marketing_attribution.get_attribution_overview_windowed",
        new=AsyncMock(side_effect=InvalidTimelineRangeError("start_month is after end_month")),
    ):
        r = client.get(_URL, params={"start_month": "2026-06", "end_month": "2026-01"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_range"


def test_windowed_odoo_query_error_returns_503(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.marketing_attribution.get_attribution_overview_windowed",
        new=AsyncMock(side_effect=OdooQueryError("connection refused")),
    ):
        r = client.get(_URL)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "odoo_unavailable"


def test_windowed_unexpected_exception_returns_500(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.marketing_attribution.get_attribution_overview_windowed",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        r = client.get(_URL)
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "internal_error"


# ── RBAC gate ─────────────────────────────────────────────────────────────────


def test_windowed_401_when_unauthenticated() -> None:
    c = TestClient(app, raise_server_exceptions=True)
    r = c.get(_URL)
    assert r.status_code == 401


def test_windowed_403_without_module_grant() -> None:
    c = _client_with(_OTHER_MODULE_RECORD)
    try:
        r = c.get(_URL)
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "MODULE_ACCESS_DENIED"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        if hasattr(app.state, "user_repo"):
            del app.state.user_repo


def test_windowed_200_with_scoped_module_grant() -> None:
    c = _client_with(_SCOPED_RECORD)
    try:
        with patch(
            "backend.api.v1.endpoints.marketing_attribution.get_attribution_overview_windowed",
            new=AsyncMock(return_value=_MOCK_WINDOWED),
        ):
            r = c.get(_URL)
        assert r.status_code == 200
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        if hasattr(app.state, "user_repo"):
            del app.state.user_repo
