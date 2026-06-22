"""
Unit tests for the Projects Inventory Pricing Outliers service (Slice 2.5).

OdooClient is fully mocked — a dispatch returns fixed rs.structure.unit, rs.contract and
rs.payment.term search_read results, so no live Odoo connection is made. The synthetic
set is hand-built so EVERY flag is known in advance:

  Peer group P1 (zone 10, type 20, vintage bucket 2022) — 6 NC units, area 100 each,
  realized/m² = [19000, 20000, 20000, 20500, 21000, 35000]. Inclusive-linear quartiles:
  Q1=20000, median=20250, Q3=20875, IQR=875 → upper fence 22187.5. Only u6 (35000) is
  past the fence AND |dev|=72.84% ≥ 15 → Section A "above". u6 also sold above its own
  list (3.0M list vs 3.5M realized → −16.67% ≤ −10) → Section B "premium" → CONFIRMED.

  u7 (singleton group) sold at a 50% discount → Section B "deep" only (its peer group is
  too small for Section A → counted as insufficient-peers). u8 / u13 (Cassette) are clean
  singletons (population + insufficient, no flags). Excluded: u9 (sold, no contract),
  u10 (area 0), u11 (La Puerta), u12 (available).

Covers: population scoping, the Tukey + min-deviation flag, the discount thresholds, the
A∩B "confirmed" join, insufficient-peers counting, sort order, per-project ↔ combined
reconciliation, La Puerta exclusion, the dedup-sum / cancel-state contract join, the sale
date via payment_term.contract_date, caching, the schema round-trip and the RPC guard.

Live verification: scripts/verify_pricing_outliers_live.py.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.core.exceptions import OdooQueryError
from backend.modules.projects_inventory.schemas import PricingOutliersOverview
from backend.modules.projects_inventory.services import cache as _cache
from backend.modules.projects_inventory.services.pricing_outliers_service import (
    _quantile,
    _vintage_bucket,
    get_pricing_outliers_overview,
)

_NC = [1, "New Capital "]
_CAS = [2, "Cassette "]
_LP = [3, "La puerta "]


@pytest.fixture(autouse=True)
def fresh_cache():
    _cache.clear()
    yield
    _cache.clear()


def _u(uid, state, project, amount, area, zone, utype, code="") -> dict:
    """One rs.structure.unit row (the fields pricing_outliers_service reads)."""
    return {
        "id": uid, "state": state, "project_id": list(project),
        "amount": amount, "total_area": area,
        "zone_id": [zone, f"Zone#{zone}"] if zone else False,
        "unit_type_id": [utype, f"Type#{utype}"] if utype else False,
        "code": code or f"U{uid}",
    }


def _ct(uid, sales_price, term_id, state="confirm") -> dict:
    """One rs.contract row (unit_id m2o, sales_price, state, payment_term_id m2o)."""
    return {
        "unit_id": [uid, f"u{uid}"], "sales_price": sales_price, "state": state,
        "payment_term_id": [term_id, f"PT{term_id}"] if term_id else False,
    }


# Payment-term → contract_date (the true sale date). All 2022/2023 → bucket 2022.
_TERMS = {
    1: "2022-03-01", 2: "2022-06-01", 3: "2023-01-01", 4: "2023-05-01",
    5: "2022-09-01", 6: "2023-07-01", 7: "2022-02-01", 8: "2022-04-01",
    10: "2022-01-01", 13: "2022-08-01",
}


def _units() -> list[dict]:
    return [
        # Peer group P1 (zone 10, type 20) — pm2 [20000,20000,21000,19000,20500,35000].
        _u(1, "contracted", _NC, 2_000_000, 100, 10, 20, "P1-1"),
        _u(2, "contracted", _NC, 2_000_000, 100, 10, 20, "P1-2"),
        _u(3, "contracted", _NC, 2_100_000, 100, 10, 20, "P1-3"),
        _u(4, "delivered", _NC, 1_900_000, 100, 10, 20, "P1-4"),
        _u(5, "contracted", _NC, 2_050_000, 100, 10, 20, "P1-5"),
        _u(6, "contracted", _NC, 3_000_000, 100, 10, 20, "P1-6"),   # A above + B premium → CONFIRMED
        # Singletons (insufficient peers for Section A).
        _u(7, "contracted", _NC, 2_000_000, 100, 11, 21, "S-7"),    # B deep (50% off)
        _u(8, "contracted", _NC, 2_000_000, 100, 12, 22, "S-8"),    # clean
        _u(13, "contracted", _CAS, 2_000_000, 100, 30, 40, "S-13"),  # Cassette, clean
        # Excluded from the population.
        _u(9, "contracted", _NC, 2_000_000, 100, 10, 20, "X-9"),    # NO contract
        _u(10, "contracted", _NC, 5_000_000, 0, 10, 20, "X-10"),    # area 0
        _u(11, "contracted", _LP, 9_000_000, 100, 99, 99, "X-11"),  # La Puerta
        _u(12, "available", _NC, 2_000_000, 100, 10, 20, "X-12"),   # not sold
    ]


def _contracts() -> list[dict]:
    return [
        _ct(1, 2_000_000, 1), _ct(1, 500_000, 1, state="cancel"),   # cancel excluded
        _ct(2, 1_000_000, 2), _ct(2, 1_000_000, 2),                  # dedup-sum → 2.0M
        _ct(3, 2_100_000, 3),
        _ct(4, 1_900_000, 4),
        _ct(5, 2_050_000, 5),
        _ct(6, 3_500_000, 6),
        _ct(7, 1_000_000, 7),                                        # 50% off 2.0M list
        _ct(8, 2_000_000, 8),
        _ct(10, 5_000_000, 10),                                      # area-0 unit
        _ct(11, 5_000_000, 11),                                      # La Puerta (never requested)
        _ct(13, 2_000_000, 13),
    ]


def _make_client(units, contracts, terms=_TERMS):
    def _dispatch(model, method, args=None, kwargs=None):
        if model == "rs.structure.unit" and method == "search_read":
            return units
        if model == "rs.contract" and method == "search_read":
            wanted = set(args[0][0][2])    # [('unit_id','in',[...])]
            return [c for c in contracts if c["unit_id"][0] in wanted]
        if model == "rs.payment.term" and method == "search_read":
            wanted = set(args[0][0][2])    # [('id','in',[...])]
            return [{"id": tid, "contract_date": d} for tid, d in terms.items() if tid in wanted]
        raise AssertionError(f"unexpected RPC: {model}.{method}")

    client = MagicMock()
    client.execute_kw = AsyncMock(side_effect=_dispatch)
    client.close = AsyncMock()
    return client


def _row_by_unit(rows):
    return {r["unit_id"]: r for r in rows}


# ── pure helpers ──────────────────────────────────────────────────────────────


def test_vintage_bucket_floors_to_two_years():
    assert _vintage_bucket(2022) == 2022
    assert _vintage_bucket(2023) == 2022
    assert _vintage_bucket(2024) == 2024
    assert _vintage_bucket(2018) == 2018


def test_quantile_inclusive_linear():
    vals = [19000, 20000, 20000, 20500, 21000, 35000]
    assert _quantile(vals, 0.25) == 20000
    assert _quantile(vals, 0.5) == 20250
    assert _quantile(vals, 0.75) == 20875
    assert _quantile([42.0], 0.5) == 42.0   # single-element group


# ── combined counts ───────────────────────────────────────────────────────────


async def test_combined_counts():
    r = await get_pricing_outliers_overview(client=_make_client(_units(), _contracts()))

    assert r["population_count"] == 9               # u1-u8 + u13
    assert r["eligible_group_count"] == 1           # only P1 (>=5)
    assert r["insufficient_peers_count"] == 3       # u7, u8, u13 (singletons)

    assert r["section_a_count"] == 1
    assert (r["section_a_below_count"], r["section_a_above_count"]) == (0, 1)
    assert r["section_b_count"] == 2
    assert (r["section_b_deep_count"], r["section_b_premium_count"]) == (1, 1)
    assert r["confirmed_count"] == 1


# ── Section A — the Tukey + min-deviation flag ────────────────────────────────


async def test_section_a_flags_only_the_peer_outlier():
    r = await get_pricing_outliers_overview(client=_make_client(_units(), _contracts()))
    assert [row["unit_id"] for row in r["section_a"]] == [6]
    a = r["section_a"][0]
    assert a["direction"] == "above"
    assert a["realized_pm2"] == 35_000.0
    assert a["group_median_pm2"] == 20_250.0
    assert a["deviation_pct"] == round((35_000 - 20_250) / 20_250 * 100, 2)   # 72.84
    assert a["vintage_bucket_label"] == "2022–2023"
    assert a["sale_date"] == "2023-07-01"
    assert a["is_confirmed"] is True
    assert a["zone_name"] == "Zone#10"
    assert a["unit_type_name"] == "Type#20"


# ── Section B — discount thresholds + sort (deep first, then premium) ─────────


async def test_section_b_flags_and_sort_order():
    r = await get_pricing_outliers_overview(client=_make_client(_units(), _contracts()))
    # Deep (u7, +50%) sorts before premium (u6, −16.67%).
    assert [row["unit_id"] for row in r["section_b"]] == [7, 6]
    by_id = _row_by_unit(r["section_b"])
    assert by_id[7]["kind"] == "deep"
    assert by_id[7]["discount_pct"] == 50.0
    assert by_id[7]["list_total"] == 2_000_000.0
    assert by_id[7]["realized_total"] == 1_000_000.0
    assert by_id[7]["is_confirmed"] is False
    assert by_id[6]["kind"] == "premium"
    assert by_id[6]["discount_pct"] == round((3_000_000 - 3_500_000) / 3_000_000 * 100, 2)  # -16.67
    assert by_id[6]["is_confirmed"] is True


# ── confirmed join ────────────────────────────────────────────────────────────


async def test_confirmed_is_marked_in_both_sections():
    r = await get_pricing_outliers_overview(client=_make_client(_units(), _contracts()))
    a_conf = {row["unit_id"] for row in r["section_a"] if row["is_confirmed"]}
    b_conf = {row["unit_id"] for row in r["section_b"] if row["is_confirmed"]}
    assert a_conf == b_conf == {6}
    assert r["confirmed_count"] == 1


# ── per-project ↔ combined ────────────────────────────────────────────────────


async def test_per_project_counts_and_reconcile():
    r = await get_pricing_outliers_overview(client=_make_client(_units(), _contracts()))
    by_pid = {p["project_id"]: p for p in r["projects"]}
    assert set(by_pid) == {1, 2}                    # NC + Cassette (both have population)
    assert (by_pid[1]["section_a_count"], by_pid[1]["section_b_count"],
            by_pid[1]["confirmed_count"]) == (1, 2, 1)
    assert (by_pid[2]["section_a_count"], by_pid[2]["section_b_count"],
            by_pid[2]["confirmed_count"]) == (0, 0, 0)
    # Σ per-project == combined.
    assert sum(p["section_a_count"] for p in r["projects"]) == r["section_a_count"]
    assert sum(p["section_b_count"] for p in r["projects"]) == r["section_b_count"]
    assert sum(p["confirmed_count"] for p in r["projects"]) == r["confirmed_count"]


# ── exclusions ────────────────────────────────────────────────────────────────


async def test_excluded_units_never_appear():
    r = await get_pricing_outliers_overview(client=_make_client(_units(), _contracts()))
    seen = {row["unit_id"] for row in r["section_a"]} | {row["unit_id"] for row in r["section_b"]}
    # La Puerta (11), no-contract (9), area-0 (10), available (12) never flagged.
    assert seen.isdisjoint({9, 10, 11, 12})
    # No La Puerta project row.
    assert 3 not in {p["project_id"] for p in r["projects"]}


# ── thresholds echo ───────────────────────────────────────────────────────────


async def test_thresholds_echoed():
    r = await get_pricing_outliers_overview(client=_make_client(_units(), _contracts()))
    t = r["thresholds"]
    assert t["min_group_size"] == 5
    assert t["iqr_mult"] == 1.5
    assert t["min_dev_pct"] == 15.0
    assert t["deep_discount_pct"] == 25.0
    assert t["premium_pct"] == -10.0
    assert t["vintage_bucket_years"] == 2


# ── schema round-trip ─────────────────────────────────────────────────────────


async def test_service_output_validates_against_schema():
    r = await get_pricing_outliers_overview(client=_make_client(_units(), _contracts()))
    model = PricingOutliersOverview.model_validate(r)
    assert model.population_count == 9
    assert model.confirmed_count == 1
    assert model.section_a[0].unit_id == 6
    assert model.section_b[0].kind == "deep"


# ── caching ───────────────────────────────────────────────────────────────────


async def test_cache_hit_on_second_call():
    client = _make_client(_units(), _contracts())
    first = await get_pricing_outliers_overview(client=client)
    assert first["cache_status"] == "fresh"
    second = await get_pricing_outliers_overview(client=client)
    assert second["cache_status"] == "cached"
    assert second["rpc_duration_ms"] == 0
    # First call: 1 units + 1 contracts + 1 terms search_read; second served from cache.
    assert client.execute_kw.await_count == 3


# ── guards ────────────────────────────────────────────────────────────────────


async def test_unknown_state_raises():
    bad = [_u(1, "frozen", _NC, 1_000_000, 100, 10, 20)]
    with pytest.raises(RuntimeError, match="state value"):
        await get_pricing_outliers_overview(client=_make_client(bad, []))


async def test_rpc_failure_wrapped_as_odoo_query_error():
    client = MagicMock()
    client.execute_kw = AsyncMock(side_effect=Exception("connection refused"))
    client.close = AsyncMock()
    with pytest.raises(OdooQueryError):
        await get_pricing_outliers_overview(client=client)
