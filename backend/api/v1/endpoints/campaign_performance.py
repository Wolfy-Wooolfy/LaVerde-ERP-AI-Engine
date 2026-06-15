"""
Campaign Performance endpoint.

GET /api/v1/campaign-performance/overview — per-campaign funnel (Level 1): every
    campaign's 4-group breakdown (count + %), sorted by lead volume desc, with the
    dominant media buyer shown per campaign, a long-tail aggregate, and the junk
    "None" campaign surfaced as a data-quality flag. Read-only.

RBAC: module-gated at include_router level in router.py via
require_module_api("campaign_performance"); additionally requires an
authenticated session (get_current_user). Returns 401 without a session,
403 without the module grant.
"""

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from loguru import logger

from backend.api.deps import get_current_user
from backend.core.exceptions import OdooQueryError
from backend.core.limiter import limiter
from backend.modules.campaign_performance.schemas import (
    CampaignPerformanceOverview,
)
from backend.modules.campaign_performance.services.campaign_service import (
    get_campaign_performance_overview,
)

router = APIRouter(prefix="/campaign-performance", tags=["campaign-performance"])

_ERR_503 = {"error": {"code": "odoo_unavailable", "message": "Odoo is unavailable or the query failed. Try again shortly."}}
_ERR_500 = {"error": {"code": "internal_error", "message": "An unexpected error occurred."}}


@router.get(
    "/overview",
    summary="Campaign Performance — per-campaign funnel + dominant media buyer",
    response_model=CampaignPerformanceOverview,
)
@limiter.limit("60/minute")
async def overview(
    request: Request,
    response: Response,
    _user: str = Depends(get_current_user),
) -> dict | JSONResponse:
    try:
        data = await get_campaign_performance_overview()
    except OdooQueryError:
        logger.warning("Campaign performance overview — Odoo query failed", exc_info=True)
        return JSONResponse(status_code=503, content=_ERR_503)
    except Exception:
        logger.error("Campaign performance overview — unexpected error", exc_info=True)
        return JSONResponse(status_code=500, content=_ERR_500)

    response.headers["Cache-Control"] = "private, max-age=60"
    response.headers["X-Cache-Status"] = str(data.get("cache_status", "fresh"))
    return data
