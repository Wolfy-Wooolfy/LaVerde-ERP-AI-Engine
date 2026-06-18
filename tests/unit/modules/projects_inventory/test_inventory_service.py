"""
Unit tests for the Projects Inventory service (Slice 1 — Inventory & Availability).

OdooClient is fully mocked: a dispatch returns a fixed rs.structure.unit
search_read result, so no live Odoo connection is made. Covers the LOCKED bucket
mapping (incl. initial→Reserved and delivered→Contracted), overall + per-project
aggregation, sold% math, the 0-in-a-bucket edge, the early-stage flag, reconciliation,
the reusable bucketing primitive over an arbitrary hierarchy field, caching, and the
unknown-state / RPC-failure guards.

Live verification: scripts/verify_projects_inventory_live.py.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.core.exceptions import InventoryScopeNotFoundError, OdooQueryError
from backend.modules.projects_inventory.domain import BUCKET_ORDER
from backend.modules.projects_inventory.services import cache as _cache
from backend.modules.projects_inventory.services.inventory_service import (
    _tally_by,
    get_inventory_drill,
    get_inventory_overview,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def fresh_cache():
    _cache.clear()
    yield
    _cache.clear()


_NC = [1, "Project#New Capital"]
_CAS = [2, "Project#Cassette"]
_LP = [3, "Project#La puerta"]


def _u(uid: int, state: str, project, phase=None) -> dict:
    """One rs.structure.unit search_read row (structural fields only — no PII)."""
    return {
        "id": uid,
        "state": state,
        "project_id": list(project),
        "phase_id": list(phase) if phase else [10, "Phase#1"],
        "zone_id": [20, "Zone#1"],
        "building_id": [30, "Building#1"],
    }


def _dataset() -> list[dict]:
    """3 projects, mixed states, ordered counts:
      New Capital (10): available×2, reserved×1, initial×1, contracted×4, delivered×2
      Cassette    (8) : available×4, reserved×1, contracted×3
      La puerta   (5) : available×5            (0 reserved, 0 contracted — edge + early-stage)
    overall buckets: available=11, reserved=3, contracted=9; total=23; sold=9/23.
    """
    rows: list[dict] = []
    uid = 0

    def add(n, state, project):
        nonlocal uid
        for _ in range(n):
            uid += 1
            rows.append(_u(uid, state, project))

    # New Capital
    add(2, "available", _NC)
    add(1, "reserved", _NC)
    add(1, "initial", _NC)        # → Reserved
    add(4, "contracted", _NC)
    add(2, "delivered", _NC)      # → Contracted
    # Cassette
    add(4, "available", _CAS)
    add(1, "reserved", _CAS)
    add(3, "contracted", _CAS)
    # La puerta
    add(5, "available", _LP)
    return rows


def _make_client(units: list[dict]):
    """Dispatch mock — only rs.structure.unit.search_read is expected."""

    def _dispatch(model, method, args=None, kwargs=None):
        if model == "rs.structure.unit" and method == "search_read":
            return units
        raise AssertionError(f"unexpected RPC: {model}.{method}")

    client = MagicMock()
    client.execute_kw = AsyncMock(side_effect=_dispatch)
    client.close = AsyncMock()
    return client


def _bucket(row: dict, key: str) -> dict:
    return next(b for b in row["buckets"] if b["key"] == key)


# ── Overall aggregation + bucket order ────────────────────────────────────────


async def test_overall_aggregation_and_bucket_order():
    result = await get_inventory_overview(client=_make_client(_dataset()))

    assert result["total_units"] == 23
    # Buckets always exactly 3, in BUCKET_ORDER.
    assert [b["key"] for b in result["buckets"]] == list(BUCKET_ORDER)
    assert _bucket(result, "available")["count"] == 11
    assert _bucket(result, "reserved")["count"] == 3
    assert _bucket(result, "contracted")["count"] == 9
    # Σ buckets == total (reconciliation holds).
    assert sum(b["count"] for b in result["buckets"]) == result["total_units"]


async def test_bucket_mapping_initial_and_delivered_fold():
    """initial → Reserved, delivered → Contracted (LOCKED). New Capital has 1 reserved
    + 1 initial → reserved=2, and 4 contracted + 2 delivered → contracted=6."""
    result = await get_inventory_overview(client=_make_client(_dataset()))
    nc = next(p for p in result["projects"] if p["project_id"] == 1)
    assert _bucket(nc, "reserved")["count"] == 2       # reserved(1) + initial(1)
    assert _bucket(nc, "contracted")["count"] == 6     # contracted(4) + delivered(2)
    assert _bucket(nc, "available")["count"] == 2


# ── Per-project aggregation + ordering ────────────────────────────────────────


async def test_per_project_aggregation_sorted_by_total_desc():
    result = await get_inventory_overview(client=_make_client(_dataset()))
    assert result["project_count"] == 3
    # Sorted by total_units desc: New Capital(10) > Cassette(8) > La puerta(5).
    assert [p["project_id"] for p in result["projects"]] == [1, 2, 3]
    assert [p["total_units"] for p in result["projects"]] == [10, 8, 5]
    assert [p["project_name"] for p in result["projects"]] == [
        "Project#New Capital", "Project#Cassette", "Project#La puerta",
    ]
    # Σ per-project totals == overall total.
    assert sum(p["total_units"] for p in result["projects"]) == result["total_units"]


# ── sold% math ────────────────────────────────────────────────────────────────


async def test_sold_pct_overall_and_per_project():
    result = await get_inventory_overview(client=_make_client(_dataset()))
    # overall: contracted 9 / total 23.
    assert result["sold_pct"] == round(100.0 * 9 / 23, 2)   # 39.13
    by_id = {p["project_id"]: p for p in result["projects"]}
    assert by_id[1]["sold_pct"] == 60.0     # 6 / 10
    assert by_id[2]["sold_pct"] == 37.5     # 3 / 8
    assert by_id[3]["sold_pct"] == 0.0      # 0 / 5


# ── 0-in-a-bucket edge + early-stage flag ─────────────────────────────────────


async def test_zero_bucket_edge_and_early_stage_flag():
    result = await get_inventory_overview(client=_make_client(_dataset()))
    by_id = {p["project_id"]: p for p in result["projects"]}

    lp = by_id[3]   # La puerta — only 'available'
    # All 3 buckets still emitted, the empty ones at 0.
    assert [b["key"] for b in lp["buckets"]] == list(BUCKET_ORDER)
    assert _bucket(lp, "available")["count"] == 5
    assert _bucket(lp, "reserved")["count"] == 0
    assert _bucket(lp, "contracted")["count"] == 0
    assert _bucket(lp, "reserved")["pct"] == 0.0
    # sold% 0 < 10 → early stage.
    assert lp["is_early_stage"] is True
    # Healthy projects are not flagged.
    assert by_id[1]["is_early_stage"] is False
    assert by_id[2]["is_early_stage"] is False


# ── reusable bucketing primitive over an arbitrary hierarchy field ────────────


def test_tally_by_supports_arbitrary_group_field():
    """The same primitive groups by ANY denormalised hierarchy field — proving
    per-phase / per-zone grouping is a one-line change in the next slice."""
    units = [
        _u(1, "available", _NC, phase=[10, "Phase#1"]),
        _u(2, "contracted", _NC, phase=[10, "Phase#1"]),
        _u(3, "reserved", _NC, phase=[11, "Phase#2"]),
    ]
    groups = _tally_by(units, "phase_id")
    by_id = {g["group_id"]: g for g in groups}
    assert by_id[10]["total"] == 2
    assert by_id[10]["buckets"]["available"] == 1
    assert by_id[10]["buckets"]["contracted"] == 1
    assert by_id[11]["total"] == 1
    assert by_id[11]["buckets"]["reserved"] == 1
    # group_field=None collapses to a single all-units group.
    overall = _tally_by(units, None)
    assert len(overall) == 1 and overall[0]["total"] == 3


# ── caching ───────────────────────────────────────────────────────────────────


async def test_cache_hit_on_second_call():
    client = _make_client(_dataset())
    first = await get_inventory_overview(client=client)
    assert first["cache_status"] == "fresh"
    second = await get_inventory_overview(client=client)
    assert second["cache_status"] == "cached"
    assert second["rpc_duration_ms"] == 0
    # Only ONE search_read despite two calls (second served from cache).
    assert client.execute_kw.await_count == 1
    # Payload identical apart from the cache-status/timing envelope.
    assert second["total_units"] == first["total_units"]
    assert second["buckets"] == first["buckets"]


# ── guards ────────────────────────────────────────────────────────────────────


async def test_unknown_state_raises():
    """A state outside the LOCKED 5-value map must raise (never be silently dropped)."""
    bad = [_u(1, "available", _NC), _u(2, "frozen", _NC)]
    with pytest.raises(RuntimeError, match="state value"):
        await get_inventory_overview(client=_make_client(bad))


async def test_rpc_failure_wrapped_as_odoo_query_error():
    client = MagicMock()
    client.execute_kw = AsyncMock(side_effect=Exception("connection refused"))
    client.close = AsyncMock()
    with pytest.raises(OdooQueryError):
        await get_inventory_overview(client=client)


# ══════════════════════════════════════════════════════════════════════════════
# Slice 1b — hierarchy drill-down
# ══════════════════════════════════════════════════════════════════════════════

# Hierarchy fixtures (structural codes only — no PII).
_P1 = [10, "Phase#1"]
_P2 = [11, "Phase#2"]
_Z1 = [20, "Zone#1"]
_Z2 = [21, "Zone#2"]
_Z3 = [22, "Zone#3"]
_B1 = [30, "Building#1"]
_B2 = [31, "Building#2"]
_B3 = [32, "Building#3"]
_B4 = [33, "Building#4"]
# A second project whose units must NEVER leak into a New-Capital drill scope.
_CZP = [90, "Phase#9"]
_CZZ = [91, "Zone#9"]
_CZB = [92, "Building#9"]


def _hu(uid, state, proj, phase, zone, bldg, code=None, name=None) -> dict:
    """One fully-qualified hierarchy unit row (structural fields only)."""
    return {
        "id": uid,
        "state": state,
        "project_id": list(proj),
        "phase_id": list(phase),
        "zone_id": list(zone),
        "building_id": list(bldg),
        "code": code if code is not None else f"U{uid}",
        "name": name if name is not None else str(uid),
    }


def _drill_dataset() -> list[dict]:
    """New Capital (9 units) across 2 phases / 3 zones / 4 buildings + a separate
    Cassette node (2 units) used to prove parent-scope isolation.

      NC / P1 / Z1 / B1 : available, available, contracted        (B1 = 3)
      NC / P1 / Z1 / B2 : reserved,  contracted                    (B2 = 2)
      NC / P1 / Z2 / B3 : available, contracted, contracted        (B3 = 3)
      NC / P2 / Z3 / B4 : available                                (B4 = 1)
      Cassette / P9 / Z9 / B9 : contracted, available              (excluded from NC)

    NC totals: available=4, reserved=1, contracted=4 → 9.
      P1 = 8 (a3, r1, c4) ; P2 = 1 (a1)
      Z1 = 5 (a2, r1, c2) ; Z2 = 3 (a1, c2)   (within P1)
      B1 = 3 (a2, c1)     ; B2 = 2 (r1, c1)    (within Z1)
    """
    return [
        # NC / P1 / Z1 / B1 — codes deliberately out of id order to test code sorting.
        _hu(1, "available", _NC, _P1, _Z1, _B1, code="NC-B1-C"),
        _hu(2, "available", _NC, _P1, _Z1, _B1, code="NC-B1-A"),
        _hu(3, "contracted", _NC, _P1, _Z1, _B1, code="NC-B1-B"),
        # NC / P1 / Z1 / B2
        _hu(4, "reserved", _NC, _P1, _Z1, _B2),
        _hu(5, "contracted", _NC, _P1, _Z1, _B2),
        # NC / P1 / Z2 / B3
        _hu(6, "available", _NC, _P1, _Z2, _B3),
        _hu(7, "contracted", _NC, _P1, _Z2, _B3),
        _hu(8, "delivered", _NC, _P1, _Z2, _B3),   # delivered → contracted bucket
        # NC / P2 / Z3 / B4
        _hu(9, "available", _NC, _P2, _Z3, _B4),
        # Cassette node (must be excluded from any NC scope)
        _hu(10, "contracted", _CAS, _CZP, _CZZ, _CZB),
        _hu(11, "available", _CAS, _CZP, _CZZ, _CZB),
    ]


def _bsum(buckets: list[dict]) -> int:
    return sum(b["count"] for b in buckets)


# ── project → phases ──────────────────────────────────────────────────────────


async def test_drill_project_returns_phases_with_scope_reconciliation():
    client = _make_client(_drill_dataset())
    res = await get_inventory_drill("project", 1, client=client)

    assert res["parent_level"] == "project"
    assert res["parent_id"] == 1
    assert res["parent_name"] == "Project#New Capital"
    assert res["child_level"] == "phase"
    assert res["is_leaf"] is False
    # Scope = only New Capital's 9 units (Cassette excluded).
    assert res["total_units"] == 9
    assert _bsum(res["buckets"]) == 9
    assert res["units"] == [] and res["unit_count"] == 0

    # Two phases, sorted by total desc (P1=8 before P2=1).
    assert [r["group_id"] for r in res["rows"]] == [10, 11]
    assert [r["total_units"] for r in res["rows"]] == [8, 1]
    assert res["row_count"] == 2
    # Σ child totals == scope total ; each row Σ buckets == its total.
    assert sum(r["total_units"] for r in res["rows"]) == res["total_units"]
    for r in res["rows"]:
        assert _bsum(r["buckets"]) == r["total_units"]
        assert [b["key"] for b in r["buckets"]] == list(BUCKET_ORDER)
    p1 = next(r for r in res["rows"] if r["group_id"] == 10)
    assert {b["key"]: b["count"] for b in p1["buckets"]} == {
        "available": 3, "reserved": 1, "contracted": 4,
    }
    assert p1["sold_pct"] == round(100.0 * 4 / 8, 2)   # 50.0


# ── phase → zones ─────────────────────────────────────────────────────────────


async def test_drill_phase_returns_zones():
    client = _make_client(_drill_dataset())
    res = await get_inventory_drill("phase", 10, client=client)

    assert res["child_level"] == "zone"
    assert res["is_leaf"] is False
    assert res["parent_name"] == "Phase#1"
    assert res["total_units"] == 8
    assert {r["group_id"]: r["total_units"] for r in res["rows"]} == {20: 5, 21: 3}
    assert sum(r["total_units"] for r in res["rows"]) == res["total_units"]


# ── zone → buildings ──────────────────────────────────────────────────────────


async def test_drill_zone_returns_buildings():
    client = _make_client(_drill_dataset())
    res = await get_inventory_drill("zone", 20, client=client)

    assert res["child_level"] == "building"
    assert res["is_leaf"] is False
    assert res["parent_name"] == "Zone#1"
    assert res["total_units"] == 5
    assert {r["group_id"]: r["total_units"] for r in res["rows"]} == {30: 3, 31: 2}
    assert sum(r["total_units"] for r in res["rows"]) == res["total_units"]


# ── building → unit leaf ──────────────────────────────────────────────────────


async def test_drill_building_returns_unit_leaf_sorted_by_code():
    client = _make_client(_drill_dataset())
    res = await get_inventory_drill("building", 30, client=client)

    assert res["is_leaf"] is True
    assert res["child_level"] == "unit"
    assert res["parent_name"] == "Building#1"
    assert res["rows"] == [] and res["row_count"] == 0

    units = res["units"]
    assert res["unit_count"] == len(units) == 3
    # Leaf len == scope total (per-unit reconciliation).
    assert res["total_units"] == 3
    # Sorted by code (codes were inserted out of order).
    assert [u["code"] for u in units] == ["NC-B1-A", "NC-B1-B", "NC-B1-C"]
    # Each leaf row carries code + name + raw state + board bucket.
    for u in units:
        assert set(u) == {"unit_id", "code", "name", "state", "bucket"}
    a = next(u for u in units if u["code"] == "NC-B1-A")
    assert a["state"] == "available" and a["bucket"] == "available"
    b = next(u for u in units if u["code"] == "NC-B1-B")
    assert b["state"] == "contracted" and b["bucket"] == "contracted"
    # Leaf bucket parity vs the scope header.
    assert {bk["key"]: bk["count"] for bk in res["buckets"]} == {
        "available": 2, "reserved": 0, "contracted": 1,
    }


async def test_drill_building_leaf_delivered_folds_to_contracted():
    """A delivered unit appears with raw state 'delivered' but bucket 'contracted'."""
    client = _make_client(_drill_dataset())
    res = await get_inventory_drill("building", 32, client=client)   # B3 has a delivered unit
    delivered = [u for u in res["units"] if u["state"] == "delivered"]
    assert len(delivered) == 1
    assert delivered[0]["bucket"] == "contracted"


# ── parent-scope isolation ────────────────────────────────────────────────────


async def test_drill_scope_excludes_other_projects():
    """Drilling New Capital must never include the Cassette node's units."""
    client = _make_client(_drill_dataset())
    res = await get_inventory_drill("project", 1, client=client)
    # Cassette phase id 90 must not appear among NC's phase rows.
    assert 90 not in {r["group_id"] for r in res["rows"]}
    assert res["total_units"] == 9   # not 11


