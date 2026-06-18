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
    """A non-admin user granted the module gets the rendered page (all-time path)."""
    c = _client_with(_SCOPED_RECORD)
    try:
        with patch(
            "backend.api.v1.endpoints.dashboard.get_attribution_overview",
            new=AsyncMock(return_value=_MOCK_DATA),
        ):
            r = c.get(_URL, params={"window": "all"})   # all-time path → the overview fn
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
    """Data-quality strip (pending channels) and footer (won stages) render (all-time)."""
    c = _client_with(_SCOPED_RECORD)
    try:
        with patch(
            "backend.api.v1.endpoints.dashboard.get_attribution_overview",
            new=AsyncMock(return_value=_MOCK_DATA),
        ):
            r = c.get(_URL, params={"window": "all"})   # pending only exists in the all-time view
        assert r.status_code == 200
        body = r.text
        assert "IG-Promo" in body          # pending campaign name
        assert "Reservation" in body       # is_won_stage_names in footer
    finally:
        _cleanup()


# ── default (windowed) path + the window switcher ─────────────────────────────


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
    "window": "last3",
    "is_custom_range": False,
    "window_months": 3,
    "window_start_month": "2026-04",
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


def test_default_page_is_windowed_and_renders_switcher() -> None:
    """No query params → the locked default (last 3 months, windowed). The window
    switcher + windowed coverage + the unattributed row render; the buyer row is shown
    and the all-time-only 'pending' block does not appear."""
    c = _client_with(_SCOPED_RECORD)
    try:
        with patch(
            "backend.api.v1.endpoints.dashboard.get_attribution_overview_windowed",
            new=AsyncMock(return_value=_MOCK_WINDOWED),
        ):
            r = c.get(_URL)                              # no params → DEFAULT_WINDOW (last3)
        assert r.status_code == 200
        body = r.text
        # Window switcher present (presets reuse the campaign window keys).
        assert "Time window" in body                    # campperf_window_label
        assert "All-time" in body                       # campperf_window_all pill
        assert "Last 3 months" in body                  # campperf_window_last3 pill
        assert "migration excluded" in body             # windowed coverage caption tag
        # Windowed coverage + buyer + unattributed honesty row.
        assert "86.5" in body                           # coverage_pct, round(1)
        assert "Ahmed Aymen" in body
        assert "Unattributed this window" in body       # mktattr_window_unattributed_title
        # The all-time-only "pending channels" block must NOT render in a windowed view.
        assert "awaiting your confirmation" not in body
    finally:
        _cleanup()


def test_invalid_custom_range_falls_back_to_default() -> None:
    """A hand-edited invalid custom range must NOT 500 — the HTML route silently falls
    back to the default windowed preset (never 422s a hand-edited URL)."""
    from backend.modules.campaign_performance.services.timeline_service import (
        InvalidTimelineRangeError,
    )

    calls = {"n": 0}

    async def _fake(**kwargs):
        calls["n"] += 1
        if kwargs.get("start_month") or kwargs.get("end_month"):
            raise InvalidTimelineRangeError("start_month is after end_month")
        return _MOCK_WINDOWED

    c = _client_with(_SCOPED_RECORD)
    try:
        with patch(
            "backend.api.v1.endpoints.dashboard.get_attribution_overview_windowed",
            new=_fake,
        ):
            r = c.get(_URL, params={"start_month": "2026-06", "end_month": "2026-01"})
        assert r.status_code == 200
        assert calls["n"] == 2                           # bad range, then default fallback
        assert "Ahmed Aymen" in r.text
    finally:
        _cleanup()
