"""HTML dashboard views — Jinja2 server-side rendered pages."""

import asyncio

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from loguru import logger

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
from backend.modules.marketing_attribution.domain import GROUP_ORDER
from backend.modules.marketing_attribution.services.attribution_service import (
    get_attribution_grand_coverage,
    get_attribution_overview,
    get_attribution_overview_windowed,
)
from backend.modules.marketing_attribution.services.buyer_timeline_service import (
    BuyerNotFoundError,
    get_buyer_timeline,
)
from backend.modules.projects_inventory.services.inventory_service import (
    get_inventory_overview,
)
from backend.modules.projects_inventory.services.pipeline_service import (
    get_contracts_pipeline,
)
from backend.modules.projects_inventory.services.value_service import (
    get_value_area_overview,
)
from backend.modules.projects_inventory.services.pricing_outliers_service import (
    get_pricing_outliers_overview,
)
from backend.modules.projects_inventory.services.data_quality_service import (
    get_data_quality_overview,
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


def _aggregate_outcome_groups(entity_outcomes, total: int) -> list[dict]:
    """Sum per-entity 4-group outcomes into ONE windowed funnel (route-side only).

    The windowed payload carries no top-level aggregate, so the bottom "this period"
    line re-sums the per-entity outcomes the cards already show: add each group's
    COUNT across the given per-entity outcomes lists (each per-entity `pct` is ignored
    — recomputed fresh over `total`), returning the 4 groups in GROUP_ORDER. This is a
    read-only re-aggregation of data already fetched into the windowed payload — no
    service call, no Odoo, no cache. Logs a warning (does NOT raise) if the summed
    counts do not reconcile to `total`, so a future per-entity data-shape drift
    surfaces without breaking the page.
    """
    counts = {g: 0 for g in GROUP_ORDER}
    for outcomes in entity_outcomes:
        for o in outcomes:
            counts[o["group"]] = counts.get(o["group"], 0) + int(o["count"])
    summed = sum(counts.values())
    if summed != total:
        logger.warning(
            f"Windowed outcome breakdown does not reconcile: group sum {summed} "
            f"!= total {total}. Rendering the breakdown anyway (per-entity drift?)."
        )
    return [
        {
            "group": g,
            "count": counts[g],
            "pct": round(100.0 * counts[g] / total, 1) if total else 0.0,
        }
        for g in GROUP_ORDER
    ]


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
    window: str = Query(campperf_domain.DEFAULT_WINDOW),
    start_month: str | None = Query(None),
    end_month: str | None = Query(None),
    user: str = Depends(get_current_user_html),
) -> HTMLResponse:
    # Server-side render. The buyer list is scoped to a Cairo WINDOW (locked default:
    # last 3 months). "all" routes to the shipped un-windowed attribution (incl. the
    # Nov-2025 migration — unchanged); every dated preset / valid custom range routes to
    # the windowed aggregator (migration excluded). An invalid/partial custom range
    # silently falls back to the default preset (this HTML page never 422s a hand-edited
    # URL — same policy as the campaign list / timeline). All paths are read-only; the
    # template branches on win.is_windowed.
    has_custom = bool(start_month) and bool(end_month)
    if not has_custom and window not in campperf_domain.WINDOW_PRESETS:
        window = campperf_domain.DEFAULT_WINDOW

    if window == campperf_domain.WINDOW_ALL and not has_custom:
        data = await get_attribution_overview()
        win = {
            "active": campperf_domain.WINDOW_ALL, "is_windowed": False,
            "is_custom_range": False, "start": "", "end": "",
            "ref_month": data["reference_date"][:7],
        }
        # Remainder = leads with no media buyer by nature (events/expos/organic).
        coverage_remainder_pct = round(100 - data["attribution_pct"], 1)
        # All-time top block is POPULATION-basis, but get_attribution_overview only
        # classifies ATTRIBUTED leads (it has no unattributed group breakdown). So the
        # all-time funnel + total come from the campaign module's grand-totals INCL line —
        # the SAME crm.lead population (both total_leads_population are Σ __count over all
        # leads, _CTX_ALL), an existing cached service, no new Odoo query.
        grand_totals = await get_campaign_grand_totals()
        top_groups = grand_totals["incl"]["groups"]
        top_total = grand_totals["incl"]["total"]
        if top_total != data["total_leads_population"]:
            logger.warning(
                f"All-time buyer top block: grand_totals incl.total {top_total} != "
                f"overview total_leads_population {data['total_leads_population']} "
                f"(population drift between modules?)."
            )
    else:
        try:
            data = await get_attribution_overview_windowed(
                window=window, start_month=start_month, end_month=end_month,
            )
        except InvalidTimelineRangeError:
            data = await get_attribution_overview_windowed(window=campperf_domain.DEFAULT_WINDOW)
        win = {
            "active": data["window"], "is_windowed": True,
            "is_custom_range": data["is_custom_range"],
            "start": data["window_start_month"] if data["is_custom_range"] else "",
            "end": data["window_end_month"] if data["is_custom_range"] else "",
            "ref_month": data["reference_date"][:7],
        }
        # Windowed remainder = the unattributed share of THIS window's leads.
        coverage_remainder_pct = round(100 - data["coverage_pct"], 1)
        # Windowed top block: re-sum the per-buyer outcomes PLUS the unattributed bucket so
        # the four counts reconcile to total_leads_population (population basis, route-side
        # re-aggregation of already-fetched data only — no service/Odoo/cache).
        top_groups = _aggregate_outcome_groups(
            [b["outcomes"] for b in data["buyers"]] + [data["unattributed"]["outcomes"]],
            data["total_leads_population"],
        )
        top_total = data["total_leads_population"]

    # Grand coverage is window-INDEPENDENT: the full-scale all-time attribution
    # INCLUDING the Nov-2025 migration plus the same EXCLUDING it. Called in EVERY
    # branch (all-time / preset / custom) so the pinned footer block is byte-identical
    # whatever window is on screen — never the windowed coverage line above it. Mirrors
    # the campaign grand-totals footer (f8f27bf).
    grand_coverage = await get_attribution_grand_coverage()

    ctx = _base_ctx(request, user)
    ctx.update({
        "page": "marketing_attribution_dashboard",
        "attr": data,
        "win": win,
        "coverage_remainder_pct": coverage_remainder_pct,
        "grand_coverage": grand_coverage,
        # Always-on TOP block: the population-basis total + 4-group breakdown for the
        # active mode (all-time by default, the window when one is active). Computed in
        # BOTH branches; the template renders it unconditionally.
        "top_groups": top_groups,
        "top_total": top_total,
    })
    return templates.TemplateResponse(request, "marketing_attribution/dashboard.html", ctx)


