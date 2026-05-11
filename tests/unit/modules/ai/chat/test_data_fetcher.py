"""Unit tests for data_fetcher (Stage 2a)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.modules.ai.chat.data_fetcher import _normalise_stage, fetch_data_for_intent
from backend.modules.crm.schemas import (
    DataQuality,
    OverdueBySalesperson,
    OverdueByStage,
    OverdueByTeam,
)


@pytest.fixture
def mock_crm():
    crm = MagicMock()
    crm.overdue_by_salesperson = AsyncMock(
        return_value=[
            OverdueBySalesperson(salesperson_id=1, salesperson_name="Ahmed Ali", overdue_count=10),
            OverdueBySalesperson(salesperson_id=2, salesperson_name="Sara Mohamed", overdue_count=5),
        ]
    )
    crm.overdue_by_team = AsyncMock(
        return_value=[
            OverdueByTeam(team_id=1, team_name="Team Alpha", overdue_count=15),
            OverdueByTeam(team_id=2, team_name="Team Beta", overdue_count=8),
        ]
    )
    crm.overdue_by_stage = AsyncMock(
        return_value=[
            OverdueByStage(stage_id=28, stage_name="Negotiation", overdue_count=20),
            OverdueByStage(stage_id=34, stage_name="Site Visit", overdue_count=7),
        ]
    )
    crm.data_quality_summary = AsyncMock(
        return_value=DataQuality(
            new_x_count=5,
            missing_stage_count=2,
            missing_contact_count=20,
            missing_salesperson_count=3,
            total_data_quality_issues=30,
        )
    )
    return crm


async def test_list_overdue_by_salesperson(mock_crm):
    data = await fetch_data_for_intent("list_overdue_by_salesperson", {"limit": 10}, mock_crm)
    assert data["type"] == "salesperson_overdue_list"
    assert data["total"] == 2
    assert data["rows"][0]["salesperson_name"] == "Ahmed Ali"


async def test_list_overdue_by_salesperson_with_filter(mock_crm):
    data = await fetch_data_for_intent(
        "list_overdue_by_salesperson", {"salesperson": "ahmed"}, mock_crm
    )
    assert data["total"] == 1
    assert "Ahmed" in data["rows"][0]["salesperson_name"]


async def test_list_overdue_by_team(mock_crm):
    data = await fetch_data_for_intent("list_overdue_by_team", {}, mock_crm)
    assert data["type"] == "team_overdue_list"
    assert data["total"] == 2


async def test_list_overdue_by_stage(mock_crm):
    data = await fetch_data_for_intent("list_overdue_by_stage", {}, mock_crm)
    assert data["type"] == "stage_overdue_list"
    assert data["total"] == 2


async def test_count_by_stage_all(mock_crm):
    data = await fetch_data_for_intent("count_by_stage", {}, mock_crm)
    assert data["type"] == "count"
    assert data["count"] == 27  # 20 + 7


async def test_count_by_stage_filtered(mock_crm):
    data = await fetch_data_for_intent("count_by_stage", {"stage": "Negotiation"}, mock_crm)
    assert data["count"] == 20
    assert data["label"] == "Negotiation"


async def test_count_by_team(mock_crm):
    data = await fetch_data_for_intent("count_by_team", {}, mock_crm)
    assert data["type"] == "count"
    assert data["count"] == 23  # 15 + 8


async def test_count_by_salesperson(mock_crm):
    data = await fetch_data_for_intent("count_by_salesperson", {"salesperson": "sara"}, mock_crm)
    assert data["count"] == 5


async def test_missing_contact_summary(mock_crm):
    data = await fetch_data_for_intent("missing_contact_summary", {}, mock_crm)
    assert data["type"] == "data_quality"
    assert data["missing_contact_count"] == 20


async def test_data_quality_summary(mock_crm):
    data = await fetch_data_for_intent("data_quality_summary", {}, mock_crm)
    assert data["type"] == "data_quality_full"
    assert data["missing_contact_count"] == 20
    assert data["total_data_quality_issues"] == 30


async def test_team_performance_summary(mock_crm):
    data = await fetch_data_for_intent("team_performance_summary", {}, mock_crm)
    assert data["type"] == "team_performance"
    assert data["total_overdue"] == 23


async def test_salesperson_performance_summary(mock_crm):
    data = await fetch_data_for_intent("salesperson_performance_summary", {}, mock_crm)
    assert data["type"] == "salesperson_performance"
    assert data["total_overdue"] == 15


async def test_unknown_intent_returns_clarification(mock_crm):
    data = await fetch_data_for_intent("not_a_real_intent", {}, mock_crm)
    assert data["type"] == "clarification_needed"


async def test_site_visit_signal_no_prioritizer(mock_crm):
    data = await fetch_data_for_intent("leads_with_site_visit_signal", {}, mock_crm, prioritizer=None)
    assert data["type"] == "unavailable"


async def test_recommendation_no_prioritizer(mock_crm):
    data = await fetch_data_for_intent("recommendation_top_priority", {}, mock_crm, prioritizer=None)
    assert data["type"] == "unavailable"


async def test_limit_respected(mock_crm):
    data = await fetch_data_for_intent("list_overdue_by_salesperson", {"limit": 1}, mock_crm)
    assert data["total"] == 1


# ── Bug 3: Stage name normalisation ──────────────────────────────────────────


def test_normalise_stage_english_passthrough():
    assert _normalise_stage("Negotiation") == "Negotiation"
    assert _normalise_stage("Reservation") == "Reservation"


def test_normalise_stage_arabic_to_english():
    assert _normalise_stage("التفاوض") == "Negotiation"
    assert _normalise_stage("تفاوض") == "Negotiation"
    assert _normalise_stage("الحجز") == "Reservation"
    assert _normalise_stage("حجز") == "Reservation"
    assert _normalise_stage("متابعة") == "Follow up"
    assert _normalise_stage("معاينة") == "Site Visit"


def test_normalise_stage_english_alias_case_insensitive():
    assert _normalise_stage("follow up") == "Follow up"
    assert _normalise_stage("NEGOTIATION") == "Negotiation"
    assert _normalise_stage("site visit") == "Site Visit"


def test_normalise_stage_unknown_passthrough():
    assert _normalise_stage("Some Custom Stage") == "Some Custom Stage"


async def test_count_by_stage_arabic_stage_name(mock_crm):
    """'التفاوض' (Arabic for Negotiation) must resolve to the Negotiation stage."""
    data = await fetch_data_for_intent("count_by_stage", {"stage": "التفاوض"}, mock_crm)
    assert data["count"] == 20
    assert data["label"] == "Negotiation"


async def test_count_by_stage_mixed_language_english_term(mock_crm):
    """'Negotiation' in an Arabic question context must still work."""
    data = await fetch_data_for_intent("count_by_stage", {"stage": "Negotiation"}, mock_crm)
    assert data["count"] == 20
    assert data["label"] == "Negotiation"


async def test_count_by_stage_follow_up_alias(mock_crm):
    """'متابعة' should map to 'Follow up' stage — but mock only has Negotiation/Site Visit."""
    data = await fetch_data_for_intent("count_by_stage", {"stage": "متابعة"}, mock_crm)
    # No matching stage in mock → count is 0, but normalisation ran without error
    assert data["type"] == "count"
    assert data["label"] == "Follow up"
