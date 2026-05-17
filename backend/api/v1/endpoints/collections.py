"""
Collections KPI endpoints.

GET /api/v1/collections/kpi/late-uncollected              — KPI 2: Late Uncollected
GET /api/v1/collections/kpi/total-portfolio-value         — KPI 1: Total Portfolio Value
GET /api/v1/collections/kpi/late-uncollected-by-project   — KPI 5: Late Uncollected per project
GET /api/v1/collections/kpi/pending-check-exposure        — KPI 3: Pending Check Exposure
GET /api/v1/collections/kpi/collection-trend-6m           — KPI 6: 6-Month Collection Trend
GET /api/v1/collections/kpi/collection-rate               — KPI 4: Collection Rate MTD & YTD
GET /api/v1/collections/kpi/collection-rate-by-project    — KPI 5b: Collection Rate per Project
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from loguru import logger

from backend.core.exceptions import OdooQueryError
from backend.core.limiter import limiter
from backend.modules.collections.services.kpi_service import (
    get_collection_rate_by_project,
    get_collection_rate_mtd_ytd,
    get_collection_trend_6m,
    get_late_uncollected,
    get_late_uncollected_by_project,
    get_pending_check_exposure,
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


@router.get(
    "/kpi/late-uncollected-by-project",
    summary="KPI 5 — Late Uncollected per project (New Capital, Cassette, La puerta)",
)
@limiter.limit("60/minute")
async def late_uncollected_by_project(request: Request) -> JSONResponse:
    try:
        data = await get_late_uncollected_by_project()
    except OdooQueryError:
        logger.warning("KPI 5 — Odoo query failed", exc_info=True)
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
        logger.error("KPI 5 — unexpected error", exc_info=True)
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


@router.get(
    "/kpi/pending-check-exposure",
    summary="KPI 3 — Pending Check Exposure (checks received, not yet cashed)",
)
@limiter.limit("60/minute")
async def pending_check_exposure(request: Request) -> JSONResponse:
    try:
        data = await get_pending_check_exposure()
    except OdooQueryError:
        logger.warning("KPI 3 — Odoo query failed", exc_info=True)
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
        logger.error("KPI 3 — unexpected error", exc_info=True)
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


@router.get(
    "/kpi/collection-trend-6m",
    summary="KPI 6 — 6-Month Collection Trend (payment installment headers, state=post)",
)
@limiter.limit("60/minute")
async def collection_trend_6m(request: Request) -> JSONResponse:
    try:
        data = await get_collection_trend_6m()
    except OdooQueryError:
        logger.warning("KPI 6 — Odoo query failed", exc_info=True)
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
        logger.error("KPI 6 — unexpected error", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "An unexpected error occurred.",
                }
            },
        )

    ttl = data.get("cache_ttl_seconds", 3600)
    return JSONResponse(
        content=data,
        headers={
            "Cache-Control": f"private, max-age={ttl}",
            "X-Cache-Status": str(data.get("cache_status", "fresh")),
        },
    )


@router.get(
    "/kpi/collection-rate",
    summary="KPI 4 — Collection Rate MTD & YTD (payments received ÷ installments due)",
)
@limiter.limit("60/minute")
async def collection_rate(request: Request) -> JSONResponse:
    try:
        data = await get_collection_rate_mtd_ytd()
    except OdooQueryError:
        logger.warning("KPI 4 — Odoo query failed", exc_info=True)
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
        logger.error("KPI 4 — unexpected error", exc_info=True)
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


@router.get(
    "/kpi/collection-rate-by-project",
    summary="KPI 5b — Collection Rate per Project MTD & YTD (payments ÷ installments due per project)",
)
@limiter.limit("60/minute")
async def collection_rate_by_project(request: Request) -> JSONResponse:
    try:
        data = await get_collection_rate_by_project()
    except OdooQueryError:
        logger.warning("KPI 5b — Odoo query failed", exc_info=True)
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
        logger.error("KPI 5b — unexpected error", exc_info=True)
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
