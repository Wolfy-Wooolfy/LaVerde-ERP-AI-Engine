"""
Pydantic v2 response schemas for Campaign Performance — Level 1 (per-campaign funnel).

GET /api/v1/campaign-performance/overview -> CampaignPerformanceOverview

Read-only analytics only — no write surface. The 4-group OutcomeGroup model is
imported from marketing_attribution (single source of truth) so the funnel shape
is byte-identical to the shipped buyer-centric view.
"""

from typing import Literal, Optional

from pydantic import BaseModel

# Reuse the shipped module's 4-group outcome model + label Literal (no duplication).
from backend.modules.marketing_attribution.schemas import (  # noqa: F401
    OutcomeGroup,
    OutcomeGroupName,
)

# Per-campaign media-buyer display state (§7.1, amended — DISPLAY only; does not
# change marketing_attribution's strict 52.6% attribution metric):
#   confirmed         — concentration >= 90% AND in the confirmed set
#   dominant          — concentration >= floor (50%), not confirmed
#   mixed             — has buyer leads but no single buyer clears floor+min-sample
#   no_buyer          — no leads carry a media_buyer_id at all
#   excluded_channel  — a DENYLIST campaign (channel owner, e.g. Daima) — suppressed
AttributionStatus = Literal[
    "confirmed", "dominant", "mixed", "no_buyer", "excluded_channel"
]


class CampaignFunnelRow(BaseModel):
    campaign_id: int
    campaign_name: str
    lead_count: int                       # ALL leads with this campaign_id (incl. archived)
    outcomes: list[OutcomeGroup]          # always exactly 4, GROUP_ORDER; sum(count)==lead_count

    attribution_status: AttributionStatus
    media_buyer_id: Optional[int]         # populated iff a buyer is shown (confirmed/dominant)
    media_buyer_name: Optional[str]
    concentration: Optional[float]        # 0.0–100.0 dominant both-set share; None when no buyer shown
    both_set_count: int                   # leads with BOTH campaign_id AND media_buyer_id set


class AggregateFunnel(BaseModel):
    """A roll-up of many campaigns into one funnel (the long tail below threshold)."""
    campaign_count: int
    lead_count: int
    outcomes: list[OutcomeGroup]          # 4, GROUP_ORDER; sum(count)==lead_count


class DataQualityBucket(BaseModel):
    """A non-campaign / junk bucket surfaced as a data-quality flag (not a list row)."""
    label: str                            # "None" (junk campaign) | "(no campaign)"
    campaign_ids: list[int]               # ids of the junk campaign(s); [] for the no-campaign bucket
    lead_count: int
    outcomes: list[OutcomeGroup]          # 4, GROUP_ORDER; sum(count)==lead_count


class DataQuality(BaseModel):
    junk_none: Optional[DataQualityBucket]    # the utm.campaign literally named "None" (§A.2)
    no_campaign: Optional[DataQualityBucket]  # leads with campaign_id=False (genuinely campaign-less)


class CampaignPerformanceOverview(BaseModel):
    campaigns: list[CampaignFunnelRow]    # lead_count >= threshold, sorted by lead_count desc
    long_tail: Optional[AggregateFunnel]  # campaigns below threshold, aggregated (None if empty)
    data_quality: DataQuality

    min_lead_threshold: int               # the volume cut applied to build the long tail
    total_leads_population: int           # all crm.lead incl. archived (active_test=False)
    total_campaigns_with_leads: int       # distinct utm.campaign ids with >=1 lead (incl. junk; excl. no-campaign)
    listed_campaign_count: int            # len(campaigns) (rows at/above threshold)

    is_won_stage_names: list[str]         # crm.stage names where is_won=True (spot-check aid)
    config_warnings: list[str]            # configured names that didn't resolve / matched >1 record
    integrity_alerts: list[str]           # LOUD: a confirmed campaign no longer holds >=90% (locked-decision drift)

    reference_date: str                   # Cairo-local YYYY-MM-DD
    as_of: str                            # UTC ISO 8601 of the query
    cache_status: Literal["fresh", "cached"]
    rpc_duration_ms: int                  # 0 when served from cache
