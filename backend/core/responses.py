"""Shared JSON response builders — imported by main.py and settings endpoints."""

from datetime import datetime, timezone

from fastapi import Request
from fastapi.responses import JSONResponse


def error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: dict | None = None,
) -> JSONResponse:
    """Standard error envelope used across all API endpoints."""
    request_id = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=status_code,
        content={
            "ok": False,
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
                "request_id": request_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        },
    )
