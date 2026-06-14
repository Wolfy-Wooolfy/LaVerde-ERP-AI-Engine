"""HTML dashboard views — Jinja2 server-side rendered pages."""

import asyncio

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from backend.api.deps import get_crm_service, get_current_user_html, require_admin_html, require_module_html
from backend.core.config import settings
from backend.core.i18n import detect_lang, load_translations, make_translator
from backend.modules.crm.service import CrmService
from backend.modules.hr.services.kpi_service import (
    get_department_cost,
    get_headcount,
    get_payroll_risk_dashboard,
    get_tenure_distribution,
)
from backend.modules.marketing_attribution.services.attribution_service import (
    get_attribution_overview,
)

router = APIRouter(tags=["ui"])
templates = Jinja2Templates(directory="frontend/templates")

# Load translations once at module import time (fast, idempotent)
load_translations()


def _extract_first_name(username: str) -> str:
    """khaled.elmasry@laverde-eg.com → 'Khaled',  admin → 'Admin'."""
    if settings.DISPLAY_NAME:
        return settings.DISPLAY_NAME
    local = username.split("@")[0]
    return local.split(".")[0].capitalize()


def _base_ctx(request: Request, user: str) -> dict:
    """Common context injected into every page."""
    lang = detect_lang(dict(request.cookies), request.headers.get("accept-language", ""))
    _user_record = request.app.state.user_repo.get_user(user)
    allowed_modules: list[str] = _user_record.modules if _user_record else []
    is_admin: bool = _user_record.is_admin if _user_record else False
    return {
        "request": request,
        "current_user": user,
        "user_display_name": _extract_first_name(user),
        "lang": lang,
        "is_rtl": lang == "ar",
        "_t": make_translator(lang),
        "allowed_modules": allowed_modules,
        "is_admin": is_admin,
    }


