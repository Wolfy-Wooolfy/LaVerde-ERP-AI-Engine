"""
Campaign Performance module — in-memory cache (same interface as
marketing_attribution/services/cache.py).

Separate _store dict so this module never shares cache state with other modules.
60-second TTL. Keys include today's Cairo-local date so entries auto-invalidate
at Cairo midnight without an explicit flush.
"""

import threading
import time
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

_lock = threading.Lock()
_store: dict[str, tuple[Any, float, int]] = {}  # (value, stored_at, ttl)

_TTL_SECONDS = 60


def _is_expired(stored_at: float, ttl: int) -> bool:
    return (time.monotonic() - stored_at) > ttl


def today_str() -> str:
    """Return today's Cairo-local date as YYYY-MM-DD. Extracted so tests can patch it."""
    return datetime.now(ZoneInfo("Africa/Cairo")).date().isoformat()


def make_key(name: str) -> str:
    """Build a cache key scoped to today's Cairo-local date."""
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
