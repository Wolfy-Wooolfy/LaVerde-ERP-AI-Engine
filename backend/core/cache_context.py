"""Per-request cache-bypass signal (manual ``?refresh=1`` support).

A single process-wide ``ContextVar[bool]`` that carries a per-request "skip the
in-memory KPI cache" flag. The HTTP middleware sets it at the start of every
request (``True`` only for a GET carrying ``?refresh=1``) and resets it in a
``finally`` block; the cache read layer consults it and, when set, returns a
miss so the request falls through to a fresh Odoo fetch. The subsequent
write-back still runs, so later normal requests are fast again.

Why a ContextVar is safe here:
- Every FastAPI handler in this app is ``async def`` and runs on one event loop;
  the cache is never read from a threadpool thread (``run_in_threadpool`` /
  ``to_thread`` appear nowhere under ``backend/``).
- The middleware resets the flag in a ``finally`` block on every request —
  success or exception — so it can never leak to the next request on the loop.
- The flag is set BEFORE ``call_next``, so the downstream endpoint's task
  inherits the value (its context is copied after the flag is set). Every cache
  read in the request — however deeply nested, across modules — therefore
  observes the same signal without threading a parameter through service code.

Only ``contextvars`` from the standard library is used; this module has no
project dependencies, so any cache module can import it without a cycle.
"""

from contextvars import ContextVar, Token

_cache_bypass: ContextVar[bool] = ContextVar("cache_bypass", default=False)


def set_cache_bypass(value: bool) -> Token:
    """Set the per-request cache-bypass flag; return the token used to reset it."""
    return _cache_bypass.set(value)


def reset_cache_bypass(token: Token) -> None:
    """Restore the flag to its prior value using the token from ``set_cache_bypass``."""
    _cache_bypass.reset(token)


def is_cache_bypass() -> bool:
    """Return the current cache-bypass flag (``False`` when unset)."""
    return _cache_bypass.get()
