"""
Campaign Performance — domain configuration (LOCKED DECISIONS for Level 1).

REUSE, NO DUPLICATION: the stage->group classification, the 4-group labels +
order, the attribution model fields, the concentration gate, and the CONFIRMED /
DENYLIST campaign config are IMPORTED from backend/modules/marketing_attribution
and RE-EXPORTED here — never re-declared. So every per-campaign stage-group count
and the >=90% "confirmed" gate are defined byte-for-byte as in the shipped module.

This file adds ONLY the campaign-performance-specific knobs:
  - JUNK_CAMPAIGN_NAMES      — campaign labels that are data-quality junk, not real
                               campaigns (the utm.campaign literally named "None").
  - DEFAULT_MIN_LEAD_THRESHOLD — campaigns below this lead volume roll into the
                               aggregated long tail (default; overridable per call).
  - DOMINANT_FLOOR_PCT       — concentration floor (%) for the "dominant" buyer
                               label below the >=90% confirmed gate.
  - MIN_BOTH_SET_FOR_BUYER   — minimum both-set leads before a campaign may be
                               labelled with any single buyer (so a campaign is
                               never attributed off a handful of leads).
"""

# ── Re-exported from the shipped module (single source of truth) ──────────────
from backend.modules.marketing_attribution.domain import (  # noqa: F401
    BUYER_FIELD,
    CAMPAIGN_FIELD,
    CONCENTRATION_THRESHOLD,
    CONFIRMED_BUYER_CAMPAIGNS,
    DENYLIST_CAMPAIGNS,
    GROUP_INTERESTED,
    GROUP_NEW,
    GROUP_NO_RESULT,
    GROUP_ORDER,
    GROUP_WON,
    classify_stage,
)

# ── Campaign-performance-specific config ──────────────────────────────────────

# utm.campaign labels that are junk/placeholder, NOT real campaigns. Detected by
# NAME (re-import-stable; ids shift on re-import) — see discovery §A.2: the active
# utm.campaign id=1677 literally named "None" holds ~11.8% of leads and is a
# data-quality artifact, distinct from leads that genuinely have no campaign
# (campaign_id=False). Surfaced as a data-quality flag, never as a list row.
JUNK_CAMPAIGN_NAMES: frozenset[str] = frozenset({"None"})

# Campaigns with fewer leads than this roll into a single aggregated "long tail"
# row (discovery §A.2: ~95 campaigns have >=50 leads; the rest are a long tail).
# A parameter on the service overrides it; this is only the default.
DEFAULT_MIN_LEAD_THRESHOLD: int = 50

# Concentration floor (%) for the "dominant" buyer label. >= this (but below the
# >=90% confirmed gate, and not confirmed) => status="dominant"; below it (with
# buyer leads) => status="mixed". Khaled's display rule (§7.1, amended).
DOMINANT_FLOOR_PCT: int = 50

# Minimum both-set leads (leads with BOTH campaign_id AND media_buyer_id) before a
# campaign may carry any single-buyer label — guards against labelling a campaign
# off a handful of leads. Below this, the campaign is "mixed" even at high
# concentration. (Confirmed campaigns hold thousands of both-set leads, so this
# never bites them.)
MIN_BOTH_SET_FOR_BUYER: int = 10

# ── Level-2 (per-campaign timeline) tunables ──────────────────────────────────
# These add the period-level (month) timeline knobs on top of Level 1. None of
# them change the Level-1 funnel or the buyer rule — they only parameterise the
# timeline service (services/timeline_service.py).

# A Cairo-local DAY holding >= this many leads is treated as a bulk data
# migration/import (discovery §F.1: the Nov-2025 CRM migration ran as 3 such days
# of 2,000-row chunks). Detected DYNAMICALLY at query time — never hardcoded to
# the dates — so re-imports relocate cleanly. Reproduces the discovery rule.
LEGACY_DAY_MIN: int = 10000

# How many trailing Cairo calendar months the timeline reports a FULL funnel for
# (default; overridable per call as the `months` query param, 1..12).
DEFAULT_WINDOW_MONTHS: int = 3

# Maximum span (in whole Cairo months, inclusive) of an explicit CUSTOM date range
# (start_month..end_month). The funnel renders one card + one trend bar per month,
# so an unbounded span would be unwieldy and fetch ever more leads; the real
# post-migration flow only begins ~Nov-2025, so 24 months covers all genuine data
# with headroom. A range wider than this is rejected (InvalidTimelineRangeError on
# the JSON API; silent fallback to the months preset on the HTML page). The custom
# range is OPT-IN — absent start/end, the trailing `months` preset is unchanged.
MAX_CUSTOM_SPAN_MONTHS: int = 24

# How many trailing Cairo calendar months the lightweight volume TREND spans
# (volume-only, 0-filled). Fixed per request; wider than the funnel window so the
# trend gives context the periods alone do not.
DEFAULT_TREND_MONTHS: int = 6

# ── Level-1 LIST windowing (scope the whole campaign list to a Cairo period) ──
# The Level-1 list shows ALL-TIME totals per campaign; the discovery
# (scripts/discovery_level1_windowing.py) showed ~86% of all leads are the
# Nov-2025 migration and only ~6 campaigns are active in the current month. These
# presets scope the WHOLE list to a Cairo window so the board can see "how are all
# campaigns doing THIS period" in one view. A windowed view lists every campaign
# with >=1 (post-migration) lead in the window individually (no long-tail, no
# threshold) and hides zero-activity campaigns; "all" is the shipped un-windowed
# path (>=50 threshold + long tail + migration included), unchanged.
WINDOW_ALL: str = "all"            # today's shipped behaviour (incl. migration)
WINDOW_CURRENT: str = "current"    # the current Cairo month only
WINDOW_LAST3: str = "last3"        # the current + 2 preceding Cairo months
WINDOW_CUSTOM: str = "custom"      # an explicit start_month..end_month range

# The trailing Cairo-month count each DATED preset spans (ending at the current
# Cairo month). "all" / "custom" are NOT here — "all" is the un-windowed path and
# "custom" derives its span from the explicit range (capped by MAX_CUSTOM_SPAN_MONTHS).
WINDOW_PRESET_MONTHS: dict[str, int] = {
    WINDOW_CURRENT: 1,
    WINDOW_LAST3: 3,
}

# The presets offered on the list, in display order. "custom" is driven by the
# start_month/end_month range, not a pill value.
WINDOW_PRESETS: tuple[str, ...] = (WINDOW_CURRENT, WINDOW_LAST3, WINDOW_ALL)

# The default window when the list is opened with no explicit selection (locked
# decision): scope to the LAST 3 Cairo months so the list reads as current
# performance, not the migration-dominated all-time total.
DEFAULT_WINDOW: str = WINDOW_LAST3

# Maturation heuristic (DERIVED state, not a true conversion curve — Odoo keeps no
# per-stage history / no date_won; discovery §F.5). A month whose جديد share is >=
# this % is "still raw". Combined with the month's age:
#   age <= YOUNG_MAX_AGE  & جديد% >= HIGH -> too_early  (new, naturally still raw)
#   age >= NEGLECTED_MIN  & جديد% >= HIGH -> neglected  (old but never worked)
#   otherwise (incl. zero-lead months)    -> normal
MATURATION_NEW_PCT_HIGH: float = 50.0
MATURATION_YOUNG_MAX_AGE: int = 1
MATURATION_NEGLECTED_MIN_AGE: int = 2
