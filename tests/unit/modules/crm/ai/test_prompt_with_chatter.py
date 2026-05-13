"""Tests for build_lead_prioritization_prompt chatter integration."""

from datetime import datetime, timezone

import pytest

from backend.modules.crm.ai.prompts import (
    LEAD_PRIORITIZATION_SYSTEM_PROMPT_EN as LEAD_PRIORITIZATION_SYSTEM_PROMPT,
    build_lead_prioritization_prompt,
)
from backend.modules.crm.ai.schemas import ChatterMessage, LeadContext


def _base_lead(**kwargs) -> LeadContext:
    defaults = dict(
        lead_id=999,
        name="Test Lead",
        stage_id=28,
        stage_name="Negotiation",
        create_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    defaults.update(kwargs)
    return LeadContext(**defaults)


def _msg(text: str, days_ago: int = 3) -> ChatterMessage:
    from datetime import timedelta
    return ChatterMessage(
        date=datetime.now(timezone.utc) - timedelta(days=days_ago),
        author="Khaled",
        body_text=text,
        message_type="comment",
    )


def test_prompt_has_no_chatter_section_when_empty():
    lead = _base_lead()
    prompt = build_lead_prioritization_prompt(lead)
    assert "Chatter" not in prompt
    assert "Detected signals" not in prompt


def test_prompt_includes_chatter_section_when_messages_present():
    lead = _base_lead(
        recent_messages=[_msg("Customer expressed interest in unit 5A")]
    )
    prompt = build_lead_prioritization_prompt(lead)
    assert "Recent Chatter" in prompt
    assert "Customer expressed interest in unit 5A" in prompt


def test_prompt_includes_author_and_days_ago():
    lead = _base_lead(recent_messages=[_msg("test message", days_ago=5)])
    prompt = build_lead_prioritization_prompt(lead)
    assert "Khaled" in prompt
    assert "5d ago" in prompt


def test_prompt_includes_signals_section_when_site_visit():
    lead = _base_lead(has_site_visit=True)
    prompt = build_lead_prioritization_prompt(lead)
    assert "Site visit mentioned" in prompt


def test_prompt_includes_signals_section_when_phone_attempt():
    lead = _base_lead(has_phone_attempt=True)
    prompt = build_lead_prioritization_prompt(lead)
    assert "Phone contact attempted" in prompt


def test_prompt_includes_days_since_last_message():
    lead = _base_lead(days_since_last_message=12)
    prompt = build_lead_prioritization_prompt(lead)
    assert "12 days ago" in prompt


def test_prompt_shows_na_when_no_message_date():
    lead = _base_lead(days_since_last_message=None)
    prompt = build_lead_prioritization_prompt(lead)
    assert "N/A" in prompt


def test_system_prompt_contains_whatsapp_guidance():
    assert "WhatsApp" in LEAD_PRIORITIZATION_SYSTEM_PROMPT


def test_system_prompt_forbids_email_as_primary():
    assert "email" in LEAD_PRIORITIZATION_SYSTEM_PROMPT.lower()
    assert "LAST RESORT" in LEAD_PRIORITIZATION_SYSTEM_PROMPT


def test_system_prompt_includes_key_signal_in_schema():
    assert "key_signal" in LEAD_PRIORITIZATION_SYSTEM_PROMPT


def test_system_prompt_includes_arabic_site_visit_term():
    assert "معاينة" in LEAD_PRIORITIZATION_SYSTEM_PROMPT


def test_multiple_chatter_messages_all_appear():
    msgs = [_msg("First message", 1), _msg("Second message", 3), _msg("Third message", 7)]
    lead = _base_lead(recent_messages=msgs)
    prompt = build_lead_prioritization_prompt(lead)
    assert "First message" in prompt
    assert "Second message" in prompt
    assert "Third message" in prompt
