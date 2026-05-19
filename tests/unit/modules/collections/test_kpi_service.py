"""
Unit tests for Collections KPI service — get_late_uncollected.

OdooClient is fully mocked; no live Odoo connection is made.
Live verification is the job of scripts/verify_kpi2_live.py.
"""

import re
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from backend.core.exceptions import OdooQueryError, ReadOnlyViolationError
from backend.modules.collections.services import cache as _cache
from backend.modules.collections.services.kpi_service import (
    _CACHE_KEY_PREFIX,
    _CACHE_KEY_PREFIX_KPI1,
    _CACHE_KEY_PREFIX_KPI4,
    _CACHE_KEY_PREFIX_KPI6,
    _CACHE_KEY_PREFIX_KPI7,
    _PAYMENT_HEADER_MODEL,
    get_collection_rate_mtd_ytd,
    get_collection_trend_6m,
    get_expected_collections_forecast,
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
        "cheques_in_pipeline", "cheques_record_count",
        "drill_down_domain", "cheques_drill_down_domain",
        "data_quality_warning",
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


# ── Test K2-A — cheques_in_pipeline field ────────────────────────────────────


async def test_kpi2_response_includes_cheques_in_pipeline(mock_client: MagicMock) -> None:
    result = await get_late_uncollected(client=mock_client)

    assert isinstance(result["cheques_in_pipeline"], float)
    assert result["cheques_in_pipeline"] >= 0
    assert result["cheques_in_pipeline"] <= result["value"]


# ── Test K2-B — drill_down_domain matches Candidate C ────────────────────────


async def test_kpi2_response_includes_drill_down_domain_matching_candidate_c(
    mock_client: MagicMock,
) -> None:
    with patch(
        "backend.modules.collections.services.cache.today_str",
        return_value="2026-05-19",
    ):
        result = await get_late_uncollected(client=mock_client)

    today_str = "2026-05-19"
    expected_domain = [
        ("state", "=", "post"),
        ("payment_state", "in", ["unpaid", "partial"]),
        ("date", "<", today_str),
    ]
    assert result["drill_down_domain"] == expected_domain
    assert result["drill_down_domain"] == result["domain"]


# ── Test K2-C — null Alt B fields ────────────────────────────────────────────


async def test_kpi2_cheques_record_count_is_null(mock_client: MagicMock) -> None:
    result = await get_late_uncollected(client=mock_client)

    assert result["cheques_record_count"] is None
    assert result["cheques_drill_down_domain"] is None


# ── Test K2-D — negative cheques anomaly triggers data_quality_warning ───────


async def test_kpi2_negative_cheques_triggers_data_quality_warning() -> None:
    mock_client = MagicMock()
    mock_client.execute_kw = AsyncMock(return_value=[{
        "due_amount": 100_000.0,
        "__count": 5,
        "amount": 120_000.0,
        "paid_amount": 10_000.0,
        "x_studio_actual_paid_amount": 15_000.0,  # actual > paid = anomaly
    }])

    result = await get_late_uncollected(client=mock_client)

    assert result["cheques_in_pipeline"] == 0.0
    assert result["data_quality_warning"] == "negative_cheques"


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

# search_read format: individual records with date (UTC string, "YYYY-MM-DD HH:MM:SS") and amount.
# Python-side grouping converts each UTC datetime to Egypt local time before bucketing.
#
# December 2025 only — 3 records all in Egypt-local December.
#   id=3869: "2025-11-30 22:00:00" UTC = "2025-12-01 00:00:00" Egypt (UTC+2 winter) → Dec
#   mid-month and late-month records stay clearly in December.
_MOCK_RESPONSE_KPI6_DEC_ONLY = [
    {"date": "2025-11-30 22:00:00", "amount":  99_114.00},  # Dec 1 midnight Egypt (UTC+2)
    {"date": "2025-12-15 10:00:00", "amount": 100_000.00},  # Dec 15 midday Egypt
    {"date": "2025-12-20 08:00:00", "amount": 200_000.00},  # Dec 20 morning Egypt
]
# Egypt-local totals: December 2025 = 399,114.00 EGP / 3 records

# Simulates a future state where all 6 months have data (all non-zero).
# One record per month; May uses UTC+3 (Egypt summer DST).
_MOCK_RESPONSE_KPI6_ALL_6 = [
    {"date": "2025-12-15 10:00:00", "amount": 47_465_098.00},  # Dec (UTC+2) → Dec 2025
    {"date": "2026-01-15 10:00:00", "amount": 15_000_000.00},  # Jan (UTC+2) → Jan 2026
    {"date": "2026-02-15 10:00:00", "amount": 12_000_000.00},  # Feb (UTC+2) → Feb 2026
    {"date": "2026-03-15 10:00:00", "amount": 22_000_000.00},  # Mar (UTC+2) → Mar 2026
    {"date": "2026-04-15 10:00:00", "amount": 19_000_000.00},  # Apr (UTC+2) → Apr 2026
    {"date": "2026-05-15 07:00:00", "amount":  5_000_000.00},  # May (UTC+3) 10:00 Egypt → May 2026
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
    # Decision 5.9: boundaries are UTC. Egypt observes DST (UTC+2 Nov-Apr, UTC+3 May-Oct).
    # 2025-12-01 00:00:00 Africa/Cairo (winter, UTC+2) = 2025-11-30 22:00:00 UTC
    # 2026-05-17 23:59:59 Africa/Cairo (summer, UTC+3) = 2026-05-17 20:59:59 UTC
    assert domain[1][2] == "2025-11-30 22:00:00"
    assert domain[2][0] == "date"
    assert domain[2][1] == "<="
    assert domain[2][2] == "2026-05-17 20:59:59"


# ── Test K6-1b — UTC boundary computation (Decision 5.9) ─────────────────────


async def test_kpi6_domain_boundaries_are_utc_offset(
    mock_client_kpi6: MagicMock,
) -> None:
    """Egypt observes DST (UTC+2 Nov-Apr, UTC+3 May-Oct). ZoneInfo applies
    the correct offset per date.

    today = 2026-03-10 (March, winter, UTC+2):
      period_start = 2025-10-01 Africa/Cairo (summer, UTC+3) = 2025-09-30 21:00:00 UTC
      period_end   = 2026-03-10 23:59:59 Africa/Cairo (winter, UTC+2) = 2026-03-10 21:59:59 UTC
    """
    with patch(
        "backend.modules.collections.services.cache.today_str",
        return_value=_KPI6_TODAY_MAR,
    ):
        await get_collection_trend_6m(client=mock_client_kpi6)

    call_args = mock_client_kpi6.execute_kw.call_args
    domain = call_args.kwargs["args"][0]

    assert domain[1][2] == "2025-09-30 21:00:00", (
        f"start_utc wrong: {domain[1][2]!r} — expected 2025-09-30 21:00:00 (Oct summer UTC+3)"
    )
    assert domain[2][2] == "2026-03-10 21:59:59", (
        f"end_utc wrong: {domain[2][2]!r} — expected 2026-03-10 21:59:59 (Mar winter UTC+2)"
    )


# ── Test K6-2 — Uses HEADER model via search_read with date+amount fields ─────


async def test_kpi6_uses_header_model_search_read_with_correct_fields(
    mock_client_kpi6: MagicMock,
) -> None:
    """Decision 5.10: search_read replaces read_group so Python can regroup
    records by Egypt local month rather than Odoo's UTC-based date:month key."""
    with patch(
        "backend.modules.collections.services.cache.today_str",
        return_value=_KPI6_TODAY,
    ):
        await get_collection_trend_6m(client=mock_client_kpi6)

    call_args = mock_client_kpi6.execute_kw.call_args
    assert call_args.args[0] == _PAYMENT_HEADER_MODEL, (
        f"Expected model {_PAYMENT_HEADER_MODEL!r}, got {call_args.args[0]!r}"
    )
    assert call_args.args[1] == "search_read", (
        f"Must use search_read (not read_group) per Decision 5.10, got {call_args.args[1]!r}"
    )
    fields = call_args.kwargs["args"][1]
    assert set(fields) == {"date", "amount"}, (
        f"search_read must request exactly ['date', 'amount'], got {fields!r}"
    )


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
    assert dec["amount"] == pytest.approx(399_114.00)   # 99_114 + 100_000 + 200_000
    assert dec["record_count"] == 3

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


# ── Test K6-15 — Egypt-local regrouping of UTC midnight boundary records ──────


async def test_kpi6_utc_midnight_records_bucketed_by_egypt_local_month() -> None:
    """Records stored at UTC midnight that correspond to Egypt-local-midnight of
    the next calendar day must land in the Egypt local month, not the UTC month.

    Decision 5.10: Python-side regrouping using Africa/Cairo timezone.
      "2025-11-30 22:00:00" UTC = "2025-12-01 00:00:00" Egypt (UTC+2) → December 2025
      "2025-12-31 22:00:00" UTC = "2026-01-01 00:00:00" Egypt (UTC+2) → January 2026
      "2025-12-15 10:00:00" UTC = "2025-12-15 12:00:00" Egypt            → December 2025
    """
    boundary_records = [
        {"date": "2025-11-30 22:00:00", "amount": 10_000.00},  # Dec 1 midnight Egypt → Dec
        {"date": "2025-12-31 22:00:00", "amount": 20_000.00},  # Jan 1 midnight Egypt → Jan
        {"date": "2025-12-15 10:00:00", "amount": 30_000.00},  # Dec 15 midday Egypt  → Dec
    ]
    mock_c = MagicMock()
    mock_c.execute_kw = AsyncMock(return_value=boundary_records)

    with patch(
        "backend.modules.collections.services.cache.today_str",
        return_value=_KPI6_TODAY,  # 2026-05-17 → window: 2025-12 … 2026-05
    ):
        result = await get_collection_trend_6m(client=mock_c)

    by_key = {e["month"]: e for e in result["months"]}

    # Record 1 and 3 → Egypt local December → bucketed to 2025-12
    assert by_key["2025-12"]["amount"] == pytest.approx(40_000.00), (
        "2025-11-30 22:00 UTC and 2025-12-15 10:00 UTC must both land in December 2025"
    )
    assert by_key["2025-12"]["record_count"] == 2

    # Record 2 → Egypt local January → bucketed to 2026-01
    assert by_key["2026-01"]["amount"] == pytest.approx(20_000.00), (
        "2025-12-31 22:00 UTC = 2026-01-01 00:00 Egypt must land in January 2026"
    )
    assert by_key["2026-01"]["record_count"] == 1


# ── Test K6-16 — Summer DST boundary uses UTC+3 (Africa/Cairo May–Oct) ───────


async def test_kpi6_summer_dst_midnight_bucketed_to_next_local_day() -> None:
    """In summer (May–Oct), Egypt is UTC+3 per tzdata 2025.2.
    A record at "2026-06-30 21:00:00" UTC = "2026-07-01 00:00:00" Egypt (UTC+3)
    must land in July 2026, not June 2026.

    Uses today=2026-07-31 so the 6-month window is 2026-02 … 2026-07.
    """
    summer_records = [
        {"date": "2026-06-30 21:00:00", "amount": 50_000.00},  # Jul 1 midnight Egypt (UTC+3)
        {"date": "2026-06-15 10:00:00", "amount": 25_000.00},  # Jun 15 13:00 Egypt → June
    ]
    mock_c = MagicMock()
    mock_c.execute_kw = AsyncMock(return_value=summer_records)

    with patch(
        "backend.modules.collections.services.cache.today_str",
        return_value="2026-07-31",  # window: 2026-02 … 2026-07
    ):
        result = await get_collection_trend_6m(client=mock_c)

    by_key = {e["month"]: e for e in result["months"]}

    # "2026-06-30 21:00:00 UTC" → Egypt UTC+3 = 2026-07-01 00:00:00 → July 2026
    assert by_key["2026-07"]["amount"] == pytest.approx(50_000.00), (
        "2026-06-30 21:00 UTC must land in July 2026 (UTC+3 summer offset)"
    )
    assert by_key["2026-07"]["record_count"] == 1

    # "2026-06-15 10:00:00 UTC" → Egypt UTC+3 = 2026-06-15 13:00:00 → June 2026
    assert by_key["2026-06"]["amount"] == pytest.approx(25_000.00), (
        "2026-06-15 10:00 UTC must stay in June 2026"
    )
    assert by_key["2026-06"]["record_count"] == 1


# ══════════════════════════════════════════════════════════════════════════════
# KPI 4 — Collection Rate MTD & YTD (get_collection_rate_mtd_ytd)
# ══════════════════════════════════════════════════════════════════════════════
#
# Architecture (Decision 6.1): 4 sequential read_group RPCs.
#   Q1 — MTD numerator  : rs.account.payment.installment, UTC datetime bounds
#   Q2 — MTD denominator: rs.installment, ISO date bounds
#   Q3 — YTD numerator  : rs.account.payment.installment, UTC datetime bounds
#   Q4 — YTD denominator: rs.installment, ISO date bounds
#
# Zero denominator → rate_percent: None (Decision 6.3).
# YTD period: calendar year Jan 1 → today (Decision 6.2).
# UTC boundaries: _tz_period_bounds() via Africa/Cairo ZoneInfo (Decision 5.9).
# ══════════════════════════════════════════════════════════════════════════════

# Four sequential read_group calls → four entries in side_effect.
# Happy-path values chosen to produce simple round rates (50%, 40%).
_MOCK_KPI4_Q1 = [{"amount": 10_000_000.00, "__count":  5}]   # MTD num
_MOCK_KPI4_Q2 = [{"amount": 20_000_000.00, "__count": 50}]   # MTD den
_MOCK_KPI4_Q3 = [{"amount": 80_000_000.00, "__count": 30}]   # YTD num
_MOCK_KPI4_Q4 = [{"amount": 200_000_000.00, "__count": 200}]  # YTD den

_KPI4_TODAY = "2026-05-17"  # Fixed date for deterministic period computation


@pytest.fixture
def mock_client_kpi4() -> MagicMock:
    client = MagicMock()
    client.execute_kw = AsyncMock(side_effect=[
        _MOCK_KPI4_Q1, _MOCK_KPI4_Q2, _MOCK_KPI4_Q3, _MOCK_KPI4_Q4,
    ])
    return client


# ── Test K4-01 — Happy path: full return shape + correct rates ────────────────


async def test_kpi4_happy_path_full_shape_and_rates(mock_client_kpi4: MagicMock) -> None:
    with patch("backend.modules.collections.services.cache.today_str",
               return_value=_KPI4_TODAY):
        result = await get_collection_rate_mtd_ytd(client=mock_client_kpi4)

    # Exactly 4 RPCs on first call
    assert mock_client_kpi4.execute_kw.call_count == 4

    # Top-level keys
    expected_top = {"mtd", "ytd", "ytd_period_assumption", "currency",
                    "as_of", "cache_status", "rpc_duration_ms"}
    assert set(result.keys()) == expected_top

    # Inner shape (both periods have identical key sets)
    inner_keys = {"numerator_egp", "denominator_egp", "rate_percent",
                  "period_start", "period_end", "record_count_num", "record_count_den"}
    assert set(result["mtd"].keys()) == inner_keys
    assert set(result["ytd"].keys()) == inner_keys

    # Fixed-value assertions
    assert result["currency"] == "EGP"
    assert result["ytd_period_assumption"] == "calendar_year"
    assert result["cache_status"] == "fresh"
    assert isinstance(result["rpc_duration_ms"], int)
    assert result["rpc_duration_ms"] >= 0

    # Period dates
    assert result["mtd"]["period_start"] == "2026-05-01"
    assert result["mtd"]["period_end"]   == _KPI4_TODAY
    assert result["ytd"]["period_start"] == "2026-01-01"
    assert result["ytd"]["period_end"]   == _KPI4_TODAY

    # Numeric correctness
    assert result["mtd"]["numerator_egp"]   == pytest.approx(10_000_000.00)
    assert result["mtd"]["denominator_egp"] == pytest.approx(20_000_000.00)
    assert result["mtd"]["rate_percent"]    == pytest.approx(50.0)
    assert result["mtd"]["record_count_num"] == 5
    assert result["mtd"]["record_count_den"] == 50

    assert result["ytd"]["numerator_egp"]   == pytest.approx(80_000_000.00)
    assert result["ytd"]["denominator_egp"] == pytest.approx(200_000_000.00)
    assert result["ytd"]["rate_percent"]    == pytest.approx(40.0)
    assert result["ytd"]["record_count_num"] == 30
    assert result["ytd"]["record_count_den"] == 200


# ── Test K4-02 — Zero denominator → rate_percent: None (Decision 6.3) ─────────


async def test_kpi4_zero_denominator_returns_none_rate() -> None:
    """When denominator (rs.installment.amount) = 0, rate_percent must be None.
    Zero denominator means no installments were due in the period.
    Frontend renders "—" (Decision 6.3).
    """
    mock = MagicMock()
    mock.execute_kw = AsyncMock(side_effect=[
        [{"amount": 5_000.00, "__count": 1}],   # MTD num — non-zero
        [{"amount": 0.00,     "__count": 0}],   # MTD den — zero → None
        [{"amount": 5_000.00, "__count": 1}],   # YTD num
        [{"amount": 0.00,     "__count": 0}],   # YTD den — zero → None
    ])

    with patch("backend.modules.collections.services.cache.today_str",
               return_value=_KPI4_TODAY):
        result = await get_collection_rate_mtd_ytd(client=mock)

    assert result["mtd"]["rate_percent"] is None, (
        "rate_percent must be None when denominator == 0 (Decision 6.3)"
    )
    assert result["ytd"]["rate_percent"] is None


# ── Test K4-03 — Zero numerator → rate_percent == 0.0 ────────────────────────


async def test_kpi4_zero_numerator_returns_zero_rate() -> None:
    """Zero numerator (no payments posted) + non-zero denominator → rate 0.0%.
    This is the expected current state during the data-entry phase (Decision 5.7 analog).
    """
    mock = MagicMock()
    mock.execute_kw = AsyncMock(side_effect=[
        [{"amount": 0.00,          "__count": 0}],    # MTD num = 0
        [{"amount": 43_653_133.00, "__count": 263}],  # MTD den (D0 baseline)
        [{"amount": 0.00,          "__count": 0}],    # YTD num = 0
        [{"amount": 302_882_977.00,"__count": 1861}], # YTD den (D0 baseline)
    ])

    with patch("backend.modules.collections.services.cache.today_str",
               return_value=_KPI4_TODAY):
        result = await get_collection_rate_mtd_ytd(client=mock)

    assert result["mtd"]["rate_percent"] == pytest.approx(0.0), (
        "Zero numerator with non-zero denominator must yield rate_percent=0.0, not None"
    )
    assert result["ytd"]["rate_percent"] == pytest.approx(0.0)
    assert result["mtd"]["numerator_egp"] == pytest.approx(0.0)
    assert result["ytd"]["numerator_egp"] == pytest.approx(0.0)


# ── Test K4-04 — Prepayment: numerator > denominator → rate > 100% ────────────


async def test_kpi4_prepayment_rate_exceeds_100_percent() -> None:
    """When payments collected exceed installments due (prepayment scenario),
    rate_percent must exceed 100%. This is valid business behavior, not a bug.
    """
    mock = MagicMock()
    mock.execute_kw = AsyncMock(side_effect=[
        [{"amount": 150_000.00,   "__count":  3}],   # MTD num > den
        [{"amount": 100_000.00,   "__count":  2}],   # MTD den
        [{"amount": 1_500_000.00, "__count": 15}],   # YTD num > den
        [{"amount": 1_000_000.00, "__count": 10}],   # YTD den
    ])

    with patch("backend.modules.collections.services.cache.today_str",
               return_value=_KPI4_TODAY):
        result = await get_collection_rate_mtd_ytd(client=mock)

    assert result["mtd"]["rate_percent"] == pytest.approx(150.0), (
        "Prepayment (num > den) must yield rate_percent > 100, got "
        f"{result['mtd']['rate_percent']}"
    )
    assert result["ytd"]["rate_percent"] == pytest.approx(150.0)


# ── Test K4-05 — MTD UTC boundary: Egypt summer DST (UTC+3, May 2026) ─────────


async def test_kpi4_mtd_numerator_domain_uses_summer_dst_utc_boundaries() -> None:
    """MTD period start/end UTC boundaries use Egypt DST-aware conversion.

    today = 2026-05-01 (first day of May — Egypt summer, UTC+3):
      MTD start Egypt : 2026-05-01 00:00:00 Africa/Cairo (UTC+3) = 2026-04-30 21:00:00 UTC
      MTD end Egypt   : 2026-05-01 23:59:59 Africa/Cairo (UTC+3) = 2026-05-01 20:59:59 UTC

    A naive boundary ("2026-05-01 00:00:00") would exclude receipts recorded
    at Egypt midnight that are stored at "2026-04-30 21:00:00" UTC.
    Decision 5.9.
    """
    mock = MagicMock()
    mock.execute_kw = AsyncMock(return_value=[{"amount": 0.0, "__count": 0}])

    with patch("backend.modules.collections.services.cache.today_str",
               return_value="2026-05-01"):
        await get_collection_rate_mtd_ytd(client=mock)

    # Q1 = call index 0 (MTD numerator on HEADER model)
    call_q1 = mock.execute_kw.call_args_list[0]
    assert call_q1.args[0] == _PAYMENT_HEADER_MODEL, (
        f"Q1 must query {_PAYMENT_HEADER_MODEL!r}, got {call_q1.args[0]!r}"
    )
    domain = call_q1.kwargs["args"][0]
    assert domain[0] == ("state", "=", "post")
    assert domain[1] == ("date", ">=", "2026-04-30 21:00:00"), (
        f"MTD start UTC wrong: {domain[1][2]!r} — expected '2026-04-30 21:00:00' "
        "(2026-05-01 00:00:00 Africa/Cairo UTC+3)"
    )
    assert domain[2] == ("date", "<=", "2026-05-01 20:59:59"), (
        f"MTD end UTC wrong: {domain[2][2]!r} — expected '2026-05-01 20:59:59' "
        "(2026-05-01 23:59:59 Africa/Cairo UTC+3)"
    )


# ── Test K4-06 — YTD UTC boundary: Egypt winter (UTC+2, Jan 1) ────────────────


async def test_kpi4_ytd_numerator_domain_uses_winter_utc2_boundary() -> None:
    """YTD period start (Jan 1) UTC boundary uses Egypt winter offset (UTC+2).

    today = 2026-05-17 (Egypt summer, UTC+3):
      YTD start Egypt : 2026-01-01 00:00:00 Africa/Cairo (UTC+2 winter) = 2025-12-31 22:00:00 UTC
      YTD end Egypt   : 2026-05-17 23:59:59 Africa/Cairo (UTC+3 summer) = 2026-05-17 20:59:59 UTC

    A naive boundary ("2026-01-01 00:00:00") would exclude any receipts
    recorded at Egypt midnight on Jan 1 — stored at "2025-12-31 22:00:00" UTC.
    Decision 5.9.
    """
    mock = MagicMock()
    mock.execute_kw = AsyncMock(return_value=[{"amount": 0.0, "__count": 0}])

    with patch("backend.modules.collections.services.cache.today_str",
               return_value=_KPI4_TODAY):
        await get_collection_rate_mtd_ytd(client=mock)

    # Q3 = call index 2 (YTD numerator on HEADER model)
    call_q3 = mock.execute_kw.call_args_list[2]
    assert call_q3.args[0] == _PAYMENT_HEADER_MODEL, (
        f"Q3 must query {_PAYMENT_HEADER_MODEL!r}, got {call_q3.args[0]!r}"
    )
    domain = call_q3.kwargs["args"][0]
    assert domain[0] == ("state", "=", "post")
    assert domain[1] == ("date", ">=", "2025-12-31 22:00:00"), (
        f"YTD start UTC wrong: {domain[1][2]!r} — expected '2025-12-31 22:00:00' "
        "(2026-01-01 00:00:00 Africa/Cairo UTC+2 winter)"
    )
    assert domain[2] == ("date", "<=", "2026-05-17 20:59:59"), (
        f"YTD end UTC wrong: {domain[2][2]!r} — expected '2026-05-17 20:59:59' "
        "(2026-05-17 23:59:59 Africa/Cairo UTC+3 summer)"
    )


# ── Test K4-07 — Both denominators zero → both rate_percent: None ─────────────


async def test_kpi4_both_denominators_zero_both_rates_none() -> None:
    """When both MTD and YTD denominators are zero, both rate_percent values
    must be None. This would occur if no installments are due in either period.
    Decision 6.3.
    """
    mock = MagicMock()
    mock.execute_kw = AsyncMock(side_effect=[
        [{"amount": 100_000.00, "__count": 2}],  # MTD num
        [{"amount": 0.00,       "__count": 0}],  # MTD den = 0
        [{"amount": 500_000.00, "__count": 5}],  # YTD num
        [{"amount": 0.00,       "__count": 0}],  # YTD den = 0
    ])

    with patch("backend.modules.collections.services.cache.today_str",
               return_value=_KPI4_TODAY):
        result = await get_collection_rate_mtd_ytd(client=mock)

    assert result["mtd"]["rate_percent"] is None, (
        "MTD rate_percent must be None when MTD denominator == 0"
    )
    assert result["ytd"]["rate_percent"] is None, (
        "YTD rate_percent must be None when YTD denominator == 0"
    )
    # Numerators are preserved even when rate is None
    assert result["mtd"]["numerator_egp"] == pytest.approx(100_000.00)
    assert result["ytd"]["numerator_egp"] == pytest.approx(500_000.00)


# ── Test K4-08 — Cache hit: second call served from cache ─────────────────────


async def test_kpi4_second_call_is_served_from_cache(mock_client_kpi4: MagicMock) -> None:
    with patch("backend.modules.collections.services.cache.today_str",
               return_value=_KPI4_TODAY):
        result1 = await get_collection_rate_mtd_ytd(client=mock_client_kpi4)
        result2 = await get_collection_rate_mtd_ytd(client=mock_client_kpi4)

    # First call: 4 RPCs (Q1–Q4). Second call: 0 RPCs (cache hit).
    assert mock_client_kpi4.execute_kw.call_count == 4, (
        "execute_kw must be called exactly 4 times total (first call only)"
    )
    assert result1["cache_status"] == "fresh"
    assert result2["cache_status"] == "cached"
    assert result2["rpc_duration_ms"] == 0
    # Numeric values preserved through cache round-trip
    assert result2["mtd"]["rate_percent"] == result1["mtd"]["rate_percent"]
    assert result2["ytd"]["denominator_egp"] == result1["ytd"]["denominator_egp"]


# ── Test K4-09 — OdooQueryError raised on any RPC failure ────────────────────


async def test_kpi4_rpc_failure_raises_odoo_query_error() -> None:
    """Any failure in any of the 4 sequential RPCs must raise OdooQueryError."""
    mock = MagicMock()
    mock.execute_kw = AsyncMock(side_effect=RuntimeError("connection refused"))

    with patch("backend.modules.collections.services.cache.today_str",
               return_value=_KPI4_TODAY):
        with pytest.raises(OdooQueryError):
            await get_collection_rate_mtd_ytd(client=mock)


async def test_kpi4_rpc_failure_mid_sequence_raises_odoo_query_error() -> None:
    """Failure on Q3 (after Q1 and Q2 succeed) must also raise OdooQueryError."""
    mock = MagicMock()
    mock.execute_kw = AsyncMock(side_effect=[
        _MOCK_KPI4_Q1,                          # Q1 succeeds
        _MOCK_KPI4_Q2,                          # Q2 succeeds
        RuntimeError("timeout on Q3"),           # Q3 fails
    ])

    with patch("backend.modules.collections.services.cache.today_str",
               return_value=_KPI4_TODAY):
        with pytest.raises(OdooQueryError):
            await get_collection_rate_mtd_ytd(client=mock)


# ── Test K4-10 — RPC failure writes no cache entry ───────────────────────────


async def test_kpi4_rpc_failure_writes_no_cache_entry() -> None:
    """A failed RPC must not leave a partial or empty cache entry.
    A subsequent fresh call must still hit Odoo.
    """
    mock = MagicMock()
    mock.execute_kw = AsyncMock(side_effect=RuntimeError("timeout"))

    with patch("backend.modules.collections.services.cache.today_str",
               return_value=_KPI4_TODAY):
        with pytest.raises(OdooQueryError):
            await get_collection_rate_mtd_ytd(client=mock)

    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_KPI4)
    assert _cache.get(cache_key) is None, (
        "A failed RPC must not write any cache entry — "
        "a subsequent call must re-query Odoo rather than serving stale/empty data"
    )


# ══════════════════════════════════════════════════════════════════════════════
# KPI 5b — Collection Rate per Project (get_collection_rate_by_project)
# ══════════════════════════════════════════════════════════════════════════════
#
# Architecture (Decision 7.1): Branch A — project_id is a direct field on
# rs.account.payment.installment. Four sequential read_group RPCs:
#   Q1 — MTD numerator  per project: HEADER, UTC datetime bounds, groupby project_id
#   Q2 — MTD denominator per project: rs.installment, ISO date bounds, groupby project_id
#   Q3 — YTD numerator  per project
#   Q4 — YTD denominator per project
#
# Zero denominator per project → rate_percent: None (Decision 6.3).
# Always returns 3 projects (zero-padding, Decision 3.4 analog).
# ══════════════════════════════════════════════════════════════════════════════

from backend.modules.collections.services.kpi_service import (  # noqa: E402
    _CACHE_KEY_PREFIX_KPI5B,
    get_collection_rate_by_project,
)

# Q1: MTD numerator per project (HEADER, groupby project_id)
_MOCK_KPI5B_Q1 = [
    {"project_id": [1, "Project#New Capital"], "amount":  5_000_000.00, "__count":  3},
    {"project_id": [2, "Project#Cassette"],    "amount":  3_000_000.00, "__count":  2},
    {"project_id": [3, "Project#La puerta"],   "amount":    500_000.00, "__count":  1},
]
# Q2: MTD denominator per project (rs.installment, groupby project_id)
_MOCK_KPI5B_Q2 = [
    {"project_id": [1, "Project#New Capital"], "amount": 20_000_000.00, "__count": 100},
    {"project_id": [2, "Project#Cassette"],    "amount": 15_000_000.00, "__count":  80},
    {"project_id": [3, "Project#La puerta"],   "amount":  1_000_000.00, "__count":   5},
]
# Q3: YTD numerator per project
_MOCK_KPI5B_Q3 = [
    {"project_id": [1, "Project#New Capital"], "amount": 40_000_000.00, "__count": 20},
    {"project_id": [2, "Project#Cassette"],    "amount": 25_000_000.00, "__count": 15},
    {"project_id": [3, "Project#La puerta"],   "amount":  3_000_000.00, "__count":  5},
]
# Q4: YTD denominator per project (D0 Checkpoint 1 baselines)
_MOCK_KPI5B_Q4 = [
    {"project_id": [1, "Project#New Capital"], "amount": 162_112_391.00, "__count": 1458},
    {"project_id": [2, "Project#Cassette"],    "amount": 138_966_586.00, "__count":  391},
    {"project_id": [3, "Project#La puerta"],   "amount":   1_804_000.00, "__count":   12},
]

_KPI5B_TODAY = "2026-05-17"


@pytest.fixture
def mock_client_kpi5b() -> MagicMock:
    client = MagicMock()
    client.execute_kw = AsyncMock(side_effect=[
        _MOCK_KPI5B_Q1, _MOCK_KPI5B_Q2, _MOCK_KPI5B_Q3, _MOCK_KPI5B_Q4,
    ])
    return client


# ── Test K5B-01 — Happy path: full return shape + 3 projects + correct rates ──


async def test_kpi5b_happy_path_full_shape_and_rates(mock_client_kpi5b: MagicMock) -> None:
    with patch("backend.modules.collections.services.cache.today_str",
               return_value=_KPI5B_TODAY):
        result = await get_collection_rate_by_project(client=mock_client_kpi5b)

    # Exactly 4 RPCs on first call
    assert mock_client_kpi5b.execute_kw.call_count == 4

    # Top-level keys
    expected_top = {"mtd", "ytd", "ytd_period_assumption", "currency",
                    "as_of", "cache_status", "rpc_duration_ms"}
    assert set(result.keys()) == expected_top

    # Period keys
    period_keys = {"projects", "total_numerator_egp", "total_denominator_egp",
                   "total_rate_percent", "period_start", "period_end"}
    assert set(result["mtd"].keys()) == period_keys
    assert set(result["ytd"].keys()) == period_keys

    # Per-project keys
    proj_keys = {"project_id", "project_name", "numerator_egp", "denominator_egp",
                 "rate_percent", "record_count_num", "record_count_den"}
    for period in ("mtd", "ytd"):
        assert len(result[period]["projects"]) == 3
        for proj in result[period]["projects"]:
            assert set(proj.keys()) == proj_keys

    # Fixed assertions
    assert result["currency"] == "EGP"
    assert result["ytd_period_assumption"] == "calendar_year"
    assert result["cache_status"] == "fresh"
    assert isinstance(result["rpc_duration_ms"], int)

    # Period dates
    assert result["mtd"]["period_start"] == "2026-05-01"
    assert result["mtd"]["period_end"]   == _KPI5B_TODAY
    assert result["ytd"]["period_start"] == "2026-01-01"
    assert result["ytd"]["period_end"]   == _KPI5B_TODAY

    # MTD: NC rate = 5M/20M = 25%, Cassette = 3M/15M = 20%, LP = 0.5M/1M = 50%
    mtd_nc = result["mtd"]["projects"][0]
    assert mtd_nc["project_id"] == 1
    assert mtd_nc["project_name"] == "New Capital"
    assert mtd_nc["numerator_egp"]   == pytest.approx(5_000_000.00)
    assert mtd_nc["denominator_egp"] == pytest.approx(20_000_000.00)
    assert mtd_nc["rate_percent"]    == pytest.approx(25.0)
    assert mtd_nc["record_count_num"] == 3
    assert mtd_nc["record_count_den"] == 100

    # MTD totals
    assert result["mtd"]["total_numerator_egp"]   == pytest.approx(8_500_000.00)
    assert result["mtd"]["total_denominator_egp"] == pytest.approx(36_000_000.00)
    assert result["mtd"]["total_rate_percent"]    == pytest.approx(8_500_000 / 36_000_000 * 100)


# ── Test K5B-02 — Zero denominator → rate_percent: None (Decision 6.3) ────────


async def test_kpi5b_zero_denominator_returns_none_rate() -> None:
    """Zero denominator for a project → rate_percent: None.
    Zero denominator for all projects → total_rate_percent: None.
    """
    mock = MagicMock()
    mock.execute_kw = AsyncMock(side_effect=[
        # Q1 MTD num: NC has payments
        [{"project_id": [1, "Project#New Capital"], "amount": 1_000.00, "__count": 1}],
        # Q2 MTD den: all zero — no installments due
        [],
        # Q3 YTD num
        [{"project_id": [1, "Project#New Capital"], "amount": 5_000.00, "__count": 2}],
        # Q4 YTD den: all zero
        [],
    ])

    with patch("backend.modules.collections.services.cache.today_str",
               return_value=_KPI5B_TODAY):
        result = await get_collection_rate_by_project(client=mock)

    for period in ("mtd", "ytd"):
        for proj in result[period]["projects"]:
            assert proj["rate_percent"] is None, (
                f"{period} project {proj['project_id']} rate_percent must be None "
                "when denominator == 0 (Decision 6.3)"
            )
        assert result[period]["total_rate_percent"] is None, (
            f"{period} total_rate_percent must be None when all denominators == 0"
        )


# ── Test K5B-03 — Project order always [1, 2, 3] regardless of Odoo order ─────


async def test_kpi5b_projects_ordered_1_2_3_regardless_of_odoo_order() -> None:
    """Odoo returns project rows in reverse order — service must sort to 1, 2, 3."""
    reversed_q = [
        {"project_id": [3, "Project#La puerta"],   "amount": 500.00, "__count": 1},
        {"project_id": [2, "Project#Cassette"],    "amount": 300.00, "__count": 1},
        {"project_id": [1, "Project#New Capital"], "amount": 100.00, "__count": 1},
    ]
    mock = MagicMock()
    mock.execute_kw = AsyncMock(side_effect=[reversed_q, reversed_q, reversed_q, reversed_q])

    with patch("backend.modules.collections.services.cache.today_str",
               return_value=_KPI5B_TODAY):
        result = await get_collection_rate_by_project(client=mock)

    for period in ("mtd", "ytd"):
        ids   = [p["project_id"]   for p in result[period]["projects"]]
        names = [p["project_name"] for p in result[period]["projects"]]
        assert ids   == [1, 2, 3],                            f"{period}: expected [1,2,3], got {ids}"
        assert names == ["New Capital", "Cassette", "La puerta"], f"{period}: wrong names: {names}"


# ── Test K5B-04 — Zero-padding when a project is absent from read_group ────────


async def test_kpi5b_zero_pads_missing_project() -> None:
    """If read_group returns only 2 projects, the third must be zero-padded."""
    two_projs = [
        {"project_id": [1, "Project#New Capital"], "amount": 10_000.00, "__count": 5},
        {"project_id": [2, "Project#Cassette"],    "amount":  5_000.00, "__count": 3},
    ]
    mock = MagicMock()
    mock.execute_kw = AsyncMock(side_effect=[two_projs, two_projs, two_projs, two_projs])

    with patch("backend.modules.collections.services.cache.today_str",
               return_value=_KPI5B_TODAY):
        result = await get_collection_rate_by_project(client=mock)

    for period in ("mtd", "ytd"):
        assert len(result[period]["projects"]) == 3, \
            f"{period}: must always return 3 projects (zero-padding required)"
        lp = result[period]["projects"][2]
        assert lp["project_id"]   == 3
        assert lp["project_name"] == "La puerta"
        assert lp["numerator_egp"]   == 0.0
        assert lp["denominator_egp"] == 0.0
        assert lp["rate_percent"]    is None  # zero den → None
        assert lp["record_count_num"] == 0
        assert lp["record_count_den"] == 0


# ── Test K5B-05 — Totals equal sum of per-project values ──────────────────────


async def test_kpi5b_totals_equal_sum_of_per_project_values(mock_client_kpi5b: MagicMock) -> None:
    with patch("backend.modules.collections.services.cache.today_str",
               return_value=_KPI5B_TODAY):
        result = await get_collection_rate_by_project(client=mock_client_kpi5b)

    for period in ("mtd", "ytd"):
        sub      = result[period]
        sum_num  = sum(p["numerator_egp"]   for p in sub["projects"])
        sum_den  = sum(p["denominator_egp"] for p in sub["projects"])
        assert sub["total_numerator_egp"]   == pytest.approx(sum_num)
        assert sub["total_denominator_egp"] == pytest.approx(sum_den)
        if sum_den > 0:
            assert sub["total_rate_percent"] == pytest.approx(sum_num / sum_den * 100)
        else:
            assert sub["total_rate_percent"] is None


# ── Test K5B-06 — Cache hit: second call served from cache (4 RPCs total) ─────


async def test_kpi5b_second_call_is_served_from_cache(mock_client_kpi5b: MagicMock) -> None:
    with patch("backend.modules.collections.services.cache.today_str",
               return_value=_KPI5B_TODAY):
        result1 = await get_collection_rate_by_project(client=mock_client_kpi5b)
        result2 = await get_collection_rate_by_project(client=mock_client_kpi5b)

    assert mock_client_kpi5b.execute_kw.call_count == 4, (
        "execute_kw must be called exactly 4 times total (first call only)"
    )
    assert result1["cache_status"] == "fresh"
    assert result2["cache_status"] == "cached"
    assert result2["rpc_duration_ms"] == 0
    assert result2["mtd"]["total_numerator_egp"] == result1["mtd"]["total_numerator_egp"]
    assert result2["ytd"]["total_denominator_egp"] == result1["ytd"]["total_denominator_egp"]


# ── Test K5B-07 — OdooQueryError on any RPC failure ───────────────────────────


async def test_kpi5b_rpc_failure_raises_odoo_query_error() -> None:
    mock = MagicMock()
    mock.execute_kw = AsyncMock(side_effect=RuntimeError("connection refused"))

    with patch("backend.modules.collections.services.cache.today_str",
               return_value=_KPI5B_TODAY):
        with pytest.raises(OdooQueryError):
            await get_collection_rate_by_project(client=mock)


async def test_kpi5b_rpc_failure_mid_sequence_raises_odoo_query_error() -> None:
    """Failure on Q3 (after Q1, Q2 succeed) must also raise OdooQueryError."""
    mock = MagicMock()
    mock.execute_kw = AsyncMock(side_effect=[
        _MOCK_KPI5B_Q1,
        _MOCK_KPI5B_Q2,
        RuntimeError("timeout on Q3"),
    ])

    with patch("backend.modules.collections.services.cache.today_str",
               return_value=_KPI5B_TODAY):
        with pytest.raises(OdooQueryError):
            await get_collection_rate_by_project(client=mock)


# ── Test K5B-08 — RPC failure writes no cache entry ───────────────────────────


async def test_kpi5b_rpc_failure_writes_no_cache_entry() -> None:
    mock = MagicMock()
    mock.execute_kw = AsyncMock(side_effect=RuntimeError("timeout"))

    with patch("backend.modules.collections.services.cache.today_str",
               return_value=_KPI5B_TODAY):
        with pytest.raises(OdooQueryError):
            await get_collection_rate_by_project(client=mock)

    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_KPI5B)
    assert _cache.get(cache_key) is None, (
        "A failed RPC must not write any cache entry"
    )


