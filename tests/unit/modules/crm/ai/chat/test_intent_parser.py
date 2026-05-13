"""Unit tests for intent_parser (Stage 1)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.shared.ai.cache import IntentCache
from backend.modules.crm.ai.chat.intent_parser import _parse_intent_json, parse_intent
from backend.modules.crm.ai.chat.schemas import ChatMessage, ChatMessageRole, QueryIntent


# ── _parse_intent_json ────────────────────────────────────────────────────────


def test_parse_valid_intent():
    raw = '{"intent":"list_overdue_by_salesperson","filters":{"limit":5},"response_format":"table","confidence":0.9}'
    intent = _parse_intent_json(raw)
    assert intent.intent == "list_overdue_by_salesperson"
    assert intent.response_format == "table"
    assert intent.confidence == pytest.approx(0.9)
    assert intent.filters["limit"] == 5


def test_parse_unknown_intent_returned_as_unknown():
    raw = '{"intent":"delete_all_leads","filters":{},"response_format":"table","confidence":0.95}'
    intent = _parse_intent_json(raw)
    assert intent.intent == "unknown"


def test_parse_non_json_returns_unknown():
    intent = _parse_intent_json("not json at all")
    assert intent.intent == "unknown"
    assert intent.confidence == pytest.approx(0.1)


def test_parse_empty_json_returns_unknown():
    intent = _parse_intent_json("{}")
    assert intent.intent == "unknown"


def test_parse_invalid_response_format_falls_back():
    raw = '{"intent":"count_by_stage","filters":{},"response_format":"pie_chart","confidence":0.8}'
    intent = _parse_intent_json(raw)
    assert intent.response_format == "analysis"


def test_parse_all_allowed_intents():
    from backend.modules.crm.ai.chat.prompts import ALLOWED_INTENTS

    for allowed in ALLOWED_INTENTS:
        raw = f'{{"intent":"{allowed}","filters":{{}},"response_format":"analysis","confidence":0.8}}'
        intent = _parse_intent_json(raw)
        assert intent.intent == allowed


# ── parse_intent ──────────────────────────────────────────────────────────────


async def test_parse_intent_cache_hit():
    cache = IntentCache()
    cached_intent = QueryIntent(intent="count_by_stage", response_format="number", confidence=0.95)
    cache.set("كم lead", "ar", cached_intent)

    mock_client = MagicMock()
    result, cost = await parse_intent("كم lead", [], "ar", mock_client, cache)

    assert result.intent == "count_by_stage"
    assert cost == 0.0
    mock_client.chat_completion.assert_not_called()


async def test_parse_intent_calls_ai_on_miss():
    cache = IntentCache()
    mock_client = AsyncMock()
    mock_client.chat_completion.return_value = MagicMock(
        content='{"intent":"list_overdue_by_salesperson","filters":{"limit":5},"response_format":"table","confidence":0.85}',
        cost_usd=0.0001,
    )

    intent, cost = await parse_intent("show overdue by salesperson", [], "en", mock_client, cache)

    assert intent.intent == "list_overdue_by_salesperson"
    assert cost == pytest.approx(0.0001)
    mock_client.chat_completion.assert_called_once()


async def test_parse_intent_caches_after_ai_call():
    cache = IntentCache()
    mock_client = AsyncMock()
    mock_client.chat_completion.return_value = MagicMock(
        content='{"intent":"count_by_stage","filters":{},"response_format":"number","confidence":0.7}',
        cost_usd=0.00005,
    )

    await parse_intent("how many stages", [], "en", mock_client, cache)
    # Second call should hit cache
    _, cost2 = await parse_intent("how many stages", [], "en", mock_client, cache)
    assert cost2 == 0.0
    assert mock_client.chat_completion.call_count == 1  # only called once


# ── Conversational intent parsing — Bug 2 regression ─────────────────────────


def test_parse_greeting_intent_is_allowed():
    from backend.modules.crm.ai.chat.prompts import ALLOWED_INTENTS, CONVERSATIONAL_INTENTS
    for ci in CONVERSATIONAL_INTENTS:
        assert ci in ALLOWED_INTENTS, f"Conversational intent {ci!r} missing from ALLOWED_INTENTS"


def test_parse_conversational_intent_json():
    for intent_name in ("greeting", "thanks", "farewell", "meta_question", "help_request"):
        raw = f'{{"intent":"{intent_name}","filters":{{}},"response_format":"analysis","confidence":0.9}}'
        intent = _parse_intent_json(raw)
        assert intent.intent == intent_name


async def test_parse_intent_with_context_messages():
    cache = IntentCache()
    mock_client = AsyncMock()
    mock_client.chat_completion.return_value = MagicMock(
        content='{"intent":"free_form_analysis","filters":{},"response_format":"analysis","confidence":0.6}',
        cost_usd=0.0001,
    )
    context = [
        ChatMessage(role=ChatMessageRole.USER, content="previous question"),
        ChatMessage(role=ChatMessageRole.ASSISTANT, content="previous answer"),
    ]

    intent, _ = await parse_intent("and what else?", context, "en", mock_client, cache)
    assert intent.intent == "free_form_analysis"
    call_args = mock_client.chat_completion.call_args
    messages = call_args[1]["messages"] if "messages" in call_args[1] else call_args[0][0]
    # Context messages should appear in the call
    assert len(messages) >= 3  # system + 2 context + user
