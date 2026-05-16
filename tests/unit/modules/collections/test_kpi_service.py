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
    _CACHE_KEY_PREFIX_KPI1,
    get_late_uncollected,
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
