from fastapi import APIRouter, Depends
from loguru import logger

from backend.api.deps import get_crm_service, get_current_user
from backend.core.config import settings
from backend.core.exceptions import OdooAuthenticationError, OdooConnectionError
from backend.modules.crm.service import CrmService

router = APIRouter(tags=["health"])


@router.get("/health", summary="Service health (authenticated)")
def health_check(user: str = Depends(get_current_user)) -> dict:
    return {
        "status": "ok",
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
    }


@router.get("/health/odoo", summary="Odoo connectivity check")
def health_odoo(
    user: str = Depends(get_current_user),
    service: CrmService = Depends(get_crm_service),
) -> dict:
    """Attempts a lightweight Odoo query to confirm the connection is alive."""
    try:
        service.client.authenticate()
        return {"status": "ok", "odoo": "reachable"}
    except OdooAuthenticationError as exc:
        logger.error(f"Odoo auth failed during health check: {exc}")
        return {"status": "degraded", "odoo": "auth_failed", "detail": str(exc)}
    except OdooConnectionError as exc:
        logger.error(f"Odoo unreachable during health check: {exc}")
        return {"status": "degraded", "odoo": "unreachable", "detail": str(exc)}
    except Exception as exc:
        logger.error(f"Unexpected error during Odoo health check: {exc}")
        return {"status": "degraded", "odoo": "error"}
