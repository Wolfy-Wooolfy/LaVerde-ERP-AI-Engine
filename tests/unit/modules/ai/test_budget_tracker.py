"""Unit tests for BudgetTracker."""

import json
import tempfile
from pathlib import Path

import pytest

from backend.modules.ai.budget_tracker import BudgetTracker, calculate_cost
from backend.modules.ai.exceptions import BudgetExceededError


@pytest.fixture
def tmp_budget_file(tmp_path):
    return tmp_path / "ai_budget.json"


@pytest.fixture
def tracker(tmp_budget_file):
    return BudgetTracker(
        monthly_budget_usd=10.0,
        warning_threshold=0.8,
        hard_stop=True,
        budget_file=tmp_budget_file,
    )


def test_initial_spend_is_zero(tracker):
    assert tracker.current_month_spend() == 0.0


def test_record_spend_accumulates(tracker):
    tracker.record_spend(0.005, "gpt-4o-mini")
    tracker.record_spend(0.003, "gpt-4o-mini")
    assert abs(tracker.current_month_spend() - 0.008) < 1e-9


def test_remaining_budget(tracker):
    tracker.record_spend(2.5, "gpt-4o-mini")
    assert abs(tracker.remaining_budget() - 7.5) < 1e-9


def test_is_over_budget_false_initially(tracker):
    assert tracker.is_over_budget() is False


def test_is_over_budget_true_when_exceeded(tracker):
    tracker.record_spend(10.01, "gpt-4o-mini")
    assert tracker.is_over_budget() is True


def test_is_near_budget_false_below_threshold(tracker):
    tracker.record_spend(7.9, "gpt-4o-mini")
    assert tracker.is_near_budget() is False


def test_is_near_budget_true_at_threshold(tracker):
    tracker.record_spend(8.0, "gpt-4o-mini")
    assert tracker.is_near_budget() is True


def test_enforce_budget_raises_when_over(tracker):
    tracker.record_spend(10.01, "gpt-4o-mini")
    with pytest.raises(BudgetExceededError) as exc_info:
        tracker.enforce_budget()
    assert exc_info.value.budget == 10.0


def test_enforce_budget_no_raise_when_under(tracker):
    tracker.record_spend(9.99, "gpt-4o-mini")
    tracker.enforce_budget()  # should not raise


def test_enforce_budget_no_raise_when_hard_stop_disabled(tmp_budget_file):
    t = BudgetTracker(10.0, 0.8, hard_stop=False, budget_file=tmp_budget_file)
    t.record_spend(99.0, "gpt-4o-mini")
    t.enforce_budget()  # must not raise even over budget


def test_persistence_survives_reload(tmp_budget_file):
    t1 = BudgetTracker(10.0, 0.8, budget_file=tmp_budget_file)
    t1.record_spend(3.14, "gpt-4o-mini")

    t2 = BudgetTracker(10.0, 0.8, budget_file=tmp_budget_file)
    assert abs(t2.current_month_spend() - 3.14) < 1e-9


def test_get_status_structure(tracker):
    tracker.record_spend(2.0, "gpt-4o-mini")
    status = tracker.get_status()
    assert "current_month_spend_usd" in status
    assert "monthly_budget_usd" in status
    assert "remaining_budget_usd" in status
    assert "percentage_used" in status
    assert "is_near_budget" in status
    assert "is_over_budget" in status
    assert "current_month" in status


def test_calculate_cost_gpt4o_mini():
    cost = calculate_cost("gpt-4o-mini", 1_000_000, 1_000_000)
    assert abs(cost - 0.75) < 1e-6  # (0.15 + 0.60) / 1


def test_calculate_cost_unknown_model_uses_default():
    cost = calculate_cost("unknown-model", 1_000_000, 0)
    assert cost > 0


def test_remaining_budget_never_negative(tracker):
    tracker.record_spend(50.0, "gpt-4o-mini")
    assert tracker.remaining_budget() == 0.0
