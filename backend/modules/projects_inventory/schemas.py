"""
Pydantic v2 response schemas for Projects Inventory.

Slice 1  — GET /api/v1/projects-inventory/overview            -> ProjectsInventoryOverview
Slice 1b — GET /api/v1/projects-inventory/drill/{level}/{id}  -> ProjectsInventoryDrill

Read-only analytics only — counts by sales status, overall + per project + drilled down
the Project → Phase → Zone → Building → Unit hierarchy. No write surface; no
pricing/area/value (later slice).
"""

from typing import Literal

from pydantic import BaseModel

# The 3 status buckets, fixed order (domain.BUCKET_ORDER).
BucketKey = Literal["available", "reserved", "contracted"]

# The level a drill request targets (the parent drilled INTO).
DrillLevel = Literal["project", "phase", "zone", "building"]
# The level each request returns (building → the unit leaf).
ChildLevel = Literal["phase", "zone", "building", "unit"]


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


# ── Slice 1b — hierarchy drill-down ───────────────────────────────────────────


class DrillGroupRow(BaseModel):
    """One child node of a group level (a phase under a project, a zone under a phase,
    a building under a zone). Same shape as a project card on the board."""
    group_id: int
    group_name: str
    total_units: int
    buckets: list[BucketCount]        # always exactly 3, in BUCKET_ORDER; sum == total_units
    sold_pct: float                   # contracted ÷ total_units * 100


class DrillUnitRow(BaseModel):
    """One unit leaf row (building level). `code` is the human-readable unique id; the UI
    shows a badge for `bucket`. `state` is the raw rs.structure.unit.state."""
    unit_id: int
    code: str
    name: str
    state: str
    bucket: BucketKey


class ProjectsInventoryDrill(BaseModel):
    parent_level: DrillLevel          # the level drilled into
    parent_id: int
    parent_name: str
    child_level: ChildLevel           # what this response lists (unit ⇒ leaf)
    is_leaf: bool                     # True only for the building level

    # The drilled scope's own status breakdown (the panel header bar).
    total_units: int
    buckets: list[BucketCount]        # always exactly 3, in BUCKET_ORDER; sum == total_units
    sold_pct: float

    # Exactly one of these is populated: rows for group levels, units for the leaf.
    rows: list[DrillGroupRow]         # child group rows (empty when is_leaf)
    row_count: int
    units: list[DrillUnitRow]         # unit leaf list (empty unless is_leaf)
    unit_count: int

    reference_date: str               # Cairo-local YYYY-MM-DD
    as_of: str                        # UTC ISO 8601 of the query
    cache_status: Literal["fresh", "cached"]
    rpc_duration_ms: int              # 0 when the shared unit set was served from cache


# ── Slice 2 — Value & Area (New Capital + Cassette; La Puerta excluded) ────────
# LIST value = Σ rs.structure.unit.amount (indicative, May-2026 reload).
# REALIZED value = Σ rs.contract.sales_price over sold units — the CONTRACTED deal
# value (== installments_total), NOT cash collected.


class ValueAreaMetrics(BaseModel):
    """The a–i value/area metrics shared by the combined scope and each project."""
    total_units: int                          # all scoped units (avail+reserved+sold)
    available_units_count: int
    sold_units_count: int                     # (i) contracted+delivered
    sold_units_with_contract_count: int       # (i) coverage — sold units with a contract
    sold_units_below_list_count: int          # numerator of pct_units_below_list

    available_list_value: float               # (a) Σ amount over available units
    available_area: float                      # (b) Σ total_area over available units, m²
    sold_realized_value: float                 # (c) Σ contract.sales_price over sold units
    sold_contracted_area: float                # (d) Σ total_area over sold units, m²
    sold_list_value: float                     # (e) Σ amount over sold units ("if at list")
    gap_abs: float                             # (f) sold_list_value − sold_realized_value
    gap_pct: float                             # (f) gap_abs / sold_list_value * 100
    capture_pct: float                         # sold_realized_value / sold_list_value * 100
    pct_units_below_list: float                # (g) below ÷ sold_with_contract * 100
    avg_price_per_m2_realized: float           # (h) sold_realized_value / sold_contracted_area
    sold_pct_units: float                      # sold_units_count ÷ total_units * 100


class ValueAreaProject(ValueAreaMetrics):
    project_id: int
    project_name: str


class ValueAreaOverview(ValueAreaMetrics):
    projects: list[ValueAreaProject]           # one per scoped project, sold_list_value desc
    project_count: int

    reference_date: str                        # Cairo-local YYYY-MM-DD
    as_of: str                                 # UTC ISO 8601 of the query
    cache_status: Literal["fresh", "cached"]
    rpc_duration_ms: int                       # 0 when served from cache
