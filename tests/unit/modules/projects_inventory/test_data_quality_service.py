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


def _u(uid, code, state, project, phase, zone, building, amount,
       *, total_area=0, unit_type=None, meter_price=0) -> dict:
    """One rs.structure.unit row (the fields the DQ service reads). Check D additionally
    reads total_area, unit_type_id and meter_price; Checks A/B/C ignore them. The defaults
    (area 0, no type) keep a unit OUT of Check D's priced population, so the A/B/C fixtures
    are unaffected."""
    row = {
        "id": uid, "code": code, "state": state, "project_id": list(project),
        "phase_id": list(phase), "zone_id": list(zone), "building_id": list(building),
        "amount": amount, "total_area": total_area, "meter_price": meter_price,
    }
    if unit_type is not None:
        row["unit_type_id"] = list(unit_type)
    return row


def _ct(uid, state="confirm", *, sales_price=0, payment_term=None) -> dict:
    """One rs.contract row. Check A reads only (unit_id, state); Check D also reads
    sales_price + payment_term_id (the defaults keep it inert for the A/B/C fixtures)."""
    row = {"unit_id": [uid, f"u{uid}"], "state": state, "sales_price": sales_price}
    if payment_term is not None:
        row["payment_term_id"] = [payment_term, f"term{payment_term}"]
    return row


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


def _make_client(units, contracts, phases=_PHASES, zones=_ZONES, buildings=_BUILDINGS,
                 payment_terms=None):
    payment_terms = payment_terms or {}

    def _dispatch(model, method, args=None, kwargs=None):
        if model == "rs.structure.unit" and method == "search_read":
            return units
        if model == "rs.contract" and method == "search_read":
            wanted = set(args[0][0][2])    # [('unit_id','in',[...])]
            return [c for c in contracts if c["unit_id"][0] in wanted]
        if model == "rs.payment.term" and method == "search_read":
            wanted = set(args[0][0][2])    # [('id','in',[...])]
            return [{"id": tid, "contract_date": d}
                    for tid, d in payment_terms.items() if tid in wanted]
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
    # 1 units + 1 Check-A contracts chunk + 3 parent maps + 1 Check-D contracts chunk = 6
    # RPCs (no Check-D terms read — these fixtures carry no payment_term_id).
    assert client.execute_kw.await_count == 6
    second = await get_data_quality_overview(client=client)
    assert second["cache_status"] == "cached"
    assert second["rpc_duration_ms"] == 0
    assert client.execute_kw.await_count == 6     # served from cache → no further RPC
    assert second["total_issues"] == first["total_issues"]


# ── schema round-trip ─────────────────────────────────────────────────────────


async def test_service_output_validates_against_schema():
    r = await get_data_quality_overview(client=_make_client(_units(), _contracts()))
    model = DataQualityOverview.model_validate(r)
    assert model.total_issues == 6
    assert {c.key for c in model.checks} == {"no_contract", "broken_hierarchy", "no_list_price"}
    # Check D rides alongside as a separate object (NOT in checks/total_issues).
    assert model.check_d.key == "implausible_list_price"
    assert model.check_d.count == 0   # the A/B/C fixtures carry no priced units


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


# ══════════════════════════════════════════════════════════════════════════════
# Check D — implausible list price/m² (New Capital + Cassette only)
# ══════════════════════════════════════════════════════════════════════════════
#
# Deterministic fixture (all clean NC chains so Check B never flags them; area = 100 m²
# unless noted, so list/m² = amount/100 and realized/m² = sales_price/100):
#   Studios (type 50, low-spread, realized/m² 20,000). Peer group zone 100 / 2022 has 5
#     sold members (101–105) → eligible (median 20,000). 105 lists at 65,000/m² → fires
#     BOTH Tier 1 (peer) and Tier 2a (type) → shown as "peer" (precedence + dedupe). 106
#     is a 2024-vintage studio alone in its peer group (not eligible) → Tier 2a (sold).
#     107 is an unsold studio listed at 65,000/m² → Tier 2a (unsold).
#   Apartments (type 60, low-spread, median 30,000 / max 45,000). 206 has area = 1 →
#     list/m² 3,000,000 → fires Tier 2a AND Tier 2b → shown "type" (2a beats 2b).
#   Villas (type 70, HIGH-spread: median 10,000 / max 30,000 → spread 3.0 ≥ 2.5, so Tier
#     2a is GATED). 306 lists at 50,000/m² (5× the median) → NOT flagged (the gate). 307
#     has area = 1 → list/m² 200,000 > 5×max → Tier 2b ("impossible").
#   Penthouses (type 80): only 2 sold → no type baseline; all three priced units are
#     UNEVALUABLE (no eligible peer group, no type baseline).
#   La Puerta studio (project 3): would flag, but project 3 is out of Check D scope.

_TYPE_STUDIO = [50, "Studio"]
_TYPE_APT = [60, "Apartment"]
_TYPE_VILLA = [70, "Villa"]
_TYPE_PENT = [80, "Penthouse"]

# Payment terms → contract_date (vintage). 9001 → 2022 bucket; 9002 → 2024 bucket.
_D_TERMS = {9001: "2022-06-01", 9002: "2024-01-01"}


