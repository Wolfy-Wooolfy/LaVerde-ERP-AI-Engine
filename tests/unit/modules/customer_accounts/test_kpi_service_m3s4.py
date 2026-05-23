"""
Unit tests for Customer Accounts KPI service — M3-S4:
  get_unallocated_wallet_balance (KPI C)
  get_refunds_summary            (Refunds alert section)

OdooClient is fully mocked; no live Odoo connection is made.
Live verification: scripts/verify_kpic_live.py, scripts/verify_refunds_live.py.

KPI C domain (M3-S1 discovery, MODULE_3_DISCOVERY_M3S1.md §5):
    state='post' + residual_amount > 0
    residual_amount>0 is intentional — excludes the 7 refund records.

Refunds domain (M3-S1 discovery, MODULE_3_DISCOVERY_M3S1.md §6):
    state='post' + amount < 0
    Flow direction: sign of amount (not payment_type — unreliable per Phase 3 §4.1).

Baselines (M3-S1, 2026-05-23):
    KPI C  : 17,214,301.92 EGP / 27 customers / 198 records  (moving)
    Refunds: −719,812.00 EGP / 7 records / 0 null-partner     (stable)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.core.exceptions import OdooQueryError, ReadOnlyViolationError
from backend.modules.customer_accounts.services import cache as _cache
from backend.modules.customer_accounts.services.kpi_service import (
    _CACHE_KEY_PREFIX_KPIC,
    _CACHE_KEY_PREFIX_REFUNDS,
    _RECONCILE_MODEL,
    get_refunds_summary,
    get_unallocated_wallet_balance,
)

# ── KPI C mock data ───────────────────────────────────────────────────────────
# 5 partners, each with multiple reconcile records (residual_amount > 0).
# All amounts positive — domain already filtered by Odoo.
_KPIC_MOCK_ROWS = [
    {"partner_id": [201, "Partner A"], "residual_amount": 500_000.0, "__count": 40},
    {"partner_id": [202, "Partner B"], "residual_amount": 300_000.0, "__count": 30},
    {"partner_id": [203, "Partner C"], "residual_amount": 200_000.0, "__count": 20},
    {"partner_id": [204, "Partner D"], "residual_amount": 150_000.0, "__count": 15},
    {"partner_id": [205, "Partner E"], "residual_amount":  50_000.0, "__count":  5},
]

_KPIC_EXPECTED_VALUE     = sum(float(r["residual_amount"]) for r in _KPIC_MOCK_ROWS)  # 1_200_000.0
_KPIC_EXPECTED_CUSTOMERS = len(_KPIC_MOCK_ROWS)                                        # 5
_KPIC_EXPECTED_RECORDS   = sum(int(r["__count"]) for r in _KPIC_MOCK_ROWS)             # 110

# ── Refunds mock data ─────────────────────────────────────────────────────────
# 3 partners with refund records (amount < 0). One row has partner_id = False
# to verify null_partner_count detection.
_REFUNDS_MOCK_ROWS = [
    {"partner_id": [301, "Customer X"],  "amount": -60_000.0, "__count": 3},
    {"partner_id": [302, "Customer Y"],  "amount": -40_000.0, "__count": 2},
    {"partner_id": False,               "amount": -20_000.0, "__count": 1},
]

_REFUNDS_EXPECTED_TOTAL        = sum(float(r["amount"]) for r in _REFUNDS_MOCK_ROWS)   # -120_000.0
_REFUNDS_EXPECTED_COUNT        = sum(int(r["__count"]) for r in _REFUNDS_MOCK_ROWS)    # 6
_REFUNDS_EXPECTED_NULL_PARTNER = sum(                                                   # 1
    int(r["__count"]) for r in _REFUNDS_MOCK_ROWS if not r.get("partner_id")
)


# ── Shared fixtures ───────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def fresh_cache() -> None:
    _cache.clear()
    yield
    _cache.clear()


@pytest.fixture
def mock_client_kpic() -> MagicMock:
    client = MagicMock()
    client.execute_kw = AsyncMock(return_value=_KPIC_MOCK_ROWS)
    return client


@pytest.fixture
def mock_client_refunds() -> MagicMock:
    client = MagicMock()
    client.execute_kw = AsyncMock(return_value=_REFUNDS_MOCK_ROWS)
    return client


# ═══════════════════════════════════════════════════════════════════════════════
# KPI C — get_unallocated_wallet_balance
# ═══════════════════════════════════════════════════════════════════════════════

# ── Test 1 — domain has exactly 2 clauses ─────────────────────────────────────

async def test_kpic_domain_has_two_clauses(mock_client_kpic: MagicMock) -> None:
    await get_unallocated_wallet_balance(client=mock_client_kpic)

    call   = mock_client_kpic.execute_kw.call_args
    domain = call.kwargs["args"][0]

    assert len(domain) == 2, f"Expected 2-clause domain, got {len(domain)}: {domain!r}"


# ── Test 2 — domain contains residual_amount > 0 (the intentional filter) ────

async def test_kpic_domain_has_residual_amount_gt_zero(mock_client_kpic: MagicMock) -> None:
    """residual_amount>0 excludes refund records — this filter must always be present."""
    await get_unallocated_wallet_balance(client=mock_client_kpic)

    call   = mock_client_kpic.execute_kw.call_args
    domain = call.kwargs["args"][0]

    has_residual_filter = any(
        clause[0] == "residual_amount" and clause[1] == ">" and clause[2] == 0
        for clause in domain
        if isinstance(clause, (list, tuple)) and len(clause) == 3
    )
    assert has_residual_filter, (
        f"domain must include ('residual_amount', '>', 0) to exclude refund records. "
        f"Got: {domain!r}"
    )


# ── Test 3 — domain contains state = post ────────────────────────────────────

async def test_kpic_domain_has_state_post(mock_client_kpic: MagicMock) -> None:
    await get_unallocated_wallet_balance(client=mock_client_kpic)

    call   = mock_client_kpic.execute_kw.call_args
    domain = call.kwargs["args"][0]

    has_state = any(
        clause[0] == "state" and clause[1] == "=" and clause[2] == "post"
        for clause in domain
        if isinstance(clause, (list, tuple)) and len(clause) == 3
    )
    assert has_state, f"domain must include ('state', '=', 'post'). Got: {domain!r}"


# ── Test 4 — queries the reconcile model, not rs.installment ─────────────────

async def test_kpic_queries_reconcile_model(mock_client_kpic: MagicMock) -> None:
    await get_unallocated_wallet_balance(client=mock_client_kpic)

    call  = mock_client_kpic.execute_kw.call_args
    model = call.args[0]

    assert model == _RECONCILE_MODEL, (
        f"Expected model={_RECONCILE_MODEL!r}, got {model!r}"
    )


# ── Test 5 — uses read_group grouped by partner_id ───────────────────────────

async def test_kpic_uses_read_group_groupby_partner_id(mock_client_kpic: MagicMock) -> None:
    await get_unallocated_wallet_balance(client=mock_client_kpic)

    call    = mock_client_kpic.execute_kw.call_args
    method  = call.args[1]
    groupby = call.kwargs["args"][2]

    assert method  == "read_group",   f"Expected read_group, got {method!r}"
    assert groupby == ["partner_id"], f"Expected groupby=['partner_id'], got {groupby!r}"


# ── Test 6 — return shape has all required keys ───────────────────────────────

async def test_kpic_return_shape_has_all_required_keys(mock_client_kpic: MagicMock) -> None:
    result = await get_unallocated_wallet_balance(client=mock_client_kpic)

    expected_keys = {
        "value", "customer_count", "record_count",
        "currency", "as_of", "cache_status", "rpc_duration_ms", "domain",
    }
    assert set(result.keys()) == expected_keys

    assert isinstance(result["value"],           float)
    assert isinstance(result["customer_count"],  int)
    assert isinstance(result["record_count"],    int)
    assert result["currency"]     == "EGP"
    assert result["cache_status"] in {"fresh", "cached"}
    assert isinstance(result["rpc_duration_ms"], int)
    assert isinstance(result["domain"],          list)


# ── Test 7 — value is SUM(residual_amount) across all rows ───────────────────

async def test_kpic_value_is_sum_of_residual_amount(mock_client_kpic: MagicMock) -> None:
    result = await get_unallocated_wallet_balance(client=mock_client_kpic)

    assert result["value"] == pytest.approx(_KPIC_EXPECTED_VALUE), (
        f"Expected value={_KPIC_EXPECTED_VALUE}, got {result['value']}"
    )


# ── Test 8 — customer_count is number of partner groups ──────────────────────

async def test_kpic_customer_count_equals_number_of_groups(mock_client_kpic: MagicMock) -> None:
    result = await get_unallocated_wallet_balance(client=mock_client_kpic)

    assert result["customer_count"] == _KPIC_EXPECTED_CUSTOMERS, (
        f"Expected customer_count={_KPIC_EXPECTED_CUSTOMERS} (len of read_group rows), "
        f"got {result['customer_count']}"
    )


# ── Test 9 — record_count is SUM(__count), not len(rows) ─────────────────────

async def test_kpic_record_count_from_groupby_count(mock_client_kpic: MagicMock) -> None:
    result = await get_unallocated_wallet_balance(client=mock_client_kpic)

    assert result["record_count"] == _KPIC_EXPECTED_RECORDS, (
        f"Expected record_count={_KPIC_EXPECTED_RECORDS} (sum of __count), "
        f"got {result['record_count']}. "
        f"If {_KPIC_EXPECTED_CUSTOMERS}: incorrectly used len(rows)."
    )
    assert result["record_count"] > 0


# ── Test 10 — second call served from cache ───────────────────────────────────

async def test_kpic_second_call_served_from_cache(mock_client_kpic: MagicMock) -> None:
    result1 = await get_unallocated_wallet_balance(client=mock_client_kpic)
    result2 = await get_unallocated_wallet_balance(client=mock_client_kpic)

    assert mock_client_kpic.execute_kw.call_count == 1, (
        "execute_kw must be called exactly once; second call must be served from cache"
    )
    assert result1["cache_status"] == "fresh"
    assert result2["cache_status"] == "cached"
    assert result2["rpc_duration_ms"] == 0
    assert result2["value"]          == result1["value"]
    assert result2["customer_count"] == result1["customer_count"]


# ── Test 11 — RPC failure raises OdooQueryError ───────────────────────────────

async def test_kpic_rpc_failure_raises_odoo_query_error(mock_client_kpic: MagicMock) -> None:
    mock_client_kpic.execute_kw.side_effect = RuntimeError("connection refused")

    with pytest.raises(OdooQueryError):
        await get_unallocated_wallet_balance(client=mock_client_kpic)


# ── Test 12 — RPC failure writes no cache entry ───────────────────────────────

async def test_kpic_rpc_failure_writes_no_cache_entry(mock_client_kpic: MagicMock) -> None:
    mock_client_kpic.execute_kw.side_effect = RuntimeError("timeout")

    with pytest.raises(OdooQueryError):
        await get_unallocated_wallet_balance(client=mock_client_kpic)

    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_KPIC)
    assert _cache.get(cache_key) is None, "A failed RPC must not leave a cache entry"


# ── Test 13 — read-only guard raises before RPC ───────────────────────────────

async def test_kpic_read_only_violation_raises_before_rpc(
    monkeypatch: pytest.MonkeyPatch,
    mock_client_kpic: MagicMock,
) -> None:
    monkeypatch.setattr(
        "backend.modules.customer_accounts.services.kpi_service.ALLOWED_METHODS",
        frozenset({"read_group", "write"}),
    )

    with pytest.raises(ReadOnlyViolationError):
        await get_unallocated_wallet_balance(client=mock_client_kpic)

    mock_client_kpic.execute_kw.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# Refunds — get_refunds_summary
# ═══════════════════════════════════════════════════════════════════════════════

# ── Test 14 — domain has exactly 2 clauses ────────────────────────────────────

async def test_refunds_domain_has_two_clauses(mock_client_refunds: MagicMock) -> None:
    await get_refunds_summary(client=mock_client_refunds)

    call   = mock_client_refunds.execute_kw.call_args
    domain = call.kwargs["args"][0]

    assert len(domain) == 2, f"Expected 2-clause domain, got {len(domain)}: {domain!r}"


# ── Test 15 — domain filters amount < 0 (not payment_type) ──────────────────

async def test_refunds_domain_filters_amount_lt_zero(mock_client_refunds: MagicMock) -> None:
    """Must use amount<0, not payment_type — payment_type='inbound' for all 205 records."""
    await get_refunds_summary(client=mock_client_refunds)

    call   = mock_client_refunds.execute_kw.call_args
    domain = call.kwargs["args"][0]

    has_amount_filter = any(
        clause[0] == "amount" and clause[1] == "<" and clause[2] == 0
        for clause in domain
        if isinstance(clause, (list, tuple)) and len(clause) == 3
    )
    assert has_amount_filter, (
        f"domain must use ('amount', '<', 0) for refund detection — "
        f"payment_type is unreliable (MODULE_3_DISCOVERY_PHASE_3.md §4.1). "
        f"Got: {domain!r}"
    )

    has_payment_type = any(
        isinstance(clause, (list, tuple)) and clause[0] == "payment_type"
        for clause in domain
    )
    assert not has_payment_type, (
        "domain must NOT use payment_type — it is 'inbound' for all records including refunds"
    )


# ── Test 16 — queries the reconcile model ────────────────────────────────────

async def test_refunds_queries_reconcile_model(mock_client_refunds: MagicMock) -> None:
    await get_refunds_summary(client=mock_client_refunds)

    call  = mock_client_refunds.execute_kw.call_args
    model = call.args[0]

    assert model == _RECONCILE_MODEL, (
        f"Expected model={_RECONCILE_MODEL!r}, got {model!r}"
    )


# ── Test 17 — return shape has all required keys ──────────────────────────────

async def test_refunds_return_shape_has_all_required_keys(mock_client_refunds: MagicMock) -> None:
    result = await get_refunds_summary(client=mock_client_refunds)

    expected_keys = {
        "total_refunds", "refund_count", "null_partner_count",
        "currency", "as_of", "cache_status", "rpc_duration_ms", "domain",
    }
    assert set(result.keys()) == expected_keys

    assert isinstance(result["total_refunds"],      float)
    assert isinstance(result["refund_count"],       int)
    assert isinstance(result["null_partner_count"], int)
    assert result["currency"]     == "EGP"
    assert result["cache_status"] in {"fresh", "cached"}


# ── Test 18 — total_refunds is SUM(amount) — must be negative ────────────────

async def test_refunds_total_is_sum_of_amounts(mock_client_refunds: MagicMock) -> None:
    result = await get_refunds_summary(client=mock_client_refunds)

    assert result["total_refunds"] == pytest.approx(_REFUNDS_EXPECTED_TOTAL), (
        f"Expected total_refunds={_REFUNDS_EXPECTED_TOTAL}, got {result['total_refunds']}"
    )
    assert result["total_refunds"] < 0, (
        "total_refunds must be negative — it is SUM of amount<0 records"
    )


# ── Test 19 — refund_count is SUM(__count), not len(rows) ────────────────────

async def test_refunds_count_from_groupby_count(mock_client_refunds: MagicMock) -> None:
    result = await get_refunds_summary(client=mock_client_refunds)

    assert result["refund_count"] == _REFUNDS_EXPECTED_COUNT, (
        f"Expected refund_count={_REFUNDS_EXPECTED_COUNT} (sum of __count per group), "
        f"got {result['refund_count']}. "
        f"If {len(_REFUNDS_MOCK_ROWS)}: incorrectly used len(rows)."
    )


# ── Test 20 — null_partner_count correctly sums __count for falsy partner_id ──

async def test_refunds_null_partner_count_correct(mock_client_refunds: MagicMock) -> None:
    """Mock has 1 row with partner_id=False and __count=1 → null_partner_count must be 1."""
    result = await get_refunds_summary(client=mock_client_refunds)

    assert result["null_partner_count"] == _REFUNDS_EXPECTED_NULL_PARTNER, (
        f"Expected null_partner_count={_REFUNDS_EXPECTED_NULL_PARTNER}, "
        f"got {result['null_partner_count']}"
    )


# ── Test 21 — null_partner_count is 0 when all partners are known ─────────────

async def test_refunds_null_partner_count_is_zero_when_all_known() -> None:
    rows_all_known = [
        {"partner_id": [301, "Cust X"], "amount": -50_000.0, "__count": 3},
        {"partner_id": [302, "Cust Y"], "amount": -30_000.0, "__count": 2},
    ]
    client = MagicMock()
    client.execute_kw = AsyncMock(return_value=rows_all_known)

    result = await get_refunds_summary(client=client)

    assert result["null_partner_count"] == 0, (
        f"Expected null_partner_count=0 when all rows have a known partner, "
        f"got {result['null_partner_count']}"
    )


# ── Test 22 — second call served from cache ───────────────────────────────────

async def test_refunds_second_call_served_from_cache(mock_client_refunds: MagicMock) -> None:
    result1 = await get_refunds_summary(client=mock_client_refunds)
    result2 = await get_refunds_summary(client=mock_client_refunds)

    assert mock_client_refunds.execute_kw.call_count == 1
    assert result1["cache_status"] == "fresh"
    assert result2["cache_status"] == "cached"
    assert result2["rpc_duration_ms"] == 0
    assert result2["total_refunds"]      == result1["total_refunds"]
    assert result2["null_partner_count"] == result1["null_partner_count"]


# ── Test 23 — RPC failure raises OdooQueryError ───────────────────────────────

async def test_refunds_rpc_failure_raises_odoo_query_error(
    mock_client_refunds: MagicMock,
) -> None:
    mock_client_refunds.execute_kw.side_effect = RuntimeError("network error")

    with pytest.raises(OdooQueryError):
        await get_refunds_summary(client=mock_client_refunds)


# ── Test 24 — RPC failure writes no cache entry ───────────────────────────────

async def test_refunds_rpc_failure_writes_no_cache_entry(
    mock_client_refunds: MagicMock,
) -> None:
    mock_client_refunds.execute_kw.side_effect = RuntimeError("timeout")

    with pytest.raises(OdooQueryError):
        await get_refunds_summary(client=mock_client_refunds)

    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_REFUNDS)
    assert _cache.get(cache_key) is None, "A failed RPC must not leave a cache entry"


# ── Test 25 — read-only guard raises before RPC ───────────────────────────────

async def test_refunds_read_only_violation_raises_before_rpc(
    monkeypatch: pytest.MonkeyPatch,
    mock_client_refunds: MagicMock,
) -> None:
    monkeypatch.setattr(
        "backend.modules.customer_accounts.services.kpi_service.ALLOWED_METHODS",
        frozenset({"read_group", "unlink"}),
    )

    with pytest.raises(ReadOnlyViolationError):
        await get_refunds_summary(client=mock_client_refunds)

    mock_client_refunds.execute_kw.assert_not_called()


# ── Test 26 — KPI C and Refunds use independent cache entries ─────────────────

async def test_kpic_and_refunds_use_independent_cache_entries(
    mock_client_kpic: MagicMock,
    mock_client_refunds: MagicMock,
) -> None:
    """Populating KPI C cache must not affect Refunds cache and vice versa."""
    await get_unallocated_wallet_balance(client=mock_client_kpic)

    refunds_key = _cache.make_key(_CACHE_KEY_PREFIX_REFUNDS)
    assert _cache.get(refunds_key) is None, (
        "KPI C cache entry must not bleed into Refunds cache"
    )

    await get_refunds_summary(client=mock_client_refunds)

    kpic_result2 = await get_unallocated_wallet_balance(client=mock_client_kpic)
    assert kpic_result2["cache_status"] == "cached", (
        "KPI C must still be cached after Refunds call"
    )
    assert mock_client_kpic.execute_kw.call_count == 1, (
        "KPI C execute_kw must still have been called exactly once"
    )
