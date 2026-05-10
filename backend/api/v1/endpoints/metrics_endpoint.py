from fastapi import APIRouter, Depends, Request

from backend.api.deps import get_current_user
from backend.core.limiter import limiter
from backend.core.metrics import metrics

router = APIRouter(tags=["observability"])


@router.get("/metrics", summary="In-memory performance metrics")
@limiter.limit("120/minute")
async def get_metrics(
    request: Request,
    user: str = Depends(get_current_user),
) -> dict:
    return metrics.snapshot()
