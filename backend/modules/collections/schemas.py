"""
Pydantic response schemas for Collections KPIs.

KPI 2 — Late Uncollected (Stage 2, PATH C applied).
KPI 7 — Expected Collections Forecast (Stage 1, Phase 1).
"""

from datetime import date
from typing import Literal

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
    cheques_record_count: int | None  # null under Alternative B (Decision 9.1)
    drill_down_domain: list  # well-formed Odoo domain
    cheques_drill_down_domain: list | None  # null under Alternative B (Decision 9.1)


class ExpectedCollectionsForecastResponse(BaseModel):
    buckets: dict[str, ForecastBucket]  # keyed by bucket name
    currency: Literal["EGP"]
    today_cairo: date
    cache_status: Literal["fresh", "cached"]
    rpc_duration_ms: int
    data_quality_warning: str | None
