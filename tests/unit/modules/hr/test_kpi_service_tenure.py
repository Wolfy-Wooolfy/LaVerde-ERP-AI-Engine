"""
Unit tests for HR KPI service — get_tenure_distribution (KPI B, re-foundation 2026-06-03).

Employment: Running contract (state='open') — NOT hr.employee.active (§3.6).
Tenure: net accumulated service = sum of worked periods, overlaps clamped,
gaps naturally excluded (§3.7 D2).

OdooClient is fully mocked; no live Odoo connection is made.
Live verification: scripts/verify_kpi_b_tenure_live.py (D5).

Mock structure (1 RPC — all contracts, any state):
  client.execute_kw = AsyncMock(return_value=[...contract dicts...])
  Each contract: {id, employee_id: [eid, name], state, date_start, date_end}

Reference date for boundary tests: _FIXED_REF = date(2026, 5, 29)
  Frozen via _freeze_ref_date(monkeypatch) which patches kpi_service.datetime.
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

# ── Shared mock data ──────────────────────────────────────────────────────────

# One open-ended single-contract employee per band; stable for many years from 2026.
_HAPPY_CONTRACTS = [
    {"id": 1, "employee_id": [1, "E1"], "state": "open", "date_start": "2026-01-01", "date_end": False},
    {"id": 2, "employee_id": [2, "E2"], "state": "open", "date_start": "2024-01-01", "date_end": False},
    {"id": 3, "employee_id": [3, "E3"], "state": "open", "date_start": "2022-01-01", "date_end": False},
    {"id": 4, "employee_id": [4, "E4"], "state": "open", "date_start": "2019-01-01", "date_end": False},
    {"id": 5, "employee_id": [5, "E5"], "state": "open", "date_start": "2013-01-01", "date_end": False},
]

# Fixed reference date used in all boundary and general-logic tests
_FIXED_REF = date(2026, 5, 29)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def fresh_cache():
    _cache.clear()
    yield
    _cache.clear()


@pytest.fixture
def mock_client():
    """1-RPC mock returning _HAPPY_CONTRACTS."""
    client = MagicMock()
    client.execute_kw = AsyncMock(return_value=_HAPPY_CONTRACTS)
    return client


def _make_client(contract_records: list) -> MagicMock:
    """Build a mock OdooClient that returns contract_records from the single RPC."""
    client = MagicMock()
    client.execute_kw = AsyncMock(return_value=contract_records)
    return client


def _freeze_ref_date(monkeypatch, ref_date: date | None = None) -> None:
    """Patch datetime.now in the service to return ref_date for the Cairo TZ call
    and a stable UTC string for the as_of call."""
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
        "bands", "missing_date_count", "total_employed",
        "reference_date", "as_of", "cache_status", "rpc_duration_ms",
    }
    assert isinstance(result["bands"], list)
    assert result["cache_status"] == "fresh"
    assert isinstance(result["reference_date"], str)
    assert isinstance(result["as_of"], str)
    assert result["rpc_duration_ms"] >= 0
    assert result["missing_date_count"] == 0
    assert result["total_employed"] == 5


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
# Each test uses a single open-ended contract (date_end=False → endpoint = frozen
# cairo_today). For a single contract, total_days = (cairo_today - date_start).days
# and virtual_start == date_start exactly — so _tenure_years fires identically to
# the prior first_contract_date implementation.


async def test_band_boundary_exactly_1y_lands_in_1_3y(monkeypatch):
    _freeze_ref_date(monkeypatch)
    # date_start = 2025-05-29; total_days = 365; virtual_start = 2025-05-29
    # _tenure_years(2025-05-29, 2026-05-29) = 1 → "y1_3" (anniversary reached)
    client = _make_client([
        {"id": 1, "employee_id": [1, "E1"], "state": "open",
         "date_start": "2025-05-29", "date_end": False}
    ])
    result = await get_tenure_distribution(client=client)

    bands = {b["band"]: b["count"] for b in result["bands"]}
    assert bands["y1_3"] == 1
    assert bands["lt1y"] == 0


async def test_band_boundary_exactly_3y_lands_in_3_5y(monkeypatch):
    _freeze_ref_date(monkeypatch)
    # date_start = 2023-05-29; virtual_start = 2023-05-29
    # _tenure_years = 3 → "y3_5"
    client = _make_client([
        {"id": 1, "employee_id": [1, "E1"], "state": "open",
         "date_start": "2023-05-29", "date_end": False}
    ])
    result = await get_tenure_distribution(client=client)

    bands = {b["band"]: b["count"] for b in result["bands"]}
    assert bands["y3_5"] == 1
    assert bands["y1_3"] == 0


async def test_band_boundary_exactly_5y_lands_in_5_10y(monkeypatch):
    _freeze_ref_date(monkeypatch)
    # date_start = 2021-05-29; virtual_start = 2021-05-29
    # _tenure_years = 5 → "y5_10"
    client = _make_client([
        {"id": 1, "employee_id": [1, "E1"], "state": "open",
         "date_start": "2021-05-29", "date_end": False}
    ])
    result = await get_tenure_distribution(client=client)

    bands = {b["band"]: b["count"] for b in result["bands"]}
    assert bands["y5_10"] == 1
    assert bands["y3_5"] == 0


async def test_band_boundary_exactly_10y_lands_in_10_plus_y(monkeypatch):
    _freeze_ref_date(monkeypatch)
    # date_start = 2016-05-29; virtual_start = 2016-05-29
    # _tenure_years = 10 → "y10plus"
    client = _make_client([
        {"id": 1, "employee_id": [1, "E1"], "state": "open",
         "date_start": "2016-05-29", "date_end": False}
    ])
    result = await get_tenure_distribution(client=client)

    bands = {b["band"]: b["count"] for b in result["bands"]}
    assert bands["y10plus"] == 1
    assert bands["y5_10"] == 0


# ── Tests 7–10 — Just-before-boundary: employee stays in the lower band ────────


async def test_band_just_before_1y_lands_in_lt_1y(monkeypatch):
    _freeze_ref_date(monkeypatch)
    # date_start = 2025-05-30: one day after the anniversary date
    # total_days = 364; virtual_start = 2025-05-30
    # _tenure_years: (5,29) < (5,30) → years=0 → "lt1y"
    client = _make_client([
        {"id": 1, "employee_id": [1, "E1"], "state": "open",
         "date_start": "2025-05-30", "date_end": False}
    ])
    result = await get_tenure_distribution(client=client)

    bands = {b["band"]: b["count"] for b in result["bands"]}
    assert bands["lt1y"] == 1
    assert bands["y1_3"] == 0


async def test_band_just_before_3y_lands_in_1_3y(monkeypatch):
    _freeze_ref_date(monkeypatch)
    # date_start = 2023-05-30; _tenure_years: (5,29) < (5,30) → years=2 → "y1_3"
    client = _make_client([
        {"id": 1, "employee_id": [1, "E1"], "state": "open",
         "date_start": "2023-05-30", "date_end": False}
    ])
    result = await get_tenure_distribution(client=client)

    bands = {b["band"]: b["count"] for b in result["bands"]}
    assert bands["y1_3"] == 1
    assert bands["y3_5"] == 0


async def test_band_just_before_5y_lands_in_3_5y(monkeypatch):
    _freeze_ref_date(monkeypatch)
    # date_start = 2021-05-30; _tenure_years: (5,29) < (5,30) → years=4 → "y3_5"
    client = _make_client([
        {"id": 1, "employee_id": [1, "E1"], "state": "open",
         "date_start": "2021-05-30", "date_end": False}
    ])
    result = await get_tenure_distribution(client=client)

    bands = {b["band"]: b["count"] for b in result["bands"]}
    assert bands["y3_5"] == 1
    assert bands["y5_10"] == 0


async def test_band_just_before_10y_lands_in_5_10y(monkeypatch):
    _freeze_ref_date(monkeypatch)
    # date_start = 2016-05-30; _tenure_years: (5,29) < (5,30) → years=9 → "y5_10"
    client = _make_client([
        {"id": 1, "employee_id": [1, "E1"], "state": "open",
         "date_start": "2016-05-30", "date_end": False}
    ])
    result = await get_tenure_distribution(client=client)

    bands = {b["band"]: b["count"] for b in result["bands"]}
    assert bands["y5_10"] == 1
    assert bands["y10plus"] == 0


# ── Test 11 — GENERAL LOGIC: gap between two contracts excluded ───────────────
# Employee 101 has a prior Expired contract (service: 2019-01-01 → 2021-12-31)
# and a current Running contract (service: 2025-01-01 → ref 2026-05-29).
# The 3-year gap (2022-2024) is not at La Verde — it must NOT count.
#
# Net accumulated:
#   Period A: (2021-12-31 − 2019-01-01).days = 1095 days
#   Period B: (2026-05-29 − 2025-01-01).days = 514 days
#   Total: 1609 days → virtual_start ≈ 2022-01-02
#   _tenure_years(~2022-01-02, 2026-05-29) = 4 → "y3_5"
#
# Cross-checks proving the general logic is correct:
#   Naive (first→today): 7.4y → "y5_10"  ← gap included (WRONG)
#   Current contract only: 1.4y → "y1_3"  ← prior service ignored (WRONG)


async def test_two_contracts_with_gap_gap_excluded_from_tenure(monkeypatch):
    _freeze_ref_date(monkeypatch)
    client = _make_client([
        {"id": 10, "employee_id": [101, "E101"], "state": "close",
         "date_start": "2019-01-01", "date_end": "2021-12-31"},
        {"id": 11, "employee_id": [101, "E101"], "state": "open",
         "date_start": "2025-01-01", "date_end": False},
    ])
    result = await get_tenure_distribution(client=client)

    bands = {b["band"]: b["count"] for b in result["bands"]}
    assert bands["y3_5"] == 1, (
        "net-accumulated service (1609d ≈ 4.4y) must land in 'y3_5'"
    )
    assert bands["y5_10"] == 0, (
        "naive first→today (7.4y) would give 'y5_10' — gap must be excluded from tenure"
    )
    assert bands["y1_3"] == 0, (
        "current-contract-only (1.4y) would give 'y1_3' — prior service must count"
    )
    assert result["total_employed"] == 1
    assert result["missing_date_count"] == 0


# ── Test 12 — GENERAL LOGIC: overlapping contracts clamped ───────────────────
# Employee 201 has a closed contract AND a Running contract that both start
# 2024-01-01. Contract A (close) ends 2026-01-01; Contract B (open) is open-
# ended (→ ref 2026-05-29). The period from 2024-01-01 to 2026-01-01 is covered
# by BOTH contracts — without clamping this would be double-counted.
#
# Clamped:
#   merged = [(2024-01-01, 2026-05-29)]  ← max(2026-01-01, 2026-05-29)
#   total_days = 880 → virtual_start = 2024-01-01
#   _tenure_years(2024-01-01, 2026-05-29) = 2 → "y1_3"
#
# Naive (unclamped): 731 + 880 = 1611 days → virtual_start ≈ 2021-12-31
#   _tenure_years(~2021-12-31, 2026-05-29) = 4 → "y3_5"  ← WRONG (double-count)


async def test_two_overlapping_contracts_overlap_clamped_not_double_counted(monkeypatch):
    _freeze_ref_date(monkeypatch)
    client = _make_client([
        {"id": 20, "employee_id": [201, "E201"], "state": "close",
         "date_start": "2024-01-01", "date_end": "2026-01-01"},
        {"id": 21, "employee_id": [201, "E201"], "state": "open",
         "date_start": "2024-01-01", "date_end": False},
    ])
    result = await get_tenure_distribution(client=client)

    bands = {b["band"]: b["count"] for b in result["bands"]}
    assert bands["y1_3"] == 1, (
        "clamped merged period (2024-01-01→2026-05-29 = 880d ≈ 2.4y) must be in 'y1_3'"
    )
    assert bands["y3_5"] == 0, (
        "naive double-count (1611d ≈ 4.4y) would give 'y3_5' — overlap must be clamped"
    )
    assert result["total_employed"] == 1
    assert result["missing_date_count"] == 0


# ── Test 13 — Open-ended contract (null date_end) uses today as endpoint ──────


async def test_open_ended_running_contract_uses_today_as_endpoint(monkeypatch):
    _freeze_ref_date(monkeypatch)
    # date_end=False → endpoint = cairo_today = 2026-05-29 (not a missing date)
    # date_start = 2024-01-01: total_days = 880; virtual_start = 2024-01-01
    # _tenure_years = 2 → "y1_3"
    client = _make_client([
        {"id": 30, "employee_id": [301, "E301"], "state": "open",
         "date_start": "2024-01-01", "date_end": False}
    ])
    result = await get_tenure_distribution(client=client)

    assert result["missing_date_count"] == 0, (
        "null date_end is open-ended, not a missing date — endpoint is cairo_today"
    )
    assert result["total_employed"] == 1
    bands = {b["band"]: b["count"] for b in result["bands"]}
    assert bands["y1_3"] == 1


# ── Test 14 — Null date_start on Running contract → missing_date_count ────────


async def test_null_date_start_on_running_contract_counted_as_missing(monkeypatch):
    _freeze_ref_date(monkeypatch)
    client = _make_client([
        {"id": 40, "employee_id": [401, "E401"], "state": "open",
         "date_start": False, "date_end": "2026-06-30"}
    ])
    result = await get_tenure_distribution(client=client)

    assert result["missing_date_count"] == 1
    assert result["total_employed"] == 1
    assert sum(b["count"] for b in result["bands"]) == 0


# ── Test 15 — Departed employee (no Running contract) excluded entirely ────────


async def test_departed_employee_no_running_contract_excluded(monkeypatch):
    _freeze_ref_date(monkeypatch)
    # Employee 501 holds only a close contract — no Running contract.
    # Must not appear in total_employed, missing_date_count, or any band.
    client = _make_client([
        {"id": 50, "employee_id": [501, "E501"], "state": "close",
         "date_start": "2020-01-01", "date_end": "2023-12-31"}
    ])
    result = await get_tenure_distribution(client=client)

    assert result["total_employed"] == 0
    assert result["missing_date_count"] == 0
    assert sum(b["count"] for b in result["bands"]) == 0


# ── Test 16 — Sanity invariant: sum(bands) + missing == total_employed ─────────


async def test_sanity_invariant_holds():
    contracts = [
        # 3 computable Running employees
        {"id": 1, "employee_id": [1, "E1"], "state": "open",
         "date_start": "2026-01-01", "date_end": False},
        {"id": 2, "employee_id": [2, "E2"], "state": "open",
         "date_start": "2024-01-01", "date_end": False},
        {"id": 3, "employee_id": [3, "E3"], "state": "open",
         "date_start": "2020-01-01", "date_end": False},
        # employee 4: Running with null date_start → missing
        {"id": 4, "employee_id": [4, "E4"], "state": "open",
         "date_start": False, "date_end": "2026-06-30"},
        # employee 5: departed (close only) → excluded from total_employed entirely
        {"id": 5, "employee_id": [5, "E5"], "state": "close",
         "date_start": "2019-01-01", "date_end": "2022-12-31"},
    ]
    client = _make_client(contracts)
    result = await get_tenure_distribution(client=client)

    band_sum = sum(b["count"] for b in result["bands"])
    assert band_sum + result["missing_date_count"] == result["total_employed"], (
        f"band_sum ({band_sum}) + missing ({result['missing_date_count']}) "
        f"must == total_employed ({result['total_employed']})"
    )
    # Running employees: 1, 2, 3, 4 → total_employed=4; departed 5 is excluded
    assert result["total_employed"] == 4
    assert result["missing_date_count"] == 1   # employee 4
    assert band_sum == 3                        # employees 1, 2, 3


# ── Test 17 — All-zero edge case ──────────────────────────────────────────────


async def test_all_zero_edge_case():
    client = _make_client([])
    result = await get_tenure_distribution(client=client)

    assert result["total_employed"] == 0
    assert result["missing_date_count"] == 0
    assert len(result["bands"]) == 5
    assert all(b["count"] == 0 for b in result["bands"])
    assert result["cache_status"] == "fresh"


# ── Test 18 — missing_date_count populated, bands all zero ───────────────────


async def test_missing_date_count_populated(monkeypatch):
    _freeze_ref_date(monkeypatch)
    # 3 Running employees, all with null date_start → all missing
    contracts = [
        {"id": i, "employee_id": [100 + i, f"E{i}"], "state": "open",
         "date_start": False, "date_end": "2026-06-30"}
        for i in range(1, 4)
    ]
    client = _make_client(contracts)
    result = await get_tenure_distribution(client=client)

    assert result["missing_date_count"] == 3
    assert result["total_employed"] == 3
    assert all(b["count"] == 0 for b in result["bands"])


# ── Test 19 — Cache hit ───────────────────────────────────────────────────────


async def test_second_call_served_from_cache(mock_client):
    result1 = await get_tenure_distribution(client=mock_client)
    result2 = await get_tenure_distribution(client=mock_client)

    assert mock_client.execute_kw.call_count == 1, (
        "execute_kw must fire exactly once on the first call; "
        "the second call must be served from cache"
    )
    assert result1["cache_status"] == "fresh"
    assert result2["cache_status"] == "cached"
    assert result2["rpc_duration_ms"] == 0
    assert result2["total_employed"] == result1["total_employed"]
    assert result2["bands"] == result1["bands"]


# ── Test 20 — Read-only guard fires before any RPC ────────────────────────────


async def test_read_only_guard_raises_before_rpc(monkeypatch, mock_client):
    monkeypatch.setattr(
        "backend.modules.hr.services.kpi_service.ALLOWED_METHODS",
        frozenset({"read_group", "write"}),
    )

    with pytest.raises(ReadOnlyViolationError):
        await get_tenure_distribution(client=mock_client)

    mock_client.execute_kw.assert_not_called()


# ── Test 21 — RPC failure raises OdooQueryError ──────────────────────────────


async def test_rpc_failure_raises_odoo_query_error(mock_client):
    mock_client.execute_kw.side_effect = RuntimeError("connection refused")

    with pytest.raises(OdooQueryError):
        await get_tenure_distribution(client=mock_client)


# ── Test 22 — RPC failure writes no cache entry ──────────────────────────────


async def test_rpc_failure_writes_no_cache_entry(monkeypatch):
    _freeze_ref_date(monkeypatch)
    client = MagicMock()
    client.execute_kw = AsyncMock(side_effect=RuntimeError("timeout"))

    with pytest.raises(OdooQueryError):
        await get_tenure_distribution(client=client)

    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_TENURE)
    assert _cache.get(cache_key) is None, "A failed RPC must not leave a cache entry"


# ── Test 23 — Cairo TZ reference date used (not UTC) ─────────────────────────


async def test_cairo_tz_reference_date_used(monkeypatch):
    fixed_cairo = date(2026, 1, 15)
    _freeze_ref_date(monkeypatch, ref_date=fixed_cairo)
    client = _make_client([])

    result = await get_tenure_distribution(client=client)

    assert result["reference_date"] == "2026-01-15", (
        f"reference_date must be Cairo TZ date {fixed_cairo.isoformat()!r}, "
        f"got {result['reference_date']!r}"
    )
