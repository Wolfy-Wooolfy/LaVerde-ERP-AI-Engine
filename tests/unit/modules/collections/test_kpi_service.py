"""
Unit tests for Collections KPI service — get_late_uncollected.

OdooClient is fully mocked; no live Odoo connection is made.
Live verification is the job of scripts/verify_kpi2_live.py.
"""

import re
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from backend.core.exceptions import OdooQueryError, ReadOnlyViolationError
from backend.modules.collections.services import cache as _cache
from backend.modules.collections.services.kpi_service import (
    _CACHE_KEY_PREFIX,
    _CACHE_KEY_PREFIX_KPI1,
    _CACHE_KEY_PREFIX_KPI6,
    _PAYMENT_HEADER_MODEL,
    get_collection_trend_6m,
    get_late_uncollected,
    get_pending_check_exposure,
    get_total_portfolio_value,
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


# ══════════════════════════════════════════════════════════════════════════════
# KPI 1 — Total Portfolio Value (get_total_portfolio_value)
# ══════════════════════════════════════════════════════════════════════════════

_MOCK_RESPONSE_KPI1 = [{"amount": 6_123_549_625.23, "__count": 42_443}]


@pytest.fixture
def mock_client_kpi1() -> MagicMock:
    client = MagicMock()
    client.execute_kw = AsyncMock(return_value=_MOCK_RESPONSE_KPI1)
    return client


# ── Test K1-1 — Domain construction ──────────────────────────────────────────


async def test_kpi1_domain_is_state_eq_post(mock_client_kpi1: MagicMock) -> None:
    await get_total_portfolio_value(client=mock_client_kpi1)

    call = mock_client_kpi1.execute_kw.call_args
    domain = call.kwargs["args"][0]

    assert len(domain) == 1, f"Expected 1-clause domain, got {len(domain)}: {domain}"
    assert domain[0] == ("state", "=", "post"), (
        f"Expected ('state','=','post'), got {domain[0]!r}. "
        "Domain must match Odoo 'All Installments' view — see Decision 2.4."
    )


# ── Test K1-2 — Aggregation method ───────────────────────────────────────────


async def test_kpi1_uses_read_group_not_search_read(mock_client_kpi1: MagicMock) -> None:
    await get_total_portfolio_value(client=mock_client_kpi1)

    call = mock_client_kpi1.execute_kw.call_args
    assert call.args[1] == "read_group", (
        f"Expected read_group, got {call.args[1]!r}. "
        "Aggregation must not use search_read + Python sum."
    )


# ── Test K1-3 — Return shape ──────────────────────────────────────────────────


async def test_kpi1_return_shape_has_all_required_keys(mock_client_kpi1: MagicMock) -> None:
    result = await get_total_portfolio_value(client=mock_client_kpi1)

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


async def test_kpi1_return_values_match_mock_response(mock_client_kpi1: MagicMock) -> None:
    result = await get_total_portfolio_value(client=mock_client_kpi1)

    assert result["value"] == pytest.approx(6_123_549_625.23)
    assert result["record_count"] == 42_443
    assert result["cache_status"] == "fresh"
    assert result["rpc_duration_ms"] >= 0


# ── Test K1-4 — Cache hit ─────────────────────────────────────────────────────


async def test_kpi1_second_call_is_served_from_cache(mock_client_kpi1: MagicMock) -> None:
    result1 = await get_total_portfolio_value(client=mock_client_kpi1)
    result2 = await get_total_portfolio_value(client=mock_client_kpi1)

    assert mock_client_kpi1.execute_kw.call_count == 1
    assert result1["cache_status"] == "fresh"
    assert result2["cache_status"] == "cached"
    assert result2["rpc_duration_ms"] == 0
    assert result2["value"] == result1["value"]


# ── Test K1-5 — Cache key independence from KPI 2 ────────────────────────────


async def test_kpi1_cache_independent_of_kpi2() -> None:
    mock_kpi1 = MagicMock()
    mock_kpi1.execute_kw = AsyncMock(return_value=_MOCK_RESPONSE_KPI1)

    mock_kpi2 = MagicMock()
    mock_kpi2.execute_kw = AsyncMock(return_value=_MOCK_RESPONSE)

    # Both KPIs: first call must be fresh (no cross-key pollution)
    r1 = await get_total_portfolio_value(client=mock_kpi1)
    r2 = await get_late_uncollected(client=mock_kpi2)
    assert r1["cache_status"] == "fresh"
    assert r2["cache_status"] == "fresh"

    # Both KPIs: second call must be cached from their own key
    r1b = await get_total_portfolio_value(client=mock_kpi1)
    r2b = await get_late_uncollected(client=mock_kpi2)
    assert r1b["cache_status"] == "cached"
    assert r2b["cache_status"] == "cached"

    # Each Odoo client called exactly once — no extra RPCs due to cross-pollution
    assert mock_kpi1.execute_kw.call_count == 1
    assert mock_kpi2.execute_kw.call_count == 1


# ── Test K1-6 — Cache invalidation at midnight ───────────────────────────────


async def test_kpi1_different_dates_produce_independent_cache_entries(
    mock_client_kpi1: MagicMock,
) -> None:
    with patch(
        "backend.modules.collections.services.cache.today_str",
        return_value="2026-05-15",
    ):
        await get_total_portfolio_value(client=mock_client_kpi1)

    with patch(
        "backend.modules.collections.services.cache.today_str",
        return_value="2026-05-16",
    ):
        await get_total_portfolio_value(client=mock_client_kpi1)

    assert mock_client_kpi1.execute_kw.call_count == 2


# ── Test K1-7 — Odoo RPC failure ─────────────────────────────────────────────


async def test_kpi1_rpc_failure_raises_odoo_query_error(mock_client_kpi1: MagicMock) -> None:
    mock_client_kpi1.execute_kw.side_effect = RuntimeError("connection refused")

    with pytest.raises(OdooQueryError):
        await get_total_portfolio_value(client=mock_client_kpi1)


async def test_kpi1_rpc_failure_writes_no_cache_entry(mock_client_kpi1: MagicMock) -> None:
    mock_client_kpi1.execute_kw.side_effect = RuntimeError("timeout")

    with pytest.raises(OdooQueryError):
        await get_total_portfolio_value(client=mock_client_kpi1)

    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_KPI1)
    assert _cache.get(cache_key) is None, "A failed RPC must not leave a cache entry"


