"""
Projects Inventory — domain configuration & invariants (LOCKED DECISIONS).

This module holds ONLY config (the model names, the document→bucket ranking, the
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

# ── Status buckets (LOCKED — six-bucket DOCUMENT-DRIVEN model, recon 2026-07-30) ─
# Stable keys used by the API + template. A unit's board bucket is DERIVED FROM ITS
# DOCUMENTS — its contracts first, then its live reservations — and the unit's own
# `state` is consulted only as a last-resort fallback. The old unit-state→bucket map
# is GONE: on the live data the unit state disagrees with the documents (e.g. units
# sitting in `reserved`/`initial` with no reservation and no contract at all), so it
# cannot be the source of truth. See services/inventory_service.classify_unit().
BUCKET_AVAILABLE = "available"
BUCKET_RESERVED = "reserved"          # a LIVE reservation hold, no contract yet
BUCKET_UNDER_REVIEW = "under_review"  # a contract exists but is not yet confirmed
BUCKET_CONTRACTED = "contracted"      # a confirmed contract (label: Contracted)
BUCKET_DELIVERED = "delivered"        # a delivered contract (handed over)
BUCKET_UNCLASSIFIED = "unclassified"  # no document AND no trustworthy unit state

# Fixed output order — LIFECYCLE order. Every overall/per-project bucket list is
# emitted in this order. `unclassified` sits last because it is the data-quality
# tail, not a lifecycle stage.
BUCKET_ORDER: tuple[str, ...] = (
    BUCKET_AVAILABLE,
    BUCKET_RESERVED,
    BUCKET_UNDER_REVIEW,
    BUCKET_CONTRACTED,
    BUCKET_DELIVERED,
    BUCKET_UNCLASSIFIED,
)

# The ONE rs.structure.unit.state the classifier trusts as a fallback (precedence
# step c): "no document, genuinely on the shelf". Every OTHER undocumented unit state
# falls through to `unclassified` — that bucket IS the alarm, so the unit axis
# degrades silently by design and never raises.
UNIT_STATE_AVAILABLE = "available"

# The 5 live rs.structure.unit.state values (fields_get-verified 2026-07-30:
# available / initial / reserved / contracted / delivered). The BOARD no longer maps
# these to buckets. This frozen vocabulary exists ONLY for the unit-STATE-based
# slices (value / pricing-outliers / data-quality) whose shared guard still refuses
# to run against a state vocabulary it has never seen.
LOCKED_UNIT_STATES: frozenset[str] = frozenset(
    {"available", "initial", "reserved", "contracted", "delivered"}
)

# ── The CONTRACT axis (STRICT — an unknown state is a loud error) ─────────────
# fields_get-verified 2026-07-30: rs.contract.state is one of
# draft / legal / finance / engineering / confirm / delivered / cancel.
# Rank = how far along the contract is. A unit carrying SEVERAL non-cancel contracts
# counts exactly ONCE, at its MAX rank (live proof: unit AF208-6-501 / unit_id 3608
# holds two `confirm` contracts). `cancel` is excluded upstream by the query domain
# and is therefore deliberately absent here — it is never ranked. A non-cancel state
# missing from this map is a material vocabulary change and the service RAISES on it:
# strictness lives on the contract axis.
CONTRACT_RANK: dict[str, int] = {
    "delivered": 3,
    "confirm": 2,
    "draft": 1,
    "legal": 1,
    "finance": 1,
    "engineering": 1,
}

# Contract rank → board bucket. Rank 1 (ANY pre-confirm contract stage) is "under
# review" — the deal is on paper but not yet committed.
RANK_TO_BUCKET: dict[int, str] = {
    3: BUCKET_DELIVERED,
    2: BUCKET_CONTRACTED,
    1: BUCKET_UNDER_REVIEW,
}

# ── The RESERVATION axis — consulted ONLY when a unit has no contract ─────────
# fields_get-verified 2026-07-30: rs.reservation.state is one of
# draft / initial / confirm / contract / cancel / expire, and the unit join field is
# `unit_id`. `contract` means CONVERTED TO A CONTRACT — it, `cancel` and `expire` are
# all TERMINAL, so a row in one of them is NOT a live hold (a converted reservation's
# unit is already classified by the contract axis). The LIVE set on 2026-07-30 was
# exactly {draft, initial, confirm} — 1 / 10 / 12 rows over 23 distinct units.
RESERVATION_MODEL = "rs.reservation"
RESERVATION_UNIT_FIELD = "unit_id"
RESERVATION_STATE_FIELD = "state"
RESERVATION_LIVE_STATES: frozenset[str] = frozenset({"draft", "initial", "confirm"})

# "Sold" = a confirmed OR delivered contract; sold% = (contracted + delivered) ÷ total.
SOLD_BUCKETS: tuple[str, ...] = (BUCKET_CONTRACTED, BUCKET_DELIVERED)

# A project whose sold% is below this is flagged "Early stage" in the UI (a subtle,
# neutral badge). Display-only — it changes no count. La Puerta (≈3.6% sold, the
# not-yet-loaded project) is the live example this surfaces.
EARLY_STAGE_SOLD_PCT_THRESHOLD = 10.0


# ── Contracts pipeline — the pre-confirm funnel, grouped by STAGE ──────────────
# The board view of deals still in flight. The population is the SAME non-cancel
# rs.contract set the bucket classifier reads; the pipeline groups it by stage instead
# of folding it onto units:
#   draft                         → awaiting action (no desk owns it yet)
#   legal / finance / engineering  → under review (a named desk owns it)
#   confirm / delivered            → counts only (they have left the funnel)
CONTRACT_DRAFT_STATE = "draft"
CONTRACT_CONFIRM_STATE = "confirm"
CONTRACT_DELIVERED_STATE = "delivered"

# The under-review desks — technical value → human label, in deal-flow order.
PIPELINE_REVIEW_STAGES: dict[str, str] = {
    "legal": "Legal Review",
    "finance": "Finance Review",
    "engineering": "Engineering Review",
}

# Every non-cancel state that occupies a place IN the funnel. Together with
# confirm/delivered this covers all 6 non-cancel states; anything else is a loud raise,
# the SAME strictness rule the contract axis uses above.
PIPELINE_STAGE_STATES: tuple[str, ...] = (
    CONTRACT_DRAFT_STATE,
) + tuple(PIPELINE_REVIEW_STAGES)

# ── days-in-stage evidence — Odoo chatter, NOT write_date (probe 2026-07-30) ───
# write_date is REJECTED as a signal: 31 of the 33 live pipeline contracts share ONE
# bulk-edit stamp (2026-06-09 12:56:46), so it dates the edit, not the stage entry.
# The stage-entry date is the LATEST mail.message whose tracking rows record a change
# to rs.contract.state. A contract that never changed state — every draft, and 32 of
# the 33 live pipeline rows — falls back to create_date, which is the NORMAL path.
#
# Verified on THIS Odoo 18 server (2026-07-30): mail.tracking.value identifies its
# field ONLY through field_id, an m2o to ir.model.fields — there is no `field` char
# column as on older majors — and that m2o's display name is the translatable LABEL
# ("Status"). So the state filter is pushed SERVER-side as a dotted path on the
# TECHNICAL field name: exact, immune to translation, and it costs no extra
# round-trip (cross-checked identical to a client-side field_id match).
MAIL_MESSAGE_MODEL = "mail.message"
MAIL_MESSAGE_MODEL_FIELD = "model"
MAIL_MESSAGE_RES_ID_FIELD = "res_id"
MAIL_MESSAGE_DATE_FIELD = "date"
TRACKING_MODEL = "mail.tracking.value"
TRACKING_MESSAGE_FIELD = "mail_message_id"
TRACKING_FIELD_NAME_PATH = "field_id.name"     # technical field name, e.g. "state"
TRACKING_FIELD_MODEL_PATH = "field_id.model"   # owning model, e.g. "rs.contract"


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

# The unit states that count as SOLD for the value join (contracted + delivered) and
# the single state that counts as AVAILABLE for list-value/area. These are explicit
# unit-STATE-based semantics for the Value / Pricing-Outliers / Data-Quality slices,
# intentionally INDEPENDENT of the board buckets: they are FROZEN as literals so the
# board bucket model can evolve without moving those populations.
SOLD_STATES: frozenset[str] = frozenset({"contracted", "delivered"})
AVAILABLE_STATES: frozenset[str] = frozenset({"available"})


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
#   Tier 2a (type)      — CURRENT-ERA aware [2026-06-24]. The type baseline is keyed by
#       (unit-type, 2-yr vintage bucket); a unit is scored against its OWN sale-period
#       bucket if sold, else the type's LATEST qualifying bucket (no all-history fallback —
#       Option A → unevaluable when none). The chosen bucket must have ≥ OUTLIER_MIN_GROUP_SIZE
#       sold AND be LOW-SPREAD (max/median < DQ_LIST_TYPE_SPREAD_MAX); flag if list_pm2 >
#       DQ_LIST_TYPE_K × that bucket's MEDIAN realized price/m². Benchmarking today's lists
#       against an all-history median was wrong (prices escalated ~6× 2018-2025; the studio
#       65,000/m² lists are CONFIRMED-correct current values — discovery commit 611261f).
#   Tier 2b (area error) — same current-era bucket selection; flag if list_pm2 >
#       DQ_LIST_IMPOSSIBLE_K × that bucket's MAX realized price/m². A spread-independent
#       catch-all: every live catch is an area-entry error (e.g. total_area entered as 1),
#       hence the human-facing label "Possible area error". All three are TUNABLE constants.
DQ_LIST_TYPE_K = 3.0
DQ_LIST_TYPE_SPREAD_MAX = 2.5
DQ_LIST_IMPOSSIBLE_K = 5.0
