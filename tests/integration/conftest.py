"""Shared fixtures for integration tests."""

import pytest
from fastapi.testclient import TestClient

from backend.auth.password import hash_password
from backend.auth.repository import SQLiteUserRepository
from backend.core.config import settings
from backend.main import app

_TESTPASS_HASH = hash_password("testpass")


def _ensure_user(
    username: str,
    modules: list[str],
    *,
    is_admin: bool = False,
) -> None:
    """Idempotently create a test user in the test DB."""
    repo = SQLiteUserRepository(settings.USER_DB_PATH)
    try:
        repo.create_user(
            username=username,
            password_hash=_TESTPASS_HASH,
            modules=modules,
            is_admin=is_admin,
            is_active=True,
        )
    except ValueError:
        pass  # already exists


@pytest.fixture(scope="module")
def authed_client():
    """Module-scoped TestClient with a valid session cookie (amortises bcrypt cost)."""
    with TestClient(app) as c:
        r = c.post(
            "/login",
            data={"username": "testadmin", "password": "testpass", "next": "/dashboard"},
            follow_redirects=False,
        )
        assert r.status_code == 303, f"Login failed: {r.status_code} — {r.text[:200]}"
        yield c


@pytest.fixture(scope="module")
def hr_only_client():
    """TestClient authenticated as hr_only (modules=['hr'])."""
    with TestClient(app) as c:
        # Lifespan just ran → testadmin seeded if DB was empty.
        # Now add hr_only (idempotent if this fixture ran in a prior module).
        _ensure_user("hr_only", ["hr"])
        r = c.post(
            "/login",
            data={"username": "hr_only", "password": "testpass"},
            follow_redirects=False,
        )
        assert r.status_code == 303, f"hr_only login failed: {r.status_code} {r.text[:100]}"
        yield c


@pytest.fixture(scope="module")
def coll_ca_client():
    """TestClient authenticated as coll_ca (modules=['collections','customer_accounts'])."""
    with TestClient(app) as c:
        _ensure_user("coll_ca", ["collections", "customer_accounts"])
        r = c.post(
            "/login",
            data={"username": "coll_ca", "password": "testpass"},
            follow_redirects=False,
        )
        assert r.status_code == 303, f"coll_ca login failed: {r.status_code} {r.text[:100]}"
        yield c


@pytest.fixture(scope="module")
def no_modules_client():
    """TestClient authenticated as no_modules (modules=[])."""
    with TestClient(app) as c:
        _ensure_user("no_modules", [])
        r = c.post(
            "/login",
            data={"username": "no_modules", "password": "testpass"},
            follow_redirects=False,
        )
        assert r.status_code == 303, f"no_modules login failed: {r.status_code} {r.text[:100]}"
        yield c
