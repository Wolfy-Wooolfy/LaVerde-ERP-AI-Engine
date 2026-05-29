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
    total_active: int
    total_inactive: int
    by_department: list[DepartmentHeadcountRow]
    by_job: list[JobHeadcountRow]
    as_of: str                              # ISO 8601 UTC datetime of the query
    cache_status: Literal["fresh", "cached"]
    rpc_duration_ms: int                    # 0 when served from cache
