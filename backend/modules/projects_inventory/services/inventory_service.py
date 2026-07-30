"""
Projects Inventory service — unit INVENTORY by sales STATUS (read-only).

Board-level counts only — overall + per project + drilled down the hierarchy. Data
sources: rs.structure.unit, rs.contract and rs.reservation via the shared read-only
OdooClient. No method ever calls create / write / unlink. _assert_read_only() runs at
entry.

DESIGN PRINCIPLE (six-bucket DOCUMENT-DRIVEN model, live recon 2026-07-30): a unit's
board bucket is derived from its DOCUMENTS — its contracts first, then its live
reservations — with rs.structure.unit.state used only as a last-resort fallback. The
unit state is NOT trustworthy on its own: the live data holds units sitting in
`reserved`/`initial` with no reservation and no contract behind them.

The two axes have deliberately DIFFERENT strictness:
  • CONTRACT axis — STRICT. A non-cancel contract state outside domain.CONTRACT_RANK
    is a material vocabulary change and raises UnknownContractStateError.
  • UNIT axis — SILENT. An unknown/blank unit state degrades into `unclassified`;
    that bucket IS the alarm, so it is surfaced rather than raised.

Algorithm:
  1. THREE batched Odoo fetch groups per cold cache, and nothing per unit or per drill:
     (a) every unit's [id, state, hierarchy m2os, code, name, pricing fields] — one
         paged search_read (the SINGLE module-wide unit set, shared with Slices 2/2.5
         and Data Quality);
     (b) every NON-cancel rs.contract's [unit_id, state] → unit_id → MAX contract rank
         (a unit with several contracts counts ONCE, at its highest rank);
     (c) every LIVE rs.reservation's [unit_id] → the set of units on a live hold.
         Terminal reservations (contract / cancel / expire) are excluded by the query
         domain, so a converted reservation never double-counts against its contract.
  2. classify_unit(unit_state, max_contract_rank, has_live_reservation) folds each unit
     into ONE of the six buckets by strict precedence — contract, then live
     reservation, then `available`, else `unclassified`. It is a pure function.
  3. _tally_by(units, group_field, buckets) is the single reusable bucketing
     primitive, keyed by ANY denormalised hierarchy field — called with None for the
     overall totals and with "project_id" / "phase_id" / "zone_id" / "building_id"
     for every breakdown.
  4. sold% = (contracted + delivered) ÷ total (overall and per project). A project
     below the early-stage threshold is flagged for a subtle UI badge (display only).
  5. Reconcile: Σ(bucket counts) == total, and Σ(per-project totals) == overall
     total — explicit raises (survive python -O).

SINGLE-SOURCE INVARIANT: the board overview and every drill level read the SAME
cached units + the SAME cached documents, so a drill panel can never disagree with
the header that opened it. Both caches carry the module's 60s TTL and Cairo-date key,
so ?refresh=1 bypasses them together.
"""

import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from loguru import logger

from backend.core.exceptions import (
    InventoryScopeNotFoundError,
    OdooQueryError,
    ReadOnlyViolationError,
)
from backend.modules.projects_inventory import domain
from backend.modules.projects_inventory.domain import (
    BUCKET_AVAILABLE,
    BUCKET_ORDER,
    BUCKET_RESERVED,
    BUCKET_UNCLASSIFIED,
    CHILD_FIELD,
    CHILD_LEVEL,
    CONTRACT_CANCEL_STATE,
    CONTRACT_MODEL,
    CONTRACT_RANK,
    CONTRACT_STATE_FIELD,
    CONTRACT_UNIT_FIELD,
    DRILL_LEVELS,
    EARLY_STAGE_SOLD_PCT_THRESHOLD,
    LEAF_LEVEL,
    LEVEL_FIELD,
    LOCKED_UNIT_STATES,
    RANK_TO_BUCKET,
    RESERVATION_LIVE_STATES,
    RESERVATION_MODEL,
    RESERVATION_STATE_FIELD,
    RESERVATION_UNIT_FIELD,
    SOLD_BUCKETS,
    UNIT_MODEL,
    UNIT_STATE_AVAILABLE,
)
from backend.modules.projects_inventory.services import cache as _cache
from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient

# Methods that must never appear in ALLOWED_METHODS.
_FORBIDDEN_WRITE_METHODS = frozenset({"create", "write", "unlink"})


class UnknownContractStateError(RuntimeError):
    """A non-cancel rs.contract carries a state outside domain.CONTRACT_RANK.

    The contract axis is STRICT: an unranked state would silently mis-bucket real
    units, so the service refuses to return a breakdown at all. RuntimeError-derived
    so the endpoint layer maps it to a 500 like every other reconciliation raise."""


# `code` + `name` carry the human-readable leaf identifier (code is 100% populated and
# unique, e.g. "AF190-1-101"; name is the short label, e.g. "101"). Both are fetched in
# the SAME single search_read so the cached unit set is self-sufficient for the leaf
# list — the drill never issues a per-node query. The overview ignores them.
# `amount` (LIST price) + `total_area` (m²) are fetched here too so the SINGLE cached
# unit set also feeds Slice 2 (value_service) — both slices share one units query. The
# Slice-1 board/drill ignore these two fields; they only matter to value_service.
# `unit_type_id` rides along for Slice 2.5 (pricing_outliers_service) as one leg of its
# peer key — the board/drill/value slices ignore it; it only matters to that service.
# `meter_price` rides along the same way for Inventory Data Quality Check D (the per-m²
# list price the data team edits) — every other slice ignores it.
_UNIT_FIELDS = ["id", "state", "project_id", "phase_id", "zone_id", "building_id",
                "code", "name", domain.UNIT_AMOUNT_FIELD, domain.UNIT_AREA_FIELD,
                domain.UNIT_TYPE_FIELD, domain.UNIT_METER_PRICE_FIELD]

# The two DOCUMENT fetches are deliberately minimal — only what the classifier needs.
_CONTRACT_DOC_FIELDS = [CONTRACT_UNIT_FIELD, CONTRACT_STATE_FIELD]
_RESERVATION_DOC_FIELDS = [RESERVATION_UNIT_FIELD]

_CACHE_KEY_PREFIX = "projects_inventory:overview"
# The RAW unit rows are cached under their OWN key, shared by the board overview AND
# every drill level, so both read identical rows (exact reconciliation by construction).
_UNITS_CACHE_KEY_PREFIX = "projects_inventory:units"
# The classifying DOCUMENTS (non-cancel contracts + live reservations) share one key,
# fetched and expired together so a bucket can never be derived from a half-stale pair.
_DOCS_CACHE_KEY_PREFIX = "projects_inventory:unit_docs"
_CAIRO_TZ = ZoneInfo("Africa/Cairo")
_PAGE = 5000


def _assert_read_only() -> None:
    """Defense-in-depth: abort if any write method has leaked into ALLOWED_METHODS."""
    violations = ALLOWED_METHODS & _FORBIDDEN_WRITE_METHODS
    if violations:
        raise ReadOnlyViolationError(
            f"ALLOWED_METHODS contains forbidden write method(s): {sorted(violations)}. "
            "The Odoo client is no longer strictly read-only. Halting before any RPC."
        )


