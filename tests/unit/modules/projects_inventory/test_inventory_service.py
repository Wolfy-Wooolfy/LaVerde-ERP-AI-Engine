"""
Unit tests for the Projects Inventory service (Slice 1 — Inventory & Availability).

The six-bucket DOCUMENT-DRIVEN model: a unit's board bucket comes from its contracts
first, then its live reservations, and only then from rs.structure.unit.state. Odoo is
fully mocked by a small dispatch fake that honours the search_read DOMAIN, the paging
kwargs and the requested FIELDS, so the tests prove the service filters server-side
(cancelled contracts / terminal reservations never arrive) rather than in Python.

Covers the pure classifier and its precedence, max-rank dedupe over multi-contract
units, every contract and reservation state vocabulary, the silent `unclassified`
degradation vs the loud unknown-contract-state raise, six-bucket emission everywhere,
(contracted + delivered) sold%, the early-stage flag, reconciliation, the reusable
bucketing primitive, leaf/header bucket parity, caching and the RPC-failure guard.

Live verification: scripts/verify_projects_inventory_live.py.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.core.exceptions import InventoryScopeNotFoundError, OdooQueryError
from backend.modules.projects_inventory.domain import (
    BUCKET_ORDER,
    CONTRACT_RANK,
    RESERVATION_LIVE_STATES,
)
from backend.modules.projects_inventory.schemas import (
    ProjectsInventoryDrill,
    ProjectsInventoryOverview,
)
from backend.modules.projects_inventory.services import cache as _cache
from backend.modules.projects_inventory.services.inventory_service import (
    UnknownContractStateError,
    _tally_by,
    classify_unit,
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

_UNIT_MODEL = "rs.structure.unit"
_CONTRACT_MODEL = "rs.contract"
_RESERVATION_MODEL = "rs.reservation"


def _u(uid: int, state: str, project, phase=None) -> dict:
    """One rs.structure.unit search_read row (structural fields only — no PII)."""
    return {
        "id": uid,
        "state": state,
        "project_id": list(project),
        "phase_id": list(phase) if phase else [10, "Phase#1"],
        "zone_id": [20, "Zone#1"],
        "building_id": [30, "Building#1"],
        "code": f"U{uid:03d}",
        "name": str(uid),
    }


def _ct(unit_id, state: str, cid: int = 0) -> dict:
    """One rs.contract row. `unit_id` is the m2o pair Odoo returns; pass unit_id=False
    for an unlinked contract."""
    return {
        "id": cid or 9000 + (unit_id if isinstance(unit_id, int) else 0),
        "unit_id": [unit_id, f"U{unit_id:03d}"] if isinstance(unit_id, int) else False,
        "state": state,
    }


def _rv(unit_id, state: str = "initial", rid: int = 0) -> dict:
    """One rs.reservation row. Pass unit_id=False for an unlinked reservation."""
    return {
        "id": rid or 8000 + (unit_id if isinstance(unit_id, int) else 0),
        "unit_id": [unit_id, f"U{unit_id:03d}"] if isinstance(unit_id, int) else False,
        "state": state,
    }


def _apply_domain(rows: list[dict], domain: list) -> list[dict]:
    """Evaluate the tiny domain grammar the service actually uses, so the fake filters
    exactly where Odoo would."""
    out = list(rows)
    for field, op, value in domain:
        if op == "!=":
            out = [r for r in out if r.get(field) != value]
        elif op == "in":
            out = [r for r in out if r.get(field) in value]
        else:  # pragma: no cover — a new operator must be taught to the fake
            raise AssertionError(f"unsupported domain operator {op!r}")
    return out


def _make_client(units: list[dict], contracts=(), reservations=()):
    """Dispatch fake for the module's THREE read-only fetch groups. Honours the domain,
    the limit/offset paging and the requested field list."""
    store = {
        _UNIT_MODEL: list(units),
        _CONTRACT_MODEL: list(contracts),
        _RESERVATION_MODEL: list(reservations),
    }

    def _dispatch(model, method, args=None, kwargs=None):
        if model not in store or method != "search_read":
            raise AssertionError(f"unexpected RPC: {model}.{method}")
        kwargs = kwargs or {}
        rows = _apply_domain(store[model], (args or [[]])[0])
        offset = kwargs.get("offset", 0)
        limit = kwargs.get("limit")
        rows = rows[offset:offset + limit] if limit else rows[offset:]
        fields = kwargs.get("fields")
        if fields:
            rows = [{k: v for k, v in r.items() if k in fields} for r in rows]
        return rows

    client = MagicMock()
    client.execute_kw = AsyncMock(side_effect=_dispatch)
    client.close = AsyncMock()
    return client


def _bucket(row: dict, key: str) -> dict:
    return next(b for b in row["buckets"] if b["key"] == key)


def _counts(row: dict) -> dict[str, int]:
    return {b["key"]: b["count"] for b in row["buckets"]}


def _bsum(buckets: list[dict]) -> int:
    return sum(b["count"] for b in buckets)


def _calls_for(client, model: str) -> list:
    return [c for c in client.execute_kw.await_args_list if c.args[0] == model]


def _dataset() -> tuple[list[dict], list[dict], list[dict]]:
    """23 units across 3 projects, bucketed by their DOCUMENTS.

    New Capital (10)
      u1  available  | cancelled contract (noise)      -> available
      u2  available  | expired reservation (noise)     -> available
      u3  reserved   | live reservation (draft)        -> reserved
      u4  initial    | no reservation, no contract     -> unclassified  (mirrors live F5)
      u5  contracted | contract confirm                -> contracted
      u6  contracted | contract confirm × 2            -> contracted    (max-rank dedupe)
      u7  available  | contract draft                  -> under_review  (contract > state)
      u8  contracted | contract delivered              -> delivered
      u9  available  | contract finance                -> under_review
      u10 reserved   | contract confirm + live rsv     -> contracted    (contract > rsv)
    Cassette (8)
      u11-u14 available | no documents                 -> available × 4
      u15 available  | live reservation (confirm)      -> reserved
      u16-u18 contracted | contract confirm each       -> contracted × 3
    La puerta (5)
      u19-u23 available | no documents                 -> available × 5

    overall: available 11 | reserved 2 | under_review 2 | contracted 6 | delivered 1
             | unclassified 1  ==  23        sold (contracted + delivered) = 7
    per project: NC 10 (a2 r1 u2 c3 d1 x1) | Cassette 8 (a4 r1 c3) | La puerta 5 (a5)
    """
    units = (
        [_u(1, "available", _NC), _u(2, "available", _NC), _u(3, "reserved", _NC),
         _u(4, "initial", _NC), _u(5, "contracted", _NC), _u(6, "contracted", _NC),
         _u(7, "available", _NC), _u(8, "contracted", _NC), _u(9, "available", _NC),
         _u(10, "reserved", _NC)]
        + [_u(i, "available", _CAS) for i in (11, 12, 13, 14, 15)]
        + [_u(i, "contracted", _CAS) for i in (16, 17, 18)]
        + [_u(i, "available", _LP) for i in (19, 20, 21, 22, 23)]
    )
    contracts = [
        _ct(1, "cancel", cid=1001),          # excluded server-side by the query domain
        _ct(5, "confirm", cid=1005),
        _ct(6, "confirm", cid=1006),         # the live F4 case: two confirm contracts
        _ct(6, "confirm", cid=1007),         #   on ONE unit — must count once
        _ct(7, "draft", cid=1008),
        _ct(8, "delivered", cid=1009),
        _ct(9, "finance", cid=1010),
        _ct(10, "confirm", cid=1011),
        _ct(16, "confirm", cid=1016), _ct(17, "confirm", cid=1017),
        _ct(18, "confirm", cid=1018),
    ]
    reservations = [
        _rv(2, "expire"),                    # terminal — excluded server-side
        _rv(3, "draft"),
        _rv(10, "initial"),                  # loses to u10's confirm contract
        _rv(15, "confirm"),
    ]
    return units, contracts, reservations


# ══════════════════════════════════════════════════════════════════════════════
# The pure classifier — precedence, in isolation
# ══════════════════════════════════════════════════════════════════════════════


def test_classify_unit_contract_rank_beats_reservation_and_state():
    """(a) A contract wins outright — even against a live reservation AND an
    `available` unit state pulling the other way."""
    assert classify_unit("available", 3, True) == "delivered"
    assert classify_unit("available", 2, True) == "contracted"
    assert classify_unit("available", 1, True) == "under_review"
    # ...and against a stale unit state in the opposite direction.
    assert classify_unit("reserved", 2, False) == "contracted"


def test_classify_unit_live_reservation_beats_unit_state():
    """(b) With no contract, a live reservation decides — whatever the unit state."""
    for state in ("available", "initial", "reserved", "contracted", "delivered", "zzz"):
        assert classify_unit(state, None, True) == "reserved", state


def test_classify_unit_available_state_is_the_only_trusted_fallback():
    """(c) `available` is the ONLY unit state trusted without a document."""
    assert classify_unit("available", None, False) == "available"


def test_classify_unit_unknown_state_degrades_to_unclassified():
    """(d) The unit axis degrades SILENTLY: unknown, blank and merely-stale states all
    land in `unclassified` and nothing raises — that bucket IS the alarm."""
    for state in ("initial", "reserved", "contracted", "delivered", "frozen", "", None, False):
        assert classify_unit(state, None, False) == "unclassified", repr(state)


# ══════════════════════════════════════════════════════════════════════════════
# Domain invariants
# ══════════════════════════════════════════════════════════════════════════════


def test_bucket_order_is_the_six_lifecycle_buckets():
    assert list(BUCKET_ORDER) == [
        "available", "reserved", "under_review", "contracted", "delivered", "unclassified"
    ]


def test_terminal_reservation_states_are_not_live():
    """`contract` (converted), `cancel` and `expire` are terminal — a row in any of them
    is not a hold, so it must never be part of the live-reservation vocabulary."""
    for terminal in ("contract", "cancel", "expire"):
        assert terminal not in RESERVATION_LIVE_STATES, terminal
    assert RESERVATION_LIVE_STATES == frozenset({"draft", "initial", "confirm"})


# ══════════════════════════════════════════════════════════════════════════════
# Overall + per-project aggregation
# ══════════════════════════════════════════════════════════════════════════════


async def test_six_buckets_always_present_in_order_and_sum_to_total():
    result = await get_inventory_overview(client=_make_client(*_dataset()))

    assert [b["key"] for b in result["buckets"]] == list(BUCKET_ORDER)
    assert len(result["buckets"]) == 6
    assert _bsum(result["buckets"]) == result["total_units"] == 23
    # Every project card carries all six too, in the same order.
    for p in result["projects"]:
        assert [b["key"] for b in p["buckets"]] == list(BUCKET_ORDER)
        assert _bsum(p["buckets"]) == p["total_units"]


async def test_overall_bucket_counts():
    result = await get_inventory_overview(client=_make_client(*_dataset()))
    assert _counts(result) == {
        "available": 11, "reserved": 2, "under_review": 2,
        "contracted": 6, "delivered": 1, "unclassified": 1,
    }


async def test_per_project_aggregation_sorted_by_total_desc():
    result = await get_inventory_overview(client=_make_client(*_dataset()))
    assert result["project_count"] == 3
    # Sorted by total_units desc: New Capital(10) > Cassette(8) > La puerta(5).
    assert [p["project_id"] for p in result["projects"]] == [1, 2, 3]
    assert [p["total_units"] for p in result["projects"]] == [10, 8, 5]
    assert [p["project_name"] for p in result["projects"]] == [
        "Project#New Capital", "Project#Cassette", "Project#La puerta",
    ]
    by_id = {p["project_id"]: p for p in result["projects"]}
    assert _counts(by_id[1]) == {
        "available": 2, "reserved": 1, "under_review": 2,
        "contracted": 3, "delivered": 1, "unclassified": 1,
    }
    assert _counts(by_id[2]) == {
        "available": 4, "reserved": 1, "under_review": 0,
        "contracted": 3, "delivered": 0, "unclassified": 0,
    }
    # Σ per-project totals == overall total.
    assert sum(p["total_units"] for p in result["projects"]) == result["total_units"]


async def test_sold_pct_is_contracted_plus_delivered():
    """sold% counts BOTH the contracted and the delivered buckets — and the fixture has
    a non-zero delivered bucket so the delivered leg is genuinely exercised."""
    result = await get_inventory_overview(client=_make_client(*_dataset()))
    assert _bucket(result, "delivered")["count"] == 1
    # overall: (contracted 6 + delivered 1) / 23.
    assert result["sold_pct"] == round(100.0 * 7 / 23, 2)   # 30.43
    by_id = {p["project_id"]: p for p in result["projects"]}
    assert by_id[1]["sold_pct"] == 40.0     # (3 + 1) / 10
    assert by_id[2]["sold_pct"] == 37.5     # (3 + 0) / 8
    assert by_id[3]["sold_pct"] == 0.0      # (0 + 0) / 5


async def test_zero_bucket_edge_and_early_stage_flag():
    result = await get_inventory_overview(client=_make_client(*_dataset()))
    by_id = {p["project_id"]: p for p in result["projects"]}

    lp = by_id[3]   # La puerta — every unit available, no documents at all
    assert [b["key"] for b in lp["buckets"]] == list(BUCKET_ORDER)
    assert _counts(lp) == {
        "available": 5, "reserved": 0, "under_review": 0,
        "contracted": 0, "delivered": 0, "unclassified": 0,
    }
    assert _bucket(lp, "under_review")["pct"] == 0.0
    # sold% 0 < 10 → early stage, judged on the NEW (contracted + delivered) sold%.
    assert lp["is_early_stage"] is True
    assert by_id[1]["is_early_stage"] is False   # 40.0%
    assert by_id[2]["is_early_stage"] is False   # 37.5%


# ══════════════════════════════════════════════════════════════════════════════
# Precedence + the contract axis, end-to-end through the service
# ══════════════════════════════════════════════════════════════════════════════


async def test_contract_beats_live_reservation_and_unit_state():
    """u10 is `reserved` on the unit, holds a LIVE reservation, and carries a confirm
    contract — the contract decides. u7 is `available` on the unit but holds a draft
    contract — again the contract decides."""
    units, contracts, reservations = _dataset()
    res = await get_inventory_drill(
        "building", 30, client=_make_client(units, contracts, reservations)
    )
    by_id = {u["unit_id"]: u for u in res["units"]}
    assert by_id[10]["state"] == "reserved" and by_id[10]["bucket"] == "contracted"
    assert by_id[7]["state"] == "available" and by_id[7]["bucket"] == "under_review"


async def test_two_confirm_contracts_count_once_as_contracted():
    """The live F4 case — unit AF208-6-501 carries two `confirm` contracts. Max-rank
    dedupe must count the unit ONCE."""
    units = [_u(1, "contracted", _NC)]
    contracts = [_ct(1, "confirm", cid=1), _ct(1, "confirm", cid=2)]
    result = await get_inventory_overview(client=_make_client(units, contracts))
    assert result["total_units"] == 1
    assert _counts(result)["contracted"] == 1
    assert _bsum(result["buckets"]) == 1


async def test_draft_plus_confirm_contracts_count_once_as_contracted():
    """MAX rank wins: a draft alongside a confirm is contracted, not under review."""
    units = [_u(1, "contracted", _NC)]
    contracts = [_ct(1, "draft", cid=1), _ct(1, "confirm", cid=2)]
    result = await get_inventory_overview(client=_make_client(units, contracts))
    assert _counts(result)["contracted"] == 1
    assert _counts(result)["under_review"] == 0


async def test_delivered_contract_lands_in_delivered_bucket():
    """`delivered` outranks `confirm`, and gets its OWN bucket (it is no longer folded
    into contracted) — even though the live delivered count is 0 today."""
    units = [_u(1, "contracted", _NC)]
    contracts = [_ct(1, "confirm", cid=1), _ct(1, "delivered", cid=2)]
    result = await get_inventory_overview(client=_make_client(units, contracts))
    assert _counts(result)["delivered"] == 1
    assert _counts(result)["contracted"] == 0
    # A delivered unit is sold.
    assert result["sold_pct"] == 100.0


async def test_every_pre_confirm_contract_state_is_under_review():
    """draft / legal / finance / engineering all rank 1 → under_review."""
    for state in ("draft", "legal", "finance", "engineering"):
        _cache.clear()
        assert CONTRACT_RANK[state] == 1, state
        units = [_u(1, "contracted", _NC)]
        result = await get_inventory_overview(
            client=_make_client(units, [_ct(1, state, cid=1)])
        )
        assert _counts(result)["under_review"] == 1, state
        assert _bsum(result["buckets"]) == 1, state


async def test_cancelled_contract_never_buckets_its_unit():
    """A cancelled contract carries no claim: u1 keeps its `available` fallback. The
    exclusion happens SERVER-side — the cancel row never reaches the classifier."""
    result = await get_inventory_overview(client=_make_client(*_dataset()))
    units, contracts, reservations = _dataset()
    drill = await get_inventory_drill(
        "building", 30, client=_make_client(units, contracts, reservations)
    )
    assert {u["unit_id"]: u["bucket"] for u in drill["units"]}[1] == "available"
    # u1 is one of the 11 available units, not an under_review/contracted one.
    assert _counts(result)["available"] == 11


async def test_contract_query_excludes_cancelled_server_side():
    client = _make_client(*_dataset())
    await get_inventory_overview(client=client)
    calls = _calls_for(client, _CONTRACT_MODEL)
    assert len(calls) == 1
    assert calls[0].kwargs["args"] == [[("state", "!=", "cancel")]]
    assert calls[0].kwargs["kwargs"]["fields"] == ["unit_id", "state"]


# ══════════════════════════════════════════════════════════════════════════════
# The reservation axis
# ══════════════════════════════════════════════════════════════════════════════


async def test_every_live_reservation_state_is_reserved():
    """draft / initial / confirm are LIVE holds → reserved, overriding a unit state
    that would otherwise fall through to `unclassified`."""
    for state in sorted(RESERVATION_LIVE_STATES):
        _cache.clear()
        units = [_u(1, "initial", _NC)]
        result = await get_inventory_overview(
            client=_make_client(units, [], [_rv(1, state)])
        )
        assert _counts(result)["reserved"] == 1, state
        assert _counts(result)["unclassified"] == 0, state


async def test_terminal_reservations_never_produce_reserved():
    """A `contract` / `cancel` / `expire` reservation is not a hold. The unit has no
    contract either, so it falls through to (c)/(d) — here (d), `unclassified`."""
    for state in ("contract", "cancel", "expire"):
        _cache.clear()
        units = [_u(1, "reserved", _NC)]
        result = await get_inventory_overview(
            client=_make_client(units, [], [_rv(1, state)])
        )
        assert _counts(result)["reserved"] == 0, state
        assert _counts(result)["unclassified"] == 1, state


async def test_reservation_query_filters_to_live_states_server_side():
    client = _make_client(*_dataset())
    await get_inventory_overview(client=client)
    calls = _calls_for(client, _RESERVATION_MODEL)
    assert len(calls) == 1
    domain = calls[0].kwargs["args"][0]
    assert len(domain) == 1
    field, op, value = domain[0]
    assert (field, op) == ("state", "in")
    assert set(value) == set(RESERVATION_LIVE_STATES)
    assert calls[0].kwargs["kwargs"]["fields"] == ["unit_id"]


async def test_reservation_row_with_empty_unit_id_is_skipped():
    """An unlinked reservation cannot be attributed to a unit — skipped silently, no
    raise, and it steals nothing from the tally."""
    units = [_u(1, "available", _NC), _u(2, "reserved", _NC)]
    reservations = [_rv(False, "draft", rid=7001), _rv(2, "draft")]
    result = await get_inventory_overview(client=_make_client(units, [], reservations))
    assert _counts(result) == {
        "available": 1, "reserved": 1, "under_review": 0,
        "contracted": 0, "delivered": 0, "unclassified": 0,
    }
    assert _bsum(result["buckets"]) == 2


# ══════════════════════════════════════════════════════════════════════════════
# Fallbacks + the two strictness axes
# ══════════════════════════════════════════════════════════════════════════════


async def test_docless_available_unit_is_available():
    units = [_u(1, "available", _NC)]
    result = await get_inventory_overview(client=_make_client(units))
    assert _counts(result)["available"] == 1


async def test_docless_reserved_or_initial_unit_is_unclassified():
    """The live F5 case — BF170-8-702 (`initial`) and BF255-15-203 (`reserved`) hold a
    reserved-ish unit state with NO reservation and NO contract. They are
    `unclassified` by design: the bucket is the data-quality signal."""
    units = [_u(1, "initial", _NC), _u(2, "reserved", _NC)]
    result = await get_inventory_overview(client=_make_client(units))
    assert _counts(result)["unclassified"] == 2
    assert _counts(result)["reserved"] == 0
    assert _bsum(result["buckets"]) == 2


async def test_unknown_unit_state_is_unclassified_and_never_raises():
    """The UNIT axis degrades silently — no raise, and the total still reconciles."""
    units = [_u(1, "available", _NC), _u(2, "frozen", _NC), _u(3, "", _NC)]
    result = await get_inventory_overview(client=_make_client(units))
    assert _counts(result)["unclassified"] == 2
    assert _counts(result)["available"] == 1
    assert _bsum(result["buckets"]) == result["total_units"] == 3


async def test_unknown_contract_state_raises_naming_the_state():
    """The CONTRACT axis is STRICT — an unranked non-cancel state is a loud, named
    error that lists the offending state(s) and their counts."""
    units = [_u(1, "contracted", _NC), _u(2, "contracted", _NC)]
    contracts = [_ct(1, "arbitration", cid=1), _ct(2, "arbitration", cid=2)]
    with pytest.raises(UnknownContractStateError) as exc:
        await get_inventory_overview(client=_make_client(units, contracts))
    message = str(exc.value)
    assert "arbitration" in message
    assert "'arbitration': 2" in message
    # It is RuntimeError-derived so the endpoint layer maps it to a 500.
    assert isinstance(exc.value, RuntimeError)


# ══════════════════════════════════════════════════════════════════════════════
# The reusable bucketing primitive
# ══════════════════════════════════════════════════════════════════════════════


def test_tally_by_supports_arbitrary_group_field():
    """The same primitive groups by ANY denormalised hierarchy field, and tallies the
    classification it is HANDED — never one it recomputes."""
    units = [
        _u(1, "available", _NC, phase=[10, "Phase#1"]),
        _u(2, "contracted", _NC, phase=[10, "Phase#1"]),
        _u(3, "reserved", _NC, phase=[11, "Phase#2"]),
    ]
    buckets = {1: "available", 2: "delivered", 3: "unclassified"}
    groups = _tally_by(units, "phase_id", buckets)
    by_id = {g["group_id"]: g for g in groups}
    assert by_id[10]["total"] == 2
    assert by_id[10]["buckets"]["available"] == 1
    assert by_id[10]["buckets"]["delivered"] == 1
    assert by_id[11]["total"] == 1
    assert by_id[11]["buckets"]["unclassified"] == 1
    # Every group carries all six keys, zeros included.
    for g in groups:
        assert set(g["buckets"]) == set(BUCKET_ORDER)
    # group_field=None collapses to a single all-units group.
    overall = _tally_by(units, None, buckets)
    assert len(overall) == 1 and overall[0]["total"] == 3


# ══════════════════════════════════════════════════════════════════════════════
# Caching + guards
# ══════════════════════════════════════════════════════════════════════════════


async def test_cold_cache_issues_exactly_three_fetch_groups():
    """Units + non-cancel contracts + live reservations. Nothing per unit."""
    client = _make_client(*_dataset())
    await get_inventory_overview(client=client)
    assert client.execute_kw.await_count == 3
    assert [c.args[0] for c in client.execute_kw.await_args_list] == [
        _UNIT_MODEL, _CONTRACT_MODEL, _RESERVATION_MODEL
    ]
    assert {c.args[1] for c in client.execute_kw.await_args_list} == {"search_read"}


async def test_cache_hit_on_second_call():
    client = _make_client(*_dataset())
    first = await get_inventory_overview(client=client)
    assert first["cache_status"] == "fresh"
    second = await get_inventory_overview(client=client)
    assert second["cache_status"] == "cached"
    assert second["rpc_duration_ms"] == 0
    # Still only the ONE cold-cache round of 3 fetches despite two calls.
    assert client.execute_kw.await_count == 3
    # Payload identical apart from the cache-status/timing envelope.
    assert second["total_units"] == first["total_units"]
    assert second["buckets"] == first["buckets"]


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


def _drill_dataset() -> tuple[list[dict], list[dict], list[dict]]:
    """New Capital (9 units) across 2 phases / 3 zones / 4 buildings + a separate
    Cassette node (2 units) used to prove parent-scope isolation. Buckets are DERIVED:

      NC / P1 / Z1 / B1 : u1 available          -> available
                          u2 available          -> available
                          u3 reserved + confirm -> contracted   (state ≠ bucket)
      NC / P1 / Z1 / B2 : u4 reserved + live rsv -> reserved
                          u5 contracted+confirm -> contracted
      NC / P1 / Z2 / B3 : u6 available          -> available
                          u7 contracted+confirm -> contracted
                          u8 contracted+delivrd -> delivered
      NC / P2 / Z3 / B4 : u9 available          -> available
      Cassette / P9 / Z9 / B9 : u10 contracted+confirm, u11 available (excluded from NC)

    NC totals: available 4 | reserved 1 | contracted 3 | delivered 1 -> 9
      P1 = 8 (a3, r1, c3, d1) ; P2 = 1 (a1)
      Z1 = 5 (a2, r1, c2)     ; Z2 = 3 (a1, c1, d1)   (within P1)
      B1 = 3 (a2, c1)         ; B2 = 2 (r1, c1)       (within Z1)
    """
    units = [
        # NC / P1 / Z1 / B1 — codes deliberately out of id order to test code sorting.
        _hu(1, "available", _NC, _P1, _Z1, _B1, code="NC-B1-C"),
        _hu(2, "available", _NC, _P1, _Z1, _B1, code="NC-B1-A"),
        _hu(3, "reserved", _NC, _P1, _Z1, _B1, code="NC-B1-B"),
        # NC / P1 / Z1 / B2
        _hu(4, "reserved", _NC, _P1, _Z1, _B2),
        _hu(5, "contracted", _NC, _P1, _Z1, _B2),
        # NC / P1 / Z2 / B3
        _hu(6, "available", _NC, _P1, _Z2, _B3),
        _hu(7, "contracted", _NC, _P1, _Z2, _B3),
        _hu(8, "contracted", _NC, _P1, _Z2, _B3),
        # NC / P2 / Z3 / B4
        _hu(9, "available", _NC, _P2, _Z3, _B4),
        # Cassette node (must be excluded from any NC scope)
        _hu(10, "contracted", _CAS, _CZP, _CZZ, _CZB),
        _hu(11, "available", _CAS, _CZP, _CZZ, _CZB),
    ]
    contracts = [
        _ct(3, "confirm", cid=2003),      # a `reserved` unit that is really contracted
        _ct(5, "confirm", cid=2005),
        _ct(7, "confirm", cid=2007),
        _ct(8, "delivered", cid=2008),
        _ct(10, "confirm", cid=2010),
    ]
    reservations = [_rv(4, "initial")]
    return units, contracts, reservations


# ── project → phases ──────────────────────────────────────────────────────────


async def test_drill_project_returns_phases_with_scope_reconciliation():
    res = await get_inventory_drill("project", 1, client=_make_client(*_drill_dataset()))

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
    # Σ child totals == scope total ; each row Σ buckets == its total, all six keys.
    assert sum(r["total_units"] for r in res["rows"]) == res["total_units"]
    for r in res["rows"]:
        assert _bsum(r["buckets"]) == r["total_units"]
        assert [b["key"] for b in r["buckets"]] == list(BUCKET_ORDER)
    p1 = next(r for r in res["rows"] if r["group_id"] == 10)
    assert _counts(p1) == {
        "available": 3, "reserved": 1, "under_review": 0,
        "contracted": 3, "delivered": 1, "unclassified": 0,
    }
    assert p1["sold_pct"] == round(100.0 * 4 / 8, 2)   # (3 contracted + 1 delivered) / 8


# ── phase → zones ─────────────────────────────────────────────────────────────


async def test_drill_phase_returns_zones():
    res = await get_inventory_drill("phase", 10, client=_make_client(*_drill_dataset()))

    assert res["child_level"] == "zone"
    assert res["is_leaf"] is False
    assert res["parent_name"] == "Phase#1"
    assert res["total_units"] == 8
    assert {r["group_id"]: r["total_units"] for r in res["rows"]} == {20: 5, 21: 3}
    assert sum(r["total_units"] for r in res["rows"]) == res["total_units"]


# ── zone → buildings ──────────────────────────────────────────────────────────


async def test_drill_zone_returns_buildings():
    res = await get_inventory_drill("zone", 20, client=_make_client(*_drill_dataset()))

    assert res["child_level"] == "building"
    assert res["is_leaf"] is False
    assert res["parent_name"] == "Zone#1"
    assert res["total_units"] == 5
    assert {r["group_id"]: r["total_units"] for r in res["rows"]} == {30: 3, 31: 2}
    assert sum(r["total_units"] for r in res["rows"]) == res["total_units"]


# ── building → unit leaf ──────────────────────────────────────────────────────


async def test_drill_building_returns_unit_leaf_sorted_by_code():
    res = await get_inventory_drill("building", 30, client=_make_client(*_drill_dataset()))

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
    # Each leaf row carries code + name + raw state + derived board bucket.
    for u in units:
        assert set(u) == {"unit_id", "code", "name", "state", "bucket"}
    a = next(u for u in units if u["code"] == "NC-B1-A")
    assert a["state"] == "available" and a["bucket"] == "available"


async def test_drill_leaf_bucket_diverges_from_raw_state_under_contract():
    """u3's raw unit state is `reserved` but it holds a confirm contract — the leaf row
    shows BOTH, and the bucket is the derived one."""
    res = await get_inventory_drill("building", 30, client=_make_client(*_drill_dataset()))
    u3 = next(u for u in res["units"] if u["unit_id"] == 3)
    assert u3["state"] == "reserved"
    assert u3["bucket"] == "contracted"


async def test_drill_leaf_bucket_matches_derived_bucket_for_delivered_contract():
    """A unit whose contract is `delivered` shows raw state `contracted` but bucket
    `delivered` — and the header counts it in the delivered bucket."""
    res = await get_inventory_drill("building", 32, client=_make_client(*_drill_dataset()))
    u8 = next(u for u in res["units"] if u["unit_id"] == 8)
    assert u8["state"] == "contracted"
    assert u8["bucket"] == "delivered"
    assert _counts(res)["delivered"] == 1


async def test_drill_leaf_buckets_reconcile_with_scope_header():
    """A drill panel can never disagree with itself: the per-unit derived buckets tally
    EXACTLY to the header bar the same call emitted."""
    for building_id in (30, 31, 32, 33):
        _cache.clear()
        res = await get_inventory_drill(
            "building", building_id, client=_make_client(*_drill_dataset())
        )
        leaf_tally = {b: 0 for b in BUCKET_ORDER}
        for u in res["units"]:
            leaf_tally[u["bucket"]] += 1
        assert leaf_tally == _counts(res), building_id
        assert sum(leaf_tally.values()) == res["total_units"], building_id


# ── parent-scope isolation ────────────────────────────────────────────────────


async def test_drill_scope_excludes_other_projects():
    """Drilling New Capital must never include the Cassette node's units."""
    res = await get_inventory_drill("project", 1, client=_make_client(*_drill_dataset()))
    # Cassette phase id 90 must not appear among NC's phase rows.
    assert 90 not in {r["group_id"] for r in res["rows"]}
    assert res["total_units"] == 9   # not 11


