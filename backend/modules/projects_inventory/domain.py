"""
Projects Inventory — domain configuration & invariants (LOCKED DECISIONS).

This module holds ONLY config (the model name, the status→bucket mapping, the
fixed bucket order, the early-stage threshold). It contains no Odoo I/O. The
per-project names and every count are DERIVED at runtime from live data — never
hardcoded here. See docs/PROJECTS_INVENTORY_DISCOVERY.md.
"""

# The inventory model — every unit denormalises the full hierarchy chain
# (project_id, phase_id, zone_id, building_id), all 100% populated (§1).
UNIT_MODEL = "rs.structure.unit"

# Denormalised many2one hierarchy fields, in descending order. ANY of these can be
# passed to the bucketing helper as the grouping key — Slice 1 groups by project_id;
# phase_id / zone_id / building_id are ready for the next slice with no service change.
GROUP_FIELDS: tuple[str, ...] = ("project_id", "phase_id", "zone_id", "building_id")

# ── Slice 1b — hierarchy drill-down (LOCKED) ──────────────────────────────────
# The drill walks Project → Phase → Zone → Building → Unit. A drill request names the
# level you drill INTO (the parent); the response returns that scope's children.
DRILL_LEVELS: tuple[str, ...] = ("project", "phase", "zone", "building")

# The denormalised m2o on rs.structure.unit that identifies each level — used to filter
# the unit set down to one parent scope (e.g. level="phase" → keep units whose
# phase_id == parent_id). 100% populated on every unit (§1/§3.1).
LEVEL_FIELD: dict[str, str] = {
    "project": "project_id",
    "phase": "phase_id",
    "zone": "zone_id",
    "building": "building_id",
}

# For a GROUP level (project/phase/zone), the unit m2o to group its children by.
# "building" has no entry — it is the LEAF (units are listed, not grouped).
CHILD_FIELD: dict[str, str] = {
    "project": "phase_id",
    "phase": "zone_id",
    "zone": "building_id",
}

# The name of the child level each level produces (drives the panel labels). The
# building level produces the unit leaf list.
CHILD_LEVEL: dict[str, str] = {
    "project": "phase",
    "phase": "zone",
    "zone": "building",
    "building": "unit",
}

# The single leaf level — its children are individual units, not a grouped breakdown.
LEAF_LEVEL = "building"

# ── Status buckets (LOCKED — §2). Stable keys used by the API + template. ──────
BUCKET_AVAILABLE = "available"
BUCKET_RESERVED = "reserved"
BUCKET_CONTRACTED = "contracted"   # the "sold" bucket (label: Contracted)

# Fixed output order — every overall/per-project bucket list is emitted in this order.
BUCKET_ORDER: tuple[str, ...] = (BUCKET_AVAILABLE, BUCKET_RESERVED, BUCKET_CONTRACTED)

# Map each live rs.structure.unit.state value → a board bucket (LOCKED, §2). The 5
# live states are confirmed and exhaustive; a value outside this map is a material
# data change and is raised on (never silently miscounted) by the service.
STATE_TO_BUCKET: dict[str, str] = {
    "available": BUCKET_AVAILABLE,
    "initial": BUCKET_RESERVED,      # "Initial Reserve" → Reserved
    "reserved": BUCKET_RESERVED,
    "contracted": BUCKET_CONTRACTED,
    "delivered": BUCKET_CONTRACTED,  # handed-over units count as sold
}

# "Sold" = the contracted bucket; sold% = contracted ÷ total.
SOLD_BUCKET = BUCKET_CONTRACTED

# A project whose sold% is below this is flagged "Early stage" in the UI (a subtle,
# neutral badge). Display-only — it changes no count. La Puerta (≈3.6% sold, the
# not-yet-loaded project) is the live example this surfaces.
EARLY_STAGE_SOLD_PCT_THRESHOLD = 10.0