# ── Test K5B-09 — Read-only assertion ─────────────────────────────────────────


async def test_kpi5b_contaminated_allowed_methods_raises_before_any_rpc(
    monkeypatch: pytest.MonkeyPatch,
    mock_client_kpi5b: MagicMock,
) -> None:
    monkeypatch.setattr(
        "backend.modules.collections.services.kpi_service.ALLOWED_METHODS",
        frozenset({"read_group", "write"}),
    )

    with pytest.raises(ReadOnlyViolationError):
        await get_collection_rate_by_project(client=mock_client_kpi5b)

    mock_client_kpi5b.execute_kw.assert_not_called()


async def test_kpi5b_clean_allowed_methods_does_not_raise(mock_client_kpi5b: MagicMock) -> None:
    with patch("backend.modules.collections.services.cache.today_str",
               return_value=_KPI5B_TODAY):
        result = await get_collection_rate_by_project(client=mock_client_kpi5b)
    assert result["mtd"]["total_numerator_egp"] >= 0.0


# ── Test K5B-10 — UnknownProjectError for unexpected project_id ───────────────


async def test_kpi5b_unknown_project_id_raises_unknown_project_error() -> None:
    mock = MagicMock()
    mock.execute_kw = AsyncMock(side_effect=[
        [{"project_id": [99, "Project#Unknown"], "amount": 1_000.00, "__count": 1}],
        _MOCK_KPI5B_Q2,
        _MOCK_KPI5B_Q3,
        _MOCK_KPI5B_Q4,
    ])

    with patch("backend.modules.collections.services.cache.today_str",
               return_value=_KPI5B_TODAY):
        with pytest.raises(UnknownProjectError):
            await get_collection_rate_by_project(client=mock)


