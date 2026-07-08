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
    """Second call is served from cache: it makes zero additional backing calls.

    Verified deterministically via the backing-client call count (miss-then-hit),
    not wall-clock timing. The first summary() is a cache MISS that invokes the
    backing client; the second is a cache HIT that must invoke it zero more times
    and return the identical cached object.
    """
    # _slow_client()'s execute_kw is a plain async function (no MagicMock/AsyncMock
    # call tracking), so wrap it locally with a counter — leaves _slow_client()
    # untouched for the other tests that share it.
    client = _slow_client()
    backing = client.execute_kw
    call_count = 0

    async def counting_execute(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        return await backing(*args, **kwargs)

    client.execute_kw = counting_execute
    svc = CrmService(client=client)

    first = await svc.summary()  # cache MISS — invokes the backing client
    assert call_count > 0, "warmup call should hit the backing client on a cache miss"

    calls_after_warmup = call_count
    second = await svc.summary()  # cache HIT — must not touch the backing client

    assert call_count == calls_after_warmup, (
        f"cached call made {call_count - calls_after_warmup} extra backing call(s) — "
        "expected 0 (should be served entirely from cache)"
    )
    assert second == first, "cached call should return the same object as the first call"
