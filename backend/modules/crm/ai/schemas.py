from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ChatterMessage(BaseModel):
    date: datetime
    author: str
    body_text: str  # HTML stripped, max 300 chars
    message_type: str  # comment / email / notification


class LeadContext(BaseModel):
    """Input to AI: everything needed to score a lead."""

    lead_id: int
    name: str
    stage_id: int
    stage_name: str
    salesperson_name: Optional[str] = None
    team_name: Optional[str] = None
    create_date: datetime
    last_activity_date: Optional[datetime] = None
    days_in_stage: int = 0
    is_critical_stage: bool = False
    has_phone: bool = False
    has_mobile: bool = False
    has_email: bool = False
    activity_state: str = "none"  # overdue/today/planned/none
    recent_messages: list[ChatterMessage] = Field(default_factory=list)
    has_site_visit: bool = False
    has_phone_attempt: bool = False
    last_message_date: Optional[datetime] = None
    days_since_last_message: Optional[int] = None


class LeadPriority(BaseModel):
    """AI output for one lead."""

    model_config = ConfigDict(protected_namespaces=())

    lead_id: int
    score: int = Field(ge=0, le=100)
    tier: Literal["critical", "high", "medium", "low", "dead"]
    reasoning: str
    recommended_action: str
    key_signal: str = ""
    cached: bool = False
    cost_usd: float = 0.0
    generated_at: datetime
    model_used: str


class BudgetStatus(BaseModel):
    current_month_spend_usd: float
    monthly_budget_usd: float
    remaining_budget_usd: float
    percentage_used: float
    is_near_budget: bool
    is_over_budget: bool
    current_month: str  # "2026-05"


class PrioritizeOverdueRequest(BaseModel):
    limit: int = Field(default=50, ge=1, le=100)


class PrioritizeOverdueResponse(BaseModel):
    ok: bool = True
    leads: list[LeadPriority]
    total_cost_usd: float
    cached_count: int
    fresh_count: int


class AIHealthResponse(BaseModel):
    status: Literal["ok", "degraded", "disabled"]
    model: str
    budget_ok: bool
    ai_enabled: bool
    feature_prioritization: bool


class ChatCompletionResponse(BaseModel):
    content: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    duration_ms: int
    cached: bool = False
