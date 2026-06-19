"""
Projects Inventory — Inventory Data Quality service (read-only, ADMIN-only page).

An ongoing data-entry review tool (analogous to the CRM "Missing Contacts"): it
surfaces data-completeness defects across ALL three projects so they can be tracked
and fixed in Odoo. It NEVER writes — only reads (the shared units cache + one contracts
read + small parent-record reads). See docs/INVENTORY_DATA_QUALITY_DISCOVERY.md.

Three checks (numbers re-derived live by scripts/verify_inventory_data_quality_live.py
and asserted identity-equal; nothing is hardcoded here):

  A — Sold unit WITHOUT a contract  (defect_type "no_contract")
      sold = state ∈ SOLD_STATES; flagged = NO rs.contract with state != 'cancel'
      references it via unit_id. detail carries the unit's list `amount`.

  B — Broken hierarchy chain  (defect_type "phase_project" / "zone_phase" / "building_zone")
      Each unit denormalises its full chain (project_id, phase_id, zone_id, building_id).
      Reading each PARENT record's OWN upward m2o as the source of truth
      (rs.structure.phase.project_id, rs.structure.zone.phase_id,
      rs.structure.building.zone_id) we verify, per unit:
        phase_id→project == project_id ; zone_id→phase == phase_id ;
        building_id→zone == zone_id.
      A unit with ≥1 broken link is flagged ONCE; the first break in canonical chain
      order (phase→project, zone→phase, building→zone) names its defect_type. (Authoritative
      logic ported from the now-deleted scripts/audit_inventory_hierarchy.py.)

  C — Sold unit with NO list price  (defect_type "no_list_price")
      sold unit whose `amount` is 0 / falsy. A standing guard — 0 today.

Scope: all 3 projects. La Puerta's 129 unpriced AVAILABLE units are EXPECTED (early-stage),
NEVER a defect — Check C only looks at SOLD units, so they are never flagged.

READ-ONLY: _assert_read_only() runs at entry; only search_read is ever issued. Every
unmapped unit state is raised on (never silently dropped), mirroring Slice 1/2.
"""

import time
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from loguru import logger

from backend.core.exceptions import OdooQueryError, ReadOnlyViolationError
from backend.modules.projects_inventory.domain import (
    CONTRACT_CANCEL_STATE,
    CONTRACT_MODEL,
    CONTRACT_STATE_FIELD,
    CONTRACT_UNIT_FIELD,
    SOLD_STATES,
    UNIT_AMOUNT_FIELD,
)
from backend.modules.projects_inventory.services import cache as _cache
from backend.modules.projects_inventory.services.inventory_service import (
    _assert_read_only,
    _classify_states,
    _get_units_cached,
    _m2o,
)
from backend.shared.odoo.client import OdooClient

_CACHE_KEY_PREFIX = "projects_inventory:dq:overview"
_CONTRACTS_CACHE_KEY_PREFIX = "projects_inventory:dq:contracts"
_PARENTS_CACHE_KEY_PREFIX = "projects_inventory:dq:parents"   # per parent model
_CAIRO_TZ = ZoneInfo("Africa/Cairo")
_CONTRACT_CHUNK = 200   # search_read sold-unit contracts in id chunks (matches Slice 2)

# Stable per-check keys (drive the template's localized section names + CSV file names).
CHECK_NO_CONTRACT = "no_contract"
CHECK_BROKEN_HIERARCHY = "broken_hierarchy"
CHECK_NO_LIST_PRICE = "no_list_price"

# Authoritative chain links, in CANONICAL order (the first break a unit has names its
# defect). Each tuple: (defect_type, unit child m2o, child kind word, parent MODEL,
# the parent record's OWN upward m2o, parent kind word, the unit field the parent's
# upward m2o must equal). Parent fields confirmed via fields_get 2026-06-19.
_CHAIN_LINKS: tuple[tuple[str, str, str, str, str, str, str], ...] = (
    ("phase_project", "phase_id", "phase", "rs.structure.phase", "project_id", "project", "project_id"),
    ("zone_phase", "zone_id", "zone", "rs.structure.zone", "phase_id", "phase", "phase_id"),
    ("building_zone", "building_id", "building", "rs.structure.building", "zone_id", "zone", "zone_id"),
)

# The valid defect_type vocabulary the schema/template rely on.
DEFECT_TYPES: frozenset[str] = frozenset(
    {"no_contract", "no_list_price", *(link[0] for link in _CHAIN_LINKS)}
)


def _num(value) -> float:
    """Coerce an Odoo monetary/float (or False for an empty value) to float."""
    return float(value) if isinstance(value, (int, float)) else 0.0


