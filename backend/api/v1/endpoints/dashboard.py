from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from backend.api.deps import get_crm_service, get_current_user
from backend.modules.crm.service import CrmService

router = APIRouter(tags=["ui"])
templates = Jinja2Templates(directory="frontend/templates")


@router.get("/dashboard", response_class=HTMLResponse, summary="CRM dashboard (HTML)")
def dashboard(
    request: Request,
    user: str = Depends(get_current_user),
    service: CrmService = Depends(get_crm_service),
) -> HTMLResponse:
    data = service.summary()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "current_user": user,
            "mode": data.mode,
            "scope": data.scope,
            "summary": data.summary,
            "data_quality": data.data_quality,
            "followup_risk": data.followup_risk,
        },
    )


@router.get(
    "/data-quality/missing-contact",
    response_class=HTMLResponse,
    summary="Missing contact details page (HTML)",
)
def missing_contact_page(
    request: Request,
    user: str = Depends(get_current_user),
    service: CrmService = Depends(get_crm_service),
) -> HTMLResponse:
    data = service.missing_contact_response()
    return templates.TemplateResponse(
        request,
        "missing_contact.html",
        {
            "current_user": user,
            "mode": data.mode,
            "scope": data.scope,
            "rows": data.missing_contact_details,
        },
    )
