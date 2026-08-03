"""Unit tests for the GET / root redirect.

Before this route existed, hitting the bare server address produced FastAPI's
raw 404 JSON ({"detail":"Not Found"}) instead of landing anywhere useful. The
handler is a bare, stateless RedirectResponse to /login — no session/app.state/
Odoo access — matching the "Legacy redirect shims" pattern already in
backend/main.py.

No lifespan: like test_main_docs.py, this uses TestClient(app) WITHOUT its
context manager. Both / and the /login page it redirects to touch no
app.state / Odoo / OpenAI.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from backend.main import app


# ── (a) GET / without following redirects — 307 to /login ──────────────────────


def test_root_redirects_to_login_without_following() -> None:
    client = TestClient(app, follow_redirects=False)
    resp = client.get("/")
    assert resp.status_code == 307
    assert resp.headers["location"] == "/login"


# ── (b) GET / following redirects — lands on the login page (200) ──────────────


def test_root_redirect_resolves_to_login_page() -> None:
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.request.url.path == "/login"


# ── (c) The root route is hidden from the OpenAPI schema ───────────────────────


def test_root_route_not_in_openapi_schema() -> None:
    client = TestClient(app)
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    assert "/" not in resp.json()["paths"]
