"""
Marketing Attribution endpoint.

GET /api/v1/marketing-attribution/overview — per-media-buyer attributed leads
    plus the 4-group outcome breakdown, campaign-driven (§3.3). Read-only.

RBAC: module-gated at include_router level in router.py via
require_module_api("marketing_attribution"); additionally requires an
authenticated session (get_current_user). Returns 401 without a session,
403 without the module grant.
"""

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from loguru import logger

from backend.api.deps import get_current_user
from backend.core.exceptions import OdooQueryError
from backend.core.limiter import limiter
from backend.modules.marketing_attribution.schemas import (
    MarketingAttributionOverview,
)
from backend.modules.marketing_attribution.services.attribution_service import (
    get_attribution_overview,
)

router = APIRouter(prefix="/marketing-attribution", tags=["marketing-attribution"])

_ERR_503 = {"error": {"code": "odoo_unavailable", "message": "Odoo is unavailable or the query failed. Try again shortly."}}
_ERR_500 = {"error": {"code": "internal_error", "message": "An unexpected error occurred."}}


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