# ── Slice 2 — Value & Area (LOCKED) ───────────────────────────────────────────
# Confirmed live project IDs (read-only discovery 2026-06-19,
# docs/PROJECTS_INVENTORY_PRICING_DISCOVERY.md): New Capital = 1, Cassette = 2 are
# fully priced on `amount` (99.9% / 100%). La Puerta = 3 is EXCLUDED from every value
# figure — only 9/138 of its units carry an `amount` (its `meter_price` is a decoy).
# The value page is scoped to these two projects ONLY.
VALUE_SCOPE_PROJECT_IDS: tuple[int, ...] = (1, 2)
VALUE_EXCLUDED_PROJECT_IDS: tuple[int, ...] = (3,)   # La Puerta — pricing incomplete

# Pricing/area fields on rs.structure.unit (all stored — confirmed via fields_get).
#   amount     — LIST price ("Total Unit Price", May-2026 reload, indicative).
#   total_area — unit area in m² ("Total Unit Area"); 100% on NC+Cassette.
#   meter_price — the per-m² LIST price the data team edits ("Meter Price"). For the units
#                 Check D flags it equals amount/total_area, so it names the field to fix.
# net_area is deliberately NOT used (≈7% coverage — a trap).
UNIT_AMOUNT_FIELD = "amount"
UNIT_AREA_FIELD = "total_area"
UNIT_METER_PRICE_FIELD = "meter_price"

# The realized side — rs.contract. sales_price is the authoritative CONTRACTED deal
# value per sold unit (== installments_total). It is the value the customer committed
# to over the plan — NOT cash collected. unit_id joins one hop back to the unit.
CONTRACT_MODEL = "rs.contract"
CONTRACT_PRICE_FIELD = "sales_price"
CONTRACT_UNIT_FIELD = "unit_id"
CONTRACT_STATE_FIELD = "state"
# A cancelled contract carries no committed value — excluded from the realized join.
CONTRACT_CANCEL_STATE = "cancel"

# The unit states that count as SOLD for the value join (the LOCKED contracted bucket:
# contracted + delivered). Realized value is summed only over units in these states.
SOLD_STATES: frozenset[str] = frozenset(
    s for s, b in STATE_TO_BUCKET.items() if b == SOLD_BUCKET
)
# The single state that counts as AVAILABLE for list-value/area (the available bucket).
AVAILABLE_STATES: frozenset[str] = frozenset(
    s for s, b in STATE_TO_BUCKET.items() if b == BUCKET_AVAILABLE
)


# ── Slice 2.5 — Pricing Outliers (LOCKED) ─────────────────────────────────────
# A board-facing READ-ONLY view that surfaces sold units priced/sold anomalously,
# scoped to the same NC + Cassette population as Slice 2 (La Puerta excluded). Two
# vintage-CONTROLLED signals — see docs/PROJECTS_INVENTORY_PRICING_DISCOVERY.md §5.
#
# The unit's product-type peer attribute (→ rs.structure.unit.type). High cardinality
# (163 distinct, 99% coverage); used coarsely as one leg of the Section-A peer key. A
# unit with no unit_type_id simply groups under a None type (still language-neutral).
UNIT_TYPE_FIELD = "unit_type_id"

# VINTAGE / sale date. The TRUE sale date is NOT on the contract (its reservation_date /
# create_date are Nov-2025 migration stamps). It lives one more hop out:
#   unit → rs.contract (non-cancel).payment_term_id → rs.payment.term.contract_date
# Confirmed live (probe 2026-06-22): a `date` field, 100% coverage over the in-scope
# non-cancel contracts, spanning 2018–2025, and every unit's contracts agree on it.
CONTRACT_PAYMENT_TERM_FIELD = "payment_term_id"
PAYMENT_TERM_MODEL = "rs.payment.term"
PAYMENT_TERM_DATE_FIELD = "contract_date"

# Vintage bucket width in years — a 2-year bucket (e.g. 2022 & 2023 → "2022–2023"),
# bucket = (year // VINTAGE_BUCKET_YEARS) * VINTAGE_BUCKET_YEARS. Coarse enough to keep
# peer groups populated, fine enough to control the dominant time confound on price/m².
VINTAGE_BUCKET_YEARS = 2

