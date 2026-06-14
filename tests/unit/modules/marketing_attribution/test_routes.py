"""
Endpoint tests for Marketing Attribution — GET /api/v1/marketing-attribution/overview.

Uses FastAPI TestClient with get_attribution_overview patched — no Odoo connection.
Confirms the RBAC gate: 401 unauthenticated, 403 without the module grant, 200 with it.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_current_user
from backend.auth.models import UserRecord
from backend.core.exceptions import OdooQueryError
from backend.main import app

_URL = "/api/v1/marketing-attribution/overview"

_TESTADMIN_RECORD = UserRecord(
    username="testadmin", password_hash="", modules=["*"],
    is_admin=True, is_active=True,
    created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
)

# A user explicitly granted the marketing_attribution module (non-admin, scoped).
_SCOPED_RECORD = UserRecord(
    username="scoped", password_hash="", modules=["marketing_attribution"],
    is_admin=False, is_active=True,
    created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
)

# A user WITHOUT the module (only crm) — must be 403.
_OTHER_MODULE_RECORD = UserRecord(
    username="other", password_hash="", modules=["crm"],
    is_admin=False, is_active=True,
    created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
)

_MOCK_DATA = {
    "buyers": [
        {
            "buyer_id": 101,
            "buyer_name": "Ahmed Aymen",
            "total_attributed": 130,
            "outcomes": [
                {"group": "جديد", "count": 60, "pct": 46.15},
                {"group": "مهتم", "count": 30, "pct": 23.08},
                {"group": "اشترى", "count": 20, "pct": 15.38},
                {"group": "بلا نتيجة", "count": 20, "pct": 15.38},
            ],
            "campaign_ids": [1],
        },
    ],
    "confirmed_campaigns": [
        {
            "campaign_id": 1, "campaign_name": "FB-AY",
            "dominant_buyer_id": 101, "dominant_buyer_name": "Ahmed Aymen",
            "concentration": 100.0, "both_set_count": 100, "lead_count": 130,
        },
    ],
    "pending_campaigns": [],
    "total_leads_population": 700,
    "total_attributed": 130,
    "attribution_pct": 18.57,
    "is_won_stage_names": ["Reservation"],
    "config_warnings": [],
    "integrity_alerts": [],
    "reference_date": "2026-06-14",
    "as_of": "2026-06-14T10:00:00+00:00",
    "cache_status": "fresh",
    "rpc_duration_ms": 42,
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


def test_overview_returns_200_and_keys(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.marketing_attribution.get_attribution_overview",
        new=AsyncMock(return_value=_MOCK_DATA),
    ):
        r = client.get(_URL)

    assert r.status_code == 200
    body = r.json()
    for key in (
        "buyers", "confirmed_campaigns", "pending_campaigns",
        "total_leads_population", "total_attributed", "attribution_pct",
        "is_won_stage_names", "config_warnings", "integrity_alerts",
        "reference_date", "as_of", "cache_status", "rpc_duration_ms",
    ):
        assert key in body, f"Response missing key: {key!r}"
    assert body["total_attributed"] == 130
    assert body["buyers"][0]["outcomes"][0]["group"] == "جديد"


def test_response_has_cache_headers(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.marketing_attribution.get_attribution_overview",
        new=AsyncMock(return_value=_MOCK_DATA),
    ):
        r = client.get(_URL)

    assert r.status_code == 200
    assert "private, max-age=60" in r.headers.get("cache-control", "")
    assert r.headers.get("x-cache-status") == "fresh"


# ── error mapping ─────────────────────────────────────────────────────────────


def test_odoo_query_error_returns_503(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.marketing_attribution.get_attribution_overview",
        new=AsyncMock(side_effect=OdooQueryError("connection refused")),
    ):
        r = client.get(_URL)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "odoo_unavailable"


def test_unexpected_exception_returns_500(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.marketing_attribution.get_attribution_overview",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        r = client.get(_URL)
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "internal_error"


# ── RBAC gate ─────────────────────────────────────────────────────────────────


def test_401_when_unauthenticated() -> None:
    """No session -> 401, before the handler body runs."""
    c = TestClient(app, raise_server_exceptions=True)
    r = c.get(_URL)
    assert r.status_code == 401


def test_403_without_module_grant() -> None:
    """Authenticated but lacking the marketing_attribution module -> 403."""
    c = _client_with(_OTHER_MODULE_RECORD)
    try:
        r = c.get(_URL)
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "MODULE_ACCESS_DENIED"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        if hasattr(app.state, "user_repo"):
            del app.state.user_repo


def test_200_with_scoped_module_grant() -> None:
    """A non-admin user explicitly granted the module is allowed."""
    c = _client_with(_SCOPED_RECORD)
    try:
        with patch(
            "backend.api.v1.endpoints.marketing_attribution.get_attribution_overview",
            new=AsyncMock(return_value=_MOCK_DATA),
        ):
            r = c.get(_URL)
        assert r.status_code == 200
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        if hasattr(app.state, "user_repo"):
            del app.state.user_repo
