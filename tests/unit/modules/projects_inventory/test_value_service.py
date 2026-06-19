"""
Unit tests for the Projects Inventory Value & Area service (Slice 2).

OdooClient is fully mocked — a dispatch returns fixed rs.structure.unit and rs.contract
search_read results, so no live Odoo connection is made. Covers: the LIST/REALIZED
split, La Puerta exclusion, the contract join (incl. the duplicate-contract dedup-sum
and cancel-state exclusion), sold-units-without-a-contract coverage, the "% below list"
with-contract denominator, the divide-by-zero area guard, per-project ↔ combined
reconciliation, sort order, caching, the schema round-trip, and the RPC-failure guard.

Live verification: scripts/verify_projects_inventory_value_live.py.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.core.exceptions import OdooQueryError
from backend.modules.projects_inventory.schemas import ValueAreaOverview
from backend.modules.projects_inventory.services import cache as _cache
from backend.modules.projects_inventory.services.value_service import (
    _compute_scope,
    _realized_by_unit,
    _reconcile,
    get_value_area_overview,
)

_NC = [1, "New Capital "]
_CAS = [2, "Cassette "]
_LP = [3, "La puerta "]


@pytest.fixture(autouse=True)
def fresh_cache():
    _cache.clear()
    yield
    _cache.clear()


def _u(uid, state, project, amount, area) -> dict:
    """One rs.structure.unit row (the fields value_service reads)."""
    return {
        "id": uid, "state": state, "project_id": list(project),
        "amount": amount, "total_area": area,
    }


def _ct(uid, sales_price, state="confirm") -> dict:
    """One rs.contract row (unit_id m2o, sales_price, state)."""
    return {"unit_id": [uid, f"u{uid}"], "sales_price": sales_price, "state": state}


def _units() -> list[dict]:
    return [
        # New Capital (project 1): 2 available, 4 sold (1 no-contract), 1 reserved.
        _u(1, "available", _NC, 1_000_000, 100),
        _u(2, "available", _NC, 2_000_000, 200),
        _u(3, "contracted", _NC, 3_000_000, 300),   # realized == list
        _u(4, "contracted", _NC, 4_000_000, 400),   # realized < list (discount)
        _u(5, "delivered", _NC, 5_000_000, 500),    # realized > list (premium)
        _u(6, "contracted", _NC, 6_000_000, 600),   # NO contract — coverage gap
        _u(7, "reserved", _NC, 7_000_000, 700),     # reserved: neither avail nor sold
        # Cassette (project 2): 1 available, 1 sold (with a duplicate 0-price contract).
        _u(8, "available", _CAS, 10_000_000, 1_000),
        _u(9, "contracted", _CAS, 20_000_000, 2_000),
        # La Puerta (project 3): MUST be excluded from every figure.
        _u(10, "contracted", _LP, 99_000_000, 9_999),
    ]


def _contracts() -> list[dict]:
    return [
        _ct(3, 3_000_000),
        _ct(4, 3_600_000),
        _ct(4, 999_999, state="cancel"),    # cancel → excluded from realized
        _ct(5, 5_500_000),
        _ct(9, 0),                            # duplicate, junk price
        _ct(9, 18_000_000),                   # the real deal
        _ct(10, 99_000_000),                  # La Puerta — never requested / counted
    ]


def _make_client(units, contracts):
    def _dispatch(model, method, args=None, kwargs=None):
        if model == "rs.structure.unit" and method == "search_read":
            return units
        if model == "rs.contract" and method == "search_read":
            wanted = set(args[0][0][2])    # [('unit_id','in',[...])]
            return [c for c in contracts if c["unit_id"][0] in wanted]
        raise AssertionError(f"unexpected RPC: {model}.{method}")

    client = MagicMock()
    client.execute_kw = AsyncMock(side_effect=_dispatch)
    client.close = AsyncMock()
    return client


def _by_id(result):
    return {p["project_id"]: p for p in result["projects"]}


# ── combined + per-project values ─────────────────────────────────────────────


async def test_combined_values():
    r = await get_value_area_overview(client=_make_client(_units(), _contracts()))

    assert r["total_units"] == 9            # La Puerta's unit excluded (would be 10)
    assert r["available_units_count"] == 3
    assert r["sold_units_count"] == 5
    assert r["sold_units_with_contract_count"] == 4   # u6 has none
    assert r["available_list_value"] == 13_000_000
    assert r["available_area"] == 1_300
    assert r["sold_realized_value"] == 30_100_000     # 12.1M (NC) + 18M (Cas)
    assert r["sold_contracted_area"] == 3_800
    assert r["sold_list_value"] == 38_000_000
    assert r["gap_abs"] == 7_900_000
    assert r["gap_pct"] == round(7_900_000 / 38_000_000 * 100, 2)   # 20.79
    assert r["capture_pct"] == round(30_100_000 / 38_000_000 * 100, 2)
    assert r["sold_units_below_list_count"] == 2       # u4, u9
    assert r["pct_units_below_list"] == 50.0           # 2 / 4 with-contract
    assert r["avg_price_per_m2_realized"] == round(30_100_000 / 3_800, 2)
    assert r["project_count"] == 2


async def test_per_project_values_and_sort_order():
    r = await get_value_area_overview(client=_make_client(_units(), _contracts()))
    # Sorted by sold_list_value desc: Cassette (20M) before New Capital (18M).
    assert [p["project_id"] for p in r["projects"]] == [2, 1]

    by_id = _by_id(r)
    nc, cas = by_id[1], by_id[2]

    assert nc["available_list_value"] == 3_000_000
    assert nc["available_area"] == 300
    assert nc["sold_realized_value"] == 12_100_000     # cancel on u4 excluded
    assert nc["sold_list_value"] == 18_000_000          # all 4 sold (incl u6)
    assert nc["sold_contracted_area"] == 1_800
    assert nc["sold_units_count"] == 4
    assert nc["sold_units_with_contract_count"] == 3
    assert nc["sold_units_below_list_count"] == 1       # u4 only
    assert nc["pct_units_below_list"] == round(1 / 3 * 100, 2)
    assert nc["sold_pct_units"] == round(4 / 7 * 100, 2)

    assert cas["sold_realized_value"] == 18_000_000     # 0 + 18M dedup-sum
    assert cas["sold_list_value"] == 20_000_000
    assert cas["pct_units_below_list"] == 100.0


async def test_la_puerta_fully_excluded():
    r = await get_value_area_overview(client=_make_client(_units(), _contracts()))
    assert 3 not in _by_id(r)
    # La Puerta's 99M list / 99M realized must not appear anywhere.
    assert r["sold_list_value"] == 38_000_000
    assert r["sold_realized_value"] == 30_100_000
    assert r["sold_units_count"] == 5                   # not 6


# ── contract-join edges ───────────────────────────────────────────────────────


def test_realized_by_unit_dedup_sum_and_cancel():
    realized = _realized_by_unit(_contracts())
    assert realized[4] == 3_600_000        # cancel contract (999,999) excluded
    assert realized[9] == 18_000_000       # 0 + 18M summed (the 0 is harmless)
    assert 6 not in realized               # u6 has no contract at all


async def test_sold_unit_without_contract_kept_in_list_value_not_realized():
    r = await get_value_area_overview(client=_make_client(_units(), _contracts()))
    nc = _by_id(r)[1]
    # u6 (6M list) is in sold_list_value but contributes 0 realized; coverage shows it.
    assert nc["sold_units_count"] - nc["sold_units_with_contract_count"] == 1


# ── divide-by-zero area guard ─────────────────────────────────────────────────


def test_compute_scope_guards_zero_area_and_zero_list():
    # All-available scope: no sold units → no division blows up, all sold metrics 0.
    units = [_u(1, "available", _NC, 5_000_000, 500)]
    m = _compute_scope(units, {})
    assert m["sold_units_count"] == 0
    assert m["avg_price_per_m2_realized"] == 0.0
    assert m["gap_pct"] == 0.0
    assert m["capture_pct"] == 0.0
    assert m["pct_units_below_list"] == 0.0
    assert m["available_list_value"] == 5_000_000


# ── reconciliation guard ──────────────────────────────────────────────────────


def test_reconcile_raises_on_mismatch():
    combined = {
        "total_units": 5, "available_units_count": 1, "sold_units_count": 2,
        "sold_units_with_contract_count": 2, "sold_units_below_list_count": 0,
        "available_list_value": 100.0, "available_area": 10.0,
        "sold_realized_value": 90.0, "sold_contracted_area": 20.0,
        "sold_list_value": 100.0, "gap_abs": 10.0,
    }
    # A single project whose sums don't match the combined totals.
    bad = dict(combined)
    bad["total_units"] = 99
    with pytest.raises(RuntimeError, match="reconciliation FAILED"):
        _reconcile(combined, [bad])


def test_reconcile_raises_on_bad_gap():
    scope = {
        "total_units": 1, "available_units_count": 0, "sold_units_count": 1,
        "sold_units_with_contract_count": 1, "sold_units_below_list_count": 0,
        "available_list_value": 0.0, "available_area": 0.0,
        "sold_realized_value": 90.0, "sold_contracted_area": 1.0,
        "sold_list_value": 100.0, "gap_abs": 999.0,   # should be 10.0
    }
    with pytest.raises(RuntimeError, match="gap_abs"):
        _reconcile(scope, [scope])


# ── schema round-trip ─────────────────────────────────────────────────────────


async def test_service_output_validates_against_schema():
    r = await get_value_area_overview(client=_make_client(_units(), _contracts()))
    model = ValueAreaOverview.model_validate(r)
    assert model.project_count == 2
    assert model.projects[0].project_id == 2
    assert model.sold_realized_value == 30_100_000


# ── caching ───────────────────────────────────────────────────────────────────


async def test_cache_hit_on_second_call():
    client = _make_client(_units(), _contracts())
    first = await get_value_area_overview(client=client)
    assert first["cache_status"] == "fresh"
    second = await get_value_area_overview(client=client)
    assert second["cache_status"] == "cached"
    assert second["rpc_duration_ms"] == 0
    # First call: 1 units + 1 contracts search_read; second served from cache → no more.
    assert client.execute_kw.await_count == 2
    assert second["sold_realized_value"] == first["sold_realized_value"]


# ── guards ────────────────────────────────────────────────────────────────────


async def test_unknown_state_raises():
    bad = [_u(1, "frozen", _NC, 1_000_000, 100)]
    with pytest.raises(RuntimeError, match="state value"):
        await get_value_area_overview(client=_make_client(bad, []))


async def test_rpc_failure_wrapped_as_odoo_query_error():
    client = MagicMock()
    client.execute_kw = AsyncMock(side_effect=Exception("connection refused"))
    client.close = AsyncMock()
    with pytest.raises(OdooQueryError):
        await get_value_area_overview(client=client)