# ── Test K1-8 — Read-only assertion ──────────────────────────────────────────


async def test_kpi1_contaminated_allowed_methods_raises_before_any_rpc(
    monkeypatch: pytest.MonkeyPatch,
    mock_client_kpi1: MagicMock,
) -> None:
    monkeypatch.setattr(
        "backend.modules.collections.services.kpi_service.ALLOWED_METHODS",
        frozenset({"read_group", "write"}),
    )

    with pytest.raises(ReadOnlyViolationError):
        await get_total_portfolio_value(client=mock_client_kpi1)

    mock_client_kpi1.execute_kw.assert_not_called()


async def test_kpi1_clean_allowed_methods_does_not_raise(mock_client_kpi1: MagicMock) -> None:
    result = await get_total_portfolio_value(client=mock_client_kpi1)
    assert result["value"] >= 0.0


# ══════════════════════════════════════════════════════════════════════════════
# KPI 5 — Late Uncollected by Project (get_late_uncollected_by_project)
# ══════════════════════════════════════════════════════════════════════════════

from backend.core.exceptions import UnknownProjectError
from backend.modules.collections.services.kpi_service import (
    _CACHE_KEY_PREFIX_KPI5,
    _PROJECT_NAMES,
    get_late_uncollected_by_project,
)

# Odoo read_group returns project_id as [id, display_name] for many2one fields.
_MOCK_RESPONSE_KPI5 = [
    {"project_id": [1, "Project#New Capital"], "due_amount": 164_017_258.40, "__count": 1472},
    {"project_id": [2, "Project#Cassette"],    "due_amount": 151_019_442.00, "__count": 488},
    {"project_id": [3, "Project#La puerta"],   "due_amount":   3_589_500.00, "__count": 21},
]

_EXPECTED_TOTAL = 164_017_258.40 + 151_019_442.00 + 3_589_500.00  # 318_626_200.40
_EXPECTED_COUNT = 1472 + 488 + 21  # 1981


@pytest.fixture
def mock_client_kpi5() -> MagicMock:
    client = MagicMock()
    client.execute_kw = AsyncMock(return_value=_MOCK_RESPONSE_KPI5)
    return client


# ── Test K5-1 — Domain construction ──────────────────────────────────────────


async def test_kpi5_domain_is_exact_candidate_c_three_clause(mock_client_kpi5: MagicMock) -> None:
    await get_late_uncollected_by_project(client=mock_client_kpi5)

    call = mock_client_kpi5.execute_kw.call_args
    domain = call.kwargs["args"][0]

    assert domain[0] == ("state", "=", "post")
    assert domain[1] == ("payment_state", "in", ["unpaid", "partial"])
    assert domain[2][0] == "date"
    assert domain[2][1] == "<"
    import re
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", domain[2][2]), (
        f"date clause must be YYYY-MM-DD, got {domain[2][2]!r}"
    )


# ── Test K5-2 — Aggregation method and groupby ───────────────────────────────


async def test_kpi5_uses_read_group_with_project_groupby(mock_client_kpi5: MagicMock) -> None:
    await get_late_uncollected_by_project(client=mock_client_kpi5)

    call = mock_client_kpi5.execute_kw.call_args
    assert call.args[1] == "read_group"
    groupby = call.kwargs["args"][2]
    assert groupby == ["project_id"], (
        f"Expected groupby=['project_id'], got {groupby!r}"
    )


# ── Test K5-3 — Return shape ──────────────────────────────────────────────────


async def test_kpi5_return_shape_has_all_required_top_level_keys(mock_client_kpi5: MagicMock) -> None:
    result = await get_late_uncollected_by_project(client=mock_client_kpi5)

    expected_keys = {
        "projects", "total_late_uncollected", "total_record_count",
        "currency", "as_of", "cache_status", "rpc_duration_ms", "domain",
    }
    assert set(result.keys()) == expected_keys

    assert isinstance(result["projects"], list)
    assert len(result["projects"]) == 3
    assert result["currency"] == "EGP"
    assert result["cache_status"] in {"fresh", "cached"}
    assert isinstance(result["rpc_duration_ms"], int)

    per_project_keys = {"project_id", "project_name", "late_uncollected", "record_count"}
    for proj in result["projects"]:
        assert set(proj.keys()) == per_project_keys
        assert isinstance(proj["project_id"], int)
        assert isinstance(proj["project_name"], str)
        assert isinstance(proj["late_uncollected"], float)
        assert isinstance(proj["record_count"], int)


# ── Test K5-4 — Project order: always 1, 2, 3 ────────────────────────────────


async def test_kpi5_projects_always_ordered_1_2_3_regardless_of_odoo_order(
    mock_client_kpi5: MagicMock,
) -> None:
    # Return rows in reverse order from Odoo — service must sort to 1, 2, 3.
    reversed_mock = [
        {"project_id": [3, "Project#La puerta"],   "due_amount": 3_589_500.00,   "__count": 21},
        {"project_id": [2, "Project#Cassette"],    "due_amount": 151_019_442.00, "__count": 488},
        {"project_id": [1, "Project#New Capital"], "due_amount": 164_017_258.40, "__count": 1472},
    ]
    mock_client_kpi5.execute_kw = AsyncMock(return_value=reversed_mock)

    result = await get_late_uncollected_by_project(client=mock_client_kpi5)

    ids = [p["project_id"] for p in result["projects"]]
    assert ids == [1, 2, 3], f"Expected [1,2,3], got {ids}"

    names = [p["project_name"] for p in result["projects"]]
    assert names == ["New Capital", "Cassette", "La puerta"], f"Wrong name order: {names}"


