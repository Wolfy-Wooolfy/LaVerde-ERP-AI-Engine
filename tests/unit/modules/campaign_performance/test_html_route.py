"""
Route/RBAC tests for the Campaign Performance HTML page —
GET /campaign-performance/dashboard.

Mirrors the marketing-attribution HTML page gating: 302 (unauthenticated, → /login),
403 (authenticated but without the module grant), 200 (with the module). Uses dependency
overrides + a mocked user_repo and patches get_campaign_performance_overview, so no session
bootstrap or Odoo connection is required — same approach as test_routes.py for the JSON
endpoint.
"""

from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_current_user_html
from backend.auth.models import UserRecord
from backend.main import app

_URL = "/campaign-performance/dashboard"

# A user explicitly granted the campaign_performance module (non-admin, scoped).
_SCOPED_RECORD = UserRecord(
    username="scoped", password_hash="", modules=["campaign_performance"],
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
    "campaigns": [
        {
            "campaign_id": 1,
            "campaign_name": "FB-AY",
            "lead_count": 130,
            "outcomes": [
                {"group": "جديد", "count": 60, "pct": 46.15},
                {"group": "مهتم", "count": 30, "pct": 23.08},
                {"group": "اشترى", "count": 20, "pct": 15.38},
                {"group": "بلا نتيجة", "count": 20, "pct": 15.38},
            ],
            "attribution_status": "confirmed",
            "media_buyer_id": 101,
            "media_buyer_name": "Ahmed Aymen",
            "concentration": 100.0,
            "both_set_count": 100,
        },
        {
            "campaign_id": 2,
            "campaign_name": "Daima-Channel",
            "lead_count": 90,
            "outcomes": [
                {"group": "جديد", "count": 90, "pct": 100.0},
                {"group": "مهتم", "count": 0, "pct": 0.0},
                {"group": "اشترى", "count": 0, "pct": 0.0},
                {"group": "بلا نتيجة", "count": 0, "pct": 0.0},
            ],
            "attribution_status": "excluded_channel",
            "media_buyer_id": None,
            "media_buyer_name": None,
            "concentration": None,
            "both_set_count": 0,
        },
        {
            "campaign_id": 3,
            "campaign_name": "Organic-Push",
            "lead_count": 70,
            "outcomes": [
                {"group": "جديد", "count": 40, "pct": 57.14},
                {"group": "مهتم", "count": 10, "pct": 14.29},
                {"group": "اشترى", "count": 5, "pct": 7.14},
                {"group": "بلا نتيجة", "count": 15, "pct": 21.43},
            ],
            "attribution_status": "no_buyer",
            "media_buyer_id": None,
            "media_buyer_name": None,
            "concentration": None,
            "both_set_count": 0,
        },
    ],
    "long_tail": {
        "campaign_count": 117,
        "lead_count": 2200,
        "outcomes": [
            {"group": "جديد", "count": 1500, "pct": 68.18},
            {"group": "مهتم", "count": 300, "pct": 13.64},
            {"group": "اشترى", "count": 100, "pct": 4.55},
            {"group": "بلا نتيجة", "count": 300, "pct": 13.64},
        ],
    },
    "data_quality": {
        "junk_none": {
            "label": "None",
            "campaign_ids": [6],
            "lead_count": 17385,
            "outcomes": [
                {"group": "جديد", "count": 9000, "pct": 51.77},
                {"group": "مهتم", "count": 2000, "pct": 11.50},
                {"group": "اشترى", "count": 385, "pct": 2.21},
                {"group": "بلا نتيجة", "count": 6000, "pct": 34.51},
            ],
        },
        "no_campaign": {
            "label": "(no campaign)",
            "campaign_ids": [],
            "lead_count": 15,
            "outcomes": [
                {"group": "جديد", "count": 15, "pct": 100.0},
                {"group": "مهتم", "count": 0, "pct": 0.0},
                {"group": "اشترى", "count": 0, "pct": 0.0},
                {"group": "بلا نتيجة", "count": 0, "pct": 0.0},
            ],
        },
    },
    "min_lead_threshold": 50,
    "total_leads_population": 35000,
    "total_campaigns_with_leads": 212,
    "listed_campaign_count": 3,
    "is_won_stage_names": ["Reservation"],
    "config_warnings": [],
    "integrity_alerts": [],
    "reference_date": "2026-06-15",
    "as_of": "2026-06-15T10:00:00+00:00",
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
    """Authenticated but lacking campaign_performance → 403 (rendered as HTML)."""
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
            "backend.api.v1.endpoints.dashboard.get_campaign_performance_overview",
            new=AsyncMock(return_value=_MOCK_DATA),
        ):
            r = c.get(_URL)
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")
        body = r.text
        # Core content rendered from the mocked overview.
        assert "FB-AY" in body                          # top campaign name
        assert "Ahmed Aymen" in body                    # confirmed campaign's media buyer
        assert "Confirmed" in body                      # confirmed badge (EN default)
        assert "No media buyer" in body                 # no_buyer status label
        # Sidebar entry present for a user with the module.
        assert 'href="/campaign-performance/dashboard"' in body
    finally:
        _cleanup()


def test_200_renders_data_quality_long_tail_and_won_stages() -> None:
    """Data-quality strip, long-tail summary row, and footer (won stages) render —
    and the junk 'None' bucket is surfaced as data quality, NOT as a campaign row."""
    c = _client_with(_SCOPED_RECORD)
    try:
        with patch(
            "backend.api.v1.endpoints.dashboard.get_campaign_performance_overview",
            new=AsyncMock(return_value=_MOCK_DATA),
        ):
            r = c.get(_URL)
        assert r.status_code == 200
        body = r.text
        assert "Data quality" in body                   # data-quality section heading
        assert "17,385" in body                          # junk 'None' lead_count (thousands sep)
        assert "smaller campaigns" in body               # long-tail summary suffix (EN)
        assert "Reservation" in body                     # is_won_stage_names in footer
        # The junk bucket's raw "None" label must NOT leak in as a campaign list row.
        assert ">None<" not in body
    finally:
        _cleanup()


def test_200_surfaces_integrity_alerts_loudly() -> None:
    """A non-empty integrity_alerts list must be surfaced visibly (locked-decision drift)."""
    data = deepcopy(_MOCK_DATA)
    data["integrity_alerts"] = [
        "INTEGRITY: confirmed campaign 'FB-AY' (id=1) dominant buyer holds 80.0% — drift."
    ]
    c = _client_with(_SCOPED_RECORD)
    try:
        with patch(
            "backend.api.v1.endpoints.dashboard.get_campaign_performance_overview",
            new=AsyncMock(return_value=data),
        ):
            r = c.get(_URL)
        assert r.status_code == 200
        assert "Integrity alerts" in r.text
        assert "locked-decision drift" in r.text or "drift" in r.text
    finally:
        _cleanup()
