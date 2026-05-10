"""
Unit tests for CrmService — OdooClient is fully mocked.
Verifies that business logic maps Odoo responses to correct Pydantic models.
"""

from unittest.mock import MagicMock

import pytest

from backend.core.cache import clear_cache, init_cache
from backend.modules.crm.service import CrmService


@pytest.fixture(autouse=True)
def fresh_cache() -> None:
    init_cache(ttl=60)
    clear_cache()


@pytest.fixture
def mock_client() -> MagicMock:
    return MagicMock()


# ── activity_summary ─────────────────────────────────────────────────────────


def test_activity_summary_maps_states(mock_client: MagicMock) -> None:
    mock_client.execute_kw.return_value = [
        {"activity_state": "overdue", "activity_state_count": 7},
        {"activity_state": "planned", "activity_state_count": 12},
        {"activity_state": "today", "activity_state_count": 3},
        {"activity_state": False, "activity_state_count": 20},
    ]
    svc = CrmService(client=mock_client)
    result = svc.activity_summary()
    assert result["overdue_followups"] == 7
    assert result["planned_followups"] == 12
    assert result["followups_today"] == 3
    assert result["no_activity_leads"] == 20


def test_activity_summary_empty_response(mock_client: MagicMock) -> None:
    mock_client.execute_kw.return_value = []
    svc = CrmService(client=mock_client)
    result = svc.activity_summary()
    assert result["overdue_followups"] == 0


# ── total_leads ───────────────────────────────────────────────────────────────


def test_total_leads(mock_client: MagicMock) -> None:
    mock_client.execute_kw.return_value = [{"__count": 42}]
    svc = CrmService(client=mock_client)
    assert svc.total_leads() == 42


def test_total_leads_empty(mock_client: MagicMock) -> None:
    mock_client.execute_kw.return_value = []
    svc = CrmService(client=mock_client)
    assert svc.total_leads() == 0


# ── overdue_by_salesperson ────────────────────────────────────────────────────


def test_overdue_by_salesperson_sorted_descending(mock_client: MagicMock) -> None:
    mock_client.execute_kw.return_value = [
        {"user_id": [10, "Ahmed"], "user_id_count": 3},
        {"user_id": [11, "Sara"], "user_id_count": 8},
        {"user_id": False, "user_id_count": 1},
    ]
    svc = CrmService(client=mock_client)
    result = svc.overdue_by_salesperson()
    assert result[0].overdue_count == 8
    assert result[0].salesperson_name == "Sara"
    assert result[-1].salesperson_name == "Unassigned"


# ── missing_contact_details ───────────────────────────────────────────────────


def test_missing_contact_details_maps_fields(mock_client: MagicMock) -> None:
    mock_client.execute_kw.return_value = [
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
    ]
    svc = CrmService(client=mock_client)
    rows = svc.missing_contact_details()
    assert len(rows) == 1
    row = rows[0]
    assert row.lead_id == 99
    assert row.salesperson_name == "Ahmed"
    assert row.source_name == "No Source"
    assert row.stage_name == "New Lead"


# ── summary caching ───────────────────────────────────────────────────────────


def test_summary_is_cached_on_second_call(mock_client: MagicMock) -> None:
    """Second call to summary() should not re-query Odoo."""
    mock_client.execute_kw.return_value = [{"__count": 0}]
    svc = CrmService(client=mock_client)

    first = svc.summary()
    call_count_after_first = mock_client.execute_kw.call_count

    second = svc.summary()
    # execute_kw should NOT have been called again
    assert mock_client.execute_kw.call_count == call_count_after_first
    assert first is second  # same object from cache