# Section A — peer realized price/m² outliers (vintage-controlled). Peer key =
# (zone_id, unit_type_id, vintage_bucket). A unit is FLAGGED iff BOTH hold:
#   (i)  Tukey fence: realized_pm2 < Q1 − IQR_MULT·IQR  OR  > Q3 + IQR_MULT·IQR, AND
#   (ii) |realized_pm2 − group_median| / group_median * 100 ≥ MIN_DEV_PCT.
# Groups with < MIN_GROUP_SIZE in-scope units are NOT evaluated (counted as
# "insufficient peers" for the footnote). All three are TUNABLE named constants.
OUTLIER_MIN_GROUP_SIZE = 5
OUTLIER_IQR_MULT = 1.5
OUTLIER_MIN_DEV_PCT = 15.0

# Section B — discount outliers vs the unit's OWN list price (amount). discount_pct =
# (amount − realized_total) / amount * 100, only when amount > 0. FLAG premium (sold above
# own list) if discount_pct ≤ PREMIUM_PCT (unchanged). Both are TUNABLE named constants.
OUTLIER_DEEP_DISCOUNT_PCT = 25.0
OUTLIER_PREMIUM_PCT = -10.0

# Section B DEEP-discount refinement (cohort-relative + list-trust guard). A flat
# "discount ≥ 25%" over-flags two non-anomalies: the deliberate ~25% house-standard
# discount, and list-price data errors. So a deep flag now means GENUINELY unusual:
#   (1) LIST-TRUST guard — a unit whose list price/m² exceeds OUTLIER_LIST_TRUST_K × its
#       peer group's MEDIAN realized price/m² has an implausible list (a data error, not a
#       real discount) and is NEVER deep-flagged.
#   (2) COHORT rule — in an eligible peer group (zone × unit-type × 2-yr vintage, the SAME
#       groups Section A uses) a discount is deep iff it is a Tukey outlier
#       (> Q3 + IQR_MULT·IQR of the group's discounts) AND ≥ OUTLIER_DEEP_DISCOUNT_PCT.
#       Units in sub-MIN_GROUP_SIZE groups have no peers to compare against → an absolute
#       ≥ OUTLIER_DEEP_SMALLGROUP_PCT fallback cut (guard not applied). All TUNABLE.
OUTLIER_LIST_TRUST_K = 2.0
OUTLIER_DEEP_SMALLGROUP_PCT = 35.0


# ── Inventory Data Quality — Check D (implausible list price) ──────────────────
# A READ-ONLY admin check that flags PRICED units (sold AND unsold) whose list price/m²
# (amount ÷ total_area) is implausibly high vs what comparable units actually realize —
# a list-price DATA ERROR for the team to correct in Odoo (dominantly the HS-Studio
# "65,000/m²" regime, where studios realize ~20,000/m²). Scoped to NC + Cassette
# (VALUE_SCOPE_PROJECT_IDS; La Puerta excluded). Baselines come from SOLD units (= units
# with realized value) in that scope. A unit is flagged if ANY tier fires (deduped, the
# shown signal by precedence Tier 1 → Tier 2a → Tier 2b):
#   Tier 1 (peer)       — sold unit in an eligible (≥ OUTLIER_MIN_GROUP_SIZE sold) peer
#       group (zone × unit-type × 2-yr vintage), list_pm2 > OUTLIER_LIST_TRUST_K × the
#       group's MEDIAN realized price/m². EXACT mirror of the Slice 2.5 list-trust guard.
#   Tier 2a (type)      — unit whose unit-type has a baseline (≥ OUTLIER_MIN_GROUP_SIZE
#       sold) AND is LOW-SPREAD (type realized max/median < DQ_LIST_TYPE_SPREAD_MAX),
#       list_pm2 > DQ_LIST_TYPE_K × the type's MEDIAN realized price/m². Catches the studio
#       regime (sold + unsold) without over-flagging genuinely wide-priced unit types.
#   Tier 2b (impossible) — unit whose type has a baseline, list_pm2 > DQ_LIST_IMPOSSIBLE_K
#       × the type's MAX realized price/m². A spread-independent area-error catch-all
#       (e.g. total_area entered as 1). All three are TUNABLE named constants.
DQ_LIST_TYPE_K = 3.0
DQ_LIST_TYPE_SPREAD_MAX = 2.5
DQ_LIST_IMPOSSIBLE_K = 5.0
