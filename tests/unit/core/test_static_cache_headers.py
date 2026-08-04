"""Cache-Control policy on /static responses.

No lifespan: like test_main_docs.py, these use TestClient(app) WITHOUT its
context manager — the static mount and the security-headers middleware exist at
import time. The long-cache rule is keyed on the PRESENCE of the fingerprint
query param, not the path: a ?v= URL can never change content under that URL,
while any bare request (old bookmark, manifest miss) must revalidate every use.
"""

from fastapi.testclient import TestClient

from backend.main import app


def test_fingerprinted_static_request_gets_immutable() -> None:
    r = TestClient(app).get("/static/js/app.js", params={"v": "abc123def456"})
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_bare_static_request_gets_no_cache() -> None:
    r = TestClient(app).get("/static/js/app.js")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-cache"


def test_font_request_gets_bounded_max_age() -> None:
    r = TestClient(app).get("/static/vendor/fonts/inter-400.woff2")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=2592000"


def test_unknown_static_path_still_404s() -> None:
    r = TestClient(app).get("/static/js/__no_such_asset__.js")
    assert r.status_code == 404


def test_non_static_routes_gain_no_cache_control() -> None:
    """Guard: the policy is path-scoped to /static — /health must stay untouched."""
    r = TestClient(app).get("/health")
    assert r.status_code == 200
    assert "cache-control" not in r.headers
