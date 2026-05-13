"""Tests for locale-aware prompt generation."""

from datetime import datetime, timezone

import pytest

from backend.shared.ai.cache import lead_cache_key, overdue_list_cache_key
from backend.modules.crm.ai.prompts import (
    LEAD_PRIORITIZATION_SYSTEM_PROMPT_AR,
    LEAD_PRIORITIZATION_SYSTEM_PROMPT_EN,
    build_lead_prioritization_prompt,
    get_system_prompt,
)
from backend.modules.crm.ai.schemas import LeadContext


def _lead(**kwargs) -> LeadContext:
    defaults = dict(
        lead_id=42,
        name="Ahmed Hassan",
        stage_id=28,
        stage_name="Negotiation",
        create_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        has_phone=True,
        has_mobile=False,
        has_email=True,
        salesperson_name="Khaled",
        team_name="Team A",
        days_in_stage=30,
        is_critical_stage=True,
        activity_state="overdue",
        days_since_last_message=10,
    )
    defaults.update(kwargs)
    return LeadContext(**defaults)


# ── get_system_prompt ─────────────────────────────────────────────────────────


def test_get_system_prompt_en_returns_en_prompt():
    assert get_system_prompt("en") is LEAD_PRIORITIZATION_SYSTEM_PROMPT_EN


def test_get_system_prompt_ar_returns_ar_prompt():
    assert get_system_prompt("ar") is LEAD_PRIORITIZATION_SYSTEM_PROMPT_AR


def test_get_system_prompt_unknown_locale_defaults_to_en():
    assert get_system_prompt("fr") is LEAD_PRIORITIZATION_SYSTEM_PROMPT_EN
    assert get_system_prompt("") is LEAD_PRIORITIZATION_SYSTEM_PROMPT_EN


# ── EN system prompt content ──────────────────────────────────────────────────


def test_en_prompt_contains_english_output_rule():
    assert "ENTIRELY in English" in LEAD_PRIORITIZATION_SYSTEM_PROMPT_EN


def test_en_prompt_forbids_email_as_primary():
    assert "LAST RESORT" in LEAD_PRIORITIZATION_SYSTEM_PROMPT_EN


def test_en_prompt_mentions_whatsapp():
    assert "WhatsApp" in LEAD_PRIORITIZATION_SYSTEM_PROMPT_EN


def test_en_prompt_has_key_signal_field():
    assert "key_signal" in LEAD_PRIORITIZATION_SYSTEM_PROMPT_EN


# ── AR system prompt content ──────────────────────────────────────────────────


def test_ar_prompt_is_in_arabic():
    assert "واتساب" in LEAD_PRIORITIZATION_SYSTEM_PROMPT_AR
    assert "معاينة" in LEAD_PRIORITIZATION_SYSTEM_PROMPT_AR


def test_ar_prompt_tier_values_stay_english():
    assert "critical" in LEAD_PRIORITIZATION_SYSTEM_PROMPT_AR
    assert "high" in LEAD_PRIORITIZATION_SYSTEM_PROMPT_AR
    assert "dead" in LEAD_PRIORITIZATION_SYSTEM_PROMPT_AR


def test_ar_prompt_has_arabic_output_rule():
    assert "بالعربية بالكامل" in LEAD_PRIORITIZATION_SYSTEM_PROMPT_AR


def test_ar_prompt_has_key_signal_field():
    assert "key_signal" in LEAD_PRIORITIZATION_SYSTEM_PROMPT_AR


# ── EN user prompt ────────────────────────────────────────────────────────────


def test_en_user_prompt_uses_english_labels():
    prompt = build_lead_prioritization_prompt(_lead(), locale="en")
    assert "Lead ID:" in prompt
    assert "Stage:" in prompt
    assert "Days in stage:" in prompt
    assert "Contact info:" in prompt


def test_en_user_prompt_contact_info_english():
    prompt = build_lead_prioritization_prompt(_lead(has_phone=True, has_mobile=True, has_email=False), locale="en")
    assert "phone" in prompt
    assert "mobile" in prompt


def test_en_user_prompt_days_since_in_english():
    prompt = build_lead_prioritization_prompt(_lead(days_since_last_message=7), locale="en")
    assert "7 days ago" in prompt


def test_en_user_prompt_na_when_no_days():
    prompt = build_lead_prioritization_prompt(_lead(days_since_last_message=None), locale="en")
    assert "N/A" in prompt


# ── AR user prompt ────────────────────────────────────────────────────────────


def test_ar_user_prompt_uses_arabic_labels():
    prompt = build_lead_prioritization_prompt(_lead(), locale="ar")
    assert "رقم العميل:" in prompt
    assert "المرحلة:" in prompt
    assert "أيام في المرحلة:" in prompt
    assert "معلومات الاتصال:" in prompt


def test_ar_user_prompt_contact_info_arabic():
    prompt = build_lead_prioritization_prompt(_lead(has_phone=True, has_mobile=True, has_email=False), locale="ar")
    assert "تليفون" in prompt
    assert "موبايل" in prompt


def test_ar_user_prompt_days_since_arabic():
    prompt = build_lead_prioritization_prompt(_lead(days_since_last_message=5), locale="ar")
    assert "منذ 5 يوم" in prompt


def test_ar_user_prompt_na_when_no_days():
    prompt = build_lead_prioritization_prompt(_lead(days_since_last_message=None), locale="ar")
    assert "غير متاح" in prompt


def test_ar_user_prompt_critical_stage_arabic():
    prompt = build_lead_prioritization_prompt(_lead(is_critical_stage=True), locale="ar")
    assert "نعم" in prompt


def test_ar_user_prompt_non_critical_arabic():
    prompt = build_lead_prioritization_prompt(_lead(is_critical_stage=False), locale="ar")
    assert "لا" in prompt


def test_ar_user_prompt_unassigned_arabic():
    prompt = build_lead_prioritization_prompt(_lead(salesperson_name=None, team_name=None), locale="ar")
    assert "غير محدد" in prompt
    assert "بدون فريق" in prompt


# ── Cache key includes locale ─────────────────────────────────────────────────


def test_lead_cache_key_differs_by_locale():
    k_en = lead_cache_key(42, 28, None, 3, "abc", "en")
    k_ar = lead_cache_key(42, 28, None, 3, "abc", "ar")
    assert k_en != k_ar


def test_lead_cache_key_same_locale_same_key():
    k1 = lead_cache_key(42, 28, None, 3, "abc", "en")
    k2 = lead_cache_key(42, 28, None, 3, "abc", "en")
    assert k1 == k2


def test_overdue_list_cache_key_differs_by_locale():
    k_en = overdue_list_cache_key(10, "en")
    k_ar = overdue_list_cache_key(10, "ar")
    assert k_en != k_ar
    assert "en" in k_en
    assert "ar" in k_ar


def test_overdue_list_cache_key_default_locale_is_en():
    assert overdue_list_cache_key(10) == overdue_list_cache_key(10, "en")