@router.get("/dashboard", response_class=HTMLResponse, summary="CRM dashboard (HTML)", dependencies=[Depends(require_module_html("crm"))])
async def dashboard(
    request: Request,
    user: str = Depends(get_current_user_html),
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


@router.get("/collections/dashboard", response_class=HTMLResponse, summary="Collections dashboard (HTML)", dependencies=[Depends(require_module_html("collections"))])
async def collections_dashboard(
    request: Request,
    user: str = Depends(get_current_user_html),
) -> HTMLResponse:
    ctx = _base_ctx(request, user)
    ctx["page"] = "collections_dashboard"
    return templates.TemplateResponse(request, "collections/dashboard.html", ctx)


@router.get("/customer-accounts/dashboard", response_class=HTMLResponse, summary="Customer Accounts dashboard (HTML)", dependencies=[Depends(require_module_html("customer_accounts"))])
async def customer_accounts_dashboard(
    request: Request,
    user: str = Depends(get_current_user_html),
) -> HTMLResponse:
    ctx = _base_ctx(request, user)
    ctx["page"] = "customer_accounts_dashboard"
    return templates.TemplateResponse(request, "customer_accounts/dashboard.html", ctx)


@router.get("/hr/dashboard", response_class=HTMLResponse, summary="HR overview dashboard (HTML)", dependencies=[Depends(require_module_html("hr"))])
async def hr_dashboard(
    request: Request,
    user: str = Depends(get_current_user_html),
) -> HTMLResponse:
    headcount, tenure, payroll_risk, dept_cost = await asyncio.gather(
        get_headcount(),
        get_tenure_distribution(),
        get_payroll_risk_dashboard(),
        get_department_cost(),
    )
    dept_count = len([d for d in headcount["by_department"] if d["department_id"] is not None])
    avg_per_emp = (
        round(dept_cost["grand_total_wage"] / headcount["headcount"])
        if headcount["headcount"]
        else 0
    )
    buckets_by_label = {b["label"]: b["count"] for b in payroll_risk["buckets"]}
    named_dept_rows = [
        r for r in dept_cost["rows"]
        if r["department_name"] != "Other (small departments)"
    ]
    other_row = next(
        (r for r in dept_cost["rows"] if r["department_name"] == "Other (small departments)"),
        None,
    )
    max_dept_wage = max(
        (r["total_wage"] for r in named_dept_rows if r["total_wage"] is not None),
        default=1,
    )
    max_band_count = max((b["count"] for b in tenure["bands"]), default=1)
    ctx = _base_ctx(request, user)
    ctx.update({
        "page":              "hr_dashboard",
        "headcount":         headcount,
        "tenure":            tenure,
        "payroll_risk":      payroll_risk,
        "dept_cost":         dept_cost,
        "dept_count":        dept_count,
        "avg_per_emp":       avg_per_emp,
        "buckets_by_label":  buckets_by_label,
        "named_dept_rows":   named_dept_rows,
        "other_row":         other_row,
        "max_dept_wage":     max_dept_wage,
        "max_band_count":    max_band_count,
        "attn_expired":      buckets_by_label.get("expired", 0),
        "attn_expiring_45d": buckets_by_label.get("expiring_45d", 0),
    })
    return templates.TemplateResponse(request, "hr/dashboard.html", ctx)


@router.get(
    "/marketing-attribution/dashboard",
    response_class=HTMLResponse,
    summary="Marketing Attribution overview (HTML)",
    dependencies=[Depends(require_module_html("marketing_attribution"))],
)
async def marketing_attribution_dashboard(
    request: Request,
    user: str = Depends(get_current_user_html),
) -> HTMLResponse:
    # Server-side render, mirroring the HR page: call the read-only service and pass the
    # result straight to the template. No new backend logic — display only.
    data = await get_attribution_overview()
    # Remainder = leads with no media buyer by nature (events/expos/organic/place-based).
    coverage_remainder_pct = round(100 - data["attribution_pct"], 1)
    ctx = _base_ctx(request, user)
    ctx.update({
        "page": "marketing_attribution_dashboard",
        "attr": data,
        "coverage_remainder_pct": coverage_remainder_pct,
    })
    return templates.TemplateResponse(request, "marketing_attribution/dashboard.html", ctx)


@router.get(
    "/settings",
    response_class=HTMLResponse,
    summary="Settings — User Management (admin only)",
    include_in_schema=False,
    dependencies=[Depends(require_admin_html)],
)
async def settings_page(
    request: Request,
    user: str = Depends(get_current_user_html),
) -> HTMLResponse:
    ctx = _base_ctx(request, user)
    ctx["page"] = "settings"
    return templates.TemplateResponse(request, "settings.html", ctx)


@router.get(
    "/data-quality/missing-contact",
    response_class=HTMLResponse,
    summary="Missing contact details page (HTML)",
    dependencies=[Depends(require_module_html("crm"))],
)
async def missing_contact_page(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    team_id: int | None = Query(None),
    salesperson_id: int | None = Query(None),
    sort: str = Query("create_date desc"),
    user: str = Depends(get_current_user_html),
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


@router.get(
    "/data-quality/missing-stage",
    response_class=HTMLResponse,
    summary="Missing stage details page (HTML)",
    dependencies=[Depends(require_module_html("crm"))],
)
async def missing_stage_page(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    team_id: int | None = Query(None),
    salesperson_id: int | None = Query(None),
    sort: str = Query("create_date desc"),
    user: str = Depends(get_current_user_html),
    service: CrmService = Depends(get_crm_service),
) -> HTMLResponse:
    rows, total = await service.missing_stage_details(
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
            "page": "missing_stage",
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
    return templates.TemplateResponse(request, "missing_stage.html", ctx)


@router.get(
    "/data-quality/missing-salesperson",
    response_class=HTMLResponse,
    summary="Missing salesperson details page (HTML)",
    dependencies=[Depends(require_module_html("crm"))],
)
async def missing_salesperson_page(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    team_id: int | None = Query(None),
    salesperson_id: int | None = Query(None),
    sort: str = Query("create_date desc"),
    user: str = Depends(get_current_user_html),
    service: CrmService = Depends(get_crm_service),
) -> HTMLResponse:
    rows, total = await service.missing_salesperson_details(
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
            "page": "missing_salesperson",
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
    return templates.TemplateResponse(request, "missing_salesperson.html", ctx)
