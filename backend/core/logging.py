import os
import sys

from loguru import logger

from backend.core.config import settings


def setup_logging() -> None:
    """Configure Loguru sinks based on the current environment."""
    logger.remove()

    if settings.ENVIRONMENT == "production":
        logger.add(sys.stdout, level=settings.LOG_LEVEL, serialize=True)
    else:
        logger.add(
            sys.stdout,
            format=(
                "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
                "<level>{level: <8}</level> | "
                "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> — "
                "<level>{message}</level>"
            ),
            level=settings.LOG_LEVEL,
            colorize=True,
        )

    os.makedirs("logs", exist_ok=True)
    logger.add(
        "logs/app.log",
        rotation="10 MB",
        retention="7 days",
        level=settings.LOG_LEVEL,
        encoding="utf-8",
    )
