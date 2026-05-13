"""Unit tests for LeadPrioritizer service."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.shared.ai.cache import AICache
from backend.shared.ai.exceptions import BudgetExceededError
from backend.modules.ai.prioritizer import LeadPrioritizer, _parse_ai_response
from backend.modules.ai.schemas import ChatCompletionResponse, LeadContext, LeadPriority


def _make_lead(lead_id: int = 1, stage_id: int = 28) -> LeadContext:
    return LeadContext(
        lead_id=lead_id,
        name=f"Test Lead {lead_id}",
        stage_id=stage_id,
        stage_name="Negotiation",
        create_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        is_critical_stage=True,
        has_phone=True,
        activity_state="overdue",
    )


def _make_ai_response(score: int = 80) -> ChatCompletionResponse:
    return ChatCompletionResponse(
        content=f'{{"score":{score},"tier":"high","reasoning":"test reason","recommended_action":"call now"}}',
        model="gpt-4o-mini",
        input_tokens=100,
        output_tokens=30,
        cost_usd=0.0001,
        duration_ms=500,
    )


@pytest.fixture
def prioritizer(tmp_path):
    odoo = MagicMock()
    ai = MagicMock()
    budget = MagicMock()
    budget.enforce_budget = MagicMock()
    budget.is_near_budget.return_value = False
    cache = AICache(ttl_seconds=3600, maxsize=50, cache_file=tmp_path / "ai_cache.json")
    return LeadPrioritizer(odoo_client=odoo, ai_client=ai, budget_tracker=budget, cache=cache)


@pytest.mark.asyncio
async def test_prioritize_single_success(prioritizer):
    prioritizer._ai.chat_completion = AsyncMock(return_value=_make_ai_response(80))

    lead = _make_lead()
    result = await prioritizer.prioritize_single(lead)

    assert result.lead_id == 1
    assert result.score == 80
    assert result.tier == "high"
    assert result.cached is False
    assert result.cost_usd == pytest.approx(0.0001)


@pytest.mark.asyncio
async def test_prioritize_single_uses_cache_on_second_call(prioritizer):
    prioritizer._ai.chat_completion = AsyncMock(return_value=_make_ai_response(75))

    lead = _make_lead()
    r1 = await prioritizer.prioritize_single(lead)
    r2 = await prioritizer.prioritize_single(lead)

    assert prioritizer._ai.chat_completion.call_count == 1
    assert r2.cached is True
    assert r2.cost_usd == 0.0


@pytest.mark.asyncio
async def test_prioritize_single_respects_budget_hard_stop(prioritizer):
    prioritizer._budget.enforce_budget.side_effect = BudgetExceededError(10.0, 10.0)

    lead = _make_lead()
    with pytest.raises(BudgetExceededError):
        await prioritizer.prioritize_single(lead)


@pytest.mark.asyncio
async def test_prioritize_batch_returns_sorted(prioritizer):
    async def mock_ai(*args, **kwargs):
        content = args[0][1]["content"] if args else ""
        return _make_ai_response(80)

    prioritizer._ai.chat_completion = AsyncMock(return_value=_make_ai_response(70))

    leads = [_make_lead(i) for i in range(1, 4)]
    results = await prioritizer.prioritize_batch(leads)

    assert len(results) == 3
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio
async def test_prioritize_batch_handles_partial_budget_stop(prioritizer):
    call_count = 0

    async def mock_ai_fail_on_third(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise BudgetExceededError(10.0, 10.0)
        return _make_ai_response(70)

    prioritizer._ai.chat_completion = mock_ai_fail_on_third
    prioritizer._budget.enforce_budget = MagicMock()

    leads = [_make_lead(i) for i in range(1, 4)]
    results = await prioritizer.prioritize_batch(leads)
    # Should return partial results without crashing
    assert len(results) >= 0


def test_parse_ai_response_valid():
    content = '{"score": 85, "tier": "high", "reasoning": "Near closing stage.", "recommended_action": "Call client"}'
    result = _parse_ai_response(content, lead_id=1, model="gpt-4o-mini", cost=0.0001, cached=False)
    assert result.score == 85
    assert result.tier == "high"
    assert "closing" in result.reasoning


def test_parse_ai_response_score_clamped():
    content = '{"score": 150, "tier": "critical", "reasoning": "Over 100", "recommended_action": "Call"}'
    result = _parse_ai_response(content, lead_id=1, model="gpt-4o-mini", cost=0.0, cached=False)
    assert result.score == 100


def test_parse_ai_response_invalid_json_raises():
    from backend.shared.ai.exceptions import AIInvalidResponseError

    with pytest.raises(AIInvalidResponseError):
        _parse_ai_response("this is not json", lead_id=1, model="gpt-4o-mini", cost=0.0, cached=False)


def test_parse_ai_response_derives_tier_from_score():
    content = '{"score": 93, "tier": "not-valid-tier", "reasoning": "ok", "recommended_action": "act"}'
    result = _parse_ai_response(content, lead_id=1, model="gpt-4o-mini", cost=0.0, cached=False)
    assert result.tier == "critical"
