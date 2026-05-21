"""
Pydantic response schemas for Collections KPIs.

KPI 2 — Late Uncollected (Stage 2, PATH C applied).
KPI 7 — Expected Collections Forecast (Stage 1, Phase 1).
Stage 5 — Drill-down endpoints (Decision 14.1-14.13).
"""

from datetime import date
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel


class LateUncollectedResponse(BaseModel):
    value: float
    currency: Literal["EGP"]
    record_count: int
    cheques_in_pipeline: float
    cheques_record_count: int | None
    drill_down_domain: list
    cheques_drill_down_domain: list | None
    as_of: str
    cache_status: Literal["fresh", "cached"]
    rpc_duration_ms: int
    domain: list  # legacy — same value as drill_down_domain
    data_quality_warning: str | None


class ForecastBucket(BaseModel):
    bucket: Literal["this_month", "this_quarter", "this_half", "this_year"]
    period_start: date
    period_end: date
    amount: float
    record_count: int
    due_amount: float
    cheques_in_pipeline: float
    cheques_record_count: int | None  # int >= 0 from Stage 5 (Decision 14.6); was None under Alt B
    drill_down_domain: list  # well-formed Odoo domain
    cheques_drill_down_domain: list | None  # null under Alternative B (Decision 9.1)


class ExpectedCollectionsForecastResponse(BaseModel):
    buckets: dict[str, ForecastBucket]  # keyed by bucket name
    currency: Literal["EGP"]
    today_cairo: date
    cache_status: Literal["fresh", "cached"]
    rpc_duration_ms: int
    data_quality_warning: str | None


# ---------------------------------------------------------------------------
# Stage 5 — Drill-down schemas (Decision 14.1 envelope, E2 cursor, E3 req-id)
# ---------------------------------------------------------------------------

class DrilldownMeta(BaseModel):
    request_id: str
    as_of: str
    rpc_duration_ms: int
    page_size: int
    total_count: int
    cursor_current: str | None
    cursor_next: str | None
    has_next: bool
    filters_applied: dict
    sort_applied: dict
    data_quality: dict | None = None  # Decision 14.13: populated by portfolio when project_id=False rows exist


_T = TypeVar("_T")


class DrilldownEnvelope(BaseModel, Generic[_T]):
    version: Literal["1.0"]
    data: _T
    meta: DrilldownMeta


class InstallmentRow(BaseModel):
    record_id: int
    customer_name: str
    project_id: int
    project_name_ar: str
    project_name_en: str
    date: str
    amount: float
    due_amount: float
    paid_amount: float
    actual_paid_amount: float
    pending_cheque: float           # max(paid_amount - actual_paid_amount, 0.0)
    payment_state: str
    late_amount: float              # Decision 14.8: amount - actual_paid_amount (PATH A per-record)


class PortfolioProjectBreakdown(BaseModel):
    project_id: int | None        # None when project_id=False in Odoo (Decision 14.13)
    project_name_ar: str          # "بدون مشروع" when unassigned
    project_name_en: str          # "No Project Assigned" when unassigned
    amount: float
    due_amount: float
    record_count: int


class PortfolioCustomerRow(BaseModel):
    customer_id: int
    customer_name: str
    total_amount: float
    total_paid: float
    total_due: float
    total_actual_paid: float
    record_count: int
    project_breakdown: list[PortfolioProjectBreakdown]


class LateDrilldownData(BaseModel):
    items: list[InstallmentRow]


class ForecastDrilldownData(BaseModel):
    bucket: str           # internal form e.g. "this_month"
    bucket_url_key: str   # URL form e.g. "month"
    items: list[InstallmentRow]


class PortfolioDrilldownData(BaseModel):
    customers: list[PortfolioCustomerRow]


class ProjectDrilldownData(BaseModel):
    project_id: int
    project_name_ar: str
    project_name_en: str
    total_late_uncollected: float   # SUM(due_amount) — identity-equal with KPI 5 (Decision 14.2)
    total_record_count: int
    items: list[InstallmentRow]


class TrendDrilldownData(BaseModel):
    month: str              # YYYY-MM
    items: list[InstallmentRow]


LateDrilldownResponse = DrilldownEnvelope[LateDrilldownData]
ForecastDrilldownResponse = DrilldownEnvelope[ForecastDrilldownData]
PortfolioDrilldownResponse = DrilldownEnvelope[PortfolioDrilldownData]
ProjectDrilldownResponse = DrilldownEnvelope[ProjectDrilldownData]
TrendDrilldownResponse = DrilldownEnvelope[TrendDrilldownData]