# ── 404 / validation / guards ─────────────────────────────────────────────────


async def test_drill_unknown_scope_raises_not_found():
    with pytest.raises(InventoryScopeNotFoundError):
        await get_inventory_drill("project", 999, client=_make_client(*_drill_dataset()))


async def test_drill_bad_level_raises_value_error():
    with pytest.raises(ValueError, match="unknown drill level"):
        await get_inventory_drill("street", 1, client=_make_client(*_drill_dataset()))


async def test_drill_unknown_unit_state_is_unclassified():
    """The drill shares the board's silent unit axis — an unknown state is shown as
    `unclassified`, never raised."""
    units = [_hu(1, "available", _NC, _P1, _Z1, _B1),
             _hu(2, "frozen", _NC, _P1, _Z1, _B1)]
    res = await get_inventory_drill("building", 30, client=_make_client(units))
    assert {u["unit_id"]: u["bucket"] for u in res["units"]} == {
        1: "available", 2: "unclassified"
    }
    assert _counts(res)["unclassified"] == 1


# ── shared classified snapshot (single-source invariant) ──────────────────────


async def test_overview_and_drill_share_one_classified_snapshot():
    """The board overview and a drill read the SAME cached units AND the SAME cached
    documents — exactly three fetches across both calls, so they reconcile by
    construction."""
    client = _make_client(*_drill_dataset())
    ov = await get_inventory_overview(client=client)
    assert ov["cache_status"] == "fresh"
    drill = await get_inventory_drill("project", 1, client=client)
    assert client.execute_kw.await_count == 3
    assert drill["cache_status"] == "cached"
    assert drill["rpc_duration_ms"] == 0
    # The NC scope's header equals the NC project card on the board.
    nc_card = next(p for p in ov["projects"] if p["project_id"] == 1)
    assert _counts(drill) == _counts(nc_card)
    assert drill["total_units"] == nc_card["total_units"]
    assert drill["sold_pct"] == nc_card["sold_pct"]


async def test_drill_cold_cache_reports_fresh():
    client = _make_client(*_drill_dataset())
    drill = await get_inventory_drill("project", 1, client=client)
    assert drill["cache_status"] == "fresh"
    assert client.execute_kw.await_count == 3


# ── schema round-trip (the six-key BucketKey Literal) ─────────────────────────


async def test_overview_validates_against_schema():
    result = await get_inventory_overview(client=_make_client(*_dataset()))
    model = ProjectsInventoryOverview.model_validate(result)
    assert [b.key for b in model.buckets] == list(BUCKET_ORDER)
    assert model.total_units == 23


async def test_drill_validates_against_schema():
    res = await get_inventory_drill("building", 32, client=_make_client(*_drill_dataset()))
    model = ProjectsInventoryDrill.model_validate(res)
    assert [b.key for b in model.buckets] == list(BUCKET_ORDER)
    assert {u.bucket for u in model.units} == {"available", "contracted", "delivered"}
