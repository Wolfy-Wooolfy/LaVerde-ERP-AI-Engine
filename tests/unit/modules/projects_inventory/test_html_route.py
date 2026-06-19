"""
Route/RBAC tests for the Projects Inventory HTML page —
GET /projects-inventory/dashboard.

Mirrors the marketing-attribution HTML page gating: 302 (unauthenticated, → /login),
403 (authenticated but without the module grant), 200 (with the module). Uses
dependency overrides + a mocked user_repo and patches get_inventory_overview, so no
session bootstrap or Odoo connection is required.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from backend.api.deps import get_current_user_html
from backend.auth.models import UserRecord
from backend.main import app

_URL = "/projects-inventory/dashboard"
_VALUE_URL = "/projects-inventory/value-area"

# A user explicitly granted the projects_inventory module (non-admin, scoped).
_SCOPED_RECORD = UserRecord(
    username="scoped", password_hash="", modules=["projects_inventory"],
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
    """Authenticated but lacking projects_inventory → 403 (rendered as HTML)."""
    c = _client_with(_OTHER_MODULE_RECORD)
    try:
        r = c.get(_URL, headers={"Accept": "text/html"})
        assert r.status_code == 403
        assert "text/html" in r.headers.get("content-type", "")
    finally:
        _cleanup()


# ── 200 with the module grant ──────────────────────────────────────────────────


def test_200_with_scoped_module_grant() -> None:
    """A non-admin user granted the module gets the rendered page with the key labels."""
    c = _client_with(_SCOPED_RECORD)
    try:
        with patch(
            "backend.api.v1.endpoints.dashboard.get_inventory_overview",
            new=AsyncMock(return_value=_MOCK_DATA),
        ):
            r = c.get(_URL)
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")
        body = r.text
        # Page title / KPI labels (EN default).
        assert "Inventory &amp; Availability" in body or "Inventory & Availability" in body
        assert "Total units" in body
        assert "By project" in body
        # Status labels.
        assert "Available" in body
        assert "Reserved" in body
        assert "Contracted" in body
        # Project rows rendered from the mock.
        assert "Project#New Capital" in body
        assert "Project#La puerta" in body
        assert "60.0" in body                 # New Capital sold_pct, round(1)
        # Early-stage badge for the shell project.
        assert "Early stage" in body
        # Sidebar entry present for a user with the module.
        assert 'href="/projects-inventory/dashboard"' in body
    finally:
        _cleanup()


# ── Slice 1b — drill-down wiring rendered into the page ────────────────────────


def test_drill_wiring_present_in_page() -> None:
    """Project cards are drill triggers; the panel partial, controller JS, the strings
    object, and the breadcrumb root are all rendered."""
    c = _client_with(_SCOPED_RECORD)
    try:
        with patch(
            "backend.api.v1.endpoints.dashboard.get_inventory_overview",
            new=AsyncMock(return_value=_MOCK_DATA),
        ):
            r = c.get(_URL)
        assert r.status_code == 200
        body = r.text
        # Each project card carries the drill trigger data-attributes.
        assert 'data-pi-drill-level="project"' in body
        assert 'data-pi-drill-id="1"' in body
        assert 'data-pi-drill-id="3"' in body
        assert 'data-pi-drill-name="Project#New Capital"' in body
        # The drill panel partial is rendered (portal block).
        assert 'id="pi-dd-panel"' in body
        assert 'id="pi-dd-breadcrumb"' in body
        # The breadcrumb root crumb + its label.
        assert 'id="pi-dd-root-crumb"' in body
        assert "Portfolio" in body
        # The controller JS is included and the strings object injected.
        assert "/static/js/projects_inventory_drill.js" in body
        assert "window.PROJINV_STRINGS" in body
        # A level label string is present in the strings block.
        assert "Phases" in body
    finally:
        _cleanup()


# ── Slice 2 — Value & Area HTML page ───────────────────────────────────────────

_MOCK_VALUE = {
    "total_units": 1735, "available_units_count": 287, "sold_units_count": 1400,
    "sold_units_with_contract_count": 1395, "sold_units_below_list_count": 664,
    "available_list_value": 4_606_666_395.0, "available_area": 67_724.07,
    "sold_realized_value": 5_709_600_379.98, "sold_contracted_area": 286_960.70,
    "sold_list_value": 6_345_001_260.75, "gap_abs": 635_400_880.77, "gap_pct": 10.01,
    "capture_pct": 89.99, "pct_units_below_list": 47.60,
    "avg_price_per_m2_realized": 19_896.80, "sold_pct_units": 80.69,
    "projects": [
        {
            "project_id": 1, "project_name": "New Capital",
            "total_units": 1401, "available_units_count": 201, "sold_units_count": 1166,
            "sold_units_with_contract_count": 1163, "sold_units_below_list_count": 538,
            "available_list_value": 2_572_283_895.0, "available_area": 45_003.07,
            "sold_realized_value": 3_404_246_935.98, "sold_contracted_area": 214_856.70,
            "sold_list_value": 3_752_961_960.75, "gap_abs": 348_715_024.77, "gap_pct": 9.29,
            "capture_pct": 90.71, "pct_units_below_list": 46.26,
            "avg_price_per_m2_realized": 15_844.27, "sold_pct_units": 83.23,
        },
        {
            "project_id": 2, "project_name": "Cassette",
            "total_units": 334, "available_units_count": 86, "sold_units_count": 234,
            "sold_units_with_contract_count": 232, "sold_units_below_list_count": 126,
            "available_list_value": 2_034_382_500.0, "available_area": 22_721.0,
            "sold_realized_value": 2_305_353_444.0, "sold_contracted_area": 72_104.0,
            "sold_list_value": 2_592_039_300.0, "gap_abs": 286_685_856.0, "gap_pct": 11.06,
            "capture_pct": 88.94, "pct_units_below_list": 54.31,
            "avg_price_per_m2_realized": 31_972.62, "sold_pct_units": 70.06,
        },
    ],
    "project_count": 2,
    "reference_date": "2026-06-19", "as_of": "2026-06-19T10:00:00+00:00",
    "cache_status": "fresh", "rpc_duration_ms": 80,
}


def test_value_area_200_with_scoped_module_grant() -> None:
    """A non-admin user granted the module gets the rendered Value & Area page."""
    c = _client_with(_SCOPED_RECORD)
    try:
        with patch(
            "backend.api.v1.endpoints.dashboard.get_value_area_overview",
            new=AsyncMock(return_value=_MOCK_VALUE),
        ):
            r = c.get(_VALUE_URL)
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")
        body = r.text
        # Page title + KPI labels (EN default).
        assert "Value &amp; Area" in body or "Value & Area" in body
        assert "actual value" in body
        assert "if at list price" in body
        # Both scoped projects rendered; La Puerta absent.
        assert "New Capital" in body
        assert "Cassette" in body
        assert "La puerta" not in body
        # Realized-as-contracted caveat is present (honesty requirement).
        assert "contracted value" in body
        # Sidebar entry for the new page present for a user with the module.
        assert 'href="/projects-inventory/value-area"' in body
    finally:
        _cleanup()


def test_value_area_403_without_module_grant() -> None:
    c = _client_with(_OTHER_MODULE_RECORD)
    try:
        r = c.get(_VALUE_URL, headers={"Accept": "text/html"})
        assert r.status_code == 403
    finally:
        _cleanup()


def test_value_area_unauthenticated_redirects_to_login() -> None:
    c = TestClient(app, raise_server_exceptions=True, follow_redirects=False)
    r = c.get(_VALUE_URL)
    assert r.status_code == 302
    assert "/login" in r.headers.get("location", "")
