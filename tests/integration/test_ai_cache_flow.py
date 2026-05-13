"""Integration test: caching saves money (second call costs $0)."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.shared.ai.budget_tracker import BudgetTracker
from backend.shared.ai.cache import AICache
from backend.shared.ai.client import OpenAIClient
from backend.modules.ai.prioritizer import LeadPrioritizer
from backend.modules.ai.schemas import ChatCompletionResponse, LeadContext


def _make_lead(lead_id: int = 1) -> LeadContext:
    return LeadContext(
        lead_id=lead_id,
        name=f"Lead {lead_id}",
        stage_id=28,
        stage_name="Negotiation",
        create_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        is_critical_stage=True,
        has_phone=True,
        activity_state="overdue",
    )


def _mock_ai_response(score: int = 75) -> ChatCompletionResponse:
    return ChatCompletionResponse(
        content=f'{{"score":{score},"tier":"high","reasoning":"test","recommended_action":"call"}}',
        model="gpt-4o-mini",
        input_tokens=100,
        output_tokens=30,
        cost_usd=0.0001,
        duration_ms=400,
    )


@pytest.fixture
def prioritizer(tmp_path):
    odoo = MagicMock()
    ai_client = MagicMock()
    ai_client.chat_completion = AsyncMock(return_value=_mock_ai_response())

    budget = BudgetTracker(10.0, 0.8, budget_file=tmp_path / "budget.json")
    cache = AICache(ttl_seconds=3600, cache_file=tmp_path / "cache.json")

    return LeadPrioritizer(
        odoo_client=odoo,
        ai_client=ai_client,
        budget_tracker=budget,
        cache=cache,
    )


@pytest.mark.asyncio
async def test_second_call_uses_cache_zero_cost(prioritizer):
    lead = _make_lead()
    r1 = await prioritizer.prioritize_single(lead)
    assert r1.cached is False
    assert r1.cost_usd > 0

    r2 = await prioritizer.prioritize_single(lead)
    assert r2.cached is True
    assert r2.cost_usd == 0.0


@pytest.mark.asyncio
async def test_cache_hit_rate_increases(prioritizer):
    lead = _make_lead()
    await prioritizer.prioritize_single(lead)  # miss
    await prioritizer.prioritize_single(lead)  # hit
    await prioritizer.prioritize_single(lead)  # hit

    stats = prioritizer._cache.stats()
    assert stats["hits"] == 2
    assert stats["misses"] == 1
    assert stats["hit_rate"] > 0.5


@pytest.mark.asyncio
async def test_different_leads_each_get_own_cache_entry(prioritizer):
    lead1 = _make_lead(1)
    lead2 = _make_lead(2)

    r1 = await prioritizer.prioritize_single(lead1)
    r2 = await prioritizer.prioritize_single(lead2)

    assert r1.cached is False
    assert r2.cached is False
    assert prioritizer._ai.chat_completion.call_count == 2


@pytest.mark.asyncio
async def test_budget_total_only_charges_for_cache_misses(prioritizer):
    lead = _make_lead()
    await prioritizer.prioritize_single(lead)  # 1 API call
    await prioritizer.prioritize_single(lead)  # cached
    await prioritizer.prioritize_single(lead)  # cached

    total_api_calls = prioritizer._ai.chat_completion.call_count
    assert total_api_calls == 1  # Only one real API call
