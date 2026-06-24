"""
Marketing Attribution endpoint.

GET /api/v1/marketing-attribution/overview — per-media-buyer attributed leads
    plus the 4-group outcome breakdown, campaign-driven (§3.3). Read-only.

GET /api/v1/marketing-attribution/windowed — the SAME per-media-buyer attribution
    scoped to a Cairo time window (preset 'current'/'last3', or a custom
    start_month..end_month range): every buyer with >=1 attributed lead that arose in
    the window, funnel scoped to the window with the Nov-2025 migration excluded, the
    campaign→buyer map still all-time, plus an unattributed bucket + windowed coverage.
    All-time uses /overview. Read-only. 422 on an invalid custom range (same contract
    as campaign-performance/windowed).

GET /api/v1/marketing-attribution/buyer/{buyer_id}/timeline — per-media-buyer
    TIMELINE: drill into ONE media buyer and see that buyer's leads over Cairo-local
    months — a volume trend plus a full 4-group funnel + derived maturation state per
    recent month, scoped to the buyer's all-time attributing campaigns (>=90% gate)
    with the Nov-2025 migration excluded. Read-only. 404 if the buyer_id is not the
    dominant buyer of any attributing campaign; 422 on an invalid custom range.

RBAC: module-gated at include_router level in router.py via
require_module_api("marketing_attribution"); additionally requires an
authenticated session (get_current_user). Returns 401 without a session,
403 without the module grant.
"""

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.responses import JSONResponse
from loguru import logger

from backend.api.deps import get_current_user
from backend.core.exceptions import OdooQueryError
from backend.core.limiter import limiter
from backend.modules.campaign_performance.services.timeline_service import (
    InvalidTimelineRangeError,
)
from backend.modules.marketing_attribution.schemas import (
    BuyerTimeline,
    MarketingAttributionOverview,
    MarketingAttributionWindowed,
)
from backend.modules.marketing_attribution.services.attribution_service import (
    get_attribution_overview,
    get_attribution_overview_windowed,
)
from backend.modules.marketing_attribution.services.buyer_timeline_service import (
    BuyerNotFoundError,
    get_buyer_timeline,
)

router = APIRouter(prefix="/marketing-attribution", tags=["marketing-attribution"])

_ERR_503 = {"error": {"code": "odoo_unavailable", "message": "Odoo is unavailable or the query failed. Try again shortly."}}
_ERR_500 = {"error": {"code": "internal_error", "message": "An unexpected error occurred."}}
_ERR_404 = {"error": {"code": "buyer_not_found", "message": "No media buyer found for the given buyer_id."}}


@router.get(
    "/overview",
    summary="Marketing Attribution — per-media-buyer attributed leads + 4-group outcomes",
    response_model=MarketingAttributionOverview,
)
@limiter.limit("60/minute")
async def overview(
    request: Request,
    response: Response,
    _user: str = Depends(get_current_user),
) -> dict | JSONResponse:
    try:
        data = await get_attribution_overview()
    except OdooQueryError:
        logger.warning("Marketing attribution overview — Odoo query failed", exc_info=True)
        return JSONResponse(status_code=503, content=_ERR_503)
    except Exception:
        logger.error("Marketing attribution overview — unexpected error", exc_info=True)
        return JSONResponse(status_code=500, content=_ERR_500)

    response.headers["Cache-Control"] = "private, max-age=60"
    response.headers["X-Cache-Status"] = str(data.get("cache_status", "fresh"))
    return data


@router.get(
    "/windowed",
    summary="Marketing Attribution — per-media-buyer attribution scoped to a Cairo time window",
    response_model=MarketingAttributionWindowed,
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
    # Windowed buyer attribution: every media buyer with >=1 attributed lead that arose
    # in the Cairo window (migration excluded), funnel scoped to the window, the
    # campaign→buyer map still all-time. "all" is NOT served here — the un-windowed
    # /overview owns it. The custom-range validation contract is shared with the campaign
    # windowing (same 422).
    try:
        data = await get_attribution_overview_windowed(
            window=window,
            start_month=start_month,
            end_month=end_month,
        )
    except InvalidTimelineRangeError as exc:
        logger.info(f"Marketing attribution windowed — invalid window/range: {exc}")
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "invalid_range", "message": str(exc)}},
        )
    except OdooQueryError:
        logger.warning("Marketing attribution windowed — Odoo query failed", exc_info=True)
        return JSONResponse(status_code=503, content=_ERR_503)
    except Exception:
        logger.error("Marketing attribution windowed — unexpected error", exc_info=True)
        return JSONResponse(status_code=500, content=_ERR_500)

    response.headers["Cache-Control"] = "private, max-age=60"
    response.headers["X-Cache-Status"] = str(data.get("cache_status", "fresh"))
    return data


@router.get(
    "/buyer/{buyer_id}/timeline",
    summary="Marketing Attribution — per-media-buyer timeline (volume trend + monthly funnels)",
    response_model=BuyerTimeline,
)
@limiter.limit("60/minute")
async def buyer_timeline(
    request: Request,
    response: Response,
    buyer_id: int = Path(..., gt=0, description="res.users id of the media buyer to drill into"),
    months: int = Query(3, ge=1, le=12, description="trailing Cairo months reported with a full funnel (ignored when a custom range is given)"),
    start_month: str | None = Query(None, description="custom range start, Cairo 'YYYY-MM' (both-or-neither with end_month)"),
    end_month: str | None = Query(None, description="custom range end, Cairo 'YYYY-MM' (both-or-neither with start_month)"),
    _user: str = Depends(get_current_user),
) -> dict | JSONResponse:
    # Per-media-buyer drill-in (Slice 3): the buyer's leads over Cairo months, scoped to
    # the buyer's all-time attributing campaigns (>=90% gate), migration excluded. Same
    # window/custom-range contract + auth/RBAC/rate-limit/Cache-Control as /windowed.
    try:
        data = await get_buyer_timeline(
            buyer_id=buyer_id,
            window_months=months,
            start_month=start_month,
            end_month=end_month,
        )
    except InvalidTimelineRangeError as exc:
        logger.info(f"Buyer timeline — invalid custom range: {exc}")
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "invalid_range", "message": str(exc)}},
        )
    except BuyerNotFoundError:
        logger.info(f"Buyer timeline — buyer_id={buyer_id} not found")
        return JSONResponse(status_code=404, content=_ERR_404)
    except OdooQueryError:
        logger.warning("Marketing attribution buyer timeline — Odoo query failed", exc_info=True)
        return JSONResponse(status_code=503, content=_ERR_503)
    except Exception:
        logger.error("Marketing attribution buyer timeline — unexpected error", exc_info=True)
        return JSONResponse(status_code=500, content=_ERR_500)

    response.headers["Cache-Control"] = "private, max-age=60"
    response.headers["X-Cache-Status"] = str(data.get("cache_status", "fresh"))
    return data
