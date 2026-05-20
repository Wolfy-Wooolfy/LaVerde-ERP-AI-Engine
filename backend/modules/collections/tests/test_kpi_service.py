"""
Unit tests for Collections KPI service — KPI 2 PATH A (Stage 2.5).

OdooClient is fully mocked; no live Odoo connection is made.
Live verification: scripts/verify_kpi2_live.py.
"""
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.core.exceptions import OdooQueryError, ReadOnlyViolationError
from backend.modules.collections.services import cache as _cache
from backend.modules.collections.services.kpi_service import (
    _CACHE_KEY_PREFIX,
    _CACHE_KEY_PREFIX_KPI1,
    _CACHE_KEY_PREFIX_KPI3,
    _CACHE_KEY_PREFIX_KPI5,
    get_late_uncollected,
    get_total_portfolio_value,
)

# Synthetic mock values — no live data. PATH A formula: value = amount - actual_paid.
_AMOUNT       = 1_000_000.0
_PAID         =   200_000.0
_ACTUAL_PAID  =   150_000.0
_TOTAL_DUE    =   850_000.0   # = _AMOUNT - _ACTUAL_PAID: exact H2 identity
_RECORD_COUNT = 42

_EXPECTED_VALUE   = _AMOUNT - _ACTUAL_PAID   # PATH A formula (Decision 11.13)
_EXPECTED_CHEQUES = _PAID   - _ACTUAL_PAID   # Alt B cheques subset

_MOCK_RESPONSE = [{
    "amount":                      _AMOUNT,
    "paid_amount":                 _PAID,
    "x_studio_actual_paid_amount": _ACTUAL_PAID,
    "total_due_amount":            _TOTAL_DUE,
    "__count":                     _RECORD_COUNT,
}]


@pytest.fixture(autouse=True)
def fresh_cache():
    _cache.clear()
    yield
    _cache.clear()


@pytest.fixture
def mock_client() -> MagicMock:
    client = MagicMock()
    client.execute_kw = AsyncMock(return_value=_MOCK_RESPONSE)
    return client


@pytest.fixture
def loguru_records():
    """Capture loguru records as list of {"level": str, "text": str} dicts."""
    from loguru import logger
    records = []

    def _sink(message):
        records.append({
            "level": message.record["level"].name,
            "text":  message.record["message"],
        })

    handler_id = logger.add(_sink, level="DEBUG", colorize=False)
    yield records
    logger.remove(handler_id)


# ── Test 1: Domain construction ───────────────────────────────────────────────


async def test_domain_is_exact_candidate_c_three_clause(mock_client: MagicMock) -> None:
    await get_late_uncollected(client=mock_client)

    kw = mock_client.execute_kw.call_args
    domain = kw.kwargs["args"][0]

    assert domain[0] == ("state", "=", "post")
    assert domain[1] == ("payment_state", "in", ["unpaid", "partial"])
    assert domain[2][0] == "date"
    assert domain[2][1] == "<"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", domain[2][2]), (
        f"today clause must be YYYY-MM-DD, got {domain[2][2]!r}"
    )


# ── Test 2: Aggregation method ────────────────────────────────────────────────


async def test_uses_read_group_not_search_read(mock_client: MagicMock) -> None:
    await get_late_uncollected(client=mock_client)

    kw = mock_client.execute_kw.call_args
    assert kw.args[1] == "read_group", (
        f"Expected read_group, got {kw.args[1]!r}"
    )


# ── Test 3: Return shape ──────────────────────────────────────────────────────


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


# ── Test 4: Return values — PATH A formula ────────────────────────────────────


async def test_return_values_match_path_a_formula(mock_client: MagicMock) -> None:
    result = await get_late_uncollected(client=mock_client)

    assert result["value"] == pytest.approx(_EXPECTED_VALUE)   # PATH A: amount - actual_paid
    assert result["record_count"] == _RECORD_COUNT
    assert result["cache_status"] == "fresh"
    assert result["rpc_duration_ms"] >= 0


# ── Test 5: Cache hit ─────────────────────────────────────────────────────────


async def test_second_call_is_served_from_cache(mock_client: MagicMock) -> None:
    result1 = await get_late_uncollected(client=mock_client)
    result2 = await get_late_uncollected(client=mock_client)

    assert mock_client.execute_kw.call_count == 1
    assert result1["cache_status"] == "fresh"
    assert result2["cache_status"] == "cached"
    assert result2["rpc_duration_ms"] == 0
    assert result2["value"] == result1["value"]


# ── Test 6: Cache midnight invalidation ──────────────────────────────────────


async def test_different_dates_produce_independent_cache_entries(
    mock_client: MagicMock,
) -> None:
    with patch("backend.modules.collections.services.cache.today_str", return_value="2026-05-15"):
        await get_late_uncollected(client=mock_client)

    with patch("backend.modules.collections.services.cache.today_str", return_value="2026-05-16"):
        await get_late_uncollected(client=mock_client)

    assert mock_client.execute_kw.call_count == 2


# ── Test 7: RPC failure raises OdooQueryError ─────────────────────────────────


async def test_rpc_failure_raises_odoo_query_error(mock_client: MagicMock) -> None:
    mock_client.execute_kw.side_effect = RuntimeError("connection refused")

    with pytest.raises(OdooQueryError):
        await get_late_uncollected(client=mock_client)


# ── Test 8: RPC failure does not pollute cache ────────────────────────────────


async def test_rpc_failure_writes_no_cache_entry(mock_client: MagicMock) -> None:
    mock_client.execute_kw.side_effect = RuntimeError("timeout")

    with pytest.raises(OdooQueryError):
        await get_late_uncollected(client=mock_client)

    cache_key = _cache.make_key(_CACHE_KEY_PREFIX)
    assert _cache.get(cache_key) is None, "A failed RPC must not leave a cache entry"


