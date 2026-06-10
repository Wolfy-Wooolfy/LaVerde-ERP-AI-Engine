"""Phase B integration tests — Settings API (admin user management).

Sections:
  A — Auth matrix: all 6 endpoints × admin / non-admin / unauthed
  B — CRUD: list, create, update-modules, status toggle, admin toggle, reset-password
  C — Lockout rules (integration coverage for L1 + L2; L3/L4 are unit-only per Q8)
  D — Settings page visibility and sidebar link
"""

import pytest
from fastapi.testclient import TestClient

from backend.main import app

# authed_client, hr_only_client, second_admin_client are in tests/integration/conftest.py


@pytest.fixture(scope="module")
def unauthed_client():
    """TestClient with no session cookie — verifies 401/302 on unauthenticated requests."""
    with TestClient(app) as c:
        yield c


# ── Section A — Auth matrix ───────────────────────────────────────────────────

_ENDPOINTS = [
    ("GET",   "/api/v1/settings/users",                        None),
    ("POST",  "/api/v1/settings/users",                        {"username": "b_mx", "password": "testpass12", "modules": [], "is_admin": False}),
    ("PATCH", "/api/v1/settings/users/b_mx/modules",           {"modules": ["*"]}),
    ("PATCH", "/api/v1/settings/users/b_mx/status",            {"is_active": True}),
    ("PATCH", "/api/v1/settings/users/b_mx/admin",             {"is_admin": True}),
    ("POST",  "/api/v1/settings/users/b_mx/reset-password",    {"new_password": "testpass12"}),
]


class TestSettingsAuthMatrix:
    @pytest.mark.parametrize("method,path,body", _ENDPOINTS)
    def test_admin_not_blocked(self, authed_client, method, path, body):
        kwargs = {"json": body} if body is not None else {}
        r = getattr(authed_client, method.lower())(path, **kwargs)
        assert r.status_code not in (401, 403), f"{method} {path} → {r.status_code} {r.text[:120]}"

    @pytest.mark.parametrize("method,path,body", _ENDPOINTS)
    def test_non_admin_gets_403(self, hr_only_client, method, path, body):
        kwargs = {"json": body} if body is not None else {}
        r = getattr(hr_only_client, method.lower())(path, **kwargs)
        assert r.status_code == 403, f"{method} {path} → {r.status_code}"

    @pytest.mark.parametrize("method,path,body", _ENDPOINTS)
    def test_unauthed_gets_401(self, unauthed_client, method, path, body):
        kwargs = {"json": body} if body is not None else {}
        r = getattr(unauthed_client, method.lower())(path, **kwargs)
        assert r.status_code == 401, f"{method} {path} → {r.status_code}"


# ── Section B — CRUD ──────────────────────────────────────────────────────────


