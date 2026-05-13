"""Additional tests for LeadPrioritizer._fetch_overdue_leads and prioritize_overdue."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.shared.ai.cache import AICache
from backend.modules.ai.prioritizer import LeadPrioritizer, _parse_ai_response, _completeness_score
from backend.modules.ai.schemas import ChatCompletionResponse, LeadContext


def _good_ai_response() -> ChatCompletionResponse:
    return ChatCompletionResponse(
        content='{"score":70,"tier":"high","reasoning":"test","recommended_action":"call"}',
        model="gpt-4o-mini",
        input_tokens=80,
        output_tokens=30,
        cost_usd=0.00008,
        duration_ms=300,
    )


@pytest.fixture
def prioritizer(tmp_path):
    odoo = MagicMock()
    odoo.execute_kw = AsyncMock(return_value=[])
    odoo.fetch_recent_messages = AsyncMock(return_value=[])
    ai = MagicMock()
    ai.chat_completion = AsyncMock(return_value=_good_ai_response())
    budget = MagicMock()
    budget.enforce_budget = MagicMock()
    budget.is_near_budget.return_value = False
    cache = AICache(ttl_seconds=3600, cache_file=tmp_path / "c.json")
    return LeadPrioritizer(odoo_client=odoo, ai_client=ai, budget_tracker=budget, cache=cache)


ODOO_ROW = {
    "id": 101,
    "name": "Test Lead",
    "stage_id": [28, "Negotiation"],
    "user_id": [5, "Khaled"],
    "team_id": [3, "Alpha"],
    "create_date": "2026-01-01 10:00:00",
    "activity_date_deadline": "2026-04-15",
    "phone": "01234567890",
    "mobile": False,
    "email_from": "test@test.com",
    "activity_state": "overdue",
}


@pytest.mark.asyncio
async def test_fetch_overdue_leads_maps_fields(prioritizer):
    prioritizer._odoo.execute_kw = AsyncMock(return_value=[ODOO_ROW])
    leads = await prioritizer._fetch_overdue_leads(10)
    assert len(leads) == 1
    lead = leads[0]
    assert lead.lead_id == 101
    assert lead.stage_name == "Negotiation"
    assert lead.salesperson_name == "Khaled"
    assert lead.team_name == "Alpha"
    assert lead.has_phone is True
    assert lead.has_email is True
    assert lead.has_mobile is False
    assert lead.activity_state == "overdue"
    assert lead.is_critical_stage is True  # stage 28 is in critical list


@pytest.mark.asyncio
async def test_fetch_overdue_leads_no_stage(prioritizer):
    row = {**ODOO_ROW, "stage_id": False, "user_id": False, "team_id": False}
    prioritizer._odoo.execute_kw = AsyncMock(return_value=[row])
    leads = await prioritizer._fetch_overdue_leads(10)
    assert leads[0].stage_name == "No Stage"
    assert leads[0].salesperson_name is None
    assert leads[0].team_name is None


@pytest.mark.asyncio
async def test_fetch_overdue_leads_odoo_error_returns_empty(prioritizer):
    prioritizer._odoo.execute_kw = AsyncMock(side_effect=Exception("Odoo down"))
    leads = await prioritizer._fetch_overdue_leads(10)
    assert leads == []


@pytest.mark.asyncio
async def test_fetch_overdue_leads_empty_returns_empty(prioritizer):
    prioritizer._odoo.execute_kw = AsyncMock(return_value=[])
    leads = await prioritizer._fetch_overdue_leads(10)
    assert leads == []


@pytest.mark.asyncio
async def test_prioritize_overdue_uses_list_cache_on_second_call(prioritizer):
    prioritizer._odoo.execute_kw = AsyncMock(return_value=[ODOO_ROW])
    r1 = await prioritizer.prioritize_overdue(limit=10)
    r2 = await prioritizer.prioritize_overdue(limit=10)
    # Odoo was only called once
    assert prioritizer._odoo.execute_kw.call_count == 1
    assert r1 == r2


@pytest.mark.asyncio
async def test_prioritize_overdue_returns_sorted_results(prioritizer):
    rows = [
        {**ODOO_ROW, "id": i, "name": f"Lead {i}"}
        for i in range(1, 6)
    ]
    prioritizer._odoo.execute_kw = AsyncMock(return_value=rows)
    results = await prioritizer.prioritize_overdue(limit=5)
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


def test_completeness_score_full():
    lead = LeadContext(
        lead_id=1, name="X", stage_id=1, stage_name="S",
        create_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        has_phone=True, has_mobile=True, has_email=True,
        salesperson_name="K", team_name="T",
    )
    assert _completeness_score(lead) == 5


def test_completeness_score_empty():
    lead = LeadContext(
        lead_id=1, name="X", stage_id=1, stage_name="S",
        create_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert _completeness_score(lead) == 0


def test_parse_response_tier_mapping_dead():
    content = '{"score": 5, "tier": "bad-tier", "reasoning": "ok", "recommended_action": "nothing"}'
    result = _parse_ai_response(content, 1, "gpt-4o-mini", 0.0, False)
    assert result.tier == "dead"


def test_parse_response_tier_mapping_medium():
    content = '{"score": 55, "tier": "INVALID", "reasoning": "ok", "recommended_action": "call"}'
    result = _parse_ai_response(content, 1, "gpt-4o-mini", 0.0, False)
    assert result.tier == "medium"


def test_parse_response_score_negative_clamped():
    content = '{"score": -10, "tier": "dead", "reasoning": "ok", "recommended_action": "nothing"}'
    result = _parse_ai_response(content, 1, "gpt-4o-mini", 0.0, False)
    assert result.score == 0
