"""Integration test: budget hard stop end-to-end flow."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.shared.ai.budget_tracker import BudgetTracker
from backend.shared.ai.cache import AICache
from backend.shared.ai.exceptions import BudgetExceededError
from backend.modules.ai.prioritizer import LeadPrioritizer
from backend.main import app

AUTH = ("testadmin", "testpass")


@pytest.fixture
def exhausted_tracker(tmp_path):
    """A tracker already at budget."""
    t = BudgetTracker(
        monthly_budget_usd=0.01,
        warning_threshold=0.8,
        hard_stop=True,
        budget_file=tmp_path / "budget_test.json",
    )
    t.record_spend(0.02, "gpt-4o-mini")  # over budget
    return t


def test_budget_hard_stop_blocks_ai_calls(exhausted_tracker):
    """Enforce budget raises BudgetExceededError when over budget."""
    with pytest.raises(BudgetExceededError) as exc_info:
        exhausted_tracker.enforce_budget()
    assert exc_info.value.spent >= 0.02
    assert exc_info.value.budget == 0.01


def test_budget_status_reflects_exhaustion(exhausted_tracker):
    status = exhausted_tracker.get_status()
    assert status["is_over_budget"] is True
    assert status["remaining_budget_usd"] == 0.0
    assert status["percentage_used"] > 100


def test_budget_endpoint_shows_exhausted_state(tmp_path):
    tracker = BudgetTracker(0.01, 0.8, budget_file=tmp_path / "b.json")
    tracker.record_spend(0.02, "gpt-4o-mini")

    with TestClient(app) as client:
        app.state.ai_budget_tracker = tracker
        app.state.ai_prioritizer = MagicMock()
        resp = client.get("/api/v1/ai/budget", auth=AUTH)

    assert resp.status_code == 200
    data = resp.json()
    assert data["is_over_budget"] is True


def test_prioritize_overdue_returns_402_when_budget_exhausted(tmp_path):
    tracker = BudgetTracker(0.01, 0.8, budget_file=tmp_path / "b2.json")
    tracker.record_spend(0.02, "gpt-4o-mini")

    mock_prioritizer = MagicMock()
    mock_prioritizer.prioritize_overdue = AsyncMock(
        side_effect=BudgetExceededError(0.02, 0.01)
    )

    with TestClient(app) as client:
        app.state.ai_budget_tracker = tracker
        app.state.ai_prioritizer = mock_prioritizer
        resp = client.post("/api/v1/ai/prioritize-overdue", json={"limit": 10}, auth=AUTH)

    assert resp.status_code == 402
    data = resp.json()
    detail = data.get("detail") or data
    error = detail.get("error") if isinstance(detail, dict) else {}
    assert error.get("code") == "AI_BUDGET_EXCEEDED"


def test_ai_health_shows_degraded_when_budget_exhausted(tmp_path):
    tracker = BudgetTracker(0.01, 0.8, budget_file=tmp_path / "b3.json")
    tracker.record_spend(0.02, "gpt-4o-mini")

    with TestClient(app) as client:
        app.state.ai_budget_tracker = tracker
        app.state.ai_prioritizer = MagicMock()
        resp = client.get("/api/v1/ai/health", auth=AUTH)

    assert resp.status_code == 200
    data = resp.json()
    assert data["budget_ok"] is False
    assert data["status"] == "degraded"
