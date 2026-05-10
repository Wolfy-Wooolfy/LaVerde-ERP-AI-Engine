from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

from backend.api.deps import get_crm_service, get_current_user
from backend.core.limiter import limiter
from backend.modules.crm.schemas import PaginatedMissingContactResponse
from backend.modules.crm.service import CrmService

router = APIRouter(tags=["crm"])

_VALID_SORT_FIELDS = {"create_date", "name", "user_id", "team_id", "stage_id"}


@router.get(
    "/data-quality/missing-contact",
    response_model=PaginatedMissingContactResponse,
    summary="Opportunities missing all contact phone fields (paginated)",
)
@limiter.limit("30/minute")
async def crm_missing_contact(
    request: Request,
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=200, description="Results per page"),
    team_id: Optional[int] = Query(None, description="Filter by team ID"),
    salesperson_id: Optional[int] = Query(None, description="Filter by salesperson ID"),
    sort: str = Query("create_date desc", description="Sort field and direction"),
    user: str = Depends(get_current_user),
    service: CrmService = Depends(get_crm_service),
) -> PaginatedMissingContactResponse:
    # Validate sort field to prevent injection
    sort_field = sort.split()[0] if sort else "create_date"
    if sort_field not in _VALID_SORT_FIELDS:
        sort = "create_date desc"
    return await service.missing_contact_response(
        page=page,
        page_size=page_size,
        team_id=team_id,
        salesperson_id=salesperson_id,
        sort=sort,
    )
