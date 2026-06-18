"""
Projects Inventory endpoint (Slice 1 — Inventory & Availability).

GET /api/v1/projects-inventory/overview — unit counts by sales status, overall and
    per project (counts only; no pricing/area/value). Read-only.

RBAC: module-gated at include_router level in router.py via
require_module_api("projects_inventory"); additionally requires an authenticated
session (get_current_user). Returns 401 without a session, 403 without the module
grant.
"""

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from loguru import logger

from backend.api.deps import get_current_user
from backend.core.exceptions import OdooQueryError
from backend.core.limiter import limiter
from backend.modules.projects_inventory.schemas import ProjectsInventoryOverview
from backend.modules.projects_inventory.services.inventory_service import (
    get_inventory_overview,
)

router = APIRouter(prefix="/projects-inventory", tags=["projects-inventory"])

_ERR_503 = {"error": {"code": "odoo_unavailable", "message": "Odoo is unavailable or the query failed. Try again shortly."}}
_ERR_500 = {"error": {"code": "internal_error", "message": "An unexpected error occurred."}}


@router.get(
    "/overview",
    summary="Projects Inventory — unit counts by status, overall + per project",
    response_model=ProjectsInventoryOverview,
)
@limiter.limit("60/minute")
async def overview(
    request: Request,
    response: Response,
    _user: str = Depends(get_current_user),
) -> dict | JSONResponse:
    try:
        data = await get_inventory_overview()
    except OdooQueryError:
        logger.warning("Projects inventory overview — Odoo query failed", exc_info=True)
        return JSONResponse(status_code=503, content=_ERR_503)
    except Exception:
        logger.error("Projects inventory overview — unexpected error", exc_info=True)
        return JSONResponse(status_code=500, content=_ERR_500)

    response.headers["Cache-Control"] = "private, max-age=60"
    response.headers["X-Cache-Status"] = str(data.get("cache_status", "fresh"))
    return data