def _du(uid, code, state, unit_type, amount, area, project=_P1) -> dict:
    """A Check-D unit on a clean New Capital chain. meter_price = list/m² (amount/area) —
    the realistic 'meter price == list/m²' case Check D surfaces."""
    meter = (amount / area) if area else 0
    return _u(uid, code, state, project, _ph(10), _zo(100), _bu(1000), amount,
              total_area=area, unit_type=unit_type, meter_price=meter)


def _d_units() -> list[dict]:
    return [
        # Studios (type 50) — peer group zone 100 / 2022 (101–105), eligible.
        _du(101, "STU-OK1", "contracted", _TYPE_STUDIO, 2_000_000, 100),
        _du(102, "STU-OK2", "contracted", _TYPE_STUDIO, 2_000_000, 100),
        _du(103, "STU-OK3", "contracted", _TYPE_STUDIO, 2_000_000, 100),
        _du(104, "STU-OK4", "contracted", _TYPE_STUDIO, 2_000_000, 100),
        _du(105, "STU-T1", "contracted", _TYPE_STUDIO, 6_500_000, 100),  # 65k/m² → Tier1+Tier2a → peer
        _du(106, "STU-T2A-SOLD", "delivered", _TYPE_STUDIO, 6_500_000, 100),  # 2024 vintage, alone → Tier2a
        _du(107, "STU-T2A-UNSOLD", "available", _TYPE_STUDIO, 6_500_000, 100),  # unsold → Tier2a
        # Apartments (type 60, low-spread).
        _du(201, "APT-OK1", "contracted", _TYPE_APT, 3_000_000, 100),
        _du(202, "APT-OK2", "contracted", _TYPE_APT, 3_000_000, 100),
        _du(203, "APT-OK3", "contracted", _TYPE_APT, 3_000_000, 100),
        _du(204, "APT-OK4", "contracted", _TYPE_APT, 3_000_000, 100),
        _du(205, "APT-OK5", "contracted", _TYPE_APT, 4_500_000, 100),
        _du(206, "APT-AREAERR", "available", _TYPE_APT, 3_000_000, 1),  # area=1 → Tier2a beats Tier2b → type
        # Villas (type 70, HIGH-spread → Tier 2a gated).
        _du(301, "VIL-OK1", "contracted", _TYPE_VILLA, 1_000_000, 100),
        _du(302, "VIL-OK2", "contracted", _TYPE_VILLA, 1_000_000, 100),
        _du(303, "VIL-OK3", "contracted", _TYPE_VILLA, 1_000_000, 100),
        _du(304, "VIL-OK4", "contracted", _TYPE_VILLA, 1_000_000, 100),
        _du(305, "VIL-OK5", "contracted", _TYPE_VILLA, 3_000_000, 100),
        _du(306, "VIL-GATE", "available", _TYPE_VILLA, 5_000_000, 100),  # 50k/m² but high-spread → NOT flagged
        _du(307, "VIL-T2B", "available", _TYPE_VILLA, 200_000, 1),  # area=1 → Tier2b impossible
        # Penthouses (type 80): 2 sold → no baseline → all priced units unevaluable.
        _du(401, "PEN-1", "contracted", _TYPE_PENT, 5_000_000, 100),
        _du(402, "PEN-2", "contracted", _TYPE_PENT, 5_000_000, 100),
        _du(403, "PEN-UNEVAL", "available", _TYPE_PENT, 9_000_000, 100),
        # La Puerta (project 3) studio that WOULD flag — but project 3 is out of scope.
        _u(901, "LP-STUDIO", "contracted", _P3, _ph(30), _zo(300), _bu(3000), 6_500_000,
           total_area=100, unit_type=_TYPE_STUDIO, meter_price=65_000),
    ]


def _d_contracts() -> list[dict]:
    return [
        _ct(101, sales_price=2_000_000, payment_term=9001),
        _ct(102, sales_price=2_000_000, payment_term=9001),
        _ct(103, sales_price=2_000_000, payment_term=9001),
        _ct(104, sales_price=2_000_000, payment_term=9001),
        _ct(105, sales_price=2_000_000, payment_term=9001),
        _ct(106, sales_price=2_000_000, payment_term=9002),   # 2024 → alone in its peer group
        _ct(201, sales_price=3_000_000),
        _ct(202, sales_price=3_000_000),
        _ct(203, sales_price=3_000_000),
        _ct(204, sales_price=3_000_000),
        _ct(205, sales_price=4_500_000),
        _ct(301, sales_price=1_000_000),
        _ct(302, sales_price=1_000_000),
        _ct(303, sales_price=1_000_000),
        _ct(304, sales_price=1_000_000),
        _ct(305, sales_price=3_000_000),
        _ct(401, sales_price=5_000_000),
        _ct(402, sales_price=5_000_000),
        _ct(901, sales_price=2_000_000, payment_term=9001),   # La Puerta — never fetched (out of scope)
    ]


def _d_client():
    return _make_client(_d_units(), _d_contracts(), payment_terms=_D_TERMS)


