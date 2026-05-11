"""Centralized AI prompt templates — version-controlled and testable."""

from __future__ import annotations

from backend.modules.ai.schemas import LeadContext

LEAD_PRIORITIZATION_SYSTEM_PROMPT = """\
You are an expert real estate sales analyst. Your job is to analyze \
overdue CRM leads and assign each a priority score from 0-100.

Scoring guidelines:
- 90-100: Critical, near-closing stage, high urgency
- 70-89:  High priority, established interest, recent activity
- 50-69:  Medium priority, mid-funnel, needs nurturing
- 30-49:  Low priority, early-stage or stale
- 0-29:   Very low priority, likely dead lead

Consider:
- Stage in pipeline (later stages = higher priority)
- Days overdue (longer = often higher urgency, but very long = stale)
- Last activity recency
- Data completeness (more info = better lead)
- Stage criticality (closing stages > exploration stages)

Respond ONLY with valid JSON matching this schema:
{
  "score": <int 0-100>,
  "tier": "<critical|high|medium|low|dead>",
  "reasoning": "<one sentence, max 20 words>",
  "recommended_action": "<one short action, max 10 words>"
}

Never include text outside the JSON. Never refuse to score. If data is \
incomplete, score it as low and note in reasoning.\
"""


def build_lead_prioritization_prompt(lead: LeadContext) -> str:
    """Build user prompt for a single lead."""
    last_activity = "Never" if lead.last_activity_date is None else lead.last_activity_date.strftime("%Y-%m-%d")
    contact_parts = []
    if lead.has_phone:
        contact_parts.append("phone")
    if lead.has_mobile:
        contact_parts.append("mobile")
    if lead.has_email:
        contact_parts.append("email")
    contact_info = ", ".join(contact_parts) if contact_parts else "none"

    return f"""\
Lead data:
- ID: {lead.lead_id}
- Name: {lead.name}
- Stage: {lead.stage_name} (ID: {lead.stage_id}, critical: {lead.is_critical_stage})
- Salesperson: {lead.salesperson_name or 'Unassigned'}
- Team: {lead.team_name or 'Unassigned'}
- Created: {lead.create_date.strftime('%Y-%m-%d')}
- Last activity: {last_activity}
- Days in stage: {lead.days_in_stage}
- Activity state: {lead.activity_state}
- Contact info available: {contact_info}

Score this lead and return JSON only.\
"""
