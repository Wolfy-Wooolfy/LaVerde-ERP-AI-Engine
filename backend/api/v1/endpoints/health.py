import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, Response
from loguru import logger

from backend.api.deps import get_crm_service, get_current_user
from backend.core.config import settings
from backend.core.exceptions import OdooAuthenticationError, OdooConnectionError
from backend.core.limiter import limiter
from backend.core.metrics import get_uptime
from backend.modules.crm.service import CrmService

router = APIRouter(tags=["health"])


@router.get("/health", summary="Service health (authenticated)")
@limiter.limit("600/minute")
async def health_check(
    request: Request,
    user: str = Depends(get_current_user),
) -> dict:
    from backend.core.cache import _cache, _lock

    with _lock:
        cache_size = len(_cache)
        cache_max = _cache.maxsize

    return {
        "status": "ok",
        "uptime_seconds": get_uptime(),
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "components": {
            "cache": {
                "status": "ok",
                "size": cache_size,
                "max_size": cache_max,
            },
        },
    }


@router.get("/health/odoo", summary="Odoo connectivity check")
@limiter.limit("600/minute")
async def health_odoo(
    request: Request,
    user: str = Depends(get_current_user),
    service: CrmService = Depends(get_crm_service),
) -> dict:
    start = time.monotonic()
    try:
        await service.client.authenticate()
        response_ms = int((time.monotonic() - start) * 1000)
        return {
            "status": "ok",
            "odoo_url": settings.ODOO_URL,
            "odoo_db": settings.ODOO_DB,
            "auth_valid": True,
            "response_time_ms": response_ms,
            "last_successful_call": datetime.now(timezone.utc).isoformat(),
        }
    except OdooAuthenticationError as exc:
        logger.error(f"Odoo auth failed during health check: {exc}")
        return {"status": "degraded", "odoo_url": settings.ODOO_URL, "auth_valid": False}
    except OdooConnectionError as exc:
        logger.error(f"Odoo unreachable during health check: {exc}")
        return {"status": "degraded", "odoo_url": settings.ODOO_URL, "auth_valid": False}
    except Exception as exc:
        logger.error(f"Unexpected error during Odoo health check: {exc}")
        return {"status": "degraded", "odoo_url": settings.ODOO_URL, "auth_valid": False}


@router.get("/health/deep", summary="Full deep health check (200 ok / 503 degraded)")
@limiter.limit("60/minute")
async def health_deep(
    request: Request,
    response: Response,
    user: str = Depends(get_current_user),
    service: CrmService = Depends(get_crm_service),
) -> dict:
    """Full health check: Odoo connectivity, cache, and latency probe."""
    issues = []

    # ── Odoo connectivity ─────────────────────────────────────────────────────
    odoo_ok = False
    odoo_ms = 0
    try:
        start = time.monotonic()
        await service.client.authenticate()
        # Light query: fetch 1 lead
        await service.client.execute_kw(
            "crm.lead", "search_read", [[]], {"fields": ["id"], "limit": 1}
        )
        odoo_ms = int((time.monotonic() - start) * 1000)
        odoo_ok = True
    except Exception as exc:
        issues.append(f"odoo: {exc}")
        logger.error(f"Deep health — Odoo check failed: {exc}")

    # ── Cache check ───────────────────────────────────────────────────────────
    from backend.core.cache import _cache, _lock

    with _lock:
        cache_size = len(_cache)

    status = "ok" if not issues else "degraded"
    if not odoo_ok:
        response.status_code = 503

    return {
        "status": status,
        "uptime_seconds": get_uptime(),
        "checks": {
            "odoo": {
                "ok": odoo_ok,
                "latency_ms": odoo_ms,
            },
            "cache": {
                "ok": True,
                "size": cache_size,
            },
        },
        "issues": issues,
    }
