"""
Performance tests for CrmService.
Verifies that summary() completes within acceptable time bounds using mock Odoo.
"""

import asyncio
import time
from unittest.mock import MagicMock

import pytest

from backend.core.cache import clear_cache, init_cache
from backend.modules.crm.service import CrmService

# Each "Odoo call" takes 50ms in mock — simulates real-world latency
_MOCK_LATENCY_SEC = 0.05


@pytest.fixture(autouse=True)
def fresh_cache() -> None:
    init_cache(ttl=60)
    clear_cache()


def _slow_client(latency: float = _MOCK_LATENCY_SEC) -> MagicMock:
    async def fake_execute(*args, **kwargs) -> list:  # type: ignore[type-arg]
        await asyncio.sleep(latency)
        return [{"__count": 10}]

    client = MagicMock()
    client.execute_kw = fake_execute
    return client


@pytest.mark.benchmark(group="summary")
async def test_summary_completes_within_1500ms(benchmark: pytest.fixture) -> None:  # type: ignore[type-arg]
    """
    Sequential: 11 calls × 50ms = 550ms
    Parallel (asyncio.gather): ~50ms (all concurrent)
    Threshold: 1500ms (generous for CI)
    """
    svc = CrmService(client=_slow_client())

    start = time.monotonic()
    await svc.summary()
    elapsed_ms = (time.monotonic() - start) * 1000

    assert elapsed_ms < 1500, f"summary() took {elapsed_ms:.0f}ms — expected <1500ms"


async def test_summary_parallel_speedup_vs_sequential() -> None:
    """
    Demonstrate that parallel gather is significantly faster than sequential.
    With 11 calls at 50ms each, sequential = ~550ms, parallel = ~50ms.
    """
    svc = CrmService(client=_slow_client(latency=0.05))

    start = time.monotonic()
    await svc.summary()
    elapsed_parallel = time.monotonic() - start

    # Sequential baseline would be at least 8 calls × 50ms = 400ms
    sequential_baseline = 8 * _MOCK_LATENCY_SEC
    assert elapsed_parallel < sequential_baseline, (
        f"Parallel ({elapsed_parallel:.3f}s) should be faster than sequential "
        f"({sequential_baseline:.3f}s)"
    )


async def test_cached_summary_is_instant() -> None:
    """Second call hits cache and returns in <10ms."""
    svc = CrmService(client=_slow_client())

    await svc.summary()  # warm cache

    start = time.monotonic()
    await svc.summary()  # from cache
    elapsed_ms = (time.monotonic() - start) * 1000

    assert elapsed_ms < 10, f"Cached summary took {elapsed_ms:.1f}ms — expected <10ms"