# ── Test K5B-extra — project_names use clean display names ────────────────────


async def test_kpi5b_project_names_are_clean_without_project_prefix(
    mock_client_kpi5b: MagicMock,
) -> None:
    with patch("backend.modules.collections.services.cache.today_str",
               return_value=_KPI5B_TODAY):
        result = await get_collection_rate_by_project(client=mock_client_kpi5b)

    for period in ("mtd", "ytd"):
        for proj in result[period]["projects"]:
            assert not proj["project_name"].startswith("Project#"), (
                f"project_name must not include 'Project#' prefix, "
                f"got {proj['project_name']!r}"
            )
            assert proj["project_name"] in {"New Capital", "Cassette", "La puerta"}, (
                f"unexpected project_name: {proj['project_name']!r}"
            )


# ══════════════════════════════════════════════════════════════════════════════
# KPI 7 — Expected Collections Forecast (get_expected_collections_forecast)
# ══════════════════════════════════════════════════════════════════════════════

# Default per-bucket 5-tuple: (amount, count, due_amount, cheques_clamped, cheques_raw)
_RPC_TUPLE_KPI7 = (22_719_871.0, 133, 22_693_463.0, 0.0, 0.0)

# RPC 1 returns amount+due_amount aggregate; RPC 2 returns cheque fields.
# 8 responses total: 2 per bucket × 4 buckets.
_KPI7_RPC1 = [{"amount": 22_719_871.0, "due_amount": 22_693_463.0, "__count": 133}]
_KPI7_RPC2 = [{"paid_amount": 26_408.0, "x_studio_actual_paid_amount": 26_408.0}]


