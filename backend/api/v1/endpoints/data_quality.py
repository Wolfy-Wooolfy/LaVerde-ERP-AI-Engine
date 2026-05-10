from fastapi import APIRouter, Depends

from backend.api.deps import get_crm_service, get_current_user
from backend.modules.crm.schemas import DataQualityMissingContactResponse
from backend.modules.crm.service import CrmService

router = APIRouter(tags=["crm"])


@router.get(
    "/data-quality/missing-contact",
    response_model=DataQualityMissingContactResponse,
    summary="Opportunities missing all contact phone fields",
)
def crm_missing_contact(
    user: str = Depends(get_current_user),
    service: CrmService = Depends(get_crm_service),
) -> DataQualityMissingContactResponse:
    return service.missing_contact_response()
