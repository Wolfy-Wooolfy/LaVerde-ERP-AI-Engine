"""
Route/RBAC tests for the Marketing Attribution HTML page —
GET /marketing-attribution/dashboard.

Mirrors the HR HTML page gating: 302 (unauthenticated, → /login), 403 (authenticated but
without the module grant), 200 (with the module). Uses dependency overrides + a mocked
user_repo and patches get_attribution_overview, so no session bootstrap or Odoo connection
is required — same approach as test_routes.py for the JSON endpoint.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_current_user_html
from backend.auth.models import UserRecord
from backend.main import app

_URL = "/marketing-attribution/dashboard"

# A user explicitly granted the marketing_attribution module (non-admin, scoped).
_SCOPED_RECORD = UserRecord(
    username="scoped", password_hash="", modules=["marketing_attribution"],
    is_admin=False, is_active=True,
    created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
)

# A user WITHOUT the module (only hr) — must be 403.
_OTHER_MODULE_RECORD = UserRecord(
    username="other", password_hash="", modules=["hr"],
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
    "confirmed_campaigns": [],
    "pending_campaigns": [
        {
            "campaign_id": 9, "campaign_name": "IG-Promo",
            "dominant_buyer_id": 101, "dominant_buyer_name": "Ahmed Aymen",
            "concentration": 95.0, "both_set_count": 40, "lead_count": 55,
        },
    ],
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
    app.dependency_overrides[get_current_user_html] = lambda: record.username
    mock_repo = MagicMock()
    mock_repo.get_user.return_value = record
    app.state.user_repo = mock_repo
    return TestClient(app, raise_server_exceptions=True, follow_redirects=False)


def _cleanup() -> None:
    app.dependency_overrides.pop(get_current_user_html, None)
    if hasattr(app.state, "user_repo"):
        del app.state.user_repo


# ── 302 unauthenticated ────────────────────────────────────────────────────────


def test_unauthenticated_redirects_to_login() -> None:
    """No session → 302 to /login, before the handler body runs."""
    c = TestClient(app, raise_server_exceptions=True, follow_redirects=False)
    r = c.get(_URL)
    assert r.status_code == 302
    assert "/login" in r.headers.get("location", "")


# ── 403 without the module grant ───────────────────────────────────────────────


def test_403_without_module_grant() -> None:
    """Authenticated but lacking marketing_attribution → 403 (rendered as HTML)."""
    c = _client_with(_OTHER_MODULE_RECORD)
    try:
        r = c.get(_URL, headers={"Accept": "text/html"})
        assert r.status_code == 403
        assert "text/html" in r.headers.get("content-type", "")
    finally:
        _cleanup()


# ── 200 with the module grant ──────────────────────────────────────────────────


def test_200_with_scoped_module_grant() -> None:
    """A non-admin user granted the module gets the rendered page."""
    c = _client_with(_SCOPED_RECORD)
    try:
        with patch(
            "backend.api.v1.endpoints.dashboard.get_attribution_overview",
            new=AsyncMock(return_value=_MOCK_DATA),
        ):
            r = c.get(_URL)
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")
        body = r.text
        # Core content rendered from the mocked overview.
        assert "Ahmed Aymen" in body
        assert "18.6" in body                       # attribution_pct, round(1)
        assert "Media Buyer" in body                # buyer role label (EN default)
        # Sidebar entry present for a user with the module.
        assert 'href="/marketing-attribution/dashboard"' in body
    finally:
        _cleanup()


def test_200_renders_pending_and_won_stages() -> None:
    """Data-quality strip (pending channels) and footer (won stages) render."""
    c = _client_with(_SCOPED_RECORD)
    try:
        with patch(
            "backend.api.v1.endpoints.dashboard.get_attribution_overview",
            new=AsyncMock(return_value=_MOCK_DATA),
        ):
            r = c.get(_URL)
        assert r.status_code == 200
        body = r.text
        assert "IG-Promo" in body          # pending campaign name
        assert "Reservation" in body       # is_won_stage_names in footer
    finally:
        _cleanup()
