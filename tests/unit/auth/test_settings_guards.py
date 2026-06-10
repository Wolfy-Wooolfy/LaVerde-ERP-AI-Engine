"""
Unit tests for Phase B admin guards: require_admin_api and require_admin_html.

Uses a mini FastAPI app for the API guard (no settings API routes exist yet in Commit 1)
and the main app's /settings route for the HTML guard.

Test matrix:
  TestRequireAdminApi:   admin→200, non-admin→403, unauthed→401
  TestRequireAdminHtml:  admin→200 (html), non-admin→403 (html), unauthed→302
  TestAdminVsModules:    is_admin ⊥ modules — admin with modules=[] passes; non-admin with ["*"] fails
"""

from unittest.mock import MagicMock

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from backend.api.deps import get_current_user, get_current_user_html, require_admin_api, require_admin_html
from backend.auth.models import UserRecord
from backend.main import app


def _user(is_admin: bool, modules: list[str] | None = None) -> UserRecord:
    return UserRecord(
        username="testadmin",
        password_hash="$2b$12$placeholder",
        modules=modules if modules is not None else ["*"],
        is_admin=is_admin,
        is_active=True,
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
    )


# ── Mini app: used to unit-test require_admin_api in isolation ────────────────

_api_guard_app = FastAPI()
_api_guard_app.add_middleware(SessionMiddleware, secret_key="test-secret-32-chars-minimum!!")


@_api_guard_app.get("/admin-only", dependencies=[Depends(require_admin_api)])
def _admin_only_endpoint():
    return {"ok": True}


# ── TestRequireAdminApi ───────────────────────────────────────────────────────


class TestRequireAdminApi:
    def test_admin_passes(self):
        mock_repo = MagicMock()
        mock_repo.get_user.return_value = _user(is_admin=True)
        _api_guard_app.dependency_overrides[get_current_user] = lambda: "testadmin"
        _api_guard_app.state.user_repo = mock_repo
        try:
            c = TestClient(_api_guard_app, raise_server_exceptions=True)
            r = c.get("/admin-only")
        finally:
            _api_guard_app.dependency_overrides.pop(get_current_user, None)
        assert r.status_code == 200

    def test_non_admin_gets_403(self):
        mock_repo = MagicMock()
        mock_repo.get_user.return_value = _user(is_admin=False)
        _api_guard_app.dependency_overrides[get_current_user] = lambda: "testadmin"
        _api_guard_app.state.user_repo = mock_repo
        try:
            c = TestClient(_api_guard_app, raise_server_exceptions=False)
            r = c.get("/admin-only")
        finally:
            _api_guard_app.dependency_overrides.pop(get_current_user, None)
        assert r.status_code == 403

    def test_unauthenticated_gets_401(self):
        # No override — no session cookie → get_current_user raises 401
        _api_guard_app.dependency_overrides.pop(get_current_user, None)
        c = TestClient(_api_guard_app, raise_server_exceptions=False)
        r = c.get("/admin-only")
        assert r.status_code == 401


# ── TestRequireAdminHtml ──────────────────────────────────────────────────────


class TestRequireAdminHtml:
    def test_admin_passes_html(self):
        mock_repo = MagicMock()
        mock_repo.get_user.return_value = _user(is_admin=True)
        app.dependency_overrides[get_current_user_html] = lambda: "testadmin"
        try:
            with TestClient(app, raise_server_exceptions=True) as c:
                c.app.state.user_repo = mock_repo
                r = c.get("/settings")
        finally:
            app.dependency_overrides.pop(get_current_user_html, None)
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")

    def test_non_admin_gets_403_html(self):
        mock_repo = MagicMock()
        mock_repo.get_user.return_value = _user(is_admin=False)
        app.dependency_overrides[get_current_user_html] = lambda: "testadmin"
        try:
            with TestClient(app, raise_server_exceptions=False) as c:
                c.app.state.user_repo = mock_repo
                r = c.get("/settings", headers={"Accept": "text/html"})
        finally:
            app.dependency_overrides.pop(get_current_user_html, None)
        assert r.status_code == 403
        assert "text/html" in r.headers.get("content-type", "")

    def test_unauthenticated_302_html(self):
        # No override — no session → get_current_user_html raises 302 to /login
        app.dependency_overrides.pop(get_current_user_html, None)
        c = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
        r = c.get("/settings")
        assert r.status_code == 302
        assert "/login" in r.headers.get("location", "")


# ── TestAdminVsModules ────────────────────────────────────────────────────────


class TestAdminVsModules:
    def test_admin_with_no_modules_passes_admin_guard(self):
        """is_admin=True, modules=[] — admin guard allows (is_admin independent of modules, A1.D3)."""
        mock_repo = MagicMock()
        mock_repo.get_user.return_value = _user(is_admin=True, modules=[])
        _api_guard_app.dependency_overrides[get_current_user] = lambda: "testadmin"
        _api_guard_app.state.user_repo = mock_repo
        try:
            c = TestClient(_api_guard_app, raise_server_exceptions=True)
            r = c.get("/admin-only")
        finally:
            _api_guard_app.dependency_overrides.pop(get_current_user, None)
        assert r.status_code == 200

    def test_non_admin_with_wildcard_modules_gets_403(self):
        """is_admin=False, modules=['*'] — admin guard rejects (is_admin independent of modules, A1.D3)."""
        mock_repo = MagicMock()
        mock_repo.get_user.return_value = _user(is_admin=False, modules=["*"])
        _api_guard_app.dependency_overrides[get_current_user] = lambda: "testadmin"
        _api_guard_app.state.user_repo = mock_repo
        try:
            c = TestClient(_api_guard_app, raise_server_exceptions=False)
            r = c.get("/admin-only")
        finally:
            _api_guard_app.dependency_overrides.pop(get_current_user, None)
        assert r.status_code == 403
