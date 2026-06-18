"""
Route/RBAC tests for the Campaign Performance TIMELINE HTML page (Level 2) —
GET /campaign-performance/timeline.

Mirrors test_html_route.py (the Level-1 page): 302 (unauthenticated → /login), 403
(authenticated but without the module grant), 200 (with the module — key content),
and the graceful redirect back to the list when campaign_id is missing/invalid or
resolves to no campaign. Uses dependency overrides + a mocked user_repo and patches
get_campaign_timeline, so no session bootstrap or Odoo connection is required.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_current_user_html
from backend.auth.models import UserRecord
from backend.main import app
from backend.modules.campaign_performance.services.timeline_service import (
    CampaignNotFoundError,
    InvalidTimelineRangeError,
)

_URL = "/campaign-performance/timeline"
_LIST_URL = "/campaign-performance/dashboard"

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
        "campaign_id": 10,
        "campaign_name": "FB-AY",
        "total_leads_in_window": 12,
        "attribution_status": "confirmed",
        "media_buyer_id": 101,
        "media_buyer_name": "Ahmed Aymen",
        "concentration": 100.0,
        "both_set_count": 27364,
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
        {"month": "2026-04", "lead_count": 0, "outcomes": _outcomes(), "maturation_state": "normal"},
        {"month": "2026-05", "lead_count": 10, "outcomes": _outcomes(new=8, intr=1, nores=1), "maturation_state": "neglected"},
        {"month": "2026-06", "lead_count": 5, "outcomes": _outcomes(new=4, won=1), "maturation_state": "too_early"},
    ],
    "window_months": 3,
    "trend_months": 6,
    "window_start_month": "2026-04",
    "window_end_month": "2026-06",
    "is_custom_range": False,
    "legacy_days_excluded": ["2025-11-15", "2025-11-16", "2025-11-26"],
    "reference_date": "2026-06-16",
    "as_of": "2026-06-16T10:00:00+00:00",
    "config_warnings": [],
    "integrity_alerts": [],
    "cache_status": "fresh",
    "rpc_duration_ms": 33,
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
    r = c.get(_URL, params={"campaign_id": 10})
    assert r.status_code == 302
    assert "/login" in r.headers.get("location", "")


# ── 403 without the module grant ───────────────────────────────────────────────


def test_403_without_module_grant() -> None:
    """Authenticated but lacking campaign_performance → 403."""
    c = _client_with(_OTHER_MODULE_RECORD)
    try:
        r = c.get(_URL, params={"campaign_id": 10})
        assert r.status_code == 403
    finally:
        _cleanup()


# ── 200 with the module grant ──────────────────────────────────────────────────


def test_200_renders_timeline_content() -> None:
    """A non-admin user granted the module gets the rendered timeline page."""
    c = _client_with(_SCOPED_RECORD)
    try:
        with patch(
            "backend.api.v1.endpoints.dashboard.get_campaign_timeline",
            new=AsyncMock(return_value=_MOCK_TIMELINE),
        ):
            r = c.get(_URL, params={"campaign_id": 10, "months": 3})
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")
        body = r.text
        # Header: campaign name, page label, buyer (confirmed state).
        assert "FB-AY" in body                               # campaign name
        assert "Performance over time" in body               # page label (EN default)
        assert "Ahmed Aymen" in body                         # confirmed buyer name
        assert "Confirmed" in body                           # confirmed badge
        # Preset pills link back to this campaign with each window.
        assert "/campaign-performance/timeline?campaign_id=10&months=6" in body
        # Period months + maturation badges render.
        assert "2026-06" in body
        assert "Neglected" in body                           # neglected month badge
        assert "Too early to assess" in body                 # too_early month badge
        # Footer caveat (real data, not "illustrative"): the human-readable
        # migration note replaces the old raw "N migration days excluded" counter.
        assert "a one-time batch from the previous system" in body
        assert "migration days excluded" not in body
    finally:
        _cleanup()


# ── graceful redirect on a missing / invalid / unknown campaign_id ──────────────


def test_missing_campaign_id_redirects_to_list() -> None:
    """No campaign_id → 302 back to the campaign list (no 422 / stack trace)."""
    c = _client_with(_SCOPED_RECORD)
    try:
        r = c.get(_URL)
        assert r.status_code == 302
        assert _LIST_URL in r.headers.get("location", "")
    finally:
        _cleanup()


def test_unknown_campaign_id_redirects_to_list() -> None:
    """A campaign_id resolving to no utm.campaign → 302 back to the list."""
    c = _client_with(_SCOPED_RECORD)
    try:
        with patch(
            "backend.api.v1.endpoints.dashboard.get_campaign_timeline",
            new=AsyncMock(side_effect=CampaignNotFoundError("no such campaign")),
        ):
            r = c.get(_URL, params={"campaign_id": 999999})
        assert r.status_code == 302
        assert _LIST_URL in r.headers.get("location", "")
    finally:
        _cleanup()


# ── custom range rendering + silent fallback ────────────────────────────────


_CUSTOM_TIMELINE = {
    **_MOCK_TIMELINE,
    "is_custom_range": True,
    "window_months": 4,
    "window_start_month": "2025-11",
    "window_end_month": "2026-02",
}


def test_custom_range_renders_with_custom_pill_active() -> None:
    """A custom range renders: the Custom pill is the active one, the month pickers
    are seeded with the active window, and the trend caption names the end month."""
    c = _client_with(_SCOPED_RECORD)
    try:
        with patch(
            "backend.api.v1.endpoints.dashboard.get_campaign_timeline",
            new=AsyncMock(return_value=_CUSTOM_TIMELINE),
        ) as m:
            r = c.get(
                _URL,
                params={"campaign_id": 10, "start_month": "2025-11", "end_month": "2026-02"},
            )
        assert r.status_code == 200
        body = r.text
        # Custom pill label + month dropdowns (selects, not native month inputs).
        assert "Custom" in body
        assert 'type="month"' not in body
        assert 'x-model="start"' in body
        assert 'x-model="end"' in body
        assert body.count("<select") == 2
        assert "Select month" in body                       # placeholder option
        # Alpine state is seeded from the active custom window (drives the selected option).
        assert "start: '2025-11'" in body
        assert "end: '2026-02'" in body
        # Conditional trend caption names the window end month (parallel to the preset copy).
        assert "Leads per month — 6 months ending" in body
        assert "2026-02" in body
        # The custom range was forwarded to the service.
        assert m.await_args.kwargs["start_month"] == "2025-11"
        assert m.await_args.kwargs["end_month"] == "2026-02"
    finally:
        _cleanup()


def test_invalid_custom_range_falls_back_to_preset() -> None:
    """A bad hand-edited custom URL does NOT 422 on the HTML page — it silently
    retries with the months preset and renders normally."""
    c = _client_with(_SCOPED_RECORD)
    try:
        with patch(
            "backend.api.v1.endpoints.dashboard.get_campaign_timeline",
            new=AsyncMock(side_effect=[InvalidTimelineRangeError("bad"), _MOCK_TIMELINE]),
        ) as m:
            r = c.get(
                _URL,
                params={"campaign_id": 10, "start_month": "2026-06", "end_month": "2026-01"},
            )
        assert r.status_code == 200
        assert "FB-AY" in r.text                       # rendered the preset result
        assert m.await_count == 2                       # custom attempt, then preset fallback
        # Second (fallback) call dropped the custom range.
        assert m.await_args.kwargs.get("start_month") is None
        assert m.await_args.kwargs.get("end_month") is None
    finally:
        _cleanup()
