"""Unit tests for Phase B self-lockout and last-admin protection (L1–L4).

Uses a mini FastAPI app with settings_router mounted without auth guards —
guards are tested separately in test_settings_guards.py.
"""

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from backend.api.deps import get_current_user
from backend.api.v1.endpoints.settings import router as settings_router
from backend.auth.models import UserRecord

# ── helpers ───────────────────────────────────────────────────────────────────

_PH = "$2b$12$placeholder"
_TS = "2026-01-01T00:00:00"


def _admin_record(username: str = "target", is_active: bool = True) -> UserRecord:
    return UserRecord(username=username, password_hash=_PH, modules=["*"],
                      is_admin=True, is_active=is_active, created_at=_TS, updated_at=_TS)


def _non_admin_record(username: str = "target") -> UserRecord:
    return UserRecord(username=username, password_hash=_PH, modules=["crm"],
                      is_admin=False, is_active=True, created_at=_TS, updated_at=_TS)


# Mini app — no require_admin_api guard; tests the endpoint business logic only
_test_app = FastAPI()
_test_app.add_middleware(SessionMiddleware, secret_key="test-secret-32-chars-minimum!!")
_test_app.include_router(settings_router)


def _client(requesting_username: str, mock_repo: MagicMock) -> TestClient:
    _test_app.dependency_overrides[get_current_user] = lambda: requesting_username
    _test_app.state.user_repo = mock_repo
    return TestClient(_test_app, raise_server_exceptions=False)


# ── L1 — self-deactivation ────────────────────────────────────────────────────


class TestL1SelfDeactivation:
    def test_cannot_deactivate_self(self):
        c = _client("target", MagicMock())
        r = c.patch("/users/target/status", json={"is_active": False})
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "SELF_LOCKOUT_DEACTIVATION"

    def test_l1_fires_before_db_read(self):
        mock_repo = MagicMock()
        c = _client("target", mock_repo)
        c.patch("/users/target/status", json={"is_active": False})
        mock_repo.get_user.assert_not_called()
        mock_repo.count_active_admins.assert_not_called()

    def test_can_activate_self(self):
        mock_repo = MagicMock()
        mock_repo.get_user.return_value = _admin_record("target")
        mock_repo.update_user.return_value = _admin_record("target")
        c = _client("target", mock_repo)
        r = c.patch("/users/target/status", json={"is_active": True})
        assert r.status_code == 200

    def test_can_deactivate_other_user(self):
        mock_repo = MagicMock()
        mock_repo.get_user.return_value = _non_admin_record("other")
        mock_repo.update_user.return_value = _non_admin_record("other")
        c = _client("admin", mock_repo)
        r = c.patch("/users/other/status", json={"is_active": False})
        assert r.status_code == 200


# ── L2 — self-demote ──────────────────────────────────────────────────────────


class TestL2SelfDemote:
    def test_cannot_demote_self(self):
        c = _client("target", MagicMock())
        r = c.patch("/users/target/admin", json={"is_admin": False})
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "SELF_LOCKOUT_DEMOTE"

    def test_l2_fires_before_db_read(self):
        mock_repo = MagicMock()
        c = _client("target", mock_repo)
        c.patch("/users/target/admin", json={"is_admin": False})
        mock_repo.get_user.assert_not_called()
        mock_repo.count_active_admins.assert_not_called()

    def test_can_grant_admin_to_self(self):
        mock_repo = MagicMock()
        mock_repo.get_user.return_value = _non_admin_record("target")
        mock_repo.update_user.return_value = _admin_record("target")
        c = _client("target", mock_repo)
        r = c.patch("/users/target/admin", json={"is_admin": True})
        assert r.status_code == 200

    def test_can_demote_other_admin(self):
        mock_repo = MagicMock()
        mock_repo.get_user.return_value = _admin_record("other")
        mock_repo.count_active_admins.return_value = 2
        mock_repo.update_user.return_value = _admin_record("other")
        c = _client("admin", mock_repo)
        r = c.patch("/users/other/admin", json={"is_admin": False})
        assert r.status_code == 200


# ── L3 — last-admin deactivation ─────────────────────────────────────────────