class TestUserCRUD:
    def test_list_users_contains_testadmin(self, authed_client):
        r = authed_client.get("/api/v1/settings/users")
        assert r.status_code == 200
        usernames = [u["username"] for u in r.json()["data"]["users"]]
        assert "testadmin" in usernames

    def test_list_users_no_password_fields(self, authed_client):
        r = authed_client.get("/api/v1/settings/users")
        assert r.status_code == 200
        assert "password_hash" not in r.text
        assert "password" not in r.text

    def test_create_user_201(self, authed_client):
        r = authed_client.post("/api/v1/settings/users", json={
            "username": "b_created",
            "password": "testpass12",
            "modules": ["hr"],
            "is_admin": False,
        })
        assert r.status_code == 201
        u = r.json()["data"]
        assert u["username"] == "b_created"
        assert u["modules"] == ["hr"]
        assert u["is_admin"] is False
        assert u["is_active"] is True
        assert "password" not in r.text

    def test_create_empty_modules_allowed(self, authed_client):
        r = authed_client.post("/api/v1/settings/users", json={
            "username": "b_no_mods",
            "password": "testpass12",
            "modules": [],
            "is_admin": False,
        })
        assert r.status_code == 201
        assert r.json()["data"]["modules"] == []

    def test_create_duplicate_409(self, authed_client):
        r = authed_client.post("/api/v1/settings/users", json={
            "username": "testadmin",
            "password": "testpass12",
            "modules": [],
            "is_admin": False,
        })
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "USERNAME_EXISTS"

    def test_create_invalid_username_422(self, authed_client):
        r = authed_client.post("/api/v1/settings/users", json={
            "username": "has space",
            "password": "testpass12",
            "modules": [],
            "is_admin": False,
        })
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "INVALID_USERNAME"

    def test_create_short_password_422(self, authed_client):
        r = authed_client.post("/api/v1/settings/users", json={
            "username": "b_short_pw",
            "password": "short",
            "modules": [],
            "is_admin": False,
        })
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "PASSWORD_TOO_SHORT"

    def test_create_invalid_module_422(self, authed_client):
        r = authed_client.post("/api/v1/settings/users", json={
            "username": "b_bad_mod",
            "password": "testpass12",
            "modules": ["unknown_mod"],
            "is_admin": False,
        })
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "INVALID_MODULE"

    def test_update_modules_200(self, authed_client):
        # idempotent create, then update
        authed_client.post("/api/v1/settings/users", json={
            "username": "b_mod_target",
            "password": "testpass12",
            "modules": ["hr"],
            "is_admin": False,
        })
        r = authed_client.patch("/api/v1/settings/users/b_mod_target/modules",
                                json={"modules": ["hr", "crm"]})
        assert r.status_code == 200
        assert sorted(r.json()["data"]["modules"]) == ["crm", "hr"]

    def test_update_modules_empty_allowed(self, authed_client):
        authed_client.post("/api/v1/settings/users", json={
            "username": "b_mod_target",
            "password": "testpass12",
            "modules": ["hr"],
            "is_admin": False,
        })
        r = authed_client.patch("/api/v1/settings/users/b_mod_target/modules",
                                json={"modules": []})
        assert r.status_code == 200
        assert r.json()["data"]["modules"] == []

    def test_update_modules_unknown_user_404(self, authed_client):
        r = authed_client.patch("/api/v1/settings/users/nonexistent_xyz/modules",
                                json={"modules": ["hr"]})
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "USER_NOT_FOUND"

    def test_update_modules_invalid_422(self, authed_client):
        r = authed_client.patch("/api/v1/settings/users/testadmin/modules",
                                json={"modules": ["not_a_module"]})
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "INVALID_MODULE"

    def test_deactivate_then_activate(self, authed_client):
        authed_client.post("/api/v1/settings/users", json={
            "username": "b_toggle",
            "password": "testpass12",
            "modules": [],
            "is_admin": False,
        })
        deactivate = authed_client.patch("/api/v1/settings/users/b_toggle/status",
                                        json={"is_active": False})
        assert deactivate.status_code == 200
        assert deactivate.json()["data"]["is_active"] is False

        activate = authed_client.patch("/api/v1/settings/users/b_toggle/status",
                                      json={"is_active": True})
        assert activate.status_code == 200
        assert activate.json()["data"]["is_active"] is True

    def test_grant_admin(self, authed_client):
        authed_client.post("/api/v1/settings/users", json={
            "username": "b_admin_target",
            "password": "testpass12",
            "modules": ["*"],
            "is_admin": False,
        })
        r = authed_client.patch("/api/v1/settings/users/b_admin_target/admin",
                                json={"is_admin": True})
        assert r.status_code == 200
        assert r.json()["data"]["is_admin"] is True

    def test_revoke_admin_when_two_exist(self, authed_client):
        # ensure b_admin_target is admin (idempotent)
        authed_client.post("/api/v1/settings/users", json={
            "username": "b_admin_target",
            "password": "testpass12",
            "modules": ["*"],
            "is_admin": False,
        })
        authed_client.patch("/api/v1/settings/users/b_admin_target/admin",
                            json={"is_admin": True})
        # testadmin + b_admin_target = 2 active admins; revoke should succeed
        r = authed_client.patch("/api/v1/settings/users/b_admin_target/admin",
                                json={"is_admin": False})
        assert r.status_code == 200
        assert r.json()["data"]["is_admin"] is False

    def test_reset_password_200_no_hash_returned(self, authed_client):
        authed_client.post("/api/v1/settings/users", json={
            "username": "b_reset_pw",
            "password": "testpass12",
            "modules": [],
            "is_admin": False,
        })
        r = authed_client.post("/api/v1/settings/users/b_reset_pw/reset-password",
                               json={"new_password": "newpass123"})
        assert r.status_code == 200
        assert "password" not in r.text
        assert "hash" not in r.text
        d = r.json()["data"]
        assert "username" in d
        assert "updated_at" in d

    def test_reset_password_short_422(self, authed_client):
        r = authed_client.post("/api/v1/settings/users/testadmin/reset-password",
                               json={"new_password": "short"})
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "PASSWORD_TOO_SHORT"

    def test_reset_password_unknown_user_404(self, authed_client):
        r = authed_client.post("/api/v1/settings/users/nobody_xyz/reset-password",
                               json={"new_password": "newpass123"})
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "USER_NOT_FOUND"

    def test_update_status_unknown_user_404(self, authed_client):
        r = authed_client.patch("/api/v1/settings/users/nobody_xyz/status",
                                json={"is_active": True})
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "USER_NOT_FOUND"

    def test_update_admin_unknown_user_404(self, authed_client):
        r = authed_client.patch("/api/v1/settings/users/nobody_xyz/admin",
                                json={"is_admin": True})
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "USER_NOT_FOUND"


