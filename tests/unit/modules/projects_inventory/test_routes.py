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

_URL = "/api/v1/projects-inventory/overview"
_DRILL_URL = "/api/v1/projects-inventory/drill/project/1"
_VALUE_URL = "/api/v1/projects-inventory/value-area/overview"

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

_MOCK_DATA = {
    "total_units": 23,
    "buckets": [
        {"key": "available", "count": 11, "pct": 47.83},
        {"key": "reserved", "count": 3, "pct": 13.04},
        {"key": "contracted", "count": 9, "pct": 39.13},
    ],
    "sold_pct": 39.13,
    "projects": [
        {
            "project_id": 1, "project_name": "Project#New Capital", "total_units": 10,
            "buckets": [
                {"key": "available", "count": 2, "pct": 20.0},
                {"key": "reserved", "count": 2, "pct": 20.0},
                {"key": "contracted", "count": 6, "pct": 60.0},
            ],
            "sold_pct": 60.0, "is_early_stage": False,
        },
        {
            "project_id": 3, "project_name": "Project#La puerta", "total_units": 5,
            "buckets": [
                {"key": "available", "count": 5, "pct": 100.0},
                {"key": "reserved", "count": 0, "pct": 0.0},
                {"key": "contracted", "count": 0, "pct": 0.0},
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
    assert [b["key"] for b in body["buckets"]] == ["available", "reserved", "contracted"]
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
        {"key": "contracted", "count": 4, "pct": 44.44},
    ],
    "sold_pct": 44.44,
    "rows": [
        {
            "group_id": 10, "group_name": "Phase#1", "total_units": 8,
            "buckets": [
                {"key": "available", "count": 3, "pct": 37.5},
                {"key": "reserved", "count": 1, "pct": 12.5},
                {"key": "contracted", "count": 4, "pct": 50.0},
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