@pytest.fixture
def mock_client_kpi7() -> MagicMock:
    client = MagicMock()
    client.execute_kw = AsyncMock(side_effect=[
        _KPI7_RPC1, _KPI7_RPC2,   # this_month
        _KPI7_RPC1, _KPI7_RPC2,   # this_quarter
        _KPI7_RPC1, _KPI7_RPC2,   # this_half
        _KPI7_RPC1, _KPI7_RPC2,   # this_year
    ])
    return client


# ── Test K7-1 — Returns all four buckets ─────────────────────────────────────


async def test_kpi7_returns_all_four_buckets(mock_client_kpi7: MagicMock) -> None:
    with patch(
        "backend.modules.collections.services.kpi_service._fetch_bucket",
        AsyncMock(return_value=_RPC_TUPLE_KPI7),
    ):
        result = await get_expected_collections_forecast(odoo_client=mock_client_kpi7)

    assert "buckets" in result
    assert len(result["buckets"]) == 4


# ── Test K7-2 — Bucket keys are the canonical 4 names ────────────────────────


async def test_kpi7_bucket_keys_are_correct(mock_client_kpi7: MagicMock) -> None:
    with patch(
        "backend.modules.collections.services.kpi_service._fetch_bucket",
        AsyncMock(return_value=_RPC_TUPLE_KPI7),
    ):
        result = await get_expected_collections_forecast(odoo_client=mock_client_kpi7)

    assert set(result["buckets"]) == {
        "this_month", "this_quarter", "this_half", "this_year"
    }


