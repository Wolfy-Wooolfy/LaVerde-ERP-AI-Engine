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
    missing_date_count: int             # Running-contract employees with null date_start; excluded from bands
    total_employed: int                 # == sum(band.count) + missing_date_count == distinct Running-contract employees
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
    buckets: list[PayrollRiskBucket]               # 6 entries, fixed order: expired, expiring_45d, expiring_90d, expiring_135d, beyond_135d, open_ended
    department_breakdown_expired: list[DepartmentRiskRow]
    department_breakdown_expiring_45d: list[DepartmentRiskRow]
    archived_with_running_count: int               # employed employees with stale archive flag (data-quality signal)
    active_flag_no_running_count: int              # active=True, no Running contract (data-quality signal)
    active_flag_no_running_exit_gap: int           # subset: only close/cancel contracts (departed, unarchived)
    active_flag_no_running_incoming: int           # subset: has draft contract (new hire pending activation)
    active_flag_no_running_data_gap: int           # subset: no contract record at all
    total_employed: int                            # == sum(6 buckets) == KPI A headcount == distinct Running-contract employees
    reference_date: str                            # Cairo TZ ISO date YYYY-MM-DD
    as_of: str                                     # ISO 8601 UTC datetime of the query
    cache_status: Literal["fresh", "cached"]
    rpc_duration_ms: int                           # 0 when served from cache


# ── KPI D — Department Payroll Cost ──────────────────────────────────────────


class DepartmentCostRow(BaseModel):
    department_id: int | None
    department_name: str
    running_contract_count: int
    total_wage: float | None   # null iff suppressed: "Other" pool count < 3


class DepartmentCostResponse(BaseModel):
    rows: list[DepartmentCostRow]
    grand_total_wage: float                  # SUM over all open contracts; always present
    total_running_contracts: int             # distinct Running-contract employees; == KPI A headcount
    currency: Literal["EGP"]
    basis: Literal["monthly"]
    reference_date: str                      # Cairo TZ ISO date YYYY-MM-DD
    as_of: str                               # ISO 8601 UTC datetime of the query
    cache_status: Literal["fresh", "cached"]
    rpc_duration_ms: int                     # 0 when served from cache


# ── F2 — Department Staff Drill-Down ─────────────────────────────────────────


class StaffMemberRow(BaseModel):
    employee_id:    int
    employee_name:  str
    job_title:      str          # "—" if no job (null job_id)
    date_start:     str | None   # ISO date YYYY-MM-DD, or null if not set
    tenure_years:   float | None # (today - date_start).days / 365.25; null if no date_start
    contract_state: Literal["open"]  # always "open" — filter guarantees this


class DepartmentStaffResponse(BaseModel):
    department_id:         int
    department_name:       str
    headcount:             int          # == len(staff); reconciles with KPI A by_department
    total_wage:            float | None # dept SUM(wage) from KPI D; null only if k-anon suppressed
    pct_of_total_payroll:  float | None # total_wage / grand_total_wage * 100; null if total_wage null
    avg_cost_per_head:     float | None # total_wage / headcount; null if total_wage null
    staff:                 list[StaffMemberRow]
    currency:              Literal["EGP"]
    basis:                 Literal["monthly"]
    reference_date:        str  # Cairo TZ ISO date YYYY-MM-DD
    as_of:                 str  # ISO 8601 UTC datetime
    rpc_duration_ms:       int  # staff RPC only; dept_cost is typically cached (0 ms)


# ── F3 — Employee Profile Drill-Down ─────────────────────────────────────────


class EmployeeProfileResponse(BaseModel):
    employee_id:      int
    name:             str                    # trimmed; always present
    job_title:        str | None             # from contract job_id; "—" if no job
    department_name:  str | None             # from contract department_id display name
    manager_name:     str | None             # from hr.employee parent_id (trimmed); null if no manager
    hire_date:        str | None             # open contract date_start; ISO yyyy-mm-dd
    tenure_years:     float | None           # (today − date_start).days / 365.25; null if no date_start
    contract_status:  Literal["Running"]     # always "Running" — filter guarantees state='open'
    contract_end:     str | None             # open contract date_end ISO; null when is_open_ended
    is_open_ended:    bool                   # True when date_end is False/null
    location:         str | None             # work_location_id display name; null if empty
    as_of:            str                    # ISO 8601 UTC datetime of the query
    rpc_duration_ms:  int
