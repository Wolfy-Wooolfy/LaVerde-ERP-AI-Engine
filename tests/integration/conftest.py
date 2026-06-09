"""Shared fixtures for integration tests."""

import pytest
from fastapi.testclient import TestClient

from backend.main import app


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
