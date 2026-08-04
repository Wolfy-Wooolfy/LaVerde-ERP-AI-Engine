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


def build_missing_contact_domain() -> list:
    """Return a domain that matches leads with no phone info whatsoever."""
    domain: list = list(BASE_DOMAIN)
    for field in CONTACT_PHONE_FIELDS:
        domain.append([field, "=", False])
    return domain


def build_missing_stage_domain() -> list:
    """Return a domain that matches leads with no stage set.

    Single source for BOTH the dashboard card count and the
    /data-quality/missing-stage list — identity card == list by construction.
    """
    return list(BASE_DOMAIN) + [["stage_id", "=", False]]


def build_missing_salesperson_domain() -> list:
    """Return a domain that matches leads with no salesperson assigned.

    Single source for BOTH the dashboard card count and the
    /data-quality/missing-salesperson list — identity card == list by construction.
    """
    return list(BASE_DOMAIN) + [["user_id", "=", False]]


def build_missing_linked_contact_domain() -> list:
    """Return a domain that matches leads with no linked contact (partner_id).

    Single source for BOTH the hub tab headline count and the
    /data-quality?tab=linked-contact list — identity count == list by construction.

    partner_id (not contact_name) is the truthful "missing linked contact" signal:
    the shipped contact-name fallback (contact_name → partner display → partner_name
    → email) can fill a display name from free text / email even when partner_id is
    empty, so a contact_name check would mislabel real, unlinked leads.
    """
    return list(BASE_DOMAIN) + [["partner_id", "=", False]]
