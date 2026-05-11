"""Integration tests for AI endpoints using mock OpenAI and mock Odoo."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.modules.ai.schemas import LeadPriority


def _make_mock_prioritizer(tmp_path):
    """Return a mock LeadPrioritizer that returns deterministic results."""
    prioritizer = MagicMock()

    def make_priority(lead_id: int = 1) -> LeadPriority:
        return LeadPriority(
            lead_id=lead_id,
            score=80,
            tier="high",
            reasoning="Near closing stage with contact info.",
            recommended_action="Call via WhatsApp",
            key_signal="معاينة mentioned 5 days ago",
            cached=False,
            cost_usd=0.00012,
            generated_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
            model_used="gpt-4o-mini",
        )

    prioritizer.prioritize_single = AsyncMock(return_value=make_priority())
    prioritizer.prioritize_overdue = AsyncMock(return_value=[make_priority(1), make_priority(2)])
    prioritizer._fetch_overdue_leads = AsyncMock(return_value=[])
    return prioritizer


def _make_mock_budget_tracker():
    bt = MagicMock()
    bt.is_over_budget.return_value = False
    bt.is_near_budget.return_value = False
    bt.get_status.return_value = {
        "current_month_spend_usd": 1.50,
        "monthly_budget_usd": 10.0,
        "remaining_budget_usd": 8.50,
        "percentage_used": 15.0,
        "is_near_budget": False,
        "is_over_budget": False,
        "current_month": "2026-05",
    }
    return bt


AUTH = ("testadmin", "testpass")


@pytest.fixture
def client_with_ai(tmp_path):
    with TestClient(app) as c:
        app.state.ai_prioritizer = _make_mock_prioritizer(tmp_path)
        app.state.ai_budget_tracker = _make_mock_budget_tracker()
        yield c


def test_ai_health_ok(client_with_ai):
    resp = client_with_ai.get("/api/v1/ai/health", auth=AUTH)
    assert resp.status_code == 200
    data = resp.json()
    assert data["ai_enabled"] is True
    assert data["budget_ok"] is True
    assert data["status"] in ("ok", "degraded", "disabled")


def test_ai_budget_endpoint(client_with_ai):
    resp = client_with_ai.get("/api/v1/ai/budget", auth=AUTH)
    assert resp.status_code == 200
    data = resp.json()
    assert "current_month_spend_usd" in data
    assert "monthly_budget_usd" in data
    assert "percentage_used" in data


def test_prioritize_overdue_returns_leads(client_with_ai):
    resp = client_with_ai.post("/api/v1/ai/prioritize-overdue", json={"limit": 10}, auth=AUTH)
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert isinstance(data["leads"], list)
    assert len(data["leads"]) == 2
    assert data["leads"][0]["score"] == 80


def test_prioritize_overdue_lead_fields(client_with_ai):
    resp = client_with_ai.post("/api/v1/ai/prioritize-overdue", json={"limit": 5}, auth=AUTH)
    lead = resp.json()["leads"][0]
    assert "lead_id" in lead
    assert "score" in lead
    assert "tier" in lead
    assert "reasoning" in lead
    assert "recommended_action" in lead
    assert "key_signal" in lead
    assert "model_used" in lead


def test_prioritize_overdue_requires_auth():
    with TestClient(app) as c:
        resp = c.post("/api/v1/ai/prioritize-overdue", json={"limit": 10})
    assert resp.status_code == 401


def test_ai_health_requires_auth():
    with TestClient(app) as c:
        resp = c.get("/api/v1/ai/health")
    assert resp.status_code == 401


def test_ai_budget_requires_auth():
    with TestClient(app) as c:
        resp = c.get("/api/v1/ai/budget")
    assert resp.status_code == 401


def test_prioritize_overdue_budget_exceeded(client_with_ai):
    from backend.modules.ai.exceptions import BudgetExceededError

    client_with_ai.app.state.ai_prioritizer.prioritize_overdue = AsyncMock(
        side_effect=BudgetExceededError(10.0, 10.0)
    )
    resp = client_with_ai.post("/api/v1/ai/prioritize-overdue", json={"limit": 10}, auth=AUTH)
    assert resp.status_code == 402
    data = resp.json()
    # FastAPI wraps HTTPException detail under "detail" key
    detail = data.get("detail") or data
    error = detail.get("error") if isinstance(detail, dict) else {}
    assert error.get("code") == "AI_BUDGET_EXCEEDED"


def test_metrics_includes_ai_section(client_with_ai):
    resp = client_with_ai.get("/api/v1/metrics", auth=AUTH)
    assert resp.status_code == 200
    data = resp.json()
    assert "ai" in data
