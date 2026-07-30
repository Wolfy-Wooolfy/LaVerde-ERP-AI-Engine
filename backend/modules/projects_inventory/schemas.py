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

# The 6 status buckets, in lifecycle order (domain.BUCKET_ORDER). A unit's bucket is
# DERIVED from its documents (contracts, then live reservations), not from its raw
# rs.structure.unit.state — `unclassified` is the tail with neither.
BucketKey = Literal[
    "available", "reserved", "under_review", "contracted", "delivered", "unclassified"
]

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
    buckets: list[BucketCount]        # always exactly 6, in BUCKET_ORDER; sum(count) == total_units
    sold_pct: float                   # (contracted + delivered) ÷ total_units * 100
    is_early_stage: bool              # sold_pct < EARLY_STAGE_SOLD_PCT_THRESHOLD (display badge)


class ProjectsInventoryOverview(BaseModel):
    total_units: int
    buckets: list[BucketCount]        # always exactly 6, in BUCKET_ORDER; sum(count) == total_units
    sold_pct: float                   # portfolio (contracted + delivered) ÷ total_units * 100

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
    buckets: list[BucketCount]        # always exactly 6, in BUCKET_ORDER; sum == total_units
    sold_pct: float                   # (contracted + delivered) ÷ total_units * 100


class DrillUnitRow(BaseModel):
    """One unit leaf row (building level). `code` is the human-readable unique id; the UI
    shows a badge for `bucket` — the DERIVED bucket the panel header counted, which
    legitimately differs from `state`, the raw rs.structure.unit.state."""
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
    buckets: list[BucketCount]        # always exactly 6, in BUCKET_ORDER; sum == total_units
    sold_pct: float                   # (contracted + delivered) ÷ total_units * 100

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
    sold_realized_value: float                 # (c) Σ contract.sales_price over sold-with-contract
    sold_contracted_area: float                # (d) Σ total_area over ALL sold units, m²
    sold_list_value: float                     # (e) Σ amount over ALL sold units ("if at list")
    sold_with_contract_list_value: float       # (e′) Σ amount over sold-with-contract units
    sold_with_contract_area: float             # (e″) Σ total_area over sold-with-contract, m²
    no_contract_count: int                     # (n) sold_units_count − with-contract count
    no_contract_list_value: float              # (n′) list value of sold units with no contract
    gap_abs: float                             # (f) sold_with_contract_list_value − sold_realized
    gap_pct: float                             # (f) gap_abs / sold_with_contract_list_value * 100
    capture_pct: float                         # sold_realized_value / sold_with_contract_list_value
    pct_units_below_list: float                # (g) below ÷ sold_with_contract * 100
    avg_price_per_m2_realized: float           # (h) sold_realized_value / sold_with_contract_area
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


# ── Inventory Data Quality (admin-only; read-only review tool, all 3 projects) ─
# A — sold unit with no contract; B — broken hierarchy chain; C — sold unit with no
# list price. Each flagged unit is one DataQualityItem with a stable defect_type and a
# concise, language-neutral technical `detail`. La Puerta's unpriced AVAILABLE units are
# never flagged (Check C looks at sold units only).

# Stable per-item defect kinds. A → no_contract; B → one of the three chain breaks;
# C → no_list_price.
DefectType = Literal[
    "no_contract", "phase_project", "zone_phase", "building_zone", "no_list_price"
]
# Stable per-check keys (drive the localized section names + CSV file names).
CheckKey = Literal["no_contract", "broken_hierarchy", "no_list_price"]


class DataQualityItem(BaseModel):
    unit_id: int
    code: str                          # human-readable unique unit code (e.g. "AF135-7-404")
    project_name: str
    defect_type: DefectType
    detail: str                        # concise technical string (may be empty, e.g. Check C)


class DataQualityCheck(BaseModel):
    key: CheckKey
    count: int                         # == len(items)
    items: list[DataQualityItem]       # sorted by project_name then code


# ── Check D — implausible list price/m² (NC + Cassette; admin-only, read-only) ─
# Flags PRICED units (sold AND unsold) whose list price/m² is implausibly high vs what
# comparable units realize — a list-price data error to fix in Odoo. Three tiers fire,
# deduped to one shown signal by precedence: peer (Tier 1) → type (Tier 2a) →
# impossible (Tier 2b). Scoped to New Capital + Cassette; La Puerta excluded. This is a
# SEPARATE object on the response — it is NOT folded into `checks`/`total_issues` (those
# stay the A/B/C completeness defects).

# The shown anchor's origin: a peer-group median, a unit-type median, or a unit-type max.
ListPriceSignal = Literal["peer", "type", "impossible"]
# Whether the flagged unit is sold (contracted/delivered) or still unsold.
UnitSaleState = Literal["sold", "unsold"]


class DataQualityListPriceRow(BaseModel):
    """One Check-D flagged unit — its list price/m² is implausibly high vs comparable
    realized prices. `meter_price` is the editable per-m² field; for these rows it equals
    `list_pm2`. `ratio` = list_pm2 / anchor_realized_pm2."""
    unit_id: int
    code: str
    project_name: str
    unit_type_name: str
    state: UnitSaleState
    list_pm2: float                    # amount / total_area (the implausible list price/m²)
    meter_price: float                 # unit.meter_price (== list_pm2 for these rows)
    anchor_realized_pm2: float         # the comparable realized price/m² the list dwarfs
    ratio: float                       # list_pm2 / anchor_realized_pm2
    list_total: float                  # unit.amount (the list total)
    signal: ListPriceSignal


