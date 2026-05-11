"""
CRM AI Engine — FastAPI application entry point.
Phase 3: Enterprise frontend — Tailwind, Alpine.js, i18n, dark mode, RTL.
"""

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from backend.api.deps import get_current_user  # noqa: F401 — re-exported for tests
from backend.api.v1.endpoints.dashboard import router as dashboard_router
from backend.api.v1.router import api_v1_router
from backend.core.cache import init_cache
from backend.core.config import settings
from backend.core.exceptions import (
    CRMAIEngineError,
    OdooAuthenticationError,
    OdooConnectionError,
    ReadOnlyViolationError,
)
from backend.core.limiter import limiter
from backend.core.logging import setup_logging
from backend.core.metrics import get_uptime, metrics, set_start_time
from backend.modules.ai.exceptions import AIServiceError, BudgetExceededError
from backend.modules.crm.service import CrmService

# ── Application lifespan ──────────────────────────────────────────────────────


async def _session_cleanup_loop(session_manager: object) -> None:
    """Background task: purge expired chat sessions every hour."""
    while True:
        await asyncio.sleep(3600)
        await session_manager.cleanup_expired()  # type: ignore[attr-defined]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    from loguru import logger

    setup_logging()
    init_cache(settings.CACHE_TTL_SECONDS)
    app.state.crm_service = CrmService()
    app.state.limiter = limiter
    set_start_time()

    # ── Chat session manager (always initialized) ─────────────────────────────
    from backend.modules.ai.cache import IntentCache
    from backend.modules.ai.chat.session_manager import SessionManager

    session_manager = SessionManager()
    app.state.chat_session_manager = session_manager
    app.state.chat_intent_cache = IntentCache()
    cleanup_task = asyncio.create_task(_session_cleanup_loop(session_manager))

    # ── AI service initialization ─────────────────────────────────────────────
    if settings.AI_ENABLED:
        from backend.modules.ai.budget_tracker import BudgetTracker
        from backend.modules.ai.cache import AICache
        from backend.modules.ai.client import OpenAIClient
        from backend.modules.ai.prioritizer import LeadPrioritizer

        budget_tracker = BudgetTracker(
            monthly_budget_usd=settings.AI_MONTHLY_BUDGET_USD,
            warning_threshold=settings.AI_BUDGET_WARNING_THRESHOLD,
            hard_stop=settings.AI_BUDGET_HARD_STOP,
        )
        ai_client = OpenAIClient(budget_tracker=budget_tracker)
        ai_cache = AICache(ttl_seconds=settings.AI_CACHE_TTL_SECONDS)
        ai_prioritizer = LeadPrioritizer(
            odoo_client=app.state.crm_service.client,
            ai_client=ai_client,
            budget_tracker=budget_tracker,
            cache=ai_cache,
        )
        app.state.ai_budget_tracker = budget_tracker
        app.state.ai_client = ai_client
        app.state.ai_cache = ai_cache
        app.state.ai_prioritizer = ai_prioritizer
        logger.info(f"AI service initialized | model={settings.AI_MODEL} budget=${settings.AI_MONTHLY_BUDGET_USD}")
    else:
        app.state.ai_budget_tracker = None
        app.state.ai_client = None
        app.state.ai_cache = None
        app.state.ai_prioritizer = None
        logger.info("AI service disabled (AI_ENABLED=false)")

    yield

    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass

    await app.state.crm_service.client.close()
    if settings.AI_ENABLED and hasattr(app.state, "ai_client") and app.state.ai_client:
        await app.state.ai_client.close()


# ── App instance ──────────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Read-only CRM intelligence dashboard connected to Odoo.",
    lifespan=lifespan,
)

# ── Rate limiter exception handler ────────────────────────────────────────────

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

# ── CORS ──────────────────────────────────────────────────────────────────────

_origins = settings.CORS_ORIGINS if settings.CORS_ORIGINS else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=False,
)

