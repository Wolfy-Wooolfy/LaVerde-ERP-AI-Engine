"""Integration tests for all health check endpoints."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_crm_service
from backend.main import app

_AUTH = ("testadmin", "testpass")


def _make_service(auth_ok: bool = True, query_ok: bool = True) -> MagicMock:
    svc = MagicMock()
    client = MagicMock()
    if auth_ok:
        client.authenticate = AsyncMock(return_value=42)
    else:
        from backend.core.exceptions import OdooAuthenticationError

        client.authenticate = AsyncMock(side_effect=OdooAuthenticationError("bad"))
    if query_ok:
        client.execute_kw = AsyncMock(return_value=[{"id": 1}])
    else:
        from backend.core.exceptions import OdooConnectionError

        client.execute_kw = AsyncMock(side_effect=OdooConnectionError("down"))
    svc.client = client
    return svc


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def ok_service() -> None:
    svc = _make_service()
    app.dependency_overrides[get_crm_service] = lambda: svc
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def auth_fail_service() -> None:
    svc = _make_service(auth_ok=False)
    app.dependency_overrides[get_crm_service] = lambda: svc
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def conn_fail_service() -> None:
    svc = _make_service(query_ok=False)
    app.dependency_overrides[get_crm_service] = lambda: svc
    yield
    app.dependency_overrides.clear()


# ── /health (no auth) ─────────────────────────────────────────────────────────


def test_liveness_probe(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "uptime_seconds" in body
    assert "version" in body


# ── /api/v1/health ────────────────────────────────────────────────────────────


def test_authenticated_health(client: TestClient, ok_service: None) -> None:
    r = client.get("/api/v1/health", auth=_AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "components" in body
    assert body["components"]["cache"]["status"] == "ok"


# ── /api/v1/health/odoo ───────────────────────────────────────────────────────


def test_odoo_health_ok(client: TestClient, ok_service: None) -> None:
    r = client.get("/api/v1/health/odoo", auth=_AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["auth_valid"] is True
    assert "response_time_ms" in body


def test_odoo_health_auth_fail(client: TestClient, auth_fail_service: None) -> None:
    r = client.get("/api/v1/health/odoo", auth=_AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "degraded"
    assert body["auth_valid"] is False


def test_odoo_health_conn_fail(client: TestClient, conn_fail_service: None) -> None:
    # conn_fail_service fails execute_kw but auth succeeds — health/odoo only checks auth
    r = client.get("/api/v1/health/odoo", auth=_AUTH)
    assert r.status_code == 200
    # auth passed, so status is ok (deep check would catch the execute_kw failure)
    assert r.json()["auth_valid"] is True


# ── /api/v1/health/deep ───────────────────────────────────────────────────────


def test_deep_health_ok(client: TestClient, ok_service: None) -> None:
    r = client.get("/api/v1/health/deep", auth=_AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["checks"]["odoo"]["ok"] is True


def test_deep_health_odoo_down(client: TestClient) -> None:
    from backend.core.exceptions import OdooConnectionError

    svc = MagicMock()
    svc.client = MagicMock()
    svc.client.authenticate = AsyncMock(side_effect=OdooConnectionError("down"))
    svc.client.execute_kw = AsyncMock(side_effect=OdooConnectionError("down"))
    app.dependency_overrides[get_crm_service] = lambda: svc
    try:
        r = client.get("/api/v1/health/deep", auth=_AUTH)
        assert r.status_code == 503
        body = r.json()
        assert body["status"] == "degraded"
        assert body["checks"]["odoo"]["ok"] is False
    finally:
        app.dependency_overrides.clear()