# ── Test K7-3 — Nesting invariant: month ≤ quarter ≤ half ≤ year ─────────────


async def test_kpi7_nesting_invariant_holds(mock_client_kpi7: MagicMock) -> None:
    # Strictly increasing amounts so any bucket-swapping plumbing bug will fail.
    # this_year has non-zero cheques (50.0 EGP) matching Phase 0 baseline shape
    # (D0.6: near-term buckets show 0 EGP, only the year bucket has cheques).
    # Tuple: (amount, count, due_amount, cheques_clamped, cheques_raw)
    tuples = [
        (100.0,    10,  95.0,   0.0,  0.0),  # this_month
        (350.0,    35, 340.0,   0.0,  0.0),  # this_quarter
        (350.0,    35, 340.0,   0.0,  0.0),  # this_half (collapses with quarter in Q2)
        (1_000.0, 100, 980.0,  50.0, 50.0),  # this_year: 50.0 EGP cheques in pipeline
    ]
    with patch(
        "backend.modules.collections.services.kpi_service._fetch_bucket",
        AsyncMock(side_effect=tuples),
    ):
        result = await get_expected_collections_forecast(odoo_client=mock_client_kpi7)

    b = result["buckets"]

    # Amount nests
    assert b["this_month"]["amount"]   <= b["this_quarter"]["amount"]
    assert b["this_quarter"]["amount"] <= b["this_half"]["amount"]
    assert b["this_half"]["amount"]    <= b["this_year"]["amount"]

    # Record count nests
    assert b["this_month"]["record_count"]   <= b["this_quarter"]["record_count"]
    assert b["this_quarter"]["record_count"] <= b["this_half"]["record_count"]
    assert b["this_half"]["record_count"]    <= b["this_year"]["record_count"]

    # due_amount nests
    assert b["this_month"]["due_amount"]   <= b["this_quarter"]["due_amount"]
    assert b["this_quarter"]["due_amount"] <= b["this_half"]["due_amount"]
    assert b["this_half"]["due_amount"]    <= b["this_year"]["due_amount"]

    # cheques_in_pipeline nests (subset of due_amount universe)
    assert b["this_month"]["cheques_in_pipeline"]   <= b["this_quarter"]["cheques_in_pipeline"]
    assert b["this_quarter"]["cheques_in_pipeline"] <= b["this_half"]["cheques_in_pipeline"]
    assert b["this_half"]["cheques_in_pipeline"]    <= b["this_year"]["cheques_in_pipeline"]

    # period_end nests
    ends = [b[n]["period_end"] for n in
            ("this_month", "this_quarter", "this_half", "this_year")]
    assert ends[0] <= ends[1] <= ends[2] <= ends[3]