async def test_kpi5_project_names_are_clean_without_project_prefix(mock_client_kpi5: MagicMock) -> None:
    result = await get_late_uncollected_by_project(client=mock_client_kpi5)

    for proj in result["projects"]:
        assert not proj["project_name"].startswith("Project#"), (
            f"project_name must not include 'Project#' prefix, got {proj['project_name']!r}"
        )


# ── Test K5-5 — Zero-padding for missing projects ─────────────────────────────


async def test_kpi5_zero_pads_missing_project_when_read_group_returns_only_2(
    mock_client_kpi5: MagicMock,
) -> None:
    # Odoo returns only New Capital and Cassette — La puerta has no late records.
    two_project_mock = [
        {"project_id": [1, "Project#New Capital"], "due_amount": 164_017_258.40, "__count": 1472},
        {"project_id": [2, "Project#Cassette"],    "due_amount": 151_019_442.00, "__count": 488},
    ]
    mock_client_kpi5.execute_kw = AsyncMock(return_value=two_project_mock)

    result = await get_late_uncollected_by_project(client=mock_client_kpi5)

    assert len(result["projects"]) == 3, "Must always return 3 projects (zero-padding required)"

    lp = result["projects"][2]
    assert lp["project_id"] == 3
    assert lp["project_name"] == "La puerta"
    assert lp["late_uncollected"] == 0.0
    assert lp["record_count"] == 0


# ── Test K5-6 — Total computation ────────────────────────────────────────────


async def test_kpi5_totals_equal_sum_of_per_project_values(mock_client_kpi5: MagicMock) -> None:
    result = await get_late_uncollected_by_project(client=mock_client_kpi5)

    computed_late = sum(p["late_uncollected"] for p in result["projects"])
    computed_count = sum(p["record_count"] for p in result["projects"])

    assert result["total_late_uncollected"] == pytest.approx(computed_late)
    assert result["total_record_count"] == computed_count
    assert result["total_late_uncollected"] == pytest.approx(_EXPECTED_TOTAL)
    assert result["total_record_count"] == _EXPECTED_COUNT


# ── Test K5-7 — Cache hit ─────────────────────────────────────────────────────


async def test_kpi5_second_call_is_served_from_cache(mock_client_kpi5: MagicMock) -> None:
    result1 = await get_late_uncollected_by_project(client=mock_client_kpi5)
    result2 = await get_late_uncollected_by_project(client=mock_client_kpi5)

    assert mock_client_kpi5.execute_kw.call_count == 1
    assert result1["cache_status"] == "fresh"
    assert result2["cache_status"] == "cached"
    assert result2["rpc_duration_ms"] == 0
    assert result2["total_late_uncollected"] == result1["total_late_uncollected"]


# ── Test K5-8 — Cache key independence ───────────────────────────────────────


async def test_kpi5_cache_key_does_not_collide_with_kpi1_or_kpi2() -> None:
    mock_k2 = MagicMock()
    mock_k2.execute_kw = AsyncMock(return_value=_MOCK_RESPONSE)

    mock_k1 = MagicMock()
    mock_k1.execute_kw = AsyncMock(return_value=_MOCK_RESPONSE_KPI1)

    mock_k5 = MagicMock()
    mock_k5.execute_kw = AsyncMock(return_value=_MOCK_RESPONSE_KPI5)

    r2 = await get_late_uncollected(client=mock_k2)
    r1 = await get_total_portfolio_value(client=mock_k1)
    r5 = await get_late_uncollected_by_project(client=mock_k5)

    assert r2["cache_status"] == "fresh"
    assert r1["cache_status"] == "fresh"
    assert r5["cache_status"] == "fresh"

    r2b = await get_late_uncollected(client=mock_k2)
    r1b = await get_total_portfolio_value(client=mock_k1)
    r5b = await get_late_uncollected_by_project(client=mock_k5)

    assert r2b["cache_status"] == "cached"
    assert r1b["cache_status"] == "cached"
    assert r5b["cache_status"] == "cached"

    # Each Odoo client called exactly once — no cross-key pollution
    assert mock_k2.execute_kw.call_count == 1
    assert mock_k1.execute_kw.call_count == 1
    assert mock_k5.execute_kw.call_count == 1


# ── Test K5-9 — RPC failure ───────────────────────────────────────────────────


async def test_kpi5_rpc_failure_raises_odoo_query_error(mock_client_kpi5: MagicMock) -> None:
    mock_client_kpi5.execute_kw.side_effect = RuntimeError("connection refused")

    with pytest.raises(OdooQueryError):
        await get_late_uncollected_by_project(client=mock_client_kpi5)


async def test_kpi5_rpc_failure_writes_no_cache_entry(mock_client_kpi5: MagicMock) -> None:
    mock_client_kpi5.execute_kw.side_effect = RuntimeError("timeout")

    with pytest.raises(OdooQueryError):
        await get_late_uncollected_by_project(client=mock_client_kpi5)

    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_KPI5)
    assert _cache.get(cache_key) is None, "A failed RPC must not leave a cache entry"


# ── Test K5-10 — Read-only assertion ─────────────────────────────────────────


