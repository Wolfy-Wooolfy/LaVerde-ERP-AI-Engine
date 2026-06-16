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


# ── Level 2 — per-campaign timeline (period-level / month) ────────────────────
# Drill into ONE campaign and see its leads grouped over Cairo-local months: a
# lightweight volume trend, plus a full 4-group funnel + a DERIVED maturation
# state per recent month. The legacy CRM migration (Cairo days >= LEGACY_DAY_MIN
# leads) is excluded from every figure. Reuses OutcomeGroup + AttributionStatus.
#
# NOTE on maturation (discovery §F.5): Odoo keeps NO per-stage history and NO
# date_won, so there is no true conversion-over-time curve. Each month's funnel is
# the CURRENT stage breakdown of the leads that AROSE that month; maturation_state
# is a DERIVED heuristic from the month's age + its جديد share, not a measurement.


class CampaignTimelineHeader(BaseModel):
    campaign_id: int
    campaign_name: str
    total_leads_in_window: int            # Σ of the funnel periods' lead_count (windowed, post-migration)
    attribution_status: AttributionStatus
    media_buyer_id: Optional[int]         # populated iff a buyer is shown (confirmed/dominant)
    media_buyer_name: Optional[str]
    concentration: Optional[float]        # 0.0–100.0 dominant both-set share; None when no buyer shown
    both_set_count: int                   # ALL-TIME leads with BOTH campaign_id AND media_buyer_id (matches Level 1)


class TimelineTrendPoint(BaseModel):
    month: str                            # Cairo-local "YYYY-MM"
    lead_count: int                       # post-migration lead volume that arose this month (0-filled)


class TimelinePeriod(BaseModel):
    month: str                            # Cairo-local "YYYY-MM"
    lead_count: int                       # post-migration leads that arose this month
    outcomes: list[OutcomeGroup]          # always exactly 4, GROUP_ORDER; sum(count)==lead_count
    maturation_state: Literal["too_early", "neglected", "normal"]


class CampaignTimeline(BaseModel):
    header: CampaignTimelineHeader
    trend: list[TimelineTrendPoint]       # last trend_months Cairo months, oldest→newest (volume only)
    periods: list[TimelinePeriod]         # last window_months Cairo months, oldest→newest (full funnel)

    window_months: int                    # # of funnel periods (the `months` preset, OR the derived custom span)
    trend_months: int                     # # of trend points (fixed: DEFAULT_TREND_MONTHS)
    window_start_month: str               # oldest funnel period "YYYY-MM"
    window_end_month: str                 # newest funnel period "YYYY-MM" (preset: current Cairo month; custom: end_month)
    is_custom_range: bool                 # True iff an explicit start_month..end_month range drove the window (vs a months preset)
    legacy_days_excluded: list[str]       # detected migration Cairo days (YYYY-MM-DD) dropped from every figure

    reference_date: str                   # Cairo-local YYYY-MM-DD
    as_of: str                            # UTC ISO 8601 of the query
    config_warnings: list[str]            # configured gate names that didn't resolve / matched >1 record
    integrity_alerts: list[str]           # LOUD: confirmed campaign no longer holds >=90% (locked-decision drift)
    cache_status: Literal["fresh", "cached"]
    rpc_duration_ms: int                  # 0 when served from cache


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
