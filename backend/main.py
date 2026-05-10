"""
CRM AI Engine — FastAPI application entry point.
"""

import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

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
from backend.core.logging import setup_logging
from backend.modules.crm.service import CrmService

# ── Application lifespan ──────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    init_cache(settings.CACHE_TTL_SECONDS)
    app.state.crm_service = CrmService()
    yield
    app.state.crm_service.client.close()


# ── App instance ──────────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Read-only CRM intelligence dashboard connected to Odoo.",
    lifespan=lifespan,
)


# ── Request ID middleware ─────────────────────────────────────────────────────


@app.middleware("http")
async def request_id_middleware(request: Request, call_next: object) -> Response:
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    start = time.monotonic()
    response: Response = await call_next(request)  # type: ignore[operator]
    duration_ms = int((time.monotonic() - start) * 1000)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Response-Time"] = f"{duration_ms}ms"
    return response


# ── Global exception handlers ─────────────────────────────────────────────────


@app.exception_handler(ReadOnlyViolationError)
async def readonly_violation_handler(request: Request, exc: ReadOnlyViolationError) -> JSONResponse:
    return JSONResponse(
        status_code=403,
        content={"ok": False, "error": str(exc), "error_code": "READ_ONLY_VIOLATION"},
    )


@app.exception_handler(OdooAuthenticationError)
async def odoo_auth_handler(request: Request, exc: OdooAuthenticationError) -> JSONResponse:
    return JSONResponse(
        status_code=502,
        content={
            "ok": False,
            "error": "Odoo authentication failed",
            "error_code": "ODOO_AUTH_ERROR",
        },  # noqa: E501
    )


@app.exception_handler(OdooConnectionError)
async def odoo_connection_handler(request: Request, exc: OdooConnectionError) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "ok": False,
            "error": "Odoo is unreachable",
            "error_code": "ODOO_CONNECTION_ERROR",
        },  # noqa: E501
    )


@app.exception_handler(CRMAIEngineError)
async def crm_engine_handler(request: Request, exc: CRMAIEngineError) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"ok": False, "error": "Internal service error", "error_code": "INTERNAL_ERROR"},
    )


# ── Public health check (no auth) ─────────────────────────────────────────────


@app.get("/health", tags=["health"], summary="Basic liveness probe (no auth)")
def liveness() -> dict:
    return {"status": "ok", "service": settings.APP_NAME, "version": settings.APP_VERSION}


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


# ── Logout (clears Basic Auth credentials in the browser) ─────────────────────


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
