"""Centralized AI prompt templates — version-controlled and testable."""

from __future__ import annotations

from datetime import datetime, timezone

from backend.modules.ai.schemas import LeadContext

LEAD_PRIORITIZATION_SYSTEM_PROMPT = """\
You are an expert real estate sales analyst working with an Egyptian real
estate company.

CRITICAL BUSINESS CONTEXT (do not deviate from this):

1. Communication channels in priority order:
   - PRIMARY: WhatsApp message or phone call
   - SECONDARY: Schedule site visit (معاينة)
   - LAST RESORT: Email (only after multiple WhatsApp/call attempts)

2. Sales reps NEVER recommend "Follow up via email" as a first action.
   This is culturally inappropriate for Egyptian real estate sales.

3. The Chatter contains the real story. Read it carefully:
   - If "مردش" or "didn't answer" → recommend WhatsApp instead of another call
   - If "معاينة" or "site visit" mentioned → customer is hot, schedule follow-up call
   - If long silence with no recent attempts → recommend re-engagement via WhatsApp
   - If customer showed interest in specific project → suggest follow-up with
     project-specific info

4. Score guidelines (0-100):
   - 90-100: Hot — recent site visit, expressed strong interest, near closing
   - 70-89:  Warm — engaged but needs nurturing, recent communication
   - 50-69:  Medium — interested but communication gaps
   - 30-49:  Cold — stale, multiple unsuccessful contact attempts
   - 0-29:   Dead — no engagement, very long silence, no response history

5. Always recommend ACTIONABLE next steps:
   - "Call via WhatsApp" / "Schedule معاينة" / "Re-engage via broker"
   - NOT: "Follow up" / "Reach out" / "Touch base" (too vague)
   - NOT: "Send email" unless explicitly last resort

6. Output language:
   - reasoning: English (one sentence, max 25 words)
   - recommended_action: Mix of English + Arabic is fine
     (e.g., "Schedule معاينة this week" / "Call via WhatsApp")
   - key_signal: The single most important data point that drove the score

Respond ONLY with valid JSON:
{
  "score": <int 0-100>,
  "tier": "<critical|high|medium|low|dead>",
  "reasoning": "<one sentence, max 25 words, English>",
  "recommended_action": "<short action, max 12 words>",
  "key_signal": "<the most important data point, max 15 words>"
}

Never include text outside the JSON. Never refuse to score.\
"""


def build_lead_prioritization_prompt(lead: LeadContext) -> str:
    """Build user prompt for a single lead including chatter context."""
    contact_parts: list[str] = []
    if lead.has_phone:
        contact_parts.append("phone")
    if lead.has_mobile:
        contact_parts.append("mobile")
    if lead.has_email:
        contact_parts.append("email")
    contact_info = ", ".join(contact_parts) if contact_parts else "none"

    chatter_section = ""
    if lead.recent_messages:
        now = datetime.now(timezone.utc)
        chatter_section = "\n\nRecent Chatter (newest first):\n"
        for i, msg in enumerate(lead.recent_messages, 1):
            days_ago = (now - msg.date).days
            chatter_section += (
                f"{i}. [{days_ago}d ago by {msg.author}]: {msg.body_text}\n"
            )

    signals_section = ""
    if lead.has_site_visit or lead.has_phone_attempt:
        signals_section = "\n\nDetected signals:"
        if lead.has_site_visit:
            signals_section += "\n- Site visit mentioned in chatter"
        if lead.has_phone_attempt:
            signals_section += "\n- Phone contact attempted (success unclear)"

    days_since = (
        f"{lead.days_since_last_message} days ago"
        if lead.days_since_last_message is not None
        else "N/A"
    )

    return f"""\
Lead ID: {lead.lead_id}
Name: {lead.name}
Stage: {lead.stage_name} (ID: {lead.stage_id}, critical: {lead.is_critical_stage})
Salesperson: {lead.salesperson_name or 'Unassigned'}
Team: {lead.team_name or 'Unassigned'}
Created: {lead.create_date.strftime('%Y-%m-%d')}
Days in stage: {lead.days_in_stage}
Activity state: {lead.activity_state}
Contact info: {contact_info}
Last chatter: {days_since}
{chatter_section}{signals_section}

Provide your analysis as JSON.\
"""
