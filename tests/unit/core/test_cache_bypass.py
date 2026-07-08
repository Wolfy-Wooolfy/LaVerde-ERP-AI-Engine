"""Unit tests for the request-scoped cache-bypass ContextVar (manual ?refresh=1).

These cover the mechanism in isolation:
- the ContextVar default / set / reset contract (backend.core.cache_context),
- that the cache READ seams honour it (core cache + a representative module cache),
- that a bypassed read skips WITHOUT deleting the stored value (write-back survives),
- a leak-guard proving the finally-reset contract restores the prior value, and
- (propagation tripwire) that a value set inside an @app.middleware("http")
  BaseHTTPMiddleware is visible to the endpoint — the real request path the
  feature relies on, and the one fact the direct-call tests above cannot prove.
  This guards against a future Starlette bump silently breaking propagation.
"""

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend.core import cache as core_cache
from backend.core.cache_context import (
    is_cache_bypass,
    reset_cache_bypass,
    set_cache_bypass,
)
from backend.modules.collections.services import cache as collections_cache


@pytest.fixture(autouse=True)
def _reset_bypass_flag():
    """Guarantee every test starts and ends with the flag cleared, so a stray
    set() can never leak into another test in the same process/context."""
    token = set_cache_bypass(False)
    try:
        yield
    finally:
        reset_cache_bypass(token)


# ── 1. default is False ───────────────────────────────────────────────────────
def test_default_is_false() -> None:
    assert is_cache_bypass() is False


# ── 2. set(True) flips it; reset restores the prior value ─────────────────────
def test_set_true_then_reset_restores_prior() -> None:
    assert is_cache_bypass() is False
    token = set_cache_bypass(True)
    try:
        assert is_cache_bypass() is True
    finally:
        reset_cache_bypass(token)
    assert is_cache_bypass() is False


# ── 3. core cache: bypass skips the read but does NOT delete the value ────────
def test_core_cache_bypass_skips_but_keeps_value() -> None:
    core_cache.init_cache(ttl=60, maxsize=32)
    core_cache.clear_cache()
    core_cache.set_cached("k", {"v": 1})
    assert core_cache.get_cached("k") == {"v": 1}  # normal hit

    token = set_cache_bypass(True)
    try:
        assert core_cache.get_cached("k") is None  # bypass → forced miss
    finally:
        reset_cache_bypass(token)

    assert core_cache.get_cached("k") == {"v": 1}  # still stored; only skipped
    core_cache.clear_cache()


# ── 4. a module cache (collections) honours the same signal ───────────────────
def test_module_cache_bypass_skips_but_keeps_value() -> None:
    collections_cache.clear()
    collections_cache.set("mk", {"v": 2})
    assert collections_cache.get("mk") == {"v": 2}  # normal hit

    token = set_cache_bypass(True)
    try:
        assert collections_cache.get("mk") is None  # bypass → forced miss
    finally:
        reset_cache_bypass(token)

    assert collections_cache.get("mk") == {"v": 2}  # still stored; only skipped
    collections_cache.clear()


# ── 5. leak guard: reset restores False for the next read ─────────────────────
def test_reset_prevents_leak() -> None:
    token = set_cache_bypass(True)
    assert is_cache_bypass() is True
    reset_cache_bypass(token)
    assert is_cache_bypass() is False


# ── 6. propagation tripwire: middleware → endpoint on THIS Starlette ──────────
def test_middleware_contextvar_propagates_to_endpoint() -> None:
    """A value set in an @app.middleware('http') must be visible to the route
    handler — the real request path the feature depends on. If a future
    Starlette version breaks contextvar propagation from BaseHTTPMiddleware to
    the endpoint, THIS test fails loudly instead of the feature silently
    becoming a no-op."""
    app = FastAPI()

    @app.middleware("http")
    async def _set_bypass(request: Request, call_next):
        token = set_cache_bypass(request.query_params.get("refresh") == "1")
        try:
            return await call_next(request)
        finally:
            reset_cache_bypass(token)

    @app.get("/probe")
    async def _probe() -> dict:
        # Read the flag from deep inside the endpoint's task, as a real cache
        # read would. It must reflect what the middleware set for this request.
        return {"bypass": is_cache_bypass()}

    client = TestClient(app)
    assert client.get("/probe").json() == {"bypass": False}
    assert client.get("/probe?refresh=1").json() == {"bypass": True}
