"""Stage 2b: Synthesize final AI response from intent + fetched data."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from loguru import logger

from backend.core.config import settings
from backend.modules.ai.chat.prompts import (
    CONVERSATIONAL_INTENTS,
    FALLBACK_FOLLOWUPS,
    RESPONSE_FORMATS,
    build_conversational_response_prompt_ar,
    build_conversational_response_prompt_en,
    build_response_generation_prompt_ar,
    build_response_generation_prompt_en,
)
from backend.modules.ai.chat.schemas import ChatMessage, ChatMessageRole, QueryIntent

if TYPE_CHECKING:
    from backend.modules.ai.client import OpenAIClient

_CLARIFICATION_EN = (
    "I'm not sure I understood that well enough to answer accurately. "
    "Could you rephrase it? For example:\n\n"
    "- 'Show me the top 5 sales employees with the most overdue leads'\n"
    "- 'How many leads are in Negotiation stage?'\n"
    "- 'Recommend leads for me to call today'"
)

_CLARIFICATION_AR = (
    "عذراً، لم أفهم سؤالك بشكل كافٍ لأجيب بدقة. "
    "هل يمكنك إعادة صياغته؟ على سبيل المثال:\n\n"
    "- إيه أعلى 5 موظفي مبيعات عندهم تأخر؟\n"
    "- كم lead في مرحلة Negotiation؟\n"
    "- اقترح عليّ leads أتصل بيهم النهارده"
)

_EMPTY_DATA_EN = "I don't have enough specific data to answer that. Try one of these:"

_EMPTY_DATA_AR = "لا تتوفر لديّ بيانات كافية لهذا السؤال تحديداً. جرّب أحد هذه:"

# Patterns that indicate a meta/open-ended follow-up (not data-grounded)
_META_FOLLOWUP_PATTERNS = re.compile(
    r"هل\s+(تحتاج|تريد|تودّ|هناك|يمكن)|"
    r"(any other|anything else|more reports|do you need|would you like|"
    r"is there anything|can I help|shall I|أي شيء آخر|هل من شيء آخر|"
    r"تقارير أخرى|مزيداً من|هل تود)",
    re.IGNORECASE,
)


_BR_RE = re.compile(r'<br\s*/?>', re.IGNORECASE)


def _normalise_linebreaks(text: str) -> str:
    """Replace HTML <br> tags the AI sometimes emits with real newlines."""
    return _BR_RE.sub('\n', text)


def _extract_followups(text: str) -> tuple[str, list[str]]:
    """Split response into (main_content, follow_up_list)."""
    pattern = r"💡\s*(?:You might also ask|يمكنك أيضاً أن تسأل)[:\s]*\n?(.*?)$"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    if not match:
        return text.strip(), []

    main_content = text[: match.start()].strip()
    followup_block = match.group(1).strip()
    followups = []
    for line in followup_block.split("\n"):
        line = line.strip().lstrip("0123456789.-) \t").strip()
        if line and not line.startswith("💡"):
            followups.append(line)
    return main_content, followups[:3]


def _filter_followups(followups: list[str]) -> list[str]:
    """Drop meta / open-ended follow-up suggestions."""
    return [f for f in followups if not _META_FOLLOWUP_PATTERNS.search(f)]


def _get_fallback_followups(intent: str, locale: str) -> list[str]:
    """Return curated fallback follow-ups for an intent + locale."""
    intent_map = FALLBACK_FOLLOWUPS.get(intent, FALLBACK_FOLLOWUPS.get("free_form_analysis", {}))
    return intent_map.get(locale, intent_map.get("en", []))


def is_data_empty(data: dict) -> bool:
    """True if data contains no queryable content — just metadata or nothing."""
    if not data:
        return True
    dtype = data.get("type", "")
    if dtype in ("clarification_needed", "error", "unavailable"):
        return True
    if dtype == "conversational":
        return False  # handled separately
    rows = data.get("rows", [])
    items = data.get("items", [])
    count = data.get("count")
    leads = data.get("leads", [])
    # summary type always has content even if zeros
    if dtype in ("summary", "general_summary"):
        return False
    if rows or items or leads:
        return False
    if count is not None:
        return False  # 0 leads is a real answer, not missing data
    # stage_count always has a count key — never empty
    if dtype == "stage_count":
        return False
    # data_quality types always have content
    if dtype in ("data_quality", "data_quality_full", "team_performance", "salesperson_performance"):
        return False
    return True


async def build_response(
    question: str,
    intent: QueryIntent,
    data: dict,
    locale: str,
    context: list[ChatMessage],
    ai_client: "OpenAIClient",
) -> tuple[str, list[str], float]:
    """
    Stage 2b: Generate final human-readable response.
    Returns (response_text, followup_suggestions, cost_usd).
    """
    # ── Fast-path: conversational intents (bypass CRM, short prompt) ──────────
    if intent.intent in CONVERSATIONAL_INTENTS:
        subtype = intent.intent
        if locale == "ar":
            user_prompt = build_conversational_response_prompt_ar(question, subtype)
        else:
            user_prompt = build_conversational_response_prompt_en(question, subtype)

        try:
            response = await ai_client.chat_completion(
                messages=[{"role": "user", "content": user_prompt}],
                model=settings.AI_MODEL,
                temperature=0.7,
                max_tokens=200,
            )
        except Exception as exc:
            logger.error(f"Conversational response builder failed: {exc}")
            fallback = "العفو! تحت أمرك." if locale == "ar" else "You're welcome! How can I help?"
            return fallback, _get_fallback_followups("free_form_analysis", locale), 0.0

        main_text, followups = _extract_followups(_normalise_linebreaks(response.content))
        followups = _filter_followups(followups)
        if len(followups) < 2:
            followups = _get_fallback_followups("free_form_analysis", locale)
        return main_text, followups, response.cost_usd

    # ── Short-circuit: unknown intent ─────────────────────────────────────────
    if intent.intent == "unknown" or data.get("type") == "clarification_needed":
        clarification = _CLARIFICATION_AR if locale == "ar" else _CLARIFICATION_EN
        return clarification, _get_fallback_followups("free_form_analysis", locale), 0.0

    # ── Short-circuit: stage not found (no AI call needed) ────────────────────
    if data.get("type") == "stage_not_found":
        stage = data.get("requested_stage", "")
        valid = "New, No Answer, Follow up, Interested, Contact in the Future, Re-Distribution, Reservation, Down Payment Confirm & Contracted"
        if locale == "ar":
            msg = (
                f"لم أجد مرحلة باسم '{stage}' بشكل دقيق. "
                f"الأسماء الصحيحة للمراحل: {valid}."
            )
        else:
            msg = (
                f"I couldn't find a stage named exactly '{stage}'. "
                f"Valid stage names: {valid}."
            )
        return msg, _get_fallback_followups("count_by_stage", locale), 0.0

    # ── Empty-data guardrail ──────────────────────────────────────────────────
    if is_data_empty(data):
        empty_msg = _EMPTY_DATA_AR if locale == "ar" else _EMPTY_DATA_EN
        fallbacks = _get_fallback_followups(intent.intent, locale)
        bullet_list = "\n".join(f"- {q}" for q in fallbacks)
        # Double newline separates intro paragraph from bullet list block so
        # marked.js renders the bullets as a proper <ul>, not escaped <br> text.
        return f"{empty_msg}\n\n{bullet_list}", fallbacks, 0.0

    # ── Normal path: data-backed response generation ──────────────────────────
    format_hint = RESPONSE_FORMATS.get(intent.response_format, RESPONSE_FORMATS["analysis"])

    if locale == "ar":
        user_prompt = build_response_generation_prompt_ar(question, intent.intent, data, format_hint)
    else:
        user_prompt = build_response_generation_prompt_en(question, intent.intent, data, format_hint)

    # Include last 3 conversation turns for coherence
    messages: list[dict[str, str]] = []
    for msg in context[-3:]:
        if msg.role == ChatMessageRole.USER:
            messages.append({"role": "user", "content": msg.content})
        elif msg.role == ChatMessageRole.ASSISTANT:
            messages.append({"role": "assistant", "content": msg.content})
    messages.append({"role": "user", "content": user_prompt})

    try:
        response = await ai_client.chat_completion(
            messages=messages,
            model=settings.AI_MODEL,
            temperature=0.4,
            max_tokens=600,
        )
    except Exception as exc:
        logger.error(f"Response builder AI call failed: {exc}")
        fallback = "عذراً، حدث خطأ. حاول مرة أخرى." if locale == "ar" else "Sorry, an error occurred. Please try again."
        return fallback, [], 0.0

    main_text, followups = _extract_followups(_normalise_linebreaks(response.content))

    # Filter out meta/open-ended follow-ups then pad with fallbacks if needed
    followups = _filter_followups(followups)
    if len(followups) < 2:
        fallbacks = _get_fallback_followups(intent.intent, locale)
        for fb in fallbacks:
            if fb not in followups:
                followups.append(fb)
            if len(followups) >= 2:
                break

    return main_text, followups[:3], response.cost_usd
