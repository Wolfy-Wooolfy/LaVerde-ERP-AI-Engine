"""Budget tracker — enforces monthly AI spend hard stop, persisted to disk."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from backend.shared.ai.exceptions import BudgetExceededError

# Cost per million tokens (USD)
PRICING: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4-turbo": {"input": 10.00, "output": 30.00},
}

_BUDGET_FILE = Path("logs/ai_budget.json")


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return USD cost for a call given token counts."""
    pricing = PRICING.get(model, PRICING["gpt-4o-mini"])
    return (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000


class BudgetTracker:
    """Track AI spend per calendar month with hard stop enforcement."""

    def __init__(
        self,
        monthly_budget_usd: float,
        warning_threshold: float,
        hard_stop: bool = True,
        budget_file: Path = _BUDGET_FILE,
    ) -> None:
        self.monthly_budget = monthly_budget_usd
        self.warning_threshold = warning_threshold
        self.hard_stop = hard_stop
        self._budget_file = budget_file
        self._lock = threading.Lock()
        self._spend_by_month: dict[str, float] = {}
        self._load()

    # ── Persistence ────────────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            if self._budget_file.exists():
                data = json.loads(self._budget_file.read_text(encoding="utf-8"))
                self._spend_by_month = {k: float(v) for k, v in data.items()}
                logger.debug(f"Budget loaded from {self._budget_file}: {self._spend_by_month}")
        except Exception as exc:
            logger.warning(f"Could not load AI budget file: {exc} — starting fresh")
            self._spend_by_month = {}

    def _save(self) -> None:
        try:
            self._budget_file.parent.mkdir(parents=True, exist_ok=True)
            self._budget_file.write_text(
                json.dumps(self._spend_by_month, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            logger.error(f"Could not persist AI budget: {exc}")

    # ── Public API ─────────────────────────────────────────────────────────────

    @staticmethod
    def _current_month_key() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m")

    def record_spend(self, cost_usd: float, model: str) -> None:
        key = self._current_month_key()
        with self._lock:
            self._spend_by_month[key] = self._spend_by_month.get(key, 0.0) + cost_usd
            self._save()
        logger.debug(f"AI spend recorded: ${cost_usd:.6f} ({model}), month total: ${self.current_month_spend():.6f}")

    def current_month_spend(self) -> float:
        key = self._current_month_key()
        with self._lock:
            return self._spend_by_month.get(key, 0.0)

    def remaining_budget(self) -> float:
        return max(0.0, self.monthly_budget - self.current_month_spend())

    def is_over_budget(self) -> bool:
        return self.current_month_spend() >= self.monthly_budget

    def is_near_budget(self) -> bool:
        return self.current_month_spend() >= self.warning_threshold * self.monthly_budget

    def enforce_budget(self) -> None:
        """Raise BudgetExceededError if hard stop is enabled and over budget."""
        if self.hard_stop and self.is_over_budget():
            spent = self.current_month_spend()
            raise BudgetExceededError(spent=spent, budget=self.monthly_budget)

    def get_status(self) -> dict:
        spent = self.current_month_spend()
        pct = (spent / self.monthly_budget * 100) if self.monthly_budget > 0 else 0.0
        return {
            "current_month_spend_usd": round(spent, 6),
            "monthly_budget_usd": self.monthly_budget,
            "remaining_budget_usd": round(self.remaining_budget(), 6),
            "percentage_used": round(pct, 2),
            "is_near_budget": self.is_near_budget(),
            "is_over_budget": self.is_over_budget(),
            "current_month": self._current_month_key(),
        }
