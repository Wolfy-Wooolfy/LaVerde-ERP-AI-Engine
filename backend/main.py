"""
LaVerde ERP AI Engine — FastAPI application entry point.
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
from starlette.middleware.sessions import SessionMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from backend.api.deps import get_current_user  # re-exported for dependency_overrides in tests
from backend.api.v1.endpoints.auth import router as auth_router
from backend.api.v1.endpoints.dashboard import router as dashboard_router
from backend.api.v1.router import api_v1_router
from backend.core.cache import init_cache
from backend.core.cache_context import reset_cache_bypass, set_cache_bypass
from backend.core.config import settings
from backend.core.exceptions import (
    LaVerdeERPError,
    OdooAuthenticationError,
    OdooConnectionError,
    ReadOnlyViolationError,
)
from backend.core.responses import error_response as _core_error_response
from backend.core.limiter import limiter
from backend.core.logging import setup_logging
from backend.core.metrics import get_uptime, metrics, set_start_time
from backend.core.templates import templates as _err_templates
from backend.shared.ai.exceptions import AIServiceError, BudgetExceededError
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

    from backend.auth.repository import SQLiteUserRepository
    from backend.auth.seed import seed_initial_user
    user_repo = SQLiteUserRepository(settings.USER_DB_PATH)
    seed_initial_user(user_repo)
    app.state.user_repo = user_repo

    app.state.crm_service = CrmService()
    app.state.limiter = limiter
    set_start_time()

    # ── AI Module Registry ────────────────────────────────────────────────────
    from backend.modules.crm.ai.registry import register as register_crm
    register_crm()

    # ── Chat session manager (always initialized) ─────────────────────────────
    from backend.shared.ai.cache import IntentCache
    from backend.modules.crm.ai.chat.session_manager import SessionManager

    session_manager = SessionManager()
    app.state.chat_session_manager = session_manager
    app.state.chat_intent_cache = IntentCache()
    cleanup_task = asyncio.create_task(_session_cleanup_loop(session_manager))

    # ── AI service initialization ─────────────────────────────────────────────
    logger.info(f"Display name resolved: '{settings.DISPLAY_NAME}' (set DISPLAY_NAME in .env; restart required to apply)")

    if settings.AI_ENABLED:
        from backend.shared.ai.budget_tracker import BudgetTracker
        from backend.shared.ai.cache import AICache
        from backend.shared.ai.client import OpenAIClient
        from backend.modules.crm.ai.prioritizer import LeadPrioritizer

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


# ── Interactive API docs (production-gated) ───────────────────────────────────


def docs_urls_for(environment: str) -> dict:
    """Return the FastAPI docs-URL kwargs for the given ENVIRONMENT.

    In production the interactive API docs are disabled: returning ``None`` for
    docs_url/redoc_url/openapi_url removes /docs, /redoc, /openapi.json AND the
    /docs/oauth2-redirect helper from the route map (they expose the route shapes,
    no data). In every non-production environment the default paths are served, so
    local development and the audit / CI guard keep their docs surface intact.
    """
    if environment == "production":
        return {"docs_url": None, "redoc_url": None, "openapi_url": None}
    return {"docs_url": "/docs", "redoc_url": "/redoc", "openapi_url": "/openapi.json"}


# ── App instance ──────────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Read-only ERP intelligence dashboard connected to Odoo.",
    lifespan=lifespan,
    **docs_urls_for(settings.ENVIRONMENT),
)

# ── Rate limiter exception handler ────────────────────────────────────────────

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

# ── CORS ──────────────────────────────────────────────────────────────────────


def cors_kwargs_for(origins: list[str]) -> dict:
    """Return the CORSMiddleware kwargs for the given CORS_ORIGINS list.

    No wildcard fallback: an empty list means no cross-origin access is
    granted, not "allow everything". Settings.validate_cors_origins already
    forbids "*" outright when ENVIRONMENT=production.
    """
    return {
        "allow_origins": origins,
        "allow_methods": ["GET", "OPTIONS"],
        "allow_headers": ["*"],
        "allow_credentials": False,
    }


app.add_middleware(CORSMiddleware, **cors_kwargs_for(settings.CORS_ORIGINS))

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SESSION_SECRET,  # validated non-empty ≥32 chars at Settings init
    session_cookie="laverde_session",  # distinct name: "session" is shared by every localhost app
    max_age=settings.SESSION_COOKIE_MAX_AGE,
    https_only=(settings.ENVIRONMENT == "production"),
    same_site="lax",
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
    # Static asset cache policy, keyed on the PRESENCE of the content fingerprint,
    # not the path: a ?v= URL can never change content under that URL (the hash
    # changes instead), so it is safe to cache for a year; anything reaching an
    # asset without a fingerprint (old bookmark, manifest miss) must revalidate on
    # every use and can therefore never go stale. Fonts are un-fingerprinted by
    # design (see backend/core/static_manifest.py) and get a bounded 30 days.
    if request.url.path.startswith("/static/"):
        if "v" in request.query_params:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif request.url.path.startswith("/static/vendor/fonts/"):
            response.headers["Cache-Control"] = "public, max-age=2592000"
        else:
            response.headers["Cache-Control"] = "no-cache"
    return response


# ── Request ID + metrics middleware ──────────────────────────────────────────


@app.middleware("http")
async def request_id_middleware(request: Request, call_next: object) -> Response:
    # Honour client-supplied X-Request-ID; fall back to a fresh 32-char hex UUID.
    # Stored in request.state so _req_id() reads one canonical value per request.
    rid = (request.headers.get("X-Request-ID") or "").strip()
    request_id = rid if rid else uuid.uuid4().hex
    request.state.request_id = request_id
    # Per-request cache-bypass signal: a manual ?refresh=1 on a GET forces every
    # in-memory KPI cache read to miss (fresh Odoo fetch); write-back is preserved.
    # Set BEFORE call_next so the downstream endpoint's task inherits the value;
    # reset in finally so it can never leak to the next request on the loop.
    bypass = request.method == "GET" and request.query_params.get("refresh") == "1"
    token = set_cache_bypass(bypass)
    start = time.monotonic()
    try:
        response: Response = await call_next(request)  # type: ignore[operator]
        duration_ms = int((time.monotonic() - start) * 1000)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time"] = f"{duration_ms}ms"
        metrics.record_api_request(duration_ms, response.status_code)
        return response
    finally:
        reset_cache_bypass(token)


# ── Error response builder ────────────────────────────────────────────────────


def _error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: dict | None = None,
) -> JSONResponse:
    return _core_error_response(request, status_code, code, message, details)


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


@app.exception_handler(LaVerdeERPError)
async def crm_engine_handler(request: Request, exc: LaVerdeERPError) -> JSONResponse:
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


@app.exception_handler(403)
async def module_forbidden_handler(request: Request, exc: Exception) -> Response:
    """Render 403.html for browser requests; JSON for API requests."""
    from backend.core.i18n import detect_lang, make_translator
    if "text/html" in request.headers.get("accept", ""):
        lang = detect_lang(dict(request.cookies), request.headers.get("accept-language", ""))
        ctx = {
            "request": request,
            "lang": lang,
            "is_rtl": lang == "ar",
            "_t": make_translator(lang),
        }
        return _err_templates.TemplateResponse("403.html", ctx, status_code=403)
    return _error_response(
        request, 403, "MODULE_ACCESS_DENIED",
        "You do not have access to this module.",
    )


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


# ── Root redirect (307 Temporary) ─────────────────────────────────────────────
# Deliberately 307, not 301 like the legacy shims above:
#   (1) 307 preserves the request method, so a stray POST to "/" is not silently
#       downgraded to a GET.
#   (2) 307 is temporary, so browsers do not permanently cache "/" -> "/login";
#       the legacy shims are 301 because those old paths really are gone
#       forever, whereas "/" is the live front door whose behaviour may change
#       later. Do not "fix" this into consistency with the 301s above.


@app.get("/", include_in_schema=False)
def root_redirect() -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=307)


# ── Mount routers ─────────────────────────────────────────────────────────────

app.include_router(api_v1_router, prefix="/api/v1")
app.include_router(dashboard_router)
app.include_router(auth_router)

# ── Static files ──────────────────────────────────────────────────────────────

_static_dir = Path("frontend/static")
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")
