"""
Integration tests for main.py exception handlers.
Verifies the structured error response format (Phase 2).
"""

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_crm_service, get_current_user
from backend.core.exceptions import (
    CRMAIEngineError,
    OdooAuthenticationError,
    OdooConnectionError,
    ReadOnlyViolationError,
)
from backend.main import app

_AUTH = ("testadmin", "testpass")


def _raising(exc: Exception):  # type: ignore[type-arg]
    def _override() -> None:
        raise exc

    return _override


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _with_auth(svc_override):  # type: ignore[type-arg]
    app.dependency_overrides[get_crm_service] = svc_override
    app.dependency_overrides[get_current_user] = lambda: "testadmin"


def _clear() -> None:
    app.dependency_overrides.clear()


# ── ReadOnlyViolationError → 403 ──────────────────────────────────────────────


def test_read_only_violation_returns_403(client: TestClient) -> None:
    _with_auth(_raising(ReadOnlyViolationError("create not allowed")))
    try:
        r = client.get("/api/v1/summary", auth=_AUTH)
        assert r.status_code == 403
        body = r.json()
        assert body["ok"] is False
        assert body["error"]["code"] == "READ_ONLY_VIOLATION"
        assert "request_id" in body["error"]
        assert "timestamp" in body["error"]
    finally:
        _clear()


# ── OdooAuthenticationError → 502 ────────────────────────────────────────────


def test_odoo_auth_error_returns_502(client: TestClient) -> None:
    _with_auth(_raising(OdooAuthenticationError("bad key")))
    try:
        r = client.get("/api/v1/summary", auth=_AUTH)
        assert r.status_code == 502
        body = r.json()
        assert body["error"]["code"] == "ODOO_AUTH_ERROR"
    finally:
        _clear()


# ── OdooConnectionError → 503 ────────────────────────────────────────────────


def test_odoo_connection_error_returns_503(client: TestClient) -> None:
    _with_auth(_raising(OdooConnectionError("unreachable")))
    try:
        r = client.get("/api/v1/summary", auth=_AUTH)
        assert r.status_code == 503
        body = r.json()
        assert body["error"]["code"] == "ODOO_CONNECTION_ERROR"
    finally:
        _clear()


# ── CRMAIEngineError → 500 ────────────────────────────────────────────────────


def test_generic_crm_error_returns_500(client: TestClient) -> None:
    _with_auth(_raising(CRMAIEngineError("unexpected")))
    try:
        r = client.get("/api/v1/summary", auth=_AUTH)
        assert r.status_code == 500
        body = r.json()
        assert body["error"]["code"] == "INTERNAL_ERROR"
    finally:
        _clear()


# ── Error response always has ok=False ───────────────────────────────────────


def test_error_response_always_has_ok_false(client: TestClient) -> None:
    _with_auth(_raising(ReadOnlyViolationError("x")))
    try:
        r = client.get("/api/v1/summary", auth=_AUTH)
        assert r.json()["ok"] is False
    finally:
        _clear()
