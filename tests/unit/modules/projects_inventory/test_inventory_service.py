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

from backend.core.exceptions import OdooQueryError
from backend.modules.projects_inventory.domain import BUCKET_ORDER
from backend.modules.projects_inventory.services import cache as _cache
from backend.modules.projects_inventory.services.inventory_service import (
    _tally_by,
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
