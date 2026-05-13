"""AI endpoints: lead prioritization, budget status, health."""

from datetime import datetime, timezone

from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from loguru import logger
from pydantic import ValidationError

from backend.api.deps import get_current_user
from backend.core.config import settings
from backend.core.limiter import limiter
from backend.shared.ai.exceptions import (
    AIFeatureDisabledError,
    AIServiceError,
    BudgetExceededError,
)
from backend.modules.ai.schemas import (
    AIHealthResponse,
    BudgetStatus,
    LeadContext,
    LeadPriority,
    PrioritizeOverdueRequest,
    PrioritizeOverdueResponse,
)

router = APIRouter(prefix="/ai", tags=["ai"])


# ── Dependency: get the AI prioritizer from app state ─────────────────────────

def _get_prioritizer(request: Request):  # type: ignore[no-untyped-def]
    prioritizer = getattr(request.app.state, "ai_prioritizer", None)
    if prioritizer is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"ok": False, "error": {"code": "AI_NOT_INITIALIZED", "message": "AI service not ready"}},
        )
    return prioritizer


def _get_budget_tracker(request: Request):  # type: ignore[no-untyped-def]
    tracker = getattr(request.app.state, "ai_budget_tracker", None)
    if tracker is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"ok": False, "error": {"code": "AI_NOT_INITIALIZED", "message": "AI service not ready"}},
        )
    return tracker


def _budget_error_response(exc: BudgetExceededError) -> dict:
    return {
        "ok": False,
        "error": {
            "code": "AI_BUDGET_EXCEEDED",
            "message": "Monthly AI budget exhausted. Resets next month.",
            "details": {"spent": exc.spent, "budget": exc.budget},
        },
    }


def _ai_disabled_response() -> dict:
    return {
        "ok": False,
        "error": {
            "code": "AI_DISABLED",
            "message": "AI features are currently disabled.",
            "details": {},
        },
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post(
    "/prioritize-lead/{lead_id}",
    response_model=LeadPriority,
    summary="Score a single lead with AI",
)
@limiter.limit("30/minute")
async def prioritize_lead(
    lead_id: int,
    request: Request,
    lead_context: Optional[LeadContext] = Body(None),
    user: str = Depends(get_current_user),
    prioritizer=Depends(_get_prioritizer),
) -> LeadPriority:
    if not settings.AI_ENABLED:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_ai_disabled_response())

    if lead_context is None:
        # Build minimal context if not provided — fetch from Odoo
        leads = await prioritizer._fetch_overdue_leads(100)
        lead = next((l for l in leads if l.lead_id == lead_id), None)
        if lead is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"ok": False, "error": {"code": "LEAD_NOT_FOUND", "message": f"Lead {lead_id} not found in overdue list"}},
            )
    else:
        lead = lead_context

    lang = request.cookies.get("lang", "en")
    if lang not in ("en", "ar"):
        lang = "en"

    try:
        result = await prioritizer.prioritize_single(lead, locale=lang)
        return result
    except BudgetExceededError as exc:
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=_budget_error_response(exc))
    except AIFeatureDisabledError:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_ai_disabled_response())
    except AIServiceError as exc:
        logger.error(f"AI service error for lead {lead_id}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"ok": False, "error": {"code": "AI_SERVICE_ERROR", "message": str(exc)}},
        )


@router.post(
    "/prioritize-overdue",
    response_model=PrioritizeOverdueResponse,
    summary="Fetch and score all overdue leads",
)
@limiter.limit("10/minute")
async def prioritize_overdue(
    request: Request,
    body: PrioritizeOverdueRequest = PrioritizeOverdueRequest(),
    user: str = Depends(get_current_user),
    prioritizer=Depends(_get_prioritizer),
) -> PrioritizeOverdueResponse:
    if not settings.AI_ENABLED:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_ai_disabled_response())

    lang = request.cookies.get("lang", "en")
    if lang not in ("en", "ar"):
        lang = "en"

    try:
        leads = await prioritizer.prioritize_overdue(limit=body.limit, locale=lang)
        total_cost = sum(l.cost_usd for l in leads)
        cached_count = sum(1 for l in leads if l.cached)
        return PrioritizeOverdueResponse(
            ok=True,
            leads=leads,
            total_cost_usd=round(total_cost, 6),
            cached_count=cached_count,
            fresh_count=len(leads) - cached_count,
        )
    except BudgetExceededError as exc:
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=_budget_error_response(exc))
    except AIFeatureDisabledError:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_ai_disabled_response())
    except (AIServiceError, ValidationError) as exc:
        logger.exception("AI service error in prioritize-overdue")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"ok": False, "error": {"code": "AI_SERVICE_UNAVAILABLE", "message": "AI service temporarily unavailable"}},
        )
    except Exception as exc:
        logger.exception("Unexpected error in prioritize-overdue")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"ok": False, "error": {"code": "AI_INTERNAL_ERROR", "message": "AI service encountered an error"}},
        )


@router.get(
    "/budget",
    response_model=BudgetStatus,
    summary="Current AI monthly budget status",
)
@limiter.limit("60/minute")
async def get_budget(
    request: Request,
    user: str = Depends(get_current_user),
    budget_tracker=Depends(_get_budget_tracker),
) -> BudgetStatus:
    s = budget_tracker.get_status()
    return BudgetStatus(**s)


@router.get(
    "/health",
    response_model=AIHealthResponse,
    summary="AI service health status",
)
@limiter.limit("60/minute")
async def ai_health(
    request: Request,
    user: str = Depends(get_current_user),
    budget_tracker=Depends(_get_budget_tracker),
) -> AIHealthResponse:
    if not settings.AI_ENABLED:
        return AIHealthResponse(
            status="disabled",
            model=settings.AI_MODEL,
            budget_ok=True,
            ai_enabled=False,
            feature_prioritization=False,
        )

    budget_ok = not budget_tracker.is_over_budget()
    overall_status = "ok" if budget_ok else "degraded"

    return AIHealthResponse(
        status=overall_status,
        model=settings.AI_MODEL,
        budget_ok=budget_ok,
        ai_enabled=settings.AI_ENABLED,
        feature_prioritization=settings.AI_FEATURE_LEAD_PRIORITIZATION,
    )
