"""
Endpoint tests for Projects Inventory — GET /api/v1/projects-inventory/overview.

Uses FastAPI TestClient with get_inventory_overview patched — no Odoo connection.
Confirms the RBAC gate: 401 unauthenticated, 403 without the module grant, 200 with it,
plus the cache headers and the 503/500 error mapping.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_current_user
from backend.auth.models import UserRecord
from backend.core.exceptions import InventoryScopeNotFoundError, OdooQueryError
from backend.main import app
from backend.modules.projects_inventory.domain import BUCKET_ORDER
from backend.modules.projects_inventory.services.inventory_service import (
    UnknownContractStateError,
)

_URL = "/api/v1/projects-inventory/overview"
_DRILL_URL = "/api/v1/projects-inventory/drill/project/1"
_VALUE_URL = "/api/v1/projects-inventory/value-area/overview"
_OUTLIERS_URL = "/api/v1/projects-inventory/pricing-outliers/overview"

_TESTADMIN_RECORD = UserRecord(
    username="testadmin", password_hash="", modules=["*"],
    is_admin=True, is_active=True,
    created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
)

# A user explicitly granted the projects_inventory module (non-admin, scoped).
_SCOPED_RECORD = UserRecord(
    username="scoped", password_hash="", modules=["projects_inventory"],
    is_admin=False, is_active=True,
    created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
)

# A user WITHOUT the module (only crm) — must be 403.
_OTHER_MODULE_RECORD = UserRecord(
    username="other", password_hash="", modules=["crm"],
    is_admin=False, is_active=True,
    created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
)

# Six-bucket payloads (domain.BUCKET_ORDER). sold_pct = (contracted + delivered) ÷ total.
_MOCK_DATA = {
    "total_units": 23,
    "buckets": [
        {"key": "available", "count": 11, "pct": 47.83},
        {"key": "reserved", "count": 2, "pct": 8.70},
        {"key": "under_review", "count": 1, "pct": 4.35},
        {"key": "contracted", "count": 8, "pct": 34.78},
        {"key": "delivered", "count": 1, "pct": 4.35},
        {"key": "unclassified", "count": 0, "pct": 0.0},
    ],
    "sold_pct": 39.13,
    "projects": [
        {
            "project_id": 1, "project_name": "Project#New Capital", "total_units": 10,
            "buckets": [
                {"key": "available", "count": 2, "pct": 20.0},
                {"key": "reserved", "count": 1, "pct": 10.0},
                {"key": "under_review", "count": 1, "pct": 10.0},
                {"key": "contracted", "count": 5, "pct": 50.0},
                {"key": "delivered", "count": 1, "pct": 10.0},
                {"key": "unclassified", "count": 0, "pct": 0.0},
            ],
            "sold_pct": 60.0, "is_early_stage": False,
        },
        {
            "project_id": 3, "project_name": "Project#La puerta", "total_units": 5,
            "buckets": [
                {"key": "available", "count": 5, "pct": 100.0},
                {"key": "reserved", "count": 0, "pct": 0.0},
                {"key": "under_review", "count": 0, "pct": 0.0},
                {"key": "contracted", "count": 0, "pct": 0.0},
                {"key": "delivered", "count": 0, "pct": 0.0},
                {"key": "unclassified", "count": 0, "pct": 0.0},
            ],
            "sold_pct": 0.0, "is_early_stage": True,
        },
    ],
    "project_count": 2,
    "reference_date": "2026-06-18",
    "as_of": "2026-06-18T10:00:00+00:00",
    "cache_status": "fresh",
    "rpc_duration_ms": 50,
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
        "backend.api.v1.endpoints.projects_inventory.get_inventory_overview",
        new=AsyncMock(return_value=_MOCK_DATA),
    ):
        r = client.get(_URL)

    assert r.status_code == 200
    body = r.json()
    for key in (
        "total_units", "buckets", "sold_pct", "projects", "project_count",
        "reference_date", "as_of", "cache_status", "rpc_duration_ms",
    ):
        assert key in body, f"Response missing key: {key!r}"
    assert body["total_units"] == 23
    assert [b["key"] for b in body["buckets"]] == list(BUCKET_ORDER)
    assert body["projects"][1]["is_early_stage"] is True


def test_response_has_cache_headers(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.projects_inventory.get_inventory_overview",
        new=AsyncMock(return_value=_MOCK_DATA),
    ):
        r = client.get(_URL)

    assert r.status_code == 200
    assert "private, max-age=60" in r.headers.get("cache-control", "")
    assert r.headers.get("x-cache-status") == "fresh"


# ── error mapping ─────────────────────────────────────────────────────────────


def test_odoo_query_error_returns_503(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.projects_inventory.get_inventory_overview",
        new=AsyncMock(side_effect=OdooQueryError("connection refused")),
    ):
        r = client.get(_URL)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "odoo_unavailable"


def test_unexpected_exception_returns_500(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.projects_inventory.get_inventory_overview",
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
    """Authenticated but lacking the projects_inventory module -> 403."""
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
            "backend.api.v1.endpoints.projects_inventory.get_inventory_overview",
            new=AsyncMock(return_value=_MOCK_DATA),
        ):
            r = c.get(_URL)
        assert r.status_code == 200
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        if hasattr(app.state, "user_repo"):
            del app.state.user_repo


# ══════════════════════════════════════════════════════════════════════════════
# Slice 1b — drill endpoint GET /api/v1/projects-inventory/drill/{level}/{parent_id}
# ══════════════════════════════════════════════════════════════════════════════

_MOCK_DRILL = {
    "parent_level": "project",
    "parent_id": 1,
    "parent_name": "Project#New Capital",
    "child_level": "phase",
    "is_leaf": False,
    "total_units": 9,
    "buckets": [
        {"key": "available", "count": 4, "pct": 44.44},
        {"key": "reserved", "count": 1, "pct": 11.11},
        {"key": "under_review", "count": 0, "pct": 0.0},
        {"key": "contracted", "count": 3, "pct": 33.33},
        {"key": "delivered", "count": 1, "pct": 11.11},
        {"key": "unclassified", "count": 0, "pct": 0.0},
    ],
    "sold_pct": 44.44,
    "rows": [
        {
            "group_id": 10, "group_name": "Phase#1", "total_units": 8,
            "buckets": [
                {"key": "available", "count": 3, "pct": 37.5},
                {"key": "reserved", "count": 1, "pct": 12.5},
                {"key": "under_review", "count": 0, "pct": 0.0},
                {"key": "contracted", "count": 3, "pct": 37.5},
                {"key": "delivered", "count": 1, "pct": 12.5},
                {"key": "unclassified", "count": 0, "pct": 0.0},
            ],
            "sold_pct": 50.0,
        },
    ],
    "row_count": 1,
    "units": [],
    "unit_count": 0,
    "reference_date": "2026-06-18",
    "as_of": "2026-06-18T10:00:00+00:00",
    "cache_status": "fresh",
    "rpc_duration_ms": 12,
}


def test_drill_200_and_shape(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.projects_inventory.get_inventory_drill",
        new=AsyncMock(return_value=_MOCK_DRILL),
    ) as m:
        r = client.get(_DRILL_URL)
    assert r.status_code == 200
    body = r.json()
    for key in (
        "parent_level", "parent_id", "parent_name", "child_level", "is_leaf",
        "total_units", "buckets", "sold_pct", "rows", "row_count", "units",
        "unit_count", "cache_status", "rpc_duration_ms",
    ):
        assert key in body, f"Response missing key: {key!r}"
    assert body["child_level"] == "phase"
    assert body["rows"][0]["group_id"] == 10
    # The service was called with the parsed path params.
    m.assert_awaited_once_with("project", 1)
    assert "private, max-age=60" in r.headers.get("cache-control", "")
    assert r.headers.get("x-cache-status") == "fresh"


def test_drill_422_on_bad_level(client: TestClient) -> None:
    """`level` is a Literal path param → FastAPI 422 before the handler runs."""
    r = client.get("/api/v1/projects-inventory/drill/street/1")
    assert r.status_code == 422


def test_drill_422_on_non_positive_id(client: TestClient) -> None:
    """parent_id has ge=1 → 422 for 0 / negative."""
    r = client.get("/api/v1/projects-inventory/drill/project/0")
    assert r.status_code == 422


def test_drill_404_on_empty_scope(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.projects_inventory.get_inventory_drill",
        new=AsyncMock(side_effect=InventoryScopeNotFoundError("nope")),
    ):
        r = client.get(_DRILL_URL)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "scope_not_found"


def test_drill_503_on_odoo_error(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.projects_inventory.get_inventory_drill",
        new=AsyncMock(side_effect=OdooQueryError("boom")),
    ):
        r = client.get(_DRILL_URL)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "odoo_unavailable"


def test_drill_500_on_unexpected(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.projects_inventory.get_inventory_drill",
        new=AsyncMock(side_effect=RuntimeError("kaboom")),
    ):
        r = client.get(_DRILL_URL)
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "internal_error"


def test_drill_401_when_unauthenticated() -> None:
    c = TestClient(app, raise_server_exceptions=True)
    r = c.get(_DRILL_URL)
    assert r.status_code == 401


def test_drill_403_without_module_grant() -> None:
    c = _client_with(_OTHER_MODULE_RECORD)
    try:
        r = c.get(_DRILL_URL)
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "MODULE_ACCESS_DENIED"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        if hasattr(app.state, "user_repo"):
            del app.state.user_repo


# ══════════════════════════════════════════════════════════════════════════════
# Slice 2 — value-area endpoint GET /api/v1/projects-inventory/value-area/overview
# ══════════════════════════════════════════════════════════════════════════════

_MOCK_VALUE = {
    "total_units": 1735, "available_units_count": 287, "sold_units_count": 1400,
    "sold_units_with_contract_count": 1395, "sold_units_below_list_count": 664,
    "no_contract_count": 5,
    "available_list_value": 4_606_666_395.0, "available_area": 67_724.07,
    "sold_realized_value": 5_709_600_379.98, "sold_contracted_area": 286_960.70,
    "sold_list_value": 6_345_001_260.75,
    "sold_with_contract_list_value": 6_270_305_215.75,
    "sold_with_contract_area": 285_755.70, "no_contract_list_value": 74_696_045.0,
    "gap_abs": 560_704_835.77, "gap_pct": 8.94,
    "capture_pct": 91.06, "pct_units_below_list": 47.60,
    "avg_price_per_m2_realized": 19_980.71, "sold_pct_units": 80.69,
    "projects": [
        {
            "project_id": 1, "project_name": "New Capital",
            "total_units": 1401, "available_units_count": 201, "sold_units_count": 1166,
            "sold_units_with_contract_count": 1163, "sold_units_below_list_count": 538,
            "no_contract_count": 3,
            "available_list_value": 2_572_283_895.0, "available_area": 45_003.07,
            "sold_realized_value": 3_404_246_935.98, "sold_contracted_area": 214_856.70,
            "sold_list_value": 3_752_961_960.75,
            "sold_with_contract_list_value": 3_748_291_960.75,
            "sold_with_contract_area": 214_416.70, "no_contract_list_value": 4_670_000.0,
            "gap_abs": 344_045_024.77, "gap_pct": 9.18,
            "capture_pct": 90.82, "pct_units_below_list": 46.26,
            "avg_price_per_m2_realized": 15_876.78, "sold_pct_units": 83.23,
        },
    ],
    "project_count": 1,
    "reference_date": "2026-06-19", "as_of": "2026-06-19T10:00:00+00:00",
    "cache_status": "fresh", "rpc_duration_ms": 80,
}


def test_value_area_returns_200_and_keys(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.projects_inventory.get_value_area_overview",
        new=AsyncMock(return_value=_MOCK_VALUE),
    ):
        r = client.get(_VALUE_URL)
    assert r.status_code == 200
    body = r.json()
    for key in (
        "available_list_value", "sold_realized_value", "sold_list_value", "gap_abs",
        "gap_pct", "pct_units_below_list", "avg_price_per_m2_realized",
        "sold_units_count", "sold_units_with_contract_count", "projects", "project_count",
        "sold_with_contract_list_value", "sold_with_contract_area",
        "no_contract_count", "no_contract_list_value",
    ):
        assert key in body, f"Response missing key: {key!r}"
    assert body["sold_realized_value"] == 5_709_600_379.98
    assert "private, max-age=60" in r.headers.get("cache-control", "")
    assert r.headers.get("x-cache-status") == "fresh"


def test_value_area_503_on_odoo_error(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.projects_inventory.get_value_area_overview",
        new=AsyncMock(side_effect=OdooQueryError("boom")),
    ):
        r = client.get(_VALUE_URL)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "odoo_unavailable"


def test_value_area_500_on_unexpected(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.projects_inventory.get_value_area_overview",
        new=AsyncMock(side_effect=RuntimeError("kaboom")),
    ):
        r = client.get(_VALUE_URL)
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "internal_error"


def test_value_area_401_when_unauthenticated() -> None:
    c = TestClient(app, raise_server_exceptions=True)
    r = c.get(_VALUE_URL)
    assert r.status_code == 401


def test_value_area_403_without_module_grant() -> None:
    c = _client_with(_OTHER_MODULE_RECORD)
    try:
        r = c.get(_VALUE_URL)
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "MODULE_ACCESS_DENIED"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        if hasattr(app.state, "user_repo"):
            del app.state.user_repo


# ══════════════════════════════════════════════════════════════════════════════
# Slice 2.5 — pricing-outliers endpoint
# GET /api/v1/projects-inventory/pricing-outliers/overview  (module-gated, NOT admin)
# ══════════════════════════════════════════════════════════════════════════════

_MOCK_OUTLIERS = {
    "section_a": [
        {"unit_id": 6, "code": "P1-6", "project_id": 1, "project_name": "New Capital",
         "zone_name": "Zone#10", "unit_type_name": "Type#20",
         "vintage_bucket_label": "2022–2023", "sale_date": "2023-07-01",
         "realized_pm2": 35_000.0, "group_median_pm2": 20_250.0,
         "deviation_pct": 72.84, "direction": "above", "is_confirmed": True},
    ],
    "section_b": [
        {"unit_id": 7, "code": "S-7", "project_id": 1, "project_name": "New Capital",
         "unit_type_name": "Type#21", "sale_date": "2022-02-01",
         "list_total": 2_000_000.0, "realized_total": 1_000_000.0,
         "discount_pct": 50.0, "peer_median_discount_pct": None, "kind": "deep",
         "is_confirmed": False},
        {"unit_id": 6, "code": "P1-6", "project_id": 1, "project_name": "New Capital",
         "unit_type_name": "Type#20", "sale_date": "2023-07-01",
         "list_total": 3_000_000.0, "realized_total": 3_500_000.0,
         "discount_pct": -16.67, "peer_median_discount_pct": 0.0, "kind": "premium",
         "is_confirmed": True},
    ],
    "section_a_count": 1, "section_a_below_count": 0, "section_a_above_count": 1,
    "section_b_count": 2, "section_b_deep_count": 1, "section_b_premium_count": 1,
    "confirmed_count": 1,
    "insufficient_peers_count": 3, "eligible_group_count": 1, "population_count": 9,
    "projects": [
        {"project_id": 1, "project_name": "New Capital",
         "section_a_count": 1, "section_b_count": 2, "confirmed_count": 1},
        {"project_id": 2, "project_name": "Cassette",
         "section_a_count": 0, "section_b_count": 0, "confirmed_count": 0},
    ],
    "project_count": 2,
    "thresholds": {"min_group_size": 5, "iqr_mult": 1.5, "min_dev_pct": 15.0,
                   "deep_discount_pct": 25.0, "premium_pct": -10.0, "vintage_bucket_years": 2},
    "reference_date": "2026-06-22", "as_of": "2026-06-22T10:00:00+00:00",
    "cache_status": "fresh", "rpc_duration_ms": 90,
}


def test_pricing_outliers_returns_200_and_keys(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.projects_inventory.get_pricing_outliers_overview",
        new=AsyncMock(return_value=_MOCK_OUTLIERS),
    ):
        r = client.get(_OUTLIERS_URL)
    assert r.status_code == 200
    body = r.json()
    for key in (
        "section_a", "section_b", "section_a_count", "section_a_below_count",
        "section_a_above_count", "section_b_count", "section_b_deep_count",
        "section_b_premium_count", "confirmed_count", "insufficient_peers_count",
        "eligible_group_count", "population_count", "projects", "project_count",
        "thresholds",
    ):
        assert key in body, f"Response missing key: {key!r}"
    assert body["confirmed_count"] == 1
    assert body["section_b"][0]["kind"] == "deep"
    assert "private, max-age=60" in r.headers.get("cache-control", "")
    assert r.headers.get("x-cache-status") == "fresh"


def test_pricing_outliers_503_on_odoo_error(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.projects_inventory.get_pricing_outliers_overview",
        new=AsyncMock(side_effect=OdooQueryError("boom")),
    ):
        r = client.get(_OUTLIERS_URL)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "odoo_unavailable"


def test_pricing_outliers_500_on_unexpected(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.projects_inventory.get_pricing_outliers_overview",
        new=AsyncMock(side_effect=RuntimeError("kaboom")),
    ):
        r = client.get(_OUTLIERS_URL)
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "internal_error"


def test_pricing_outliers_401_when_unauthenticated() -> None:
    c = TestClient(app, raise_server_exceptions=True)
    r = c.get(_OUTLIERS_URL)
    assert r.status_code == 401


def test_pricing_outliers_403_without_module_grant() -> None:
    c = _client_with(_OTHER_MODULE_RECORD)
    try:
        r = c.get(_OUTLIERS_URL)
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "MODULE_ACCESS_DENIED"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        if hasattr(app.state, "user_repo"):
            del app.state.user_repo


def test_pricing_outliers_200_with_scoped_module_grant() -> None:
    """A non-admin user explicitly granted the module is allowed (NOT admin-only)."""
    c = _client_with(_SCOPED_RECORD)
    try:
        with patch(
            "backend.api.v1.endpoints.projects_inventory.get_pricing_outliers_overview",
            new=AsyncMock(return_value=_MOCK_OUTLIERS),
        ):
            r = c.get(_OUTLIERS_URL)
        assert r.status_code == 200
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        if hasattr(app.state, "user_repo"):
            del app.state.user_repo


# ══════════════════════════════════════════════════════════════════════════════
# Inventory Data Quality — GET /api/v1/projects-inventory/data-quality/overview
# Admin-only (require_admin_api on top of the module gate).
# ══════════════════════════════════════════════════════════════════════════════

_DQ_URL = "/api/v1/projects-inventory/data-quality/overview"

_MOCK_DQ = {
    "checks": [
        {"key": "no_contract", "count": 1, "items": [
            {"unit_id": 3637, "code": "AF135-7-404", "project_name": "New Capital",
             "defect_type": "no_contract", "detail": "amount 1,620,000"},
        ]},
        {"key": "broken_hierarchy", "count": 1, "items": [
            {"unit_id": 4321, "code": "AF155-3-702", "project_name": "New Capital",
             "defect_type": "zone_phase", "detail": "zone 26 'Zone#1' → phase 4; unit phase_id=2"},
        ]},
        {"key": "no_list_price", "count": 0, "items": []},
    ],
    "total_issues": 2,
    "check_d": {
        "key": "implausible_list_price",
        "count": 1,
        "items": [
            {"unit_id": 5501, "code": "HS-STUDIO-12", "project_name": "New Capital",
             "unit_type_name": "HS-Studio", "state": "sold", "list_pm2": 65000.0,
             "meter_price": 65000.0, "anchor_realized_pm2": 20000.0, "ratio": 3.25,
             "list_total": 3_250_000.0, "signal": "peer"},
        ],
        "tier1_count": 1, "tier2a_count": 0, "tier2b_count": 0,
        "evaluated_count": 1734, "unevaluable_count": 84,
        "thresholds": {"list_trust_k": 2.0, "type_k": 3.0, "type_spread_max": 2.5,
                       "impossible_k": 5.0, "min_group_size": 5},
    },
    "reference_date": "2026-06-19",
    "as_of": "2026-06-19T10:00:00+00:00",
    "cache_status": "fresh",
    "rpc_duration_ms": 70,
}


def test_data_quality_200_with_admin(client: TestClient) -> None:
    """The default `client` fixture is the admin record (modules=['*'], is_admin=True)."""
    with patch(
        "backend.api.v1.endpoints.projects_inventory.get_data_quality_overview",
        new=AsyncMock(return_value=_MOCK_DQ),
    ):
        r = client.get(_DQ_URL)
    assert r.status_code == 200
    body = r.json()
    for key in ("checks", "total_issues", "check_d", "reference_date", "as_of",
                "cache_status", "rpc_duration_ms"):
        assert key in body, f"Response missing key: {key!r}"
    assert body["total_issues"] == 2
    assert {c["key"] for c in body["checks"]} == {
        "no_contract", "broken_hierarchy", "no_list_price"}
    # Check D rides alongside as a separate object with its tiered counts.
    assert body["check_d"]["key"] == "implausible_list_price"
    assert body["check_d"]["count"] == 1
    assert body["check_d"]["items"][0]["signal"] == "peer"
    assert "private, max-age=60" in r.headers.get("cache-control", "")
    assert r.headers.get("x-cache-status") == "fresh"


def test_data_quality_403_without_admin() -> None:
    """A non-admin WITH the projects_inventory module passes the module gate but fails
    the admin gate → 403. (The app's global 403 handler maps every 403 to the same
    MODULE_ACCESS_DENIED envelope for API requests, so the 403 itself — not the code — is
    what proves the admin gate fired: a user who already holds the module is still denied.)"""
    c = _client_with(_SCOPED_RECORD)
    try:
        r = c.get(_DQ_URL)
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "MODULE_ACCESS_DENIED"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        if hasattr(app.state, "user_repo"):
            del app.state.user_repo


def test_data_quality_401_when_unauthenticated() -> None:
    c = TestClient(app, raise_server_exceptions=True)
    r = c.get(_DQ_URL)
    assert r.status_code == 401


def test_data_quality_503_on_odoo_error(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.projects_inventory.get_data_quality_overview",
        new=AsyncMock(side_effect=OdooQueryError("boom")),
    ):
        r = client.get(_DQ_URL)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "odoo_unavailable"


def test_data_quality_500_on_unexpected(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.projects_inventory.get_data_quality_overview",
        new=AsyncMock(side_effect=RuntimeError("kaboom")),
    ):
        r = client.get(_DQ_URL)
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "internal_error"


# ══════════════════════════════════════════════════════════════════════════════
# Contracts pipeline — GET /api/v1/projects-inventory/pipeline
# The pre-confirm funnel by stage. Module-gated (NOT admin), GET-only.
# ══════════════════════════════════════════════════════════════════════════════

_PIPELINE_URL = "/api/v1/projects-inventory/pipeline"

# awaiting_action = draft (stage/stage_label None); under_review = a named desk.
# Both lists arrive days_in_stage desc; Σ groups == total_non_cancel (2 + 1 + 3 + 1 = 7).
_MOCK_PIPELINE = {
    "awaiting_action": [
        {"contract_id": 901, "name": "C00901", "unit_id": 3608, "unit_name": "AF208-6-501",
         "days_in_stage": 95, "stage": None, "stage_label": None},
        {"contract_id": 902, "name": "C00902", "unit_id": 3609, "unit_name": "AF208-6-502",
         "days_in_stage": 58, "stage": None, "stage_label": None},
    ],
    "awaiting_action_count": 2,
    "under_review": [
        {"contract_id": 255, "name": "C00255", "unit_id": 4170,
         "unit_name": "Unit#BF170-10-702", "days_in_stage": 199,
         "stage": "finance", "stage_label": "Finance Review"},
    ],
    "under_review_count": 1,
    "confirmed_count": 3,
    "delivered_count": 1,
    "total_non_cancel": 7,
    "reference_date": "2026-07-30",
    "as_of": "2026-07-30T10:00:00+00:00",
    "cache_status": "fresh",
    "rpc_duration_ms": 140,
}


def test_pipeline_returns_200_and_keys(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.projects_inventory.get_contracts_pipeline",
        new=AsyncMock(return_value=_MOCK_PIPELINE),
    ):
        r = client.get(_PIPELINE_URL)
    assert r.status_code == 200
    body = r.json()
    for key in (
        "awaiting_action", "awaiting_action_count", "under_review", "under_review_count",
        "confirmed_count", "delivered_count", "total_non_cancel", "reference_date",
        "as_of", "cache_status", "rpc_duration_ms",
    ):
        assert key in body, f"Response missing key: {key!r}"
    # The four groups reconcile to total_non_cancel.
    assert (
        body["awaiting_action_count"] + body["under_review_count"]
        + body["confirmed_count"] + body["delivered_count"]
    ) == body["total_non_cancel"] == 7
    # Row lists are oldest-first and carry the full PipelineEntry shape.
    assert [e["days_in_stage"] for e in body["awaiting_action"]] == [95, 58]
    for entry in body["awaiting_action"] + body["under_review"]:
        for key in ("contract_id", "name", "unit_id", "unit_name", "days_in_stage",
                    "stage", "stage_label"):
            assert key in entry, f"PipelineEntry missing key: {key!r}"
    # A draft sits at no named desk; a review row carries the desk + its human label.
    assert body["awaiting_action"][0]["stage"] is None
    assert body["awaiting_action"][0]["stage_label"] is None
    assert body["under_review"][0]["stage"] == "finance"
    assert body["under_review"][0]["stage_label"] == "Finance Review"
    assert body["under_review"][0]["days_in_stage"] == 199


def test_pipeline_response_has_cache_headers(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.projects_inventory.get_contracts_pipeline",
        new=AsyncMock(return_value=_MOCK_PIPELINE),
    ):
        r = client.get(_PIPELINE_URL)
    assert r.status_code == 200
    assert "private, max-age=60" in r.headers.get("cache-control", "")
    assert r.headers.get("x-cache-status") == "fresh"


def test_pipeline_cached_status_reflected_in_header(client: TestClient) -> None:
    """X-Cache-Status echoes the payload's cache_status — 'cached' on a cache hit."""
    with patch(
        "backend.api.v1.endpoints.projects_inventory.get_contracts_pipeline",
        new=AsyncMock(return_value={**_MOCK_PIPELINE, "cache_status": "cached",
                                    "rpc_duration_ms": 0}),
    ):
        r = client.get(_PIPELINE_URL)
    assert r.status_code == 200
    assert r.headers.get("x-cache-status") == "cached"
    assert r.json()["rpc_duration_ms"] == 0