# ── 404 / validation / guards ─────────────────────────────────────────────────


async def test_drill_unknown_scope_raises_not_found():
    client = _make_client(_drill_dataset())
    with pytest.raises(InventoryScopeNotFoundError):
        await get_inventory_drill("project", 999, client=client)


async def test_drill_bad_level_raises_value_error():
    client = _make_client(_drill_dataset())
    with pytest.raises(ValueError, match="unknown drill level"):
        await get_inventory_drill("street", 1, client=client)


async def test_drill_unknown_state_raises():
    bad = [_hu(1, "available", _NC, _P1, _Z1, _B1),
           _hu(2, "frozen", _NC, _P1, _Z1, _B1)]
    with pytest.raises(RuntimeError, match="state value"):
        await get_inventory_drill("building", 30, client=_make_client(bad))


# ── shared units cache (Locked decision 2) ────────────────────────────────────


async def test_overview_and_drill_share_one_units_query():
    """The board overview and a drill read the SAME cached unit rows — exactly one
    search_read across both calls."""
    client = _make_client(_drill_dataset())
    ov = await get_inventory_overview(client=client)
    assert ov["cache_status"] == "fresh"
    drill = await get_inventory_drill("project", 1, client=client)
    # Units came from the shared cache populated by the overview → no second RPC.
    assert client.execute_kw.await_count == 1
    assert drill["cache_status"] == "cached"
    assert drill["rpc_duration_ms"] == 0


async def test_drill_cold_cache_reports_fresh():
    client = _make_client(_drill_dataset())
    drill = await get_inventory_drill("project", 1, client=client)
    assert drill["cache_status"] == "fresh"
    assert client.execute_kw.await_count == 1