@router.get(
    "/marketing-attribution/buyer/{buyer_id}/timeline",
    response_class=HTMLResponse,
    summary="Marketing Attribution — per-media-buyer timeline (HTML)",
    dependencies=[Depends(require_module_html("marketing_attribution"))],
)
async def marketing_attribution_buyer_timeline(
    request: Request,
    buyer_id: int,
    months: int = Query(3, ge=1, le=12),
    start_month: str | None = Query(None),
    end_month: str | None = Query(None),
    user: str = Depends(get_current_user_html),
) -> HTMLResponse:
    # Display-only drill-in mirroring the campaign timeline: call the read-only buyer
    # timeline service and hand the result straight to the template — no new backend
    # logic. A non-positive buyer_id, or one that attributes from no confirmed campaign,
    # redirects back to the buyer list (graceful — no 404 stack trace for a hand-edited
    # URL). An invalid/partial custom range silently falls back to the `months` preset
    # (this HTML page never 422s a hand-edited URL — the real UI only submits valid
    # ranges). This HTML path is distinct from the JSON API at
    # /api/v1/marketing-attribution/buyer/{id}/timeline (different router + prefix).
    _LIST_URL = "/marketing-attribution/dashboard"
    if buyer_id <= 0:
        return RedirectResponse(_LIST_URL, status_code=302)
    try:
        data = await get_buyer_timeline(
            buyer_id=buyer_id,
            window_months=months,
            start_month=start_month,
            end_month=end_month,
        )
    except InvalidTimelineRangeError:
        try:
            data = await get_buyer_timeline(buyer_id=buyer_id, window_months=months)
        except BuyerNotFoundError:
            return RedirectResponse(_LIST_URL, status_code=302)
    except BuyerNotFoundError:
        return RedirectResponse(_LIST_URL, status_code=302)
    ctx = _base_ctx(request, user)
    ctx.update({
        "page": "marketing_attribution_dashboard",
        "tl": data,
    })
    return templates.TemplateResponse(request, "marketing_attribution/timeline.html", ctx)


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
        # All-time top block: re-aggregate the overview's population buckets — listed
        # campaigns + the long_tail roll-up + the junk / no-campaign data-quality buckets
        # (each None-guarded) — so the four counts reconcile to total_leads_population. Pure
        # re-aggregation of already-fetched data; no new fetch.
        lt = data["long_tail"]
        dq = data["data_quality"]
        top_groups = _aggregate_outcome_groups(
            [c["outcomes"] for c in data["campaigns"]]
            + [b["outcomes"] for b in (lt, dq["junk_none"], dq["no_campaign"]) if b is not None],
            data["total_leads_population"],
        )
        top_total = data["total_leads_population"]
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
        # Windowed top block: re-sum the per-campaign outcomes PLUS the data-quality
        # buckets (junk / no-campaign, None-guarded) so the four counts reconcile to
        # total_leads_population (population basis, route-side re-aggregation — no service).
        dq = data["data_quality"]
        top_groups = _aggregate_outcome_groups(
            [c["outcomes"] for c in data["campaigns"]]
            + [b["outcomes"] for b in (dq["junk_none"], dq["no_campaign"]) if b is not None],
            data["total_leads_population"],
        )
        top_total = data["total_leads_population"]

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
        # Always-on TOP block: the population-basis total + 4-group breakdown for the
        # active mode (all-time by default, the window when one is active). Computed in
        # BOTH branches; the template renders it unconditionally.
        "top_groups": top_groups,
        "top_total": top_total,
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
    "/projects-inventory/dashboard",
    response_class=HTMLResponse,
    summary="Projects Inventory — Inventory & Availability (HTML)",
    dependencies=[Depends(require_module_html("projects_inventory"))],
)
async def projects_inventory_dashboard(
    request: Request,
    user: str = Depends(get_current_user_html),
) -> HTMLResponse:
    # Server-side render of the inventory-by-status board (Slice 1: counts only —
    # overall + per project). Read-only; the service owns all bucketing/reconciliation.
    data = await get_inventory_overview()

    # The contracts pipeline is an ADDITIVE section on the same page, and it reads a
    # DIFFERENT axis (rs.contract + chatter) from the unit board above it. So it
    # degrades on its own: any failure renders an inline error card in that one
    # section and the rest of the board still renders. The unit axis stays strict —
    # if get_inventory_overview() fails the page is genuinely empty and still 500s.
    pipeline = None
    try:
        pipeline = await get_contracts_pipeline()
    except Exception:
        logger.warning(
            "Projects inventory page — contracts pipeline unavailable; rendering the "
            "board without it.", exc_info=True,
        )

    ctx = _base_ctx(request, user)
    ctx.update({
        "page": "projects_inventory_dashboard",
        "inv": data,
        "pipeline": pipeline,
    })
    return templates.TemplateResponse(request, "projects_inventory/dashboard.html", ctx)


