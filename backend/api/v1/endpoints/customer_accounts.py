"""
Customer Accounts KPI endpoints.

GET /api/v1/customer-accounts/kpi/total-receivables  — KPI A: Total Customer Receivables
"""

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from loguru import logger

from backend.core.exceptions import OdooQueryError
from backend.core.limiter import limiter
from backend.modules.customer_accounts.schemas import TotalReceivablesResponse
from backend.modules.customer_accounts.services.kpi_service import (
    get_total_customer_receivables,
)

router = APIRouter(prefix="/customer-accounts", tags=["customer_accounts"])

_ERR_503 = {"error": {"code": "odoo_unavailable", "message": "Odoo is unavailable or the query failed. Try again shortly."}}
_ERR_500 = {"error": {"code": "internal_error", "message": "An unexpected error occurred."}}


@router.get(
    "/kpi/total-receivables",
    summary="KPI A — Total Customer Receivables",
    response_model=TotalReceivablesResponse,
)
@limiter.limit("60/minute")
async def total_customer_receivables(
    request: Request,
    response: Response,
) -> dict | JSONResponse:
    try:
        data = await get_total_customer_receivables()
    except OdooQueryError:
        logger.warning("KPI A — Odoo query failed", exc_info=True)
        return JSONResponse(status_code=503, content=_ERR_503)
    except Exception:
        logger.error("KPI A — unexpected error", exc_info=True)
        return JSONResponse(status_code=500, content=_ERR_500)

    response.headers["Cache-Control"] = "private, max-age=60"
    response.headers["X-Cache-Status"] = str(data.get("cache_status", "fresh"))
    return data
