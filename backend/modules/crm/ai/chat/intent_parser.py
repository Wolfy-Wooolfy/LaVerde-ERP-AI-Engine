"""Stage 1: Parse user question → structured QueryIntent."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from loguru import logger

from backend.core.config import settings
from backend.modules.crm.ai.chat.prompts import ALLOWED_INTENTS, INTENT_PARSING_SYSTEM_PROMPT
from backend.modules.crm.ai.chat.schemas import ChatMessage, ChatMessageRole, QueryIntent

if TYPE_CHECKING:
    from backend.shared.ai.cache import IntentCache
    from backend.shared.ai.client import OpenAIClient


def _build_context_messages(context: list[ChatMessage]) -> list[dict[str, str]]:
    result = []
    for msg in context:
        if msg.role == ChatMessageRole.USER:
            result.append({"role": "user", "content": msg.content})
        elif msg.role == ChatMessageRole.ASSISTANT:
            result.append({"role": "assistant", "content": msg.content})
    return result


def _parse_intent_json(raw: str) -> QueryIntent:
    """Parse AI JSON output into QueryIntent with safe fallback."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(f"Intent parser returned non-JSON: {raw[:200]!r}")
        return QueryIntent(intent="unknown", response_format="analysis", confidence=0.1)

    intent_value = data.get("intent", "unknown")
    if intent_value not in ALLOWED_INTENTS:
        logger.warning(f"Intent parser returned unlisted intent: {intent_value!r} — falling back to unknown")
        intent_value = "unknown"

    filters = data.get("filters", {})
    if not isinstance(filters, dict):
        filters = {}

    response_format = data.get("response_format", "analysis")
    if response_format not in ("table", "number", "list", "analysis", "mini_dashboard"):
        response_format = "analysis"

    confidence = float(data.get("confidence", 0.8))

    return QueryIntent(
        intent=intent_value,
        filters=filters,
        response_format=response_format,  # type: ignore[arg-type]
        confidence=confidence,
    )


async def parse_intent(
    question: str,
    context: list[ChatMessage],
    locale: str,
    ai_client: "OpenAIClient",
    intent_cache: "IntentCache",
) -> tuple[QueryIntent, float]:
    """
    Stage 1: Classify user question into a structured intent.
    Returns (intent, cost_usd). Cost is 0.0 on cache hit.
    """
    cached = intent_cache.get(question, locale)
    if cached is not None:
        logger.debug(f"Intent cache hit: {question[:50]!r}")
        return cached, 0.0

    messages: list[dict[str, str]] = [
        {"role": "system", "content": INTENT_PARSING_SYSTEM_PROMPT},
        *_build_context_messages(context[-5:]),
        {"role": "user", "content": question},
    ]

    response = await ai_client.chat_completion(
        messages=messages,
        model=settings.AI_MODEL,
        temperature=0.1,
        max_tokens=200,
        response_format={"type": "json_object"},
    )

    intent = _parse_intent_json(response.content)
    intent_cache.set(question, locale, intent)

    logger.debug(
        f"Intent parsed: {intent.intent!r} (conf={intent.confidence:.2f}) "
        f"for: {question[:50]!r}"
    )
    return intent, response.cost_usd
