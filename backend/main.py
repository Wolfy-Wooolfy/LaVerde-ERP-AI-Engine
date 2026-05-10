"""
CRM AI Engine — FastAPI application entry point.
Phase 2: async, rate limiting, CORS, security headers, structured error responses.
"""

import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
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
from backend.modules.crm.service import CrmService

# ── Application lifespan ──────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    init_cache(settings.CACHE_TTL_SECONDS)
    app.state.crm_service = CrmService()
    app.state.limiter = limiter
    set_start_time()
    yield
    await app.state.crm_service.client.close()


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
