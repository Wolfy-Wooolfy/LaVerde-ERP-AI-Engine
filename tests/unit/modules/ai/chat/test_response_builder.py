"""Unit tests for response_builder (Stage 2b)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.modules.ai.chat.response_builder import (
    _extract_followups,
    _filter_followups,
    _get_fallback_followups,
    _normalise_linebreaks,
    build_response,
    is_data_empty,
)
from backend.modules.ai.chat.schemas import QueryIntent


# ── _extract_followups ────────────────────────────────────────────────────────


def test_extract_followups_en():
    text = (
        "There are **10 overdue leads** this week.\n\n"
        "💡 You might also ask:\n"
        "- How many are in Negotiation stage?\n"
        "- Who has the most overdue leads?\n"
        "- Show me team performance"
    )
    main, followups = _extract_followups(text)
    assert "10 overdue leads" in main
    assert len(followups) == 3
    assert "Negotiation" in followups[0]


def test_extract_followups_ar():
    text = (
        "يوجد **10 فرص متأخرة** هذا الأسبوع.\n\n"
        "💡 يمكنك أيضاً أن تسأل:\n"
        "- كم منهم في مرحلة Negotiation؟\n"
        "- من لديه أكبر تأخر؟"
    )
    main, followups = _extract_followups(text)
    assert "10 فرص متأخرة" in main
    assert len(followups) == 2


def test_extract_followups_none():
    text = "Simple response with no follow-up marker."
    main, followups = _extract_followups(text)
    assert main == "Simple response with no follow-up marker."
    assert followups == []


def test_extract_followups_caps_at_3():
    text = (
        "Answer\n\n💡 You might also ask:\n"
        "- Q1\n- Q2\n- Q3\n- Q4\n- Q5"
    )
    _, followups = _extract_followups(text)
    assert len(followups) == 3


# ── _filter_followups — Bug 4 regression ─────────────────────────────────────


def test_filter_removes_meta_ar():
    followups = [
        "إيه أعلى 5 موظفي مبيعات عندهم تأخر؟",
        "هل تحتاج أي تقارير أخرى عن أداء المبيعات؟",
        "كم lead في مرحلة Negotiation؟",
    ]
    filtered = _filter_followups(followups)
    assert len(filtered) == 2
    assert all("هل تحتاج" not in f for f in filtered)


def test_filter_removes_meta_en():
    followups = [
        "Which team has the most overdue leads?",
        "Is there anything else I can help you with?",
        "Do you need any other reports?",
    ]
    filtered = _filter_followups(followups)
    assert len(filtered) == 1
    assert filtered[0] == "Which team has the most overdue leads?"


def test_filter_keeps_concrete_questions():
    followups = [
        "إيه أعلى 5 موظفي مبيعات عندهم تأخر؟",
        "اقترح عليّ 3 عملاء أتواصل معاهم النهارده",
    ]
    assert _filter_followups(followups) == followups


# ── _get_fallback_followups ───────────────────────────────────────────────────


def test_fallback_followups_known_intent_ar():
    fbs = _get_fallback_followups("list_overdue_by_salesperson", "ar")
    assert len(fbs) >= 2
    assert all(isinstance(f, str) and len(f) > 5 for f in fbs)


def test_fallback_followups_known_intent_en():
    fbs = _get_fallback_followups("count_by_stage", "en")
    assert len(fbs) >= 2


def test_fallback_followups_unknown_intent_returns_generic():
    fbs = _get_fallback_followups("not_a_real_intent", "ar")
    assert len(fbs) >= 2  # falls back to free_form_analysis fallbacks


# ── is_data_empty — Bug 3 regression ─────────────────────────────────────────


def test_is_data_empty_empty_dict():
    assert is_data_empty({}) is True


def test_is_data_empty_clarification_needed():
    assert is_data_empty({"type": "clarification_needed"}) is True


def test_is_data_empty_error():
    assert is_data_empty({"type": "error", "message": "oops"}) is True


def test_is_data_empty_with_rows():
    assert is_data_empty({"type": "salesperson_overdue_list", "rows": [{"a": 1}], "total": 1}) is False


def test_is_data_empty_count_zero():
    # count=0 is a valid "0 overdue leads" answer — NOT empty
    assert is_data_empty({"type": "count", "count": 0, "label": "x"}) is False


def test_is_data_empty_count_positive():
    assert is_data_empty({"type": "count", "count": 5, "label": "x"}) is False


def test_is_data_empty_summary_type_always_has_content():
    assert is_data_empty({"type": "general_summary", "total_leads": 0}) is False


def test_is_data_empty_data_quality_type():
    assert is_data_empty({"type": "data_quality", "missing_contact_count": 0}) is False


def test_is_data_empty_conversational_not_empty():
    assert is_data_empty({"type": "conversational"}) is False


# ── build_response ────────────────────────────────────────────────────────────


async def test_build_response_unknown_returns_clarification_en():
    mock_client = MagicMock()
    intent = QueryIntent(intent="unknown", response_format="analysis")
    text, followups, cost = await build_response(
        "إنت AI؟", intent, {"type": "clarification_needed"}, "en", [], mock_client
    )
    assert "rephrase" in text.lower() or "understand" in text.lower()
    assert cost == 0.0
    mock_client.chat_completion.assert_not_called()


async def test_build_response_unknown_returns_clarification_ar():
    mock_client = MagicMock()
    intent = QueryIntent(intent="unknown", response_format="analysis")
    text, followups, cost = await build_response(
        "كلام", intent, {"type": "clarification_needed"}, "ar", [], mock_client
    )
    assert "عذراً" in text or "صياغة" in text
    assert cost == 0.0


async def test_build_response_with_data():
    mock_client = AsyncMock()
    mock_client.chat_completion.return_value = MagicMock(
        content=(
            "There are **47 overdue** leads in Negotiation stage.\n\n"
            "💡 You might also ask:\n"
            "- Which team has the most?\n"
            "- Who is responsible for them?"
        ),
        cost_usd=0.0003,
    )
    intent = QueryIntent(intent="count_by_stage", response_format="number")
    data = {"type": "count", "count": 47, "label": "Negotiation"}

    text, followups, cost = await build_response(
        "How many in Negotiation?", intent, data, "en", [], mock_client
    )
    assert "47" in text
    assert len(followups) >= 2
    assert cost == pytest.approx(0.0003)


async def test_build_response_ar_locale():
    mock_client = AsyncMock()
    mock_client.chat_completion.return_value = MagicMock(
        content="يوجد **47 فرصة** في مرحلة Negotiation.\n\n💡 يمكنك أيضاً أن تسأل:\n- ما التالي؟",
        cost_usd=0.0002,
    )
    intent = QueryIntent(intent="count_by_stage", response_format="number")
    data = {"type": "count", "count": 47, "label": "Negotiation"}

    text, followups, cost = await build_response(
        "كم في Negotiation؟", intent, data, "ar", [], mock_client
    )
    assert "47" in text
    # Verify the prompt passed to AI was Arabic
    call_kwargs = mock_client.chat_completion.call_args[1]
    messages = call_kwargs["messages"]
    last_user_msg = messages[-1]["content"]
    assert "عربية" in last_user_msg or "العربية" in last_user_msg or "بالعربية" in last_user_msg


async def test_build_response_ai_error_returns_fallback():
    mock_client = AsyncMock()
    mock_client.chat_completion.side_effect = Exception("AI service down")
    intent = QueryIntent(intent="count_by_stage", response_format="number")

    text, followups, cost = await build_response(
        "How many?", intent, {"type": "count", "count": 5}, "en", [], mock_client
    )
    assert "error" in text.lower() or "sorry" in text.lower()
    assert cost == 0.0


# ── Bug 3: empty data triggers clarification, not empty headers ───────────────


async def test_build_response_empty_data_returns_clarification_not_ai_call():
    """When data is empty for a data intent, respond with clarification, no AI call."""
    mock_client = AsyncMock()
    intent = QueryIntent(intent="list_overdue_by_salesperson", response_format="table")
    empty_data = {"type": "salesperson_overdue_list", "rows": [], "total": 0}

    text, followups, cost = await build_response(
        "إيه أعلى موظفي مبيعات؟", intent, empty_data, "ar", [], mock_client
    )
    mock_client.chat_completion.assert_not_called()
    assert cost == 0.0
    assert len(followups) >= 2


async def test_build_response_empty_data_en_includes_fallback_hints():
    mock_client = AsyncMock()
    intent = QueryIntent(intent="count_by_stage", response_format="number")
    # count=0 is valid data ("0 overdue leads"). Use empty rows to test the guardrail.
    empty_data = {"type": "salesperson_overdue_list", "rows": [], "total": 0}

    text, followups, cost = await build_response(
        "How many in Unknown Stage?", intent, empty_data, "en", [], mock_client
    )
    mock_client.chat_completion.assert_not_called()
    assert len(followups) >= 2


# ── Bug 2: conversational intents bypass CRM ─────────────────────────────────


async def test_build_response_greeting_uses_short_prompt():
    mock_client = AsyncMock()
    mock_client.chat_completion.return_value = MagicMock(
        content="أهلاً! كيف يمكنني مساعدتك؟\n\n💡 يمكنك أيضاً أن تسأل:\n- إيه أعلى 5 موظفي مبيعات؟\n- كم lead متأخر؟",
        cost_usd=0.00005,
    )
    intent = QueryIntent(intent="greeting", response_format="analysis")
    data = {"type": "conversational", "subtype": "greeting"}

    text, followups, cost = await build_response(
        "أهلاً", intent, data, "ar", [], mock_client
    )
    assert mock_client.chat_completion.call_count == 1
    # Should use a very short max_tokens (conversational path)
    call_kwargs = mock_client.chat_completion.call_args[1]
    assert call_kwargs["max_tokens"] <= 200
    assert len(followups) >= 2


async def test_build_response_thanks_en_bypasses_crm():
    mock_client = AsyncMock()
    mock_client.chat_completion.return_value = MagicMock(
        content="You're welcome!\n\n💡 You might also ask:\n- Top 5 sales employees by overdue leads?",
        cost_usd=0.00004,
    )
    intent = QueryIntent(intent="thanks", response_format="analysis")
    data = {"type": "conversational", "subtype": "thanks"}

    text, followups, cost = await build_response(
        "Thank you!", intent, data, "en", [], mock_client
    )
    assert "welcome" in text.lower()
    assert cost > 0  # AI was called once for conversational reply


async def test_build_response_conversational_ai_error_returns_friendly_fallback():
    mock_client = AsyncMock()
    mock_client.chat_completion.side_effect = Exception("timeout")
    intent = QueryIntent(intent="thanks", response_format="analysis")
    data = {"type": "conversational", "subtype": "thanks"}

    text, followups, cost = await build_response(
        "شكراً", intent, data, "ar", [], mock_client
    )
    assert cost == 0.0
    assert len(followups) >= 2


# ── Bug 1: terminology — no "مندوب" in clarification strings ─────────────────


def test_no_mandup_in_clarification_ar():
    from backend.modules.ai.chat.response_builder import _CLARIFICATION_AR
    assert "مندوب" not in _CLARIFICATION_AR
    assert "مندوبين" not in _CLARIFICATION_AR


def test_no_mandup_in_suggested_questions():
    from backend.modules.ai.chat.prompts import SUGGESTED_QUESTIONS
    for q in SUGGESTED_QUESTIONS["ar"]:
        assert "مندوب" not in q, f"Found 'مندوب' in suggested question: {q!r}"


# ── Bug 1: <br> normalisation ─────────────────────────────────────────────────


def test_normalise_linebreaks_replaces_br():
    assert _normalise_linebreaks("line1<br>line2") == "line1\nline2"
    assert _normalise_linebreaks("line1<BR>line2") == "line1\nline2"
    assert _normalise_linebreaks("line1<br/>line2") == "line1\nline2"
    assert _normalise_linebreaks("line1<br />line2") == "line1\nline2"


def test_normalise_linebreaks_leaves_clean_text():
    assert _normalise_linebreaks("clean text\nwith newlines") == "clean text\nwith newlines"


async def test_build_response_strips_br_from_ai_output():
    """AI output containing <br> must not reach the caller — it is normalized to \\n."""
    mock_client = AsyncMock()
    mock_client.chat_completion.return_value = MagicMock(
        content=(
            "يوجد **10 عملاء**.<br>أعلى موظف: Ahmed Ali (5 متأخر).<br>"
            "💡 يمكنك أيضاً أن تسأل:\n- إيه أعلى 5 موظفي مبيعات؟\n- كم lead في Negotiation؟"
        ),
        cost_usd=0.0002,
    )
    intent = QueryIntent(intent="list_overdue_by_salesperson", response_format="table")
    data = {"type": "salesperson_overdue_list", "rows": [{"salesperson_name": "Ahmed Ali", "overdue_count": 5}], "total": 1}

    text, followups, cost = await build_response(
        "إيه أعلى موظفي المبيعات؟", intent, data, "ar", [], mock_client
    )
    assert "<br>" not in text
    assert "<BR>" not in text


def test_terminology_prompt_uses_correct_term():
    from backend.modules.ai.chat.prompts import _TERMINOLOGY_RULES
    assert "موظف مبيعات" in _TERMINOLOGY_RULES
    assert "NEVER" in _TERMINOLOGY_RULES  # English rule present
    assert "مندوب" in _TERMINOLOGY_RULES  # only appears as the forbidden term
