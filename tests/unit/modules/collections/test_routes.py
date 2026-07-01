"""
Endpoint integration tests for Collections KPI endpoints.

GET /api/v1/collections/kpi/late-uncollected       — KPI 2
GET /api/v1/collections/kpi/total-portfolio-value  — KPI 1
GET /api/v1/collections/kpi/pending-check-exposure — KPI 3
GET /api/v1/collections/kpi/collection-trend-6m    — KPI 6
GET /api/v1/collections/kpi/expected-forecast      — KPI 7

Uses FastAPI TestClient with service functions patched — no Odoo connection.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_current_user
from backend.auth.models import UserRecord
from backend.core.exceptions import OdooQueryError, ProjectNotFoundError
from backend.main import app
from backend.modules.collections.schemas import (
    ExpectedCollectionsForecastResponse,
    LateUncollectedResponse,
)

_TESTADMIN_RECORD = UserRecord(
    username="testadmin", password_hash="", modules=["*"],
    is_admin=True, is_active=True,
    created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
)

_URL = "/api/v1/collections/kpi/late-uncollected"

_MOCK_DATA = {
    "value": 312_604_879.40,
    "currency": "EGP",
    "record_count": 1971,
    "cheques_in_pipeline": 1_929_000.0,
    "cheques_record_count": None,
    "drill_down_domain": [
        ["state", "=", "post"],
        ["payment_state", "in", ["unpaid", "partial"]],
        ["date", "<", "2026-05-16"],
    ],
    "cheques_drill_down_domain": None,
    "as_of": "2026-05-16T10:00:00+00:00",
    "cache_status": "fresh",
    "rpc_duration_ms": 42,
    "domain": [
        ["state", "=", "post"],
        ["payment_state", "in", ["unpaid", "partial"]],
        ["date", "<", "2026-05-16"],
    ],
    "data_quality_warning": None,
}


@pytest.fixture
def client() -> TestClient:
    app.dependency_overrides[get_current_user] = lambda: "testadmin"
    mock_repo = MagicMock()
    mock_repo.get_user.return_value = _TESTADMIN_RECORD
    app.state.user_repo = mock_repo
    c = TestClient(app, raise_server_exceptions=True)
    yield c
    app.dependency_overrides.pop(get_current_user, None)
    if hasattr(app.state, "user_repo"):
        del app.state.user_repo


# ── Test 8a — 200 + JSON shape ────────────────────────────────────────────────


def test_get_returns_200_and_all_keys(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_late_uncollected",
        new=AsyncMock(return_value=_MOCK_DATA),
    ):
        r = client.get(_URL)

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
        r = client.get(_URL)

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
        r = client.get(_URL)

    assert r.headers.get("x-cache-status") == "cached"


# ── Test 8c — 503 on OdooQueryError ──────────────────────────────────────────


def test_odoo_unavailable_returns_503_with_error_shape(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_late_uncollected",
        new=AsyncMock(side_effect=OdooQueryError("Odoo is down")),
    ):
        r = client.get(_URL)

    assert r.status_code == 503
    body = r.json()
    assert "error" in body
    assert body["error"]["code"] == "odoo_unavailable"
    assert isinstance(body["error"]["message"], str)


# ── Test 8d — 405 on POST ─────────────────────────────────────────────────────


def test_post_returns_405(client: TestClient) -> None:
    r = client.post(_URL)
    assert r.status_code == 405


# ── Test 8e — response_model= is enforced on the success path ────────────────


def test_kpi2_endpoint_response_model_validates_success_shape(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_late_uncollected",
        new=AsyncMock(return_value=_MOCK_DATA),
    ):
        r = client.get(_URL)

    assert r.status_code == 200
    # Must not raise — confirms response_model= is active on the success path
    LateUncollectedResponse(**r.json())


# ── Test 8f — strict shape includes all 12 keys + cheques constraints ─────────


def test_kpi2_endpoint_strict_shape_includes_new_fields(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_late_uncollected",
        new=AsyncMock(return_value=_MOCK_DATA),
    ):
        r = client.get(_URL)

    assert r.status_code == 200
    body = r.json()

    expected_keys = {
        "value", "currency", "record_count", "as_of",
        "cache_status", "rpc_duration_ms", "domain",
        "cheques_in_pipeline", "cheques_record_count",
        "drill_down_domain", "cheques_drill_down_domain",
        "data_quality_warning",
    }
    assert set(body.keys()) == expected_keys

    assert isinstance(body["cheques_in_pipeline"], float)
    assert body["cheques_in_pipeline"] >= 0
    assert body["cheques_record_count"] is None

    ddomain = body["drill_down_domain"]
    assert len(ddomain) == 3
    assert ddomain[0] == ["state", "=", "post"]
    assert ddomain[1] == ["payment_state", "in", ["unpaid", "partial"]]
    assert ddomain[2][0] == "date"
    assert ddomain[2][1] == "<"

    assert body["cheques_drill_down_domain"] is None


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
        r = client.get(_URL_KPI1)

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
        r = client.get(_URL_KPI1)

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
        r = client.get(_URL_KPI1)

    assert r.status_code == 503
    body = r.json()
    assert "error" in body
    assert body["error"]["code"] == "odoo_unavailable"
    assert isinstance(body["error"]["message"], str)


# ── Test K1-8d — 405 on POST ──────────────────────────────────────────────────


def test_kpi1_post_returns_405(client: TestClient) -> None:
    r = client.post(_URL_KPI1)
    assert r.status_code == 405


# ══════════════════════════════════════════════════════════════════════════════
# KPI 5 — Late Uncollected by Project endpoint tests
# ══════════════════════════════════════════════════════════════════════════════

_URL_KPI5 = "/api/v1/collections/kpi/late-uncollected-by-project"

_MOCK_DATA_KPI5 = {
    "projects": [
        {"project_id": 1, "project_name": "New Capital", "late_uncollected": 164_017_258.40, "record_count": 1472},
        {"project_id": 2, "project_name": "Cassette",    "late_uncollected": 151_019_442.00, "record_count": 488},
        {"project_id": 3, "project_name": "La puerta",   "late_uncollected":   3_589_500.00, "record_count": 21},
    ],
    "total_late_uncollected": 318_626_200.40,
    "total_record_count": 1981,
    "currency": "EGP",
    "as_of": "2026-05-16T16:51:26+00:00",
    "cache_status": "fresh",
    "rpc_duration_ms": 79,
    "domain": [
        ["state", "=", "post"],
        ["payment_state", "in", ["unpaid", "partial"]],
        ["date", "<", "2026-05-16"],
    ],
}


# ── Test K5-8a — 200 + JSON shape ────────────────────────────────────────────


def test_kpi5_get_returns_200_and_all_keys(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_late_uncollected_by_project",
        new=AsyncMock(return_value=_MOCK_DATA_KPI5),
    ):
        r = client.get(_URL_KPI5)

    assert r.status_code == 200
    body = r.json()
    for key in ("projects", "total_late_uncollected", "total_record_count",
                "currency", "as_of", "cache_status", "rpc_duration_ms", "domain"):
        assert key in body, f"Response missing key: {key!r}"

    assert isinstance(body["projects"], list)
    assert len(body["projects"]) == 3
    for proj in body["projects"]:
        for k in ("project_id", "project_name", "late_uncollected", "record_count"):
            assert k in proj, f"Project entry missing key: {k!r}"


# ── Test K5-8b — Response headers ────────────────────────────────────────────


def test_kpi5_response_has_cache_control_and_x_cache_status(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_late_uncollected_by_project",
        new=AsyncMock(return_value=_MOCK_DATA_KPI5),
    ):
        r = client.get(_URL_KPI5)

    assert r.status_code == 200
    assert "private" in r.headers.get("cache-control", "")
    assert "max-age=60" in r.headers.get("cache-control", "")
    assert r.headers.get("x-cache-status") == "fresh"


# ── Test K5-8c — 503 on OdooQueryError ───────────────────────────────────────


def test_kpi5_odoo_unavailable_returns_503(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_late_uncollected_by_project",
        new=AsyncMock(side_effect=OdooQueryError("Odoo is down")),
    ):
        r = client.get(_URL_KPI5)

    assert r.status_code == 503
    body = r.json()
    assert "error" in body
    assert body["error"]["code"] == "odoo_unavailable"
    assert isinstance(body["error"]["message"], str)


# ── Test K5-8d — 405 on POST ──────────────────────────────────────────────────


def test_kpi5_post_returns_405(client: TestClient) -> None:
    r = client.post(_URL_KPI5)
    assert r.status_code == 405


# ══════════════════════════════════════════════════════════════════════════════
# KPI 3 — Pending Check Exposure endpoint tests
# ══════════════════════════════════════════════════════════════════════════════

_URL_KPI3 = "/api/v1/collections/kpi/pending-check-exposure"

_MOCK_DATA_KPI3 = {
    "value": 518_235_384.10,
    "currency": "EGP",
    "record_count": 42_443,
    "as_of": "2026-05-16T20:28:58+00:00",
    "cache_status": "fresh",
    "rpc_duration_ms": 5500,
    "domain": [["state", "=", "post"]],
    "paid_amount_sum": 3_488_834_648.95,
    "actual_paid_sum": 2_970_599_264.85,
    "derivation_note": "value = paid_amount_sum - actual_paid_sum",
    "data_quality_warning": None,
}


# ── Test K3-8a — 200 + JSON shape ────────────────────────────────────────────


def test_kpi3_get_returns_200_and_all_keys(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_pending_check_exposure",
        new=AsyncMock(return_value=_MOCK_DATA_KPI3),
    ):
        r = client.get(_URL_KPI3)

    assert r.status_code == 200
    body = r.json()
    for key in (
        "value", "currency", "record_count", "as_of",
        "cache_status", "rpc_duration_ms", "domain",
        "paid_amount_sum", "actual_paid_sum",
        "derivation_note", "data_quality_warning",
    ):
        assert key in body, f"Response missing key: {key!r}"


# ── Test K3-8b — Response headers ────────────────────────────────────────────


def test_kpi3_response_has_cache_control_and_x_cache_status(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_pending_check_exposure",
        new=AsyncMock(return_value=_MOCK_DATA_KPI3),
    ):
        r = client.get(_URL_KPI3)

    assert r.status_code == 200
    assert "private" in r.headers.get("cache-control", "")
    assert "max-age=60" in r.headers.get("cache-control", "")
    assert r.headers.get("x-cache-status") == "fresh"


# ── Test K3-8c — 503 on OdooQueryError ───────────────────────────────────────


def test_kpi3_odoo_unavailable_returns_503(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_pending_check_exposure",
        new=AsyncMock(side_effect=OdooQueryError("Odoo is down")),
    ):
        r = client.get(_URL_KPI3)

    assert r.status_code == 503
    body = r.json()
    assert "error" in body
    assert body["error"]["code"] == "odoo_unavailable"
    assert isinstance(body["error"]["message"], str)


# ── Test K3-8d — 405 on POST ──────────────────────────────────────────────────


def test_kpi3_post_returns_405(client: TestClient) -> None:
    r = client.post(_URL_KPI3)
    assert r.status_code == 405


# ══════════════════════════════════════════════════════════════════════════════
# KPI 6 — 6-Month Collection Trend endpoint tests
# ══════════════════════════════════════════════════════════════════════════════

_URL_KPI6 = "/api/v1/collections/kpi/collection-trend-6m"

_MOCK_DATA_KPI6 = {
    "months": [
        {"month": "2025-12", "label_en": "Dec 2025", "label_ar": "ديسمبر",
         "amount": 47_465_098.00, "record_count": 431},
        {"month": "2026-01", "label_en": "Jan 2026", "label_ar": "يناير",
         "amount": 0.0, "record_count": 0},
        {"month": "2026-02", "label_en": "Feb 2026", "label_ar": "فبراير",
         "amount": 0.0, "record_count": 0},
        {"month": "2026-03", "label_en": "Mar 2026", "label_ar": "مارس",
         "amount": 0.0, "record_count": 0},
        {"month": "2026-04", "label_en": "Apr 2026", "label_ar": "أبريل",
         "amount": 0.0, "record_count": 0},
        {"month": "2026-05", "label_en": "May 2026", "label_ar": "مايو",
         "amount": 0.0, "record_count": 0},
    ],
    "total_6m": 47_465_098.00,
    "total_record_count": 431,
    "average_monthly": 47_465_098.00 / 6,
    "period_start": "2025-12-01",
    "period_end": "2026-05-17",
    "currency": "EGP",
    "as_of": "2026-05-17T10:00:00+00:00",
    "cache_status": "fresh",
    "cache_ttl_seconds": 3600,
    "rpc_duration_ms": 85,
    "domain": [
        ["state", "=", "post"],
        ["date", ">=", "2025-12-01"],
        ["date", "<=", "2026-05-17 23:59:59"],
    ],
}


# ── Test K6-8a — 200 + JSON shape ────────────────────────────────────────────


def test_kpi6_get_returns_200_and_all_keys(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_collection_trend_6m",
        new=AsyncMock(return_value=_MOCK_DATA_KPI6),
    ):
        r = client.get(_URL_KPI6)

    assert r.status_code == 200
    body = r.json()
    for key in (
        "months", "total_6m", "total_record_count", "average_monthly",
        "period_start", "period_end", "currency", "as_of",
        "cache_status", "cache_ttl_seconds", "rpc_duration_ms", "domain",
    ):
        assert key in body, f"Response missing key: {key!r}"

    assert isinstance(body["months"], list)
    assert len(body["months"]) == 6
    for entry in body["months"]:
        for k in ("month", "label_en", "label_ar", "amount", "record_count"):
            assert k in entry, f"Month entry missing key: {k!r}"


# ── Test K6-8b — Response headers: max-age=3600 (NOT 60) ─────────────────────


def test_kpi6_response_has_cache_control_max_age_3600(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_collection_trend_6m",
        new=AsyncMock(return_value=_MOCK_DATA_KPI6),
    ):
        r = client.get(_URL_KPI6)

    assert r.status_code == 200
    cc = r.headers.get("cache-control", "")
    assert "private" in cc,      f"Cache-Control must contain 'private', got: {cc!r}"
    assert "max-age=3600" in cc, f"Cache-Control must contain 'max-age=3600', got: {cc!r}"
    assert "max-age=60"  not in cc, "KPI 6 must NOT use max-age=60 (that is for 60s KPIs)"
    assert r.headers.get("x-cache-status") == "fresh"


def test_kpi6_x_cache_status_reflects_cached_when_served_from_cache(
    client: TestClient,
) -> None:
    cached_data = {**_MOCK_DATA_KPI6, "cache_status": "cached", "rpc_duration_ms": 0}
    with patch(
        "backend.api.v1.endpoints.collections.get_collection_trend_6m",
        new=AsyncMock(return_value=cached_data),
    ):
        r = client.get(_URL_KPI6)

    assert r.headers.get("x-cache-status") == "cached"


# ── Test K6-8c — 503 on OdooQueryError ───────────────────────────────────────


def test_kpi6_odoo_unavailable_returns_503(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_collection_trend_6m",
        new=AsyncMock(side_effect=OdooQueryError("Odoo is down")),
    ):
        r = client.get(_URL_KPI6)

    assert r.status_code == 503
    body = r.json()
    assert "error" in body
    assert body["error"]["code"] == "odoo_unavailable"
    assert isinstance(body["error"]["message"], str)


# ── Test K6-8d — 405 on POST ──────────────────────────────────────────────────


def test_kpi6_post_returns_405(client: TestClient) -> None:
    r = client.post(_URL_KPI6)
    assert r.status_code == 405


# ══════════════════════════════════════════════════════════════════════════════
# KPI 7 — Expected Collections Forecast endpoint tests
# ══════════════════════════════════════════════════════════════════════════════

_URL_KPI7 = "/api/v1/collections/kpi/expected-forecast"

# KPI 7 v2 (Decision 19.1) — full-period buckets. Mock data uses 2026-05-19
# as today (Cairo); windows are the full calendar periods containing it.
# Month figures mirror the N3 discovery anchors (2026-06-11):
# 580,500 + 15,445,485 + 32,766,338 == 48,792,323 (exact invariant).
_TODAY_KPI7 = "2026-05-19"
_BUCKET_PERIODS = {
    "this_month":   ("2026-05-01", "2026-05-31"),
    "this_quarter": ("2026-04-01", "2026-06-30"),
    "this_half":    ("2026-01-01", "2026-06-30"),
    "this_year":    ("2026-01-01", "2026-12-31"),
}


def _make_bucket(name: str) -> dict:
    period_start, period_end = _BUCKET_PERIODS[name]
    return {
        "period_start":          period_start,
        "period_end":            period_end,
        "record_count":          390,
        "period_total_egp":      48_792_323.00,
        "collected_cleared_egp": 580_500.00,
        "cheques_pending_egp":   15_445_485.00,
        "remaining_egp":         32_766_338.00,
    }


_MOCK_DATA_KPI7 = {
    "buckets": {name: _make_bucket(name) for name in
                ("this_month", "this_quarter", "this_half", "this_year")},
    "currency":             "EGP",
    "today_cairo":          _TODAY_KPI7,
    "cache_status":         "fresh",
    "rpc_duration_ms":      84,
    "data_quality_warning": None,
}

_BUCKET_FIELDS = (
    "period_start", "period_end", "record_count", "period_total_egp",
    "collected_cleared_egp", "cheques_pending_egp", "remaining_egp",
)


# ── Test K7-8a — 200 + strict full-shape verification (v2) ───────────────────


def test_kpi7_get_returns_200_and_strict_shape(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_expected_collections_forecast",
        new=AsyncMock(return_value=_MOCK_DATA_KPI7),
    ):
        r = client.get(_URL_KPI7)

    assert r.status_code == 200
    body = r.json()

    # Top-level keys
    for key in ("buckets", "currency", "today_cairo", "cache_status",
                "rpc_duration_ms", "data_quality_warning"):
        assert key in body, f"Response missing top-level key: {key!r}"

    # Scalar top-level values
    assert body["currency"] == "EGP"
    assert body["today_cairo"] == _TODAY_KPI7
    assert body["cache_status"] in {"fresh", "cached"}

    # All 4 bucket keys present
    buckets = body["buckets"]
    for bname in ("this_month", "this_quarter", "this_half", "this_year"):
        assert bname in buckets, f"Missing bucket key: {bname!r}"

    # Per-bucket: all 7 v2 fields, full-period bounds, three-segment invariant
    for bname, (expected_start, expected_end) in _BUCKET_PERIODS.items():
        b = buckets[bname]
        for field in _BUCKET_FIELDS:
            assert field in b, f"Bucket {bname!r} missing field: {field!r}"
        assert b["period_start"] == expected_start, \
            f"period_start mismatch for {bname!r}: expected {expected_start!r}, got {b['period_start']!r}"
        assert b["period_end"] == expected_end, \
            f"period_end mismatch for {bname!r}: expected {expected_end!r}, got {b['period_end']!r}"
        assert (
            b["collected_cleared_egp"] + b["cheques_pending_egp"] + b["remaining_egp"]
            == pytest.approx(b["period_total_egp"])
        ), f"three-segment invariant broken in bucket {bname!r}"
        # v1 fields must be gone from the wire format
        for legacy in ("bucket", "amount", "due_amount", "cheques_in_pipeline",
                       "cheques_record_count", "drill_down_domain",
                       "cheques_drill_down_domain", "type_breakdown"):
            assert legacy not in b, f"v1 field {legacy!r} leaked into bucket {bname!r}"


# ── Test K7-8b — Response headers ────────────────────────────────────────────


def test_kpi7_response_has_cache_control_and_x_cache_status(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_expected_collections_forecast",
        new=AsyncMock(return_value=_MOCK_DATA_KPI7),
    ):
        r = client.get(_URL_KPI7)

    assert r.status_code == 200
    assert "private"    in r.headers.get("cache-control", "")
    assert "max-age=60" in r.headers.get("cache-control", "")
    assert r.headers.get("x-cache-status") == "fresh"


def test_kpi7_x_cache_status_reflects_cached_when_served_from_cache(
    client: TestClient,
) -> None:
    cached_data = {**_MOCK_DATA_KPI7, "cache_status": "cached", "rpc_duration_ms": 0}
    with patch(
        "backend.api.v1.endpoints.collections.get_expected_collections_forecast",
        new=AsyncMock(return_value=cached_data),
    ):
        r = client.get(_URL_KPI7)

    assert r.headers.get("x-cache-status") == "cached"


# ── Test K7-8c — 503 on OdooQueryError ───────────────────────────────────────


def test_kpi7_odoo_unavailable_returns_503(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_expected_collections_forecast",
        new=AsyncMock(side_effect=OdooQueryError("Odoo is down")),
    ):
        r = client.get(_URL_KPI7)

    assert r.status_code == 503
    body = r.json()
    assert "error" in body
    assert body["error"]["code"] == "odoo_unavailable"
    assert isinstance(body["error"]["message"], str)


# ── Test K7-8d — 405 on POST ──────────────────────────────────────────────────


def test_kpi7_post_returns_405(client: TestClient) -> None:
    r = client.post(_URL_KPI7)
    assert r.status_code == 405


# ── Test K7-8e — response_model= is enforced on the success path ─────────────


def test_kpi7_response_model_validates_success_shape(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_expected_collections_forecast",
        new=AsyncMock(return_value=_MOCK_DATA_KPI7),
    ):
        r = client.get(_URL_KPI7)

    assert r.status_code == 200
    # If response_model= is active on the success path, the dict return is
    # validated by FastAPI and must parse as ExpectedCollectionsForecastResponse
    # without raising. A JSONResponse return would bypass this.
    validated = ExpectedCollectionsForecastResponse(**r.json())
    assert validated.currency == "EGP"
    assert set(validated.buckets.keys()) == {
        "this_month", "this_quarter", "this_half", "this_year"
    }


# ══════════════════════════════════════════════════════════════════════════════
# KPI 4 — Collection Rate MTD & YTD endpoint tests
# ══════════════════════════════════════════════════════════════════════════════

_URL_KPI4 = "/api/v1/collections/kpi/collection-rate"

_MOCK_DATA_KPI4 = {
    "mtd": {
        "numerator_egp":    12_500_000.00,
        "denominator_egp":  20_000_000.00,
        "rate_percent":     62.5,
        "period_start":     "2026-06-01",
        "period_end":       "2026-06-30",
        "record_count_num": 145,
        "record_count_den": 230,
    },
    "ytd": {
        "numerator_egp":    310_000_000.00,
        "denominator_egp":  500_000_000.00,
        "rate_percent":     62.0,
        "period_start":     "2026-01-01",
        "period_end":       "2026-06-30",
        "record_count_num": 3_120,
        "record_count_den": 5_010,
    },
    "ytd_period_assumption": "calendar_year",
    "currency":              "EGP",
    "as_of":                 "2026-06-30T10:00:00+00:00",
    "cache_status":          "fresh",
    "rpc_duration_ms":       128,
}


# ── Test K4-8a — 200 + strict key shape ──────────────────────────────────────


def test_kpi4_get_returns_200_and_all_keys(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_collection_rate_mtd_ytd",
        new=AsyncMock(return_value=_MOCK_DATA_KPI4),
    ):
        r = client.get(_URL_KPI4)

    assert r.status_code == 200
    body = r.json()

    assert set(body.keys()) == {
        "mtd", "ytd", "ytd_period_assumption",
        "currency", "as_of", "cache_status", "rpc_duration_ms",
    }

    period_keys = {
        "numerator_egp", "denominator_egp", "rate_percent",
        "period_start", "period_end", "record_count_num", "record_count_den",
    }
    assert set(body["mtd"].keys()) == period_keys
    assert set(body["ytd"].keys()) == period_keys


# ── Test K4-8b — Response headers ────────────────────────────────────────────


def test_kpi4_response_has_cache_control_and_x_cache_status(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_collection_rate_mtd_ytd",
        new=AsyncMock(return_value=_MOCK_DATA_KPI4),
    ):
        r = client.get(_URL_KPI4)

    assert r.status_code == 200
    assert "private" in r.headers.get("cache-control", "")
    assert "max-age=60" in r.headers.get("cache-control", "")
    assert r.headers.get("x-cache-status") == "fresh"


def test_kpi4_x_cache_status_reflects_cached_when_served_from_cache(
    client: TestClient,
) -> None:
    cached_data = {**_MOCK_DATA_KPI4, "cache_status": "cached", "rpc_duration_ms": 0}
    with patch(
        "backend.api.v1.endpoints.collections.get_collection_rate_mtd_ytd",
        new=AsyncMock(return_value=cached_data),
    ):
        r = client.get(_URL_KPI4)

    assert r.headers.get("x-cache-status") == "cached"


# ── Test K4-8c — 503 on OdooQueryError ───────────────────────────────────────


def test_kpi4_odoo_unavailable_returns_503(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_collection_rate_mtd_ytd",
        new=AsyncMock(side_effect=OdooQueryError("Odoo is down")),
    ):
        r = client.get(_URL_KPI4)

    assert r.status_code == 503
    body = r.json()
    assert "error" in body
    assert body["error"]["code"] == "odoo_unavailable"
    assert isinstance(body["error"]["message"], str)


# ── Test K4-8d — 405 on POST ──────────────────────────────────────────────────


def test_kpi4_post_returns_405(client: TestClient) -> None:
    r = client.post(_URL_KPI4)
    assert r.status_code == 405


# ── Test K4-8e — 401 when unauthenticated ────────────────────────────────────


def test_kpi4_401_when_no_auth() -> None:
    c = TestClient(app, raise_server_exceptions=True)
    r = c.get(_URL_KPI4)  # no session
    assert r.status_code == 401, (
        f"Expected 401 for unauthenticated Collections request, got {r.status_code}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# KPI 5b — Collection Rate by Project (MTD & YTD) endpoint tests
# ══════════════════════════════════════════════════════════════════════════════

_URL_KPI5B = "/api/v1/collections/kpi/collection-rate-by-project"

_MOCK_DATA_KPI5B = {
    "mtd": {
        "projects": [
            {"project_id": 1, "project_name": "New Capital",
             "numerator_egp": 5_000_000.00, "denominator_egp": 8_000_000.00,
             "rate_percent": 62.5, "record_count_num": 60, "record_count_den": 95},
            {"project_id": 2, "project_name": "Cassette",
             "numerator_egp": 3_000_000.00, "denominator_egp": 6_000_000.00,
             "rate_percent": 50.0, "record_count_num": 40, "record_count_den": 80},
            {"project_id": 3, "project_name": "La puerta",
             "numerator_egp": 0.0, "denominator_egp": 0.0,
             "rate_percent": None, "record_count_num": 0, "record_count_den": 0},
        ],
        "total_numerator_egp":   8_000_000.00,
        "total_denominator_egp": 14_000_000.00,
        "total_rate_percent":    57.142857142857146,
        "period_start":          "2026-06-01",
        "period_end":            "2026-06-30",
    },
    "ytd": {
        "projects": [
            {"project_id": 1, "project_name": "New Capital",
             "numerator_egp": 120_000_000.00, "denominator_egp": 200_000_000.00,
             "rate_percent": 60.0, "record_count_num": 1_400, "record_count_den": 2_300},
            {"project_id": 2, "project_name": "Cassette",
             "numerator_egp": 80_000_000.00, "denominator_egp": 150_000_000.00,
             "rate_percent": 53.333333333333336, "record_count_num": 900, "record_count_den": 1_500},
            {"project_id": 3, "project_name": "La puerta",
             "numerator_egp": 1_500_000.00, "denominator_egp": 3_000_000.00,
             "rate_percent": 50.0, "record_count_num": 20, "record_count_den": 40},
        ],
        "total_numerator_egp":   201_500_000.00,
        "total_denominator_egp": 353_000_000.00,
        "total_rate_percent":    57.082152974504246,
        "period_start":          "2026-01-01",
        "period_end":            "2026-06-30",
    },
    "ytd_period_assumption": "calendar_year",
    "currency":              "EGP",
    "as_of":                 "2026-06-30T10:00:00+00:00",
    "cache_status":          "fresh",
    "rpc_duration_ms":       212,
}


# ── Test K5b-8a — 200 + strict key shape (3 levels) ──────────────────────────


def test_kpi5b_get_returns_200_and_all_keys(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_collection_rate_by_project",
        new=AsyncMock(return_value=_MOCK_DATA_KPI5B),
    ):
        r = client.get(_URL_KPI5B)

    assert r.status_code == 200
    body = r.json()

    assert set(body.keys()) == {
        "mtd", "ytd", "ytd_period_assumption",
        "currency", "as_of", "cache_status", "rpc_duration_ms",
    }

    period_keys = {
        "projects", "total_numerator_egp", "total_denominator_egp",
        "total_rate_percent", "period_start", "period_end",
    }
    project_keys = {
        "project_id", "project_name", "numerator_egp", "denominator_egp",
        "rate_percent", "record_count_num", "record_count_den",
    }
    for period in ("mtd", "ytd"):
        assert set(body[period].keys()) == period_keys
        projects = body[period]["projects"]
        assert isinstance(projects, list)
        assert len(projects) == 3
        assert [p["project_id"] for p in projects] == [1, 2, 3]
        for proj in projects:
            assert set(proj.keys()) == project_keys


# ── Test K5b-8b — Response headers ───────────────────────────────────────────


def test_kpi5b_response_has_cache_control_and_x_cache_status(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_collection_rate_by_project",
        new=AsyncMock(return_value=_MOCK_DATA_KPI5B),
    ):
        r = client.get(_URL_KPI5B)

    assert r.status_code == 200
    assert "private" in r.headers.get("cache-control", "")
    assert "max-age=60" in r.headers.get("cache-control", "")
    assert r.headers.get("x-cache-status") == "fresh"


def test_kpi5b_x_cache_status_reflects_cached_when_served_from_cache(
    client: TestClient,
) -> None:
    cached_data = {**_MOCK_DATA_KPI5B, "cache_status": "cached", "rpc_duration_ms": 0}
    with patch(
        "backend.api.v1.endpoints.collections.get_collection_rate_by_project",
        new=AsyncMock(return_value=cached_data),
    ):
        r = client.get(_URL_KPI5B)

    assert r.headers.get("x-cache-status") == "cached"


# ── Test K5b-8c — 503 on OdooQueryError ──────────────────────────────────────


def test_kpi5b_odoo_unavailable_returns_503(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_collection_rate_by_project",
        new=AsyncMock(side_effect=OdooQueryError("Odoo is down")),
    ):
        r = client.get(_URL_KPI5B)

    assert r.status_code == 503
    body = r.json()
    assert "error" in body
    assert body["error"]["code"] == "odoo_unavailable"
    assert isinstance(body["error"]["message"], str)


# ── Test K5b-8d — 405 on POST ─────────────────────────────────────────────────


def test_kpi5b_post_returns_405(client: TestClient) -> None:
    r = client.post(_URL_KPI5B)
    assert r.status_code == 405


# ── Test K5b-8f — 401 when unauthenticated ───────────────────────────────────


def test_kpi5b_401_when_no_auth() -> None:
    c = TestClient(app, raise_server_exceptions=True)
    r = c.get(_URL_KPI5B)  # no session
    assert r.status_code == 401, (
        f"Expected 401 for unauthenticated Collections request, got {r.status_code}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Drill-down: KPI 2 — Late Uncollected installments (paginated) endpoint tests
# ══════════════════════════════════════════════════════════════════════════════

_URL_DRILL_LATE = "/api/v1/collections/drilldown/late"

_MOCK_DRILL_LATE = {
    "version": "1.0",
    "data": {
        "items": [
            {
                "record_id": 101,
                "customer_name": "Ahmed Hassan",
                "project_id": 1,
                "project_name_ar": "العاصمة الإدارية",
                "project_name_en": "New Capital",
                "installment_type_id": 4,
                "installment_type_name_ar": "قسط",
                "installment_type_name_en": "Installment",
                "date": "2026-03-15",
                "amount": 250_000.00,
                "due_amount": 180_000.00,
                "paid_amount": 70_000.00,
                "actual_paid_amount": 50_000.00,
                "pending_cheque": 20_000.00,
                "payment_state": "partial",
                "late_amount": 200_000.00,
            },
        ],
    },
    "meta": {
        "request_id": "mock-req-late",
        "as_of": "2026-06-30T10:00:00+00:00",
        "rpc_duration_ms": 64,
        "page_size": 50,
        "total_count": 1,
        "cursor_current": None,
        "cursor_next": None,
        "has_next": False,
        "filters_applied": {"payment_state": None, "has_pending_cheque": None},
        "sort_applied": {"sort_by": "due_amount", "sort_dir": "desc"},
        "data_quality": None,
    },
}

_META_KEYS = {
    "request_id", "as_of", "rpc_duration_ms", "page_size", "total_count",
    "cursor_current", "cursor_next", "has_next", "filters_applied",
    "sort_applied", "data_quality",
}

_INSTALLMENT_ROW_KEYS = {
    "record_id", "customer_name", "project_id", "project_name_ar",
    "project_name_en", "installment_type_id", "installment_type_name_ar",
    "installment_type_name_en", "date", "amount", "due_amount", "paid_amount",
    "actual_paid_amount", "pending_cheque", "payment_state", "late_amount",
}


# ── Test DL-8a — 200 + strict envelope shape ─────────────────────────────────


def test_drill_late_returns_200_and_envelope_shape(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_late_drilldown",
        new=AsyncMock(return_value=_MOCK_DRILL_LATE),
    ):
        r = client.get(_URL_DRILL_LATE)

    assert r.status_code == 200
    body = r.json()

    assert set(body.keys()) == {"version", "data", "meta"}
    assert body["version"] == "1.0"

    assert set(body["meta"].keys()) == _META_KEYS
    assert isinstance(body["meta"]["filters_applied"], dict)
    assert isinstance(body["meta"]["sort_applied"], dict)

    assert set(body["data"].keys()) == {"items"}
    items = body["data"]["items"]
    assert isinstance(items, list)
    assert len(items) >= 1
    assert set(items[0].keys()) == _INSTALLMENT_ROW_KEYS


# ── Test DL-8b — X-Request-ID echo-back + no Cache-Control ────────────────────


def test_drill_late_request_id_echoed_and_no_cache_control(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_late_drilldown",
        new=AsyncMock(return_value=_MOCK_DRILL_LATE),
    ):
        r = client.get(_URL_DRILL_LATE, headers={"X-Request-ID": "test-rid-late"})

    assert r.status_code == 200
    assert r.headers["x-request-id"] == "test-rid-late"
    assert "cache-control" not in r.headers


# ── Test DL-8c — 503 on OdooQueryError ───────────────────────────────────────


def test_drill_late_odoo_unavailable_returns_503(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_late_drilldown",
        new=AsyncMock(side_effect=OdooQueryError("Odoo is down")),
    ):
        r = client.get(_URL_DRILL_LATE)

    assert r.status_code == 503
    body = r.json()
    assert "error" in body
    assert body["error"]["code"] == "odoo_unavailable"
    assert isinstance(body["error"]["message"], str)
    assert r.headers.get("x-request-id")


# ── Test DL-8d — 405 on POST ──────────────────────────────────────────────────


def test_drill_late_post_returns_405(client: TestClient) -> None:
    r = client.post(_URL_DRILL_LATE)
    assert r.status_code == 405


# ── Test DL-8e — 401 when unauthenticated ────────────────────────────────────


def test_drill_late_401_when_no_auth() -> None:
    c = TestClient(app, raise_server_exceptions=True)
    r = c.get(_URL_DRILL_LATE)  # no session
    assert r.status_code == 401, (
        f"Expected 401 for unauthenticated Collections request, got {r.status_code}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Drill-down: KPI 1 — Portfolio customer × project breakdown endpoint tests
# ══════════════════════════════════════════════════════════════════════════════

_URL_DRILL_PORTFOLIO = "/api/v1/collections/drilldown/portfolio"

_MOCK_DRILL_PORTFOLIO = {
    "version": "1.0",
    "data": {
        "customers": [
            {
                "customer_id": 5001,
                "customer_name": "Mona Said",
                "total_amount": 3_200_000.00,
                "total_paid": 1_100_000.00,
                "total_due": 2_100_000.00,
                "total_actual_paid": 950_000.00,
                "record_count": 12,
                "project_breakdown": [
                    {
                        "project_id": 1,
                        "project_name_ar": "العاصمة الإدارية",
                        "project_name_en": "New Capital",
                        "amount": 3_200_000.00,
                        "due_amount": 2_100_000.00,
                        "record_count": 12,
                    },
                ],
            },
        ],
    },
    "meta": {
        "request_id": "mock-req-portfolio",
        "as_of": "2026-06-30T10:00:00+00:00",
        "rpc_duration_ms": 78,
        "page_size": 50,
        "total_count": 1,
        "cursor_current": None,
        "cursor_next": None,
        "has_next": False,
        "filters_applied": {"project_id": None},
        "sort_applied": {},
        "data_quality": None,
    },
}

_PORTFOLIO_CUSTOMER_KEYS = {
    "customer_id", "customer_name", "total_amount", "total_paid", "total_due",
    "total_actual_paid", "record_count", "project_breakdown",
}

_PORTFOLIO_BREAKDOWN_KEYS = {
    "project_id", "project_name_ar", "project_name_en",
    "amount", "due_amount", "record_count",
}


# ── Test DP-8a — 200 + strict envelope shape (incl. nested breakdown) ─────────


def test_drill_portfolio_returns_200_and_envelope_shape(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_portfolio_drilldown",
        new=AsyncMock(return_value=_MOCK_DRILL_PORTFOLIO),
    ):
        r = client.get(_URL_DRILL_PORTFOLIO)

    assert r.status_code == 200
    body = r.json()

    assert set(body.keys()) == {"version", "data", "meta"}
    assert body["version"] == "1.0"

    assert set(body["meta"].keys()) == _META_KEYS
    assert isinstance(body["meta"]["filters_applied"], dict)
    assert isinstance(body["meta"]["sort_applied"], dict)

    assert set(body["data"].keys()) == {"customers"}
    customers = body["data"]["customers"]
    assert isinstance(customers, list)
    assert len(customers) >= 1
    assert set(customers[0].keys()) == _PORTFOLIO_CUSTOMER_KEYS

    breakdown = customers[0]["project_breakdown"]
    assert isinstance(breakdown, list)
    assert len(breakdown) >= 1
    assert set(breakdown[0].keys()) == _PORTFOLIO_BREAKDOWN_KEYS


# ── Test DP-8b — X-Request-ID echo-back + no Cache-Control ────────────────────


def test_drill_portfolio_request_id_echoed_and_no_cache_control(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_portfolio_drilldown",
        new=AsyncMock(return_value=_MOCK_DRILL_PORTFOLIO),
    ):
        r = client.get(_URL_DRILL_PORTFOLIO, headers={"X-Request-ID": "test-rid-portfolio"})

    assert r.status_code == 200
    assert r.headers["x-request-id"] == "test-rid-portfolio"
    assert "cache-control" not in r.headers


# ── Test DP-8c — 503 on OdooQueryError ───────────────────────────────────────


def test_drill_portfolio_odoo_unavailable_returns_503(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_portfolio_drilldown",
        new=AsyncMock(side_effect=OdooQueryError("Odoo is down")),
    ):
        r = client.get(_URL_DRILL_PORTFOLIO)

    assert r.status_code == 503
    body = r.json()
    assert "error" in body
    assert body["error"]["code"] == "odoo_unavailable"
    assert isinstance(body["error"]["message"], str)
    assert r.headers.get("x-request-id")


# ── Test DP-8d — 405 on POST ──────────────────────────────────────────────────


def test_drill_portfolio_post_returns_405(client: TestClient) -> None:
    r = client.post(_URL_DRILL_PORTFOLIO)
    assert r.status_code == 405


# ── Test DP-8e — 401 when unauthenticated ────────────────────────────────────


def test_drill_portfolio_401_when_no_auth() -> None:
    c = TestClient(app, raise_server_exceptions=True)
    r = c.get(_URL_DRILL_PORTFOLIO)  # no session
    assert r.status_code == 401, (
        f"Expected 401 for unauthenticated Collections request, got {r.status_code}"
    )


# ── Test DP-8f — ?project_id=99 → 200 empty filter (drilldown-404 vs portfolio-200) ──


def test_drill_portfolio_unknown_project_id_returns_200_empty_filter(client: TestClient) -> None:
    # Stage 4 (Decision 25.4): the portfolio project_id is a PURE optional domain filter
    # (Query ge=1, no le=3, no 404 handling) — an unknown positive id yields a VALID 200
    # with empty filtered results, NOT a 404. This pins the deliberate asymmetry:
    # drilldown/project/99 → 404, but drilldown/portfolio?project_id=99 → 200-empty.
    empty_portfolio = {
        **_MOCK_DRILL_PORTFOLIO,
        "data": {"customers": []},
        "meta": {**_MOCK_DRILL_PORTFOLIO["meta"], "total_count": 0,
                 "filters_applied": {"project_id": 99}},
    }
    with patch(
        "backend.api.v1.endpoints.collections.get_portfolio_drilldown",
        new=AsyncMock(return_value=empty_portfolio),
    ):
        r = client.get(_URL_DRILL_PORTFOLIO, params={"project_id": 99})

    assert r.status_code == 200
    body = r.json()
    assert body["data"]["customers"] == []
    assert body["meta"]["total_count"] == 0
    assert body["meta"]["filters_applied"]["project_id"] == 99


# ══════════════════════════════════════════════════════════════════════════════
# Drill-down: KPI 5 — Late Uncollected for one project endpoint tests
# ══════════════════════════════════════════════════════════════════════════════

_URL_DRILL_PROJECT = "/api/v1/collections/drilldown/project/1"

_MOCK_DRILL_PROJECT = {
    "version": "1.0",
    "data": {
        "project_id": 1,
        "project_name_ar": "العاصمة الإدارية",
        "project_name_en": "New Capital",
        "total_late_uncollected": 164_017_258.40,
        "total_record_count": 1_472,
        "items": [
            {
                "record_id": 202,
                "customer_name": "Khaled Omar",
                "project_id": 1,
                "project_name_ar": "العاصمة الإدارية",
                "project_name_en": "New Capital",
                "installment_type_id": 4,
                "installment_type_name_ar": "قسط",
                "installment_type_name_en": "Installment",
                "date": "2026-02-10",
                "amount": 300_000.00,
                "due_amount": 220_000.00,
                "paid_amount": 80_000.00,
                "actual_paid_amount": 60_000.00,
                "pending_cheque": 20_000.00,
                "payment_state": "partial",
                "late_amount": 240_000.00,
            },
        ],
    },
    "meta": {
        "request_id": "mock-req-project",
        "as_of": "2026-06-30T10:00:00+00:00",
        "rpc_duration_ms": 71,
        "page_size": 50,
        "total_count": 1,
        "cursor_current": None,
        "cursor_next": None,
        "has_next": False,
        "filters_applied": {"payment_state": None, "has_pending_cheque": None},
        "sort_applied": {"sort_by": "due_amount", "sort_dir": "desc"},
        "data_quality": None,
    },
}

_PROJECT_DATA_KEYS = {
    "project_id", "project_name_ar", "project_name_en",
    "total_late_uncollected", "total_record_count", "items",
}


# ── Test DPr-8a — 200 + strict envelope shape ────────────────────────────────


def test_drill_project_returns_200_and_envelope_shape(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_project_drilldown",
        new=AsyncMock(return_value=_MOCK_DRILL_PROJECT),
    ):
        r = client.get(_URL_DRILL_PROJECT)

    assert r.status_code == 200
    body = r.json()

    assert set(body.keys()) == {"version", "data", "meta"}
    assert body["version"] == "1.0"

    assert set(body["meta"].keys()) == _META_KEYS
    assert isinstance(body["meta"]["filters_applied"], dict)
    assert isinstance(body["meta"]["sort_applied"], dict)

    assert set(body["data"].keys()) == _PROJECT_DATA_KEYS
    items = body["data"]["items"]
    assert isinstance(items, list)
    assert len(items) >= 1
    assert set(items[0].keys()) == _INSTALLMENT_ROW_KEYS


# ── Test DPr-8b — X-Request-ID echo-back + no Cache-Control ───────────────────


def test_drill_project_request_id_echoed_and_no_cache_control(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_project_drilldown",
        new=AsyncMock(return_value=_MOCK_DRILL_PROJECT),
    ):
        r = client.get(_URL_DRILL_PROJECT, headers={"X-Request-ID": "test-rid-project"})

    assert r.status_code == 200
    assert r.headers["x-request-id"] == "test-rid-project"
    assert "cache-control" not in r.headers


# ── Test DPr-8c — 503 on OdooQueryError ──────────────────────────────────────


def test_drill_project_odoo_unavailable_returns_503(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_project_drilldown",
        new=AsyncMock(side_effect=OdooQueryError("Odoo is down")),
    ):
        r = client.get(_URL_DRILL_PROJECT)

    assert r.status_code == 503
    body = r.json()
    assert "error" in body
    assert body["error"]["code"] == "odoo_unavailable"
    assert isinstance(body["error"]["message"], str)
    assert r.headers.get("x-request-id")


# ── Test DPr-8d — 405 on POST ─────────────────────────────────────────────────


def test_drill_project_post_returns_405(client: TestClient) -> None:
    r = client.post(_URL_DRILL_PROJECT)
    assert r.status_code == 405


# ── Test DPr-8e — 401 when unauthenticated ───────────────────────────────────


def test_drill_project_401_when_no_auth() -> None:
    c = TestClient(app, raise_server_exceptions=True)
    r = c.get(_URL_DRILL_PROJECT)  # no session
    assert r.status_code == 401, (
        f"Expected 401 for unauthenticated Collections request, got {r.status_code}"
    )


# ── Test DPr-8f — out-of-range LOW id → 422 (Path ge=1 survives le=3 removal) ──


def test_drill_project_low_id_returns_422_fastapi_validation(client: TestClient) -> None:
    # Stage 4 (Decision 25.4) dropped le=3 but KEPT ge=1, so a 0/negative id is still
    # malformed → FastAPI rejects it with its own 422 BEFORE the handler runs. The
    # malformed-id guard survives; no service patch needed.
    r = client.get("/api/v1/collections/drilldown/project/0")

    assert r.status_code == 422
    body = r.json()
    assert "detail" in body   # FastAPI validation shape
    assert "error" not in body  # NOT the handler's invalid_param shape


# ── Test DPr-8g — positive-but-unknown id → 404 project_not_found ─────────────


def test_drill_project_unknown_id_returns_404_project_not_found(client: TestClient) -> None:
    # Stage 4 (Decision 25.4): with le=3 gone, an in-range-but-unknown positive id now
    # flows to the service, whose resolver map is {1,2,3}; project 99 raises
    # ProjectNotFoundError → the router's 404 branch (project_not_found) — NOT a 503 and
    # NOT a 200-empty. Mock the service to raise it, exactly as the 503 test mocks
    # OdooQueryError (route tests mock the service layer).
    with patch(
        "backend.api.v1.endpoints.collections.get_project_drilldown",
        new=AsyncMock(side_effect=ProjectNotFoundError("Project 99 not found.")),
    ):
        r = client.get("/api/v1/collections/drilldown/project/99")

    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "project_not_found"
    assert isinstance(body["error"]["message"], str)
    assert "detail" not in body  # NOT the FastAPI validation shape
    assert r.headers.get("x-request-id")


# ══════════════════════════════════════════════════════════════════════════════
# Drill-down: KPI 6 — Collection Trend, installments due in one month endpoint tests
# ══════════════════════════════════════════════════════════════════════════════

_URL_DRILL_TREND = "/api/v1/collections/drilldown/trend/2026-03"

_MOCK_DRILL_TREND = {
    "version": "1.0",
    "data": {
        "month": "2026-03",
        "items": [
            {
                "record_id": 303,
                "customer_name": "Sara Mahmoud",
                "project_id": 1,
                "project_name_ar": "العاصمة الإدارية",
                "project_name_en": "New Capital",
                "installment_type_id": 4,
                "installment_type_name_ar": "قسط",
                "installment_type_name_en": "Installment",
                "date": "2026-03-20",
                "amount": 275_000.00,
                "due_amount": 175_000.00,
                "paid_amount": 100_000.00,
                "actual_paid_amount": 90_000.00,
                "pending_cheque": 10_000.00,
                "payment_state": "partial",
                "late_amount": 185_000.00,
            },
        ],
    },
    "meta": {
        "request_id": "mock-req-trend",
        "as_of": "2026-06-30T10:00:00+00:00",
        "rpc_duration_ms": 58,
        "page_size": 50,
        "total_count": 1,
        "cursor_current": None,
        "cursor_next": None,
        "has_next": False,
        "filters_applied": {"month": "2026-03", "payment_state": None, "has_pending_cheque": None},
        "sort_applied": {"sort_by": "due_amount", "sort_dir": "desc"},
        "data_quality": None,
    },
}


# ── Test DT-8a — 200 + strict envelope shape ─────────────────────────────────


def test_drill_trend_returns_200_and_envelope_shape(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_trend_drilldown",
        new=AsyncMock(return_value=_MOCK_DRILL_TREND),
    ):
        r = client.get(_URL_DRILL_TREND)

    assert r.status_code == 200
    body = r.json()

    assert set(body.keys()) == {"version", "data", "meta"}
    assert body["version"] == "1.0"

    assert set(body["meta"].keys()) == _META_KEYS
    assert isinstance(body["meta"]["filters_applied"], dict)
    assert isinstance(body["meta"]["sort_applied"], dict)

    assert set(body["data"].keys()) == {"month", "items"}
    items = body["data"]["items"]
    assert isinstance(items, list)
    assert len(items) >= 1
    assert set(items[0].keys()) == _INSTALLMENT_ROW_KEYS


# ── Test DT-8b — X-Request-ID echo-back + no Cache-Control ─────────────────────


def test_drill_trend_request_id_echoed_and_no_cache_control(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_trend_drilldown",
        new=AsyncMock(return_value=_MOCK_DRILL_TREND),
    ):
        r = client.get(_URL_DRILL_TREND, headers={"X-Request-ID": "test-rid-trend"})

    assert r.status_code == 200
    assert r.headers["x-request-id"] == "test-rid-trend"
    assert "cache-control" not in r.headers


# ── Test DT-8c — 503 on OdooQueryError ───────────────────────────────────────


def test_drill_trend_odoo_unavailable_returns_503(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_trend_drilldown",
        new=AsyncMock(side_effect=OdooQueryError("Odoo is down")),
    ):
        r = client.get(_URL_DRILL_TREND)

    assert r.status_code == 503
    body = r.json()
    assert "error" in body
    assert body["error"]["code"] == "odoo_unavailable"
    assert isinstance(body["error"]["message"], str)
    assert r.headers.get("x-request-id")


# ── Test DT-8d — 405 on POST ──────────────────────────────────────────────────


def test_drill_trend_post_returns_405(client: TestClient) -> None:
    r = client.post(_URL_DRILL_TREND)
    assert r.status_code == 405


# ── Test DT-8e — 401 when unauthenticated ────────────────────────────────────


def test_drill_trend_401_when_no_auth() -> None:
    c = TestClient(app, raise_server_exceptions=True)
    r = c.get(_URL_DRILL_TREND)  # no session
    assert r.status_code == 401, (
        f"Expected 401 for unauthenticated Collections request, got {r.status_code}"
    )


# ── Test DT-8f — FastAPI Path-regex 422 on malformed month ───────────────────


def test_drill_trend_invalid_format_returns_422_fastapi_validation(client: TestClient) -> None:
    # "2026-1" has a 1-digit month, so it FAILS the Path regex ^\d{4}-\d{2}$ and
    # FastAPI rejects it with its own 422 BEFORE the handler runs. No service patch.
    # NB: the request-id middleware sets X-Request-ID on every response (including
    # FastAPI-generated 422s), so we do NOT assert anything about that header here.
    r = client.get("/api/v1/collections/drilldown/trend/2026-1")

    assert r.status_code == 422
    body = r.json()
    assert "detail" in body   # FastAPI validation shape
    assert "error" not in body  # NOT the handler's invalid_param shape


# ── Test DT-8g — handler invalid_param 422 on regex-valid bad calendar month ──


def test_drill_trend_bad_calendar_month_returns_422_invalid_param(client: TestClient) -> None:
    # "2026-13" PASSES the Path regex ^\d{4}-\d{2}$ but month 13 is an invalid
    # calendar month, so the REAL service (no patch) raises ValueError at
    # date.fromisoformat("2026-13-01") — BEFORE any Odoo call, so this is hermetic —
    # and the handler's except ValueError branch maps it to 422 invalid_param.
    r = client.get("/api/v1/collections/drilldown/trend/2026-13")

    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "invalid_param"
    assert isinstance(body["error"]["message"], str)
    assert "detail" not in body  # NOT the FastAPI validation shape
    assert r.headers.get("x-request-id")


# ══════════════════════════════════════════════════════════════════════════════
# Drill-down: KPI 7 v2 — Forecast segment (bucket × segment) endpoint tests
# ══════════════════════════════════════════════════════════════════════════════

_URL_DRILL_FORECAST = "/api/v1/collections/drilldown/forecast/this_month/cleared"

_FORECAST_SEGMENT_ROW_KEYS = _INSTALLMENT_ROW_KEYS | {
    "partner_id", "unit_id", "unit_name", "segment", "segment_metric",
}

_FORECAST_DATA_KEYS = {
    "bucket", "segment", "period_start", "period_end", "segment_total_egp", "items",
}

_MOCK_DRILL_FORECAST = {
    "version": "1.0",
    "data": {
        "bucket": "this_month",
        "segment": "cleared",
        "period_start": "2026-05-01",
        "period_end": "2026-05-31",
        "segment_total_egp": 580_500.00,
        "items": [
            {
                # — InstallmentRow base (16) —
                "record_id": 404,
                "customer_name": "Omar Fathy",
                "project_id": 1,
                "project_name_ar": "العاصمة الإدارية",
                "project_name_en": "New Capital",
                "installment_type_id": 4,
                "installment_type_name_ar": "قسط",
                "installment_type_name_en": "Installment",
                "date": "2026-05-12",
                "amount": 120_000.00,
                "due_amount": 0.00,
                "paid_amount": 120_000.00,
                "actual_paid_amount": 120_000.00,
                "pending_cheque": 0.00,
                "payment_state": "paid",
                "late_amount": 0.00,
                # — ForecastSegmentRow extras (5) —
                "partner_id": 7001,
                "unit_id": 9001,
                "unit_name": "Unit#AF208-20-601",
                "segment": "cleared",
                "segment_metric": 120_000.00,
            },
        ],
    },
    "meta": {
        "request_id": "mock-req-forecast",
        "as_of": "2026-06-30T10:00:00+00:00",
        "rpc_duration_ms": 92,
        "page_size": 50,
        "total_count": 1,
        "cursor_current": None,
        "cursor_next": None,
        "has_next": False,
        "filters_applied": {"bucket": "this_month", "segment": "cleared", "installment_type_id": None},
        "sort_applied": {"sort_by": "date", "sort_dir": "desc"},
        "data_quality": None,
    },
}


# ── Test DF-8a — 200 + strict envelope shape (21-field row) ───────────────────


def test_drill_forecast_returns_200_and_envelope_shape(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_forecast_segment_drilldown",
        new=AsyncMock(return_value=_MOCK_DRILL_FORECAST),
    ):
        r = client.get(_URL_DRILL_FORECAST)

    assert r.status_code == 200
    body = r.json()

    assert set(body.keys()) == {"version", "data", "meta"}
    assert body["version"] == "1.0"

    assert set(body["meta"].keys()) == _META_KEYS
    assert isinstance(body["meta"]["filters_applied"], dict)
    assert isinstance(body["meta"]["sort_applied"], dict)

    assert set(body["data"].keys()) == _FORECAST_DATA_KEYS
    items = body["data"]["items"]
    assert isinstance(items, list)
    assert len(items) >= 1
    assert set(items[0].keys()) == _FORECAST_SEGMENT_ROW_KEYS


# ── Test DF-8b — X-Request-ID echo-back + no Cache-Control ─────────────────────


def test_drill_forecast_request_id_echoed_and_no_cache_control(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_forecast_segment_drilldown",
        new=AsyncMock(return_value=_MOCK_DRILL_FORECAST),
    ):
        r = client.get(_URL_DRILL_FORECAST, headers={"X-Request-ID": "test-rid-forecast"})

    assert r.status_code == 200
    assert r.headers["x-request-id"] == "test-rid-forecast"
    assert "cache-control" not in r.headers


# ── Test DF-8c — 503 on OdooQueryError ───────────────────────────────────────


def test_drill_forecast_odoo_unavailable_returns_503(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.collections.get_forecast_segment_drilldown",
        new=AsyncMock(side_effect=OdooQueryError("Odoo is down")),
    ):
        r = client.get(_URL_DRILL_FORECAST)

    assert r.status_code == 503
    body = r.json()
    assert "error" in body
    assert body["error"]["code"] == "odoo_unavailable"
    assert isinstance(body["error"]["message"], str)
    assert r.headers.get("x-request-id")


# ── Test DF-8d — 405 on POST ──────────────────────────────────────────────────


def test_drill_forecast_post_returns_405(client: TestClient) -> None:
    r = client.post(_URL_DRILL_FORECAST)
    assert r.status_code == 405


# ── Test DF-8e — 401 when unauthenticated ────────────────────────────────────


def test_drill_forecast_401_when_no_auth() -> None:
    c = TestClient(app, raise_server_exceptions=True)
    r = c.get(_URL_DRILL_FORECAST)  # no session
    assert r.status_code == 401, (
        f"Expected 401 for unauthenticated Collections request, got {r.status_code}"
    )


# ── Test DF-8f — FastAPI Literal-path 422 on out-of-range bucket ──────────────


def test_drill_forecast_invalid_bucket_returns_422_fastapi_validation(client: TestClient) -> None:
    # bucket is a Literal Path param, so FastAPI rejects "banana" with its own 422
    # BEFORE the handler runs — the handler's invalid_param branch is therefore
    # unreachable via HTTP, exactly like project/{id}. No service patch needed.
    r = client.get("/api/v1/collections/drilldown/forecast/banana/cleared")

    assert r.status_code == 422
    body = r.json()
    assert "detail" in body   # FastAPI validation shape
    assert "error" not in body  # NOT the handler's invalid_param shape


# ══════════════════════════════════════════════════════════════════════════════
# Auth regression — all Collections endpoints must reject unauthenticated callers
# ══════════════════════════════════════════════════════════════════════════════


def test_401_when_no_auth() -> None:
    """Collections endpoints must reject unauthenticated requests with 401.

    Added 2026-06-09 as part of the security hotfix that wired
    Depends(get_current_user) onto all 13 Collections routes.
    No service patch needed — auth is checked before the handler body runs.
    """
    c = TestClient(app, raise_server_exceptions=True)
    r = c.get(_URL)  # no session
    assert r.status_code == 401, (
        f"Expected 401 for unauthenticated Collections request, got {r.status_code}"
    )
