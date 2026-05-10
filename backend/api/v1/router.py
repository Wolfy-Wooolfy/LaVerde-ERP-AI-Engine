from fastapi import APIRouter

from backend.api.v1.endpoints import data_quality, followup, health, summary
from backend.api.v1.endpoints.metrics_endpoint import router as metrics_router

# All routes under /api/v1/
api_v1_router = APIRouter()

api_v1_router.include_router(health.router)
api_v1_router.include_router(summary.router)
api_v1_router.include_router(followup.router)
api_v1_router.include_router(data_quality.router)
api_v1_router.include_router(metrics_router)
