from typing import Any, Optional

from pydantic import BaseModel

# ── Overdue breakdown rows ────────────────────────────────────────────────────


class OverdueBySalesperson(BaseModel):
    salesperson_id: Optional[int]
    salesperson_name: str
    overdue_count: int


class OverdueByTeam(BaseModel):
    team_id: Optional[int]
    team_name: str
    overdue_count: int


class OverdueByStage(BaseModel):
    stage_id: Optional[int]
    stage_name: str
    overdue_count: int


class OverdueMatrixRow(BaseModel):
    team_id: Optional[int]
    team_name: str
    salesperson_id: Optional[int]
    salesperson_name: str
    stage_id: Optional[int]
    stage_name: str
    overdue_count: int


# ── Aggregate containers ──────────────────────────────────────────────────────


class ActivitySummary(BaseModel):
    total_leads: int
    followups_today: int
    overdue_followups: int
    planned_followups: int
    no_activity_leads: int
    critical_overdue: int
    data_quality_issues: int


class DataQuality(BaseModel):
    new_x_count: int
    missing_stage_count: int
    missing_contact_count: int
    missing_salesperson_count: int
    total_data_quality_issues: int


class FollowupRisk(BaseModel):
    overdue_by_salesperson: list[OverdueBySalesperson]
    overdue_by_team: list[OverdueByTeam]
    overdue_by_stage: list[OverdueByStage]
    overdue_matrix_by_team_salesperson_stage: list[OverdueMatrixRow]


# ── Top-level response models ─────────────────────────────────────────────────


class SummaryResponse(BaseModel):
    mode: str
    scope: str
    summary: ActivitySummary
    data_quality: DataQuality
    followup_risk: FollowupRisk


class FollowupRiskResponse(BaseModel):
    mode: str
    scope: str
    followup_risk: FollowupRisk


class MissingContactRow(BaseModel):
    lead_id: int
    opportunity_name: str
    contact_name: str
    salesperson_id: Optional[int]
    salesperson_name: str
    team_id: Optional[int]
    team_name: str
    stage_id: Optional[int]
    stage_name: str
    source_id: Optional[int]
    source_name: str
    create_date: str
    # Truthful "linked contact" flag: the Odoo partner_id, or None when the lead
    # has no linked contact. Drives the hub's Tab-1 "No linked contact" badge —
    # needed because contact_name may still be filled by the display fallback.
    partner_id: Optional[int] = None


class DataQualityMissingContactResponse(BaseModel):
    mode: str
    scope: str
    missing_contact_details: list[MissingContactRow]


# ── Pagination ────────────────────────────────────────────────────────────────


class Pagination(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int
    has_next: bool
    has_prev: bool


class PaginatedMissingContactResponse(BaseModel):
    ok: bool = True
    data: list[MissingContactRow]
    pagination: Pagination


# ── Stage count result (used by count_leads_by_stage) ─────────────────────────


class StageCountResult(BaseModel):
    stage_name: str
    matched_stages: list[dict[str, Any]]
    count: int
    overdue_only: bool


# ── Error response (v2 structured format) ─────────────────────────────────────


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict = {}
    request_id: Optional[str] = None
    timestamp: Optional[str] = None


class ErrorResponse(BaseModel):
    ok: bool = False
    error: ErrorDetail
