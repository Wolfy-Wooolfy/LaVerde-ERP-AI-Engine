"""
Pydantic v2 response schemas for Projects Inventory (Slice 1 — Inventory & Availability).

GET /api/v1/projects-inventory/overview -> ProjectsInventoryOverview

Read-only analytics only — counts by sales status, overall + per project. No write
surface; no pricing/area/value (later slice).
"""

from typing import Literal

from pydantic import BaseModel

# The 3 status buckets, fixed order (domain.BUCKET_ORDER).
BucketKey = Literal["available", "reserved", "contracted"]


class BucketCount(BaseModel):
    key: BucketKey
    count: int
    pct: float            # % of the owning total (overall or per-project); 0.0 when total == 0


class ProjectInventory(BaseModel):
    project_id: int
    project_name: str
    total_units: int
    buckets: list[BucketCount]        # always exactly 3, in BUCKET_ORDER; sum(count) == total_units
    sold_pct: float                   # contracted ÷ total_units * 100
    is_early_stage: bool              # sold_pct < EARLY_STAGE_SOLD_PCT_THRESHOLD (display badge)


class ProjectsInventoryOverview(BaseModel):
    total_units: int
    buckets: list[BucketCount]        # always exactly 3, in BUCKET_ORDER; sum(count) == total_units
    sold_pct: float                   # portfolio contracted ÷ total_units * 100

    projects: list[ProjectInventory]  # one per project, sorted by total_units desc
    project_count: int

    reference_date: str               # Cairo-local YYYY-MM-DD
    as_of: str                        # UTC ISO 8601 of the query
    cache_status: Literal["fresh", "cached"]
    rpc_duration_ms: int              # 0 when served from cache
