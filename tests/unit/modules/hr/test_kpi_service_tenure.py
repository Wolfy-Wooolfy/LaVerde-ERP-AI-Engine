"""
Unit tests for HR KPI service — get_tenure_distribution (KPI B).

OdooClient is fully mocked; no live Odoo connection is made.
Live verification: scripts/verify_kpi_b_tenure_live.py (D4).

Discovery baselines (canonical run 2026-05-28T13:43:49Z):
  total_active   == 136  (S3: employee.active_true)
  first_contract_date range (active): 2017-12-26 → 2025-11-17
  Reference date: today in Africa/Cairo TZ, not UTC
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.core.exceptions import OdooQueryError, ReadOnlyViolationError
from backend.modules.hr.services import cache as _cache
from backend.modules.hr.services.kpi_service import (
    _BAND_LABELS,
    _CACHE_KEY_PREFIX_TENURE,
    get_tenure_distribution,
)

# ── Shared mock data (happy path) ─────────────────────────────────────────────

# One employee per band; mid-band dates stable for many years from 2026
_HAPPY_RECORDS = [
    {"id": 1, "first_contract_date": "2026-01-01"},   # ~0.4y  → <1y
    {"id": 2, "first_contract_date": "2024-01-01"},   # ~2.4y  → 1-3y
    {"id": 3, "first_contract_date": "2022-01-01"},   # ~4.4y  → 3-5y
    {"id": 4, "first_contract_date": "2019-01-01"},   # ~7.4y  → 5-10y
    {"id": 5, "first_contract_date": "2013-01-01"},   # ~13.4y → 10+y
]
_HAPPY_MISSING_COUNT = 3

# Fixed reference date used in all boundary tests — patches kpi_service.datetime
_FIXED_REF = date(2026, 5, 29)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def fresh_cache():
    _cache.clear()
    yield
    _cache.clear()


@pytest.fixture
def mock_client():
    """2-RPC mock: search_read (happy records), search_count (missing count)."""
    client = MagicMock()
    client.execute_kw = AsyncMock(side_effect=[_HAPPY_RECORDS, _HAPPY_MISSING_COUNT])
    return client


def _make_client(records, missing_count=0):
    client = MagicMock()
    client.execute_kw = AsyncMock(side_effect=[records, missing_count])
    return client


def _freeze_ref_date(monkeypatch, ref_date=None):
    """Patch datetime.now in the service to return ref_date (default _FIXED_REF)
    for the Cairo TZ call, and a stable UTC string for the as_of call."""
    ref = ref_date or _FIXED_REF

    cairo_mock = MagicMock()
    cairo_mock.date.return_value = ref

    utc_mock = MagicMock()
    utc_mock.isoformat.return_value = f"{ref.isoformat()}T00:00:00+00:00"

    mock_dt = MagicMock()
    mock_dt.now.side_effect = [cairo_mock, utc_mock]

    monkeypatch.setattr("backend.modules.hr.services.kpi_service.datetime", mock_dt)


# ── Test 1 — Happy path: return shape ────────────────────────────────────────


async def test_happy_path_returns_expected_shape(mock_client):
    result = await get_tenure_distribution(client=mock_client)

    assert set(result.keys()) == {
        "bands", "missing_date_count", "total_active",
        "reference_date", "as_of", "cache_status", "rpc_duration_ms",
    }
    assert isinstance(result["bands"], list)
    assert result["cache_status"] == "fresh"
    assert isinstance(result["reference_date"], str)
    assert isinstance(result["as_of"], str)
    assert result["rpc_duration_ms"] >= 0
    assert result["missing_date_count"] == _HAPPY_MISSING_COUNT


# ── Test 2 — Five bands always present in fixed order ─────────────────────────


async def test_five_bands_always_present_in_fixed_order(mock_client):
    result = await get_tenure_distribution(client=mock_client)

    bands = result["bands"]
    assert len(bands) == 5
    for i, label in enumerate(_BAND_LABELS):
        assert bands[i]["band"] == label, (
            f"Band #{i} must be {label!r}, got {bands[i]['band']!r}"
        )


# ── Tests 3–6 — At-boundary: employee lands in the higher band ────────────────


async def test_band_boundary_exactly_1y_lands_in_1_3y(monkeypatch):
    _freeze_ref_date(monkeypatch)
    # first_contract_date == _FIXED_REF − 1y: tenure = 1 → "1-3y", not "<1y"
    client = _make_client([{"id": 1, "first_contract_date": "2025-05-29"}])
    result = await get_tenure_distribution(client=client)

    bands = {b["band"]: b["count"] for b in result["bands"]}
    assert bands["1-3y"] == 1
    assert bands["<1y"] == 0


async def test_band_boundary_exactly_3y_lands_in_3_5y(monkeypatch):
    _freeze_ref_date(monkeypatch)
    client = _make_client([{"id": 1, "first_contract_date": "2023-05-29"}])
    result = await get_tenure_distribution(client=client)

    bands = {b["band"]: b["count"] for b in result["bands"]}
    assert bands["3-5y"] == 1
    assert bands["1-3y"] == 0


async def test_band_boundary_exactly_5y_lands_in_5_10y(monkeypatch):
    _freeze_ref_date(monkeypatch)
    client = _make_client([{"id": 1, "first_contract_date": "2021-05-29"}])
    result = await get_tenure_distribution(client=client)

    bands = {b["band"]: b["count"] for b in result["bands"]}
    assert bands["5-10y"] == 1
    assert bands["3-5y"] == 0


async def test_band_boundary_exactly_10y_lands_in_10_plus_y(monkeypatch):
    _freeze_ref_date(monkeypatch)
    client = _make_client([{"id": 1, "first_contract_date": "2016-05-29"}])
    result = await get_tenure_distribution(client=client)

    bands = {b["band"]: b["count"] for b in result["bands"]}
    assert bands["10+y"] == 1
    assert bands["5-10y"] == 0


# ── Tests 7–10 — Just-before-boundary: employee stays in the lower band ────────


async def test_band_just_before_1y_lands_in_lt_1y(monkeypatch):
    _freeze_ref_date(monkeypatch)
    # one day after the 1y anniversary → anniversary not yet reached → "<1y"
    client = _make_client([{"id": 1, "first_contract_date": "2025-05-30"}])
    result = await get_tenure_distribution(client=client)

    bands = {b["band"]: b["count"] for b in result["bands"]}
    assert bands["<1y"] == 1
    assert bands["1-3y"] == 0


async def test_band_just_before_3y_lands_in_1_3y(monkeypatch):
    _freeze_ref_date(monkeypatch)
    client = _make_client([{"id": 1, "first_contract_date": "2023-05-30"}])
    result = await get_tenure_distribution(client=client)

    bands = {b["band"]: b["count"] for b in result["bands"]}
    assert bands["1-3y"] == 1
    assert bands["3-5y"] == 0


async def test_band_just_before_5y_lands_in_3_5y(monkeypatch):
    _freeze_ref_date(monkeypatch)
    client = _make_client([{"id": 1, "first_contract_date": "2021-05-30"}])
    result = await get_tenure_distribution(client=client)

    bands = {b["band"]: b["count"] for b in result["bands"]}
    assert bands["3-5y"] == 1
    assert bands["5-10y"] == 0


async def test_band_just_before_10y_lands_in_5_10y(monkeypatch):
    _freeze_ref_date(monkeypatch)
    client = _make_client([{"id": 1, "first_contract_date": "2016-05-30"}])
    result = await get_tenure_distribution(client=client)

    bands = {b["band"]: b["count"] for b in result["bands"]}
    assert bands["5-10y"] == 1
    assert bands["10+y"] == 0


# ── Test 11 — missing_date_count populated ────────────────────────────────────


async def test_missing_date_count_populated():
    client = _make_client([], missing_count=5)
    result = await get_tenure_distribution(client=client)

    assert result["missing_date_count"] == 5
    assert result["total_active"] == 5
    assert all(b["count"] == 0 for b in result["bands"])


# ── Test 12 — Sanity invariant ────────────────────────────────────────────────


async def test_sanity_invariant_holds():
    records = [
        {"id": 1, "first_contract_date": "2026-01-01"},
        {"id": 2, "first_contract_date": "2024-01-01"},
        {"id": 3, "first_contract_date": "2020-01-01"},
    ]
    client = _make_client(records, missing_count=2)
    result = await get_tenure_distribution(client=client)

    band_sum = sum(b["count"] for b in result["bands"])
    assert band_sum + result["missing_date_count"] == result["total_active"], (
        f"band_sum ({band_sum}) + missing ({result['missing_date_count']}) "
        f"must == total_active ({result['total_active']})"
    )


# ── Test 13 — All-zero edge case ──────────────────────────────────────────────


async def test_all_zero_edge_case():
    client = _make_client([], missing_count=0)
    result = await get_tenure_distribution(client=client)

    assert result["total_active"] == 0
    assert result["missing_date_count"] == 0
    assert len(result["bands"]) == 5
    assert all(b["count"] == 0 for b in result["bands"])
    assert result["cache_status"] == "fresh"


# ── Test 14 — Cache hit ───────────────────────────────────────────────────────


async def test_second_call_served_from_cache(mock_client):
    result1 = await get_tenure_distribution(client=mock_client)
    result2 = await get_tenure_distribution(client=mock_client)

    assert mock_client.execute_kw.call_count == 2, (
        "execute_kw must fire exactly 2 times on the first call; "
        "the second call must be served from cache"
    )
    assert result1["cache_status"] == "fresh"
    assert result2["cache_status"] == "cached"
    assert result2["rpc_duration_ms"] == 0
    assert result2["total_active"] == result1["total_active"]
    assert result2["bands"] == result1["bands"]


# ── Test 15 — Read-only guard fires before any RPC ────────────────────────────


async def test_read_only_guard_raises_before_rpc(monkeypatch, mock_client):
    monkeypatch.setattr(
        "backend.modules.hr.services.kpi_service.ALLOWED_METHODS",
        frozenset({"read_group", "write"}),
    )

    with pytest.raises(ReadOnlyViolationError):
        await get_tenure_distribution(client=mock_client)

    mock_client.execute_kw.assert_not_called()


# ── Test 16 — RPC failure raises OdooQueryError ──────────────────────────────


async def test_rpc_failure_raises_odoo_query_error(mock_client):
    mock_client.execute_kw.side_effect = RuntimeError("connection refused")

    with pytest.raises(OdooQueryError):
        await get_tenure_distribution(client=mock_client)


# ── Test 17 — RPC failure writes no cache entry ──────────────────────────────


async def test_rpc_failure_writes_no_cache_entry(monkeypatch):
    _freeze_ref_date(monkeypatch)
    client = MagicMock()
    client.execute_kw = AsyncMock(side_effect=RuntimeError("timeout"))

    with pytest.raises(OdooQueryError):
        await get_tenure_distribution(client=client)

    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_TENURE)
    assert _cache.get(cache_key) is None, "A failed RPC must not leave a cache entry"


# ── Test 18 — Cairo TZ reference date used (not UTC) ─────────────────────────


async def test_cairo_tz_reference_date_used(monkeypatch):
    fixed_cairo = date(2026, 1, 15)
    _freeze_ref_date(monkeypatch, ref_date=fixed_cairo)
    client = _make_client([], missing_count=0)

    result = await get_tenure_distribution(client=client)

    assert result["reference_date"] == "2026-01-15", (
        f"reference_date must be Cairo TZ date {fixed_cairo.isoformat()!r}, "
        f"got {result['reference_date']!r}"
    )