async def test_kpi5_contaminated_allowed_methods_raises_before_any_rpc(
    monkeypatch: pytest.MonkeyPatch,
    mock_client_kpi5: MagicMock,
) -> None:
    monkeypatch.setattr(
        "backend.modules.collections.services.kpi_service.ALLOWED_METHODS",
        frozenset({"read_group", "write"}),
    )

    with pytest.raises(ReadOnlyViolationError):
        await get_late_uncollected_by_project(client=mock_client_kpi5)

    mock_client_kpi5.execute_kw.assert_not_called()


async def test_kpi5_clean_allowed_methods_does_not_raise(mock_client_kpi5: MagicMock) -> None:
    result = await get_late_uncollected_by_project(client=mock_client_kpi5)
    assert result["total_late_uncollected"] >= 0.0


# ── Test K5-extra — Unknown project_id raises UnknownProjectError ─────────────


async def test_kpi5_unknown_project_id_raises_unknown_project_error(
    mock_client_kpi5: MagicMock,
) -> None:
    mock_client_kpi5.execute_kw = AsyncMock(return_value=[
        {"project_id": [99, "Project#Unknown"], "due_amount": 1_000.00, "__count": 1},
    ])

    with pytest.raises(UnknownProjectError):
        await get_late_uncollected_by_project(client=mock_client_kpi5)


# ══════════════════════════════════════════════════════════════════════════════
# KPI 3 — Pending Check Exposure (get_pending_check_exposure)
# ══════════════════════════════════════════════════════════════════════════════

from backend.modules.collections.services.kpi_service import _CACHE_KEY_PREFIX_KPI3  # noqa: E402

# D0-confirmed values (2026-05-16): derived = paid - actual = 518,235,384.10 EGP.
_MOCK_RESPONSE_KPI3 = [{
    "paid_amount": 3_488_834_648.95,
    "x_studio_actual_paid_amount": 2_970_599_264.85,
    "__count": 42_443,
}]
_EXPECTED_KPI3_PAID = 3_488_834_648.95
_EXPECTED_KPI3_ACTUAL = 2_970_599_264.85
_EXPECTED_KPI3_VALUE = _EXPECTED_KPI3_PAID - _EXPECTED_KPI3_ACTUAL  # 518_235_384.10
_EXPECTED_KPI3_DERIVATION_NOTE = "value = paid_amount_sum - actual_paid_sum"


@pytest.fixture
def mock_client_kpi3() -> MagicMock:
    client = MagicMock()
    client.execute_kw = AsyncMock(return_value=_MOCK_RESPONSE_KPI3)
    return client


# ── Test K3-1 — Domain construction ──────────────────────────────────────────


async def test_kpi3_domain_is_state_eq_post(mock_client_kpi3: MagicMock) -> None:
    await get_pending_check_exposure(client=mock_client_kpi3)

    call_args = mock_client_kpi3.execute_kw.call_args
    domain = call_args.kwargs["args"][0]

    assert len(domain) == 1, f"Expected 1-clause domain, got {len(domain)}: {domain}"
    assert domain[0] == ("state", "=", "post"), (
        f"Expected ('state','=','post'), got {domain[0]!r}. "
        "Decision 4.1: KPI 3 uses state='post' domain."
    )


# ── Test K3-2 — Aggregation: single read_group with BOTH fields ───────────────


async def test_kpi3_uses_read_group_with_both_amount_fields(mock_client_kpi3: MagicMock) -> None:
    await get_pending_check_exposure(client=mock_client_kpi3)

    call_args = mock_client_kpi3.execute_kw.call_args
    assert call_args.args[1] == "read_group", (
        f"Expected read_group, got {call_args.args[1]!r}. "
        "KPI 3 must aggregate via read_group, not search_read + Python sum."
    )
    fields = call_args.kwargs["args"][1]
    assert "paid_amount" in fields, (
        f"'paid_amount' must be in read_group fields, got {fields!r}"
    )
    assert "x_studio_actual_paid_amount" in fields, (
        f"'x_studio_actual_paid_amount' must be in read_group fields, got {fields!r}"
    )
    # Confirm single RPC — not two sequential calls
    assert mock_client_kpi3.execute_kw.call_count == 1, (
        "Both fields must be aggregated in a single read_group call, not two."
    )


# ── Test K3-3 — Return shape ──────────────────────────────────────────────────


async def test_kpi3_return_shape_has_all_required_keys(mock_client_kpi3: MagicMock) -> None:
    result = await get_pending_check_exposure(client=mock_client_kpi3)

    standard_keys = {"value", "currency", "record_count", "as_of",
                     "cache_status", "rpc_duration_ms", "domain"}
    kpi3_keys = {"paid_amount_sum", "actual_paid_sum",
                 "derivation_note", "data_quality_warning"}
    expected_keys = standard_keys | kpi3_keys
    assert set(result.keys()) == expected_keys

    assert isinstance(result["value"], float)
    assert result["currency"] == "EGP"
    assert isinstance(result["record_count"], int)
    assert isinstance(result["as_of"], str)
    assert result["cache_status"] in {"fresh", "cached"}
    assert isinstance(result["rpc_duration_ms"], int)
    assert isinstance(result["domain"], list)
    assert isinstance(result["paid_amount_sum"], float)
    assert isinstance(result["actual_paid_sum"], float)
    assert isinstance(result["derivation_note"], str)
    # data_quality_warning is None in the normal (non-negative) case
    assert result["data_quality_warning"] is None


# ── Test K3-4 — Derivation correctness ───────────────────────────────────────


async def test_kpi3_return_values_and_derivation(mock_client_kpi3: MagicMock) -> None:
    result = await get_pending_check_exposure(client=mock_client_kpi3)

    assert result["paid_amount_sum"] == pytest.approx(_EXPECTED_KPI3_PAID)
    assert result["actual_paid_sum"] == pytest.approx(_EXPECTED_KPI3_ACTUAL)
    assert result["value"] == pytest.approx(_EXPECTED_KPI3_VALUE)
    # Derivation: value == paid - actual (within float precision)
    assert abs(result["paid_amount_sum"] - result["actual_paid_sum"] - result["value"]) < 0.01
    assert result["derivation_note"] == _EXPECTED_KPI3_DERIVATION_NOTE
    assert result["record_count"] == 42_443
    assert result["cache_status"] == "fresh"


