"""
Loguru multi-sink configuration.
Sinks: stdout (console), app.log, errors.log, odoo.log.
Audit sink is handled separately in shared/audit.py.
"""

import os
import sys

from loguru import logger

from backend.core.config import settings

_PRETTY_FMT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan>"
    " [{extra[request_id]}] — <level>{message}</level>"
)

_ROTATION = "10 MB"
_RETENTION = 5


def setup_logging() -> None:
    """Configure all Loguru sinks based on environment."""
    logger.remove()

    # ── Console sink ──────────────────────────────────────────────────────────
    if settings.ENVIRONMENT == "production":
        logger.add(
            sys.stdout,
            level=settings.LOG_LEVEL,
            serialize=True,
            filter=lambda r: not r["extra"].get("audit"),
        )
    else:
        logger.add(
            sys.stdout,
            format=_PRETTY_FMT,
            level=settings.LOG_LEVEL,
            colorize=True,
            filter=lambda r: not r["extra"].get("audit"),
        )

    os.makedirs("logs", exist_ok=True)

    # ── General app log ───────────────────────────────────────────────────────
    logger.add(
        "logs/app.log",
        rotation=_ROTATION,
        retention=_RETENTION,
        level=settings.LOG_LEVEL,
        encoding="utf-8",
        filter=lambda r: not r["extra"].get("audit"),
    )

    # ── Errors only ───────────────────────────────────────────────────────────
    logger.add(
        "logs/errors.log",
        rotation=_ROTATION,
        retention=_RETENTION,
        level="ERROR",
        encoding="utf-8",
        filter=lambda r: not r["extra"].get("audit"),
    )

    # ── Odoo RPC calls (DEBUG level so they only appear when LOG_LEVEL=DEBUG) ─
    logger.add(
        "logs/odoo.log",
        rotation=_ROTATION,
        retention=_RETENTION,
        level="DEBUG",
        encoding="utf-8",
        filter=lambda r: (
            not r["extra"].get("audit")
            and ("Odoo" in r["message"] or "odoo" in r["message"].lower())
        ),
    )

    logger.configure(extra={"request_id": "-"})
