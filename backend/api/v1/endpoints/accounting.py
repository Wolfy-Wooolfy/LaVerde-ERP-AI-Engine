"""
Accounting endpoints (Module 4 · Phase 1).

GET /api/v1/accounting/balance-sheet — live balance sheet (uncached, M4.3)

Module gate "accounting" is applied at include time in backend/api/v1/router.py;
each endpoint additionally depends on get_current_user (401 unauthenticated).
"""

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from loguru import logger

from backend.api.deps import get_current_user
from backend.core.exceptions import OdooQueryError
from backend.core.limiter import limiter
from backend.modules.accounting.schemas import BalanceSheetResponse
from backend.modules.accounting.services.balance_sheet_service import (
    BalanceSheetIntegrityError,
    get_balance_sheet,
)

router = APIRouter(prefix="/accounting", tags=["accounting"])


@router.get(
    "/balance-sheet",
    summary="Balance sheet — live from posted GL lines (opening-balance phase, uncached)",
    response_model=BalanceSheetResponse,
)
@limiter.limit("60/minute")
async def balance_sheet(
    request: Request,
    response: Response,
    _user: str = Depends(get_current_user),
) -> dict | JSONResponse:
    try:
        data = await get_balance_sheet()
    except BalanceSheetIntegrityError:
        # Data/label-map invariant broken (M4.5) — a 503 would invite retries
        # that cannot succeed; the offending values are in the log.
        logger.error("Balance sheet — integrity check failed", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "An unexpected error occurred.",
                }
            },
        )
    except OdooQueryError:
        logger.warning("Balance sheet — Odoo query failed", exc_info=True)
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "odoo_unavailable",
                    "message": "Odoo is unavailable or the query failed. Try again shortly.",
                }
            },
        )
    except Exception:
        logger.error("Balance sheet — unexpected error", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "An unexpected error occurred.",
                }
            },
        )

    # Deliberately uncached (M4.3): figures are edited in place by finance —
    # the browser must never serve a stale sheet during verification. No
    # X-Cache-Status header either: nothing is ever cached on this route.
    response.headers["Cache-Control"] = "no-store"
    return data
