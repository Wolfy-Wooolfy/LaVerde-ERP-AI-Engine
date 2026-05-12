"""Stage 2a: Execute parsed intent → fetch real CRM data via CrmService."""

from __future__ import annotations

from typing import Any

from loguru import logger

from backend.modules.crm.service import CrmService

# Arabic → English stage name normalization (case-insensitive partial match applied separately)
STAGE_AR_TO_EN: dict[str, str] = {
    "التفاوض": "Negotiation",
    "تفاوض": "Negotiation",
    "الحجز": "Reservation",
    "حجز": "Reservation",
    "متابعة": "Follow up",
    "المتابعة": "Follow up",
    "follow up": "Follow up",
    "followup": "Follow up",
    "اهتمام": "Interested",
    "مهتم": "Interested",
    "خسارة": "Lost",
    "خسر": "Lost",
    "فاز": "Won",
    "مغلق": "Won",
    "معاينة": "Site Visit",
    "site visit": "Site Visit",
    "جديد": "New",
    "new": "New",
}


def _normalise_stage(stage_filter: str) -> str:
    """Translate Arabic stage names to English and normalise casing."""
    stripped = stage_filter.strip()
    # Exact Arabic match
    if stripped in STAGE_AR_TO_EN:
        return STAGE_AR_TO_EN[stripped]
    # Case-insensitive English alias match
    lower = stripped.lower()
    for ar, en in STAGE_AR_TO_EN.items():
        if lower == ar.lower() or lower == en.lower():
            return en
    # Return as-is for direct English stage names (Negotiation, Reservation, …)
    return stripped


async def fetch_data_for_intent(
    intent: str,
    filters: dict,
    crm: CrmService,
    prioritizer: Any | None = None,
) -> dict:
    """Dispatch to the appropriate intent handler. Returns a serializable dict."""
    handler = _INTENT_HANDLERS.get(intent)
    if handler is None:
        return {"type": "clarification_needed", "message": "Intent not recognized"}
    return await handler(crm, filters, prioritizer)


# ── Handlers ───────────────────────────────────────────────────────────────────


async def _handle_list_overdue_by_salesperson(
    crm: CrmService, filters: dict, _p: Any
) -> dict:
    limit = int(filters.get("limit") or 10)
    sp_filter = (filters.get("salesperson") or "").lower()
    rows = await crm.overdue_by_salesperson()
    if sp_filter:
        rows = [r for r in rows if sp_filter in r.salesperson_name.lower()]
    rows = rows[:limit]
    return {
        "type": "salesperson_overdue_list",
        "rows": [r.model_dump() for r in rows],
        "total": len(rows),
    }


async def _handle_list_overdue_by_team(
    crm: CrmService, filters: dict, _p: Any
) -> dict:
    limit = int(filters.get("limit") or 10)
    team_filter = (filters.get("team") or "").lower()
    rows = await crm.overdue_by_team()
    if team_filter:
        rows = [r for r in rows if team_filter in r.team_name.lower()]
    rows = rows[:limit]
    return {
        "type": "team_overdue_list",
        "rows": [r.model_dump() for r in rows],
        "total": len(rows),
    }


async def _handle_list_overdue_by_stage(
    crm: CrmService, filters: dict, _p: Any
) -> dict:
    limit = int(filters.get("limit") or 10)
    raw_filter = (filters.get("stage") or "").strip()
    stage_filter = _normalise_stage(raw_filter).lower() if raw_filter else ""
    rows = await crm.overdue_by_stage()
    if stage_filter:
        rows = [r for r in rows if stage_filter in r.stage_name.lower()]
    rows = rows[:limit]
    return {
        "type": "stage_overdue_list",
        "rows": [r.model_dump() for r in rows],
        "total": len(rows),
    }


async def _handle_count_by_stage(crm: CrmService, filters: dict, _p: Any) -> dict:
    raw_filter = (filters.get("stage") or "").strip()
    if not raw_filter:
        return {"type": "clarification_needed", "message": "Stage name required"}

    normalised = _normalise_stage(raw_filter)
    overdue_only = bool(filters.get("overdue_only", False))

    result = await crm.count_leads_by_stage(stage_name=normalised, overdue_only=overdue_only)

    if not result.matched_stages:
        return {
            "type": "stage_not_found",
            "requested_stage": raw_filter,
        }

    return {
        "type": "stage_count",
        "stage_name": result.stage_name,
        "count": result.count,
        "overdue_only": overdue_only,
        "matched_count": len(result.matched_stages),
    }


async def _handle_count_overdue_by_stage(crm: CrmService, filters: dict, _p: Any) -> dict:
    """Explicit overdue-only stage count — user asked about متأخر/overdue leads."""
    return await _handle_count_by_stage(crm, {**filters, "overdue_only": True}, _p)