# ── Test K7-4 — cheques_in_pipeline is clamped (≥ 0) and ≤ bucket amount ─────


async def test_kpi7_cheques_in_pipeline_le_bucket_amount(
    mock_client_kpi7: MagicMock,
) -> None:
    # Phase 0 baseline (D0.6): this_year has 643,000 EGP cheques; others 0.
    tuples = [
        (22_719_871.0,   133,  22_693_463.0,       0.0,       0.0),
        (55_527_209.0,   355,  55_459_801.0,       0.0,       0.0),
        (55_527_209.0,   355,  55_459_801.0,       0.0,       0.0),
        (337_946_411.0, 1934, 337_223_075.0, 643_000.0, 643_000.0),
    ]
    with patch(
        "backend.modules.collections.services.kpi_service._fetch_bucket",
        AsyncMock(side_effect=tuples),
    ):
        result = await get_expected_collections_forecast(odoo_client=mock_client_kpi7)

    for bname in ("this_month", "this_quarter", "this_half", "this_year"):
        b = result["buckets"][bname]
        assert b["cheques_in_pipeline"] >= 0.0, (
            f"cheques_in_pipeline must be ≥ 0 (clamped), bucket {bname!r}"
        )
        assert b["cheques_in_pipeline"] <= b["amount"], (
            f"cheques_in_pipeline must be ≤ bucket amount, bucket {bname!r}"
        )


