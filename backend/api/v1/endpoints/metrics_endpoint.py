from fastapi import APIRouter, Depends, Request

from backend.api.deps import get_current_user
from backend.core.config import settings
from backend.core.limiter import limiter
from backend.core.metrics import metrics

router = APIRouter(tags=["observability"])


@router.get("/metrics", summary="In-memory performance metrics")
@limiter.limit("120/minute")
async def get_metrics(
    request: Request,
    user: str = Depends(get_current_user),
) -> dict:
    snapshot = metrics.snapshot()

    # Attach AI metrics if the service is running
    if settings.AI_ENABLED:
        budget_tracker = getattr(request.app.state, "ai_budget_tracker", None)
        ai_cache = getattr(request.app.state, "ai_cache", None)

        budget_status = budget_tracker.get_status() if budget_tracker else {}
        cache_stats = ai_cache.stats() if ai_cache else {}

        snapshot["ai"] = {
            "total_requests": cache_stats.get("hits", 0) + cache_stats.get("misses", 0),
            "cache_hits": cache_stats.get("hits", 0),
            "cache_misses": cache_stats.get("misses", 0),
            "cache_hit_rate": cache_stats.get("hit_rate", 0.0),
            "total_cost_usd": budget_status.get("current_month_spend_usd", 0.0),
            "monthly_budget_usd": settings.AI_MONTHLY_BUDGET_USD,
            "budget_used_pct": budget_status.get("percentage_used", 0.0),
            "by_model": {settings.AI_MODEL: {"calls": cache_stats.get("misses", 0), "cost_usd": budget_status.get("current_month_spend_usd", 0.0)}},
        }
    else:
        snapshot["ai"] = {"enabled": False}

    return snapshot