async def _handle_count_by_team(crm: CrmService, filters: dict, _p: Any) -> dict:
    team_filter = (filters.get("team") or "").lower()
    rows = await crm.overdue_by_team()
    if team_filter:
        matching = [r for r in rows if team_filter in r.team_name.lower()]
        count = sum(r.overdue_count for r in matching)
        label = filters.get("team", "matching team")
    else:
        count = sum(r.overdue_count for r in rows)
        label = "all teams"
    return {
        "type": "count",
        "count": count,
        "label": label,
        "breakdown": [r.model_dump() for r in rows],
    }


async def _handle_count_by_salesperson(crm: CrmService, filters: dict, _p: Any) -> dict:
    sp_filter = (filters.get("salesperson") or "").lower()
    rows = await crm.overdue_by_salesperson()
    if sp_filter:
        matching = [r for r in rows if sp_filter in r.salesperson_name.lower()]
        count = sum(r.overdue_count for r in matching)
        label = filters.get("salesperson", "matching salesperson")
    else:
        count = sum(r.overdue_count for r in rows)
        label = "all salespeople"
    return {
        "type": "count",
        "count": count,
        "label": label,
        "breakdown": [r.model_dump() for r in rows],
    }


async def _handle_lead_details_by_id(
    crm: CrmService, filters: dict, prioritizer: Any
) -> dict:
    lead_id = filters.get("lead_id")
    if not prioritizer or not lead_id:
        return {"type": "error", "message": "Lead ID required and AI must be enabled"}
    try:
        leads = await prioritizer._fetch_overdue_leads(100)
        lead = next((l for l in leads if l.lead_id == lead_id), None)
        if not lead:
            return {"type": "not_found", "lead_id": lead_id}
        return {"type": "lead_detail", "lead": lead.model_dump()}
    except Exception as exc:
        logger.warning(f"Failed to fetch lead {lead_id}: {exc}")
        return {"type": "error", "message": str(exc)}


async def _handle_leads_with_site_visit_signal(
    crm: CrmService, filters: dict, prioritizer: Any
) -> dict:
    if not prioritizer:
        return {"type": "unavailable", "message": "AI service required for signal detection"}
    limit = int(filters.get("limit") or 10)
    try:
        leads = await prioritizer._fetch_overdue_leads(50)
        matching = [l for l in leads if l.has_site_visit][:limit]
        return {
            "type": "lead_list",
            "signal": "site_visit",
            "rows": [
                {
                    "lead_id": l.lead_id,
                    "name": l.name,
                    "salesperson": l.salesperson_name,
                    "stage": l.stage_name,
                }
                for l in matching
            ],
            "total": len(matching),
        }
    except Exception as exc:
        logger.warning(f"Site visit signal fetch failed: {exc}")
        return {"type": "error", "message": str(exc)}


async def _handle_leads_with_phone_attempt_signal(
    crm: CrmService, filters: dict, prioritizer: Any
) -> dict:
    if not prioritizer:
        return {"type": "unavailable", "message": "AI service required for signal detection"}
    limit = int(filters.get("limit") or 10)
    try:
        leads = await prioritizer._fetch_overdue_leads(50)
        matching = [l for l in leads if l.has_phone_attempt][:limit]
        return {
            "type": "lead_list",
            "signal": "phone_attempt",
            "rows": [
                {
                    "lead_id": l.lead_id,
                    "name": l.name,
                    "salesperson": l.salesperson_name,
                    "stage": l.stage_name,
                }
                for l in matching
            ],
            "total": len(matching),
        }
    except Exception as exc:
        logger.warning(f"Phone attempt signal fetch failed: {exc}")
        return {"type": "error", "message": str(exc)}


async def _handle_missing_contact_summary(crm: CrmService, filters: dict, _p: Any) -> dict:
    dq = await crm.data_quality_summary()
    return {
        "type": "data_quality",
        "missing_contact_count": dq.missing_contact_count,
        "total_issues": dq.total_data_quality_issues,
    }


async def _handle_data_quality_summary(crm: CrmService, filters: dict, _p: Any) -> dict:
    dq = await crm.data_quality_summary()
    return {
        "type": "data_quality_full",
        "new_x_count": dq.new_x_count,
        "missing_stage_count": dq.missing_stage_count,
        "missing_contact_count": dq.missing_contact_count,
        "missing_salesperson_count": dq.missing_salesperson_count,
        "total_data_quality_issues": dq.total_data_quality_issues,
    }


async def _handle_team_performance_summary(crm: CrmService, filters: dict, _p: Any) -> dict:
    team_filter = (filters.get("team") or "").lower()
    rows = await crm.overdue_by_team()
    if team_filter:
        rows = [r for r in rows if team_filter in r.team_name.lower()]
    return {
        "type": "team_performance",
        "rows": [r.model_dump() for r in rows],
        "total_overdue": sum(r.overdue_count for r in rows),
    }


