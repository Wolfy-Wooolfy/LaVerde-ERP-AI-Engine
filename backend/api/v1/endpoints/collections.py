"""
Collections KPI endpoints.

GET /api/v1/collections/kpi/late-uncollected       — KPI 2: Late Uncollected
GET /api/v1/collections/kpi/total-portfolio-value  — KPI 1: Total Portfolio Value
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from loguru import logger

from backend.core.exceptions import OdooQueryError
from backend.core.limiter import limiter
from backend.modules.collections.services.kpi_service import (
    get_late_uncollected,
    get_total_portfolio_value,
)

router = APIRouter(prefix="/collections", tags=["collections"])


@router.get("/kpi/late-uncollected", summary="KPI 2 — Late Uncollected receivables")
@limiter.limit("60/minute")
async def late_uncollected(request: Request) -> JSONResponse:
    try:
        data = await get_late_uncollected()
    except OdooQueryError:
        logger.warning("KPI 2 — Odoo query failed", exc_info=True)
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "odoo_unavailable",
                    "message": "Odoo is unavailable or the query failed. Try again shortly.",
                }
            },
        )
    except Exception:
        logger.error("KPI 2 — unexpected error", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "An unexpected error occurred.",
                }
            },
        )

    return JSONResponse(
        content=data,
        headers={
            "Cache-Control": "private, max-age=60",
            "X-Cache-Status": str(data.get("cache_status", "fresh")),
        },
    )


@router.get("/kpi/total-portfolio-value", summary="KPI 1 — Total Portfolio Value")
@limiter.limit("60/minute")
async def total_portfolio_value(request: Request) -> JSONResponse:
    try:
        data = await get_total_portfolio_value()
    except OdooQueryError:
        logger.warning("KPI 1 — Odoo query failed", exc_info=True)
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "odoo_unavailable",
                    "message": "Odoo is unavailable or the query failed. Try again shortly.",
                }
            },
        )
    except Exception:
        logger.error("KPI 1 — unexpected error", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "An unexpected error occurred.",
                }
            },
        )

    return JSONResponse(
        content=data,
        headers={
            "Cache-Control": "private, max-age=60",
            "X-Cache-Status": str(data.get("cache_status", "fresh")),
        },
    )
