"""
Unit tests for Collections KPI service — get_late_uncollected.

OdooClient is fully mocked; no live Odoo connection is made.
Live verification is the job of scripts/verify_kpi2_live.py.
"""

import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.core.exceptions import OdooQueryError, ReadOnlyViolationError
from backend.modules.collections.services import cache as _cache
from backend.modules.collections.services.kpi_service import (
    _CACHE_KEY_PREFIX,
    get_late_uncollected,
)

_MOCK_RESPONSE = [{"due_amount": 312_604_879.40, "__count": 1971}]


@pytest.fixture(autouse=True)
def fresh_cache() -> None:
    _cache.clear()
    yield
    _cache.clear()


@pytest.fixture
def mock_client() -> MagicMock:
    client = MagicMock()
    client.execute_kw = AsyncMock(return_value=_MOCK_RESPONSE)
    return client


# ── Test 1 — Domain construction ─────────────────────────────────────────────


async def test_domain_is_exact_candidate_c_three_clause(mock_client: MagicMock) -> None:
    await get_late_uncollected(client=mock_client)

    call = mock_client.execute_kw.call_args
    domain = call.kwargs["args"][0]

    assert domain[0] == ("state", "=", "post")
    assert domain[1] == ("payment_state", "in", ["unpaid", "partial"])
    assert domain[2][0] == "date"
    assert domain[2][1] == "<"

    today_value = domain[2][2]
    # Must be YYYY-MM-DD — no time component, no timezone suffix
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", today_value), (
        f"today clause must be YYYY-MM-DD, got {today_value!r}"
    )


# ── Test 2 — Aggregation method ──────────────────────────────────────────────


async def test_uses_read_group_not_search_read(mock_client: MagicMock) -> None:
    await get_late_uncollected(client=mock_client)

    call = mock_client.execute_kw.call_args
    assert call.args[1] == "read_group", (
        f"Expected read_group, got {call.args[1]!r}. "
        "Aggregation must not use search_read + Python sum."
    )


# ── Test 3 — Return shape ─────────────────────────────────────────────────────


async def test_return_shape_has_all_required_keys(mock_client: MagicMock) -> None:
    result = await get_late_uncollected(client=mock_client)

    expected_keys = {
        "value", "currency", "record_count", "as_of",
        "cache_status", "rpc_duration_ms", "domain",
    }
    assert set(result.keys()) == expected_keys

    assert isinstance(result["value"], float)
    assert result["currency"] == "EGP"
    assert isinstance(result["record_count"], int)
    assert isinstance(result["as_of"], str)
    assert result["cache_status"] in {"fresh", "cached"}
    assert isinstance(result["rpc_duration_ms"], int)
    assert isinstance(result["domain"], list)


async def test_return_values_match_mock_response(mock_client: MagicMock) -> None:
    result = await get_late_uncollected(client=mock_client)

    assert result["value"] == pytest.approx(312_604_879.40)
    assert result["record_count"] == 1971
    assert result["cache_status"] == "fresh"
    assert result["rpc_duration_ms"] >= 0


# ── Test 4 — Cache hit ────────────────────────────────────────────────────────


async def test_second_call_is_served_from_cache(mock_client: MagicMock) -> None:
    result1 = await get_late_uncollected(client=mock_client)
    result2 = await get_late_uncollected(client=mock_client)

    assert mock_client.execute_kw.call_count == 1
    assert result1["cache_status"] == "fresh"
    assert result2["cache_status"] == "cached"
    assert result2["rpc_duration_ms"] == 0
    assert result2["value"] == result1["value"]


# ── Test 5 — Cache key invalidation at midnight ───────────────────────────────


async def test_different_dates_produce_independent_cache_entries(
    mock_client: MagicMock,
) -> None:
    # Simulate a call made on "yesterday"
    with patch(
        "backend.modules.collections.services.cache.today_str",
        return_value="2026-05-15",
    ):
        await get_late_uncollected(client=mock_client)

    # Simulate a call made on "today" (UTC midnight has passed)
    with patch(
        "backend.modules.collections.services.cache.today_str",
        return_value="2026-05-16",
    ):
        await get_late_uncollected(client=mock_client)

    # Both dates produced cache misses — Odoo was queried twice
    assert mock_client.execute_kw.call_count == 2


# ── Test 6 — Odoo RPC failure ─────────────────────────────────────────────────


async def test_rpc_failure_raises_odoo_query_error(mock_client: MagicMock) -> None:
    mock_client.execute_kw.side_effect = RuntimeError("connection refused")

    with pytest.raises(OdooQueryError):
        await get_late_uncollected(client=mock_client)


async def test_rpc_failure_writes_no_cache_entry(mock_client: MagicMock) -> None:
    mock_client.execute_kw.side_effect = RuntimeError("timeout")

    with pytest.raises(OdooQueryError):
        await get_late_uncollected(client=mock_client)

    cache_key = _cache.make_key(_CACHE_KEY_PREFIX)
    assert _cache.get(cache_key) is None, "A failed RPC must not leave a cache entry"


# ── Test 7 — Read-only assertion ──────────────────────────────────────────────


async def test_contaminated_allowed_methods_raises_before_any_rpc(
    monkeypatch: pytest.MonkeyPatch,
    mock_client: MagicMock,
) -> None:
    monkeypatch.setattr(
        "backend.modules.collections.services.kpi_service.ALLOWED_METHODS",
        frozenset({"read_group", "write"}),  # contaminated
    )

    with pytest.raises(ReadOnlyViolationError):
        await get_late_uncollected(client=mock_client)

    mock_client.execute_kw.assert_not_called()


async def test_clean_allowed_methods_does_not_raise(mock_client: MagicMock) -> None:
    # Baseline: the production ALLOWED_METHODS must never trigger the assertion
    result = await get_late_uncollected(client=mock_client)
    assert result["value"] >= 0.0
