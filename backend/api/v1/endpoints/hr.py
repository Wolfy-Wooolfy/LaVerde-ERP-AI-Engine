"""
HR KPI endpoints.

GET /api/v1/hr/kpi/headcount           — KPI A: Headcount
GET /api/v1/hr/kpi/tenure-distribution — KPI B: Tenure Distribution
"""

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from loguru import logger

from backend.core.exceptions import OdooQueryError
from backend.core.limiter import limiter
from backend.modules.hr.schemas import HeadcountResponse, TenureDistributionResponse
from backend.modules.hr.services.kpi_service import get_headcount, get_tenure_distribution

router = APIRouter(prefix="/hr", tags=["hr"])

_ERR_503 = {"error": {"code": "odoo_unavailable", "message": "Odoo is unavailable or the query failed. Try again shortly."}}
_ERR_500 = {"error": {"code": "internal_error", "message": "An unexpected error occurred."}}


@router.get(
    "/kpi/headcount",
    summary="KPI A — Headcount",
    response_model=HeadcountResponse,
)
@limiter.limit("60/minute")
async def headcount(
    request: Request,
    response: Response,
) -> dict | JSONResponse:
    try:
        data = await get_headcount()
    except OdooQueryError:
        logger.warning("HR KPI A headcount — Odoo query failed", exc_info=True)
        return JSONResponse(status_code=503, content=_ERR_503)
    except Exception:
        logger.error("HR KPI A headcount — unexpected error", exc_info=True)
        return JSONResponse(status_code=500, content=_ERR_500)

    response.headers["Cache-Control"] = "private, max-age=60"
    response.headers["X-Cache-Status"] = str(data.get("cache_status", "fresh"))
    return data


@router.get(
    "/kpi/tenure-distribution",
    summary="KPI B — Tenure Distribution",
    response_model=TenureDistributionResponse,
)
@limiter.limit("60/minute")
async def tenure_distribution(
    request: Request,
    response: Response,
) -> dict | JSONResponse:
    try:
        data = await get_tenure_distribution()
    except OdooQueryError:
        logger.warning("HR KPI B tenure distribution — Odoo query failed", exc_info=True)
        return JSONResponse(status_code=503, content=_ERR_503)
    except Exception:
        logger.error("HR KPI B tenure distribution — unexpected error", exc_info=True)
        return JSONResponse(status_code=500, content=_ERR_500)

    response.headers["Cache-Control"] = "private, max-age=60"
    response.headers["X-Cache-Status"] = str(data.get("cache_status", "fresh"))
    return data