# ── Test K3-5 — Cache hit ─────────────────────────────────────────────────────


async def test_kpi3_second_call_is_served_from_cache(mock_client_kpi3: MagicMock) -> None:
    result1 = await get_pending_check_exposure(client=mock_client_kpi3)
    result2 = await get_pending_check_exposure(client=mock_client_kpi3)

    assert mock_client_kpi3.execute_kw.call_count == 1
    assert result1["cache_status"] == "fresh"
    assert result2["cache_status"] == "cached"
    assert result2["rpc_duration_ms"] == 0
    assert result2["value"] == result1["value"]


# ── Test K3-6 — Cache key independence ───────────────────────────────────────


async def test_kpi3_cache_key_does_not_collide_with_kpi1_kpi2_kpi5() -> None:
    from backend.modules.collections.services.kpi_service import (
        get_late_uncollected_by_project as _get_kpi5,
    )

    mock_k2 = MagicMock()
    mock_k2.execute_kw = AsyncMock(return_value=_MOCK_RESPONSE)

    mock_k1 = MagicMock()
    mock_k1.execute_kw = AsyncMock(return_value=_MOCK_RESPONSE_KPI1)

    mock_k5 = MagicMock()
    mock_k5.execute_kw = AsyncMock(return_value=_MOCK_RESPONSE_KPI5)

    mock_k3 = MagicMock()
    mock_k3.execute_kw = AsyncMock(return_value=_MOCK_RESPONSE_KPI3)

    r2 = await get_late_uncollected(client=mock_k2)
    r1 = await get_total_portfolio_value(client=mock_k1)
    r5 = await _get_kpi5(client=mock_k5)
    r3 = await get_pending_check_exposure(client=mock_k3)

    assert r2["cache_status"] == "fresh"
    assert r1["cache_status"] == "fresh"
    assert r5["cache_status"] == "fresh"
    assert r3["cache_status"] == "fresh"

    r2b = await get_late_uncollected(client=mock_k2)
    r1b = await get_total_portfolio_value(client=mock_k1)
    r5b = await _get_kpi5(client=mock_k5)
    r3b = await get_pending_check_exposure(client=mock_k3)

    assert r2b["cache_status"] == "cached"
    assert r1b["cache_status"] == "cached"
    assert r5b["cache_status"] == "cached"
    assert r3b["cache_status"] == "cached"

    # Each Odoo client called exactly once — no cross-key pollution
    assert mock_k2.execute_kw.call_count == 1
    assert mock_k1.execute_kw.call_count == 1
    assert mock_k5.execute_kw.call_count == 1
    assert mock_k3.execute_kw.call_count == 1


# ── Test K3-7a — RPC failure raises OdooQueryError ───────────────────────────


async def test_kpi3_rpc_failure_raises_odoo_query_error(mock_client_kpi3: MagicMock) -> None:
    mock_client_kpi3.execute_kw.side_effect = RuntimeError("connection refused")

    with pytest.raises(OdooQueryError):
        await get_pending_check_exposure(client=mock_client_kpi3)


# ── Test K3-7b — RPC failure writes no cache entry ───────────────────────────


async def test_kpi3_rpc_failure_writes_no_cache_entry(mock_client_kpi3: MagicMock) -> None:
    mock_client_kpi3.execute_kw.side_effect = RuntimeError("timeout")

    with pytest.raises(OdooQueryError):
        await get_pending_check_exposure(client=mock_client_kpi3)

    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_KPI3)
    assert _cache.get(cache_key) is None, "A failed RPC must not leave a cache entry"


# ── Test K3-8a — Read-only assertion ─────────────────────────────────────────


async def test_kpi3_contaminated_allowed_methods_raises_before_any_rpc(
    monkeypatch: pytest.MonkeyPatch,
    mock_client_kpi3: MagicMock,
) -> None:
    monkeypatch.setattr(
        "backend.modules.collections.services.kpi_service.ALLOWED_METHODS",
        frozenset({"read_group", "write"}),
    )

    with pytest.raises(ReadOnlyViolationError):
        await get_pending_check_exposure(client=mock_client_kpi3)

    mock_client_kpi3.execute_kw.assert_not_called()


async def test_kpi3_clean_allowed_methods_does_not_raise(mock_client_kpi3: MagicMock) -> None:
    result = await get_pending_check_exposure(client=mock_client_kpi3)
    assert result["value"] >= 0.0


# ── Test K3-9 — Edge case: zero result ───────────────────────────────────────


async def test_kpi3_zero_result_returns_zero_value_and_no_warning(
    mock_client_kpi3: MagicMock,
) -> None:
    # Odoo returns a single row with all zeros — possible if no installments posted.
    mock_client_kpi3.execute_kw = AsyncMock(return_value=[{
        "paid_amount": 0.0,
        "x_studio_actual_paid_amount": 0.0,
        "__count": 0,
    }])

    result = await get_pending_check_exposure(client=mock_client_kpi3)

    assert result["value"] == pytest.approx(0.0)
    assert result["paid_amount_sum"] == pytest.approx(0.0)
    assert result["actual_paid_sum"] == pytest.approx(0.0)
    assert result["record_count"] == 0
    # Zero is not negative — no data quality warning
    assert result["data_quality_warning"] is None