def _project_name(unit: dict) -> str:
    """The unit's project display name (from its denormalised project_id m2o)."""
    return _m2o(unit.get("project_id"))[1] or "—"


def _item(unit: dict, defect_type: str, detail: str) -> dict:
    """One structured flagged-unit row (matches schemas.DataQualityItem)."""
    if defect_type not in DEFECT_TYPES:
        raise RuntimeError(
            f"Refusing to emit a data-quality item with an unknown defect_type "
            f"{defect_type!r} (allowed: {sorted(DEFECT_TYPES)})."
        )
    return {
        "unit_id": unit["id"],
        "code": unit.get("code") or "",
        "project_name": _project_name(unit),
        "defect_type": defect_type,
        "detail": detail,
    }


def _sort_items(items: list[dict]) -> list[dict]:
    """Each check's items are sorted by project then code (stable, language-neutral)."""
    return sorted(items, key=lambda it: (it["project_name"], it["code"]))


async def _fetch_contracts(client: OdooClient, unit_ids: list[int]) -> list[dict]:
    """search_read rs.contract (unit_id, state) for the given (sold) unit ids, in id
    chunks. Read-only — mirrors value_service's chunked contract fetch."""
    rows: list[dict] = []
    for i in range(0, len(unit_ids), _CONTRACT_CHUNK):
        chunk = unit_ids[i:i + _CONTRACT_CHUNK]
        part = await client.execute_kw(
            CONTRACT_MODEL,
            "search_read",
            args=[[(CONTRACT_UNIT_FIELD, "in", chunk)]],
            kwargs={"fields": [CONTRACT_UNIT_FIELD, CONTRACT_STATE_FIELD]},
        )
        rows.extend(part)
    return rows


async def _get_contracts_cached(client: OdooClient, unit_ids: list[int]) -> list[dict]:
    """Cached (60s TTL, per Cairo date) read of the SOLD-unit contracts. The sold-unit id
    set is deterministic from the shared units cache, so a fixed key is safe."""
    cache_key = _cache.make_key(_CONTRACTS_CACHE_KEY_PREFIX)
    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"DQ contracts cache hit: {cache_key}")
        return cached
    logger.info(f"DQ contracts cache miss: {cache_key} — querying Odoo")
    rows = await _fetch_contracts(client, unit_ids)
    _cache.set(cache_key, rows)
    return rows


async def _fetch_parent_map(client: OdooClient, model: str, parent_field: str) -> dict:
    """search_read every record of `model` → {id: parent_id} from its OWN upward m2o
    `parent_field` (the source of truth). Read-only, one query per parent model."""
    rows = await client.execute_kw(
        model,
        "search_read",
        args=[[]],
        kwargs={"fields": ["id", parent_field], "order": "id"},
    )
    return {int(r["id"]): _m2o(r.get(parent_field))[0] for r in rows}


async def _get_parent_map_cached(client: OdooClient, model: str, parent_field: str) -> dict:
    """Cached (60s TTL) parent map for one chain link, keyed by model name."""
    cache_key = _cache.make_key(f"{_PARENTS_CACHE_KEY_PREFIX}:{model}")
    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"DQ parents cache hit: {cache_key}")
        return cached
    logger.info(f"DQ parents cache miss: {cache_key} — querying Odoo")
    pmap = await _fetch_parent_map(client, model, parent_field)
    _cache.set(cache_key, pmap)
    return pmap


def _check_no_contract(sold: list[dict], covered: set[int]) -> list[dict]:
    """Check A — sold units with no non-cancel contract. detail carries the list amount."""
    items: list[dict] = []
    for u in sold:
        if u["id"] in covered:
            continue
        amount = _num(u.get(UNIT_AMOUNT_FIELD))
        items.append(_item(u, "no_contract", f"amount {amount:,.0f}"))
    return _sort_items(items)


def _check_broken_hierarchy(units: list[dict], parent_maps: dict[str, dict]) -> list[dict]:
    """Check B — broken hierarchy chain (AUTHORITATIVE: the parent record is the source of
    truth). Each unit is flagged ONCE; the FIRST break in canonical chain order names its
    defect_type + detail. Ported from scripts/audit_inventory_hierarchy.py (Part 1)."""
    items: list[dict] = []
    for u in units:
        for defect_type, child_field, child_kind, _model, _pfield, parent_kind, unit_field in _CHAIN_LINKS:
            cid, cname = _m2o(u.get(child_field))
            claimed_id = _m2o(u.get(unit_field))[0]
            if cid is None:
                items.append(_item(u, defect_type, f"unit has no {child_field}"))
                break
            pmap = parent_maps[defect_type]
            if cid not in pmap:
                items.append(_item(
                    u, defect_type,
                    f"{child_kind} {cid} {cname!r} → {parent_kind} (missing parent record); "
                    f"unit {unit_field}={claimed_id}",
                ))
                break
            actual_parent = pmap[cid]
            if actual_parent != claimed_id:
                items.append(_item(
                    u, defect_type,
                    f"{child_kind} {cid} {cname!r} → {parent_kind} {actual_parent}; "
                    f"unit {unit_field}={claimed_id}",
                ))
                break   # first break wins — one item per flagged unit
    return _sort_items(items)


