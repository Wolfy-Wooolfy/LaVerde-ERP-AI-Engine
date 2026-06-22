"""
Projects Inventory endpoints.

GET /api/v1/projects-inventory/overview — unit counts by sales status, overall and
    per project (counts only; no pricing/area/value). Read-only. [Slice 1]
GET /api/v1/projects-inventory/drill/{level}/{parent_id} — one hierarchy scope of
    Project → Phase → Zone → Building → Unit: the scope's status breakdown plus its
    child rows (or the unit leaf list at the building level). Read-only. [Slice 1b]
GET /api/v1/projects-inventory/value-area/overview — LIST value of available inventory,
    CONTRACTED (realized) value of sold units, the list-vs-realized gap, and area
    metrics, for New Capital + Cassette (La Puerta excluded). Read-only. [Slice 2]
GET /api/v1/projects-inventory/pricing-outliers/overview — sold units priced/sold
    anomalously: peer realized price/m² outliers (vintage-controlled, Section A) and
    discount outliers vs own list (Section B), NC + Cassette. Read-only. [Slice 2.5]

RBAC: module-gated at include_router level in router.py via
require_module_api("projects_inventory"); additionally requires an authenticated
session (get_current_user). Returns 401 without a session, 403 without the module
grant.
"""

from fastapi import APIRouter, Depends, Path, Request, Response
from fastapi.responses import JSONResponse
from loguru import logger

from backend.api.deps import get_current_user, require_admin_api
from backend.core.exceptions import InventoryScopeNotFoundError, OdooQueryError
from backend.core.limiter import limiter
from backend.modules.projects_inventory.schemas import (
    DataQualityOverview,
    DrillLevel,
    PricingOutliersOverview,
    ProjectsInventoryDrill,
    ProjectsInventoryOverview,
    ValueAreaOverview,
)
from backend.modules.projects_inventory.services.data_quality_service import (
    get_data_quality_overview,
)
from backend.modules.projects_inventory.services.inventory_service import (
    get_inventory_drill,
    get_inventory_overview,
)
from backend.modules.projects_inventory.services.pricing_outliers_service import (
    get_pricing_outliers_overview,
)
from backend.modules.projects_inventory.services.value_service import (
    get_value_area_overview,
)

router = APIRouter(prefix="/projects-inventory", tags=["projects-inventory"])

_ERR_503 = {"error": {"code": "odoo_unavailable", "message": "Odoo is unavailable or the query failed. Try again shortly."}}
_ERR_500 = {"error": {"code": "internal_error", "message": "An unexpected error occurred."}}
_ERR_404 = {"error": {"code": "scope_not_found", "message": "No units found for that node. It may not exist or is stale."}}


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


@router.get(
    "/value-area/overview",
    summary="Projects Inventory — Value & Area (list vs realized), NC + Cassette",
    response_model=ValueAreaOverview,
)
@limiter.limit("60/minute")
async def value_area_overview(
    request: Request,
    response: Response,
    _user: str = Depends(get_current_user),
) -> dict | JSONResponse:
    """LIST value of available inventory, CONTRACTED (realized) value of sold units, the
    list-vs-realized gap and area metrics — combined + per project for New Capital +
    Cassette. La Puerta is excluded. Realized is contracted value, not cash collected."""
    try:
        data = await get_value_area_overview()
    except OdooQueryError:
        logger.warning("Projects inventory value-area — Odoo query failed", exc_info=True)
        return JSONResponse(status_code=503, content=_ERR_503)
    except Exception:
        logger.error("Projects inventory value-area — unexpected error", exc_info=True)
        return JSONResponse(status_code=500, content=_ERR_500)

    response.headers["Cache-Control"] = "private, max-age=60"
    response.headers["X-Cache-Status"] = str(data.get("cache_status", "fresh"))
    return data