# ── Security headers middleware ───────────────────────────────────────────────


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next: object) -> Response:
    response: Response = await call_next(request)  # type: ignore[operator]
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = settings.CSP_POLICY
    if settings.ENVIRONMENT == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# ── Request ID + metrics middleware ──────────────────────────────────────────


@app.middleware("http")
async def request_id_middleware(request: Request, call_next: object) -> Response:
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    start = time.monotonic()
    response: Response = await call_next(request)  # type: ignore[operator]
    duration_ms = int((time.monotonic() - start) * 1000)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Response-Time"] = f"{duration_ms}ms"
    metrics.record_api_request(duration_ms, response.status_code)
    return response


# ── Error response builder ────────────────────────────────────────────────────


def _error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: dict | None = None,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=status_code,
        content={
            "ok": False,
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
                "request_id": request_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        },
    )


# ── Global exception handlers ─────────────────────────────────────────────────


@app.exception_handler(ReadOnlyViolationError)
async def readonly_violation_handler(request: Request, exc: ReadOnlyViolationError) -> JSONResponse:
    return _error_response(request, 403, "READ_ONLY_VIOLATION", str(exc))


@app.exception_handler(OdooAuthenticationError)
async def odoo_auth_handler(request: Request, exc: OdooAuthenticationError) -> JSONResponse:
    return _error_response(request, 502, "ODOO_AUTH_ERROR", "Odoo authentication failed")


@app.exception_handler(OdooConnectionError)
async def odoo_connection_handler(request: Request, exc: OdooConnectionError) -> JSONResponse:
    return _error_response(request, 503, "ODOO_CONNECTION_ERROR", "Odoo is unreachable")


@app.exception_handler(CRMAIEngineError)
async def crm_engine_handler(request: Request, exc: CRMAIEngineError) -> JSONResponse:
    return _error_response(request, 500, "INTERNAL_ERROR", "Internal service error")


@app.exception_handler(BudgetExceededError)
async def budget_exceeded_handler(request: Request, exc: BudgetExceededError) -> JSONResponse:
    return _error_response(
        request,
        402,
        "AI_BUDGET_EXCEEDED",
        "Monthly AI budget exhausted. Resets next month.",
        {"spent": exc.spent, "budget": exc.budget},
    )


@app.exception_handler(AIServiceError)
async def ai_service_handler(request: Request, exc: AIServiceError) -> JSONResponse:
    return _error_response(request, 502, "AI_SERVICE_ERROR", "AI service error")


# ── Public health check (no auth) ─────────────────────────────────────────────


@app.get("/health", tags=["health"], summary="Basic liveness probe (no auth)")
def liveness() -> dict:
    return {
        "status": "ok",
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "uptime_seconds": get_uptime(),
    }


# ── Legacy redirect shims (301 Permanent) ─────────────────────────────────────


@app.get("/crm/summary", include_in_schema=False)
def legacy_summary() -> RedirectResponse:
    return RedirectResponse(url="/api/v1/summary", status_code=301)


@app.get("/crm/followup-risk", include_in_schema=False)
def legacy_followup() -> RedirectResponse:
    return RedirectResponse(url="/api/v1/followup-risk", status_code=301)


@app.get("/crm/data-quality/missing-contact", include_in_schema=False)
def legacy_missing_contact() -> RedirectResponse:
    return RedirectResponse(url="/api/v1/data-quality/missing-contact", status_code=301)


# ── Logout ─────────────────────────────────────────────────────────────────────


@app.get("/logout", include_in_schema=False)
def logout() -> Response:
    return Response(
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="CRM AI Engine"'},
        content=b"Logged out. <a href='/dashboard'>Sign in again</a>",
        media_type="text/html",
    )


# ── Mount routers ─────────────────────────────────────────────────────────────

app.include_router(api_v1_router, prefix="/api/v1")
app.include_router(dashboard_router)

# ── Static files ──────────────────────────────────────────────────────────────

_static_dir = Path("frontend/static")
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")
