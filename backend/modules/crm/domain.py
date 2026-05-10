"""
Business rules and domain constants for the CRM module.
All stage ID lists are driven by Settings so they can be overridden per-environment via .env.
"""

from backend.core.config import settings

# Every query in this app is restricted to resolved opportunities.
BASE_DOMAIN: list = [
    ["type", "=", "opportunity"],
    ["opportunity_status", "=", "resolved"],
]

# All phone-related fields — a lead missing ALL of these has no contact info.
CONTACT_PHONE_FIELDS: list[str] = [
    "phone",
    "mobile",
    "phone_one",
    "phone_two",
    "phone_three",
    "phone_four",
    "phone_five",
    "phone_six",
    "phone_seven",
    "phone_eight",
    "phone_nine",
    "phone_ten",
    "phone_note_ids",
]


def get_critical_stage_ids() -> list[int]:
    """Stages where an overdue activity is considered critical."""
    return settings.critical_stage_ids


def get_closed_excluded_stage_ids() -> list[int]:
    """Terminal stages excluded from overdue follow-up tracking."""
    return settings.closed_excluded_stage_ids


def get_data_quality_stage_ids() -> list[int]:
    """Stages that flag a lead as a data-quality issue (needs classification)."""
    return settings.data_quality_stage_ids


def build_missing_contact_domain() -> list:
    """Return a domain that matches leads with no phone info whatsoever."""
    domain: list = list(BASE_DOMAIN)
    for field in CONTACT_PHONE_FIELDS:
        domain.append([field, "=", False])
    return domain