def _d_items(result):
    return {it["code"]: it for it in result["check_d"]["items"]}


async def test_check_d_counts_tiers_and_separate_from_abc():
    r = await get_data_quality_overview(client=_d_client())
    d = r["check_d"]
    assert d["key"] == "implausible_list_price"
    assert d["count"] == 5
    assert (d["tier1_count"], d["tier2a_count"], d["tier2b_count"]) == (1, 3, 1)
    assert d["tier1_count"] + d["tier2a_count"] + d["tier2b_count"] == d["count"]
    assert d["evaluated_count"] == 23
    assert d["unevaluable_count"] == 3
    # Check D is a separate object — A/B/C counts/total are untouched by it.
    assert r["total_issues"] == 0
    assert {c["key"] for c in r["checks"]} == {
        "no_contract", "broken_hierarchy", "no_list_price"}


async def test_check_d_tier1_peer_precedence_and_dedupe():
    # 105 fires BOTH Tier 1 (peer) and Tier 2a (type); it appears ONCE, shown as "peer".
    r = await get_data_quality_overview(client=_d_client())
    items = r["check_d"]["items"]
    assert sum(1 for it in items if it["code"] == "STU-T1") == 1
    row = _d_items(r)["STU-T1"]
    assert row["signal"] == "peer"
    assert row["state"] == "sold"
    assert row["list_pm2"] == 65_000.0
    assert row["meter_price"] == 65_000.0          # meter == list/m² (the fix target)
    assert row["anchor_realized_pm2"] == 20_000.0  # peer-group median realized/m²
    assert row["ratio"] == 3.25                    # 65,000 / 20,000
    assert row["list_total"] == 6_500_000.0


async def test_check_d_tier2a_low_spread_type_sold_and_unsold():
    r = await get_data_quality_overview(client=_d_client())
    by_code = _d_items(r)
    assert by_code["STU-T2A-SOLD"]["signal"] == "type"
    assert by_code["STU-T2A-SOLD"]["state"] == "sold"
    assert by_code["STU-T2A-UNSOLD"]["signal"] == "type"
    assert by_code["STU-T2A-UNSOLD"]["state"] == "unsold"
    # Both anchor on the unit-type MEDIAN realized/m².
    assert by_code["STU-T2A-SOLD"]["anchor_realized_pm2"] == 20_000.0
    assert by_code["STU-T2A-UNSOLD"]["anchor_realized_pm2"] == 20_000.0


async def test_check_d_low_spread_gate_high_spread_type_not_flagged():
    # VIL-GATE lists at 50,000/m² (5× its type median) but its type is HIGH-spread, so
    # Tier 2a is gated and Tier 2b does not fire → it must NOT be flagged.
    r = await get_data_quality_overview(client=_d_client())
    assert "VIL-GATE" not in _d_items(r)


async def test_check_d_tier2b_impossible_area_error():
    r = await get_data_quality_overview(client=_d_client())
    row = _d_items(r)["VIL-T2B"]
    assert row["signal"] == "impossible"
    assert row["state"] == "unsold"
    assert row["list_pm2"] == 200_000.0
    assert row["anchor_realized_pm2"] == 30_000.0  # unit-type MAX realized/m²


async def test_check_d_area_error_low_spread_shows_type_not_impossible():
    # APT-AREAERR fires Tier 2a AND Tier 2b (low-spread type) → precedence keeps it "type".
    r = await get_data_quality_overview(client=_d_client())
    assert _d_items(r)["APT-AREAERR"]["signal"] == "type"


async def test_check_d_unevaluable_units_skipped_and_counted():
    r = await get_data_quality_overview(client=_d_client())
    codes = set(_d_items(r))
    assert "PEN-UNEVAL" not in codes           # no peer group, no type baseline
    assert "PEN-1" not in codes
    assert "PEN-2" not in codes
    assert r["check_d"]["unevaluable_count"] == 3   # PEN-1, PEN-2, PEN-UNEVAL


async def test_check_d_la_puerta_excluded():
    r = await get_data_quality_overview(client=_d_client())
    assert "LP-STUDIO" not in _d_items(r)


async def test_check_d_sorted_by_ratio_desc():
    r = await get_data_quality_overview(client=_d_client())
    ratios = [it["ratio"] for it in r["check_d"]["items"]]
    assert ratios == sorted(ratios, reverse=True)
    # The area-error apartment (ratio 100) sorts first.
    assert r["check_d"]["items"][0]["code"] == "APT-AREAERR"


async def test_check_d_thresholds_echoed():
    r = await get_data_quality_overview(client=_d_client())
    th = r["check_d"]["thresholds"]
    assert th["list_trust_k"] == 2.0
    assert th["type_k"] == 3.0
    assert th["type_spread_max"] == 2.5
    assert th["impossible_k"] == 5.0
    assert th["min_group_size"] == 5


async def test_check_d_validates_against_schema():
    r = await get_data_quality_overview(client=_d_client())
    model = DataQualityOverview.model_validate(r)
    assert model.check_d.count == 5
    signals = {row.signal for row in model.check_d.items}
    assert signals == {"peer", "type", "impossible"}
