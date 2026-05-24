"""
Unit tests for customer_accounts/services/refunds_detail_service.py.

OdooClient is fully mocked; no live Odoo connection is made.
Live verification is the job of scripts/verify_refunds_detail_live.py.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.core.exceptions import OdooQueryError, ReadOnlyViolationError
from backend.modules.customer_accounts.services import cache as _cache
from backend.modules.customer_accounts.services.refunds_detail_service import (
    _RECONCILE_MODEL,
    get_refunds_detail,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

_MOCK_RECORDS = [
    {
        "id": 10,
        "partner_id": [42, "أحمد محمد"],
        "amount": -120000.0,
        "date": "2025-11-10",
    },
    {
        "id": 11,
        "partner_id": [99, "عميل غير معروف"],
        "amount": -60000.0,
        "date": "2025-09-05",
    },
    {
        "id": 12,
        "partner_id": False,
        "amount": -539812.0,
        "date": "2025-07-01",
    },
]


@pytest.fixture(autouse=True)
def fresh_cache():
    _cache.clear()
    yield
    _cache.clear()


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.execute_kw = AsyncMock(return_value=_MOCK_RECORDS)
    return client


# ── Test 1 — Domain is exactly [('state','=','post'), ('amount','<',0)] ───────


async def test_domain_correct(mock_client: MagicMock) -> None:
    await get_refunds_detail(client=mock_client)

    call_args = mock_client.execute_kw.call_args
    domain = call_args.kwargs["args"][0]

    assert domain == [("state", "=", "post"), ("amount", "<", 0)], (
        f"Expected refunds domain, got {domain!r}"
    )


# ── Test 2 — Model is rs.account.payment.reconcile ───────────────────────────


async def test_model_correct(mock_client: MagicMock) -> None:
    await get_refunds_detail(client=mock_client)

    call_args = mock_client.execute_kw.call_args
    model = call_args.args[0]

    assert model == _RECONCILE_MODEL


# ── Test 3 — null partner_id → customer_name fallback "غير معروف" ─────────────


async def test_null_partner_fallback(mock_client: MagicMock) -> None:
    result = await get_refunds_detail(client=mock_client)

    items = result["items"]
    null_row = next(i for i in items if i["record_id"] == 12)

    assert null_row["customer_id"] == 0
    assert null_row["customer_name"] == "غير معروف"


# ── Test 4 — named partner → name extracted correctly ────────────────────────


async def test_named_partner_extracted(mock_client: MagicMock) -> None:
    result = await get_refunds_detail(client=mock_client)

    items = result["items"]
    row = next(i for i in items if i["record_id"] == 10)

    assert row["customer_id"] == 42
    assert row["customer_name"] == "أحمد محمد"


# ── Test 5 — read-only guard: ALLOWED_METHODS must not contain write methods ──


async def test_read_only_guard() -> None:
    with patch(
        "backend.modules.customer_accounts.services.refunds_detail_service.ALLOWED_METHODS",
        {"search_read", "write"},
    ):
        with pytest.raises(ReadOnlyViolationError):
            await get_refunds_detail()


# ── Test 6 — response shape has all required keys ────────────────────────────


async def test_response_shape(mock_client: MagicMock) -> None:
    result = await get_refunds_detail(client=mock_client)

    required_keys = {
        "items", "total_amount", "record_count",
        "currency", "as_of", "cache_status", "rpc_duration_ms", "domain",
    }
    assert required_keys.issubset(result.keys()), (
        f"Missing keys: {required_keys - result.keys()!r}"
    )
    assert result["currency"] == "EGP"
    assert result["cache_status"] == "fresh"
    assert len(result["items"]) == len(_MOCK_RECORDS)

    row = result["items"][0]
    row_keys = {"record_id", "customer_id", "customer_name", "amount", "date"}
    assert row_keys.issubset(row.keys())


# ── Test 7 — total_amount equals sum of item amounts ─────────────────────────


async def test_total_amount_equals_sum(mock_client: MagicMock) -> None:
    result = await get_refunds_detail(client=mock_client)

    expected_total = sum(r["amount"] for r in _MOCK_RECORDS)
    assert abs(result["total_amount"] - expected_total) < 0.01, (
        f"total_amount {result['total_amount']} != sum {expected_total}"
    )
    assert result["record_count"] == len(_MOCK_RECORDS)


# ── Test 8 — RPC failure → OdooQueryError ────────────────────────────────────


async def test_rpc_failure_raises_odoo_query_error() -> None:
    bad_client = MagicMock()
    bad_client.execute_kw = AsyncMock(side_effect=Exception("connection refused"))

    with pytest.raises(OdooQueryError):
        await get_refunds_detail(client=bad_client)


# ── Test 9 — cache hit returns cached value ───────────────────────────────────


async def test_cache_hit_returns_cached(mock_client: MagicMock) -> None:
    first  = await get_refunds_detail(client=mock_client)
    second = await get_refunds_detail(client=mock_client)

    assert first["total_amount"] == second["total_amount"]
    assert second["cache_status"] == "cached"
    assert second["rpc_duration_ms"] == 0
    # execute_kw called only once (second call served from cache)
    assert mock_client.execute_kw.call_count == 1
