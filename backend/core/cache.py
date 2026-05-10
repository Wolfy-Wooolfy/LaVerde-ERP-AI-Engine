import threading
from typing import Any, Optional

from cachetools import TTLCache

_lock = threading.Lock()
_cache: TTLCache = TTLCache(maxsize=128, ttl=60)  # re-initialised at app startup


def init_cache(ttl: int, maxsize: int = 128) -> None:
    """Replace the global cache with a new instance using the given TTL."""
    global _cache
    with _lock:
        _cache = TTLCache(maxsize=maxsize, ttl=ttl)


def get_cached(key: str) -> Optional[Any]:
    with _lock:
        return _cache.get(key)


def set_cached(key: str, value: Any) -> None:
    with _lock:
        _cache[key] = value


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def invalidate(key: str) -> None:
    with _lock:
        _cache.pop(key, None)
