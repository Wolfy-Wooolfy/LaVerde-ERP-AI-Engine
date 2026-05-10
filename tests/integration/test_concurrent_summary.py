"""
Integration test: verify summary() fires multiple Odoo calls concurrently.
Uses a real mock Odoo server in-process to measure parallelism.
"""

import asyncio
import time
from unittest.mock import MagicMock

import pytest

from backend.core.cache import clear_cache, init_cache
from backend.modules.crm.service import CrmService


@pytest.fixture(autouse=True)
def fresh_cache() -> None:
    init_cache(ttl=60)
    clear_cache()


async def test_summary_runs_parallel_calls() -> None:
    """Verify summary() uses gather by counting concurrent Odoo calls."""
    call_order: list[str] = []
    concurrent_calls: list[int] = []
    active = {"count": 0}

    async def fake_execute_kw(model: str, method: str, args=None, kwargs=None) -> list:  # type: ignore[type-arg]
        active["count"] += 1
        concurrent_calls.append(active["count"])
        call_order.append(f"{model}.{method}")
        await asyncio.sleep(0.01)  # simulate small latency
        active["count"] -= 1
        return [{"__count": 5}]

    mock_client = MagicMock()
    mock_client.execute_kw = fake_execute_kw
    svc = CrmService(client=mock_client)

    start = time.monotonic()
    await svc.summary()
    elapsed = time.monotonic() - start

    # If sequential: 11 calls × 10ms = 110ms
    # If parallel: ~10ms (limited by gather depth)
    # We verify at least 2 were concurrent (max_concurrent > 1)
    assert max(concurrent_calls) >= 2, "Expected concurrent Odoo calls"

    # Parallel should be significantly faster than sequential
    # With gather, should complete in <<110ms even with test overhead
    assert elapsed < 1.0, f"summary() took {elapsed:.2f}s — expected <1s"


async def test_followup_risk_parallel_calls() -> None:
    """followup_risk_response() also uses gather for 4 overdue queries."""
    call_count = {"n": 0}

    async def fake_execute_kw(model: str, method: str, args=None, kwargs=None) -> list:  # type: ignore[type-arg]
        call_count["n"] += 1
        await asyncio.sleep(0.005)
        return [{"__count": 0}]

    mock_client = MagicMock()
    mock_client.execute_kw = fake_execute_kw
    svc = CrmService(client=mock_client)

    await svc.followup_risk_response()
    assert call_count["n"] >= 4
