"""
Campaign Performance endpoint.

GET /api/v1/campaign-performance/overview — per-campaign funnel (Level 1): every
    campaign's 4-group breakdown (count + %), sorted by lead volume desc, with the
    dominant media buyer shown per campaign, a long-tail aggregate, and the junk
    "None" campaign surfaced as a data-quality flag. Read-only.

GET /api/v1/campaign-performance/timeline — per-campaign TIMELINE (Level 2): drill
    into ONE campaign and see its leads grouped over Cairo-local months — a volume
    trend plus a full 4-group funnel + derived maturation state per recent month.
    The legacy CRM migration is excluded dynamically. Read-only. 404 if the
    campaign_id resolves to no utm.campaign record.

RBAC: module-gated at include_router level in router.py via
require_module_api("campaign_performance"); additionally requires an
authenticated session (get_current_user). Returns 401 without a session,
403 without the module grant.
"""

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from loguru import logger

from backend.api.deps import get_current_user
from backend.core.exceptions import OdooQueryError
from backend.core.limiter import limiter
from backend.modules.campaign_performance.schemas import (
    CampaignPerformanceOverview,
    CampaignTimeline,
)
from backend.modules.campaign_performance.services.campaign_service import (
    get_campaign_performance_overview,
)
from backend.modules.campaign_performance.services.timeline_service import (
    CampaignNotFoundError,
    get_campaign_timeline,
)

router = APIRouter(prefix="/campaign-performance", tags=["campaign-performance"])

_ERR_503 = {"error": {"code": "odoo_unavailable", "message": "Odoo is unavailable or the query failed. Try again shortly."}}
_ERR_500 = {"error": {"code": "internal_error", "message": "An unexpected error occurred."}}
_ERR_404 = {"error": {"code": "campaign_not_found", "message": "No campaign found for the given campaign_id."}}


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


@router.get(
    "/timeline",
    summary="Campaign Performance — per-campaign timeline (volume trend + monthly funnels)",
    response_model=CampaignTimeline,
)
@limiter.limit("60/minute")
async def timeline(
    request: Request,
    response: Response,
    campaign_id: int = Query(..., gt=0, description="utm.campaign id to drill into"),
    months: int = Query(3, ge=1, le=12, description="trailing Cairo months reported with a full funnel"),
    _user: str = Depends(get_current_user),
) -> dict | JSONResponse:
    try:
        data = await get_campaign_timeline(campaign_id=campaign_id, window_months=months)
    except CampaignNotFoundError:
        logger.info(f"Campaign timeline — campaign_id={campaign_id} not found")
        return JSONResponse(status_code=404, content=_ERR_404)
    except OdooQueryError:
        logger.warning("Campaign performance timeline — Odoo query failed", exc_info=True)
        return JSONResponse(status_code=503, content=_ERR_503)
    except Exception:
        logger.error("Campaign performance timeline — unexpected error", exc_info=True)
        return JSONResponse(status_code=500, content=_ERR_500)

    response.headers["Cache-Control"] = "private, max-age=60"
    response.headers["X-Cache-Status"] = str(data.get("cache_status", "fresh"))
    return data
