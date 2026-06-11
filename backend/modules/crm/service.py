"""
CRM Service — async business logic layer.
All Odoo calls are async; summary() fires 8 concurrent requests via asyncio.gather.
"""

import asyncio
import math
from typing import Optional, cast

from loguru import logger

from backend.core.cache import get_cached, set_cached
from backend.shared.odoo.client import OdooClient
from backend.modules.crm.domain import (
    BASE_DOMAIN,
    build_missing_contact_domain,
    build_missing_salesperson_domain,
    build_missing_stage_domain,
    get_closed_excluded_stage_ids,
    get_critical_stage_ids,
    get_data_quality_stage_ids,
)
from backend.modules.crm.schemas import (
    ActivitySummary,
    DataQuality,
    FollowupRisk,
    FollowupRiskResponse,
    MissingContactRow,
    OverdueBySalesperson,
    OverdueByStage,
    OverdueByTeam,
    OverdueMatrixRow,
    PaginatedMissingContactResponse,
    Pagination,
    StageCountResult,
    SummaryResponse,
)

_MODE = "read_only"
_SCOPE = "resolved_opportunities_only"


class CrmService:
    def __init__(self, client: Optional[OdooClient] = None) -> None:
        self.client = client if client is not None else OdooClient()

    # ── Leaf-level Odoo calls (each = 1 await) ────────────────────────────────

    async def activity_summary(self) -> dict:
        rows = await self.client.execute_kw(
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

    async def total_leads(self) -> int:
        rows = await self.client.execute_kw(
            "crm.lead",
            "read_group",
            args=[BASE_DOMAIN, ["__count"], []],
            kwargs={},
        )
        return rows[0].get("__count", 0) if rows else 0

    async def critical_overdue_count(self) -> int:
        domain = BASE_DOMAIN + [
            ["activity_state", "=", "overdue"],
            ["stage_id", "in", get_critical_stage_ids()],
        ]
        rows = await self.client.execute_kw(
            "crm.lead", "read_group", args=[domain, ["__count"], []], kwargs={}
        )
        return rows[0].get("__count", 0) if rows else 0

    async def _count_domain(self, extra: list) -> int:
        """Count leads matching BASE_DOMAIN + extra conditions."""
        rows = await self.client.execute_kw(
            "crm.lead",
            "read_group",
            args=[BASE_DOMAIN + extra, ["__count"], []],
            kwargs={},
        )
        return rows[0].get("__count", 0) if rows else 0

    async def overdue_by_salesperson(self) -> list[OverdueBySalesperson]:
        domain = BASE_DOMAIN + [
            ["activity_state", "=", "overdue"],
            ["stage_id", "not in", get_closed_excluded_stage_ids()],
        ]
        rows = await self.client.execute_kw(
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

    async def overdue_by_team(self) -> list[OverdueByTeam]:
        domain = BASE_DOMAIN + [
            ["activity_state", "=", "overdue"],
            ["stage_id", "not in", get_closed_excluded_stage_ids()],
        ]
        rows = await self.client.execute_kw(
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

    async def overdue_by_stage(self) -> list[OverdueByStage]:
        domain = BASE_DOMAIN + [
            ["activity_state", "=", "overdue"],
            ["stage_id", "not in", get_closed_excluded_stage_ids()],
        ]
        rows = await self.client.execute_kw(
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

    async def count_leads_by_stage(
        self,
        stage_name: str,
        overdue_only: bool = False,
    ) -> StageCountResult:
        """
        Count resolved opportunities in a stage by EXACT name match (case-insensitive).

        "New" matches "New" only — never "New X". If overdue_only=True, only leads
        with activity_state=overdue are counted.
        """
        stages = await self.client.execute_kw(
            "crm.stage",
            "search_read",
            args=[[]],
            kwargs={"fields": ["id", "name"], "limit": 200},
        )

        target = stage_name.strip().lower()
        matched = [s for s in stages if s["name"].strip().lower() == target]

        if not matched:
            return StageCountResult(
                stage_name=stage_name,
                matched_stages=[],
                count=0,
                overdue_only=overdue_only,
            )

        matched_ids = [s["id"] for s in matched]
        domain = list(BASE_DOMAIN) + [["stage_id", "in", matched_ids]]
        if overdue_only:
            domain.append(["activity_state", "=", "overdue"])

        rows = await self.client.execute_kw(
            "crm.lead",
            "read_group",
            args=[domain, ["__count"], []],
            kwargs={},
        )
        count = rows[0].get("__count", 0) if rows else 0

        return StageCountResult(
            stage_name=matched[0]["name"],
            matched_stages=[{"id": s["id"], "name": s["name"]} for s in matched],
            count=count,
            overdue_only=overdue_only,
        )

    async def overdue_matrix(self) -> list[OverdueMatrixRow]:
        domain = BASE_DOMAIN + [
            ["activity_state", "=", "overdue"],
            ["stage_id", "not in", get_closed_excluded_stage_ids()],
        ]
        rows = await self.client.execute_kw(
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

    # ── Composite methods (internally parallel) ────────────────────────────────

    async def data_quality_summary(self) -> DataQuality:
        """Run 4 Odoo count queries in parallel."""
        new_x, missing_stage, missing_contact, missing_sp = await asyncio.gather(
            self._count_domain([["stage_id", "in", get_data_quality_stage_ids()]]),
            self._count_domain(self._missing_stage_extra()),
            self._count_domain(self._missing_contact_extra()),
            self._count_domain(self._missing_salesperson_extra()),
        )
        return DataQuality(
            new_x_count=new_x,
            missing_stage_count=missing_stage,
            missing_contact_count=missing_contact,
            missing_salesperson_count=missing_sp,
            total_data_quality_issues=new_x + missing_stage + missing_contact + missing_sp,
        )

    def _missing_contact_extra(self) -> list:
        """Return the phone-field conditions without the BASE_DOMAIN prefix."""
        full = build_missing_contact_domain()
        return full[len(BASE_DOMAIN) :]

    def _missing_stage_extra(self) -> list:
        """Return the no-stage condition without the BASE_DOMAIN prefix."""
        full = build_missing_stage_domain()
        return full[len(BASE_DOMAIN) :]

    def _missing_salesperson_extra(self) -> list:
        """Return the no-salesperson condition without the BASE_DOMAIN prefix."""
        full = build_missing_salesperson_domain()
        return full[len(BASE_DOMAIN) :]

    async def missing_contact_details(
        self,
        page: int = 1,
        page_size: int = 50,
        team_id: Optional[int] = None,
        salesperson_id: Optional[int] = None,
        sort: str = "create_date desc",
    ) -> tuple[list[MissingContactRow], int]:
        """Return paginated missing-contact rows and the total count."""
        return await self._dq_lead_details(
            build_missing_contact_domain(),
            page=page,
            page_size=page_size,
            team_id=team_id,
            salesperson_id=salesperson_id,
            sort=sort,
        )

    async def missing_stage_details(
        self,
        page: int = 1,
        page_size: int = 50,
        team_id: Optional[int] = None,
        salesperson_id: Optional[int] = None,
        sort: str = "create_date desc",
    ) -> tuple[list[MissingContactRow], int]:
        """Return paginated missing-stage rows and the total count.

        Domain is build_missing_stage_domain() — the VERBATIM domain behind the
        dashboard card count (identity card == list by construction).
        """
        return await self._dq_lead_details(
            build_missing_stage_domain(),
            page=page,
            page_size=page_size,
            team_id=team_id,
            salesperson_id=salesperson_id,
            sort=sort,
        )

    async def missing_salesperson_details(
        self,
        page: int = 1,
        page_size: int = 50,
        team_id: Optional[int] = None,
        salesperson_id: Optional[int] = None,
        sort: str = "create_date desc",
    ) -> tuple[list[MissingContactRow], int]:
        """Return paginated missing-salesperson rows and the total count.

        Domain is build_missing_salesperson_domain() — the VERBATIM domain behind
        the dashboard card count (identity card == list by construction).
        """
        return await self._dq_lead_details(
            build_missing_salesperson_domain(),
            page=page,
            page_size=page_size,
            team_id=team_id,
            salesperson_id=salesperson_id,
            sort=sort,
        )

    async def _dq_lead_details(
        self,
        domain: list,
        *,
        page: int = 1,
        page_size: int = 50,
        team_id: Optional[int] = None,
        salesperson_id: Optional[int] = None,
        sort: str = "create_date desc",
    ) -> tuple[list[MissingContactRow], int]:
        """Shared paginated lead fetch for the data-quality detail lists."""
        if team_id is not None:
            domain = domain + [["team_id", "=", team_id]]
        if salesperson_id is not None:
            domain = domain + [["user_id", "=", salesperson_id]]

        offset = (page - 1) * page_size

        rows_raw, count_rows = await asyncio.gather(
            self.client.execute_kw(
                "crm.lead",
                "search_read",
                args=[domain],
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
                    "limit": page_size,
                    "offset": offset,
                    "order": sort,
                },
            ),
            self.client.execute_kw(
                "crm.lead",
                "read_group",
                args=[domain, ["__count"], []],
                kwargs={},
            ),
        )

        total = count_rows[0].get("__count", 0) if count_rows else 0

        result = []
        for row in rows_raw:
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
        return result, total

    # ── Composite responses (with caching, parallel gather) ───────────────────

    async def summary(self) -> SummaryResponse:
        cached = get_cached("crm:summary")
        if cached is not None:
            logger.debug("Cache hit: crm:summary")
            return cast(SummaryResponse, cached)

        # Fire 8 coroutines concurrently (data_quality runs 4 in parallel internally)
        _results = await asyncio.gather(
            self.activity_summary(),
            self.data_quality_summary(),
            self.total_leads(),
            self.critical_overdue_count(),
            self.overdue_by_salesperson(),
            self.overdue_by_team(),
            self.overdue_by_stage(),
            self.overdue_matrix(),
        )
        activity: dict = _results[0]  # type: ignore[assignment]
        dq: DataQuality = _results[1]  # type: ignore[assignment]
        total: int = _results[2]  # type: ignore[assignment]
        critical: int = _results[3]  # type: ignore[assignment]
        overdue_sp: list = _results[4]  # type: ignore[assignment]
        overdue_team: list = _results[5]  # type: ignore[assignment]
        overdue_stage: list = _results[6]  # type: ignore[assignment]
        matrix: list = _results[7]  # type: ignore[assignment]

        result = SummaryResponse(
            mode=_MODE,
            scope=_SCOPE,
            summary=ActivitySummary(
                total_leads=total,
                followups_today=activity["followups_today"],
                overdue_followups=activity["overdue_followups"],
                planned_followups=activity["planned_followups"],
                no_activity_leads=activity["no_activity_leads"],
                critical_overdue=critical,
                data_quality_issues=dq.total_data_quality_issues,
            ),
            data_quality=dq,
            followup_risk=FollowupRisk(
                overdue_by_salesperson=overdue_sp,
                overdue_by_team=overdue_team,
                overdue_by_stage=overdue_stage,
                overdue_matrix_by_team_salesperson_stage=matrix,
            ),
        )
        set_cached("crm:summary", result)
        return result

    async def followup_risk_response(self) -> FollowupRiskResponse:
        cached = get_cached("crm:followup_risk")
        if cached is not None:
            logger.debug("Cache hit: crm:followup_risk")
            return cast(FollowupRiskResponse, cached)

        overdue_sp, overdue_team, overdue_stage, matrix = await asyncio.gather(
            self.overdue_by_salesperson(),
            self.overdue_by_team(),
            self.overdue_by_stage(),
            self.overdue_matrix(),
        )

        result = FollowupRiskResponse(
            mode=_MODE,
            scope=_SCOPE,
            followup_risk=FollowupRisk(
                overdue_by_salesperson=overdue_sp,
                overdue_by_team=overdue_team,
                overdue_by_stage=overdue_stage,
                overdue_matrix_by_team_salesperson_stage=matrix,
            ),
        )
        set_cached("crm:followup_risk", result)
        return result

    async def missing_contact_response(
        self,
        page: int = 1,
        page_size: int = 50,
        team_id: Optional[int] = None,
        salesperson_id: Optional[int] = None,
        sort: str = "create_date desc",
    ) -> PaginatedMissingContactResponse:
        rows, total = await self.missing_contact_details(
            page=page,
            page_size=page_size,
            team_id=team_id,
            salesperson_id=salesperson_id,
            sort=sort,
        )
        return self._paginated_dq_response(rows, total, page, page_size)

    async def missing_stage_response(
        self,
        page: int = 1,
        page_size: int = 50,
        team_id: Optional[int] = None,
        salesperson_id: Optional[int] = None,
        sort: str = "create_date desc",
    ) -> PaginatedMissingContactResponse:
        rows, total = await self.missing_stage_details(
            page=page,
            page_size=page_size,
            team_id=team_id,
            salesperson_id=salesperson_id,
            sort=sort,
        )
        return self._paginated_dq_response(rows, total, page, page_size)

    async def missing_salesperson_response(
        self,
        page: int = 1,
        page_size: int = 50,
        team_id: Optional[int] = None,
        salesperson_id: Optional[int] = None,
        sort: str = "create_date desc",
    ) -> PaginatedMissingContactResponse:
        rows, total = await self.missing_salesperson_details(
            page=page,
            page_size=page_size,
            team_id=team_id,
            salesperson_id=salesperson_id,
            sort=sort,
        )
        return self._paginated_dq_response(rows, total, page, page_size)

    @staticmethod
    def _paginated_dq_response(
        rows: list[MissingContactRow],
        total: int,
        page: int,
        page_size: int,
    ) -> PaginatedMissingContactResponse:
        total_pages = math.ceil(total / page_size) if page_size > 0 else 1
        return PaginatedMissingContactResponse(
            ok=True,
            data=rows,
            pagination=Pagination(
                page=page,
                page_size=page_size,
                total=total,
                total_pages=total_pages,
                has_next=page < total_pages,
                has_prev=page > 1,
            ),
        )
