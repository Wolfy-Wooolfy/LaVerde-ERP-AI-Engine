from fastapi import APIRouter

from backend.api.v1.endpoints import data_quality, followup, health, summary
from backend.api.v1.endpoints.ai import router as ai_router
from backend.api.v1.endpoints.chat import router as chat_router
from backend.api.v1.endpoints.collections import router as collections_router
from backend.api.v1.endpoints.customer_accounts import router as customer_accounts_router
from backend.api.v1.endpoints.dashboard_api import router as dashboard_api_router
from backend.api.v1.endpoints.hr import router as hr_router
from backend.api.v1.endpoints.metrics_endpoint import router as metrics_router

# All routes under /api/v1/
api_v1_router = APIRouter()

api_v1_router.include_router(health.router)
api_v1_router.include_router(summary.router)
api_v1_router.include_router(followup.router)
api_v1_router.include_router(data_quality.router)
api_v1_router.include_router(metrics_router)
api_v1_router.include_router(dashboard_api_router)
api_v1_router.include_router(ai_router)
api_v1_router.include_router(chat_router)
api_v1_router.include_router(collections_router)
api_v1_router.include_router(customer_accounts_router)
api_v1_router.include_router(hr_router)
