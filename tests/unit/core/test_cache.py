import time

import pytest

from backend.core.cache import clear_cache, get_cached, init_cache, invalidate, set_cached


@pytest.fixture(autouse=True)
def reset_cache() -> None:
    """Re-initialise with a short TTL before each test."""
    init_cache(ttl=2, maxsize=32)
    clear_cache()


def test_set_and_get() -> None:
    set_cached("key1", {"value": 42})
    assert get_cached("key1") == {"value": 42}


def test_miss_returns_none() -> None:
    assert get_cached("nonexistent") is None


def test_cache_expiry() -> None:
    init_cache(ttl=1, maxsize=32)
    set_cached("expiring", "hello")
    assert get_cached("expiring") == "hello"
    time.sleep(1.2)
    assert get_cached("expiring") is None


def test_clear_removes_all() -> None:
    set_cached("a", 1)
    set_cached("b", 2)
    clear_cache()
    assert get_cached("a") is None
    assert get_cached("b") is None


def test_invalidate_single_key() -> None:
    set_cached("keep", "yes")
    set_cached("drop", "no")
    invalidate("drop")
    assert get_cached("keep") == "yes"
    assert get_cached("drop") is None