async def _handle_salesperson_performance_summary(
    crm: CrmService, filters: dict, _p: Any
) -> dict:
    sp_filter = (filters.get("salesperson") or "").lower()
    rows = await crm.overdue_by_salesperson()
    if sp_filter:
        rows = [r for r in rows if sp_filter in r.salesperson_name.lower()]
    return {
        "type": "salesperson_performance",
        "rows": [r.model_dump() for r in rows],
        "total_overdue": sum(r.overdue_count for r in rows),
    }


async def _handle_recommendation_top_priority(
    crm: CrmService, filters: dict, prioritizer: Any
) -> dict:
    if not prioritizer:
        return {"type": "unavailable", "message": "AI prioritization service required"}
    limit = min(int(filters.get("limit") or 3), 10)
    try:
        leads = await prioritizer.prioritize_overdue(limit=20, locale="en")
        top = sorted(leads, key=lambda l: l.score, reverse=True)[:limit]
        return {
            "type": "recommendations",
            "leads": [
                {
                    "lead_id": l.lead_id,
                    "score": l.score,
                    "tier": l.tier,
                    "reasoning": l.reasoning,
                    "recommended_action": l.recommended_action,
                }
                for l in top
            ],
        }
    except Exception as exc:
        logger.warning(f"Recommendation fetch failed: {exc}")
        return {"type": "error", "message": str(exc)}


async def _handle_recommendation_for_salesperson(
    crm: CrmService, filters: dict, prioritizer: Any
) -> dict:
    if not prioritizer:
        return {"type": "unavailable", "message": "AI prioritization service required"}
    sp_filter = filters.get("salesperson")
    limit = min(int(filters.get("limit") or 3), 10)
    try:
        leads = await prioritizer.prioritize_overdue(limit=50, locale="en")
        top = sorted(leads, key=lambda l: l.score, reverse=True)[:limit]
        return {
            "type": "recommendations",
            "salesperson_filter": sp_filter,
            "leads": [
                {
                    "lead_id": l.lead_id,
                    "score": l.score,
                    "tier": l.tier,
                    "reasoning": l.reasoning,
                    "recommended_action": l.recommended_action,
                }
                for l in top
            ],
        }
    except Exception as exc:
        logger.warning(f"Salesperson recommendation fetch failed: {exc}")
        return {"type": "error", "message": str(exc)}


async def _handle_free_form_analysis(crm: CrmService, filters: dict, _p: Any) -> dict:
    summary = await crm.summary()
    return {
        "type": "general_summary",
        "total_leads": summary.summary.total_leads,
        "overdue_followups": summary.summary.overdue_followups,
        "critical_overdue": summary.summary.critical_overdue,
        "followups_today": summary.summary.followups_today,
        "data_quality_issues": summary.summary.data_quality_issues,
        "top_overdue_salespersons": [
            r.model_dump() for r in summary.followup_risk.overdue_by_salesperson[:5]
        ],
        "top_overdue_teams": [
            r.model_dump() for r in summary.followup_risk.overdue_by_team[:5]
        ],
        "hint": (
            "Use the above summary as the factual basis for analysis. "
            "For subjective questions (best/worst/most productive), explicitly state "
            "the criterion you use to measure (e.g. fewest overdue leads). "
            "NEVER produce section headers with nothing beneath them."
        ),
    }


_INTENT_HANDLERS: dict[str, Any] = {
    "list_overdue_by_salesperson": _handle_list_overdue_by_salesperson,
    "list_overdue_by_team": _handle_list_overdue_by_team,
    "list_overdue_by_stage": _handle_list_overdue_by_stage,
    "count_by_stage": _handle_count_by_stage,
    "count_overdue_by_stage": _handle_count_overdue_by_stage,
    "count_by_team": _handle_count_by_team,
    "count_by_salesperson": _handle_count_by_salesperson,
    "lead_details_by_id": _handle_lead_details_by_id,
    "leads_with_site_visit_signal": _handle_leads_with_site_visit_signal,
    "leads_with_phone_attempt_signal": _handle_leads_with_phone_attempt_signal,
    "missing_contact_summary": _handle_missing_contact_summary,
    "data_quality_summary": _handle_data_quality_summary,
    "team_performance_summary": _handle_team_performance_summary,
    "salesperson_performance_summary": _handle_salesperson_performance_summary,
    "recommendation_top_priority": _handle_recommendation_top_priority,
    "recommendation_for_salesperson": _handle_recommendation_for_salesperson,
    "free_form_analysis": _handle_free_form_analysis,
}