# ── Test 9: Read-only guard ───────────────────────────────────────────────────


async def test_read_only_violation_raises_before_rpc(
    monkeypatch: pytest.MonkeyPatch,
    mock_client: MagicMock,
) -> None:
    monkeypatch.setattr(
        "backend.modules.collections.services.kpi_service.ALLOWED_METHODS",
        frozenset({"read_group", "write"}),
    )

    with pytest.raises(ReadOnlyViolationError):
        await get_late_uncollected(client=mock_client)

    mock_client.execute_kw.assert_not_called()


# ── Test 10: Cheques in pipeline correct value ────────────────────────────────


async def test_cheques_in_pipeline_is_correct_value(mock_client: MagicMock) -> None:
    result = await get_late_uncollected(client=mock_client)

    assert result["cheques_in_pipeline"] == pytest.approx(_EXPECTED_CHEQUES)
    assert result["cheques_in_pipeline"] >= 0.0


# ── Test 11: Negative cheques anomaly ────────────────────────────────────────


async def test_negative_cheques_sets_data_quality_warning() -> None:
    client = MagicMock()
    client.execute_kw = AsyncMock(return_value=[{
        "amount":                      120_000.0,
        "paid_amount":                  10_000.0,
        "x_studio_actual_paid_amount":  15_000.0,   # actual > paid → anomaly
        "total_due_amount":            110_000.0,
        "__count":                          5,
    }])

    result = await get_late_uncollected(client=client)

    assert result["cheques_in_pipeline"] == 0.0
    assert result["data_quality_warning"] == "negative_cheques"


# ── Test 12: Identity mismatch — 3 tiers ─────────────────────────────────────


@pytest.mark.parametrize("delta,expected_warning,tier_label", [
    (0.50,   None,                     "tier1_exact_no_flag"),
    (500.0,  None,                     "tier2_micro_drift_no_flag"),
    (5000.0, "kpi2_identity_mismatch", "tier3_mismatch_flag_set"),
])
async def test_kpi2_identity_mismatch_sets_data_quality_warning(
    delta: float,
    expected_warning,
    tier_label: str,
    loguru_records,
) -> None:
    base_value = _AMOUNT - _ACTUAL_PAID
    injected_total_due = base_value + delta   # inject controlled mismatch

    client = MagicMock()
    client.execute_kw = AsyncMock(return_value=[{
        "amount":                      _AMOUNT,
        "paid_amount":                 _PAID,
        "x_studio_actual_paid_amount": _ACTUAL_PAID,
        "total_due_amount":            injected_total_due,
        "__count":                     _RECORD_COUNT,
    }])

    result = await get_late_uncollected(client=client)

    assert result["data_quality_warning"] == expected_warning, (
        f"{tier_label}: expected warning={expected_warning!r}, "
        f"got {result['data_quality_warning']!r}"
    )

    if tier_label == "tier2_micro_drift_no_flag":
        assert any(
            r["level"] == "INFO" and "micro-drift" in r["text"]
            for r in loguru_records
        ), f"{tier_label}: expected INFO 'micro-drift' log not found in {loguru_records!r}"

    if tier_label == "tier3_mismatch_flag_set":
        assert any(
            r["level"] == "WARNING" and "identity mismatch" in r["text"]
            for r in loguru_records
        ), f"{tier_label}: expected WARNING 'identity mismatch' log not found in {loguru_records!r}"


# ── Test 13: Drill-down domain and Alt B null fields ─────────────────────────


async def test_drill_down_domain_and_alt_b_nulls(mock_client: MagicMock) -> None:
    with patch("backend.modules.collections.services.cache.today_str", return_value="2026-05-20"):
        result = await get_late_uncollected(client=mock_client)

    expected_domain = [
        ("state", "=", "post"),
        ("payment_state", "in", ["unpaid", "partial"]),
        ("date", "<", "2026-05-20"),
    ]
    assert result["drill_down_domain"] == expected_domain
    assert result["drill_down_domain"] == result["domain"]
    assert result["cheques_record_count"] is None
    assert result["cheques_drill_down_domain"] is None


# ── Test 14: Cache key independence from KPI 1, KPI 3, KPI 5 ─────────────────


async def test_kpi2_cache_key_independent_of_kpi1_kpi3_kpi5() -> None:
    # Static check: prefixes must all be distinct.
    assert _CACHE_KEY_PREFIX != _CACHE_KEY_PREFIX_KPI1
    assert _CACHE_KEY_PREFIX != _CACHE_KEY_PREFIX_KPI3
    assert _CACHE_KEY_PREFIX != _CACHE_KEY_PREFIX_KPI5

    # Runtime cross-pollution check: KPI 2 and KPI 1 use independent keys.
    kpi2_client = MagicMock()
    kpi2_client.execute_kw = AsyncMock(return_value=_MOCK_RESPONSE)

    kpi1_client = MagicMock()
    kpi1_client.execute_kw = AsyncMock(return_value=[{"amount": 9_999_999.0, "__count": 1}])

    r2  = await get_late_uncollected(client=kpi2_client)
    r1  = await get_total_portfolio_value(client=kpi1_client)
    assert r2["cache_status"] == "fresh"
    assert r1["cache_status"] == "fresh"   # not polluted by KPI 2 cache entry

    r2b = await get_late_uncollected(client=kpi2_client)
    r1b = await get_total_portfolio_value(client=kpi1_client)
    assert r2b["cache_status"] == "cached"
    assert r1b["cache_status"] == "cached"

    assert kpi2_client.execute_kw.call_count == 1
    assert kpi1_client.execute_kw.call_count == 1
