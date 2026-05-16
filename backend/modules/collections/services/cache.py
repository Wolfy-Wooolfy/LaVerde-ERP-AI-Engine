"""
Collections module — in-memory cache abstraction (Decision 1.1).

Thin dict-based store with a 60-second TTL. Keys include today's date
(YYYY-MM-DD) so entries auto-invalidate at UTC midnight without an
explicit flush — a call on "today" and a call on "yesterday" never
share a cache entry.

Migration path to Redis (Decision 1.1): replace the _store dict and
_is_expired check with a Redis client. The public interface (get / set /
invalidate / clear) stays identical, so no caller changes are required.
"""

import threading
import time
from datetime import date, timezone
from typing import Any, Optional

_lock = threading.Lock()
_store: dict[str, tuple[Any, float]] = {}

_TTL_SECONDS = 60


def _is_expired(stored_at: float) -> bool:
    return (time.monotonic() - stored_at) > _TTL_SECONDS


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
        value, stored_at = entry
        if _is_expired(stored_at):
            del _store[key]
            return None
        return value


def set(key: str, value: Any) -> None:
    with _lock:
        _store[key] = (value, time.monotonic())


def invalidate(key: str) -> None:
    with _lock:
        _store.pop(key, None)


def clear() -> None:
    with _lock:
        _store.clear()
