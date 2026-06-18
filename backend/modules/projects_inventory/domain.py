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
