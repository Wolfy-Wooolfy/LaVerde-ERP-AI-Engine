"""
In-memory metrics store.
Thread-safe counters for Odoo calls, cache, and HTTP requests.
No external dependency — swap for Prometheus later if needed.
"""

import threading
import time
from collections import defaultdict
from typing import Any


class _Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._start_time: float = time.monotonic()

        # Odoo
        self.odoo_calls_total: int = 0
        self.odoo_errors_total: int = 0
        self.odoo_duration_total_ms: float = 0.0
        self.odoo_by_method: dict[str, int] = defaultdict(int)

        # Cache
        self.cache_hits: int = 0
        self.cache_misses: int = 0

        # API
        self.api_requests_total: int = 0
        self.api_duration_total_ms: float = 0.0
        self.api_errors_4xx: int = 0
        self.api_errors_5xx: int = 0

    # ── Odoo ──────────────────────────────────────────────────────────────────

    def record_odoo_call(self, method: str, duration_ms: float, error: bool = False) -> None:
        with self._lock:
            self.odoo_calls_total += 1
            self.odoo_duration_total_ms += duration_ms
            self.odoo_by_method[method] += 1
            if error:
                self.odoo_errors_total += 1

    # ── Cache ─────────────────────────────────────────────────────────────────

    def record_cache_hit(self) -> None:
        with self._lock:
            self.cache_hits += 1

    def record_cache_miss(self) -> None:
        with self._lock:
            self.cache_misses += 1

    # ── API ───────────────────────────────────────────────────────────────────

    def record_api_request(self, duration_ms: float, status_code: int) -> None:
        with self._lock:
            self.api_requests_total += 1
            self.api_duration_total_ms += duration_ms
            if 400 <= status_code < 500:
                self.api_errors_4xx += 1
            elif status_code >= 500:
                self.api_errors_5xx += 1

    # ── Snapshot ──────────────────────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            uptime = time.monotonic() - self._start_time
            odoo_avg = (
                self.odoo_duration_total_ms / self.odoo_calls_total
                if self.odoo_calls_total
                else 0.0
            )
            api_avg = (
                self.api_duration_total_ms / self.api_requests_total
                if self.api_requests_total
                else 0.0
            )
            total_cache = self.cache_hits + self.cache_misses
            hit_rate = self.cache_hits / total_cache if total_cache else 0.0

            return {
                "uptime_seconds": round(uptime, 2),
                "odoo": {
                    "total_calls": self.odoo_calls_total,
                    "avg_duration_ms": round(odoo_avg, 2),
                    "errors": self.odoo_errors_total,
                    "by_method": dict(self.odoo_by_method),
                },
                "cache": {
                    "hits": self.cache_hits,
                    "misses": self.cache_misses,
                    "hit_rate": round(hit_rate, 4),
                },
                "api": {
                    "total_requests": self.api_requests_total,
                    "avg_duration_ms": round(api_avg, 2),
                    "errors_4xx": self.api_errors_4xx,
                    "errors_5xx": self.api_errors_5xx,
                },
            }

    def reset(self) -> None:
        """Reset all counters (useful in tests)."""
        with self._lock:
            self.__init__()  # type: ignore[misc]


metrics = _Metrics()

# ── App uptime helper ─────────────────────────────────────────────────────────

_app_start_time: float = 0.0


def set_start_time() -> None:
    global _app_start_time
    _app_start_time = time.monotonic()


def get_uptime() -> float:
    return round(time.monotonic() - _app_start_time, 2)