class DataQualityListPriceThresholds(BaseModel):
    """The tunable named constants the Check-D run used (echoed for the UI footnote)."""
    list_trust_k: float                # Tier 1 multiplier (OUTLIER_LIST_TRUST_K)
    type_k: float                      # Tier 2a multiplier (DQ_LIST_TYPE_K)
    type_spread_max: float             # Tier 2a low-spread gate (DQ_LIST_TYPE_SPREAD_MAX)
    impossible_k: float                # Tier 2b multiplier (DQ_LIST_IMPOSSIBLE_K)
    min_group_size: int                # peer eligibility + min sold per type baseline


class DataQualityListPriceCheck(BaseModel):
    key: Literal["implausible_list_price"]
    count: int                         # == len(items) == tier1 + tier2a + tier2b
    items: list[DataQualityListPriceRow]   # flagged units, sorted by ratio desc
    tier1_count: int                   # shown signal "peer"
    tier2a_count: int                  # shown signal "type"
    tier2b_count: int                  # shown signal "impossible"
    evaluated_count: int               # priced units (amount>0 & area>0) examined in scope
    unevaluable_count: int             # priced units with no eligible peer group / type baseline
    thresholds: DataQualityListPriceThresholds


class DataQualityOverview(BaseModel):
    checks: list[DataQualityCheck]     # A (no_contract), B (broken_hierarchy), C (no_list_price)
    total_issues: int                  # Σ per-check counts (A/B/C only)
    check_d: DataQualityListPriceCheck  # D — implausible list price (NC + Cassette), separate

    reference_date: str                # Cairo-local YYYY-MM-DD
    as_of: str                         # UTC ISO 8601 of the query
    cache_status: Literal["fresh", "cached"]
    rpc_duration_ms: int               # 0 when served from cache


# ── Slice 2.5 — Pricing Outliers (NC + Cassette; La Puerta excluded) ───────────
# Two vintage-controlled signals over the SOLD-with-contract population. Section A =
# peer realized price/m² outliers (Tukey + min-deviation, vintage-bucketed peer groups);
# Section B = discount outliers vs the unit's own list price. A unit flagged in BOTH is
# "confirmed". Realized = contracted value (rs.contract.sales_price), NOT cash collected.

# A unit cheaper than its peers (below the lower fence) vs dearer (above the upper).
OutlierDirection = Literal["below", "above"]
# Sold far below own list (deep discount) vs above own list (premium).
OutlierKind = Literal["deep", "premium"]


class PricingOutlierARow(BaseModel):
    """One Section-A row — a unit whose realized price/m² is an outlier within its
    (zone, unit-type, vintage-bucket) peer group."""
    unit_id: int
    code: str
    project_id: int
    project_name: str
    zone_name: str
    unit_type_name: str
    vintage_bucket_label: str          # e.g. "2022–2023"
    sale_date: str                     # YYYY-MM-DD (rs.payment.term.contract_date)
    realized_pm2: float                # realized_total / total_area
    group_median_pm2: float            # peer-group median realized price/m²
    deviation_pct: float               # signed (realized_pm2 − median)/median * 100
    direction: OutlierDirection
    is_confirmed: bool                 # also flagged in Section B


class PricingOutlierBRow(BaseModel):
    """One Section-B row — a unit sold far from its own list price."""
    unit_id: int
    code: str
    project_id: int
    project_name: str
    unit_type_name: str
    sale_date: str                     # YYYY-MM-DD
    list_total: float                  # unit.amount (list)
    realized_total: float              # Σ non-cancel contract.sales_price
    discount_pct: float                # (list − realized)/list * 100 (signed)
    peer_median_discount_pct: float | None = None  # eligible-group median discount; None = small-group fallback
    kind: OutlierKind
    is_confirmed: bool                 # also flagged in Section A


class PricingOutliersProjectCount(BaseModel):
    project_id: int
    project_name: str
    section_a_count: int
    section_b_count: int
    confirmed_count: int


class PricingOutliersThresholds(BaseModel):
    """The tunable named constants the run used — echoed so the UI footnote + any tuning
    read the live values, never a hardcoded copy."""
    min_group_size: int
    iqr_mult: float
    min_dev_pct: float
    deep_discount_pct: float
    premium_pct: float
    vintage_bucket_years: int


class PricingOutliersOverview(BaseModel):
    section_a: list[PricingOutlierARow]   # sorted by |deviation_pct| desc
    section_b: list[PricingOutlierBRow]   # deep (discount desc) first, then premium

    section_a_count: int
    section_a_below_count: int
    section_a_above_count: int
    section_b_count: int
    section_b_deep_count: int
    section_b_premium_count: int
    confirmed_count: int                  # flagged in BOTH sections

    insufficient_peers_count: int         # units in sub-MIN_GROUP_SIZE peer groups (footnote)
    eligible_group_count: int             # peer groups large enough to evaluate
    population_count: int                 # in-scope sold-with-contract-and-sale-date units

    projects: list[PricingOutliersProjectCount]
    project_count: int
    thresholds: PricingOutliersThresholds

    reference_date: str                   # Cairo-local YYYY-MM-DD
    as_of: str                            # UTC ISO 8601 of the query
    cache_status: Literal["fresh", "cached"]
    rpc_duration_ms: int                  # 0 when served from cache
