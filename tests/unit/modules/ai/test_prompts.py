"""Unit tests for prompt builders."""

from datetime import datetime, timezone

import pytest

from backend.modules.ai.prompts import (
    LEAD_PRIORITIZATION_SYSTEM_PROMPT,
    build_lead_prioritization_prompt,
)
from backend.modules.ai.schemas import LeadContext


def _make_lead(**overrides) -> LeadContext:
    defaults = dict(
        lead_id=42,
        name="Ahmed Hassan",
        stage_id=28,
        stage_name="Negotiation",
        salesperson_name="Khaled",
        team_name="Team Alpha",
        create_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        last_activity_date=datetime(2026, 4, 15, tzinfo=timezone.utc),
        days_in_stage=45,
        is_critical_stage=True,
        has_phone=True,
        has_mobile=True,
        has_email=False,
        activity_state="overdue",
    )
    defaults.update(overrides)
    return LeadContext(**defaults)


def test_system_prompt_not_empty():
    assert len(LEAD_PRIORITIZATION_SYSTEM_PROMPT) > 100


def test_system_prompt_contains_json_instruction():
    assert "JSON" in LEAD_PRIORITIZATION_SYSTEM_PROMPT


def test_system_prompt_mentions_real_estate():
    assert "real estate" in LEAD_PRIORITIZATION_SYSTEM_PROMPT.lower()


def test_build_prompt_contains_lead_id():
    lead = _make_lead()
    prompt = build_lead_prioritization_prompt(lead)
    assert "42" in prompt


def test_build_prompt_contains_stage_name():
    lead = _make_lead()
    prompt = build_lead_prioritization_prompt(lead)
    assert "Negotiation" in prompt


def test_build_prompt_contains_salesperson():
    lead = _make_lead()
    prompt = build_lead_prioritization_prompt(lead)
    assert "Khaled" in prompt


def test_build_prompt_no_last_activity_shows_na():
    lead = _make_lead(last_activity_date=None)
    prompt = build_lead_prioritization_prompt(lead)
    assert "N/A" in prompt


def test_build_prompt_contact_info_listed():
    lead = _make_lead(has_phone=True, has_mobile=True, has_email=False)
    prompt = build_lead_prioritization_prompt(lead)
    assert "phone" in prompt
    assert "mobile" in prompt


def test_build_prompt_no_contact_shows_none():
    lead = _make_lead(has_phone=False, has_mobile=False, has_email=False)
    prompt = build_lead_prioritization_prompt(lead)
    assert "none" in prompt


def test_build_prompt_unassigned_salesperson():
    lead = _make_lead(salesperson_name=None, team_name=None)
    prompt = build_lead_prioritization_prompt(lead)
    assert "Unassigned" in prompt
