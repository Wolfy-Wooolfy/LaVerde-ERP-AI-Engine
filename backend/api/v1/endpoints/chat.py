"""AI chat assistant endpoints."""

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from loguru import logger

from backend.api.deps import get_crm_service, get_current_user
from backend.core.config import settings
from backend.core.limiter import limiter
from backend.modules.ai.cache import IntentCache
from backend.modules.ai.chat.data_fetcher import fetch_data_for_intent
from backend.modules.ai.chat.intent_parser import parse_intent
from backend.modules.ai.chat.prompts import CONVERSATIONAL_INTENTS, SUGGESTED_QUESTIONS
from backend.modules.ai.chat.response_builder import build_response
from backend.modules.ai.chat.schemas import (
    ChatMessage,
    ChatMessageRole,
    ChatRequest,
    ChatResponse,
    QueryIntent,
)
from backend.modules.ai.chat.session_manager import SessionManager
from backend.modules.ai.exceptions import BudgetExceededError
from backend.modules.crm.service import CrmService

router = APIRouter(prefix="/chat", tags=["chat"])


# ── Dependencies ───────────────────────────────────────────────────────────────


def _get_session_manager(request: Request) -> SessionManager:
    mgr = getattr(request.app.state, "chat_session_manager", None)
    if mgr is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"ok": False, "error": {"code": "CHAT_NOT_INITIALIZED", "message": "Chat service not ready"}},
        )
    return mgr  # type: ignore[return-value]


def _get_ai_client(request: Request):  # type: ignore[no-untyped-def]
    client = getattr(request.app.state, "ai_client", None)
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"ok": False, "error": {"code": "AI_NOT_INITIALIZED", "message": "AI service not ready"}},
        )
    return client


def _get_budget_tracker(request: Request):  # type: ignore[no-untyped-def]
    tracker = getattr(request.app.state, "ai_budget_tracker", None)
    if tracker is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"ok": False, "error": {"code": "AI_NOT_INITIALIZED", "message": "AI service not ready"}},
        )
    return tracker


def _get_intent_cache(request: Request) -> IntentCache:
    cache = getattr(request.app.state, "chat_intent_cache", None)
    if cache is None:
        cache = IntentCache()
        request.app.state.chat_intent_cache = cache
    return cache  # type: ignore[return-value]


def _get_prioritizer(request: Request):  # type: ignore[no-untyped-def]
    return getattr(request.app.state, "ai_prioritizer", None)


# ── Endpoints ──────────────────────────────────────────────────────────────────


@router.post("/message", response_model=ChatResponse, summary="Send a message to the CRM AI assistant")
@limiter.limit("30/minute")
async def post_message(
    request: Request,
    body: ChatRequest,
    user: str = Depends(get_current_user),
    crm: CrmService = Depends(get_crm_service),
    session_mgr: SessionManager = Depends(_get_session_manager),
    budget_tracker=Depends(_get_budget_tracker),
    ai_client=Depends(_get_ai_client),
    intent_cache: IntentCache = Depends(_get_intent_cache),
    prioritizer=Depends(_get_prioritizer),
) -> ChatResponse:
    if not settings.AI_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"ok": False, "error": {"code": "AI_DISABLED", "message": "AI features are disabled"}},
        )

    # 1. Budget check
    try:
        budget_tracker.enforce_budget()
    except BudgetExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={"ok": False, "error": {"code": "AI_BUDGET_EXCEEDED", "message": "Monthly AI budget exhausted"}},
        ) from exc

    # 2. Locale
    locale = request.cookies.get("lang", "en")
    if locale not in ("en", "ar"):
        locale = "en"

    # 3. Session
    session = await session_mgr.get_or_create(body.session_id, locale)

    # 4. Session message cap
    if session_mgr.is_session_full(session):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"ok": False, "error": {"code": "SESSION_LIMIT", "message": "Session message limit reached. Start a new chat."}},
        )

    # 5. Add user message
    user_msg = ChatMessage(
        id=str(uuid4()),
        role=ChatMessageRole.USER,
        content=body.message,
        timestamp=datetime.now(timezone.utc),
    )
    await session_mgr.add_message(session.session_id, user_msg)

    context = await session_mgr.get_recent_context(session.session_id, n=20)

    # 6. Stage 1: intent parsing
    try:
        intent, stage1_cost = await parse_intent(
            question=body.message,
            context=context,
            locale=locale,
            ai_client=ai_client,
            intent_cache=intent_cache,
        )
    except Exception as exc:
        logger.error(f"Intent parsing failed: {exc}")
        intent = QueryIntent(intent="unknown", response_format="analysis", confidence=0.0)
        stage1_cost = 0.0

    # 7. Data fetch (read-only) — conversational intents skip CRM entirely
    if intent.intent == "unknown":
        data: dict = {"type": "clarification_needed"}
    elif intent.intent in CONVERSATIONAL_INTENTS:
        data = {"type": "conversational", "subtype": intent.intent}
    else:
        try:
            data = await fetch_data_for_intent(
                intent=intent.intent,
                filters=intent.filters,
                crm=crm,
                prioritizer=prioritizer,
            )
        except Exception as exc:
            logger.error(f"Data fetch failed for intent {intent.intent!r}: {exc}")
            data = {"type": "error", "message": "Failed to retrieve CRM data"}

    # 8. Stage 2: response generation
    try:
        response_text, followups, stage2_cost = await build_response(
            question=body.message,
            intent=intent,
            data=data,
            locale=locale,
            context=context,
            ai_client=ai_client,
            crm=crm,
            intent_cache=intent_cache,
        )
    except Exception as exc:
        logger.error(f"Response builder failed: {exc}")
        response_text = (
            "عذراً، حدث خطأ. حاول مرة أخرى." if locale == "ar"
            else "Sorry, an error occurred. Please try again."
        )
        followups = []
        stage2_cost = 0.0

    total_cost = stage1_cost + stage2_cost

    # 9. Persist assistant message
    assistant_msg = ChatMessage(
        id=str(uuid4()),
        role=ChatMessageRole.ASSISTANT,
        content=response_text,
        timestamp=datetime.now(timezone.utc),
        data_snapshot=data,
        intent=intent.intent,
        cost_usd=total_cost,
    )
    await session_mgr.add_message(session.session_id, assistant_msg)

    logger.info(
        f"chat | session={body.session_id[:8]} intent={intent.intent} "
        f"cost=${total_cost:.6f} locale={locale}"
    )

    return ChatResponse(
        session_id=session.session_id,
        message=assistant_msg,
        suggested_followups=followups,
    )


@router.delete("/session/{session_id}", summary="Clear a chat session (new chat)")
async def clear_session(
    session_id: str,
    request: Request,
    user: str = Depends(get_current_user),
    session_mgr: SessionManager = Depends(_get_session_manager),
) -> dict:
    deleted = await session_mgr.delete_session(session_id)
    return {"ok": True, "deleted": deleted, "session_id": session_id}


@router.get("/suggested-questions", summary="Get starter questions in current locale")
@limiter.limit("60/minute")
async def suggested_questions(
    request: Request,
    user: str = Depends(get_current_user),
) -> list[str]:
    locale = request.cookies.get("lang", "en")
    if locale not in ("en", "ar"):
        locale = "en"
    return SUGGESTED_QUESTIONS[locale]
