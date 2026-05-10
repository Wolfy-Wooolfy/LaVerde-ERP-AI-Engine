"""
Resolves Odoo stage IDs to human-readable names.
Fetches from crm.stage once per TTL (default 1 hour) and caches in-memory.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

from loguru import logger

from backend.core.exceptions import StageResolutionError

if TYPE_CHECKING:
    from backend.modules.crm.client import OdooClient

_CACHE_TTL: float = 3600.0  # 1 hour


class StageResolver:
    """Thread-safe, lazily-loaded stage name cache."""

    def __init__(self, client: OdooClient) -> None:
        self._client = client
        self._cache: dict[int, str] = {}
        self._loaded_at: float = 0.0
        self._lock = threading.Lock()

    def _is_stale(self) -> bool:
        return (time.monotonic() - self._loaded_at) > _CACHE_TTL

    def _refresh(self) -> None:
        try:
            rows = self._client.execute_kw(
                "crm.stage",
                "search_read",
                [[]],
                {"fields": ["id", "name"], "limit": 200},
            )
            self._cache = {int(row["id"]): row["name"] for row in rows}
            self._loaded_at = time.monotonic()
            logger.info(f"StageResolver: loaded {len(self._cache)} stages from Odoo")
        except Exception as exc:
            raise StageResolutionError(f"Failed to fetch stages from Odoo: {exc}") from exc

    def get_name(self, stage_id: int | None) -> str:
        """Return the stage name for a given ID, or a fallback string."""
        if stage_id is None:
            return "No Stage"
        with self._lock:
            if self._is_stale():
                self._refresh()
            return self._cache.get(stage_id, f"Stage {stage_id}")

    def get_all(self) -> dict[int, str]:
        """Return a copy of the full stage map."""
        with self._lock:
            if self._is_stale():
                self._refresh()
            return dict(self._cache)
