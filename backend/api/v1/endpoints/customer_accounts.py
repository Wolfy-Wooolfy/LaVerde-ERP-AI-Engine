"""
Customer Accounts KPI endpoints.

GET /api/v1/customer-accounts/kpi/total-receivables          — KPI A: Total Customer Receivables
GET /api/v1/customer-accounts/kpi/top-overdue-customers      — KPI B: Top Overdue Customers
GET /api/v1/customer-accounts/kpi/unallocated-wallet-balance — KPI C: Unallocated Wallet Balance
GET /api/v1/customer-accounts/refunds/summary                — Refunds alert section
GET /api/v1/customer-accounts/customer/{partner_id}          — M3-S6: Customer drill-down
"""

import uuid
from typing import Literal, Optional

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse
from loguru import logger

from backend.core.exceptions import OdooQueryError
from backend.core.limiter import limiter
from backend.modules.customer_accounts.schemas import (
    CustomerDrilldownResponse,
    RefundsSummaryResponse,
    TopOverdueCustomersResponse,
    TotalReceivablesResponse,
    UnallocatedWalletBalanceResponse,
)
from backend.modules.customer_accounts.services.drilldown_service import (
    get_customer_drilldown,
)
from backend.modules.customer_accounts.services.kpi_service import (
    get_refunds_summary,
    get_top_overdue_customers,
    get_total_customer_receivables,
    get_unallocated_wallet_balance,
)

router = APIRouter(prefix="/customer-accounts", tags=["customer_accounts"])

_ERR_503 = {"error": {"code": "odoo_unavailable", "message": "Odoo is unavailable or the query failed. Try again shortly."}}
_ERR_500 = {"error": {"code": "internal_error", "message": "An unexpected error occurred."}}


def _req_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or uuid.uuid4().hex


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


@router.get(
    "/kpi/top-overdue-customers",
    summary="KPI B — Top Overdue Customers",
    response_model=TopOverdueCustomersResponse,
)
@limiter.limit("60/minute")
async def top_overdue_customers(
    request: Request,
    response: Response,
) -> dict | JSONResponse:
    try:
        data = await get_top_overdue_customers()
    except OdooQueryError:
        logger.warning("KPI B — Odoo query failed", exc_info=True)
        return JSONResponse(status_code=503, content=_ERR_503)
    except Exception:
        logger.error("KPI B — unexpected error", exc_info=True)
        return JSONResponse(status_code=500, content=_ERR_500)

    response.headers["Cache-Control"] = "private, max-age=60"
    response.headers["X-Cache-Status"] = str(data.get("cache_status", "fresh"))
    return data


@router.get(
    "/kpi/unallocated-wallet-balance",
    summary="KPI C — Unallocated Wallet Balance",
    response_model=UnallocatedWalletBalanceResponse,
)
@limiter.limit("60/minute")
async def unallocated_wallet_balance(
    request: Request,
    response: Response,
) -> dict | JSONResponse:
    try:
        data = await get_unallocated_wallet_balance()
    except OdooQueryError:
        logger.warning("KPI C — Odoo query failed", exc_info=True)
        return JSONResponse(status_code=503, content=_ERR_503)
    except Exception:
        logger.error("KPI C — unexpected error", exc_info=True)
        return JSONResponse(status_code=500, content=_ERR_500)

    response.headers["Cache-Control"] = "private, max-age=60"
    response.headers["X-Cache-Status"] = str(data.get("cache_status", "fresh"))
    return data


@router.get(
    "/refunds/summary",
    summary="Refunds alert section summary",
    response_model=RefundsSummaryResponse,
)
@limiter.limit("60/minute")
async def refunds_summary(
    request: Request,
    response: Response,
) -> dict | JSONResponse:
    try:
        data = await get_refunds_summary()
    except OdooQueryError:
        logger.warning("Refunds — Odoo query failed", exc_info=True)
        return JSONResponse(status_code=503, content=_ERR_503)
    except Exception:
        logger.error("Refunds — unexpected error", exc_info=True)
        return JSONResponse(status_code=500, content=_ERR_500)

    response.headers["Cache-Control"] = "private, max-age=60"
    response.headers["X-Cache-Status"] = str(data.get("cache_status", "fresh"))
    return data


@router.get(
    "/customer/{partner_id}",
    summary="M3-S6 — Customer drill-down: full account statement for one customer",
    response_model=CustomerDrilldownResponse,
)
@limiter.limit("60/minute")
async def customer_drilldown(
    request: Request,
    partner_id: int,
    cursor: Optional[str] = Query(default=None),
    page_size: int = Query(default=50, ge=1, le=200),
    sort_by: Literal["date", "amount", "due_amount"] = Query(default="date"),
    sort_dir: Literal["asc", "desc"] = Query(default="asc"),
) -> dict | JSONResponse:
    req_id = _req_id(request)
    try:
        data = await get_customer_drilldown(
            partner_id=partner_id,
            request_id=req_id,
            cursor=cursor,
            page_size=page_size,
            sort_by=sort_by,
            sort_dir=sort_dir,
        )
    except AssertionError as exc:
        logger.error(
            f"Customer drill-down integrity assertion failed "
            f"(partner_id={partner_id}): {exc}",
            exc_info=True,
        )
        return JSONResponse(status_code=500, content=_ERR_500)
    except OdooQueryError:
        logger.warning(
            f"Customer drill-down — Odoo query failed (partner_id={partner_id})",
            exc_info=True,
        )
        return JSONResponse(status_code=503, content=_ERR_503)
    except Exception:
        logger.error(
            f"Customer drill-down — unexpected error (partner_id={partner_id})",
            exc_info=True,
        )
        return JSONResponse(status_code=500, content=_ERR_500)

    return data
