from fastapi import APIRouter, Depends

from backend.api.deps import require_admin_api, require_module_api
from backend.api.v1.endpoints import data_quality, followup, health, summary
from backend.api.v1.endpoints.ai import router as ai_router
from backend.api.v1.endpoints.chat import router as chat_router
from backend.api.v1.endpoints.campaign_performance import router as campaign_performance_router
from backend.api.v1.endpoints.collections import router as collections_router
from backend.api.v1.endpoints.customer_accounts import router as customer_accounts_router
from backend.api.v1.endpoints.dashboard_api import router as dashboard_api_router
from backend.api.v1.endpoints.hr import router as hr_router
from backend.api.v1.endpoints.marketing_attribution import router as marketing_attribution_router
from backend.api.v1.endpoints.metrics_endpoint import router as metrics_router
from backend.api.v1.endpoints.settings import router as settings_router

api_v1_router = APIRouter()

# ── Not module-gated (authenticated-only) ────────────────────────────────────
api_v1_router.include_router(health.router)
api_v1_router.include_router(metrics_router)

# ── crm — 6 endpoint files ────────────────────────────────────────────────────
_crm = [Depends(require_module_api("crm"))]
api_v1_router.include_router(summary.router,       dependencies=_crm)
api_v1_router.include_router(followup.router,      dependencies=_crm)
api_v1_router.include_router(data_quality.router,  dependencies=_crm)
api_v1_router.include_router(dashboard_api_router, dependencies=_crm)
api_v1_router.include_router(ai_router,            dependencies=_crm)
api_v1_router.include_router(chat_router,          dependencies=_crm)

# ── collections ───────────────────────────────────────────────────────────────
api_v1_router.include_router(
    collections_router,
    dependencies=[Depends(require_module_api("collections"))],
)

# ── customer_accounts ─────────────────────────────────────────────────────────
api_v1_router.include_router(
    customer_accounts_router,
    dependencies=[Depends(require_module_api("customer_accounts"))],
)

# ── hr ────────────────────────────────────────────────────────────────────────
api_v1_router.include_router(
    hr_router,
    dependencies=[Depends(require_module_api("hr"))],
)

# ── marketing_attribution ─────────────────────────────────────────────────────
api_v1_router.include_router(
    marketing_attribution_router,
    dependencies=[Depends(require_module_api("marketing_attribution"))],
)

# ── campaign_performance ──────────────────────────────────────────────────────
api_v1_router.include_router(
    campaign_performance_router,
    dependencies=[Depends(require_module_api("campaign_performance"))],
)

# ── settings (admin-only) ─────────────────────────────────────────────────────
_admin = [Depends(require_admin_api)]
api_v1_router.include_router(
    settings_router,
    prefix="/settings",
    dependencies=_admin,
)
