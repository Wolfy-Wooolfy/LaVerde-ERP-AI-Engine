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
from backend.core.exceptions import OdooQueryError
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
