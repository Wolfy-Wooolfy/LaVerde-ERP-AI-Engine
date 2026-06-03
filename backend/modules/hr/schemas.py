"""
Pydantic response schemas for HR KPIs.

KPI A — Headcount   (M5-S1)
"""

from typing import Literal

from pydantic import BaseModel


class DepartmentHeadcountRow(BaseModel):
    department_id: int | None
    department_name: str
    count: int


class JobHeadcountRow(BaseModel):
    job_id: int | None
    job_name: str
    count: int


class HeadcountResponse(BaseModel):
    headcount: int                          # distinct Running-contract employees (true headcount)
    by_department: list[DepartmentHeadcountRow]
    by_job: list[JobHeadcountRow]
    incoming_count: int                     # distinct employees with draft contract (not in headcount)
    active_flag_count: int                  # hr.employee.active=True count — NOT headcount; divergence indicator only
    active_without_running: int             # active=True employees with no Running contract (data-quality signal)
    reference_date: str                     # Cairo TZ ISO date YYYY-MM-DD
    as_of: str                              # ISO 8601 UTC datetime of the query
    cache_status: Literal["fresh", "cached"]
    rpc_duration_ms: int                    # 0 when served from cache


# ── KPI B — Tenure Distribution ───────────────────────────────────────────────


class TenureBandRow(BaseModel):
    band: str
    count: int


class TenureDistributionResponse(BaseModel):
    bands: list[TenureBandRow]          # always exactly 5 entries, fixed order
    missing_date_count: int             # active employees where first_contract_date = False
    total_active: int                   # == sum(band.count) + missing_date_count
    reference_date: str                 # ISO date (Cairo TZ) used for band computation
    as_of: str                          # ISO 8601 UTC datetime of the query
    cache_status: Literal["fresh", "cached"]
    rpc_duration_ms: int                # 0 when served from cache


# ── KPI C — Payroll Risk Dashboard ───────────────────────────────────────────


class PayrollRiskBucket(BaseModel):
    label: str
    count: int


class DepartmentRiskRow(BaseModel):
    department_id: int | None
    department_name: str
    count: int


class PayrollRiskDashboardResponse(BaseModel):
    buckets: list[PayrollRiskBucket]               # always 7, fixed order
    department_breakdown_expired: list[DepartmentRiskRow]
    department_breakdown_expiring_45d: list[DepartmentRiskRow]
    orphan_contracts_count: int
    total_active: int                              # == sum(b.count for b in buckets)
    reference_date: str                            # Cairo TZ ISO date YYYY-MM-DD
    as_of: str                                     # ISO 8601 UTC datetime of the query
    cache_status: Literal["fresh", "cached"]
    rpc_duration_ms: int                           # 0 when served from cache
