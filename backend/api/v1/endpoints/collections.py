"""
Collections KPI endpoints.

GET /api/v1/collections/kpi/late-uncollected              — KPI 2: Late Uncollected
GET /api/v1/collections/kpi/total-portfolio-value         — KPI 1: Total Portfolio Value
GET /api/v1/collections/kpi/late-uncollected-by-project   — KPI 5: Late Uncollected per project
GET /api/v1/collections/kpi/pending-check-exposure        — KPI 3: Pending Check Exposure
GET /api/v1/collections/kpi/collection-trend-6m           — KPI 6: 6-Month Collection Trend
GET /api/v1/collections/kpi/collection-rate               — KPI 4: Collection Rate MTD & YTD
GET /api/v1/collections/kpi/collection-rate-by-project    — KPI 5b: Collection Rate per Project
GET /api/v1/collections/kpi/expected-forecast             — KPI 7: Expected Collections Forecast

Stage 5 — Drill-down endpoints (Decision 14.1–14.12, E1/E2/E3):
GET /api/v1/collections/drilldown/late                    — KPI 2 late installments
GET /api/v1/collections/drilldown/forecast/{bucket}       — KPI 7 forecast bucket
GET /api/v1/collections/drilldown/portfolio               — KPI 1 customer × project
GET /api/v1/collections/drilldown/project/{project_id}    — KPI 5 late by project
GET /api/v1/collections/drilldown/trend/{month}           — KPI 6 installments by month
"""

import uuid
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.responses import JSONResponse
from loguru import logger

from backend.api.deps import get_current_user
from backend.core.exceptions import OdooQueryError
from backend.core.limiter import limiter
from backend.modules.collections.schemas import (
    ExpectedCollectionsForecastResponse,
    ForecastDrilldownResponse,
    LateDrilldownResponse,
    LateUncollectedResponse,
    PortfolioDrilldownResponse,
    ProjectDrilldownResponse,
    TrendDrilldownResponse,
)
from backend.modules.collections.services.drilldown_service import (
    get_forecast_drilldown,
    get_late_drilldown,
    get_portfolio_drilldown,
    get_project_drilldown,
    get_trend_drilldown,
)
from backend.modules.collections.services.kpi_service import (
    get_collection_rate_by_project,
    get_collection_rate_mtd_ytd,
    get_collection_trend_6m,
    get_expected_collections_forecast,
    get_late_uncollected,
    get_late_uncollected_by_project,
    get_pending_check_exposure,
    get_total_portfolio_value,
)

# Shared error bodies reused across drill-down endpoints.
_ERR_503 = {"error": {"code": "odoo_unavailable", "message": "Odoo is unavailable or the query failed. Try again shortly."}}
_ERR_500 = {"error": {"code": "internal_error", "message": "An unexpected error occurred."}}

router = APIRouter(prefix="/collections", tags=["collections"])


@router.get(
    "/kpi/late-uncollected",
    summary="KPI 2 — Late Uncollected receivables",
    response_model=LateUncollectedResponse,
)
@limiter.limit("60/minute")
async def late_uncollected(
    request: Request,
    response: Response,
    _user: str = Depends(get_current_user),
) -> dict | JSONResponse:
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

    response.headers["Cache-Control"] = "private, max-age=60"
    response.headers["X-Cache-Status"] = str(data.get("cache_status", "fresh"))
    return data


@router.get("/kpi/total-portfolio-value", summary="KPI 1 — Total Portfolio Value")
@limiter.limit("60/minute")
async def total_portfolio_value(
    request: Request,
    _user: str = Depends(get_current_user),
) -> JSONResponse:
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
async def late_uncollected_by_project(
    request: Request,
    _user: str = Depends(get_current_user),
) -> JSONResponse:
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
async def pending_check_exposure(
    request: Request,
    _user: str = Depends(get_current_user),
) -> JSONResponse:
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
async def collection_trend_6m(
    request: Request,
    _user: str = Depends(get_current_user),
) -> JSONResponse:
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
async def collection_rate(
    request: Request,
    _user: str = Depends(get_current_user),
) -> JSONResponse:
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
async def collection_rate_by_project(
    request: Request,
    _user: str = Depends(get_current_user),
) -> JSONResponse:
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


@router.get(
    "/kpi/expected-forecast",
    summary="KPI 7 — Expected Collections Forecast (4 forward-looking calendar buckets)",
    response_model=ExpectedCollectionsForecastResponse,
)
@limiter.limit("60/minute")
async def expected_collections_forecast(
    request: Request,
    response: Response,
    _user: str = Depends(get_current_user),
) -> dict | JSONResponse:
    try:
        data = await get_expected_collections_forecast()
    except OdooQueryError:
        logger.warning("KPI 7 — Odoo query failed", exc_info=True)
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
        logger.error("KPI 7 — unexpected error", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "An unexpected error occurred.",
                }
            },
        )

    response.headers["Cache-Control"] = "private, max-age=60"
    response.headers["X-Cache-Status"] = str(data.get("cache_status", "fresh"))
    return data


