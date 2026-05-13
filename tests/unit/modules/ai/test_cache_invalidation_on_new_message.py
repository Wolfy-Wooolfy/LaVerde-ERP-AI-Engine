"""Tests that the cache key changes when chatter content changes."""

from datetime import datetime, timezone

import pytest

from backend.shared.ai.cache import lead_cache_key
from backend.modules.ai.schemas import ChatterMessage, LeadContext


def _base_lead(messages: list[ChatterMessage] | None = None) -> LeadContext:
    return LeadContext(
        lead_id=42,
        name="Test Lead",
        stage_id=28,
        stage_name="Negotiation",
        create_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        recent_messages=messages or [],
    )


def _chatter_hash(lead: LeadContext) -> str:
    import hashlib
    return hashlib.md5(
        "".join(m.body_text for m in lead.recent_messages).encode()
    ).hexdigest()[:8]


def _key(lead: LeadContext) -> str:
    from backend.modules.ai.prioritizer import _completeness_score
    completeness = _completeness_score(lead)
    return lead_cache_key(
        lead.lead_id,
        lead.stage_id,
        lead.last_activity_date,
        completeness,
        _chatter_hash(lead),
    )


def _msg(text: str) -> ChatterMessage:
    return ChatterMessage(
        date=datetime(2026, 4, 1, tzinfo=timezone.utc),
        author="Sales",
        body_text=text,
        message_type="comment",
    )


def test_same_lead_no_chatter_gives_stable_key():
    lead_a = _base_lead()
    lead_b = _base_lead()
    assert _key(lead_a) == _key(lead_b)


def test_different_chatter_gives_different_key():
    lead_old = _base_lead([_msg("Old message")])
    lead_new = _base_lead([_msg("New message after site visit")])
    assert _key(lead_old) != _key(lead_new)


def test_same_chatter_gives_same_key():
    msg = _msg("Customer confirmed interest")
    lead_a = _base_lead([msg])
    lead_b = _base_lead([msg])
    assert _key(lead_a) == _key(lead_b)


def test_empty_chatter_differs_from_nonempty():
    lead_no_chat = _base_lead([])
    lead_with_chat = _base_lead([_msg("Called, no answer")])
    assert _key(lead_no_chat) != _key(lead_with_chat)


def test_adding_second_message_changes_key():
    lead_one = _base_lead([_msg("First message")])
    lead_two = _base_lead([_msg("First message"), _msg("Second message")])
    assert _key(lead_one) != _key(lead_two)


def test_chatter_hash_is_8_chars():
    lead = _base_lead([_msg("anything")])
    h = _chatter_hash(lead)
    assert len(h) == 8


def test_lead_cache_key_accepts_chatter_hash_param():
    k1 = lead_cache_key(1, 28, None, 3, "abc12345")
    k2 = lead_cache_key(1, 28, None, 3, "xyz98765")
    assert k1 != k2
    assert len(k1) == 32
    assert len(k2) == 32


def test_lead_cache_key_default_empty_chatter_hash():
    k_default = lead_cache_key(1, 28, None, 3)
    k_empty = lead_cache_key(1, 28, None, 3, "")
    assert k_default == k_empty
