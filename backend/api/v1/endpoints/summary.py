from fastapi import APIRouter, Depends, Request

from backend.api.deps import get_crm_service, get_current_user
from backend.core.limiter import limiter
from backend.modules.crm.schemas import SummaryResponse
from backend.modules.crm.service import CrmService

router = APIRouter(tags=["crm"])


@router.get(
    "/summary",
    response_model=SummaryResponse,
    summary="Full CRM summary (11 concurrent Odoo calls)",
)
@limiter.limit("30/minute")
async def crm_summary(
    request: Request,
    user: str = Depends(get_current_user),
    service: CrmService = Depends(get_crm_service),
) -> SummaryResponse:
    return await service.summary()
