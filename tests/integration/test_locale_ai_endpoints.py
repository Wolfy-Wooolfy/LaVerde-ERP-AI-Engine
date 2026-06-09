"""Integration tests: locale detection from cookie affects prioritizer calls."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.modules.crm.ai.schemas import LeadPriority


def _make_priority(lead_id: int = 1, reasoning: str = "Good lead.") -> LeadPriority:
    return LeadPriority(
        lead_id=lead_id,
        score=75,
        tier="high",
        reasoning=reasoning,
        recommended_action="Call via WhatsApp",
        key_signal="Site visit 5 days ago",
        cached=False,
        cost_usd=0.0002,
        generated_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        model_used="gpt-4o-mini",
    )


def _make_mock_budget():
    bt = MagicMock()
    bt.is_over_budget.return_value = False
    bt.is_near_budget.return_value = False
    bt.get_status.return_value = {
        "current_month_spend_usd": 0.01,
        "monthly_budget_usd": 10.0,
        "remaining_budget_usd": 9.99,
        "percentage_used": 0.1,
        "is_near_budget": False,
        "is_over_budget": False,
        "current_month": "2026-05",
    }
    return bt


@pytest.fixture
def client_with_ai():
    mock_prioritizer = MagicMock()
    mock_prioritizer.prioritize_overdue = AsyncMock(return_value=[_make_priority()])
    mock_prioritizer.prioritize_single = AsyncMock(return_value=_make_priority())
    mock_prioritizer._fetch_overdue_leads = AsyncMock(return_value=[])

    with TestClient(app) as c:
        r = c.post(
            "/login",
            data={"username": "testadmin", "password": "testpass", "next": "/"},
            follow_redirects=False,
        )
        assert r.status_code == 303, f"Login failed: {r.status_code}"
        app.state.ai_prioritizer = mock_prioritizer
        app.state.ai_budget_tracker = _make_mock_budget()
        yield c


def test_en_cookie_passes_en_locale_to_prioritize_overdue(client_with_ai):
    resp = client_with_ai.post(
        "/api/v1/ai/prioritize-overdue",
        json={"limit": 5},
        cookies={"lang": "en"},
    )
    assert resp.status_code == 200
    call_kwargs = client_with_ai.app.state.ai_prioritizer.prioritize_overdue.call_args
    assert call_kwargs.kwargs.get("locale") == "en"


def test_ar_cookie_passes_ar_locale_to_prioritize_overdue(client_with_ai):
    resp = client_with_ai.post(
        "/api/v1/ai/prioritize-overdue",
        json={"limit": 5},
        cookies={"lang": "ar"},
    )
    assert resp.status_code == 200
    call_kwargs = client_with_ai.app.state.ai_prioritizer.prioritize_overdue.call_args
    assert call_kwargs.kwargs.get("locale") == "ar"


def test_no_cookie_defaults_to_en_locale(client_with_ai):
    resp = client_with_ai.post(
        "/api/v1/ai/prioritize-overdue",
        json={"limit": 5},
    )
    assert resp.status_code == 200
    call_kwargs = client_with_ai.app.state.ai_prioritizer.prioritize_overdue.call_args
    assert call_kwargs.kwargs.get("locale") == "en"


def test_invalid_cookie_value_defaults_to_en(client_with_ai):
    resp = client_with_ai.post(
        "/api/v1/ai/prioritize-overdue",
        json={"limit": 5},
        cookies={"lang": "fr"},
    )
    assert resp.status_code == 200
    call_kwargs = client_with_ai.app.state.ai_prioritizer.prioritize_overdue.call_args
    assert call_kwargs.kwargs.get("locale") == "en"


def test_en_and_ar_locales_call_prioritizer_with_correct_limit(client_with_ai):
    client_with_ai.post(
        "/api/v1/ai/prioritize-overdue",
        json={"limit": 7},
        cookies={"lang": "ar"},
    )
    call_kwargs = client_with_ai.app.state.ai_prioritizer.prioritize_overdue.call_args
    assert call_kwargs.kwargs.get("limit") == 7
    assert call_kwargs.kwargs.get("locale") == "ar"


def test_response_structure_unchanged_for_ar_locale(client_with_ai):
    resp = client_with_ai.post(
        "/api/v1/ai/prioritize-overdue",
        json={"limit": 5},
        cookies={"lang": "ar"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    lead = data["leads"][0]
    assert "key_signal" in lead
    assert "reasoning" in lead
    assert "recommended_action" in lead
