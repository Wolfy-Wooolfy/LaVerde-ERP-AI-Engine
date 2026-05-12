"""
Unit tests for CrmService — async, OdooClient fully mocked.
Verifies business logic, Pydantic model mapping, and caching.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.core.cache import clear_cache, init_cache
from backend.modules.crm.service import CrmService


@pytest.fixture(autouse=True)
def fresh_cache() -> None:
    init_cache(ttl=60)
    clear_cache()


@pytest.fixture
def mock_client() -> MagicMock:
    client = MagicMock()
    client.execute_kw = AsyncMock()
    return client


# ── activity_summary ─────────────────────────────────────────────────────────


async def test_activity_summary_maps_states(mock_client: MagicMock) -> None:
    mock_client.execute_kw.return_value = [
        {"activity_state": "overdue", "activity_state_count": 7},
        {"activity_state": "planned", "activity_state_count": 12},
        {"activity_state": "today", "activity_state_count": 3},
        {"activity_state": False, "activity_state_count": 20},
    ]
    svc = CrmService(client=mock_client)
    result = await svc.activity_summary()
    assert result["overdue_followups"] == 7
    assert result["planned_followups"] == 12
    assert result["followups_today"] == 3
    assert result["no_activity_leads"] == 20


async def test_activity_summary_empty_response(mock_client: MagicMock) -> None:
    mock_client.execute_kw.return_value = []
    svc = CrmService(client=mock_client)
    result = await svc.activity_summary()
    assert result["overdue_followups"] == 0


# ── total_leads ───────────────────────────────────────────────────────────────


async def test_total_leads(mock_client: MagicMock) -> None:
    mock_client.execute_kw.return_value = [{"__count": 42}]
    svc = CrmService(client=mock_client)
    assert await svc.total_leads() == 42


async def test_total_leads_empty(mock_client: MagicMock) -> None:
    mock_client.execute_kw.return_value = []
    svc = CrmService(client=mock_client)
    assert await svc.total_leads() == 0


# ── overdue_by_salesperson ────────────────────────────────────────────────────


async def test_overdue_by_salesperson_sorted_descending(mock_client: MagicMock) -> None:
    mock_client.execute_kw.return_value = [
        {"user_id": [10, "Ahmed"], "user_id_count": 3},
        {"user_id": [11, "Sara"], "user_id_count": 8},
        {"user_id": False, "user_id_count": 1},
    ]
    svc = CrmService(client=mock_client)
    result = await svc.overdue_by_salesperson()
    assert result[0].overdue_count == 8
    assert result[0].salesperson_name == "Sara"
    assert result[-1].salesperson_name == "Unassigned"


# ── missing_contact_details ───────────────────────────────────────────────────


async def test_missing_contact_details_maps_fields(mock_client: MagicMock) -> None:
    mock_client.execute_kw.side_effect = [
        # search_read result
        [
            {
                "id": 99,
                "name": "Test Opp",
                "contact_name": "John",
                "user_id": [10, "Ahmed"],
                "team_id": [1, "Alpha"],
                "stage_id": [28, "New Lead"],
                "source_id": False,
                "create_date": "2025-01-01 00:00:00",
            }
        ],
        # read_group count
        [{"__count": 1}],
    ]
    svc = CrmService(client=mock_client)
    rows, total = await svc.missing_contact_details()
    assert len(rows) == 1
    assert total == 1
    row = rows[0]
    assert row.lead_id == 99
    assert row.salesperson_name == "Ahmed"
    assert row.source_name == "No Source"
    assert row.stage_name == "New Lead"


# ── summary caching ───────────────────────────────────────────────────────────


async def test_summary_is_cached_on_second_call(mock_client: MagicMock) -> None:
    """Second call to summary() must not re-query Odoo."""
    mock_client.execute_kw.return_value = [{"__count": 0}]
    svc = CrmService(client=mock_client)

    first = await svc.summary()
    call_count_after_first = mock_client.execute_kw.call_count

    second = await svc.summary()
    assert mock_client.execute_kw.call_count == call_count_after_first
    assert first is second


async def test_summary_fires_parallel_odoo_calls(mock_client: MagicMock) -> None:
    """Verify summary makes multiple Odoo calls (parallel gather)."""
    mock_client.execute_kw.return_value = [{"__count": 0}]
    svc = CrmService(client=mock_client)

    await svc.summary()
    # summary fires activity + dq(4) + total + critical + 3 overdue + matrix = 11 calls
    assert mock_client.execute_kw.call_count >= 8


# ── count_leads_by_stage ──────────────────────────────────────────────────────


async def test_count_leads_by_stage_exact_match(mock_client: MagicMock) -> None:
    """'New' must NOT match 'New X' — strict exact match only."""
    mock_client.execute_kw.side_effect = [
        # search_read on crm.stage
        [{"id": 24, "name": "New"}, {"id": 44, "name": "New X"}],
        # read_group count for stage_id=24 only
        [{"__count": 97}],
    ]
    svc = CrmService(client=mock_client)
    result = await svc.count_leads_by_stage("New")
    assert result.count == 97
    assert len(result.matched_stages) == 1
    assert result.matched_stages[0]["id"] == 24
    assert result.matched_stages[0]["name"] == "New"


async def test_count_leads_by_stage_case_insensitive(mock_client: MagicMock) -> None:
    """'new' (lowercase) must match 'New' (capitalised)."""
    mock_client.execute_kw.side_effect = [
        [{"id": 24, "name": "New"}, {"id": 44, "name": "New X"}],
        [{"__count": 97}],
    ]
    svc = CrmService(client=mock_client)
    result = await svc.count_leads_by_stage("new")
    assert result.count == 97
    assert result.stage_name == "New"  # canonical name from Odoo


async def test_count_leads_by_stage_overdue_filter(mock_client: MagicMock) -> None:
    """overdue_only=True applies the activity_state=overdue filter."""
    mock_client.execute_kw.side_effect = [
        [{"id": 24, "name": "New"}],
        [{"__count": 1}],
    ]
    svc = CrmService(client=mock_client)
    result = await svc.count_leads_by_stage("New", overdue_only=True)
    assert result.count == 1
    assert result.overdue_only is True
    # Confirm activity_state filter was included in the domain
    call_args = mock_client.execute_kw.call_args_list[1]
    # execute_kw(model, method, args=...) — domain is args kwarg index 0
    domain_arg = call_args.kwargs["args"][0]
    assert any(
        c == ["activity_state", "=", "overdue"] for c in domain_arg
    ), f"overdue filter missing from domain: {domain_arg}"


async def test_count_leads_by_stage_no_match_returns_zero(mock_client: MagicMock) -> None:
    """Unknown stage name returns count=0 with empty matched_stages."""
    mock_client.execute_kw.return_value = [
        {"id": 24, "name": "New"},
        {"id": 27, "name": "Follow up"},
    ]
    svc = CrmService(client=mock_client)
    result = await svc.count_leads_by_stage("Nonexistent Stage")
    assert result.count == 0
    assert result.matched_stages == []
    # Must not make a second Odoo call (no stage matched — no lead query needed)
    assert mock_client.execute_kw.call_count == 1
