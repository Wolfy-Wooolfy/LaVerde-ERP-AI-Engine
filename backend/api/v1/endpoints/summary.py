from fastapi import APIRouter, Depends

from backend.api.deps import get_crm_service, get_current_user
from backend.modules.crm.schemas import SummaryResponse
from backend.modules.crm.service import CrmService

router = APIRouter(tags=["crm"])


@router.get(
    "/summary",
    response_model=SummaryResponse,
    summary="Full CRM summary",
)
def crm_summary(
    user: str = Depends(get_current_user),
    service: CrmService = Depends(get_crm_service),
) -> SummaryResponse:
    return service.summary()
