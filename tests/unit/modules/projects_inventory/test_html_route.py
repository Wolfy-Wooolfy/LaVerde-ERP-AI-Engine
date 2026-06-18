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
