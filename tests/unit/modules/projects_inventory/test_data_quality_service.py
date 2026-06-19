"""
Unit tests for the Projects Inventory Data Quality service.

OdooClient is fully mocked — a dispatch returns fixed rs.structure.unit, rs.contract and
parent-model (phase/zone/building) search_read results, so no live Odoo connection is made.
Covers: Check A (cancel-contract excluded; dedup of a unit with two non-cancel contracts;
no-contract flagged), Check B (each break type + a clean unit, first-break-wins), Check C,
La Puerta's unpriced AVAILABLE unit NOT flagged, defect_type/detail generation, the
sort order, caching, the schema round-trip, and the RPC-failure guard.

Live verification: scripts/verify_inventory_data_quality_live.py.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.core.exceptions import OdooQueryError
from backend.modules.projects_inventory.schemas import DataQualityOverview
from backend.modules.projects_inventory.services import cache as _cache
from backend.modules.projects_inventory.services.data_quality_service import (
    get_data_quality_overview,
)

_P1 = [1, "New Capital"]
_P2 = [2, "Cassette"]
_P3 = [3, "La Puerta"]


@pytest.fixture(autouse=True)
def fresh_cache():
    _cache.clear()
    yield
    _cache.clear()


def _u(uid, code, state, project, phase, zone, building, amount) -> dict:
    """One rs.structure.unit row (the fields the DQ service reads)."""
    return {
        "id": uid, "code": code, "state": state, "project_id": list(project),
        "phase_id": list(phase), "zone_id": list(zone), "building_id": list(building),
        "amount": amount,
    }


def _ct(uid, state="confirm") -> dict:
    """One rs.contract row (unit_id m2o + state — the DQ service ignores price)."""
    return {"unit_id": [uid, f"u{uid}"], "state": state}


# Parent records' OWN upward m2o (the source of truth).
#   phase  → project ; zone → phase ; building → zone.
_PHASES = {10: 1, 11: 1, 20: 2, 30: 3}
_ZONES = {100: 10, 101: 11, 200: 20, 300: 30}
_BUILDINGS = {1000: 100, 1001: 101, 2000: 200, 3000: 300}


def _ph(pid):
    return [pid, f"Phase#{pid}"]


def _zo(zid):
    return [zid, f"Zone#{zid}"]


def _bu(bid):
    return [bid, f"Building#{bid}"]


def _units() -> list[dict]:
    return [
        # ── Check A population (all NC, sold) ────────────────────────────────────
        _u(1, "NC-CLEAN", "contracted", _P1, _ph(10), _zo(100), _bu(1000), 1_000_000),     # covered
        _u(2, "NC-CANCELONLY", "delivered", _P1, _ph(10), _zo(100), _bu(1000), 2_000_000), # only-cancel → flagged A
        _u(3, "NC-NOCONTRACT", "contracted", _P1, _ph(10), _zo(100), _bu(1000), 3_000_000),# none → flagged A
        _u(4, "NC-DEDUP", "contracted", _P1, _ph(10), _zo(100), _bu(1000), 4_000_000),     # 2 contracts → covered once
        # ── Check B population (all NC, AVAILABLE so they never touch A/C) ────────
        _u(5, "NC-PHPROJ", "available", _P1, _ph(20), _zo(200), _bu(2000), 5_000_000),  # phase_project
        _u(6, "NC-ZONEPH", "available", _P1, _ph(10), _zo(200), _bu(2000), 6_000_000),  # zone_phase
        _u(7, "NC-BLDZONE", "available", _P1, _ph(10), _zo(100), _bu(2000), 7_000_000), # building_zone
        # ── Check C population (Cassette, sold, amount 0; has a contract so NOT A) ─
        _u(8, "CAS-NOLIST", "contracted", _P2, _ph(20), _zo(200), _bu(2000), 0),
        # ── La Puerta unpriced AVAILABLE unit (clean chain): must NOT be flagged ──
        _u(9, "LP-UNPRICED", "available", _P3, _ph(30), _zo(300), _bu(3000), 0),
    ]


def _contracts() -> list[dict]:
    return [
        _ct(1, "confirm"),
        _ct(2, "cancel"),               # cancel only → unit 2 stays flagged A
        _ct(4, "confirm"),
        _ct(4, "confirm"),              # duplicate non-cancel → covered once (dedup)
        _ct(8, "confirm"),              # unit 8 covered → flagged C only, not A
    ]


def _make_client(units, contracts, phases=_PHASES, zones=_ZONES, buildings=_BUILDINGS):
    def _dispatch(model, method, args=None, kwargs=None):
        if model == "rs.structure.unit" and method == "search_read":
            return units
        if model == "rs.contract" and method == "search_read":
            wanted = set(args[0][0][2])    # [('unit_id','in',[...])]
            return [c for c in contracts if c["unit_id"][0] in wanted]
        if model == "rs.structure.phase" and method == "search_read":
            return [{"id": k, "project_id": [v, f"P{v}"]} for k, v in phases.items()]
        if model == "rs.structure.zone" and method == "search_read":
            return [{"id": k, "phase_id": [v, f"Phase#{v}"]} for k, v in zones.items()]
        if model == "rs.structure.building" and method == "search_read":
            return [{"id": k, "zone_id": [v, f"Zone#{v}"]} for k, v in buildings.items()]
        raise AssertionError(f"unexpected RPC: {model}.{method}")

    client = MagicMock()
    client.execute_kw = AsyncMock(side_effect=_dispatch)
    client.close = AsyncMock()
    return client


def _checks(result):
    return {c["key"]: c for c in result["checks"]}


# ── counts + totals ─────────────────────────────────────────────────────────


async def test_check_counts_and_total():
    r = await get_data_quality_overview(client=_make_client(_units(), _contracts()))
    ck = _checks(r)
    assert ck["no_contract"]["count"] == 2          # units 2, 3
    assert ck["broken_hierarchy"]["count"] == 3     # units 5, 6, 7
    assert ck["no_list_price"]["count"] == 1        # unit 8
    assert r["total_issues"] == 6
    assert sum(c["count"] for c in r["checks"]) == 6


# ── Check A — coverage, cancel exclusion, dedup ───────────────────────────────


async def test_check_a_cancel_excluded_and_dedup():
    r = await get_data_quality_overview(client=_make_client(_units(), _contracts()))
    a = _checks(r)["no_contract"]
    codes = [it["code"] for it in a["items"]]
    # Unit 2 (cancel only) + unit 3 (none) flagged; unit 1 (covered) and unit 4
    # (two non-cancel contracts, deduped to one) are NOT flagged.
    assert codes == ["NC-CANCELONLY", "NC-NOCONTRACT"]
    assert all(it["defect_type"] == "no_contract" for it in a["items"])
    # detail carries the unit's list amount.
    details = {it["code"]: it["detail"] for it in a["items"]}
    assert details["NC-CANCELONLY"] == "amount 2,000,000"
    assert details["NC-NOCONTRACT"] == "amount 3,000,000"


# ── Check B — each break type + clean unit, first-break-wins, detail ──────────


async def test_check_b_break_types_and_detail():
    r = await get_data_quality_overview(client=_make_client(_units(), _contracts()))
    b = _checks(r)["broken_hierarchy"]
    by_code = {it["code"]: it for it in b["items"]}
    # The clean NC units (1–4) and the clean La Puerta unit (9) are NOT flagged.
    assert set(by_code) == {"NC-PHPROJ", "NC-ZONEPH", "NC-BLDZONE"}

    assert by_code["NC-PHPROJ"]["defect_type"] == "phase_project"
    assert by_code["NC-PHPROJ"]["detail"] == "phase 20 'Phase#20' → project 2; unit project_id=1"

    assert by_code["NC-ZONEPH"]["defect_type"] == "zone_phase"
    assert by_code["NC-ZONEPH"]["detail"] == "zone 200 'Zone#200' → phase 20; unit phase_id=10"

    assert by_code["NC-BLDZONE"]["defect_type"] == "building_zone"
    assert by_code["NC-BLDZONE"]["detail"] == "building 2000 'Building#2000' → zone 200; unit zone_id=100"


# ── Check C — sold, no list price ─────────────────────────────────────────────


async def test_check_c_no_list_price():
    r = await get_data_quality_overview(client=_make_client(_units(), _contracts()))
    c = _checks(r)["no_list_price"]
    assert [it["code"] for it in c["items"]] == ["CAS-NOLIST"]
    assert c["items"][0]["defect_type"] == "no_list_price"
    assert c["items"][0]["detail"] == ""
    assert c["items"][0]["project_name"] == "Cassette"


# ── La Puerta's unpriced AVAILABLE unit is never flagged ──────────────────────


async def test_la_puerta_unpriced_available_not_flagged():
    r = await get_data_quality_overview(client=_make_client(_units(), _contracts()))
    all_codes = {it["code"] for c in r["checks"] for it in c["items"]}
    assert "LP-UNPRICED" not in all_codes


# ── sort order — by project then code, within each check ──────────────────────


async def test_items_sorted_by_project_then_code():
    # Add a Cassette no-contract sold unit so Check A spans two projects; assert the
    # cross-project sort (Cassette before New Capital) then code within project.
    units = _units() + [
        _u(20, "CAS-NOCONTRACT", "contracted", _P2, _ph(20), _zo(200), _bu(2000), 9_000_000),
    ]
    r = await get_data_quality_overview(client=_make_client(units, _contracts()))
    a_items = _checks(r)["no_contract"]["items"]
    assert [(it["project_name"], it["code"]) for it in a_items] == [
        ("Cassette", "CAS-NOCONTRACT"),
        ("New Capital", "NC-CANCELONLY"),
        ("New Capital", "NC-NOCONTRACT"),
    ]


# ── caching ───────────────────────────────────────────────────────────────────


async def test_cache_hit_on_second_call():
    client = _make_client(_units(), _contracts())
    first = await get_data_quality_overview(client=client)
    assert first["cache_status"] == "fresh"
    # 1 units + 1 contracts chunk + 3 parent maps = 5 RPCs.
    assert client.execute_kw.await_count == 5
    second = await get_data_quality_overview(client=client)
    assert second["cache_status"] == "cached"
    assert second["rpc_duration_ms"] == 0
    assert client.execute_kw.await_count == 5     # served from cache → no further RPC
    assert second["total_issues"] == first["total_issues"]


# ── schema round-trip ─────────────────────────────────────────────────────────


async def test_service_output_validates_against_schema():
    r = await get_data_quality_overview(client=_make_client(_units(), _contracts()))
    model = DataQualityOverview.model_validate(r)
    assert model.total_issues == 6
    assert {c.key for c in model.checks} == {"no_contract", "broken_hierarchy", "no_list_price"}


# ── guards ────────────────────────────────────────────────────────────────────


async def test_unknown_state_raises():
    bad = [_u(1, "X", "frozen", _P1, _ph(10), _zo(100), _bu(1000), 1_000_000)]
    with pytest.raises(RuntimeError, match="state value"):
        await get_data_quality_overview(client=_make_client(bad, []))


async def test_rpc_failure_wrapped_as_odoo_query_error():
    client = MagicMock()
    client.execute_kw = AsyncMock(side_effect=Exception("connection refused"))
    client.close = AsyncMock()
    with pytest.raises(OdooQueryError):
        await get_data_quality_overview(client=client)