# ── Test K7-5 — Zero-record bucket returns 0.0 / 0, not None ─────────────────


async def test_kpi7_zero_bucket_returns_zero_not_none(
    mock_client_kpi7: MagicMock,
) -> None:
    with patch(
        "backend.modules.collections.services.kpi_service._fetch_bucket",
        AsyncMock(return_value=(0.0, 0, 0.0, 0.0, 0.0)),
    ):
        result = await get_expected_collections_forecast(odoo_client=mock_client_kpi7)

    for bname in ("this_month", "this_quarter", "this_half", "this_year"):
        b = result["buckets"][bname]
        assert isinstance(b["amount"], float)          and b["amount"]            == 0.0
        assert isinstance(b["record_count"], int)      and b["record_count"]      == 0
        assert isinstance(b["due_amount"], float)      and b["due_amount"]        == 0.0
        assert isinstance(b["cheques_in_pipeline"], float) and b["cheques_in_pipeline"] == 0.0


# ── Test K7-6 — Cache hit: rpc_duration_ms=0, cache_status="cached" ──────────


async def test_kpi7_cache_hit_returns_zero_rpc_duration(
    mock_client_kpi7: MagicMock,
) -> None:
    with patch(
        "backend.modules.collections.services.kpi_service._fetch_bucket",
        AsyncMock(return_value=_RPC_TUPLE_KPI7),
    ):
        result1 = await get_expected_collections_forecast(odoo_client=mock_client_kpi7)
        result2 = await get_expected_collections_forecast(odoo_client=mock_client_kpi7)

    assert result1["cache_status"] == "fresh"
    assert result2["cache_status"] == "cached"
    assert result2["rpc_duration_ms"] == 0
    assert (result2["buckets"]["this_year"]["amount"] ==
            result1["buckets"]["this_year"]["amount"])