# ── Test K3-10 — Edge case: negative derived value (Decision 4.4 Option A) ───
#
# When paid_amount_sum < actual_paid_sum, the service:
#   (a) returns value as-is (not clamped to 0.0)
#   (b) logs a WARNING via logger.warning(...)
#   (c) sets data_quality_warning = "value_is_negative"
#
# loguru does not integrate with pytest's caplog without a propagation bridge.
# We use patch() on the module-level logger name to verify the warning call.


async def test_kpi3_negative_derived_value_option_a(
    mock_client_kpi3: MagicMock,
) -> None:
    # actual_paid > paid → derived value is negative (Studio field anomaly)
    mock_client_kpi3.execute_kw = AsyncMock(return_value=[{
        "paid_amount": 100.0,
        "x_studio_actual_paid_amount": 200.0,
        "__count": 5,
    }])

    with patch(
        "backend.modules.collections.services.kpi_service.logger"
    ) as mock_logger:
        result = await get_pending_check_exposure(client=mock_client_kpi3)

    # (a) Value returned as-is — Decision 4.4 Option A, no clamping
    assert result["value"] == pytest.approx(-100.0), (
        "Negative value must be returned as-is (Decision 4.4 Option A), not clamped."
    )
    # (b) WARNING was logged once
    mock_logger.warning.assert_called_once()
    warning_message: str = mock_logger.warning.call_args[0][0]
    assert "negative" in warning_message.lower(), (
        f"Warning message must mention 'negative', got: {warning_message!r}"
    )
    # (c) data_quality_warning flag set
    assert result["data_quality_warning"] == "value_is_negative", (
        "data_quality_warning must be 'value_is_negative' when value < 0."
    )
    # Operands preserved for traceability
    assert result["paid_amount_sum"] == pytest.approx(100.0)
    assert result["actual_paid_sum"] == pytest.approx(200.0)


# ══════════════════════════════════════════════════════════════════════════════
# KPI 6 — 6-Month Collection Trend (get_collection_trend_6m)
# ════════════════════════���═════════════════════════════════════════════════════
#
# Architecture (Decision 5.6): queries rs.account.payment.installment (HEADER)
# grouped by date:month. Always returns 6 entries oldest-first, zero-padding
# months Odoo doesn't return. TTL = 3600s (Decision 5.2 / 5.4).
#
# Operational context (Decision 5.7): during the current data-entry period,
# only December 2025 has payment data. Jan-May 2026 are legitimately zero.
# The "5 of 6 months are zero" scenario is an explicit test case.
# ��═════════════════════════════════════════════════════════════════════════════

# D0 Part 1 discovery baseline — December 2025 only.
# Odoo returns groupby keys as English full-month names.
_MOCK_RESPONSE_KPI6_DEC_ONLY = [
    {
        "date:month": "December 2025",
        "__count": 431,
        "amount": 47_465_098.00,
    }
]

# Simulates a future state where all 6 months have data (all non-zero).
_MOCK_RESPONSE_KPI6_ALL_6 = [
    {"date:month": "December 2025", "__count": 431, "amount": 47_465_098.00},
    {"date:month": "January 2026",  "__count": 120, "amount": 15_000_000.00},
    {"date:month": "February 2026", "__count": 98,  "amount": 12_000_000.00},
    {"date:month": "March 2026",    "__count": 210, "amount": 22_000_000.00},
    {"date:month": "April 2026",    "__count": 185, "amount": 19_000_000.00},
    {"date:month": "May 2026",      "__count": 55,  "amount":  5_000_000.00},
]

_MOCK_RESPONSE_KPI6_EMPTY = []   # no payment records in window at all

# Fixed date for deterministic period computation: 2026-05-17
# → period_start = 2025-12-01, months = 2025-12 … 2026-05 (6 entries)
_KPI6_TODAY = "2026-05-17"
_KPI6_EXPECTED_MONTHS = [
    "2025-12", "2026-01", "2026-02", "2026-03", "2026-04", "2026-05",
]

# For year-wrap test: today = 2026-03-10
# → period_start = 2025-10-01, months = 2025-10 … 2026-03
_KPI6_TODAY_MAR = "2026-03-10"
_KPI6_EXPECTED_MONTHS_MAR = [
    "2025-10", "2025-11", "2025-12", "2026-01", "2026-02", "2026-03",
]


@pytest.fixture
def mock_client_kpi6() -> MagicMock:
    client = MagicMock()
    client.execute_kw = AsyncMock(return_value=_MOCK_RESPONSE_KPI6_DEC_ONLY)
    return client


# ── Test K6-1 — Domain: 3-clause with state=post and date range ───────────────


async def test_kpi6_domain_has_state_post_and_date_range(
    mock_client_kpi6: MagicMock,
) -> None:
    with patch(
        "backend.modules.collections.services.cache.today_str",
        return_value=_KPI6_TODAY,
    ):
        await get_collection_trend_6m(client=mock_client_kpi6)

    call_args = mock_client_kpi6.execute_kw.call_args
    domain = call_args.kwargs["args"][0]

    assert len(domain) == 3, f"Expected 3-clause domain, got {len(domain)}: {domain}"
    assert domain[0] == ("state", "=", "post")
    assert domain[1][0] == "date"
    assert domain[1][1] == ">="
    assert domain[1][2] == "2025-12-01"   # period_start for 2026-05-17
    assert domain[2][0] == "date"
    assert domain[2][1] == "<="
    assert domain[2][2].startswith("2026-05-17")  # period_end + " 23:59:59"


# ── Test K6-2 — Uses HEADER model with date:month groupby ────────────────────


