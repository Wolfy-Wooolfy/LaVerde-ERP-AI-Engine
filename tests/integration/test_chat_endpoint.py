"""Integration tests for chat API endpoints."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.shared.ai.cache import IntentCache
from backend.modules.crm.ai.chat.session_manager import SessionManager
from backend.modules.crm.schemas import OverdueBySalesperson
from backend.modules.crm.service import CrmService

# ── Mock factories ─────────────────────────────────────────────────────────────


def _make_ai_client(intent: str = "list_overdue_by_salesperson"):
    client = AsyncMock()
    client.chat_completion.side_effect = [
        # Stage 1: intent parsing
        MagicMock(
            content=(
                f'{{"intent":"{intent}","filters":{{"limit":5}},'
                f'"response_format":"table","confidence":0.9}}'
            ),
            cost_usd=0.0001,
        ),
        # Stage 2: response generation
        MagicMock(
            content=(
                "| Salesperson | Overdue |\n|---|---|\n| Ahmed Ali | 10 |\n\n"
                "💡 You might also ask:\n- Show teams\n- Count by stage"
            ),
            cost_usd=0.0003,
        ),
    ]
    return client


def _make_budget(over: bool = False):
    budget = MagicMock()
    if over:
        from backend.shared.ai.exceptions import BudgetExceededError
        budget.enforce_budget.side_effect = BudgetExceededError(spent=10.0, budget=10.0)
    else:
        budget.enforce_budget.return_value = None
    budget.is_over_budget.return_value = over
    budget.record_spend.return_value = None
    return budget


def _make_crm():
    crm = MagicMock()
    crm.client = MagicMock()
    crm.client.close = AsyncMock()
    crm.overdue_by_salesperson = AsyncMock(
        return_value=[
            OverdueBySalesperson(salesperson_id=1, salesperson_name="Ahmed Ali", overdue_count=10)
        ]
    )
    return crm


@pytest.fixture
def chat_client():
    with TestClient(app) as c:
        r = c.post(
            "/login",
            data={"username": "testadmin", "password": "testpass", "next": "/"},
            follow_redirects=False,
        )
        assert r.status_code == 303, f"Login failed: {r.status_code}"
        app.state.chat_session_manager = SessionManager()
        app.state.chat_intent_cache = IntentCache()
        app.state.ai_client = _make_ai_client()
        app.state.ai_budget_tracker = _make_budget()
        app.state.crm_service = _make_crm()
        app.state.ai_prioritizer = None
        yield c


# ── /api/v1/chat/suggested-questions ─────────────────────────────────────────


def test_suggested_questions_en(chat_client):
    resp = chat_client.get(
        "/api/v1/chat/suggested-questions",
        cookies={"lang": "en"},
    )
    assert resp.status_code == 200
    qs = resp.json()
    assert isinstance(qs, list)
    assert len(qs) == 6
    assert any("salesperson" in q.lower() or "overdue" in q.lower() for q in qs)


def test_suggested_questions_ar(chat_client):
    resp = chat_client.get(
        "/api/v1/chat/suggested-questions",
        cookies={"lang": "ar"},
    )
    assert resp.status_code == 200
    qs = resp.json()
    # Must use correct terminology — "موظفي مبيعات" not "مندوب"
    assert any("موظفي مبيعات" in q or "overdue" in q.lower() or "تأخر" in q for q in qs)
    assert not any("مندوب" in q for q in qs)


def test_suggested_questions_default_en_for_unknown_locale(chat_client):
    resp = chat_client.get(
        "/api/v1/chat/suggested-questions",
        cookies={"lang": "de"},
    )
    assert resp.status_code == 200
    qs = resp.json()
    assert any(q[0].isascii() for q in qs)


def test_suggested_questions_requires_auth():
    with TestClient(app) as c:
        resp = c.get("/api/v1/chat/suggested-questions")
    assert resp.status_code == 401


# ── POST /api/v1/chat/message ─────────────────────────────────────────────────


def test_post_message_success(chat_client):
    resp = chat_client.post(
        "/api/v1/chat/message",
        json={"session_id": "test-001", "message": "Show overdue by salesperson"},
        cookies={"lang": "en"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == "test-001"
    assert data["message"]["role"] == "assistant"
    assert data["message"]["content"]
    assert isinstance(data["suggested_followups"], list)
    assert data["message"]["intent"] == "list_overdue_by_salesperson"


def test_post_message_requires_auth():
    with TestClient(app) as c:
        resp = c.post(
            "/api/v1/chat/message",
            json={"session_id": "test-002", "message": "Hello"},
        )
    assert resp.status_code == 401


def test_post_message_rejects_empty(chat_client):
    resp = chat_client.post(
        "/api/v1/chat/message",
        json={"session_id": "test-003", "message": ""},
    )
    assert resp.status_code == 422


def test_post_message_rejects_too_long(chat_client):
    resp = chat_client.post(
        "/api/v1/chat/message",
        json={"session_id": "test-004", "message": "x" * 501},
    )
    assert resp.status_code == 422


def test_post_message_budget_exceeded():
    with TestClient(app) as c:
        r = c.post(
            "/login",
            data={"username": "testadmin", "password": "testpass", "next": "/"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        app.state.chat_session_manager = SessionManager()
        app.state.chat_intent_cache = IntentCache()
        app.state.ai_client = _make_ai_client()
        app.state.ai_budget_tracker = _make_budget(over=True)
        app.state.crm_service = _make_crm()
        app.state.ai_prioritizer = None

        resp = c.post(
            "/api/v1/chat/message",
            json={"session_id": "budget-test", "message": "Hello"},
            cookies={"lang": "en"},
        )
    assert resp.status_code == 402


def test_post_message_session_continuity(chat_client):
    session_id = "continuity-test"
    # First message
    app.state.ai_client = _make_ai_client("count_by_stage")
    r1 = chat_client.post(
        "/api/v1/chat/message",
        json={"session_id": session_id, "message": "How many in Negotiation?"},
        cookies={"lang": "en"},
    )
    assert r1.status_code == 200

    # Second message in same session
    app.state.ai_client = _make_ai_client("free_form_analysis")
    r2 = chat_client.post(
        "/api/v1/chat/message",
        json={"session_id": session_id, "message": "What about the pipeline overall?"},
        cookies={"lang": "en"},
    )
    assert r2.status_code == 200
    assert r2.json()["session_id"] == session_id


def test_post_message_ar_locale(chat_client):
    app.state.ai_client = _make_ai_client("list_overdue_by_salesperson")
    resp = chat_client.post(
        "/api/v1/chat/message",
        json={"session_id": "ar-test", "message": "إيه أعلى مندوبين؟"},
        cookies={"lang": "ar"},
    )
    assert resp.status_code == 200


# ── DELETE /api/v1/chat/session/{id} ─────────────────────────────────────────


def test_delete_session(chat_client):
    # Create session by sending a message first
    app.state.ai_client = _make_ai_client()
    chat_client.post(
        "/api/v1/chat/message",
        json={"session_id": "to-delete", "message": "Hello"},
        cookies={"lang": "en"},
    )
    resp = chat_client.delete("/api/v1/chat/session/to-delete")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["deleted"] is True


def test_delete_nonexistent_session(chat_client):
    resp = chat_client.delete("/api/v1/chat/session/does-not-exist")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["deleted"] is False


def test_delete_session_requires_auth():
    with TestClient(app) as c:
        resp = c.delete("/api/v1/chat/session/some-id")
    assert resp.status_code == 401


# ── Bug 2: Conversational intents bypass CRM ─────────────────────────────────


def _make_conversational_ai_client(intent: str = "greeting"):
    """AI client that returns a conversational intent in Stage 1."""
    client = AsyncMock()
    client.chat_completion.side_effect = [
        # Stage 1: intent parsing
        MagicMock(
            content=f'{{"intent":"{intent}","filters":{{}},"response_format":"analysis","confidence":0.95}}',
            cost_usd=0.00001,
        ),
        # Stage 2: conversational response (short)
        MagicMock(
            content="أهلاً! كيف يمكنني مساعدتك؟\n\n💡 يمكنك أيضاً أن تسأل:\n- إيه أعلى 5 موظفي مبيعات؟\n- كم lead متأخر؟",
            cost_usd=0.00004,
        ),
    ]
    return client


def test_greeting_does_not_call_crm(chat_client):
    """Conversational 'greeting' intent must NOT query any CRM data method."""
    crm = _make_crm()
    app.state.crm_service = crm
    app.state.ai_client = _make_conversational_ai_client("greeting")

    resp = chat_client.post(
        "/api/v1/chat/message",
        json={"session_id": "greeting-test", "message": "أهلاً"},
        cookies={"lang": "ar"},
    )
    assert resp.status_code == 200
    # No CRM read methods should have been called
    crm.overdue_by_salesperson.assert_not_called()
    crm.overdue_by_team.assert_not_called() if hasattr(crm, "overdue_by_team") else None


def test_thanks_response_is_low_cost(chat_client):
    """Thanks message should be cheap (conversational AI call only, no CRM)."""
    app.state.ai_client = _make_conversational_ai_client("thanks")

    resp = chat_client.post(
        "/api/v1/chat/message",
        json={"session_id": "thanks-test", "message": "شكراً"},
        cookies={"lang": "ar"},
    )
    assert resp.status_code == 200
    data = resp.json()
    # Cost should be minimal (just the conversational AI call)
    assert data["message"]["cost_usd"] < 0.001


def test_meta_question_returns_200(chat_client):
    """'What are you?' should be handled as meta_question, get 200 back."""
    app.state.ai_client = _make_conversational_ai_client("meta_question")

    resp = chat_client.post(
        "/api/v1/chat/message",
        json={"session_id": "meta-test", "message": "إنت AI ولا بشر؟"},
        cookies={"lang": "ar"},
    )
    assert resp.status_code == 200


# ── Read-only safety ──────────────────────────────────────────────────────────


def test_no_odoo_writes_during_chat(chat_client):
    """Verify Odoo client is only called for read operations."""
    crm = _make_crm()
    app.state.crm_service = crm
    app.state.ai_client = _make_ai_client()

    chat_client.post(
        "/api/v1/chat/message",
        json={"session_id": "readonly-test", "message": "Show overdue"},
        cookies={"lang": "en"},
    )

    # Only read method should have been called
    crm.overdue_by_salesperson.assert_called()
    # Write methods must not exist on the real CrmService class
    assert not hasattr(CrmService, "create_lead")
    assert not hasattr(CrmService, "update_lead")
    assert not hasattr(CrmService, "delete_lead")