# ── Test K7-7 — Cache miss triggers exactly 8 Odoo RPCs ──────────────────────


async def test_kpi7_cache_miss_invokes_eight_rpcs(mock_client_kpi7: MagicMock) -> None:
    await get_expected_collections_forecast(odoo_client=mock_client_kpi7)

    assert mock_client_kpi7.execute_kw.call_count == 8, (
        f"Expected 8 RPCs (2 per bucket × 4 buckets), "
        f"got {mock_client_kpi7.execute_kw.call_count}"
    )


# ── Test K7-8 — Read-only assertion fires before any RPC ─────────────────────


async def test_kpi7_read_only_assertion_fires_when_violated(
    monkeypatch: pytest.MonkeyPatch,
    mock_client_kpi7: MagicMock,
) -> None:
    monkeypatch.setattr(
        "backend.modules.collections.services.kpi_service.ALLOWED_METHODS",
        frozenset({"read_group", "write"}),
    )

    with pytest.raises(ReadOnlyViolationError):
        await get_expected_collections_forecast(odoo_client=mock_client_kpi7)

    mock_client_kpi7.execute_kw.assert_not_called()


# ── Test K7-9 — _fetch_bucket args are plain YYYY-MM-DD, never UTC datetimes ─


async def test_kpi7_bucket_boundaries_use_cairo_timezone(
    mock_client_kpi7: MagicMock,
) -> None:
    """rs.installment.date is a plain date field (D0.3). All date args passed to
    _fetch_bucket must be YYYY-MM-DD strings with no time component or tz suffix.
    UTC conversion here would be silently wrong."""
    mock_dt = MagicMock()
    mock_dt.now.return_value.date.return_value = date(2026, 5, 19)

    with patch(
        "backend.modules.collections.services.kpi_service.datetime", mock_dt
    ):
        with patch(
            "backend.modules.collections.services.kpi_service._fetch_bucket",
            AsyncMock(return_value=_RPC_TUPLE_KPI7),
        ) as mock_fetch:
            await get_expected_collections_forecast(odoo_client=mock_client_kpi7)

    for call_obj in mock_fetch.call_args_list:
        today_arg = call_obj.args[1]
        end_arg   = call_obj.args[2]
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", today_arg), (
            f"today_str arg must be plain YYYY-MM-DD, got {today_arg!r}"
        )
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", end_arg), (
            f"bucket_end_str arg must be plain YYYY-MM-DD, got {end_arg!r}"
        )


# ── Test K7-10 — Summer DST (UTC+3): correct Cairo date + Q2/H1 collapse ─────


async def test_kpi7_summer_dst_boundary(mock_client_kpi7: MagicMock) -> None:
    """today = 2026-06-15 Cairo (UTC+3, summer). June = Q2 = H1, so
    this_month, this_quarter, this_half all collapse to 2026-06-30."""
    mock_dt = MagicMock()
    mock_dt.now.return_value.date.return_value = date(2026, 6, 15)

    with patch(
        "backend.modules.collections.services.kpi_service.datetime", mock_dt
    ):
        with patch(
            "backend.modules.collections.services.kpi_service._fetch_bucket",
            AsyncMock(return_value=_RPC_TUPLE_KPI7),
        ):
            result = await get_expected_collections_forecast(odoo_client=mock_client_kpi7)

    assert result["today_cairo"] == "2026-06-15"
    for bname in ("this_month", "this_quarter", "this_half"):
        assert result["buckets"][bname]["period_end"] == "2026-06-30", (
            f"In June, {bname!r} must end 2026-06-30 (Q2/H1 collapse), "
            f"got {result['buckets'][bname]['period_end']!r}"
        )
    assert result["buckets"]["this_year"]["period_end"] == "2026-12-31"


# ── Test K7-11 — Winter DST (UTC+2): all 4 bucket ends are distinct ──────────


async def test_kpi7_winter_dst_boundary(mock_client_kpi7: MagicMock) -> None:
    """today = 2026-01-15 Cairo (UTC+2, winter). Jan sits in Q1/H1/year with
    distinct ends. Feb 2026 has 28 days (not a leap year)."""
    mock_dt = MagicMock()
    mock_dt.now.return_value.date.return_value = date(2026, 1, 15)

    with patch(
        "backend.modules.collections.services.kpi_service.datetime", mock_dt
    ):
        with patch(
            "backend.modules.collections.services.kpi_service._fetch_bucket",
            AsyncMock(return_value=_RPC_TUPLE_KPI7),
        ):
            result = await get_expected_collections_forecast(odoo_client=mock_client_kpi7)

    assert result["today_cairo"] == "2026-01-15"
    assert result["buckets"]["this_month"]["period_end"]   == "2026-01-31"
    assert result["buckets"]["this_quarter"]["period_end"] == "2026-03-31"
    assert result["buckets"]["this_half"]["period_end"]    == "2026-06-30"
    assert result["buckets"]["this_year"]["period_end"]    == "2026-12-31"


# ── Test K7-12 — Cache key uses Cairo local date, not UTC date ───────────────


async def test_kpi7_cache_key_uses_cairo_date_not_utc(
    mock_client_kpi7: MagicMock,
) -> None:
    """At 2026-01-14 23:30 UTC (UTC+2 winter), Cairo is already 2026-01-15.
    Result must be cached under 'kpi:expected_forecast:2026-01-15'.
    The UTC-date key 'kpi:expected_forecast:2026-01-14' must remain empty."""
    mock_dt = MagicMock()
    mock_dt.now.return_value.date.return_value = date(2026, 1, 15)

    with patch(
        "backend.modules.collections.services.kpi_service.datetime", mock_dt
    ):
        with patch(
            "backend.modules.collections.services.kpi_service._fetch_bucket",
            AsyncMock(return_value=_RPC_TUPLE_KPI7),
        ):
            await get_expected_collections_forecast(odoo_client=mock_client_kpi7)

    cairo_key = f"{_CACHE_KEY_PREFIX_KPI7}:2026-01-15"
    utc_key   = f"{_CACHE_KEY_PREFIX_KPI7}:2026-01-14"

    assert _cache.get(cairo_key) is not None, (
        f"Result must be cached under Cairo-date key {cairo_key!r}"
    )
    assert _cache.get(utc_key) is None, (
        f"UTC-date key {utc_key!r} must remain empty — Cairo date must be used"
    )


# ── Test K7-13 — Dec 31: all 4 buckets collapse to 2026-12-31 ────────────────


async def test_kpi7_year_end_full_collapse(mock_client_kpi7: MagicMock) -> None:
    """On December 31, this_month/this_quarter/this_half/this_year all share
    the same period_end: Dec 31. Full four-way collapse."""
    mock_dt = MagicMock()
    mock_dt.now.return_value.date.return_value = date(2026, 12, 31)

    with patch(
        "backend.modules.collections.services.kpi_service.datetime", mock_dt
    ):
        with patch(
            "backend.modules.collections.services.kpi_service._fetch_bucket",
            AsyncMock(return_value=_RPC_TUPLE_KPI7),
        ):
            result = await get_expected_collections_forecast(odoo_client=mock_client_kpi7)

    for bname in ("this_month", "this_quarter", "this_half", "this_year"):
        assert result["buckets"][bname]["period_end"] == "2026-12-31", (
            f"On Dec 31, {bname!r} period_end must be 2026-12-31 (full collapse), "
            f"got {result['buckets'][bname]['period_end']!r}"
        )
        assert result["buckets"][bname]["period_start"] == "2026-12-31"
