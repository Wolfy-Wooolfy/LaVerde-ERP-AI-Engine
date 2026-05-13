"""Stage 2b: Synthesize final AI response from intent + fetched data."""

from __future__ import annotations

import asyncio
import re
import time
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
    from backend.shared.ai.client import OpenAIClient

_CLARIFICATION_EN = (
    "I'm not sure I understood that well enough to answer accurately. "
    "Could you rephrase it? For example:\n\n"
    "- 'Show me the top 5 sales employees with the most overdue leads'\n"
    "- 'How many leads are in Follow up stage?'\n"
    "- 'Recommend leads for me to call today'"
)

_CLARIFICATION_AR = (
    "عذراً، لم أفهم سؤالك بشكل كافٍ لأجيب بدقة. "
    "هل يمكنك إعادة صياغته؟ على سبيل المثال:\n\n"
    "- إيه أعلى 5 موظفي مبيعات عندهم تأخر؟\n"
    "- كم lead في مرحلة Follow up؟\n"
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

# ── Real stage-name cache (1-hour TTL, fetched from Odoo on first use) ────────

_stage_names_cache: dict = {}
_STAGE_CACHE_TTL = 3600  # seconds

# Intents that don't produce a concrete, answerable follow-up
_DROP_INTENTS: frozenset[str] = frozenset({
    "unknown",
    "free_form_analysis",
    "greeting",
    "thanks",
    "meta_question",
    "help_request",
    "farewell",
})


async def _get_real_stage_names(crm) -> list[str]:
    """Fetch real CRM stage names from Odoo; cached for 1 hour."""
    now = time.monotonic()
    entry = _stage_names_cache.get("names")
    if entry and now - entry["ts"] < _STAGE_CACHE_TTL:
        return entry["data"]
    try:
        stages = await crm.client.execute_kw(
            "crm.stage",
            "search_read",
            args=[[]],
            kwargs={"fields": ["name"], "limit": 200},
        )
        names = [s["name"] for s in stages]
    except Exception as exc:
        logger.warning(f"Could not fetch real stage names for prompt injection: {exc}")
        names = []
    _stage_names_cache["names"] = {"data": names, "ts": now}
    return names


async def _validate_followups(
    followups: list[str],
    real_stage_names: list[str],
    ai_client,
    intent_cache,
    locale: str,
) -> list[str]:
    """
    Drop follow-up suggestions that:
    - parse to a non-data / vague intent (unknown, free_form_analysis, conversational)
    - reference a stage name not present in real Odoo data

    All intent-parse calls run in parallel (cache hits are free; misses cost ~$0.00002 each).
    """
    from backend.modules.ai.chat.intent_parser import parse_intent  # local to avoid circular
    from backend.modules.ai.chat.data_fetcher import _normalise_stage

    real_lower = {s.lower() for s in real_stage_names}

    async def _check_one(fu: str) -> str | None:
        try:
            parsed, _ = await parse_intent(fu, [], locale, ai_client, intent_cache)
            if parsed.intent in _DROP_INTENTS:
                logger.debug(f"Dropping follow-up (intent={parsed.intent!r}): {fu!r}")
                return None
            stage = (parsed.filters or {}).get("stage", "")
            if stage and real_lower:
                normalised = _normalise_stage(stage)
                if normalised.lower() not in real_lower:
                    logger.debug(
                        f"Dropping follow-up (unknown stage {stage!r}→{normalised!r}): {fu!r}"
                    )
                    return None
        except Exception as exc:
            logger.debug(f"Dropping follow-up (parse error: {exc}): {fu!r}")
            return None
        return fu

    results = await asyncio.gather(*(_check_one(fu) for fu in followups))
    return [r for r in results if r is not None]


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
    # lead_detail always has a lead object; not_found is handled by short-circuit above
    if dtype == "lead_detail":
        return False
    # recommendations: let the AI explain "no leads" even when the list is empty
    if dtype == "recommendations":
        return False
    return True


async def build_response(
    question: str,
    intent: QueryIntent,
    data: dict,
    locale: str,
    context: list[ChatMessage],
    ai_client: "OpenAIClient",
    crm=None,
    intent_cache=None,
) -> tuple[str, list[str], float]:
    """
    Stage 2b: Generate final human-readable response.
    Returns (response_text, followup_suggestions, cost_usd).

    crm / intent_cache: when provided, real stage names are injected into the
    Stage 2 prompt and AI-generated follow-ups are post-validated against Odoo data.
    """
    # Fetch real stage names once; used for prompt injection + follow-up validation
    real_stages: list[str] = []
    if crm is not None:
        real_stages = await _get_real_stage_names(crm)

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
        if real_stages and intent_cache is not None:
            followups = await _validate_followups(followups, real_stages, ai_client, intent_cache, locale)
        if len(followups) < 2:
            followups = _get_fallback_followups("free_form_analysis", locale)
        return main_text, followups, response.cost_usd

    # ── Short-circuit: unknown intent ─────────────────────────────────────────
    if intent.intent == "unknown" or data.get("type") == "clarification_needed":
        clarification = _CLARIFICATION_AR if locale == "ar" else _CLARIFICATION_EN
        return clarification, _get_fallback_followups("free_form_analysis", locale), 0.0

    # ── Short-circuit: signal with no chatter data (product gap, not a code error) ─
    if data.get("type") == "signal_no_data":
        signal = data.get("signal", "")
        if locale == "ar":
            if signal == "site_visit":
                msg = (
                    "ما لقيتش عملاء طلبوا معاينة في الـ chatter بتاع Odoo. "
                    "ممكن السيلز بيسجلوا المعاينات في مكان تاني زي WhatsApp. "
                    "هل عايز أعرضلك العملاء الأعلى أولوية للاتصال بدل كده؟"
                )
            else:
                msg = (
                    "ما لقيتش محادثات بالكلمات دي في الـ chatter بتاع Odoo. "
                    "ممكن الفريق بيسجل هذا النشاط خارج النظام. "
                    "هل عايز أعرضلك العملاء المتأخرين بدل كده؟"
                )
        else:
            if signal == "site_visit":
                msg = (
                    "I couldn't find leads with site visit signals in Odoo's chatter. "
                    "The sales team may be logging visits elsewhere (e.g., WhatsApp). "
                    "Want me to show top-priority leads to contact instead?"
                )
            else:
                msg = (
                    "I couldn't find leads with this signal in Odoo's chatter. "
                    "The team may be logging this activity outside the system. "
                    "Want me to show overdue leads instead?"
                )
        return msg, _get_fallback_followups("recommendation_top_priority", locale), 0.0

    # ── Short-circuit: lead ID not found in system ───────────────────────────
    if data.get("type") == "not_found":
        lead_id = data.get("lead_id", "")
        if locale == "ar":
            msg = f"ما لقيتش lead بالرقم {lead_id} في النظام."
        else:
            msg = f"No lead with ID {lead_id} was found in the system."
        return msg, _get_fallback_followups("lead_details_by_id", locale), 0.0

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

    # Inject real stage names so the AI cannot invent placeholder examples
    if real_stages:
        real_stages_str = ", ".join(f'"{s}"' for s in real_stages)
        user_prompt += (
            "\n\nCRITICAL — FOLLOW-UP QUESTIONS MUST USE REAL STAGES ONLY: "
            f"The real pipeline stage names in this system are: {real_stages_str}. "
            "When writing follow-up question suggestions, ONLY reference stage names from this list. "
            "NEVER invent names like 'مؤهل', 'معلق', 'Qualified', 'Pending'. "
            "If you cannot think of a concrete follow-up, skip it — the system will provide alternatives."
        )

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

    # Layer 1: drop meta/open-ended follow-ups (regex filter)
    followups = _filter_followups(followups)

    # Layer 2: post-validate against intent parser + real stage existence
    if real_stages and intent_cache is not None:
        followups = await _validate_followups(followups, real_stages, ai_client, intent_cache, locale)

    # Layer 3: pad with curated fallbacks if not enough valid follow-ups remain
    if len(followups) < 2:
        fallbacks = _get_fallback_followups(intent.intent, locale)
        for fb in fallbacks:
            if fb not in followups:
                followups.append(fb)
            if len(followups) >= 2:
                break

    return main_text, followups[:3], response.cost_usd
