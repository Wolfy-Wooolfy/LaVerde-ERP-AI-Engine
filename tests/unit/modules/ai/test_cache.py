"""Unit tests for AICache and cache key helpers."""

import time
from pathlib import Path

import pytest

from backend.modules.ai.cache import AICache, lead_cache_key, overdue_list_cache_key


@pytest.fixture
def cache(tmp_path):
    return AICache(ttl_seconds=60, maxsize=50, cache_file=tmp_path / "test_ai_cache.json")


def test_set_and_get(cache):
    cache.set("key1", {"score": 80})
    assert cache.get("key1") == {"score": 80}


def test_get_missing_returns_none(cache):
    assert cache.get("nonexistent") is None


def test_invalidate(cache):
    cache.set("key2", "hello")
    cache.invalidate("key2")
    assert cache.get("key2") is None


def test_clear(cache):
    cache.set("a", 1)
    cache.set("b", 2)
    cache.clear()
    assert cache.get("a") is None
    assert cache.get("b") is None


def test_hit_rate_tracking(cache):
    cache.set("x", 42)
    cache.get("x")  # hit
    cache.get("y")  # miss
    assert cache.hits == 1
    assert cache.misses == 1
    assert cache.hit_rate == 0.5


def test_stats(cache):
    cache.set("p", 1)
    cache.get("p")
    stats = cache.stats()
    assert "hits" in stats
    assert "misses" in stats
    assert "hit_rate" in stats
    assert "size" in stats
    assert "maxsize" in stats


def test_persistence_survives_reload(tmp_path):
    f = tmp_path / "cache_persist.json"
    c1 = AICache(ttl_seconds=300, maxsize=50, cache_file=f)
    c1.set("persist_key", {"score": 99})

    c2 = AICache(ttl_seconds=300, maxsize=50, cache_file=f)
    assert c2.get("persist_key") == {"score": 99}


def test_lead_cache_key_deterministic():
    k1 = lead_cache_key(1, 28, "2026-05-01", 3)
    k2 = lead_cache_key(1, 28, "2026-05-01", 3)
    assert k1 == k2


def test_lead_cache_key_different_for_different_inputs():
    k1 = lead_cache_key(1, 28, "2026-05-01", 3)
    k2 = lead_cache_key(1, 29, "2026-05-01", 3)  # different stage
    assert k1 != k2


def test_overdue_list_cache_key():
    assert overdue_list_cache_key(10) == "overdue_priority_top_10"
    assert overdue_list_cache_key(50) == "overdue_priority_top_50"
