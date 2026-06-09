"""Playwright e2e test configuration."""

import pytest

BASE_URL = "http://localhost:8000"


@pytest.fixture(scope="session")
def base_url() -> str:
    return BASE_URL
