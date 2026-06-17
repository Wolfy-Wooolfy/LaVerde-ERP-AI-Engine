"""
Campaign Performance endpoint.

GET /api/v1/campaign-performance/overview — per-campaign funnel (Level 1): every
    campaign's 4-group breakdown (count + %), sorted by lead volume desc, with the
    dominant media buyer shown per campaign, a long-tail aggregate, and the junk
    "None" campaign surfaced as a data-quality flag. Read-only.

GET /api/v1/campaign-performance/windowed — the SAME Level-1 list scoped to a Cairo
    time window (preset 'current'/'last3', or a custom start_month..end_month range):
    every campaign with >=1 lead that arose in the window, funnel scoped to the
    window with the Nov-2025 migration excluded, buyer cell still all-time. No
    long-tail; zero-activity campaigns hidden. All-time uses /overview. Read-only.
    422 on an invalid custom range (same contract as /timeline).

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
    CampaignPerformanceWindowed,
    CampaignTimeline,
)
from backend.modules.campaign_performance.services.campaign_service import (
    get_campaign_performance_overview,
    get_campaign_performance_windowed,
)
from backend.modules.campaign_performance.services.timeline_service import (
    CampaignNotFoundError,
    InvalidTimelineRangeError,
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
    "/windowed",
    summary="Campaign Performance — the whole list scoped to a Cairo time window",
    response_model=CampaignPerformanceWindowed,
)
@limiter.limit("60/minute")
async def windowed(
    request: Request,
    response: Response,
    window: str = Query("last3", description="dated preset: 'current' or 'last3' (ignored when a custom range is given). For all-time use /overview."),
    start_month: str | None = Query(None, description="custom range start, Cairo 'YYYY-MM' (both-or-neither with end_month)"),
    end_month: str | None = Query(None, description="custom range end, Cairo 'YYYY-MM' (both-or-neither with start_month)"),
    _user: str = Depends(get_current_user),
) -> dict | JSONResponse:
    # Windowed Level-1 list: every campaign with >=1 lead that arose in the Cairo
    # window (migration excluded), funnel scoped to the window, buyer cell still
    # all-time. "all" is NOT served here — the un-windowed /overview owns it. The
    # custom-range validation contract is shared with the timeline (same 422).
    try:
        data = await get_campaign_performance_windowed(
            window=window,
            start_month=start_month,
            end_month=end_month,
        )
    except InvalidTimelineRangeError as exc:
        logger.info(f"Campaign windowed — invalid window/range: {exc}")
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "invalid_range", "message": str(exc)}},
        )
    except OdooQueryError:
        logger.warning("Campaign performance windowed — Odoo query failed", exc_info=True)
        return JSONResponse(status_code=503, content=_ERR_503)
    except Exception:
        logger.error("Campaign performance windowed — unexpected error", exc_info=True)
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
    months: int = Query(3, ge=1, le=12, description="trailing Cairo months reported with a full funnel (ignored when a custom range is given)"),
    start_month: str | None = Query(None, description="custom range start, Cairo 'YYYY-MM' (both-or-neither with end_month)"),
    end_month: str | None = Query(None, description="custom range end, Cairo 'YYYY-MM' (both-or-neither with start_month)"),
    _user: str = Depends(get_current_user),
) -> dict | JSONResponse:
    try:
        data = await get_campaign_timeline(
            campaign_id=campaign_id,
            window_months=months,
            start_month=start_month,
            end_month=end_month,
        )
    except InvalidTimelineRangeError as exc:
        logger.info(f"Campaign timeline — invalid custom range: {exc}")
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "invalid_range", "message": str(exc)}},
        )
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