# ══════════════════════════════════════════════════════════════════════════════
# Stage 5 — Drill-down endpoints
# E1: DrilldownEnvelope {version, data, meta}
# E2: cursor-based pagination (keyset for installment rows, offset for portfolio)
# E3: X-Request-ID propagated from header; UUID4 generated if absent
# No caching (Decision 14.7). No X-Cache-Status header on drill-down routes.
# ══════════════════════════════════════════════════════════════════════════════


def _req_id(request: Request) -> str:
    # Middleware has already resolved the client X-Request-ID (or generated .hex UUID)
    # and stored it in request.state.request_id — single source of truth per request.
    # Fallback handles direct calls in tests that bypass the middleware.
    return getattr(request.state, "request_id", None) or uuid.uuid4().hex


@router.get(
    "/drilldown/late",
    summary="Drill-down: KPI 2 — Late Uncollected installments (paginated, cursor-based)",
    response_model=LateDrilldownResponse,
)
@limiter.limit("60/minute")
async def drilldown_late(
    request: Request,
    response: Response,
    page_size: int = Query(default=50, ge=1, le=200),
    cursor: Optional[str] = Query(default=None),
    sort_by: Literal["date", "amount", "due_amount"] = Query(default="due_amount"),
    sort_dir: Literal["asc", "desc"] = Query(default="desc"),
    payment_state: Optional[Literal["unpaid", "partial"]] = Query(default=None),
    has_pending_cheque: Optional[bool] = Query(default=None, description="True → cheque>0 only; False → cheque=0 only; omit for all"),
    _user: str = Depends(get_current_user),
) -> dict | JSONResponse:
    req_id = _req_id(request)
    try:
        data = await get_late_drilldown(
            request_id=req_id,
            cursor=cursor,
            page_size=page_size,
            sort_by=sort_by,
            sort_dir=sort_dir,
            payment_state=payment_state,
            has_pending_cheque=has_pending_cheque,
        )
    except OdooQueryError:
        logger.warning("Drilldown late — Odoo query failed", exc_info=True)
        return JSONResponse(status_code=503, content=_ERR_503, headers={"X-Request-ID": req_id})
    except Exception:
        logger.error("Drilldown late — unexpected error", exc_info=True)
        return JSONResponse(status_code=500, content=_ERR_500, headers={"X-Request-ID": req_id})

    response.headers["X-Request-ID"] = req_id
    return data


@router.get(
    "/drilldown/forecast/{bucket}",
    summary="Drill-down: KPI 7 — Expected Collections Forecast bucket (paginated, cursor-based)",
    response_model=ForecastDrilldownResponse,
)
@limiter.limit("60/minute")
async def drilldown_forecast(
    request: Request,
    response: Response,
    bucket: Literal["month", "quarter", "half", "year"],
    page_size: int = Query(default=50, ge=1, le=200),
    cursor: Optional[str] = Query(default=None),
    sort_by: Literal["date", "amount", "due_amount"] = Query(default="due_amount"),
    sort_dir: Literal["asc", "desc"] = Query(default="desc"),
    payment_state: Optional[Literal["unpaid", "partial"]] = Query(default=None),
    has_pending_cheque: Optional[bool] = Query(default=None, description="True → cheque>0 only; False → cheque=0 only; omit for all"),
    _user: str = Depends(get_current_user),
) -> dict | JSONResponse:
    req_id = _req_id(request)
    try:
        data = await get_forecast_drilldown(
            request_id=req_id,
            bucket_url_key=bucket,
            cursor=cursor,
            page_size=page_size,
            sort_by=sort_by,
            sort_dir=sort_dir,
            payment_state=payment_state,
            has_pending_cheque=has_pending_cheque,
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "invalid_param", "message": str(exc)}},
            headers={"X-Request-ID": req_id},
        )
    except OdooQueryError:
        logger.warning("Drilldown forecast — Odoo query failed", exc_info=True)
        return JSONResponse(status_code=503, content=_ERR_503, headers={"X-Request-ID": req_id})
    except Exception:
        logger.error("Drilldown forecast — unexpected error", exc_info=True)
        return JSONResponse(status_code=500, content=_ERR_500, headers={"X-Request-ID": req_id})

    response.headers["X-Request-ID"] = req_id
    return data


