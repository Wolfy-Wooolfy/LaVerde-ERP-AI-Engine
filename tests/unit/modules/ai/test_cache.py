"""Unit tests for AICache and cache key helpers."""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.modules.ai.cache import CACHE_SCHEMA_VERSION, AICache, lead_cache_key, overdue_list_cache_key


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
    from backend.modules.ai.schemas import LeadPriority

    f = tmp_path / "cache_persist.json"
    priority = LeadPriority(
        lead_id=42,
        score=90,
        tier="critical",
        reasoning="Very hot lead",
        recommended_action="Call immediately",
        generated_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        model_used="gpt-4o-mini",
    )
    c1 = AICache(ttl_seconds=300, maxsize=50, cache_file=f)
    c1.set("persist_key", priority)

    c2 = AICache(ttl_seconds=300, maxsize=50, cache_file=f)
    result = c2.get("persist_key")
    assert result is not None
    assert result.lead_id == 42
    assert result.score == 90


def test_lead_cache_key_deterministic():
    k1 = lead_cache_key(1, 28, "2026-05-01", 3)
    k2 = lead_cache_key(1, 28, "2026-05-01", 3)
    assert k1 == k2


def test_lead_cache_key_different_for_different_inputs():
    k1 = lead_cache_key(1, 28, "2026-05-01", 3)
    k2 = lead_cache_key(1, 29, "2026-05-01", 3)  # different stage
    assert k1 != k2


def test_overdue_list_cache_key():
    assert overdue_list_cache_key(10) == "overdue_priority_top_10_en"
    assert overdue_list_cache_key(50) == "overdue_priority_top_50_en"
    assert overdue_list_cache_key(10, "ar") == "overdue_priority_top_10_ar"


# ── Schema versioning regression tests ───────────────────────────────────────


def _make_priority_payload() -> dict:
    return {
        "lead_id": 5,
        "score": 85,
        "tier": "high",
        "reasoning": "Strong lead",
        "recommended_action": "Call",
        "generated_at": "2026-05-01T00:00:00Z",
        "model_used": "gpt-4o-mini",
    }


def test_loads_versioned_cache_correctly(tmp_path):
    f = tmp_path / "cache_v2.json"
    data = {
        "schema_version": CACHE_SCHEMA_VERSION,
        "entries": {
            "key_v2": {
                "expires_at": time.time() + 3600,
                "payload": _make_priority_payload(),
            }
        },
    }
    f.write_text(json.dumps(data), encoding="utf-8")
    c = AICache(ttl_seconds=300, maxsize=50, cache_file=f)
    result = c.get("key_v2")
    assert result is not None
    assert result.lead_id == 5
    assert result.score == 85


def test_clears_unversioned_cache(tmp_path):
    f = tmp_path / "old_cache.json"
    old_data = {"some_key": {"value": {"score": 80}, "expires_at": time.time() + 3600}}
    f.write_text(json.dumps(old_data), encoding="utf-8")
    c = AICache(ttl_seconds=300, maxsize=50, cache_file=f)
    assert c.get("some_key") is None
    assert not f.exists()


def test_clears_mismatched_version_cache(tmp_path):
    f = tmp_path / "old_v1.json"
    data = {
        "schema_version": CACHE_SCHEMA_VERSION - 1,
        "entries": {"k": {"expires_at": time.time() + 3600, "payload": _make_priority_payload()}},
    }
    f.write_text(json.dumps(data), encoding="utf-8")
    c = AICache(ttl_seconds=300, maxsize=50, cache_file=f)
    assert c.get("k") is None
    assert not f.exists()


def test_handles_corrupted_json(tmp_path):
    f = tmp_path / "corrupt.json"
    f.write_text("NOT VALID JSON {{{{", encoding="utf-8")
    c = AICache(ttl_seconds=300, maxsize=50, cache_file=f)  # must not raise
    assert c.get("anything") is None
    assert not f.exists()


def test_skips_invalid_entries(tmp_path):
    f = tmp_path / "mixed.json"
    data = {
        "schema_version": CACHE_SCHEMA_VERSION,
        "entries": {
            "good_key": {"expires_at": time.time() + 3600, "payload": _make_priority_payload()},
            "bad_key": {"expires_at": time.time() + 3600, "payload": {"junk": True}},
        },
    }
    f.write_text(json.dumps(data), encoding="utf-8")
    c = AICache(ttl_seconds=300, maxsize=50, cache_file=f)
    assert c.get("good_key") is not None
    assert c.get("good_key").lead_id == 5
    assert c.get("bad_key") is None
