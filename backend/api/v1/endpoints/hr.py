"""
HR KPI endpoints.

GET /api/v1/hr/kpi/headcount              — KPI A: Headcount
GET /api/v1/hr/kpi/tenure-distribution   — KPI B: Tenure Distribution
GET /api/v1/hr/kpi/payroll-risk-dashboard — KPI C: Payroll Risk Dashboard
GET /api/v1/hr/kpi/department-cost        — KPI D: Department Payroll Cost
GET /api/v1/hr/department/{department_id} — F2:    Department staff drill-down
"""

import asyncio

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from loguru import logger

from fastapi import Depends

from backend.api.deps import get_current_user
from backend.core.exceptions import OdooQueryError
from backend.core.limiter import limiter
from backend.modules.hr.schemas import (
    DepartmentCostResponse,
    DepartmentStaffResponse,
    HeadcountResponse,
    PayrollRiskDashboardResponse,
    TenureDistributionResponse,
)
from backend.modules.hr.services.dept_staff_service import get_department_staff
from backend.modules.hr.services.kpi_service import (
    get_department_cost,
    get_headcount,
    get_payroll_risk_dashboard,
    get_tenure_distribution,
)

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


@router.get(
    "/kpi/payroll-risk-dashboard",
    summary="KPI C — Payroll Risk Dashboard",
    response_model=PayrollRiskDashboardResponse,
)
@limiter.limit("60/minute")
async def payroll_risk_dashboard(
    request: Request,
    response: Response,
) -> dict | JSONResponse:
    try:
        data = await get_payroll_risk_dashboard()
    except OdooQueryError:
        logger.warning("HR KPI C payroll risk dashboard — Odoo query failed", exc_info=True)
        return JSONResponse(status_code=503, content=_ERR_503)
    except Exception:
        logger.error("HR KPI C payroll risk dashboard — unexpected error", exc_info=True)
        return JSONResponse(status_code=500, content=_ERR_500)

    response.headers["Cache-Control"] = "private, max-age=60"
    response.headers["X-Cache-Status"] = str(data.get("cache_status", "fresh"))
    return data


@router.get(
    "/kpi/department-cost",
    summary="KPI D — Department Payroll Cost",
    response_model=DepartmentCostResponse,
)
@limiter.limit("60/minute")
async def department_cost(
    request: Request,
    response: Response,
) -> dict | JSONResponse:
    try:
        data = await get_department_cost()
    except OdooQueryError:
        logger.warning("HR KPI D department cost — Odoo query failed", exc_info=True)
        return JSONResponse(status_code=503, content=_ERR_503)
    except Exception:
        logger.error("HR KPI D department cost — unexpected error", exc_info=True)
        return JSONResponse(status_code=500, content=_ERR_500)

    response.headers["Cache-Control"] = "private, max-age=60"
    response.headers["X-Cache-Status"] = str(data.get("cache_status", "fresh"))
    return data


@router.get(
    "/department/{department_id}",
    summary="F2 — Department staff drill-down: named employees for one department",
    response_model=DepartmentStaffResponse,
)
@limiter.limit("60/minute")
async def department_staff(
    request: Request,
    response: Response,
    department_id: int,
    _user: str = Depends(get_current_user),
) -> dict | JSONResponse:
    """Return named employees + department-level aggregates for one named department.

    Auth: HTTPBasic required — response contains employee names (PII).
    Cache-Control: private, no-store.

    Returns 400 if department_id <= 0.
    Returns 404 if department_id has no Running-contract employees (unknown dept
    or the pooled "Other" row whose dept_id is never a valid positive integer).
    Returns 503 on Odoo connectivity failure.
    """
    if department_id <= 0:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "invalid_department_id",
                    "message": "department_id must be a positive integer.",
                }
            },
        )

    try:
        staff_data, cost_data = await asyncio.gather(
            get_department_staff(department_id),
            get_department_cost(),
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "invalid_department_id", "message": str(exc)}},
        )
    except OdooQueryError:
        logger.warning(
            f"HR F2 dept staff — Odoo query failed (department_id={department_id})",
            exc_info=True,
        )
        return JSONResponse(status_code=503, content=_ERR_503)
    except Exception:
        logger.error(
            f"HR F2 dept staff — unexpected error (department_id={department_id})",
            exc_info=True,
        )
        return JSONResponse(status_code=500, content=_ERR_500)

    if not staff_data["staff"]:
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "code": "department_not_found",
                    "message": (
                        f"No Running-contract employees found for "
                        f"department_id={department_id}. "
                        "The department may not exist, have no active staff, "
                        "or be the pooled 'Other' row (not drillable)."
                    ),
                }
            },
        )

    # ── Merge department-level cost aggregates from KPI D (dept totals only) ──
    grand_total = cost_data.get("grand_total_wage") or 0.0
    dept_cost_row = next(
        (r for r in cost_data.get("rows", []) if r["department_id"] == department_id),
        None,
    )
    total_wage = dept_cost_row["total_wage"] if dept_cost_row else None
    headcount  = staff_data["headcount"]

    pct_of_total = (
        round(total_wage / grand_total * 100, 1)
        if (total_wage is not None and grand_total > 0)
        else None
    )
    avg_cost_per_head = (
        round(total_wage / headcount, 0)
        if (total_wage is not None and headcount > 0)
        else None
    )

    result = {
        **staff_data,
        "total_wage":           total_wage,
        "pct_of_total_payroll": pct_of_total,
        "avg_cost_per_head":    avg_cost_per_head,
        "currency":             "EGP",
        "basis":                "monthly",
    }

    response.headers["Cache-Control"] = "private, no-store"
    return result
