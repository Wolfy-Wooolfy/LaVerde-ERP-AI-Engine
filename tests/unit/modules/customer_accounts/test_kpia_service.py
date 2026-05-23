"""
Unit tests for Customer Accounts KPI service — get_total_customer_receivables (KPI A).

OdooClient is fully mocked; no live Odoo connection is made.
Live verification: scripts/verify_kpia_live.py.

__count confirmed present on rs.installment groupby partner_id:
    M3-S1 discovery output (scripts/discover_module3_phase1_2026-05-23.txt)
    Section 2 B1 top-20 INSTALLMENTS column shows per-partner __count values:
    76, 1, 4, 5, 20, 9, 2, 8, 8, 12 … — all non-zero and correct.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.core.exceptions import OdooQueryError, ReadOnlyViolationError
from backend.modules.customer_accounts.services import cache as _cache
from backend.modules.customer_accounts.services.kpi_service import (
    _CACHE_KEY_PREFIX_KPIA,
    _MODEL,
    get_total_customer_receivables,
)

# Three synthetic partner groups — simulates groupby partner_id read_group rows.
# partner_id returned as [id, display_name] by Odoo ORM (many2one groupby form).
_MOCK_ROWS = [
    {"partner_id": [101, "Customer A"], "due_amount": 1_000_000.0, "__count": 5},
    {"partner_id": [102, "Customer B"], "due_amount":   500_000.0, "__count": 3},
    {"partner_id": [103, "Customer C"], "due_amount":   250_000.0, "__count": 2},
]

_EXPECTED_VALUE          = 1_750_000.0  # sum of due_amounts: 1M + 500K + 250K
_EXPECTED_CUSTOMER_COUNT = 3            # len(rows)
_EXPECTED_RECORD_COUNT   = 10           # sum of __count: 5 + 3 + 2


@pytest.fixture(autouse=True)
def fresh_cache() -> None:
    _cache.clear()
    yield
    _cache.clear()


@pytest.fixture
def mock_client() -> MagicMock:
    client = MagicMock()
    client.execute_kw = AsyncMock(return_value=_MOCK_ROWS)
    return client


# ── Test 1 — Domain construction ─────────────────────────────────────────────


async def test_domain_is_state_post(mock_client: MagicMock) -> None:
    await get_total_customer_receivables(client=mock_client)

    call = mock_client.execute_kw.call_args
    domain = call.kwargs["args"][0]

    assert len(domain) == 1, f"Expected single-clause domain, got {domain!r}"
    assert domain[0] == ("state", "=", "post"), (
        f"Expected ('state','=','post'), got {domain[0]!r}"
    )


# ── Test 2 — read_group with groupby partner_id ───────────────────────────────


async def test_uses_read_group_with_groupby_partner_id(mock_client: MagicMock) -> None:
    await get_total_customer_receivables(client=mock_client)

    call = mock_client.execute_kw.call_args
    assert call.args[1] == "read_group", (
        f"Expected read_group, got {call.args[1]!r}"
    )
    groupby = call.kwargs["args"][2]
    assert groupby == ["partner_id"], (
        f"Expected groupby=['partner_id'], got {groupby!r}"
    )


# ── Test 3 — Return shape ─────────────────────────────────────────────────────


async def test_return_shape_has_all_required_keys(mock_client: MagicMock) -> None:
    result = await get_total_customer_receivables(client=mock_client)

    expected_keys = {
        "value", "customer_count", "record_count",
        "currency", "as_of", "cache_status", "rpc_duration_ms", "domain",
    }
    assert set(result.keys()) == expected_keys
    assert isinstance(result["value"], float)
    assert isinstance(result["customer_count"], int)
    assert isinstance(result["record_count"], int)
    assert result["currency"] == "EGP"
    assert isinstance(result["as_of"], str)
    assert result["cache_status"] in {"fresh", "cached"}
    assert isinstance(result["rpc_duration_ms"], int)
    assert isinstance(result["domain"], list)


# ── Test 4 — Value is sum of partner due_amounts ──────────────────────────────


async def test_value_is_sum_of_partner_due_amounts(mock_client: MagicMock) -> None:
    result = await get_total_customer_receivables(client=mock_client)

    assert result["value"] == pytest.approx(_EXPECTED_VALUE), (
        f"Expected {_EXPECTED_VALUE}, got {result['value']}"
    )
    assert result["cache_status"] == "fresh"
    assert result["rpc_duration_ms"] >= 0


# ── Test 5 — customer_count equals group count ────────────────────────────────


async def test_customer_count_equals_group_count(mock_client: MagicMock) -> None:
    result = await get_total_customer_receivables(client=mock_client)

    assert result["customer_count"] == _EXPECTED_CUSTOMER_COUNT, (
        f"Expected {_EXPECTED_CUSTOMER_COUNT} customers, got {result['customer_count']}"
    )


# ── Test 6 — Second call served from cache ────────────────────────────────────


async def test_second_call_served_from_cache(mock_client: MagicMock) -> None:
    result1 = await get_total_customer_receivables(client=mock_client)
    result2 = await get_total_customer_receivables(client=mock_client)

    assert mock_client.execute_kw.call_count == 1, (
        "execute_kw must be called exactly once; second call must be served from cache"
    )
    assert result1["cache_status"] == "fresh"
    assert result2["cache_status"] == "cached"
    assert result2["rpc_duration_ms"] == 0
    assert result2["value"] == result1["value"]
    assert result2["customer_count"] == result1["customer_count"]


# ── Test 7 — Different dates produce independent cache entries ────────────────


async def test_different_dates_independent_cache_entries(mock_client: MagicMock) -> None:
    with patch(
        "backend.modules.customer_accounts.services.cache.today_str",
        return_value="2026-05-22",
    ):
        await get_total_customer_receivables(client=mock_client)

    with patch(
        "backend.modules.customer_accounts.services.cache.today_str",
        return_value="2026-05-23",
    ):
        await get_total_customer_receivables(client=mock_client)

    assert mock_client.execute_kw.call_count == 2, (
        "Calls on different dates must each hit Odoo (independent cache entries)"
    )


# ── Test 8 — RPC failure raises OdooQueryError ────────────────────────────────


async def test_rpc_failure_raises_odoo_query_error(mock_client: MagicMock) -> None:
    mock_client.execute_kw.side_effect = RuntimeError("connection refused")

    with pytest.raises(OdooQueryError):
        await get_total_customer_receivables(client=mock_client)


# ── Test 9 — RPC failure writes no cache entry ────────────────────────────────


async def test_rpc_failure_writes_no_cache_entry(mock_client: MagicMock) -> None:
    mock_client.execute_kw.side_effect = RuntimeError("timeout")

    with pytest.raises(OdooQueryError):
        await get_total_customer_receivables(client=mock_client)

    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_KPIA)
    assert _cache.get(cache_key) is None, (
        "A failed RPC must not leave a cache entry"
    )


# ── Test 10 — Read-only guard raises before RPC ───────────────────────────────


async def test_read_only_violation_raises_before_rpc(
    monkeypatch: pytest.MonkeyPatch,
    mock_client: MagicMock,
) -> None:
    monkeypatch.setattr(
        "backend.modules.customer_accounts.services.kpi_service.ALLOWED_METHODS",
        frozenset({"read_group", "write"}),
    )

    with pytest.raises(ReadOnlyViolationError):
        await get_total_customer_receivables(client=mock_client)

    mock_client.execute_kw.assert_not_called()


# ── Test 11 — record_count derived from __count, not zero ────────────────────


async def test_record_count_from_groupby_count(mock_client: MagicMock) -> None:
    """Confirms record_count = sum(__count per group), not 0 or len(rows).

    __count confirmed present on rs.installment groupby partner_id:
    M3-S1 B1 output shows per-partner __count values (76, 1, 4, 5, 20 …).
    This test guards against a silent bug where __count is misread as 0,
    which would display '0 installments' next to the 2.63B EGP figure.
    """
    result = await get_total_customer_receivables(client=mock_client)

    assert result["record_count"] == _EXPECTED_RECORD_COUNT, (
        f"Expected record_count={_EXPECTED_RECORD_COUNT} (sum of __count per group: 5+3+2), "
        f"got {result['record_count']}. "
        "If 0: __count field is missing or misread. "
        f"If {_EXPECTED_CUSTOMER_COUNT}: incorrectly used len(rows) instead of sum(__count)."
    )
    assert result["record_count"] > 0, "record_count must be positive"
    assert result["record_count"] != result["customer_count"], (
        "record_count (total installments) must differ from customer_count (distinct partners)"
    )
