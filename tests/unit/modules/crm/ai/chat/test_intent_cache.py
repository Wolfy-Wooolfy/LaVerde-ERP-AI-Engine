"""Unit tests for IntentCache (Stage 1 cache)."""

import pytest

from backend.shared.ai.cache import IntentCache
from backend.modules.crm.ai.chat.schemas import QueryIntent


def _make_intent(name: str = "count_by_stage") -> QueryIntent:
    return QueryIntent(intent=name, response_format="number", confidence=0.9)


def test_cache_miss_returns_none():
    cache = IntentCache()
    assert cache.get("any question", "en") is None


def test_cache_set_and_get():
    cache = IntentCache()
    intent = _make_intent("list_overdue_by_salesperson")
    cache.set("show overdue", "en", intent)
    result = cache.get("show overdue", "en")
    assert result is not None
    assert result.intent == "list_overdue_by_salesperson"


def test_cache_key_is_locale_sensitive():
    cache = IntentCache()
    intent_en = _make_intent("count_by_stage")
    intent_ar = _make_intent("list_overdue_by_team")
    cache.set("question", "en", intent_en)
    cache.set("question", "ar", intent_ar)
    assert cache.get("question", "en").intent == "count_by_stage"
    assert cache.get("question", "ar").intent == "list_overdue_by_team"


def test_cache_key_normalizes_whitespace():
    cache = IntentCache()
    intent = _make_intent("count_by_stage")
    cache.set("  How many?  ", "en", intent)
    result = cache.get("how many?", "en")  # lowercase + stripped
    assert result is not None


def test_different_questions_dont_collide():
    cache = IntentCache()
    cache.set("question A", "en", _make_intent("count_by_stage"))
    cache.set("question B", "en", _make_intent("free_form_analysis"))
    assert cache.get("question A", "en").intent == "count_by_stage"
    assert cache.get("question B", "en").intent == "free_form_analysis"
