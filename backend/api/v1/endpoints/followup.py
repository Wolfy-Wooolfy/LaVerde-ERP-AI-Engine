from fastapi import APIRouter, Depends, Request

from backend.api.deps import get_crm_service, get_current_user
from backend.core.limiter import limiter
from backend.modules.crm.schemas import FollowupRiskResponse
from backend.modules.crm.service import CrmService

router = APIRouter(tags=["crm"])


@router.get(
    "/followup-risk",
    response_model=FollowupRiskResponse,
    summary="Follow-up risk breakdown by salesperson / team / stage",
)
@limiter.limit("30/minute")
async def crm_followup_risk(
    request: Request,
    user: str = Depends(get_current_user),
    service: CrmService = Depends(get_crm_service),
) -> FollowupRiskResponse:
    return await service.followup_risk_response()