class TestL3LastAdminDeactivation:
    def test_cannot_deactivate_last_admin(self):
        mock_repo = MagicMock()
        mock_repo.get_user.return_value = _admin_record("other")
        mock_repo.count_active_admins.return_value = 1
        c = _client("admin", mock_repo)
        r = c.patch("/users/other/status", json={"is_active": False})
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "LAST_ADMIN_PROTECTION"

    def test_can_deactivate_when_two_admins(self):
        mock_repo = MagicMock()
        mock_repo.get_user.return_value = _admin_record("other")
        mock_repo.count_active_admins.return_value = 2
        mock_repo.update_user.return_value = _admin_record("other", is_active=False)
        c = _client("admin", mock_repo)
        r = c.patch("/users/other/status", json={"is_active": False})
        assert r.status_code == 200

    def test_can_deactivate_non_admin(self):
        mock_repo = MagicMock()
        mock_repo.get_user.return_value = _non_admin_record("other")
        mock_repo.update_user.return_value = _non_admin_record("other")
        c = _client("admin", mock_repo)
        r = c.patch("/users/other/status", json={"is_active": False})
        assert r.status_code == 200
        mock_repo.count_active_admins.assert_not_called()

    def test_l1_takes_priority_over_l3_for_self(self):
        # Even if the DB would say count=1, L1 fires first (no DB call)
        mock_repo = MagicMock()
        mock_repo.count_active_admins.return_value = 1
        c = _client("target", mock_repo)
        r = c.patch("/users/target/status", json={"is_active": False})
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "SELF_LOCKOUT_DEACTIVATION"
        mock_repo.count_active_admins.assert_not_called()


# ── L4 — last-admin demote ────────────────────────────────────────────────────


class TestL4LastAdminDemote:
    def test_cannot_demote_last_admin(self):
        mock_repo = MagicMock()
        mock_repo.get_user.return_value = _admin_record("other")
        mock_repo.count_active_admins.return_value = 1
        c = _client("admin", mock_repo)
        r = c.patch("/users/other/admin", json={"is_admin": False})
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "LAST_ADMIN_PROTECTION"

    def test_can_demote_when_two_admins(self):
        mock_repo = MagicMock()
        mock_repo.get_user.return_value = _admin_record("other")
        mock_repo.count_active_admins.return_value = 2
        mock_repo.update_user.return_value = _non_admin_record("other")
        c = _client("admin", mock_repo)
        r = c.patch("/users/other/admin", json={"is_admin": False})
        assert r.status_code == 200

    def test_l4_not_triggered_when_granting_admin(self):
        # Granting admin (is_admin=True) never hits L4
        mock_repo = MagicMock()
        mock_repo.get_user.return_value = _non_admin_record("other")
        mock_repo.update_user.return_value = _admin_record("other")
        c = _client("admin", mock_repo)
        r = c.patch("/users/other/admin", json={"is_admin": True})
        assert r.status_code == 200
        mock_repo.count_active_admins.assert_not_called()


# ── Password validation ───────────────────────────────────────────────────────


class TestPasswordValidation:
    def test_create_short_password_422(self):
        mock_repo = MagicMock()
        c = _client("admin", mock_repo)
        r = c.post("/users", json={
            "username": "validuser",
            "password": "short",
            "modules": [],
            "is_admin": False,
        })
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "PASSWORD_TOO_SHORT"

    def test_create_exactly_8_chars_accepted(self):
        mock_repo = MagicMock()
        mock_repo.create_user.return_value = _non_admin_record("validuser")
        c = _client("admin", mock_repo)
        r = c.post("/users", json={
            "username": "validuser",
            "password": "abcd1234",
            "modules": [],
            "is_admin": False,
        })
        assert r.status_code == 201

    def test_reset_short_password_422(self):
        mock_repo = MagicMock()
        mock_repo.get_user.return_value = _non_admin_record("target")
        c = _client("admin", mock_repo)
        r = c.post("/users/target/reset-password", json={"new_password": "short"})
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "PASSWORD_TOO_SHORT"

    def test_password_not_in_create_response(self):
        mock_repo = MagicMock()
        mock_repo.create_user.return_value = _non_admin_record("validuser")
        c = _client("admin", mock_repo)
        r = c.post("/users", json={
            "username": "validuser",
            "password": "testpass12",
            "modules": [],
            "is_admin": False,
        })
        assert r.status_code == 201
        assert "password" not in r.text