async def test_kpi6_uses_header_model_with_date_month_groupby(
    mock_client_kpi6: MagicMock,
) -> None:
    with patch(
        "backend.modules.collections.services.cache.today_str",
        return_value=_KPI6_TODAY,
    ):
        await get_collection_trend_6m(client=mock_client_kpi6)

    call_args = mock_client_kpi6.execute_kw.call_args
    assert call_args.args[0] == _PAYMENT_HEADER_MODEL, (
        f"Expected model {_PAYMENT_HEADER_MODEL!r}, got {call_args.args[0]!r}"
    )
    assert call_args.args[1] == "read_group"
    groupby = call_args.kwargs["args"][2]
    assert groupby == ["date:month"], f"Expected groupby=['date:month'], got {groupby!r}"


# ── Test K6-3 — Return shape: all top-level keys present ───────��─────────────


async def test_kpi6_return_shape_has_all_required_keys(
    mock_client_kpi6: MagicMock,
) -> None:
    with patch(
        "backend.modules.collections.services.cache.today_str",
        return_value=_KPI6_TODAY,
    ):
        result = await get_collection_trend_6m(client=mock_client_kpi6)

    expected = {
        "months", "total_6m", "total_record_count", "average_monthly",
        "period_start", "period_end", "currency", "as_of",
        "cache_status", "cache_ttl_seconds", "rpc_duration_ms", "domain",
    }
    assert set(result.keys()) == expected

    assert isinstance(result["months"], list)
    assert result["currency"] == "EGP"
    assert result["cache_ttl_seconds"] == 3600
    assert result["cache_status"] in {"fresh", "cached"}
    assert isinstance(result["rpc_duration_ms"], int)

    for entry in result["months"]:
        assert set(entry.keys()) == {"month", "label_en", "label_ar", "amount", "record_count"}


# ── Test K6-4 — Always exactly 6 month entries ───────��───────────────────────


async def test_kpi6_always_returns_exactly_6_month_entries(
    mock_client_kpi6: MagicMock,
) -> None:
    with patch(
        "backend.modules.collections.services.cache.today_str",
        return_value=_KPI6_TODAY,
    ):
        result = await get_collection_trend_6m(client=mock_client_kpi6)

    assert len(result["months"]) == 6, (
        f"Must always return 6 entries regardless of Odoo response, got {len(result['months'])}"
    )


# ── Test K6-5 — 5-of-6-zero scenario (Decision 5.7) ─────────────────────────


async def test_kpi6_five_of_six_months_zero_current_operational_state(
    mock_client_kpi6: MagicMock,
) -> None:
    """Operational state as of 2026-05-17: only December 2025 has data.
    Remaining 5 months must be zero-padded, not omitted or errored.
    Decision 5.7: zero months are truthful data, not bugs.
    """
    with patch(
        "backend.modules.collections.services.cache.today_str",
        return_value=_KPI6_TODAY,
    ):
        result = await get_collection_trend_6m(client=mock_client_kpi6)

    months = result["months"]
    assert len(months) == 6

    dec = months[0]
    assert dec["month"] == "2025-12"
    assert dec["amount"] == pytest.approx(47_465_098.00)
    assert dec["record_count"] == 431

    for entry in months[1:]:
        assert entry["amount"] == 0.0, (
            f"Month {entry['month']} should be zero-padded, got {entry['amount']}"
        )
        assert entry["record_count"] == 0


# ── Test K6-6 — Months ordered oldest-first, correct YYYY-MM keys ────────────


async def test_kpi6_months_ordered_oldest_first_with_correct_ym_keys(
    mock_client_kpi6: MagicMock,
) -> None:
    with patch(
        "backend.modules.collections.services.cache.today_str",
        return_value=_KPI6_TODAY,
    ):
        result = await get_collection_trend_6m(client=mock_client_kpi6)

    actual_months = [e["month"] for e in result["months"]]
    assert actual_months == _KPI6_EXPECTED_MONTHS, (
        f"Expected {_KPI6_EXPECTED_MONTHS}, got {actual_months}"
    )


async def test_kpi6_period_wraps_correctly_across_year_boundary() -> None:
    """today = 2026-03-10: period must span Oct 2025 – Mar 2026 (year wrap)."""
    mock_c = MagicMock()
    mock_c.execute_kw = AsyncMock(return_value=[])

    with patch(
        "backend.modules.collections.services.cache.today_str",
        return_value=_KPI6_TODAY_MAR,
    ):
        result = await get_collection_trend_6m(client=mock_c)

    actual_months = [e["month"] for e in result["months"]]
    assert actual_months == _KPI6_EXPECTED_MONTHS_MAR, (
        f"Expected {_KPI6_EXPECTED_MONTHS_MAR}, got {actual_months}"
    )
    assert result["period_start"] == "2025-10-01"
    assert result["period_end"] == "2026-03-10"


# ── Test K6-7 — Aggregation math ─────────────────────────────────────────────


async def test_kpi6_total_6m_equals_sum_of_month_amounts(
    mock_client_kpi6: MagicMock,
) -> None:
    mock_client_kpi6.execute_kw = AsyncMock(return_value=_MOCK_RESPONSE_KPI6_ALL_6)

    with patch(
        "backend.modules.collections.services.cache.today_str",
        return_value=_KPI6_TODAY,
    ):
        result = await get_collection_trend_6m(client=mock_client_kpi6)

    computed = sum(e["amount"] for e in result["months"])
    assert result["total_6m"] == pytest.approx(computed)
    assert result["total_record_count"] == sum(e["record_count"] for e in result["months"])


async def test_kpi6_average_monthly_equals_total_divided_by_6(
    mock_client_kpi6: MagicMock,
) -> None:
    """average_monthly must always divide by 6 — even months with zero amount count."""
    mock_client_kpi6.execute_kw = AsyncMock(return_value=_MOCK_RESPONSE_KPI6_DEC_ONLY)

    with patch(
        "backend.modules.collections.services.cache.today_str",
        return_value=_KPI6_TODAY,
    ):
        result = await get_collection_trend_6m(client=mock_client_kpi6)

    expected_avg = result["total_6m"] / 6
    assert result["average_monthly"] == pytest.approx(expected_avg), (
        "average_monthly must be total_6m / 6 (denominator is always 6, including zero months)"
    )


