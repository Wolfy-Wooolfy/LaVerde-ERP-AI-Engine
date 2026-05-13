"""CRM module registration for the AI Module Registry."""

from backend.shared.ai.module_registry import AIModuleRegistry, AIModuleSpec

CRM_MODULE = AIModuleSpec(
    name="crm",
    display_name_en="CRM",
    display_name_ar="إدارة علاقات العملاء",
    intents=[
        "overdue_summary",
        "critical_leads",
        "followup_risk",
        "salesperson_performance",
        "stage_distribution",
        "data_quality",
        "missing_contact",
        "lead_detail",
        "team_summary",
        "pipeline_value",
        "conversational",
    ],
    suggested_questions=[
        "Who has the most overdue leads?",
        "Show me critical leads this week",
        "Which stage has the most stalled deals?",
        "Which salesperson needs follow-up attention?",
        "How many leads are missing contact info?",
    ],
    chat_endpoint="/api/v1/chat",
)


def register() -> None:
    AIModuleRegistry.register(CRM_MODULE)
