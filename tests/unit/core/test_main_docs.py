"""Unit tests for the production-gated interactive API docs.

``backend.main.docs_urls_for`` is a PURE helper: given an ENVIRONMENT string it
returns the FastAPI docs-URL kwargs. In production all three are ``None`` (which
also removes /docs/oauth2-redirect); in every other environment the default paths
are served.

The production branch is covered by the pure unit test below. Proving the prod 404
over the module-level ``app`` singleton is not feasible without env manipulation
(the app is built once at import time under the test env), so the helper unit test
is the production-branch coverage, and the live-app test proves the dev default
holds: under the test env (ENVIRONMENT != production) /docs is still served (200).

No lifespan / $0: the live-app check uses ``TestClient(app)`` WITHOUT its context
manager. Serving the Swagger UI page touches no app.state / Odoo / OpenAI.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from backend.main import app, docs_urls_for


# ── (a) Pure helper — production disables all three doc surfaces ───────────────


def test_docs_urls_for_production_disables_all() -> None:
    urls = docs_urls_for("production")
    assert urls == {"docs_url": None, "redoc_url": None, "openapi_url": None}


# ── (b) Pure helper — non-production serves the default paths ──────────────────


def test_docs_urls_for_development_serves_defaults() -> None:
    urls = docs_urls_for("development")
    assert urls == {
        "docs_url": "/docs",
        "redoc_url": "/redoc",
        "openapi_url": "/openapi.json",
    }


# ── (c) Live app — under the test (non-prod) env /docs is served ───────────────


def test_live_app_serves_docs_under_test_env() -> None:
    """The module-level app is built under the test env (ENVIRONMENT != production),
    so the interactive docs must be enabled — /docs returns 200. This proves the
    dev default holds and keeps the PUBLIC_ALLOWLIST /docs entry honest."""
    client = TestClient(app)
    resp = client.get("/docs")
    assert resp.status_code == 200
