"""
CRM Service — business logic layer.
Wraps OdooClient queries with caching and returns typed Pydantic models.
Business logic is identical to the original CrmEngine; only structure changed.
"""

from typing import Optional, cast

from loguru import logger

from backend.core.cache import get_cached, set_cached
from backend.modules.crm.client import OdooClient
from backend.modules.crm.domain import (
    BASE_DOMAIN,
    build_missing_contact_domain,
    get_closed_excluded_stage_ids,
    get_critical_stage_ids,
    get_data_quality_stage_ids,
)
from backend.modules.crm.schemas import (
    ActivitySummary,
    DataQuality,
    DataQualityMissingContactResponse,
    FollowupRisk,
    FollowupRiskResponse,
    MissingContactRow,
    OverdueBySalesperson,
    OverdueByStage,
    OverdueByTeam,
    OverdueMatrixRow,
    SummaryResponse,
)

_MODE = "read_only"
_SCOPE = "resolved_opportunities_only"


class CrmService:
    def __init__(self, client: Optional[OdooClient] = None) -> None:
        self.client = client if client is not None else OdooClient()

    # ── Activity summary ──────────────────────────────────────────────────────

    def activity_summary(self) -> dict:
        rows = self.client.execute_kw(
            "crm.lead",
            "read_group",
            args=[BASE_DOMAIN, ["activity_state"], ["activity_state"]],
            kwargs={"orderby": "activity_state"},
        )

        result = {
            "overdue_followups": 0,
            "planned_followups": 0,
            "followups_today": 0,
            "no_activity_leads": 0,
        }

        for row in rows:
            state = row.get("activity_state")
            count = row.get("activity_state_count", 0)
            if state == "overdue":
                result["overdue_followups"] = count
            elif state == "planned":
                result["planned_followups"] = count
            elif state == "today":
                result["followups_today"] = count
            elif state is False:
                result["no_activity_leads"] = count

        return result

    # ── Counts ────────────────────────────────────────────────────────────────

    def total_leads(self) -> int:
        rows = self.client.execute_kw(
            "crm.lead",
            "read_group",
            args=[BASE_DOMAIN, ["__count"], []],
            kwargs={},
        )
        return rows[0].get("__count", 0) if rows else 0

    def critical_overdue_count(self) -> int:
        domain = BASE_DOMAIN + [
            ["activity_state", "=", "overdue"],
            ["stage_id", "in", get_critical_stage_ids()],
        ]
        rows = self.client.execute_kw(
            "crm.lead", "read_group", args=[domain, ["__count"], []], kwargs={}
        )
        return rows[0].get("__count", 0) if rows else 0

    # ── Data quality ──────────────────────────────────────────────────────────

    def data_quality_summary(self) -> DataQuality:
        def _count(extra: list) -> int:
            rows = self.client.execute_kw(
                "crm.lead",
                "read_group",
                args=[BASE_DOMAIN + extra, ["__count"], []],
                kwargs={},
            )
            return rows[0].get("__count", 0) if rows else 0

        new_x = _count([["stage_id", "in", get_data_quality_stage_ids()]])
        missing_stage = _count([["stage_id", "=", False]])
        missing_contact = _count(self._missing_contact_extra())
        missing_salesperson = _count([["user_id", "=", False]])

        return DataQuality(
            new_x_count=new_x,
            missing_stage_count=missing_stage,
            missing_contact_count=missing_contact,
            missing_salesperson_count=missing_salesperson,
            total_data_quality_issues=new_x + missing_stage + missing_contact + missing_salesperson,
        )

    def _missing_contact_extra(self) -> list:
        """Return the phone-field conditions without the BASE_DOMAIN prefix."""
        full = build_missing_contact_domain()
        return full[len(BASE_DOMAIN) :]

    def missing_contact_details(self) -> list[MissingContactRow]:
        rows = self.client.execute_kw(
            "crm.lead",
            "search_read",
            args=[build_missing_contact_domain()],
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
                MissingContactRow(
                    lead_id=row["id"],
                    opportunity_name=row.get("name") or "",
                    contact_name=row.get("contact_name") or "",
                    salesperson_id=user[0] if user else None,
                    salesperson_name=user[1] if user else "Unassigned",
                    team_id=team[0] if team else None,
                    team_name=team[1] if team else "Unassigned Team",
                    stage_id=stage[0] if stage else None,
                    stage_name=stage[1] if stage else "No Stage",
                    source_id=source[0] if source else None,
                    source_name=source[1] if source else "No Source",
                    create_date=row.get("create_date") or "",
                )
            )
        return result

    # ── Overdue breakdowns ────────────────────────────────────────────────────

    def overdue_by_salesperson(self) -> list[OverdueBySalesperson]:
        domain = BASE_DOMAIN + [
            ["activity_state", "=", "overdue"],
            ["stage_id", "not in", get_closed_excluded_stage_ids()],
        ]
        rows = self.client.execute_kw(
            "crm.lead",
            "read_group",
            args=[domain, ["user_id"], ["user_id"]],
            kwargs={"orderby": "user_id"},
        )
        result = []
        for row in rows:
            user = row.get("user_id")
            result.append(
                OverdueBySalesperson(
                    salesperson_id=user[0] if user else None,
                    salesperson_name=user[1] if user else "Unassigned",
                    overdue_count=row.get("user_id_count", 0),
                )
            )
        result.sort(key=lambda r: r.overdue_count, reverse=True)
        return result

    def overdue_by_team(self) -> list[OverdueByTeam]:
        domain = BASE_DOMAIN + [
            ["activity_state", "=", "overdue"],
            ["stage_id", "not in", get_closed_excluded_stage_ids()],
        ]
        rows = self.client.execute_kw(
            "crm.lead",
            "read_group",
            args=[domain, ["team_id"], ["team_id"]],
            kwargs={"orderby": "team_id"},
        )
        result = []
        for row in rows:
            team = row.get("team_id")
            result.append(
                OverdueByTeam(
                    team_id=team[0] if team else None,
                    team_name=team[1] if team else "Unassigned",
                    overdue_count=row.get("team_id_count", 0),
                )
            )
        result.sort(key=lambda r: r.overdue_count, reverse=True)
        return result

    def overdue_by_stage(self) -> list[OverdueByStage]:
        domain = BASE_DOMAIN + [
            ["activity_state", "=", "overdue"],
            ["stage_id", "not in", get_closed_excluded_stage_ids()],
        ]
        rows = self.client.execute_kw(
            "crm.lead",
            "read_group",
            args=[domain, ["stage_id"], ["stage_id"]],
            kwargs={"orderby": "stage_id"},
        )
        result = []
        for row in rows:
            stage = row.get("stage_id")
            result.append(
                OverdueByStage(
                    stage_id=stage[0] if stage else None,
                    stage_name=stage[1] if stage else "No Stage",
                    overdue_count=row.get("stage_id_count", 0),
                )
            )
        result.sort(key=lambda r: r.overdue_count, reverse=True)
        return result

    def overdue_matrix(self) -> list[OverdueMatrixRow]:
        domain = BASE_DOMAIN + [
            ["activity_state", "=", "overdue"],
            ["stage_id", "not in", get_closed_excluded_stage_ids()],
        ]
        rows = self.client.execute_kw(
            "crm.lead",
            "read_group",
            args=[domain, ["__count"], ["team_id", "user_id", "stage_id"]],
            kwargs={"lazy": False},
        )
        result = []
        for row in rows:
            team = row.get("team_id")
            user = row.get("user_id")
            stage = row.get("stage_id")
            result.append(
                OverdueMatrixRow(
                    team_id=team[0] if team else None,
                    team_name=team[1] if team else "Unassigned Team",
                    salesperson_id=user[0] if user else None,
                    salesperson_name=user[1] if user else "Unassigned Salesperson",
                    stage_id=stage[0] if stage else None,
                    stage_name=stage[1] if stage else "No Stage",
                    overdue_count=row.get("__count", 0),
                )
            )
        result.sort(key=lambda r: r.overdue_count, reverse=True)
        return result

    # ── Composite responses (with caching) ───────────────────────────────────

    def summary(self) -> SummaryResponse:
        cached = get_cached("crm:summary")
        if cached is not None:
            logger.debug("Cache hit: crm:summary")
            return cast(SummaryResponse, cached)

        activity = self.activity_summary()
        dq = self.data_quality_summary()

        result = SummaryResponse(
            mode=_MODE,
            scope=_SCOPE,
            summary=ActivitySummary(
                total_leads=self.total_leads(),
                followups_today=activity["followups_today"],
                overdue_followups=activity["overdue_followups"],
                planned_followups=activity["planned_followups"],
                no_activity_leads=activity["no_activity_leads"],
                critical_overdue=self.critical_overdue_count(),
                data_quality_issues=dq.total_data_quality_issues,
            ),
            data_quality=dq,
            followup_risk=FollowupRisk(
                overdue_by_salesperson=self.overdue_by_salesperson(),
                overdue_by_team=self.overdue_by_team(),
                overdue_by_stage=self.overdue_by_stage(),
                overdue_matrix_by_team_salesperson_stage=self.overdue_matrix(),
            ),
        )
        set_cached("crm:summary", result)
        return result

    def followup_risk_response(self) -> FollowupRiskResponse:
        cached = get_cached("crm:followup_risk")
        if cached is not None:
            logger.debug("Cache hit: crm:followup_risk")
            return cast(FollowupRiskResponse, cached)

        result = FollowupRiskResponse(
            mode=_MODE,
            scope=_SCOPE,
            followup_risk=FollowupRisk(
                overdue_by_salesperson=self.overdue_by_salesperson(),
                overdue_by_team=self.overdue_by_team(),
                overdue_by_stage=self.overdue_by_stage(),
                overdue_matrix_by_team_salesperson_stage=self.overdue_matrix(),
            ),
        )
        set_cached("crm:followup_risk", result)
        return result

    def missing_contact_response(self) -> DataQualityMissingContactResponse:
        cached = get_cached("crm:missing_contact")
        if cached is not None:
            logger.debug("Cache hit: crm:missing_contact")
            return cast(DataQualityMissingContactResponse, cached)

        result = DataQualityMissingContactResponse(
            mode=_MODE,
            scope=_SCOPE,
            missing_contact_details=self.missing_contact_details(),
        )
        set_cached("crm:missing_contact", result)
        return result