def _m2o(value) -> tuple[Optional[int], Optional[str]]:
    """Render an Odoo many2one [id, name] (or False) as (id, name) or (None, None)."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return int(value[0]), str(value[1])
    return None, None


def _empty_buckets() -> dict[str, int]:
    return {b: 0 for b in BUCKET_ORDER}


def _bucket_rows(counts: dict[str, int], total: int) -> list[dict]:
    """Materialise the BUCKET_ORDER list of {key, count, pct} rows for a total —
    always all six buckets, the empty ones at 0."""
    return [
        {
            "key": b,
            "count": counts[b],
            "pct": round(100.0 * counts[b] / total, 2) if total else 0.0,
        }
        for b in BUCKET_ORDER
    ]


def _sold_pct(counts: dict[str, int], total: int) -> float:
    """sold% = (contracted + delivered) ÷ total — a unit is sold once its contract is
    confirmed, and stays sold after hand-over."""
    sold = sum(counts[b] for b in SOLD_BUCKETS)
    return round(100.0 * sold / total, 2) if total else 0.0


# ── the classifier ────────────────────────────────────────────────────────────


def classify_unit(
    unit_state, max_contract_rank: Optional[int], has_live_reservation: bool
) -> str:
    """Fold ONE unit into ONE of the six board buckets. Pure — no I/O, no globals.

    Precedence (documents first, unit state last):
        (a) any non-cancel contract  → RANK_TO_BUCKET[max rank]
                                       (delivered > confirm > any pre-confirm stage)
        (b) else a LIVE reservation  → reserved
        (c) else state == available  → available
        (d) else                     → unclassified

    Step (d) is the silent-degradation rule: an unknown, blank or merely stale unit
    state (e.g. `reserved` with no reservation behind it) lands in `unclassified`
    rather than raising, because that bucket is the data-quality alarm the board is
    meant to see.
    """
    if max_contract_rank is not None:
        return RANK_TO_BUCKET[max_contract_rank]
    if has_live_reservation:
        return BUCKET_RESERVED
    if unit_state == UNIT_STATE_AVAILABLE:
        return BUCKET_AVAILABLE
    return BUCKET_UNCLASSIFIED


def _contract_ranks(contract_rows: list[dict]) -> dict[int, int]:
    """unit_id → MAX domain.CONTRACT_RANK over that unit's NON-cancel contracts.

    A unit with several contracts counts ONCE, at its highest rank (live proof: unit
    AF208-6-501 carries two `confirm` contracts; a draft + confirm pair is contracted,
    not under review). Rows with no unit link cannot be attributed to a unit and are
    skipped. Every unranked state seen is collected first, then raised together so the
    error names ALL offenders with their counts.
    """
    ranks: dict[int, int] = {}
    unknown: dict[str, int] = defaultdict(int)
    for ct in contract_rows:
        state = ct.get(CONTRACT_STATE_FIELD)
        rank = CONTRACT_RANK.get(state)
        if rank is None:
            unknown[str(state)] += 1
            continue
        uid = _m2o(ct.get(CONTRACT_UNIT_FIELD))[0]
        if uid is None:
            continue
        if rank > ranks.get(uid, 0):
            ranks[uid] = rank
    if unknown:
        raise UnknownContractStateError(
            f"{CONTRACT_MODEL} carries non-cancel state value(s) outside the locked "
            f"rank map {sorted(CONTRACT_RANK)}: {dict(unknown)}. Refusing to return an "
            f"inventory breakdown that would silently mis-bucket these units."
        )
    return ranks


def _live_reservation_units(reservation_rows: list[dict]) -> set[int]:
    """The set of unit ids on a LIVE reservation hold. The query domain has already
    excluded the terminal states, so every row here is live. A row with no unit link
    cannot be attributed to a unit and is skipped WITHOUT error — unlike the contract
    axis, the reservation axis only ever adds a hold, so a dangling row loses nothing
    but a `reserved` badge (the unit falls through to (c)/(d))."""
    units: set[int] = set()
    for rv in reservation_rows:
        uid = _m2o(rv.get(RESERVATION_UNIT_FIELD))[0]
        if uid is None:
            continue
        units.add(uid)
    return units


def _classify_all(units: list[dict], docs: dict) -> dict[int, str]:
    """unit_id → board bucket for every unit, from the shared documents snapshot."""
    ranks = _contract_ranks(docs["contracts"])
    reserved = _live_reservation_units(docs["reservations"])
    return {
        u["id"]: classify_unit(u.get("state"), ranks.get(u["id"]), u["id"] in reserved)
        for u in units
    }


# ── Odoo fetches (three batched groups, nothing per unit) ─────────────────────


async def _paged_search_read(
    client: OdooClient, model: str, domain_: list, fields: list[str]
) -> list[dict]:
    """search_read every matching row in pages of _PAGE, ordered by id — the SAME paged
    pattern the campaign windowing uses for its single lead fetch. Every live result set
    here fits one page today; paging keeps it correct if the data grows past it."""
    rows, offset = [], 0
    while True:
        page = await client.execute_kw(
            model,
            "search_read",
            args=[domain_],
            kwargs={"fields": fields, "order": "id", "limit": _PAGE, "offset": offset},
        )
        rows.extend(page)
        if len(page) < _PAGE:
            break
        offset += _PAGE
    return rows


async def _fetch_all_units(client: OdooClient) -> list[dict]:
    """Fetch group 1 — every rs.structure.unit row (~1,873 today)."""
    return await _paged_search_read(client, UNIT_MODEL, [], _UNIT_FIELDS)


async def _get_units_cached(client: OdooClient) -> list[dict]:
    """Return every unit's raw rows, cached under a per-Cairo-date key (60s TTL).

    This is the SINGLE source of unit rows for the whole module: get_inventory_overview
    (the board) and get_inventory_drill (every hierarchy level) both call it, so they
    operate on identical rows and reconcile exactly by construction. A cache hit makes a
    drill a pure in-memory filter — zero Odoo round-trips."""
    cache_key = _cache.make_key(_UNITS_CACHE_KEY_PREFIX)
    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"Units cache hit: {cache_key}")
        return cached
    logger.info(f"Units cache miss: {cache_key} — querying Odoo")
    rows = await _fetch_all_units(client)
    _cache.set(cache_key, rows)
    return rows


async def _fetch_unit_docs(client: OdooClient) -> dict:
    """Fetch groups 2 and 3 — the documents that classify a unit.

    Both are whole-population batched reads filtered SERVER-side, so the classifier
    never issues a per-unit query:
      • contracts   — every rs.contract whose state != cancel (a cancelled contract
                      carries no claim on its unit and must not bucket it);
      • reservations — every rs.reservation in a LIVE state. Terminal rows (contract /
                      cancel / expire) are excluded here, which is exactly what makes a
                      converted reservation defer to its contract.
    """
    contracts = await _paged_search_read(
        client,
        CONTRACT_MODEL,
        [(CONTRACT_STATE_FIELD, "!=", CONTRACT_CANCEL_STATE)],
        _CONTRACT_DOC_FIELDS,
    )
    reservations = await _paged_search_read(
        client,
        RESERVATION_MODEL,
        [(RESERVATION_STATE_FIELD, "in", sorted(RESERVATION_LIVE_STATES))],
        _RESERVATION_DOC_FIELDS,
    )
    return {"contracts": contracts, "reservations": reservations}


async def _get_unit_docs_cached(client: OdooClient) -> dict:
    """Cached (60s TTL, per Cairo date) classifying documents. Contracts and
    reservations share ONE key so they expire together — a bucket is never derived
    from a half-stale document pair. Mirrors _get_units_cached."""
    cache_key = _cache.make_key(_DOCS_CACHE_KEY_PREFIX)
    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"Unit docs cache hit: {cache_key}")
        return cached
    logger.info(f"Unit docs cache miss: {cache_key} — querying Odoo")
    docs = await _fetch_unit_docs(client)
    _cache.set(cache_key, docs)
    return docs


def _classify_states(units: list[dict]) -> None:
    """Validate that every unit's rs.structure.unit.state is in the LOCKED vocabulary.

    NOT used by the board or the drill any more — they classify from DOCUMENTS and let
    an unknown state fall into `unclassified`. This guard survives for the
    unit-STATE-based slices (value / pricing-outliers / data-quality), whose whole
    population definition is a state literal (domain.SOLD_STATES / AVAILABLE_STATES):
    those slices must refuse to run against a vocabulary they have never seen rather
    than silently drop units. Explicit raise — survives python -O."""
    unknown: dict[str, int] = defaultdict(int)
    for u in units:
        if u.get("state") not in LOCKED_UNIT_STATES:
            unknown[str(u.get("state"))] += 1
    if unknown:
        raise RuntimeError(
            f"rs.structure.unit carries state value(s) outside the locked vocabulary "
            f"{sorted(LOCKED_UNIT_STATES)}: {dict(unknown)}. Refusing to return a "
            f"unit-state-based breakdown that would silently drop these units."
        )


def _tally_by(
    units: list[dict], group_field: Optional[str], buckets: dict[int, str]
) -> list[dict]:
    """THE reusable bucketing primitive. Tally units into the six board buckets,
    optionally grouped by a denormalised hierarchy m2o on the unit.

    Args:
        units: rows from _fetch_all_units (each carries the hierarchy m2os).
        group_field: None for a single all-units group, or one of domain.GROUP_FIELDS
            ("project_id" / "phase_id" / "zone_id" / "building_id").
        buckets: the classified snapshot from _classify_all — unit_id → bucket. Passed
            in (never recomputed here) so every caller tallies the SAME classification.

    Returns a list of group dicts, each:
        {"group_id": int|None, "group_name": str|None,
         "total": int, "buckets": {bucket: count, ...}}
    sorted by total desc, then group_name asc. For group_field=None the single entry
    has group_id/group_name = None.
    """
    if group_field is None:
        counts = _empty_buckets()
        for u in units:
            counts[buckets[u["id"]]] += 1
        return [{"group_id": None, "group_name": None, "total": len(units), "buckets": counts}]

    groups: dict[int, dict] = {}
    for u in units:
        gid, gname = _m2o(u.get(group_field))
        entry = groups.setdefault(
            gid, {"group_id": gid, "group_name": gname, "total": 0, "buckets": _empty_buckets()}
        )
        entry["total"] += 1
        entry["buckets"][buckets[u["id"]]] += 1
    return sorted(groups.values(), key=lambda g: (-g["total"], g["group_name"] or ""))


async def get_inventory_overview(client: Optional[OdooClient] = None) -> dict:
    """Return the unit inventory-by-status overview (overall + per project).

    Args:
        client: optional injected OdooClient (tests pass a mock; production opens and
            closes its own).

    Returns a dict matching schemas.ProjectsInventoryOverview.

    Raises:
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated.
        OdooQueryError: if the Odoo RPC fails.
        UnknownContractStateError: if a non-cancel contract state is unranked.
        RuntimeError: if the bucket/per-project counts fail to reconcile to the total
            (explicit raises so they survive python -O).
    """
    _assert_read_only()

    cairo_today = datetime.now(_CAIRO_TZ).date()
    cache_key = _cache.make_key(_CACHE_KEY_PREFIX)
    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"Cache hit: {cache_key}")
        return {**cached, "cache_status": "cached", "rpc_duration_ms": 0}
    logger.info(f"Cache miss: {cache_key} — querying Odoo")

    _client = client if client is not None else OdooClient()

    t0 = time.monotonic()
    try:
        units = await _get_units_cached(_client)
        docs = await _get_unit_docs_cached(_client)
    except ReadOnlyViolationError:
        raise
    except Exception as exc:
        raise OdooQueryError(f"get_inventory_overview() RPC failed: {exc}") from exc
    finally:
        if client is None:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)

    # Classify OUTSIDE the try: an unranked contract state is a data verdict, not an
    # RPC failure, and must surface as itself rather than as OdooQueryError.
    unit_buckets = _classify_all(units, docs)

    # Overall totals (single group) + per-project breakdown (same primitive).
    overall = _tally_by(units, None, unit_buckets)[0]
    total_units = overall["total"]
    overall_counts = overall["buckets"]

    project_groups = _tally_by(units, "project_id", unit_buckets)
    projects: list[dict] = []
    project_total_check = 0
    for g in project_groups:
        gid, gname, gtotal, gcounts = g["group_id"], g["group_name"], g["total"], g["buckets"]
        # Per-project reconciliation (Σ buckets == project total).
        if sum(gcounts.values()) != gtotal:
            raise RuntimeError(
                f"Inventory reconciliation FAILED for project {gname!r} (id={gid}): "
                f"bucket sum {sum(gcounts.values())} != total {gtotal}."
            )
        sold_pct = _sold_pct(gcounts, gtotal)
        projects.append(
            {
                "project_id": gid if gid is not None else 0,
                "project_name": gname or "—",
                "total_units": gtotal,
                "buckets": _bucket_rows(gcounts, gtotal),
                "sold_pct": sold_pct,
                "is_early_stage": sold_pct < EARLY_STAGE_SOLD_PCT_THRESHOLD,
            }
        )
        project_total_check += gtotal

    # Overall reconciliation: Σ buckets == total, and Σ project totals == total.
    if sum(overall_counts.values()) != total_units:
        raise RuntimeError(
            f"Inventory reconciliation FAILED (overall): bucket sum "
            f"{sum(overall_counts.values())} != total {total_units}."
        )
    if project_total_check != total_units:
        raise RuntimeError(
            f"Inventory reconciliation FAILED: Σ per-project totals "
            f"{project_total_check} != overall total {total_units}."
        )

    breakdown = " ".join(f"{b}={overall_counts[b]:,}" for b in BUCKET_ORDER)
    logger.info(
        f"Projects inventory: {total_units:,} units across {len(projects)} projects | "
        f"{breakdown} | sold={_sold_pct(overall_counts, total_units):.1f}% "
        f"| RPC in {rpc_ms}ms | cache_key={cache_key}"
    )

    result: dict = {
        "total_units": total_units,
        "buckets": _bucket_rows(overall_counts, total_units),
        "sold_pct": _sold_pct(overall_counts, total_units),
        "projects": projects,
        "project_count": len(projects),
        "reference_date": cairo_today.isoformat(),
        "as_of": datetime.now(timezone.utc).isoformat(),
        "cache_status": "fresh",
        "rpc_duration_ms": rpc_ms,
    }

    _cache.set(cache_key, result)
    return result


def _leaf_row(u: dict, bucket: str) -> dict:
    """One unit leaf row: code (primary, 100% unique) + name (short label) + the raw
    rs.structure.unit.state AND the DERIVED board bucket. The bucket is the one the
    header above it counted — a drill panel can never disagree with itself — and it is
    kept alongside the raw state precisely because the two legitimately differ (a
    `reserved` unit with a confirmed contract reads contracted)."""
    return {
        "unit_id": u["id"],
        "code": u.get("code") or "",
        "name": u.get("name") or "",
        "state": u["state"],
        "bucket": bucket,
    }


async def get_inventory_drill(
    level: str, parent_id: int, client: Optional[OdooClient] = None
) -> dict:
    """Return one drill scope of the Project → Phase → Zone → Building → Unit hierarchy.

    `level` names the level drilled INTO (the parent); `parent_id` is its id. The units
    AND the classifying documents are loaded once from the SHARED caches and filtered in
    Python to that scope — no per-drill Odoo query. Group levels (project/phase/zone)
    return the child breakdown via the same _tally_by primitive the board uses; the
    building level returns the unit leaf list. Counts only (no pricing/area).

    Args:
        level: one of domain.DRILL_LEVELS ("project"/"phase"/"zone"/"building").
        parent_id: the id at that level.
        client: optional injected OdooClient (tests pass a mock; production opens/closes).

    Returns a dict matching schemas.ProjectsInventoryDrill.

    Raises:
        ValueError: if `level` is not a known drill level (endpoint maps to 422).
        InventoryScopeNotFoundError: if no units match (level, parent_id) (→ 404).
        ReadOnlyViolationError / OdooQueryError / UnknownContractStateError: as for the
            overview.
        RuntimeError: on a reconciliation failure (explicit raise — survives python -O).
    """
    _assert_read_only()
    if level not in DRILL_LEVELS:
        raise ValueError(
            f"unknown drill level {level!r}; expected one of {list(DRILL_LEVELS)}."
        )

    cairo_today = datetime.now(_CAIRO_TZ).date()
    _client = client if client is not None else OdooClient()

    # cache_status/timing reflect the shared snapshot: a drill is RPC-free only when
    # BOTH the units and the classifying documents are already cached.
    units_cache_key = _cache.make_key(_UNITS_CACHE_KEY_PREFIX)
    docs_cache_key = _cache.make_key(_DOCS_CACHE_KEY_PREFIX)
    was_cached = (
        _cache.get(units_cache_key) is not None and _cache.get(docs_cache_key) is not None
    )
    t0 = time.monotonic()
    try:
        units = await _get_units_cached(_client)
        docs = await _get_unit_docs_cached(_client)
    except ReadOnlyViolationError:
        raise
    except Exception as exc:
        raise OdooQueryError(f"get_inventory_drill() RPC failed: {exc}") from exc
    finally:
        if client is None:
            await _client.close()
    rpc_ms = 0 if was_cached else int((time.monotonic() - t0) * 1000)

    # The SAME classified snapshot the board counts (same units, same documents).
    unit_buckets = _classify_all(units, docs)

    # Filter to the parent scope on the denormalised m2o id, then derive the parent name
    # from a matched row's m2o pair (no extra lookup — every reachable node has ≥1 unit).
    level_field = LEVEL_FIELD[level]
    scope = [u for u in units if _m2o(u.get(level_field))[0] == parent_id]
    if not scope:
        raise InventoryScopeNotFoundError(
            f"No units found for {level}={parent_id}. The node may not exist or is stale."
        )
    parent_name = _m2o(scope[0].get(level_field))[1] or "—"

    # Scope header breakdown (the panel's own status bar), via the shared primitive.
    scope_overall = _tally_by(scope, None, unit_buckets)[0]
    scope_total = scope_overall["total"]
    scope_counts = scope_overall["buckets"]
    if sum(scope_counts.values()) != scope_total:
        raise RuntimeError(
            f"Drill reconciliation FAILED (scope {level}={parent_id}): bucket sum "
            f"{sum(scope_counts.values())} != scope total {scope_total}."
        )

    child_level = CHILD_LEVEL[level]
    is_leaf = level == LEAF_LEVEL

    result: dict = {
        "parent_level": level,
        "parent_id": parent_id,
        "parent_name": parent_name,
        "child_level": child_level,
        "is_leaf": is_leaf,
        "total_units": scope_total,
        "buckets": _bucket_rows(scope_counts, scope_total),
        "sold_pct": _sold_pct(scope_counts, scope_total),
        "rows": [],
        "row_count": 0,
        "units": [],
        "unit_count": 0,
        "reference_date": cairo_today.isoformat(),
        "as_of": datetime.now(timezone.utc).isoformat(),
        "cache_status": "cached" if was_cached else "fresh",
        "rpc_duration_ms": rpc_ms,
    }

    if is_leaf:
        leaf = sorted(
            (_leaf_row(u, unit_buckets[u["id"]]) for u in scope), key=lambda r: r["code"]
        )
        # Leaf reconciliation: every scoped unit is listed exactly once.
        if len(leaf) != scope_total:
            raise RuntimeError(
                f"Drill reconciliation FAILED (leaf {level}={parent_id}): "
                f"len(units) {len(leaf)} != scope total {scope_total}."
            )
        result["units"] = leaf
        result["unit_count"] = len(leaf)
        logger.info(
            f"Inventory drill: {level}={parent_id} ({parent_name!r}) → {len(leaf)} units "
            f"| cache={'hit' if was_cached else 'miss'} rpc={rpc_ms}ms"
        )
        return result

    # Group level: tally children with the same primitive, keyed by the child m2o.
    child_field = CHILD_FIELD[level]
    rows: list[dict] = []
    child_total_check = 0
    for g in _tally_by(scope, child_field, unit_buckets):
        gid, gname, gtotal, gcounts = g["group_id"], g["group_name"], g["total"], g["buckets"]
        if sum(gcounts.values()) != gtotal:
            raise RuntimeError(
                f"Drill reconciliation FAILED for {child_level} {gname!r} (id={gid}): "
                f"bucket sum {sum(gcounts.values())} != total {gtotal}."
            )
        rows.append(
            {
                "group_id": gid if gid is not None else 0,
                "group_name": gname or "—",
                "total_units": gtotal,
                "buckets": _bucket_rows(gcounts, gtotal),
                "sold_pct": _sold_pct(gcounts, gtotal),
            }
        )
        child_total_check += gtotal

    # Σ child totals == scope total (the parent-scope reconciliation).
    if child_total_check != scope_total:
        raise RuntimeError(
            f"Drill reconciliation FAILED (scope {level}={parent_id}): Σ {child_level} "
            f"totals {child_total_check} != scope total {scope_total}."
        )

    result["rows"] = rows
    result["row_count"] = len(rows)
    logger.info(
        f"Inventory drill: {level}={parent_id} ({parent_name!r}) → {len(rows)} "
        f"{child_level}(s), {scope_total:,} units | cache={'hit' if was_cached else 'miss'} "
        f"rpc={rpc_ms}ms"
    )
    return result