@router.get(
    "/projects-inventory/value-area",
    response_class=HTMLResponse,
    summary="Projects Inventory — Value & Area (HTML)",
    dependencies=[Depends(require_module_html("projects_inventory"))],
)
async def projects_inventory_value_area(
    request: Request,
    user: str = Depends(get_current_user_html),
) -> HTMLResponse:
    # Server-side render of the Value & Area board (Slice 2: list value of available
    # inventory, realized/contracted value of sold units, the list-vs-realized gap, and
    # area metrics — New Capital + Cassette, La Puerta excluded). Read-only; the service
    # owns all computation, the contract join and reconciliation.
    data = await get_value_area_overview()
    ctx = _base_ctx(request, user)
    ctx.update({
        "page": "projects_inventory_value_area",
        "va": data,
    })
    return templates.TemplateResponse(request, "projects_inventory/value_area.html", ctx)


@router.get(
    "/projects-inventory/pricing-outliers",
    response_class=HTMLResponse,
    summary="Projects Inventory — Pricing Outliers (HTML)",
    dependencies=[Depends(require_module_html("projects_inventory"))],
)
async def projects_inventory_pricing_outliers(
    request: Request,
    user: str = Depends(get_current_user_html),
) -> HTMLResponse:
    # Server-side render of the Pricing Outliers board (Slice 2.5: peer realized price/m²
    # outliers — vintage-controlled — and discount-vs-own-list outliers, NC + Cassette,
    # La Puerta excluded). Read-only; the service owns the population, both flag sets,
    # the confirmed join and every reconciliation.
    data = await get_pricing_outliers_overview()
    ctx = _base_ctx(request, user)
    ctx.update({
        "page": "projects_inventory_pricing_outliers",
        "po": data,
    })
    return templates.TemplateResponse(request, "projects_inventory/pricing_outliers.html", ctx)


@router.get(
    "/projects-inventory/data-quality",
    response_class=HTMLResponse,
    summary="Projects Inventory — Inventory Data Quality (admin only, HTML)",
    include_in_schema=False,
    dependencies=[Depends(require_admin_html)],
)
async def projects_inventory_data_quality(
    request: Request,
    user: str = Depends(get_current_user_html),
) -> HTMLResponse:
    # Server-side render of the Inventory Data Quality review (admin only): sold units
    # with no contract (A), broken hierarchy chains (B), and sold units with no list price
    # (C) across all projects. Read-only; the service owns every check + reconciliation.
    data = await get_data_quality_overview()
    ctx = _base_ctx(request, user)
    ctx.update({
        "page": "projects_inventory_data_quality",
        "dq": data,
    })
    return templates.TemplateResponse(request, "projects_inventory/data_quality.html", ctx)


