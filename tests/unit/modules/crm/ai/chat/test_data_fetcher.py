"""Unit tests for data_fetcher (Stage 2a)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.modules.crm.ai.chat.data_fetcher import _normalise_stage, fetch_data_for_intent
from backend.modules.crm.schemas import (
    DataQuality,
    OverdueBySalesperson,
    OverdueByStage,
    OverdueByTeam,
    StageCountResult,
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
    crm.count_leads_by_stage = AsyncMock(
        return_value=StageCountResult(
            stage_name="Negotiation",
            matched_stages=[{"id": 28, "name": "Negotiation"}],
            count=120,
            overdue_only=False,
        )
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


async def test_count_by_stage_no_filter_returns_clarification(mock_crm):
    """No stage filter → clarification_needed (handler requires a stage name)."""
    data = await fetch_data_for_intent("count_by_stage", {}, mock_crm)
    assert data["type"] == "clarification_needed"


async def test_count_by_stage_returns_stage_count_type(mock_crm):
    """Handler returns stage_count type with correct fields."""
    data = await fetch_data_for_intent("count_by_stage", {"stage": "Negotiation"}, mock_crm)
    assert data["type"] == "stage_count"
    assert data["stage_name"] == "Negotiation"
    assert data["count"] == 120
    assert data["overdue_only"] is False


async def test_count_by_stage_not_found(mock_crm):
    """Unknown stage name → stage_not_found response."""
    mock_crm.count_leads_by_stage.return_value = StageCountResult(
        stage_name="Nonexistent",
        matched_stages=[],
        count=0,
        overdue_only=False,
    )
    data = await fetch_data_for_intent("count_by_stage", {"stage": "Nonexistent"}, mock_crm)
    assert data["type"] == "stage_not_found"
    assert data["requested_stage"] == "Nonexistent"


async def test_count_by_stage_does_not_match_new_x(mock_crm):
    """'New' must NOT match 'New X' — exact match enforced in service layer."""
    mock_crm.count_leads_by_stage.return_value = StageCountResult(
        stage_name="New",
        matched_stages=[{"id": 24, "name": "New"}],
        count=97,
        overdue_only=False,
    )
    data = await fetch_data_for_intent("count_by_stage", {"stage": "New"}, mock_crm)
    assert data["type"] == "stage_count"
    assert data["count"] == 97
    # Verify the service was called with the normalised name, not a substring
    mock_crm.count_leads_by_stage.assert_called_once_with(stage_name="New", overdue_only=False)


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
    """'التفاوض' (Arabic for Negotiation) normalises to 'Negotiation' before service call."""
    data = await fetch_data_for_intent("count_by_stage", {"stage": "التفاوض"}, mock_crm)
    assert data["type"] == "stage_count"
    assert data["stage_name"] == "Negotiation"
    mock_crm.count_leads_by_stage.assert_called_once_with(stage_name="Negotiation", overdue_only=False)


async def test_count_by_stage_mixed_language_english_term(mock_crm):
    """'Negotiation' in an Arabic question context must still work."""
    data = await fetch_data_for_intent("count_by_stage", {"stage": "Negotiation"}, mock_crm)
    assert data["type"] == "stage_count"
    assert data["count"] == 120


async def test_count_by_stage_follow_up_alias(mock_crm):
    """'متابعة' normalises to 'Follow up'; if stage not found returns stage_not_found."""
    mock_crm.count_leads_by_stage.return_value = StageCountResult(
        stage_name="Follow up",
        matched_stages=[],
        count=0,
        overdue_only=False,
    )
    data = await fetch_data_for_intent("count_by_stage", {"stage": "متابعة"}, mock_crm)
    assert data["type"] == "stage_not_found"
    mock_crm.count_leads_by_stage.assert_called_once_with(stage_name="Follow up", overdue_only=False)


async def test_count_overdue_by_stage(mock_crm):
    """count_overdue_by_stage intent forces overdue_only=True."""
    mock_crm.count_leads_by_stage.return_value = StageCountResult(
        stage_name="New",
        matched_stages=[{"id": 24, "name": "New"}],
        count=1,
        overdue_only=True,
    )
    data = await fetch_data_for_intent("count_overdue_by_stage", {"stage": "New"}, mock_crm)
    assert data["type"] == "stage_count"
    assert data["count"] == 1
    assert data["overdue_only"] is True
    mock_crm.count_leads_by_stage.assert_called_once_with(stage_name="New", overdue_only=True)