def _check_no_list_price(sold: list[dict]) -> list[dict]:
    """Check C — sold units whose list `amount` is 0 / falsy. detail is empty (the unit
    code + project say it all). La Puerta's unpriced AVAILABLE units are NOT sold, so they
    can never appear here."""
    items = [_item(u, "no_list_price", "") for u in sold if not _num(u.get(UNIT_AMOUNT_FIELD))]
    return _sort_items(items)


def _check(key: str, items: list[dict]) -> dict:
    """Materialise one DataQualityCheck dict."""
    return {"key": key, "count": len(items), "items": items}


async def get_data_quality_overview(client: Optional[OdooClient] = None) -> dict:
    """Return the Inventory Data Quality overview — Checks A, B and C across all projects.

    Args:
        client: optional injected OdooClient (tests pass a mock; production opens and
            closes its own).

    Returns a dict matching schemas.DataQualityOverview.

    Raises:
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated.
        OdooQueryError: if the Odoo RPC fails.
        RuntimeError: on an unmapped unit state, an unknown defect_type, or a total
            reconciliation failure (explicit raises so they survive python -O).
    """
    _assert_read_only()

    cairo_today = datetime.now(_CAIRO_TZ).date()
    cache_key = _cache.make_key(_CACHE_KEY_PREFIX)
    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"DQ cache hit: {cache_key}")
        return {**cached, "cache_status": "cached", "rpc_duration_ms": 0}
    logger.info(f"DQ cache miss: {cache_key} — querying Odoo")

    _client = client if client is not None else OdooClient()

    t0 = time.monotonic()
    try:
        units = await _get_units_cached(_client)

        # Every unit's state must map to a known bucket before we classify anything sold.
        _classify_states(units)

        sold = [u for u in units if u["state"] in SOLD_STATES]
        sold_ids = sorted(u["id"] for u in sold)

        # Check A inputs — non-cancel contracts covering each sold unit.
        contract_rows = await _get_contracts_cached(_client, sold_ids)

        # Check B inputs — one parent map per chain link (each parent record's own upward m2o).
        parent_maps: dict[str, dict] = {}
        for defect_type, _cf, _ck, model, parent_field, _pk, _uf in _CHAIN_LINKS:
            parent_maps[defect_type] = await _get_parent_map_cached(_client, model, parent_field)
    except (ReadOnlyViolationError, RuntimeError):
        raise
    except Exception as exc:
        raise OdooQueryError(f"get_data_quality_overview() RPC failed: {exc}") from exc
    finally:
        if client is None:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)

    # Check A — covered = sold units referenced by ≥1 non-cancel contract.
    covered: set[int] = set()
    for ct in contract_rows:
        if ct.get(CONTRACT_STATE_FIELD) == CONTRACT_CANCEL_STATE:
            continue
        uid = _m2o(ct.get(CONTRACT_UNIT_FIELD))[0]
        if uid is not None:
            covered.add(uid)

    checks = [
        _check(CHECK_NO_CONTRACT, _check_no_contract(sold, covered)),
        _check(CHECK_BROKEN_HIERARCHY, _check_broken_hierarchy(units, parent_maps)),
        _check(CHECK_NO_LIST_PRICE, _check_no_list_price(sold)),
    ]

    total_issues = sum(c["count"] for c in checks)
    # Reconciliation: total == Σ per-check counts == Σ items emitted (explicit raise).
    items_emitted = sum(len(c["items"]) for c in checks)
    if total_issues != items_emitted:
        raise RuntimeError(
            f"Data-quality reconciliation FAILED: total_issues {total_issues} != "
            f"Σ items emitted {items_emitted}."
        )

    logger.info(
        f"Inventory data quality: {total_issues} issue(s) across {len(units):,} units | "
        f"A(no_contract)={checks[0]['count']} B(broken_hierarchy)={checks[1]['count']} "
        f"C(no_list_price)={checks[2]['count']} | RPC {rpc_ms}ms | cache_key={cache_key}"
    )

    result: dict = {
        "checks": checks,
        "total_issues": total_issues,
        "reference_date": cairo_today.isoformat(),
        "as_of": datetime.now(timezone.utc).isoformat(),
        "cache_status": "fresh",
        "rpc_duration_ms": rpc_ms,
    }

    _cache.set(cache_key, result)
    return result
