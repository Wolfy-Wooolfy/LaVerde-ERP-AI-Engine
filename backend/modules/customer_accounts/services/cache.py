"""
Customer Accounts module — in-memory cache (same interface as collections/services/cache.py).

Separate _store dict so Customer Accounts and Collections never share cache state.
60-second TTL. Keys include today's date so entries auto-invalidate at UTC midnight.
"""

import threading
import time
from datetime import date
from typing import Any, Optional

_lock = threading.Lock()
_store: dict[str, tuple[Any, float, int]] = {}

_TTL_SECONDS = 60


def _is_expired(stored_at: float, ttl: int) -> bool:
    return (time.monotonic() - stored_at) > ttl


def today_str() -> str:
    """Return today's UTC date as YYYY-MM-DD. Extracted so tests can patch it."""
    return date.today().isoformat()


def make_key(name: str) -> str:
    """Build a cache key scoped to today's UTC date."""
    return f"{name}:{today_str()}"


def get(key: str) -> Optional[Any]:
    with _lock:
        entry = _store.get(key)
        if entry is None:
            return None
        value, stored_at, ttl = entry
        if _is_expired(stored_at, ttl):
            del _store[key]
            return None
        return value


def set(key: str, value: Any, ttl: int = _TTL_SECONDS) -> None:
    with _lock:
        _store[key] = (value, time.monotonic(), ttl)


def invalidate(key: str) -> None:
    with _lock:
        _store.pop(key, None)


def clear() -> None:
    with _lock:
        _store.clear()