@router.get(
    "/pricing-outliers/overview",
    summary="Projects Inventory — Pricing Outliers (peer price/m² + discount), NC + Cassette",
    response_model=PricingOutliersOverview,
)
@limiter.limit("60/minute")
async def pricing_outliers_overview(
    request: Request,
    response: Response,
    _user: str = Depends(get_current_user),
) -> dict | JSONResponse:
    """Sold units priced/sold anomalously, two vintage-controlled signals: peer realized
    price/m² outliers (Section A) and discount-vs-own-list outliers (Section B), with the
    units flagged in BOTH marked "confirmed" — combined + per project for New Capital +
    Cassette. La Puerta is excluded. Realized is contracted value, not cash collected."""
    try:
        data = await get_pricing_outliers_overview()
    except OdooQueryError:
        logger.warning("Projects inventory pricing-outliers — Odoo query failed", exc_info=True)
        return JSONResponse(status_code=503, content=_ERR_503)
    except Exception:
        logger.error("Projects inventory pricing-outliers — unexpected error", exc_info=True)
        return JSONResponse(status_code=500, content=_ERR_500)

    response.headers["Cache-Control"] = "private, max-age=60"
    response.headers["X-Cache-Status"] = str(data.get("cache_status", "fresh"))
    return data


@router.get(
    "/data-quality/overview",
    summary="Projects Inventory — Inventory Data Quality (admin only; all projects)",
    response_model=DataQualityOverview,
    dependencies=[Depends(require_admin_api)],
)
@limiter.limit("60/minute")
async def data_quality_overview(
    request: Request,
    response: Response,
    _user: str = Depends(get_current_user),
) -> dict | JSONResponse:
    """Read-only data-completeness review across ALL projects: sold units with no
    contract (A), broken hierarchy chains (B), and sold units with no list price (C).
    Admin-only (require_admin_api, on top of the module gate). Never writes to Odoo."""
    try:
        data = await get_data_quality_overview()
    except OdooQueryError:
        logger.warning("Projects inventory data-quality — Odoo query failed", exc_info=True)
        return JSONResponse(status_code=503, content=_ERR_503)
    except Exception:
        logger.error("Projects inventory data-quality — unexpected error", exc_info=True)
        return JSONResponse(status_code=500, content=_ERR_500)

    response.headers["Cache-Control"] = "private, max-age=60"
    response.headers["X-Cache-Status"] = str(data.get("cache_status", "fresh"))
    return data


@router.get(
    "/drill/{level}/{parent_id}",
    summary="Projects Inventory — drill one hierarchy scope (phases/zones/buildings/units)",
    response_model=ProjectsInventoryDrill,
)
@limiter.limit("60/minute")
async def drill(
    request: Request,
    response: Response,
    level: DrillLevel,
    parent_id: int = Path(ge=1, description="The id at `level` (project/phase/zone/building)."),
    _user: str = Depends(get_current_user),
) -> dict | JSONResponse:
    """Group levels (project/phase/zone) return the scope total/buckets/sold% plus the
    child rows; the building level returns is_leaf + the unit list. `level` is a Literal,
    so a bad value yields 422 before the handler runs. Empty/unknown scope → 404."""
    try:
        data = await get_inventory_drill(level, parent_id)
    except InventoryScopeNotFoundError:
        return JSONResponse(status_code=404, content=_ERR_404)
    except ValueError:
        # Defense-in-depth: the Literal path param already 422s on a bad level, but the
        # service re-validates and we map its ValueError to 422 too.
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "invalid_level", "message": "Unknown drill level."}},
        )
    except OdooQueryError:
        logger.warning("Projects inventory drill — Odoo query failed", exc_info=True)
        return JSONResponse(status_code=503, content=_ERR_503)
    except Exception:
        logger.error("Projects inventory drill — unexpected error", exc_info=True)
        return JSONResponse(status_code=500, content=_ERR_500)

    response.headers["Cache-Control"] = "private, max-age=60"
    response.headers["X-Cache-Status"] = str(data.get("cache_status", "fresh"))
    return data
