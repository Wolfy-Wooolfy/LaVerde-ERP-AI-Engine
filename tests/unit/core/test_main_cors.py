"""Unit tests for the CORS configuration.

``backend.main.cors_kwargs_for`` is a PURE helper: given the configured
CORS_ORIGINS list it returns the CORSMiddleware kwargs. There is no wildcard
fallback — an empty list means no cross-origin access, full stop.
(Settings.validate_cors_origins is what forbids "*" in production; that's
covered separately in tests/unit/core/test_config.py.)

The live-app test proves the empty-list branch holds for the real app: under
the test env (CORS_ORIGINS=[] per tests/.env.test) a foreign Origin gets no
access-control-allow-origin header back from GET /health, an endpoint that
requires no authenticated session.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from backend.main import app, cors_kwargs_for


# ── (a) Pure helper — empty origin list denies all cross-origin access ────────


def test_cors_kwargs_for_empty_list_denies_all() -> None:
    kwargs = cors_kwargs_for([])
    assert kwargs["allow_origins"] == []
    assert kwargs["allow_credentials"] is False
    assert kwargs["allow_methods"] == ["GET", "OPTIONS"]
    assert kwargs["allow_headers"] == ["*"]


# ── (b) Pure helper — a concrete origin list passes through unchanged ─────────


def test_cors_kwargs_for_concrete_origins_passthrough() -> None:
    origins = ["https://app.example.com", "https://admin.example.com"]
    kwargs = cors_kwargs_for(origins)
    assert kwargs["allow_origins"] == origins
    assert kwargs["allow_credentials"] is False
    assert kwargs["allow_methods"] == ["GET", "OPTIONS"]
    assert kwargs["allow_headers"] == ["*"]


# ── (c) Live app — under the test env, empty CORS_ORIGINS = no CORS header ────


def test_live_app_denies_foreign_origin_under_test_env() -> None:
    """The module-level app is built under the test env (CORS_ORIGINS=[] per
    tests/.env.test), so a foreign Origin must NOT receive an
    access-control-allow-origin response header. GET /health requires no
    authenticated session, so this isolates CORS behaviour from auth."""
    client = TestClient(app)
    resp = client.get("/health", headers={"Origin": "https://evil.example.com"})
    assert "access-control-allow-origin" not in resp.headers
