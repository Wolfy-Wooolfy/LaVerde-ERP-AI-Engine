"""HTML dashboard views — Jinja2 server-side rendered pages."""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from backend.api.deps import get_crm_service, get_current_user
from backend.core.config import settings
from backend.core.i18n import detect_lang, load_translations, make_translator
from backend.modules.crm.service import CrmService

router = APIRouter(tags=["ui"])
templates = Jinja2Templates(directory="frontend/templates")

# Load translations once at module import time (fast, idempotent)
load_translations()


def _extract_first_name(username: str) -> str:
    """khaled.elmasry@laverde-eg.com → 'Khaled',  admin → 'Admin'."""
    local = username.split("@")[0]
    return local.split(".")[0].capitalize()


def _base_ctx(request: Request, user: str) -> dict:
    """Common context injected into every page."""
    lang = detect_lang(dict(request.cookies), request.headers.get("accept-language", ""))
    return {
        "request": request,
        "current_user": user,
        "user_display_name": _extract_first_name(user),
        "lang": lang,
        "is_rtl": lang == "ar",
        "_t": make_translator(lang),
    }


@router.get("/dashboard", response_class=HTMLResponse, summary="CRM dashboard (HTML)")
async def dashboard(
    request: Request,
    user: str = Depends(get_current_user),
    service: CrmService = Depends(get_crm_service),
) -> HTMLResponse:
    data = await service.summary()
    ctx = _base_ctx(request, user)
    ctx.update(
        {
            "page": "dashboard",
            "mode": data.mode,
            "scope": data.scope,
            "summary": data.summary,
            "data_quality": data.data_quality,
            "followup_risk": data.followup_risk,
            "odoo_url": settings.ODOO_URL.rstrip("/"),
        }
    )
    return templates.TemplateResponse(request, "dashboard.html", ctx)


@router.get(
    "/data-quality/missing-contact",
    response_class=HTMLResponse,
    summary="Missing contact details page (HTML)",
)
async def missing_contact_page(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    team_id: int | None = Query(None),
    salesperson_id: int | None = Query(None),
    sort: str = Query("create_date desc"),
    user: str = Depends(get_current_user),
    service: CrmService = Depends(get_crm_service),
) -> HTMLResponse:
    rows, total = await service.missing_contact_details(
        page=page,
        page_size=page_size,
        team_id=team_id,
        salesperson_id=salesperson_id,
        sort=sort,
    )
    from math import ceil

    pag = {
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": max(1, ceil(total / page_size)),
        "has_prev": page > 1,
        "has_next": page * page_size < total,
    }
    ctx = _base_ctx(request, user)
    ctx.update(
        {
            "page": "missing_contact",
            "rows": rows,
            "pag": pag,
            "odoo_url": settings.ODOO_URL.rstrip("/"),
            "filters": {
                "team_id": team_id,
                "salesperson_id": salesperson_id,
                "sort": sort,
            },
        }
    )
    return templates.TemplateResponse(request, "missing_contact.html", ctx)
