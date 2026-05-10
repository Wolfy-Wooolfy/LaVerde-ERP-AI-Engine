from backend.odoo_client import OdooClient


CRITICAL_STAGE_IDS = [28, 34, 35, 37, 41]
CLOSED_EXCLUDED_STAGE_IDS = [26, 30, 31, 32, 38, 42, 46]
DATA_QUALITY_STAGE_IDS = [44]
CONTACT_PHONE_FIELDS = [
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
BASE_DOMAIN = [
    ["type", "=", "opportunity"],
    ["opportunity_status", "=", "resolved"],
]


class CrmEngine:
    def __init__(self):
        self.client = OdooClient()

    def activity_summary(self):
        rows = self.client.execute_kw(
            "crm.lead",
            "read_group",
            args=[
                BASE_DOMAIN,
                ["activity_state"],
                ["activity_state"],
            ],
            kwargs={
                "orderby": "activity_state",
            },
        )

        summary = {
            "overdue_followups": 0,
            "planned_followups": 0,
            "followups_today": 0,
            "no_activity_leads": 0,
        }

        for row in rows:
            state = row.get("activity_state")
            count = row.get("activity_state_count", 0)

            if state == "overdue":
                summary["overdue_followups"] = count
            elif state == "planned":
                summary["planned_followups"] = count
            elif state == "today":
                summary["followups_today"] = count
            elif state is False:
                summary["no_activity_leads"] = count

        return summary

    def total_leads(self):
        rows = self.client.execute_kw(
            "crm.lead",
            "read_group",
            args=[
                BASE_DOMAIN,
                ["__count"],
                [],
            ],
            kwargs={},
        )

        if not rows:
            return 0

        return rows[0].get("__count", 0)

    def critical_overdue_count(self):
        rows = self.client.execute_kw(
            "crm.lead",
            "read_group",
            args=[
                BASE_DOMAIN + [
                    ["activity_state", "=", "overdue"],
                    ["stage_id", "in", CRITICAL_STAGE_IDS],
                ],
                ["__count"],
                [],
            ],
            kwargs={},
        )

        if not rows:
            return 0

        return rows[0].get("__count", 0)

    def missing_contact_domain(self):
        domain = list(BASE_DOMAIN)

        for field_name in CONTACT_PHONE_FIELDS:
            domain.append([field_name, "=", False])

        return domain

    def data_quality_summary(self):
        new_x_rows = self.client.execute_kw(
            "crm.lead",
            "read_group",
            args=[
                BASE_DOMAIN + [
                    ["stage_id", "in", DATA_QUALITY_STAGE_IDS],
                ],
                ["__count"],
                [],
            ],
            kwargs={},
        )

        missing_stage_rows = self.client.execute_kw(
            "crm.lead",
            "read_group",
            args=[
                BASE_DOMAIN + [
                    ["stage_id", "=", False],
                ],
                ["__count"],
                [],
            ],
            kwargs={},
        )

        missing_contact_rows = self.client.execute_kw(
            "crm.lead",
            "read_group",
            args=[
                self.missing_contact_domain(),
                ["__count"],
                [],
            ],
            kwargs={},
        )

        missing_salesperson_rows = self.client.execute_kw(
            "crm.lead",
            "read_group",
            args=[
                BASE_DOMAIN + [
                    ["user_id", "=", False],
                ],
                ["__count"],
                [],
            ],
            kwargs={},
        )

        new_x_count = new_x_rows[0].get("__count", 0) if new_x_rows else 0
        missing_stage_count = missing_stage_rows[0].get("__count", 0) if missing_stage_rows else 0
        missing_contact_count = missing_contact_rows[0].get("__count", 0) if missing_contact_rows else 0
        missing_salesperson_count = missing_salesperson_rows[0].get("__count", 0) if missing_salesperson_rows else 0

        return {
            "new_x_count": new_x_count,
            "missing_stage_count": missing_stage_count,
            "missing_contact_count": missing_contact_count,
            "missing_salesperson_count": missing_salesperson_count,
            "total_data_quality_issues": (
                new_x_count
                + missing_stage_count
                + missing_contact_count
                + missing_salesperson_count
            ),
        }

    def missing_contact_details(self):
        rows = self.client.execute_kw(
            "crm.lead",
            "search_read",
            args=[
                self.missing_contact_domain(),
            ],
            kwargs={
                "fields": [
                    "id",
                    "name",
                    "contact_name",
                    "user_id",
                    "team_id",
                    "stage_id",
                    "source_id",
                    "create_date",
                ],
                "limit": 500,
                "order": "create_date desc",
            },
        )

        result = []

        for row in rows:
            user = row.get("user_id")
            team = row.get("team_id")
            stage = row.get("stage_id")
            source = row.get("source_id")

            result.append(
                {
                    "lead_id": row.get("id"),
                    "opportunity_name": row.get("name") or "",
                    "contact_name": row.get("contact_name") or "",
                    "salesperson_id": user[0] if user else None,
                    "salesperson_name": user[1] if user else "Unassigned",
                    "team_id": team[0] if team else None,
                    "team_name": team[1] if team else "Unassigned Team",
                    "stage_id": stage[0] if stage else None,
                    "stage_name": stage[1] if stage else "No Stage",
                    "source_id": source[0] if source else None,
                    "source_name": source[1] if source else "No Source",
                    "create_date": row.get("create_date") or "",
                }
            )

        return result

    def overdue_by_salesperson(self):
        rows = self.client.execute_kw(
            "crm.lead",
            "read_group",
            args=[
                BASE_DOMAIN + [
                    ["activity_state", "=", "overdue"],
                    ["stage_id", "not in", CLOSED_EXCLUDED_STAGE_IDS],
                ],
                ["user_id"],
                ["user_id"],
            ],
            kwargs={
                "orderby": "user_id",
            },
        )

        result = []

        for row in rows:
            user = row.get("user_id")
            count = row.get("user_id_count", 0)

            if not user:
                salesperson_id = None
                salesperson_name = "Unassigned"
            else:
                salesperson_id = user[0]
                salesperson_name = user[1]

            result.append(
                {
                    "salesperson_id": salesperson_id,
                    "salesperson_name": salesperson_name,
                    "overdue_count": count,
                }
            )

        result.sort(key=lambda item: item["overdue_count"], reverse=True)

        return result

    def overdue_by_team(self):
        rows = self.client.execute_kw(
            "crm.lead",
            "read_group",
            args=[
                BASE_DOMAIN + [
                    ["activity_state", "=", "overdue"],
                    ["stage_id", "not in", CLOSED_EXCLUDED_STAGE_IDS],
                ],
                ["team_id"],
                ["team_id"],
            ],
            kwargs={
                "orderby": "team_id",
            },
        )

        result = []

        for row in rows:
            team = row.get("team_id")
            count = row.get("team_id_count", 0)

            if not team:
                team_id = None
                team_name = "Unassigned"
            else:
                team_id = team[0]
                team_name = team[1]

            result.append(
                {
                    "team_id": team_id,
                    "team_name": team_name,
                    "overdue_count": count,
                }
            )

        result.sort(key=lambda item: item["overdue_count"], reverse=True)

        return result

    def overdue_by_stage(self):
        rows = self.client.execute_kw(
            "crm.lead",
            "read_group",
            args=[
                BASE_DOMAIN + [
                    ["activity_state", "=", "overdue"],
                    ["stage_id", "not in", CLOSED_EXCLUDED_STAGE_IDS],
                ],
                ["stage_id"],
                ["stage_id"],
            ],
            kwargs={
                "orderby": "stage_id",
            },
        )

        result = []

        for row in rows:
            stage = row.get("stage_id")
            count = row.get("stage_id_count", 0)

            if not stage:
                stage_id = None
                stage_name = "No Stage"
            else:
                stage_id = stage[0]
                stage_name = stage[1]

            result.append(
                {
                    "stage_id": stage_id,
                    "stage_name": stage_name,
                    "overdue_count": count,
                }
            )

        result.sort(key=lambda item: item["overdue_count"], reverse=True)

        return result

    def overdue_matrix_by_team_salesperson_stage(self):
        rows = self.client.execute_kw(
            "crm.lead",
            "read_group",
            args=[
                BASE_DOMAIN + [
                    ["activity_state", "=", "overdue"],
                    ["stage_id", "not in", CLOSED_EXCLUDED_STAGE_IDS],
                ],
                ["__count"],
                ["team_id", "user_id", "stage_id"],
            ],
            kwargs={
                "lazy": False,
            },
        )

        result = []

        for row in rows:
            team = row.get("team_id")
            user = row.get("user_id")
            stage = row.get("stage_id")
            count = row.get("__count", 0)

            result.append(
                {
                    "team_id": team[0] if team else None,
                    "team_name": team[1] if team else "Unassigned Team",
                    "salesperson_id": user[0] if user else None,
                    "salesperson_name": user[1] if user else "Unassigned Salesperson",
                    "stage_id": stage[0] if stage else None,
                    "stage_name": stage[1] if stage else "No Stage",
                    "overdue_count": count,
                }
            )

        result.sort(key=lambda item: item["overdue_count"], reverse=True)

        return result

    def summary(self):
        activity = self.activity_summary()
        data_quality = self.data_quality_summary()

        return {
            "mode": "read_only",
            "scope": "resolved_opportunities_only",
            "summary": {
                "total_leads": self.total_leads(),
                "followups_today": activity["followups_today"],
                "overdue_followups": activity["overdue_followups"],
                "planned_followups": activity["planned_followups"],
                "no_activity_leads": activity["no_activity_leads"],
                "critical_overdue": self.critical_overdue_count(),
                "data_quality_issues": data_quality["total_data_quality_issues"],
            },
            "data_quality": data_quality,
            "followup_risk": {
                "overdue_by_salesperson": self.overdue_by_salesperson(),
                "overdue_by_team": self.overdue_by_team(),
                "overdue_by_stage": self.overdue_by_stage(),
                "overdue_matrix_by_team_salesperson_stage": self.overdue_matrix_by_team_salesperson_stage(),
            },
        }