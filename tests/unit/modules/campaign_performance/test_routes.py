"""
Endpoint tests for Campaign Performance — GET /api/v1/campaign-performance/overview.

Uses FastAPI TestClient with get_campaign_performance_overview patched — no Odoo
connection. Confirms the RBAC gate: 401 unauthenticated, 403 without the module
grant, 200 with it.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_current_user
from backend.auth.models import UserRecord
from backend.core.exceptions import OdooQueryError
from backend.main import app
from backend.modules.campaign_performance.services.timeline_service import (
    CampaignNotFoundError,
    InvalidTimelineRangeError,
)

_URL = "/api/v1/campaign-performance/overview"
_WINDOWED_URL = "/api/v1/campaign-performance/windowed"
_TIMELINE_URL = "/api/v1/campaign-performance/timeline"

_TESTADMIN_RECORD = UserRecord(
    username="testadmin", password_hash="", modules=["*"],
    is_admin=True, is_active=True,
    created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
)

# A user explicitly granted the campaign_performance module (non-admin, scoped).
_SCOPED_RECORD = UserRecord(
    username="scoped", password_hash="", modules=["campaign_performance"],
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
    ],
    "long_tail": {
        "campaign_count": 1,
        "lead_count": 20,
        "outcomes": [
            {"group": "جديد", "count": 20, "pct": 100.0},
            {"group": "مهتم", "count": 0, "pct": 0.0},
            {"group": "اشترى", "count": 0, "pct": 0.0},
            {"group": "بلا نتيجة", "count": 0, "pct": 0.0},
        ],
    },
    "data_quality": {
        "junk_none": {
            "label": "None",
            "campaign_ids": [6],
            "lead_count": 55,
            "outcomes": [
                {"group": "جديد", "count": 30, "pct": 54.55},
                {"group": "مهتم", "count": 0, "pct": 0.0},
                {"group": "اشترى", "count": 0, "pct": 0.0},
                {"group": "بلا نتيجة", "count": 25, "pct": 45.45},
            ],
        },
        "no_campaign": None,
    },
    "min_lead_threshold": 50,
    "total_leads_population": 530,
    "total_campaigns_with_leads": 7,
    "listed_campaign_count": 5,
    "is_won_stage_names": ["Reservation"],
    "config_warnings": [],
    "integrity_alerts": [],
    "reference_date": "2026-06-15",
    "as_of": "2026-06-15T10:00:00+00:00",
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
        "backend.api.v1.endpoints.campaign_performance.get_campaign_performance_overview",
        new=AsyncMock(return_value=_MOCK_DATA),
    ):
        r = client.get(_URL)

    assert r.status_code == 200
    body = r.json()
    for key in (
        "campaigns", "long_tail", "data_quality", "min_lead_threshold",
        "total_leads_population", "total_campaigns_with_leads", "listed_campaign_count",
        "is_won_stage_names", "config_warnings", "integrity_alerts",
        "reference_date", "as_of", "cache_status", "rpc_duration_ms",
    ):
        assert key in body, f"Response missing key: {key!r}"
    assert body["total_leads_population"] == 530
    assert body["campaigns"][0]["outcomes"][0]["group"] == "جديد"
    assert body["campaigns"][0]["attribution_status"] == "confirmed"
    assert body["data_quality"]["junk_none"]["label"] == "None"


def test_response_has_cache_headers(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.campaign_performance.get_campaign_performance_overview",
        new=AsyncMock(return_value=_MOCK_DATA),
    ):
        r = client.get(_URL)

    assert r.status_code == 200
    assert "private, max-age=60" in r.headers.get("cache-control", "")
    assert r.headers.get("x-cache-status") == "fresh"


# ── error mapping ─────────────────────────────────────────────────────────────


def test_odoo_query_error_returns_503(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.campaign_performance.get_campaign_performance_overview",
        new=AsyncMock(side_effect=OdooQueryError("connection refused")),
    ):
        r = client.get(_URL)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "odoo_unavailable"


def test_unexpected_exception_returns_500(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.campaign_performance.get_campaign_performance_overview",
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
    """Authenticated but lacking the campaign_performance module -> 403."""
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
            "backend.api.v1.endpoints.campaign_performance.get_campaign_performance_overview",
            new=AsyncMock(return_value=_MOCK_DATA),
        ):
            r = c.get(_URL)
        assert r.status_code == 200
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        if hasattr(app.state, "user_repo"):
            del app.state.user_repo


# ── /windowed (Level 1, windowed list) ───────────────────────────────────────


_MOCK_WINDOWED = {
    "campaigns": [
        {
            "campaign_id": 1,
            "campaign_name": "FB-AY",
            "lead_count": 320,
            "outcomes": [
                {"group": "جديد", "count": 200, "pct": 62.5},
                {"group": "مهتم", "count": 60, "pct": 18.75},
                {"group": "اشترى", "count": 20, "pct": 6.25},
                {"group": "بلا نتيجة", "count": 40, "pct": 12.5},
            ],
            "attribution_status": "confirmed",
            "media_buyer_id": 101,
            "media_buyer_name": "Ahmed Aymen",
            "concentration": 100.0,
            "both_set_count": 100,
        },
    ],
    "data_quality": {"junk_none": None, "no_campaign": None},
    "total_leads_population": 320,
    "active_campaign_count": 1,
    "window": "last3",
    "is_custom_range": False,
    "window_months": 3,
    "window_start_month": "2026-04",
    "window_end_month": "2026-06",
    "legacy_days_excluded": ["2025-11-15", "2025-11-16", "2025-11-26"],
    "is_won_stage_names": ["Reservation"],
    "config_warnings": [],
    "integrity_alerts": [],
    "reference_date": "2026-06-16",
    "as_of": "2026-06-16T10:00:00+00:00",
    "cache_status": "fresh",
    "rpc_duration_ms": 25,
}


def test_windowed_returns_200_and_keys(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.campaign_performance.get_campaign_performance_windowed",
        new=AsyncMock(return_value=_MOCK_WINDOWED),
    ):
        r = client.get(_WINDOWED_URL, params={"window": "last3"})

    assert r.status_code == 200
    body = r.json()
    for key in (
        "campaigns", "data_quality", "total_leads_population", "active_campaign_count",
        "window", "is_custom_range", "window_months", "window_start_month",
        "window_end_month", "legacy_days_excluded", "is_won_stage_names",
        "config_warnings", "integrity_alerts", "reference_date", "as_of",
        "cache_status", "rpc_duration_ms",
    ):
        assert key in body, f"Response missing key: {key!r}"
    assert body["window"] == "last3"
    assert body["active_campaign_count"] == 1
    assert body["campaigns"][0]["campaign_name"] == "FB-AY"
    assert "private, max-age=60" in r.headers.get("cache-control", "")
    assert r.headers.get("x-cache-status") == "fresh"


def test_windowed_custom_range_forwarded(client: TestClient) -> None:
    custom = {**_MOCK_WINDOWED, "window": "custom", "is_custom_range": True,
              "window_start_month": "2025-10", "window_end_month": "2026-01"}
    with patch(
        "backend.api.v1.endpoints.campaign_performance.get_campaign_performance_windowed",
        new=AsyncMock(return_value=custom),
    ) as m:
        r = client.get(
            _WINDOWED_URL,
            params={"start_month": "2025-10", "end_month": "2026-01"},
        )
    assert r.status_code == 200
    assert r.json()["is_custom_range"] is True
    assert m.await_args.kwargs["start_month"] == "2025-10"
    assert m.await_args.kwargs["end_month"] == "2026-01"


def test_windowed_invalid_range_returns_422(client: TestClient) -> None:
    """Invalid window / custom range surfaces as 422 invalid_range. Validation
    happens BEFORE any RPC, so the REAL service path is exercised (no Odoo)."""
    for params in [
        {"start_month": "2026-06", "end_month": "2026-01"},   # start > end
        {"start_month": "2026-01"},                           # partial
        {"start_month": "2026-13", "end_month": "2026-01"},   # malformed
        {"start_month": "2000-01", "end_month": "2099-12"},   # over cap
        {"window": "bogus"},                                  # unknown preset
    ]:
        r = client.get(_WINDOWED_URL, params=params)
        assert r.status_code == 422, params
        assert r.json()["error"]["code"] == "invalid_range", params


def test_windowed_invalid_range_maps_service_error(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.campaign_performance.get_campaign_performance_windowed",
        new=AsyncMock(side_effect=InvalidTimelineRangeError("bad range")),
    ):
        r = client.get(_WINDOWED_URL, params={"window": "last3"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_range"


def test_windowed_503_on_odoo_error(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.campaign_performance.get_campaign_performance_windowed",
        new=AsyncMock(side_effect=OdooQueryError("connection refused")),
    ):
        r = client.get(_WINDOWED_URL, params={"window": "current"})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "odoo_unavailable"


def test_windowed_500_on_unexpected(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.campaign_performance.get_campaign_performance_windowed",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        r = client.get(_WINDOWED_URL, params={"window": "current"})
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "internal_error"


def test_windowed_401_when_unauthenticated() -> None:
    c = TestClient(app, raise_server_exceptions=True)
    r = c.get(_WINDOWED_URL, params={"window": "last3"})
    assert r.status_code == 401


def test_windowed_403_without_module_grant() -> None:
    c = _client_with(_OTHER_MODULE_RECORD)
    try:
        r = c.get(_WINDOWED_URL, params={"window": "last3"})
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "MODULE_ACCESS_DENIED"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        if hasattr(app.state, "user_repo"):
            del app.state.user_repo


# ── /timeline (Level 2) ─────────────────────────────────────────────────────


def _tl_outcomes(new=0, intr=0, won=0, nores=0) -> list[dict]:
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
        "both_set_count": 100,
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
        {"month": "2026-04", "lead_count": 3, "outcomes": _tl_outcomes(new=1, intr=1, nores=1), "maturation_state": "normal"},
        {"month": "2026-05", "lead_count": 4, "outcomes": _tl_outcomes(new=4), "maturation_state": "too_early"},
        {"month": "2026-06", "lead_count": 5, "outcomes": _tl_outcomes(new=5), "maturation_state": "too_early"},
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


def test_timeline_returns_200_and_shape(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.campaign_performance.get_campaign_timeline",
        new=AsyncMock(return_value=_MOCK_TIMELINE),
    ):
        r = client.get(_TIMELINE_URL, params={"campaign_id": 10, "months": 3})

    assert r.status_code == 200
    body = r.json()
    for key in (
        "header", "trend", "periods", "window_months", "trend_months",
        "window_start_month", "window_end_month", "legacy_days_excluded",
        "reference_date", "as_of", "config_warnings", "integrity_alerts",
        "cache_status", "rpc_duration_ms",
    ):
        assert key in body, f"Response missing key: {key!r}"
    assert body["header"]["campaign_id"] == 10
    assert body["header"]["attribution_status"] == "confirmed"
    assert len(body["trend"]) == 6
    assert [p["month"] for p in body["periods"]] == ["2026-04", "2026-05", "2026-06"]
    assert body["periods"][0]["outcomes"][0]["group"] == "جديد"
    # cache headers
    assert "private, max-age=60" in r.headers.get("cache-control", "")
    assert r.headers.get("x-cache-status") == "fresh"


def test_timeline_404_when_campaign_unknown(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.campaign_performance.get_campaign_timeline",
        new=AsyncMock(side_effect=CampaignNotFoundError("no such campaign")),
    ):
        r = client.get(_TIMELINE_URL, params={"campaign_id": 999999})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "campaign_not_found"


def test_timeline_503_on_odoo_error(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.campaign_performance.get_campaign_timeline",
        new=AsyncMock(side_effect=OdooQueryError("connection refused")),
    ):
        r = client.get(_TIMELINE_URL, params={"campaign_id": 10})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "odoo_unavailable"


def test_timeline_422_on_bad_months(client: TestClient) -> None:
    """months out of [1, 12] is rejected by FastAPI query validation (no handler call)."""
    r = client.get(_TIMELINE_URL, params={"campaign_id": 10, "months": 99})
    assert r.status_code == 422


# ── /timeline custom range (start_month / end_month) ─────────────────────────


def test_timeline_custom_range_returns_200(client: TestClient) -> None:
    custom = {**_MOCK_TIMELINE, "is_custom_range": True,
              "window_start_month": "2025-11", "window_end_month": "2026-02"}
    with patch(
        "backend.api.v1.endpoints.campaign_performance.get_campaign_timeline",
        new=AsyncMock(return_value=custom),
    ) as m:
        r = client.get(
            _TIMELINE_URL,
            params={"campaign_id": 10, "start_month": "2025-11", "end_month": "2026-02"},
        )
    assert r.status_code == 200
    assert r.json()["is_custom_range"] is True
    # The custom range is forwarded to the service.
    assert m.await_args.kwargs["start_month"] == "2025-11"
    assert m.await_args.kwargs["end_month"] == "2026-02"


def test_timeline_custom_invalid_range_returns_422(client: TestClient) -> None:
    """An invalid custom range surfaces as 422 invalid_range. Validation happens
    BEFORE any RPC, so the REAL service path is exercised (no Odoo)."""
    for params in [
        {"campaign_id": 10, "start_month": "2026-06", "end_month": "2026-01"},  # start>end
        {"campaign_id": 10, "start_month": "2026-01"},                          # partial
        {"campaign_id": 10, "start_month": "2026-13", "end_month": "2026-01"},  # malformed
        {"campaign_id": 10, "start_month": "2000-01", "end_month": "2099-12"},  # over cap
    ]:
        r = client.get(_TIMELINE_URL, params=params)
        assert r.status_code == 422, params
        assert r.json()["error"]["code"] == "invalid_range", params


def test_timeline_custom_invalid_range_maps_service_error(client: TestClient) -> None:
    """Belt-and-suspenders: if the service raises InvalidTimelineRangeError, the
    route maps it to 422 invalid_range (independent of the real validation path)."""
    with patch(
        "backend.api.v1.endpoints.campaign_performance.get_campaign_timeline",
        new=AsyncMock(side_effect=InvalidTimelineRangeError("bad range")),
    ):
        r = client.get(
            _TIMELINE_URL,
            params={"campaign_id": 10, "start_month": "2026-01", "end_month": "2026-02"},
        )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_range"


def test_timeline_422_on_missing_campaign_id(client: TestClient) -> None:
    r = client.get(_TIMELINE_URL)
    assert r.status_code == 422


def test_timeline_401_when_unauthenticated() -> None:
    """No session → 401, before the handler body runs (campaign_id provided so it is
    the auth gate, not query validation, that triggers)."""
    c = TestClient(app, raise_server_exceptions=True)
    r = c.get(_TIMELINE_URL, params={"campaign_id": 10})
    assert r.status_code == 401


def test_timeline_403_without_module_grant() -> None:
    """Authenticated but lacking the campaign_performance module → 403."""
    c = _client_with(_OTHER_MODULE_RECORD)
    try:
        r = c.get(_TIMELINE_URL, params={"campaign_id": 10})
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "MODULE_ACCESS_DENIED"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        if hasattr(app.state, "user_repo"):
            del app.state.user_repo