@router.get(
    "/drilldown/portfolio",
    summary="Drill-down: KPI 1 — Total Portfolio, customer × project breakdown (paginated, offset cursor)",
    response_model=PortfolioDrilldownResponse,
)
@limiter.limit("60/minute")
async def drilldown_portfolio(
    request: Request,
    response: Response,
    page_size: int = Query(default=50, ge=1, le=200),
    cursor: Optional[str] = Query(default=None),
    project_id: Optional[int] = Query(default=None, ge=1, le=3, description="Filter to a specific project (1=New Capital, 2=Cassette, 3=La puerta)"),
    _user: str = Depends(get_current_user),
) -> dict | JSONResponse:
    req_id = _req_id(request)
    try:
        data = await get_portfolio_drilldown(
            request_id=req_id,
            cursor=cursor,
            page_size=page_size,
            project_id=project_id,
        )
    except OdooQueryError:
        logger.warning("Drilldown portfolio — Odoo query failed", exc_info=True)
        return JSONResponse(status_code=503, content=_ERR_503, headers={"X-Request-ID": req_id})
    except Exception:
        logger.error("Drilldown portfolio — unexpected error", exc_info=True)
        return JSONResponse(status_code=500, content=_ERR_500, headers={"X-Request-ID": req_id})

    response.headers["X-Request-ID"] = req_id
    return data


@router.get(
    "/drilldown/project/{project_id}",
    summary="Drill-down: KPI 5 — Late Uncollected for one project (paginated, cursor-based)",
    response_model=ProjectDrilldownResponse,
)
@limiter.limit("60/minute")
async def drilldown_project(
    request: Request,
    response: Response,
    project_id: int = Path(ge=1, le=3, description="1=New Capital, 2=Cassette, 3=La puerta"),
    page_size: int = Query(default=50, ge=1, le=200),
    cursor: Optional[str] = Query(default=None),
    sort_by: Literal["date", "amount", "due_amount"] = Query(default="due_amount"),
    sort_dir: Literal["asc", "desc"] = Query(default="desc"),
    payment_state: Optional[Literal["unpaid", "partial"]] = Query(default=None),
    has_pending_cheque: Optional[bool] = Query(default=None, description="True → cheque>0 only; False → cheque=0 only; omit for all"),
    _user: str = Depends(get_current_user),
) -> dict | JSONResponse:
    req_id = _req_id(request)
    try:
        data = await get_project_drilldown(
            request_id=req_id,
            project_id=project_id,
            cursor=cursor,
            page_size=page_size,
            sort_by=sort_by,
            sort_dir=sort_dir,
            payment_state=payment_state,
            has_pending_cheque=has_pending_cheque,
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "invalid_param", "message": str(exc)}},
            headers={"X-Request-ID": req_id},
        )
    except OdooQueryError:
        logger.warning("Drilldown project — Odoo query failed", exc_info=True)
        return JSONResponse(status_code=503, content=_ERR_503, headers={"X-Request-ID": req_id})
    except Exception:
        logger.error("Drilldown project — unexpected error", exc_info=True)
        return JSONResponse(status_code=500, content=_ERR_500, headers={"X-Request-ID": req_id})

    response.headers["X-Request-ID"] = req_id
    return data


@router.get(
    "/drilldown/trend/{month}",
    summary="Drill-down: KPI 6 — Collection Trend, installments due in one calendar month (paginated, cursor-based)",
    response_model=TrendDrilldownResponse,
)
@limiter.limit("60/minute")
async def drilldown_trend(
    request: Request,
    response: Response,
    month: str = Path(pattern=r"^\d{4}-\d{2}$", description="Calendar month in YYYY-MM format (trailing 6 months)"),
    page_size: int = Query(default=50, ge=1, le=200),
    cursor: Optional[str] = Query(default=None),
    sort_by: Literal["date", "amount", "due_amount"] = Query(default="due_amount"),
    sort_dir: Literal["asc", "desc"] = Query(default="desc"),
    payment_state: Optional[Literal["unpaid", "partial"]] = Query(default=None),
    has_pending_cheque: Optional[bool] = Query(default=None, description="True → cheque>0 only; False → cheque=0 only; omit for all"),
    _user: str = Depends(get_current_user),
) -> dict | JSONResponse:
    req_id = _req_id(request)
    try:
        data = await get_trend_drilldown(
            request_id=req_id,
            month=month,
            cursor=cursor,
            page_size=page_size,
            sort_by=sort_by,
            sort_dir=sort_dir,
            payment_state=payment_state,
            has_pending_cheque=has_pending_cheque,
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "invalid_param", "message": str(exc)}},
            headers={"X-Request-ID": req_id},
        )
    except OdooQueryError:
        logger.warning("Drilldown trend — Odoo query failed", exc_info=True)
        return JSONResponse(status_code=503, content=_ERR_503, headers={"X-Request-ID": req_id})
    except Exception:
        logger.error("Drilldown trend — unexpected error", exc_info=True)
        return JSONResponse(status_code=500, content=_ERR_500, headers={"X-Request-ID": req_id})

    response.headers["X-Request-ID"] = req_id
    return data
