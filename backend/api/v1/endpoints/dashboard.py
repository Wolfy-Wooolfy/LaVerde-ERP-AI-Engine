"""HTML dashboard views — Jinja2 server-side rendered pages."""

import asyncio

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
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
from backend.modules.campaign_performance import domain as campperf_domain
from backend.modules.campaign_performance.services.campaign_service import (
    get_campaign_grand_totals,
    get_campaign_performance_overview,
    get_campaign_performance_windowed,
)
from backend.modules.campaign_performance.services.timeline_service import (
    CampaignNotFoundError,
    InvalidTimelineRangeError,
    get_campaign_timeline,
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
    "/campaign-performance/dashboard",
    response_class=HTMLResponse,
    summary="Campaign Performance overview (HTML)",
    dependencies=[Depends(require_module_html("campaign_performance"))],
)
async def campaign_performance_dashboard(
    request: Request,
    window: str = Query(campperf_domain.DEFAULT_WINDOW),
    start_month: str | None = Query(None),
    end_month: str | None = Query(None),
    user: str = Depends(get_current_user_html),
) -> HTMLResponse:
    # Server-side render. The list is scoped to a Cairo WINDOW (locked default: last
    # 3 months). "all" routes to the shipped un-windowed overview (incl. migration);
    # every dated preset / valid custom range routes to the windowed aggregator
    # (migration excluded). An invalid/partial custom range silently falls back to
    # the default preset (this HTML page never 422s a hand-edited URL — same policy
    # as the timeline). All paths are read-only; the template branches on win.is_windowed.
    has_custom = bool(start_month) and bool(end_month)
    if not has_custom and window not in campperf_domain.WINDOW_PRESETS:
        window = campperf_domain.DEFAULT_WINDOW

    if window == campperf_domain.WINDOW_ALL and not has_custom:
        data = await get_campaign_performance_overview()
        win = {
            "active": campperf_domain.WINDOW_ALL, "is_windowed": False,
            "is_custom_range": False, "start": "", "end": "",
            "ref_month": data["reference_date"][:7],
        }
    else:
        try:
            data = await get_campaign_performance_windowed(
                window=window, start_month=start_month, end_month=end_month,
            )
        except InvalidTimelineRangeError:
            data = await get_campaign_performance_windowed(window=campperf_domain.DEFAULT_WINDOW)
        win = {
            "active": data["window"], "is_windowed": True,
            "is_custom_range": data["is_custom_range"],
            "start": data["window_start_month"] if data["is_custom_range"] else "",
            "end": data["window_end_month"] if data["is_custom_range"] else "",
            "ref_month": data["reference_date"][:7],
        }

    # Grand totals are window-INDEPENDENT: the full-scale all-time funnel INCLUDING
    # the Nov-2025 migration plus the same EXCLUDING it. Called in EVERY branch
    # (all-time / preset / custom) so the pinned footer block is byte-identical
    # whatever window is on screen — never the windowed numbers above it.
    grand_totals = await get_campaign_grand_totals()

    ctx = _base_ctx(request, user)
    ctx.update({
        "page": "campaign_performance_dashboard",
        "campperf": data,
        "win": win,
        "grand_totals": grand_totals,
    })
    return templates.TemplateResponse(request, "campaign_performance/dashboard.html", ctx)


@router.get(
    "/campaign-performance/timeline",
    response_class=HTMLResponse,
    summary="Campaign Performance — per-campaign timeline (HTML)",
    dependencies=[Depends(require_module_html("campaign_performance"))],
)
async def campaign_performance_timeline(
    request: Request,
    campaign_id: int | None = Query(None),
    months: int = Query(3, ge=1, le=12),
    start_month: str | None = Query(None),
    end_month: str | None = Query(None),
    user: str = Depends(get_current_user_html),
) -> HTMLResponse:
    # Display-only drill-in mirroring the Level-1 page: call the read-only timeline
    # service and hand the result straight to the template — no new backend logic.
    # A missing/invalid campaign_id, or one resolving to no utm.campaign, redirects
    # back to the list (graceful — no 404 stack trace for a hand-edited URL). An
    # invalid/partial custom range silently falls back to the `months` preset (this
    # HTML page never 422s a hand-edited URL — the real UI only submits valid ranges).
    # This HTML path (/campaign-performance/timeline) is distinct from the JSON API at
    # /api/v1/campaign-performance/timeline (different router + prefix).
    _LIST_URL = "/campaign-performance/dashboard"
    if campaign_id is None or campaign_id <= 0:
        return RedirectResponse(_LIST_URL, status_code=302)
    try:
        data = await get_campaign_timeline(
            campaign_id=campaign_id,
            window_months=months,
            start_month=start_month,
            end_month=end_month,
        )
    except InvalidTimelineRangeError:
        try:
            data = await get_campaign_timeline(campaign_id=campaign_id, window_months=months)
        except CampaignNotFoundError:
            return RedirectResponse(_LIST_URL, status_code=302)
    except CampaignNotFoundError:
        return RedirectResponse(_LIST_URL, status_code=302)
    ctx = _base_ctx(request, user)
    ctx.update({
        "page": "campaign_performance_dashboard",
        "tl": data,
    })
    return templates.TemplateResponse(request, "campaign_performance/timeline.html", ctx)


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
