"""
Pydantic v2 response schemas for Marketing Attribution.

GET /api/v1/marketing-attribution/overview -> MarketingAttributionOverview

Read-only analytics only — no write surface.
"""

from typing import Literal

from pydantic import BaseModel

# The 4 outcome groups, fixed order (§3.7). Arabic per the terminology rule.
OutcomeGroupName = Literal["جديد", "مهتم", "اشترى", "بلا نتيجة"]


class OutcomeGroup(BaseModel):
    group: OutcomeGroupName
    count: int
    pct: float            # % of this buyer's total_attributed; 0.0 when total == 0


class BuyerRow(BaseModel):
    buyer_id: int
    buyer_name: str
    total_attributed: int             # ALL leads in this buyer's attributing campaigns
    outcomes: list[OutcomeGroup]      # always exactly 4, in GROUP_ORDER; sum(count)==total
    campaign_ids: list[int]           # attributing campaign ids whose dominant buyer is this buyer


class ConfirmedCampaign(BaseModel):
    """A campaign that passed the attribution gate (qualifies + confirmed + not denied)."""
    campaign_id: int
    campaign_name: str
    dominant_buyer_id: int
    dominant_buyer_name: str
    concentration: float              # 0.0–100.0 (dominant both-set share)
    both_set_count: int               # leads with BOTH campaign_id AND media_buyer_id set
    lead_count: int                   # ALL leads with this campaign_id (incl. archived)


class PendingCampaign(BaseModel):
    """Qualifies (>=90%), not denied, but NOT yet confirmed as a buyer channel (§3.5).

    Its leads are NOT attributed until Khaled confirms it is a buyer (not a channel).
    """
    campaign_id: int
    campaign_name: str
    dominant_buyer_id: int
    dominant_buyer_name: str
    concentration: float              # 0.0–100.0
    both_set_count: int
    lead_count: int


class MarketingAttributionOverview(BaseModel):
    buyers: list[BuyerRow]
    confirmed_campaigns: list[ConfirmedCampaign]   # campaigns that attributed
    pending_campaigns: list[PendingCampaign]       # qualify but await confirmation (§3.5)

    total_leads_population: int       # all crm.lead incl. archived (active_test=False)
    total_attributed: int            # sum of buyers' totals (confirmed campaigns only)
    attribution_pct: float           # total_attributed / total_leads_population * 100

    is_won_stage_names: list[str]    # crm.stage names where is_won=True (spot-check aid)
    config_warnings: list[str]       # configured names that didn't resolve / matched >1 record (A3)
    integrity_alerts: list[str]      # LOUD: a confirmed campaign failed the gate (A1) — locked-decision drift

    reference_date: str              # Cairo-local YYYY-MM-DD
    as_of: str                       # UTC ISO 8601 of the query
    cache_status: Literal["fresh", "cached"]
    rpc_duration_ms: int             # 0 when served from cache


# ── WINDOWED per-media-buyer overview (the buyer list scoped to a Cairo period) ─
# Same campaign-driven attribution as the all-time overview, but every buyer funnel
# is restricted to the leads that AROSE in the window (Cairo create_date), the legacy
# Nov-2025 migration EXCLUDED. The campaign→buyer MAP stays ALL-TIME (the >=90%
# confirmed gate), so a buyer's mapping never shifts with the window — only the LEADS
# feeding the funnel are windowed. Lists every buyer with >=1 attributed windowed lead
# (sorted by windowed volume), plus an UNATTRIBUTED bucket (windowed leads in campaigns
# with no confirmed buyer) so the windowed coverage is honest. The "all" window is
# served by MarketingAttributionOverview (the shipped un-windowed path), not this model.

WindowName = Literal["current", "last3", "custom"]


class WindowedBuyerRow(BaseModel):
    buyer_id: int
    buyer_name: str
    total_attributed: int             # windowed leads attributed to this buyer (post-migration)
    outcomes: list[OutcomeGroup]      # always exactly 4, GROUP_ORDER; sum(count)==total_attributed
    campaign_ids: list[int]           # attributing campaign ids feeding this buyer in-window


class WindowedUnattributed(BaseModel):
    """Windowed leads NOT in any attributing campaign (no campaign, junk, denylisted,
    pending, or unmapped channels) — surfaced so the windowed coverage is honest."""
    lead_count: int
    outcomes: list[OutcomeGroup]      # 4, GROUP_ORDER; sum(count)==lead_count


class MarketingAttributionWindowed(BaseModel):
    buyers: list[WindowedBuyerRow]    # buyers with >=1 attributed windowed lead, sorted by volume desc
    unattributed: WindowedUnattributed

    total_leads_population: int       # WINDOWED population: post-migration leads that arose in the window
    total_attributed: int            # Σ buyers' windowed totals
    coverage_pct: float              # total_attributed / total_leads_population * 100 (windowed coverage)

    window: WindowName                # the active dated window ("current" | "last3" | "custom")
    is_custom_range: bool             # True iff an explicit start_month..end_month range drove the window
    window_months: int                # # of Cairo months the window spans (derived for custom)
    window_start_month: str           # oldest window month "YYYY-MM"
    window_end_month: str             # newest window month "YYYY-MM" (preset: current Cairo month; custom: end_month)
    legacy_days_excluded: list[str]   # detected migration Cairo days (YYYY-MM-DD) dropped from every figure

    is_won_stage_names: list[str]    # crm.stage names where is_won=True (spot-check aid)
    config_warnings: list[str]       # configured names that didn't resolve / matched >1 record (A3)
    integrity_alerts: list[str]      # LOUD: a confirmed campaign failed the gate (A1) — locked-decision drift

    reference_date: str              # Cairo-local YYYY-MM-DD
    as_of: str                       # UTC ISO 8601 of the query
    cache_status: Literal["fresh", "cached"]
    rpc_duration_ms: int             # 0 when served from cache
