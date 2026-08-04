"""
Dashboard JSON API endpoints — fast data for frontend AJAX refresh.
GET /api/v1/dashboard/kpis       — flat KPI snapshot
GET /api/v1/dashboard/sparkline  — 7-day synthetic trend for a metric
GET /api/v1/dashboard/heatmap    — salesperson × stage overdue matrix
"""

import random
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

from backend.api.deps import get_crm_service, get_current_user
from backend.core.limiter import limiter
from backend.modules.crm.service import CrmService

router = APIRouter(prefix="/dashboard", tags=["dashboard-api"])

_SPARKLINE_VARIANCE = 0.15  # ±15% synthetic variance for trend lines


def _synthetic_sparkline(current: int, days: int = 7) -> list[int]:
    """Generate a plausible trend ending at current value."""
    if current == 0:
        return [0] * days
    rng = random.Random(current)
    points = []
    for _i in range(days - 1):
        factor = 1.0 + rng.uniform(-_SPARKLINE_VARIANCE, _SPARKLINE_VARIANCE)
        base = max(0, int(current * factor))
        points.append(base)
    points.append(current)
    return points


def _trend_pct(series: list[int]) -> str:
    if len(series) < 2 or series[0] == 0:
        return "0%"
    change = (series[-1] - series[0]) / series[0] * 100
    sign = "+" if change >= 0 else ""
    return f"{sign}{change:.1f}%"


def _day_labels(days: int) -> list[str]:
    today = datetime.now(timezone.utc).date()
    return [(today - timedelta(days=days - 1 - i)).strftime("%b %d") for i in range(days)]


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
        },
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


_METRIC_MAP = {
    "total_leads": lambda s, dq: s.total_leads,
    "overdue": lambda s, dq: s.overdue_followups,
    "critical": lambda s, dq: s.critical_overdue,
    "followups_today": lambda s, dq: s.followups_today,
    "planned": lambda s, dq: s.planned_followups,
    "no_activity": lambda s, dq: s.no_activity_leads,
    "missing_contact": lambda s, dq: dq.missing_contact_count,
    "missing_salesperson": lambda s, dq: dq.missing_salesperson_count,
    "data_quality": lambda s, dq: dq.total_data_quality_issues,
}


@router.get("/sparkline", summary="7-day synthetic trend data for a KPI")
@limiter.limit("120/minute")
async def dashboard_sparkline(
    request: Request,
    metric: str = Query("overdue", description="Metric name"),
    days: int = Query(7, ge=3, le=30),
    user: str = Depends(get_current_user),
    service: CrmService = Depends(get_crm_service),
) -> dict:
    getter = _METRIC_MAP.get(metric)
    if getter is None:
        return {"ok": False, "error": f"Unknown metric: {metric}. Valid: {list(_METRIC_MAP)}"}

    data = await service.summary()
    current = getter(data.summary, data.data_quality)
    series = _synthetic_sparkline(current, days)
    return {
        "ok": True,
        "metric": metric,
        "days": days,
        "data": series,
        "labels": _day_labels(days),
        "current": current,
        "trend": _trend_pct(series),
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
