"""
Auth regression tests for Customer Accounts endpoints.

Added 2026-06-09 as part of the security hotfix that wired
Depends(get_current_user) onto all 6 Customer Accounts routes.
"""

import pytest
from fastapi.testclient import TestClient

from backend.main import app

@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=True)


# ── Auth regression — all Customer Accounts endpoints must reject unauthenticated callers


def test_401_when_no_auth(client: TestClient) -> None:
    """Customer Accounts endpoints must reject unauthenticated requests with 401.

    No service patch needed — auth is checked before the handler body runs.
    Uses /kpi/total-receivables as the representative probe route.
    """
    r = client.get("/api/v1/customer-accounts/kpi/total-receivables")  # no auth
    assert r.status_code == 401, (
        f"Expected 401 for unauthenticated Customer Accounts request, got {r.status_code}"
    )
