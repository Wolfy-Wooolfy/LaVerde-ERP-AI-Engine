"""Playwright e2e test configuration."""

import pytest

BASE_URL = "http://localhost:8000"
AUTH = ("admin", "password")


@pytest.fixture(scope="session")
def base_url() -> str:
    return BASE_URL


@pytest.fixture(scope="session")
def auth_headers() -> dict:
    import base64

    creds = base64.b64encode(f"{AUTH[0]}:{AUTH[1]}".encode()).decode()
    return {"Authorization": f"Basic {creds}"}
