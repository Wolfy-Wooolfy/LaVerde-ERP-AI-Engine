from fastapi import APIRouter, Depends

from backend.api.deps import get_crm_service, get_current_user
from backend.modules.crm.schemas import FollowupRiskResponse
from backend.modules.crm.service import CrmService

router = APIRouter(tags=["crm"])


@router.get(
    "/followup-risk",
    response_model=FollowupRiskResponse,
    summary="Follow-up risk breakdown by salesperson / team / stage",
)
def crm_followup_risk(
    user: str = Depends(get_current_user),
    service: CrmService = Depends(get_crm_service),
) -> FollowupRiskResponse:
    return service.followup_risk_response()
