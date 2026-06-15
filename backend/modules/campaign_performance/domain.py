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