def test_pipeline_503_on_odoo_error(client: TestClient) -> None:
    with patch(
        "backend.api.v1.endpoints.projects_inventory.get_contracts_pipeline",
        new=AsyncMock(side_effect=OdooQueryError("boom")),
    ):
        r = client.get(_PIPELINE_URL)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "odoo_unavailable"


def test_pipeline_500_on_unexpected(client: TestClient) -> None:
    """The Σ-groups reconciliation RuntimeError (and any other surprise) → 500."""
    with patch(
        "backend.api.v1.endpoints.projects_inventory.get_contracts_pipeline",
        new=AsyncMock(side_effect=RuntimeError("kaboom")),
    ):
        r = client.get(_PIPELINE_URL)
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "internal_error"


def test_pipeline_500_on_unknown_contract_state(client: TestClient) -> None:
    """An unplaceable non-cancel contract state is a data verdict → 500, never a
    partial pipeline that silently drops those contracts."""
    with patch(
        "backend.api.v1.endpoints.projects_inventory.get_contracts_pipeline",
        new=AsyncMock(side_effect=UnknownContractStateError("new state 'escrow'")),
    ):
        r = client.get(_PIPELINE_URL)
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "internal_error"


def test_pipeline_401_when_unauthenticated() -> None:
    c = TestClient(app, raise_server_exceptions=True)
    r = c.get(_PIPELINE_URL)
    assert r.status_code == 401


def test_pipeline_403_without_module_grant() -> None:
    c = _client_with(_OTHER_MODULE_RECORD)
    try:
        r = c.get(_PIPELINE_URL)
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "MODULE_ACCESS_DENIED"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        if hasattr(app.state, "user_repo"):
            del app.state.user_repo


def test_pipeline_200_with_scoped_module_grant() -> None:
    """A non-admin user explicitly granted the module is allowed (NOT admin-only)."""
    c = _client_with(_SCOPED_RECORD)
    try:
        with patch(
            "backend.api.v1.endpoints.projects_inventory.get_contracts_pipeline",
            new=AsyncMock(return_value=_MOCK_PIPELINE),
        ):
            r = c.get(_PIPELINE_URL)
        assert r.status_code == 200
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        if hasattr(app.state, "user_repo"):
            del app.state.user_repo


def test_pipeline_405_on_post(client: TestClient) -> None:
    """Read-only surface: the path exists for GET only, so POST is a 405 (never a
    write attempt reaching a handler)."""
    r = client.post(_PIPELINE_URL, json={})
    assert r.status_code == 405
