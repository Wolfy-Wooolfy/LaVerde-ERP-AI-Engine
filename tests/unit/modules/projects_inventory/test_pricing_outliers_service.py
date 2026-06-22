"""
Unit tests for the Projects Inventory Pricing Outliers service (Slice 2.5 + the Section B
cohort/guard refinement).

OdooClient is fully mocked — a dispatch returns fixed rs.structure.unit, rs.contract and
rs.payment.term search_read results, so no live Odoo connection is made. The synthetic
set is hand-built so EVERY flag is known in advance. All vintages fall in bucket 2022.

  G_A (zone 10, type 20) — 6 NC units, area 100, realized/m² = [20000,20000,21000,19000,
  20500,35000]. Inclusive-linear quartiles → upper fence 22187.5; only u6 (35000) clears
  it (|dev| 72.84% ≥ 15) → Section A "above". u6 also sold above its own list (3.0M list vs
  3.5M realized → −16.67%) → Section B "premium" → CONFIRMED. The other five sit at 0%
  discount. Group median discount = 0.0.

  G_DEEP (zone 11, type 21) — 6 NC units at the ~25% HOUSE STANDARD: discounts
  [25,25,25,26,24] plus d6 = 40% on a slightly higher (but trustworthy) own list. The
  discount Tukey fence is 26.875, so the five ~25% units do NOT flag (the deliberate
  standard discount is not an anomaly) while d6 (40 > 26.875, ≥ 25) IS deep — peer median
  discount 25.0. None is a realized-price/m² outlier (Section A clean).

  G_GUARD (zone 12, type 22) — 5 NC units. g1–g4 at the 25% standard; g5 has an inflated
  list (3.0M vs a peer-normal 750k realized → list/m² 30000 = 4× the group's 7500 median
  realized/m²) so its 75% "discount" is a list-price data error → LIST-TRUST GUARD
  suppresses it (never deep), even though 75% ≥ 25%.

  Small groups (insufficient peers, no cohort to compare): s1 = 40% → deep via the absolute
  35% fallback (peer median None); s2 = 30% → below the fallback, not flagged; p1 sold 20%
  above list → premium (guard never applies). c1 (Cassette) is a clean singleton.

  Excluded: u60 (sold, no contract), u61 (area 0), u62 (La Puerta), u63 (available).

Covers: population scoping, the Tukey + min-deviation Section-A flag, the cohort-relative
deep rule + list-trust guard + small-group fallback, the premium rule (unchanged), the
peer_median_discount_pct surface, the A∩B "confirmed" join, insufficient-peers counting,
sort order, per-project ↔ combined reconciliation, La Puerta exclusion, the dedup-sum /
cancel-state contract join, the sale date via payment_term.contract_date, caching, the
schema round-trip and the RPC guard.

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


# Payment-term → contract_date (the true sale date). Term id == unit id; all 2022/2023 →
# bucket 2022. u6 keeps 2023-07-01 so the Section-A sale-date assertion is exact.
_TERMS = {
    1: "2022-03-01", 2: "2022-06-01", 3: "2023-01-01", 4: "2023-05-01",
    5: "2022-09-01", 6: "2023-07-01",
    20: "2022-01-01", 21: "2022-01-01", 22: "2022-01-01", 23: "2022-01-01",
    24: "2022-01-01", 25: "2022-01-01",
    30: "2022-01-01", 31: "2022-01-01", 32: "2022-01-01", 33: "2022-01-01", 34: "2022-01-01",
    40: "2022-02-01", 41: "2022-02-01", 42: "2022-02-01",
    50: "2022-08-01",
    61: "2022-01-01", 62: "2022-01-01",
}


def _units() -> list[dict]:
    return [
        # G_A (zone 10, type 20) — Section A peer outlier + confirmed premium.
        _u(1, "contracted", _NC, 2_000_000, 100, 10, 20, "GA-1"),
        _u(2, "contracted", _NC, 2_000_000, 100, 10, 20, "GA-2"),
        _u(3, "contracted", _NC, 2_100_000, 100, 10, 20, "GA-3"),
        _u(4, "delivered",  _NC, 1_900_000, 100, 10, 20, "GA-4"),
        _u(5, "contracted", _NC, 2_050_000, 100, 10, 20, "GA-5"),
        _u(6, "contracted", _NC, 3_000_000, 100, 10, 20, "GA-6"),    # A above + B premium → CONFIRMED
        # G_DEEP (zone 11, type 21) — ~25% house standard vs one cohort Tukey outlier.
        _u(20, "contracted", _NC, 1_000_000, 100, 11, 21, "GD-1"),   # 25%
        _u(21, "contracted", _NC, 1_000_000, 100, 11, 21, "GD-2"),   # 25%
        _u(22, "contracted", _NC, 1_000_000, 100, 11, 21, "GD-3"),   # 25%
        _u(23, "contracted", _NC, 1_000_000, 100, 11, 21, "GD-4"),   # 26%
        _u(24, "contracted", _NC, 1_000_000, 100, 11, 21, "GD-5"),   # 24%
        _u(25, "contracted", _NC, 1_250_000, 100, 11, 21, "GD-6"),   # 40% → DEEP (cohort outlier)
        # G_GUARD (zone 12, type 22) — inflated-list unit suppressed by the list-trust guard.
        _u(30, "contracted", _NC, 1_000_000, 100, 12, 22, "GG-1"),   # 25%
        _u(31, "contracted", _NC, 1_000_000, 100, 12, 22, "GG-2"),   # 25%
        _u(32, "contracted", _NC, 1_000_000, 100, 12, 22, "GG-3"),   # 25%
        _u(33, "contracted", _NC, 1_000_000, 100, 12, 22, "GG-4"),   # 25%
        _u(34, "contracted", _NC, 3_000_000, 100, 12, 22, "GG-5"),   # 75% but inflated list → GUARD
        # Small groups (insufficient peers → absolute small-group fallback only).
        _u(40, "contracted", _NC, 1_000_000, 100, 13, 23, "SM-DEEP"),  # 40% → small DEEP (≥35)
        _u(41, "contracted", _NC, 1_000_000, 100, 14, 24, "SM-MILD"),  # 30% → below the 35% cut
        _u(42, "contracted", _NC, 1_000_000, 100, 15, 25, "SM-PREM"),  # −20% → premium
        _u(50, "contracted", _CAS, 2_000_000, 100, 30, 40, "CAS-1"),   # Cassette clean singleton
        # Excluded from the population.
        _u(60, "contracted", _NC, 2_000_000, 100, 10, 20, "X-NOCT"),   # NO contract
        _u(61, "contracted", _NC, 5_000_000, 0, 10, 20, "X-AREA0"),    # area 0
        _u(62, "contracted", _LP, 9_000_000, 100, 99, 99, "X-LP"),     # La Puerta
        _u(63, "available",  _NC, 2_000_000, 100, 10, 20, "X-AVAIL"),  # not sold
    ]


def _contracts() -> list[dict]:
    return [
        _ct(1, 2_000_000, 1), _ct(1, 500_000, 1, state="cancel"),   # cancel excluded → u1 = 2.0M
        _ct(2, 1_000_000, 2), _ct(2, 1_000_000, 2),                  # dedup-sum → 2.0M
        _ct(3, 2_100_000, 3),
        _ct(4, 1_900_000, 4),
        _ct(5, 2_050_000, 5),
        _ct(6, 3_500_000, 6),
        _ct(20, 750_000, 20), _ct(21, 750_000, 21), _ct(22, 750_000, 22),
        _ct(23, 740_000, 23), _ct(24, 760_000, 24), _ct(25, 750_000, 25),
        _ct(30, 750_000, 30), _ct(31, 750_000, 31), _ct(32, 750_000, 32),
        _ct(33, 750_000, 33), _ct(34, 750_000, 34),
        _ct(40, 600_000, 40), _ct(41, 700_000, 41), _ct(42, 1_200_000, 42),
        _ct(50, 2_000_000, 50),
        _ct(61, 5_000_000, 61),                                      # area-0 unit (excluded)
        _ct(62, 5_000_000, 62),                                      # La Puerta (never requested)
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

    assert r["population_count"] == 21              # G_A 6 + G_DEEP 6 + G_GUARD 5 + 3 + c1
    assert r["eligible_group_count"] == 3           # G_A, G_DEEP, G_GUARD
    assert r["insufficient_peers_count"] == 4       # s1, s2, p1, c1 (singletons)

    assert r["section_a_count"] == 1
    assert (r["section_a_below_count"], r["section_a_above_count"]) == (0, 1)
    assert r["section_b_count"] == 4
    assert (r["section_b_deep_count"], r["section_b_premium_count"]) == (2, 2)
    assert r["confirmed_count"] == 1


# ── Section A — the Tukey + min-deviation flag (unchanged) ─────────────────────


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


# ── Section B (a) — the list-trust guard suppresses an inflated-list unit ──────


async def test_section_b_guard_suppresses_inflated_list():
    r = await get_pricing_outliers_overview(client=_make_client(_units(), _contracts()))
    b = _row_by_unit(r["section_b"])
    # g5: list/m² = 30000 > 2× the group's 7500 median realized/m², discount 75% ≥ 25% —
    # a list-price data error, NOT a real discount → never deep-flagged.
    assert 34 not in b


# ── Section B (b) — the deliberate ~25% house standard is NOT an anomaly ───────


async def test_section_b_house_standard_not_flagged():
    r = await get_pricing_outliers_overview(client=_make_client(_units(), _contracts()))
    b = _row_by_unit(r["section_b"])
    # The whole G_DEEP cohort sits at ~25% (24–26); none of those flag deep.
    for uid in (20, 21, 22, 23, 24):
        assert uid not in b


# ── Section B (c) — a cohort Tukey outlier ≥ 25% IS deep, with the peer median ─


async def test_section_b_cohort_outlier_flagged_with_peer_median():
    r = await get_pricing_outliers_overview(client=_make_client(_units(), _contracts()))
    b = _row_by_unit(r["section_b"])
    assert b[25]["kind"] == "deep"
    assert b[25]["discount_pct"] == 40.0
    assert b[25]["peer_median_discount_pct"] == 25.0   # the cohort norm it deviates from
    assert b[25]["is_confirmed"] is False              # not a Section-A outlier


# ── Section B (d) — small group falls back to the absolute ≥ 35% cut ───────────


async def test_section_b_small_group_absolute_fallback():
    r = await get_pricing_outliers_overview(client=_make_client(_units(), _contracts()))
    b = _row_by_unit(r["section_b"])
    assert b[40]["kind"] == "deep"                     # 40% ≥ 35% small-group cut
    assert b[40]["peer_median_discount_pct"] is None   # no cohort → no peer median
    assert 41 not in b                                  # 30% < 35% → not deep


# ── Section B (e) — premium is unchanged and unaffected by the guard ──────────


async def test_section_b_premium_unaffected_by_guard():
    r = await get_pricing_outliers_overview(client=_make_client(_units(), _contracts()))
    b = _row_by_unit(r["section_b"])
    # Eligible-group premium (u6) — guard does not apply; shows its cohort median discount.
    assert b[6]["kind"] == "premium"
    assert b[6]["peer_median_discount_pct"] == 0.0
    # Small-group premium (p1) — peer median None.
    assert b[42]["kind"] == "premium"
    assert b[42]["discount_pct"] == -20.0
    assert b[42]["peer_median_discount_pct"] is None


# ── Section B — sort (deep first by discount desc, then premium asc) ───────────


async def test_section_b_sort_order():
    r = await get_pricing_outliers_overview(client=_make_client(_units(), _contracts()))
    assert [row["kind"] for row in r["section_b"]] == ["deep", "deep", "premium", "premium"]
    deep = [row["unit_id"] for row in r["section_b"] if row["kind"] == "deep"]
    prem = [row["unit_id"] for row in r["section_b"] if row["kind"] == "premium"]
    assert deep == [25, 40]    # both 40% → code tie-break "GD-6" < "SM-DEEP"
    assert prem == [42, 6]     # −20% before −16.67% (most negative / biggest premium first)


# ── Section B (f) — confirmed = A ∩ new-B, marked in both lists ────────────────


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
            by_pid[1]["confirmed_count"]) == (1, 4, 1)
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
    # no-contract (60), area-0 (61), La Puerta (62), available (63) never flagged.
    assert seen.isdisjoint({60, 61, 62, 63})
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
    assert model.population_count == 21
    assert model.confirmed_count == 1
    assert model.section_a[0].unit_id == 6
    assert model.section_b[0].kind == "deep"
    # The new optional field round-trips: populated for an eligible deep, None small-group.
    b25 = next(row for row in model.section_b if row.unit_id == 25)
    assert b25.peer_median_discount_pct == 25.0
    b40 = next(row for row in model.section_b if row.unit_id == 40)
    assert b40.peer_median_discount_pct is None


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
