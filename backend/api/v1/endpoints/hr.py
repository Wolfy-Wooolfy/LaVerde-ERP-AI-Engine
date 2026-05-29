"""
HR KPI endpoints.

GET /api/v1/hr/kpi/headcount  — KPI A: Headcount
"""

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/hr", tags=["hr"])

_ERR_501 = {
    "error": {
        "code": "not_implemented",
        "message": "Not yet implemented.",
    }
}


@router.get(
    "/kpi/headcount",
    summary="KPI A — Headcount",
)
async def headcount(request: Request, response: Response) -> JSONResponse:
    return JSONResponse(status_code=501, content=_ERR_501)