# ── Section C — Lockout (integration: L1 + L2) ───────────────────────────────


class TestLockoutIntegration:
    def test_l1_cannot_deactivate_self(self, authed_client):
        """L1: admin cannot deactivate their own account."""
        r = authed_client.patch("/api/v1/settings/users/testadmin/status",
                                json={"is_active": False})
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "SELF_LOCKOUT_DEACTIVATION"

    def test_l1_can_activate_self(self, authed_client):
        """Activating own account is never blocked by L1."""
        r = authed_client.patch("/api/v1/settings/users/testadmin/status",
                                json={"is_active": True})
        assert r.status_code == 200

    def test_l2_cannot_demote_self(self, authed_client):
        """L2: admin cannot remove their own admin role."""
        r = authed_client.patch("/api/v1/settings/users/testadmin/admin",
                                json={"is_admin": False})
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "SELF_LOCKOUT_DEMOTE"

    def test_l2_can_grant_admin_to_self(self, authed_client):
        """Granting admin to self is never blocked by L2."""
        r = authed_client.patch("/api/v1/settings/users/testadmin/admin",
                                json={"is_admin": True})
        assert r.status_code == 200


# ── Section D — Settings page and sidebar visibility ─────────────────────────


class TestSettingsPage:
    def test_admin_settings_page_200(self, authed_client):
        r = authed_client.get("/settings")
        assert r.status_code == 200

    def test_admin_sees_settings_link_in_sidebar(self, authed_client):
        r = authed_client.get("/settings")
        assert r.status_code == 200
        assert 'href="/settings"' in r.text

    def test_non_admin_settings_page_403(self, hr_only_client):
        r = hr_only_client.get("/settings")
        assert r.status_code == 403

    def test_unauthed_settings_page_redirects_to_login(self, unauthed_client):
        r = unauthed_client.get("/settings", follow_redirects=False)
        assert r.status_code == 302
        assert "/login" in r.headers["location"]

    def test_non_admin_sidebar_no_settings_link(self, hr_only_client):
        r = hr_only_client.get("/hr/dashboard")
        if r.status_code != 200:
            pytest.skip(f"/hr/dashboard → {r.status_code}; Odoo unavailable, skipping sidebar check")
        assert 'href="/settings"' not in r.text
