"""
Unit tests for Customer Accounts KPI service — get_top_overdue_customers (KPI B).

OdooClient is fully mocked; no live Odoo connection is made.
Live verification: scripts/verify_kpib_live.py.

Late domain (Candidate C, three-clause) confirmed in M3-S1 discovery (commit 00f3abf):
    state='post' + payment_state in [unpaid,partial] + date < today

Baseline (M3-S1, 2026-05-23):
    total_overdue          = 333,271,714.40 EGP
    overdue_customer_count = 797
    top10_pct              = 21.8%
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.core.exceptions import OdooQueryError, ReadOnlyViolationError
from backend.modules.customer_accounts.services import cache as _cache
from backend.modules.customer_accounts.services.kpi_service import (
    _CACHE_KEY_PREFIX_KPIB,
    _CONCENTRATION_N,
    get_top_overdue_customers,
)

# ── Mock data ─────────────────────────────────────────────────────────────────
# 25 synthetic rows — more than 20 so limit and total-vs-top20 tests are meaningful.
# Amounts deliberately not in order to test sorting. Names are synthetic (no PII).
_MOCK_ROWS = [
    {"partner_id": [101, "Customer A"],  "due_amount": 500_000.0, "__count": 10},
    {"partner_id": [102, "Customer B"],  "due_amount": 900_000.0, "__count":  3},
    {"partner_id": [103, "Customer C"],  "due_amount": 200_000.0, "__count":  7},
    {"partner_id": [104, "Customer D"],  "due_amount": 750_000.0, "__count":  4},
    {"partner_id": [105, "Customer E"],  "due_amount": 100_000.0, "__count":  2},
    {"partner_id": [106, "Customer F"],  "due_amount": 850_000.0, "__count":  6},
    {"partner_id": [107, "Customer G"],  "due_amount": 300_000.0, "__count":  1},
    {"partner_id": [108, "Customer H"],  "due_amount": 650_000.0, "__count":  5},
    {"partner_id": [109, "Customer I"],  "due_amount": 420_000.0, "__count":  8},
    {"partner_id": [110, "Customer J"],  "due_amount": 980_000.0, "__count":  9},
    {"partner_id": [111, "Customer K"],  "due_amount": 110_000.0, "__count":  2},
    {"partner_id": [112, "Customer L"],  "due_amount": 760_000.0, "__count":  3},
    {"partner_id": [113, "Customer M"],  "due_amount": 330_000.0, "__count": 11},
    {"partner_id": [114, "Customer N"],  "due_amount": 870_000.0, "__count":  4},
    {"partner_id": [115, "Customer O"],  "due_amount": 440_000.0, "__count":  6},
    {"partner_id": [116, "Customer P"],  "due_amount": 560_000.0, "__count":  7},
    {"partner_id": [117, "Customer Q"],  "due_amount": 120_000.0, "__count":  2},
    {"partner_id": [118, "Customer R"],  "due_amount": 690_000.0, "__count":  5},
    {"partner_id": [119, "Customer S"],  "due_amount": 380_000.0, "__count":  3},
    {"partner_id": [120, "Customer T"],  "due_amount": 240_000.0, "__count":  8},
    {"partner_id": [121, "Customer U"],  "due_amount":  80_000.0, "__count":  1},
    {"partner_id": [122, "Customer V"],  "due_amount": 610_000.0, "__count":  4},
    {"partner_id": [123, "Customer W"],  "due_amount": 720_000.0, "__count":  6},
    {"partner_id": [124, "Customer X"],  "due_amount": 470_000.0, "__count":  9},
    {"partner_id": [125, "Customer Y"],  "due_amount":  50_000.0, "__count":  2},
]

_EXPECTED_TOTAL    = sum(float(r["due_amount"]) for r in _MOCK_ROWS)   # 12,630,000.0
_EXPECTED_CUSTOMERS = len(_MOCK_ROWS)                                   # 25
_EXPECTED_RECORDS   = sum(int(r["__count"]) for r in _MOCK_ROWS)       # 138

_SORTED_AMOUNTS = sorted(
    [float(r["due_amount"]) for r in _MOCK_ROWS], reverse=True
)
_EXPECTED_TOP10_AMOUNT = sum(_SORTED_AMOUNTS[:_CONCENTRATION_N])
_EXPECTED_TOP10_PCT    = round(_EXPECTED_TOP10_AMOUNT / _EXPECTED_TOTAL * 100, 2)


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


# ── Test 1 — Late domain has exactly 3 clauses ────────────────────────────────

async def test_domain_is_late_three_clause(mock_client: MagicMock) -> None:
    await get_top_overdue_customers(client=mock_client)

    call   = mock_client.execute_kw.call_args
    domain = call.kwargs["args"][0]

    assert len(domain) == 3, f"Expected 3-clause Late domain, got {len(domain)}: {domain!r}"


# ── Test 2 — Each clause of the Late domain is correct ───────────────────────

async def test_domain_clauses_correct(mock_client: MagicMock) -> None:
    with patch(
        "backend.modules.customer_accounts.services.kpi_service._cache.today_str",
        return_value="2026-05-23",
    ):
        await get_top_overdue_customers(client=mock_client)

    call   = mock_client.execute_kw.call_args
    domain = call.kwargs["args"][0]

    assert domain[0] == ("state", "=", "post"),               f"clause 0: {domain[0]!r}"
    assert domain[1] == ("payment_state", "in", ["unpaid", "partial"]), \
        f"clause 1: {domain[1]!r}"
    assert domain[2] == ("date", "<", "2026-05-23"),          f"clause 2: {domain[2]!r}"


# ── Test 3 — read_group with groupby partner_id ───────────────────────────────

async def test_uses_read_group_with_groupby_partner_id(mock_client: MagicMock) -> None:
    await get_top_overdue_customers(client=mock_client)

    call    = mock_client.execute_kw.call_args
    method  = call.args[1]
    groupby = call.kwargs["args"][2]

    assert method  == "read_group",      f"Expected read_group, got {method!r}"
    assert groupby == ["partner_id"],    f"Expected groupby=['partner_id'], got {groupby!r}"


# ── Test 4 — Return shape has all required keys ───────────────────────────────

async def test_return_shape_has_all_required_keys(mock_client: MagicMock) -> None:
    result = await get_top_overdue_customers(client=mock_client)

    top_keys = {
        "total_overdue", "overdue_customer_count", "record_count",
        "top_n_concentration", "top_customers",
        "currency", "as_of", "cache_status", "rpc_duration_ms", "domain",
    }
    assert set(result.keys()) == top_keys

    conc = result["top_n_concentration"]
    assert set(conc.keys()) == {"n", "amount", "pct"}
    assert conc["n"] == _CONCENTRATION_N

    assert isinstance(result["total_overdue"],          float)
    assert isinstance(result["overdue_customer_count"], int)
    assert isinstance(result["record_count"],           int)
    assert isinstance(result["top_customers"],          list)
    assert result["currency"]      == "EGP"
    assert result["cache_status"]  in {"fresh", "cached"}
    assert isinstance(result["rpc_duration_ms"], int)
    assert isinstance(result["domain"],          list)


# ── Test 5 — total_overdue is sum of ALL partners, not just top 20 ────────────

async def test_total_overdue_uses_all_partners_not_just_top20(
    mock_client: MagicMock,
) -> None:
    result = await get_top_overdue_customers(client=mock_client)

    assert result["total_overdue"] == pytest.approx(_EXPECTED_TOTAL), (
        f"Expected total from all {_EXPECTED_CUSTOMERS} partners = {_EXPECTED_TOTAL}, "
        f"got {result['total_overdue']}. "
        "If this equals the top-20 sum, total is being computed after slicing."
    )


# ── Test 6 — record_count from __count, not zero ─────────────────────────────

async def test_record_count_from_groupby_count(mock_client: MagicMock) -> None:
    result = await get_top_overdue_customers(client=mock_client)

    assert result["record_count"] == _EXPECTED_RECORDS, (
        f"Expected record_count={_EXPECTED_RECORDS} (sum __count), got {result['record_count']}. "
        "If 0: __count misread. "
        f"If {_EXPECTED_CUSTOMERS}: incorrectly used len(rows)."
    )
    assert result["record_count"] > 0


# ── Test 7 — top_customers sorted by due_amount descending ───────────────────

async def test_top_customers_sorted_descending(mock_client: MagicMock) -> None:
    result = await get_top_overdue_customers(client=mock_client)

    amounts = [row["due_amount"] for row in result["top_customers"]]
    assert amounts == sorted(amounts, reverse=True), (
        f"top_customers not sorted descending: {amounts}"
    )


# ── Test 8 — top_customers limited to 20 ─────────────────────────────────────

async def test_top_customers_limited_to_20(mock_client: MagicMock) -> None:
    result = await get_top_overdue_customers(client=mock_client)

    assert len(result["top_customers"]) == 20, (
        f"Expected 20 rows (mock has {_EXPECTED_CUSTOMERS} rows, limit=20), "
        f"got {len(result['top_customers'])}"
    )


# ── Test 9 — concentration pct uses ALL partners as denominator ──────────────

async def test_concentration_pct_correct(mock_client: MagicMock) -> None:
    result = await get_top_overdue_customers(client=mock_client)
    conc   = result["top_n_concentration"]

    assert conc["amount"] == pytest.approx(_EXPECTED_TOP10_AMOUNT), (
        f"top_n_concentration.amount: expected {_EXPECTED_TOP10_AMOUNT}, "
        f"got {conc['amount']}"
    )
    assert conc["pct"] == pytest.approx(_EXPECTED_TOP10_PCT, abs=0.01), (
        f"top_n_concentration.pct: expected {_EXPECTED_TOP10_PCT:.2f}%, "
        f"got {conc['pct']:.2f}%"
    )


# ── Test 10 — second call served from cache ───────────────────────────────────

async def test_second_call_served_from_cache(mock_client: MagicMock) -> None:
    result1 = await get_top_overdue_customers(client=mock_client)
    result2 = await get_top_overdue_customers(client=mock_client)

    assert mock_client.execute_kw.call_count == 1, (
        "execute_kw must be called exactly once; second call must be served from cache"
    )
    assert result1["cache_status"] == "fresh"
    assert result2["cache_status"] == "cached"
    assert result2["rpc_duration_ms"] == 0
    assert result2["total_overdue"] == result1["total_overdue"]
    assert result2["overdue_customer_count"] == result1["overdue_customer_count"]


# ── Test 11 — different dates produce independent cache entries ───────────────

async def test_different_dates_independent_cache_entries(
    mock_client: MagicMock,
) -> None:
    with patch(
        "backend.modules.customer_accounts.services.cache.today_str",
        return_value="2026-05-22",
    ):
        await get_top_overdue_customers(client=mock_client)

    with patch(
        "backend.modules.customer_accounts.services.cache.today_str",
        return_value="2026-05-23",
    ):
        await get_top_overdue_customers(client=mock_client)

    assert mock_client.execute_kw.call_count == 2, (
        "Calls on different dates must each hit Odoo (independent cache entries)"
    )


# ── Test 12 — RPC failure raises OdooQueryError ───────────────────────────────

async def test_rpc_failure_raises_odoo_query_error(mock_client: MagicMock) -> None:
    mock_client.execute_kw.side_effect = RuntimeError("connection refused")

    with pytest.raises(OdooQueryError):
        await get_top_overdue_customers(client=mock_client)


# ── Test 13 — RPC failure writes no cache entry ───────────────────────────────

async def test_rpc_failure_writes_no_cache_entry(mock_client: MagicMock) -> None:
    mock_client.execute_kw.side_effect = RuntimeError("timeout")

    with pytest.raises(OdooQueryError):
        await get_top_overdue_customers(client=mock_client)

    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_KPIB)
    assert _cache.get(cache_key) is None, (
        "A failed RPC must not leave a cache entry"
    )


# ── Test 14 — read-only guard raises before RPC ───────────────────────────────

async def test_read_only_violation_raises_before_rpc(
    monkeypatch: pytest.MonkeyPatch,
    mock_client: MagicMock,
) -> None:
    monkeypatch.setattr(
        "backend.modules.customer_accounts.services.kpi_service.ALLOWED_METHODS",
        frozenset({"read_group", "write"}),
    )

    with pytest.raises(ReadOnlyViolationError):
        await get_top_overdue_customers(client=mock_client)

    mock_client.execute_kw.assert_not_called()