# ── Test K6-8 — Month entry labels ────────────���──────────────────────────────


async def test_kpi6_month_labels_are_correct_for_december_2025(
    mock_client_kpi6: MagicMock,
) -> None:
    with patch(
        "backend.modules.collections.services.cache.today_str",
        return_value=_KPI6_TODAY,
    ):
        result = await get_collection_trend_6m(client=mock_client_kpi6)

    dec = result["months"][0]
    assert dec["label_en"] == "Dec 2025"
    assert dec["label_ar"] == "ديسمبر"

    jan = result["months"][1]
    assert jan["label_en"] == "Jan 2026"
    assert jan["label_ar"] == "يناير"


# ── Test K6-9 — cache_ttl_seconds == 3600 ────────────────────────────────────


async def test_kpi6_cache_ttl_seconds_is_3600(mock_client_kpi6: MagicMock) -> None:
    with patch(
        "backend.modules.collections.services.cache.today_str",
        return_value=_KPI6_TODAY,
    ):
        result = await get_collection_trend_6m(client=mock_client_kpi6)

    assert result["cache_ttl_seconds"] == 3600, (
        f"KPI 6 must report cache_ttl_seconds=3600 (hourly), got {result['cache_ttl_seconds']}"
    )


# ─�� Test K6-10 — Cache hit ───────────────────��────────────────────────────────


async def test_kpi6_second_call_is_served_from_cache(mock_client_kpi6: MagicMock) -> None:
    with patch(
        "backend.modules.collections.services.cache.today_str",
        return_value=_KPI6_TODAY,
    ):
        result1 = await get_collection_trend_6m(client=mock_client_kpi6)
        result2 = await get_collection_trend_6m(client=mock_client_kpi6)

    assert mock_client_kpi6.execute_kw.call_count == 1
    assert result1["cache_status"] == "fresh"
    assert result2["cache_status"] == "cached"
    assert result2["rpc_duration_ms"] == 0
    assert result2["total_6m"] == result1["total_6m"]
    assert result2["cache_ttl_seconds"] == 3600


# ── Test K6-11 — Cache key independence from other KPIs ──────────────────────


async def test_kpi6_cache_key_does_not_collide_with_other_kpis() -> None:
    mock_k2 = MagicMock()
    mock_k2.execute_kw = AsyncMock(return_value=_MOCK_RESPONSE)
    mock_k6 = MagicMock()
    mock_k6.execute_kw = AsyncMock(return_value=_MOCK_RESPONSE_KPI6_DEC_ONLY)

    with patch(
        "backend.modules.collections.services.cache.today_str",
        return_value=_KPI6_TODAY,
    ):
        r2  = await get_late_uncollected(client=mock_k2)
        r6  = await get_collection_trend_6m(client=mock_k6)
        r2b = await get_late_uncollected(client=mock_k2)
        r6b = await get_collection_trend_6m(client=mock_k6)

    assert r2["cache_status"]  == "fresh"
    assert r6["cache_status"]  == "fresh"
    assert r2b["cache_status"] == "cached"
    assert r6b["cache_status"] == "cached"
    assert mock_k2.execute_kw.call_count == 1
    assert mock_k6.execute_kw.call_count == 1


# ── Test K6-12 — RPC failure ──────────────────���───────────────────────────────


async def test_kpi6_rpc_failure_raises_odoo_query_error(mock_client_kpi6: MagicMock) -> None:
    mock_client_kpi6.execute_kw.side_effect = RuntimeError("connection refused")

    with patch(
        "backend.modules.collections.services.cache.today_str",
        return_value=_KPI6_TODAY,
    ):
        with pytest.raises(OdooQueryError):
            await get_collection_trend_6m(client=mock_client_kpi6)


async def test_kpi6_rpc_failure_writes_no_cache_entry(mock_client_kpi6: MagicMock) -> None:
    mock_client_kpi6.execute_kw.side_effect = RuntimeError("timeout")

    with patch(
        "backend.modules.collections.services.cache.today_str",
        return_value=_KPI6_TODAY,
    ):
        with pytest.raises(OdooQueryError):
            await get_collection_trend_6m(client=mock_client_kpi6)

    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_KPI6)
    assert _cache.get(cache_key) is None, "A failed RPC must not leave a cache entry"


# ── Test K6-13 — period_start and period_end in response ─────────────────────


async def test_kpi6_period_start_and_end_in_response(mock_client_kpi6: MagicMock) -> None:
    with patch(
        "backend.modules.collections.services.cache.today_str",
        return_value=_KPI6_TODAY,
    ):
        result = await get_collection_trend_6m(client=mock_client_kpi6)

    assert result["period_start"] == "2025-12-01"
    assert result["period_end"]   == "2026-05-17"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", result["period_start"])
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", result["period_end"])


# ── Test K6-14 — Empty window: all 6 entries zero ────────────────────────────


async def test_kpi6_all_zero_when_no_payment_records_in_window(
    mock_client_kpi6: MagicMock,
) -> None:
    """Odoo returns empty list (no payments in window) — all 6 months must be zero."""
    mock_client_kpi6.execute_kw = AsyncMock(return_value=_MOCK_RESPONSE_KPI6_EMPTY)

    with patch(
        "backend.modules.collections.services.cache.today_str",
        return_value=_KPI6_TODAY,
    ):
        result = await get_collection_trend_6m(client=mock_client_kpi6)

    assert len(result["months"]) == 6
    assert result["total_6m"] == 0.0
    assert result["total_record_count"] == 0
    assert result["average_monthly"] == 0.0
    for entry in result["months"]:
        assert entry["amount"] == 0.0
        assert entry["record_count"] == 0
