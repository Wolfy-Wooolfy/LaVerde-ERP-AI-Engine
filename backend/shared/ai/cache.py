"""AI-specific two-tier cache: in-memory TTLCache + JSON disk backup."""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from cachetools import TTLCache
from loguru import logger

_CACHE_FILE = Path("logs/ai_cache.json")
_METRICS_FILE = Path("logs/ai_cache_metrics.json")

CACHE_SCHEMA_VERSION = 2  # bump whenever key format or value structure changes


class AICache:
    """Thread-safe TTL cache with JSON persistence across restarts."""

    def __init__(self, ttl_seconds: int = 21600, maxsize: int = 500, cache_file: Path = _CACHE_FILE) -> None:
        self._ttl = ttl_seconds
        self._cache: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl_seconds)
        self._lock = threading.Lock()
        self._cache_file = cache_file
        self._hits = 0
        self._misses = 0
        self._load()

    # ── Persistence ────────────────────────────────────────────────────────────

    def _load(self) -> None:
        if not self._cache_file.exists():
            return

        try:
            data = json.loads(self._cache_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(f"AI cache file corrupted, starting fresh: {exc}")
            self._cache_file.unlink(missing_ok=True)
            return

        if not isinstance(data, dict) or "schema_version" not in data:
            logger.info("AI cache schema is outdated (no version), clearing.")
            self._cache_file.unlink(missing_ok=True)
            return

        if data["schema_version"] != CACHE_SCHEMA_VERSION:
            logger.info(
                f"AI cache schema mismatch "
                f"(found v{data['schema_version']}, want v{CACHE_SCHEMA_VERSION}), "
                f"clearing."
            )
            self._cache_file.unlink(missing_ok=True)
            return

        # Lazy import avoids circular dependency at module level
        from backend.modules.crm.ai.schemas import LeadPriority  # noqa: PLC0415

        now = datetime.now(timezone.utc).timestamp()
        loaded = 0
        for key, entry in data.get("entries", {}).items():
            try:
                if entry.get("expires_at", 0) <= now:
                    continue
                value = LeadPriority.model_validate(entry["payload"])
                with self._lock:
                    self._cache[key] = value
                loaded += 1
            except Exception as exc:
                logger.warning(f"Skipping invalid cache entry {key}: {exc}")

        logger.debug(f"AI cache loaded {loaded} entries from disk")

    def _save(self) -> None:
        try:
            self._cache_file.parent.mkdir(parents=True, exist_ok=True)
            now = datetime.now(timezone.utc).timestamp()
            entries: dict[str, Any] = {}
            with self._lock:
                for key, value in self._cache.items():
                    payload = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
                    entries[key] = {
                        "expires_at": now + self._ttl,
                        "payload": payload,
                    }
            self._cache_file.write_text(
                json.dumps(
                    {"schema_version": CACHE_SCHEMA_VERSION, "entries": entries},
                    default=str,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.error(f"Could not persist AI cache: {exc}")

    # ── Public API ─────────────────────────────────────────────────────────────

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            value = self._cache.get(key)
        if value is not None:
            self._hits += 1
            return value
        self._misses += 1
        return None

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._cache[key] = value
        self._save()

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._cache.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    @property
    def hits(self) -> int:
        return self._hits

    @property
    def misses(self) -> int:
        return self._misses

    @property
    def hit_rate(self) -> float:
        total = self._hits + self._misses
        return round(self._hits / total, 4) if total > 0 else 0.0

    def stats(self) -> dict:
        return {
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self.hit_rate,
            "size": len(self._cache),
            "maxsize": self._cache.maxsize,
        }


# ── Cache key builders ────────────────────────────────────────────────────────


def lead_cache_key(
    lead_id: int,
    stage_id: int,
    last_activity_date: Any,
    completeness_score: int,
    chatter_hash: str = "",
    locale: str = "en",
) -> str:
    raw = f"{lead_id}:{stage_id}:{last_activity_date}:{completeness_score}:{chatter_hash}:{locale}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def overdue_list_cache_key(limit: int, locale: str = "en") -> str:
    return f"overdue_priority_top_{limit}_{locale}"


# ── Intent cache for chat Stage 1 ─────────────────────────────────────────────


class IntentCache:
    """Cache parsed chat intents (Stage 1) — 1 hour TTL, in-memory only."""

    def __init__(self) -> None:
        self._cache: TTLCache = TTLCache(maxsize=500, ttl=3600)
        self._lock = threading.Lock()

    def get(self, question: str, locale: str) -> Optional[Any]:
        key = self._make_key(question, locale)
        with self._lock:
            return self._cache.get(key)

    def set(self, question: str, locale: str, intent: Any) -> None:
        key = self._make_key(question, locale)
        with self._lock:
            self._cache[key] = intent

    @staticmethod
    def _make_key(question: str, locale: str) -> str:
        raw = f"{locale}:{question.strip().lower()}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]
