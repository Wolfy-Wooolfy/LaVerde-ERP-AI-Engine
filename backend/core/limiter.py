"""Shared slowapi Limiter instance — imported by main.py and endpoints."""

from slowapi import Limiter
from slowapi.util import get_remote_address

from backend.core.config import settings

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[settings.RATE_LIMIT_DEFAULT],
)
