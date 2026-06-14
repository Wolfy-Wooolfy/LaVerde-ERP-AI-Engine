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
