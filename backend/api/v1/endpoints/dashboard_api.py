"""
Dashboard JSON API endpoints — fast data for frontend AJAX refresh.
GET /api/v1/dashboard/kpis       — flat KPI snapshot
GET /api/v1/dashboard/heatmap    — salesperson × stage overdue matrix
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

from backend.api.deps import get_crm_service, get_current_user
from backend.core.limiter import limiter
from backend.modules.crm.service import CrmService

router = APIRouter(prefix="/dashboard", tags=["dashboard-api"])


@router.get("/kpis", summary="KPI snapshot for dashboard refresh")
@limiter.limit("60/minute")
async def dashboard_kpis(
    request: Request,
    user: str = Depends(get_current_user),
    service: CrmService = Depends(get_crm_service),
) -> dict:
    data = await service.summary()
    s = data.summary
    dq = data.data_quality
    return {
        "ok": True,
        "kpis": {
            "total_leads": s.total_leads,
            "critical_overdue": s.critical_overdue,
            "overdue_followups": s.overdue_followups,
            "followups_today": s.followups_today,
            "planned_followups": s.planned_followups,
            "no_activity_leads": s.no_activity_leads,
            "data_quality_issues": dq.total_data_quality_issues,
            "missing_contact_count": dq.missing_contact_count,
            "missing_salesperson_count": dq.missing_salesperson_count,
            "missing_stage_count": dq.missing_stage_count,
            # ── Short-name aliases — additive by design; do NOT "tidy up" ────────────
            # app.js:146 matches these keys against [data-kpi-value="<key>"], which
            # _kpi_card.html:81 renders from the `sparkline_metric` macro argument.
            #
            # These short names once had FOUR consumers, because that one macro argument
            # fed four attributes. Three of them — data-sparkline, data-kpi-trend and
            # data-sparkline-metric — were removed with the fabricated sparklines and
            # trend badges, and so were their readers: _METRIC_MAP, the /sparkline route,
            # the ?metric= query value, the trend-badge selector, and the hardcoded
            # colour array in charts.js that no linter or rename tool would have flagged.
            #
            # The short names now have exactly ONE consumer: app.js:146. That makes
            # data-kpi-value SINGLY GUARDED. Nothing else in the codebase reads these
            # names, so nothing else can break loudly if one is renamed or deleted —
            # the only thing standing between a rename here and five KPI cards silently
            # never refreshing again is
            # tests/unit/core/test_kpi_vocabulary_consistency.py.
            #
            # The five keys below duplicate five keys above ON PURPOSE. Deleting them
            # silently stops five of the seven KPI cards from ever refreshing — the
            # state that shipped in 9286a7b and went unnoticed until 2026-08-04,
            # because the first server-rendered paint is always correct.
            "critical": s.critical_overdue,
            "overdue": s.overdue_followups,
            "missing_contact": dq.missing_contact_count,
            "missing_salesperson": dq.missing_salesperson_count,
            "data_quality": dq.total_data_quality_issues,
        },
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/heatmap", summary="Salesperson × Stage overdue heatmap matrix")
@limiter.limit("60/minute")
async def dashboard_heatmap(
    request: Request,
    user: str = Depends(get_current_user),
    service: CrmService = Depends(get_crm_service),
    top_n: Optional[int] = Query(10, ge=1, le=50, description="Max salespersons to include"),
) -> dict:
    data = await service.summary()
    matrix_rows = data.followup_risk.overdue_matrix_by_team_salesperson_stage

    if not matrix_rows:
        return {"ok": True, "rows": [], "cols": [], "data": [], "max": 0}

    # Collect unique salespersons (top N by total overdue) and stages
    sp_totals: dict[str, int] = {}
    stages: list[str] = []
    stage_set: set[str] = set()
    for r in matrix_rows:
        sp_totals[r.salesperson_name] = sp_totals.get(r.salesperson_name, 0) + r.overdue_count
        if r.stage_name not in stage_set:
            stages.append(r.stage_name)
            stage_set.add(r.stage_name)

    top_sp = sorted(sp_totals, key=lambda k: sp_totals[k], reverse=True)[:top_n]

    # Build cell lookup
    cell: dict[tuple[str, str], int] = {}
    for r in matrix_rows:
        key = (r.salesperson_name, r.stage_name)
        cell[key] = cell.get(key, 0) + r.overdue_count

    grid = [[cell.get((sp, st), 0) for st in stages] for sp in top_sp]
    max_val = max((v for row in grid for v in row), default=0)

    return {
        "ok": True,
        "rows": top_sp,
        "cols": stages,
        "data": grid,
        "max": max_val,
    }
