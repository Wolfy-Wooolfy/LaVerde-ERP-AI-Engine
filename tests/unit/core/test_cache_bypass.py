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


# ── 7. the REAL app: backend.main.app's own middleware stack ──────────────────
@pytest.fixture()
def _probe_on_real_app():
    """Mount a throwaway GET route on the REAL application, then remove it.

    Test 6 proves BaseHTTPMiddleware propagates a contextvar on this Starlette
    version, but it proves it about a two-line FastAPI() built inside the test.
    It would keep passing if backend/main.py stopped setting the flag, dropped
    the middleware, or registered another middleware ahead of it that consumed
    the request. Only the real app's real stack can rule that out, so this
    fixture borrows it rather than reconstructing it.

    `TestClient(app)` is used WITHOUT a `with` block on purpose: Starlette runs
    the lifespan only inside the context manager, so no Odoo connection, user
    store or scheduler is started here (the same reason
    tests/unit/core/test_static_cache_headers.py drives the real app this way).
    """
    from backend.main import app

    _PATH = "/__cache_bypass_probe__"

    async def _probe() -> dict:
        # Read the flag exactly where a cache read would: inside the endpoint's
        # own task, downstream of every middleware on the real stack.
        return {"bypass": is_cache_bypass()}

    app.add_api_route(_PATH, _probe, methods=["GET", "POST"], include_in_schema=False)
    try:
        yield app, _PATH
    finally:
        app.router.routes = [r for r in app.router.routes if getattr(r, "path", None) != _PATH]


def test_real_app_sets_bypass_on_refresh_1_and_resets_after(_probe_on_real_app) -> None:
    """T1. On backend.main.app: ?refresh=1 on a GET arrives at the endpoint as
    True, a plain GET as False, and the flag is back to False once the response
    has been returned.

    The post-request assertion is the half that matters most: main.py resets the
    flag in a `finally`, and if that reset were ever dropped the very next
    request on the loop would inherit a permanent cache bypass — every visitor
    hitting Odoo directly, with nothing failing loudly.
    """
    app, path = _probe_on_real_app
    client = TestClient(app)

    assert client.get(path).json() == {"bypass": False}
    assert is_cache_bypass() is False, "a plain GET left the flag set"

    assert client.get(path, params={"refresh": "1"}).json() == {"bypass": True}
    assert is_cache_bypass() is False, (
        "?refresh=1 leaked past the response — main.py's finally-reset is gone, "
        "so the next request on this event loop would also bypass the cache"
    )


def test_real_app_ignores_refresh_on_non_get(_probe_on_real_app) -> None:
    """Anti-vacuity for the METHOD half of main.py's condition.

    The probe accepts POST as well as GET, so this drives a real POST carrying
    ?refresh=1 through the real middleware stack and reads the flag from inside
    the endpoint. Drop `request.method == "GET"` from main.py and this is the
    only test in the suite that notices.

    It matters because a write path that bypassed every cache would quietly
    turn each mutation into a full uncached Odoo re-read.
    """
    _app, path = _probe_on_real_app
    client = TestClient(_app)

    assert client.post(path, params={"refresh": "1"}).json() == {"bypass": False}
    assert client.get(path, params={"refresh": "1"}).json() == {"bypass": True}


def test_real_app_ignores_refresh_values_other_than_1(_probe_on_real_app) -> None:
    """main.py compares against the string "1", not truthiness. ?refresh=0 and a
    bare ?refresh must NOT bypass, or a stray link would disable the cache."""
    _app, path = _probe_on_real_app
    client = TestClient(_app)

    assert client.get(path, params={"refresh": "0"}).json() == {"bypass": False}
    assert client.get(path, params={"refresh": ""}).json() == {"bypass": False}
    assert client.get(path, params={"refresh": "true"}).json() == {"bypass": False}
