"""
Mock Odoo fixtures — 300+ leads covering all edge cases.

Distribution:
  50  overdue in critical stages (28, 34, 35, 37, 41)
  30  no salesperson (user_id = False)
  40  no team (team_id = False)
  60  no phone/mobile (missing contact)
  20  no stage (stage_id = False)
 100  normal healthy leads
Total: 300
"""

from datetime import datetime, timedelta, timezone

# ── Stages ────────────────────────────────────────────────────────────────────

STAGES = [
    {"id": 26, "name": "Closed Won"},
    {"id": 28, "name": "New Lead"},
    {"id": 30, "name": "Closed Lost"},
    {"id": 31, "name": "Closed Duplicate"},
    {"id": 32, "name": "Closed Invalid"},
    {"id": 34, "name": "Qualified"},
    {"id": 35, "name": "Proposal Sent"},
    {"id": 37, "name": "Negotiation"},
    {"id": 38, "name": "Closed No Answer"},
    {"id": 41, "name": "Contract Sent"},
    {"id": 42, "name": "Closed Cancelled"},
    {"id": 44, "name": "New X"},
    {"id": 46, "name": "Closed Transferred"},
]

# ── Teams ─────────────────────────────────────────────────────────────────────

TEAMS = [
    {"id": 1, "name": "North Region"},
    {"id": 2, "name": "South Region"},
    {"id": 3, "name": "East Region"},
    {"id": 4, "name": "West Region"},
    {"id": 5, "name": "Key Accounts"},
]

# ── Users ─────────────────────────────────────────────────────────────────────

USERS = [
    {"id": 10, "name": "Ahmed Hassan"},
    {"id": 11, "name": "Sara Mohamed"},
    {"id": 12, "name": "Omar Ali"},
    {"id": 13, "name": "Nour Ibrahim"},
    {"id": 14, "name": "Khaled Youssef"},
    {"id": 15, "name": "Mona Samir"},
    {"id": 16, "name": "Tarek Farouk"},
    {"id": 17, "name": "Dina Mansour"},
]

# ── Date helpers ──────────────────────────────────────────────────────────────

_NOW = datetime.now(timezone.utc)


def _date(days_ago: int) -> str:
    return (_NOW - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


_CRITICAL_STAGES = [28, 34, 35, 37, 41]
_NORMAL_STAGES = [34, 35, 37]


def _build_lead(
    lead_id: int,
    name: str,
    stage_id: int | None,
    user_id: int | None,
    team_id: int | None,
    phone: str | None,
    mobile: str | None,
    activity_state: str | None,
    days_ago: int = 30,
) -> dict:
    stage = next((s for s in STAGES if s["id"] == stage_id), None)
    user = next((u for u in USERS if u["id"] == user_id), None)
    team = next((t for t in TEAMS if t["id"] == team_id), None)
    return {
        "id": lead_id,
        "name": name,
        "type": "opportunity",
        "active": True,
        "stage_id": [stage["id"], stage["name"]] if stage else False,
        "user_id": [user["id"], user["name"]] if user else False,
        "team_id": [team["id"], team["name"]] if team else False,
        "phone": phone,
        "mobile": mobile,
        "contact_name": f"Contact {lead_id}",
        "source_id": [1, "Website"],
        "activity_state": activity_state,
        "create_date": _date(days_ago),
        "date_deadline": _date(days_ago - 5) if activity_state == "overdue" else None,
    }


def _generate_leads() -> list[dict]:
    leads = []
    lid = 1000

    # 50 overdue leads in critical stages
    for i in range(50):
        stage = _CRITICAL_STAGES[i % len(_CRITICAL_STAGES)]
        leads.append(
            _build_lead(
                lead_id=lid,
                name=f"Overdue Opportunity {i + 1}",
                stage_id=stage,
                user_id=USERS[i % len(USERS)]["id"],
                team_id=TEAMS[i % len(TEAMS)]["id"],
                phone=f"+20100{lid:06d}",
                mobile=None,
                activity_state="overdue",
                days_ago=7 + (i % 90),
            )
        )
        lid += 1

    # 30 leads with no salesperson
    for i in range(30):
        leads.append(
            _build_lead(
                lead_id=lid,
                name=f"No Salesperson {i + 1}",
                stage_id=_NORMAL_STAGES[i % len(_NORMAL_STAGES)],
                user_id=None,
                team_id=TEAMS[i % len(TEAMS)]["id"],
                phone=f"+20110{lid:06d}",
                mobile=None,
                activity_state="planned",
                days_ago=14 + (i % 60),
            )
        )
        lid += 1

    # 40 leads with no team
    for i in range(40):
        leads.append(
            _build_lead(
                lead_id=lid,
                name=f"No Team {i + 1}",
                stage_id=_NORMAL_STAGES[i % len(_NORMAL_STAGES)],
                user_id=USERS[i % len(USERS)]["id"],
                team_id=None,
                phone=f"+20120{lid:06d}",
                mobile=None,
                activity_state=None,
                days_ago=10 + (i % 30),
            )
        )
        lid += 1

    # 60 leads with no phone/mobile (missing contact data quality)
    for i in range(60):
        leads.append(
            _build_lead(
                lead_id=lid,
                name=f"Missing Contact {i + 1}",
                stage_id=_NORMAL_STAGES[i % len(_NORMAL_STAGES)],
                user_id=USERS[i % len(USERS)]["id"],
                team_id=TEAMS[i % len(TEAMS)]["id"],
                phone=None,
                mobile=None,
                activity_state="planned" if i % 3 == 0 else None,
                days_ago=5 + (i % 120),
            )
        )
        lid += 1

    # 20 leads with no stage
    for i in range(20):
        leads.append(
            _build_lead(
                lead_id=lid,
                name=f"No Stage Lead {i + 1}",
                stage_id=None,
                user_id=USERS[i % len(USERS)]["id"],
                team_id=TEAMS[i % len(TEAMS)]["id"],
                phone=f"+20130{lid:06d}",
                mobile=None,
                activity_state=None,
                days_ago=3 + (i % 15),
            )
        )
        lid += 1

    # 100 normal healthy leads
    for i in range(100):
        state = ["overdue", "planned", "today", None][i % 4]
        leads.append(
            _build_lead(
                lead_id=lid,
                name=f"Normal Opportunity {i + 1}",
                stage_id=_NORMAL_STAGES[i % len(_NORMAL_STAGES)],
                user_id=USERS[i % len(USERS)]["id"],
                team_id=TEAMS[i % len(TEAMS)]["id"],
                phone=f"+20140{lid:06d}",
                mobile=f"+20150{lid:06d}",
                activity_state=state,
                days_ago=1 + (i % 180),
            )
        )
        lid += 1

    return leads


LEADS: list[dict] = _generate_leads()