@router.get(
    "/accounting/balance-sheet",
    response_class=HTMLResponse,
    summary="Accounting — Balance Sheet (HTML)",
    dependencies=[Depends(require_module_html("accounting"))],
)
async def accounting_balance_sheet(
    request: Request,
    user: str = Depends(get_current_user_html),
) -> HTMLResponse:
    # Shell only (Module 4 Phase 2, M4.13): ALL figures arrive client-side
    # from GET /api/v1/accounting/balance-sheet (live, no-store — M4.3) via
    # the house crmApi fetch wrapper. This route performs ZERO Odoo calls;
    # the statement is being edited in place by finance, so nothing here may
    # cache or precompute a figure.
    ctx = _base_ctx(request, user)
    ctx["page"] = "accounting_balance_sheet"
    return templates.TemplateResponse(request, "accounting/balance_sheet.html", ctx)


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


# ── Data Quality hub ──────────────────────────────────────────────────────────

# The four hub tabs are independent lists (tiny cross-tab overlap), each fetched in
# full and rendered server-side — no pagination (see build brief). _DQ_HUB_LIMIT is
# a safety cap well above today's largest list (~456); the true __count is always the
# headline, and the template's "showing first N of M" note discloses truncation if it
# ever fires — so a tab's headline stays the true domain count (N4) regardless.
_DQ_HUB_TABS = ("linked-contact", "phone", "stage", "salesperson")
_DQ_HUB_LIMIT = 1000


@router.get(
    "/data-quality",
    response_class=HTMLResponse,
    summary="Data Quality hub — 4 tabs (HTML)",
    dependencies=[Depends(require_module_html("crm"))],
)
async def data_quality_hub(
    request: Request,
    tab: str = Query("linked-contact"),
    user: str = Depends(get_current_user_html),
    service: CrmService = Depends(get_crm_service),
) -> HTMLResponse:
    # Read-only: fetch the four DQ detail lists concurrently. Each list uses its
    # VERBATIM card domain, so every tab's headline __count == its list by
    # construction (N4). ?tab= selects the initial tab (validated; default Tab 1).
    initial_tab = tab if tab in _DQ_HUB_TABS else "linked-contact"

    (
        (linked_rows, linked_total),
        (phone_rows, phone_total),
        (stage_rows, stage_total),
        (sp_rows, sp_total),
    ) = await asyncio.gather(
        service.missing_linked_contact_details(page=1, page_size=_DQ_HUB_LIMIT),
        service.missing_contact_details(page=1, page_size=_DQ_HUB_LIMIT),
        service.missing_stage_details(page=1, page_size=_DQ_HUB_LIMIT),
        service.missing_salesperson_details(page=1, page_size=_DQ_HUB_LIMIT),
    )

    ctx = _base_ctx(request, user)
    ctx.update(
        {
            "page": "data_quality",
            "initial_tab": initial_tab,
            "odoo_url": settings.ODOO_URL.rstrip("/"),
            "dq": {
                "linked_contact": {"rows": linked_rows, "total": linked_total},
                "phone": {"rows": phone_rows, "total": phone_total},
                "stage": {"rows": stage_rows, "total": stage_total},
                "salesperson": {"rows": sp_rows, "total": sp_total},
            },
        }
    )
    return templates.TemplateResponse(request, "data_quality.html", ctx)


# ── Legacy DQ detail routes → 302 redirect to the hub tabs (still crm-gated) ──


@router.get(
    "/data-quality/missing-contact",
    include_in_schema=False,
    dependencies=[Depends(require_module_html("crm"))],
)
async def missing_contact_redirect(
    user: str = Depends(get_current_user_html),
) -> RedirectResponse:
    return RedirectResponse("/data-quality?tab=phone", status_code=302)


@router.get(
    "/data-quality/missing-stage",
    include_in_schema=False,
    dependencies=[Depends(require_module_html("crm"))],
)
async def missing_stage_redirect(
    user: str = Depends(get_current_user_html),
) -> RedirectResponse:
    return RedirectResponse("/data-quality?tab=stage", status_code=302)


@router.get(
    "/data-quality/missing-salesperson",
    include_in_schema=False,
    dependencies=[Depends(require_module_html("crm"))],
)
async def missing_salesperson_redirect(
    user: str = Depends(get_current_user_html),
) -> RedirectResponse:
    return RedirectResponse("/data-quality?tab=salesperson", status_code=302)
