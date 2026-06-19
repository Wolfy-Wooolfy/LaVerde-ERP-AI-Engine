"""
Projects Inventory — Value & Area service (Slice 2, read-only).

Board-facing intelligence for New Capital + Cassette ONLY (La Puerta excluded — its
`amount` pricing is incomplete, 9/138 units). Two prices live in the data and they
diverge (docs/PROJECTS_INVENTORY_PRICING_DISCOVERY.md):

  LIST     = rs.structure.unit.amount        — indicative, May-2026 bulk reload.
  REALIZED = rs.contract.sales_price          — the CONTRACTED deal value per sold unit
             (== installments_total). This is the value the customer committed to over
             the payment plan; it is NOT cash collected. Always label it
             "actual / contracted value", never "revenue".

Algorithm (LOCKED — every number is also independently recomputed live by
scripts/verify_projects_inventory_value_live.py and asserted identity-equal):
  1. units  — reuse inventory_service._get_units_cached (the SINGLE module units query;
     it now carries `amount` + `total_area`). Filter to VALUE_SCOPE_PROJECT_IDS.
  2. contracts — a separate 60s-TTL cached read of rs.contract for the scope's SOLD
     units (fields: unit_id, sales_price, state). Realized value per sold unit = Σ
     sales_price over that unit's NON-cancel contracts (one is the norm; the lone live
     duplicate carries a 0 sibling, so the sum is clean). A sold unit with no contract
     contributes 0 realized and is reported in the coverage counts — never dropped.
  3. Per project (NC, Cassette) AND combined, compute a–i:
       a available_list_value     = Σ amount over AVAILABLE units
       b available_area           = Σ total_area over AVAILABLE units
       c sold_realized_value      = Σ realized over SOLD units (contract join)
       d sold_contracted_area     = Σ total_area over SOLD units
       e sold_list_value          = Σ amount over SOLD units (ALL sold; "if sold at list")
       f gap_abs / gap_pct        = (e − c) and (e − c)/e
       g pct_units_below_list     = below / sold_with_contract  (shared population —
                                     the no-contract units have no realized to compare)
       h avg_price_per_m2_realized= c / d  (guarded)
       i sold_units_count, sold_units_with_contract_count (coverage)
  4. Reconcile: Σ per-project == combined for a/b/c/d/e and the counts; gap_abs == e−c.
     Every reconciliation is an explicit raise (survives python -O), mirroring Slice 1.

READ-ONLY: _assert_read_only() runs at entry; only search_read is issued. No method
ever writes. If a write seems needed, this module raises rather than proceed.
"""

import time
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from loguru import logger

from backend.core.exceptions import OdooQueryError, ReadOnlyViolationError
from backend.modules.projects_inventory.domain import (
    AVAILABLE_STATES,
    CONTRACT_CANCEL_STATE,
    CONTRACT_MODEL,
    CONTRACT_PRICE_FIELD,
    CONTRACT_STATE_FIELD,
    CONTRACT_UNIT_FIELD,
    SOLD_STATES,
    UNIT_AMOUNT_FIELD,
    UNIT_AREA_FIELD,
    VALUE_EXCLUDED_PROJECT_IDS,
    VALUE_SCOPE_PROJECT_IDS,
)
from backend.modules.projects_inventory.services import cache as _cache
from backend.modules.projects_inventory.services.inventory_service import (
    _assert_read_only,
    _classify_states,
    _get_units_cached,
    _m2o,
)
from backend.shared.odoo.client import OdooClient

_CACHE_KEY_PREFIX = "projects_inventory:value:overview"
_CONTRACTS_CACHE_KEY_PREFIX = "projects_inventory:value:contracts"
_CAIRO_TZ = ZoneInfo("Africa/Cairo")
_CONTRACT_CHUNK = 200   # search_read sold-unit contracts in id chunks (matches discovery)

_CONTRACT_FIELDS = [CONTRACT_UNIT_FIELD, CONTRACT_PRICE_FIELD, CONTRACT_STATE_FIELD]


def _num(value) -> float:
    """Coerce an Odoo monetary/float (or False for an empty value) to float."""
    return float(value) if isinstance(value, (int, float)) else 0.0


def _c2(value: float) -> float:
    """Round to cents — the deterministic basis for the realized-vs-list comparison
    (both sides of the verify do the same, so the < test never flips on float noise)."""
    return round(value, 2)


async def _fetch_contracts(client: OdooClient, unit_ids: list[int]) -> list[dict]:
    """search_read rs.contract for the given (sold) unit ids, in id chunks. Read-only."""
    rows: list[dict] = []
    for i in range(0, len(unit_ids), _CONTRACT_CHUNK):
        chunk = unit_ids[i:i + _CONTRACT_CHUNK]
        part = await client.execute_kw(
            CONTRACT_MODEL,
            "search_read",
            args=[[(CONTRACT_UNIT_FIELD, "in", chunk)]],
            kwargs={"fields": _CONTRACT_FIELDS},
        )
        rows.extend(part)
    return rows


async def _get_contracts_cached(client: OdooClient, unit_ids: list[int]) -> list[dict]:
    """Cached (60s TTL, per Cairo date) read of the scope's sold-unit contracts. The
    sold-unit id set is deterministic from the shared units cache, so a fixed key is
    safe. Mirrors _get_units_cached."""
    cache_key = _cache.make_key(_CONTRACTS_CACHE_KEY_PREFIX)
    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"Value contracts cache hit: {cache_key}")
        return cached
    logger.info(f"Value contracts cache miss: {cache_key} — querying Odoo")
    rows = await _fetch_contracts(client, unit_ids)
    _cache.set(cache_key, rows)
    return rows


def _realized_by_unit(contract_rows: list[dict]) -> dict[int, float]:
    """unit_id → Σ sales_price over that unit's NON-cancel contracts.

    A unit appears in the map iff it has ≥1 non-cancel contract (that is the "has a
    realized price" / coverage signal). The live data has exactly one unit with two
    confirm contracts, one of which is priced 0 — so the per-unit sum is the real deal
    value. Cancelled contracts (none live today) are excluded for robustness."""
    realized: dict[int, float] = {}
    for ct in contract_rows:
        if ct.get(CONTRACT_STATE_FIELD) == CONTRACT_CANCEL_STATE:
            continue
        uid = _m2o(ct.get(CONTRACT_UNIT_FIELD))[0]
        if uid is None:
            continue
        realized[uid] = realized.get(uid, 0.0) + _num(ct.get(CONTRACT_PRICE_FIELD))
    return realized


def _compute_scope(units: list[dict], realized: dict[int, float]) -> dict:
    """Compute the a–i value/area metrics over one list of units (a project, or the
    combined scope). `realized` maps unit_id → realized contract value."""
    available = [u for u in units if u["state"] in AVAILABLE_STATES]
    sold = [u for u in units if u["state"] in SOLD_STATES]

    available_list_value = sum(_num(u.get(UNIT_AMOUNT_FIELD)) for u in available)
    available_area = sum(_num(u.get(UNIT_AREA_FIELD)) for u in available)

    sold_list_value = sum(_num(u.get(UNIT_AMOUNT_FIELD)) for u in sold)
    sold_contracted_area = sum(_num(u.get(UNIT_AREA_FIELD)) for u in sold)

    # Realized value + coverage (sold units that actually carry a contract).
    sold_with_contract = [u for u in sold if u["id"] in realized]
    sold_realized_value = sum(realized[u["id"]] for u in sold_with_contract)

    gap_abs = sold_list_value - sold_realized_value
    gap_pct = (gap_abs / sold_list_value * 100.0) if sold_list_value else 0.0
    capture_pct = (sold_realized_value / sold_list_value * 100.0) if sold_list_value else 0.0

    # % below list — over the with-contract population only (realized known). Cents-
    # rounded strict <, so equality-at-list never counts as a discount.
    below = sum(
        1 for u in sold_with_contract
        if _c2(realized[u["id"]]) < _c2(_num(u.get(UNIT_AMOUNT_FIELD)))
    )
    pct_units_below_list = (below / len(sold_with_contract) * 100.0) if sold_with_contract else 0.0

    avg_price_per_m2_realized = (
        sold_realized_value / sold_contracted_area if sold_contracted_area else 0.0
    )
    sold_pct_units = (len(sold) / len(units) * 100.0) if units else 0.0

    return {
        "total_units": len(units),
        "available_units_count": len(available),
        "sold_units_count": len(sold),
        "sold_units_with_contract_count": len(sold_with_contract),
        "sold_units_below_list_count": below,
        "available_list_value": round(available_list_value, 2),
        "available_area": round(available_area, 2),
        "sold_realized_value": round(sold_realized_value, 2),
        "sold_contracted_area": round(sold_contracted_area, 2),
        "sold_list_value": round(sold_list_value, 2),
        "gap_abs": round(gap_abs, 2),
        "gap_pct": round(gap_pct, 2),
        "capture_pct": round(capture_pct, 2),
        "pct_units_below_list": round(pct_units_below_list, 2),
        "avg_price_per_m2_realized": round(avg_price_per_m2_realized, 2),
        "sold_pct_units": round(sold_pct_units, 2),
    }


_SUM_KEYS = (
    "total_units", "available_units_count", "sold_units_count",
    "sold_units_with_contract_count", "sold_units_below_list_count",
    "available_list_value", "available_area", "sold_realized_value",
    "sold_contracted_area", "sold_list_value",
)


def _reconcile(combined: dict, projects: list[dict]) -> None:
    """Σ per-project == combined for every additive metric, and gap_abs == e − c on
    every scope. Explicit raises (survive python -O), mirroring the Slice 1 style."""
    for key in _SUM_KEYS:
        per_sum = round(sum(p[key] for p in projects), 2)
        comb = round(combined[key], 2)
        if per_sum != comb:
            raise RuntimeError(
                f"Value reconciliation FAILED for {key!r}: Σ per-project {per_sum} "
                f"!= combined {comb}."
            )
    for scope in (combined, *projects):
        expect = round(scope["sold_list_value"] - scope["sold_realized_value"], 2)
        if round(scope["gap_abs"], 2) != expect:
            raise RuntimeError(
                f"Value reconciliation FAILED: gap_abs {scope['gap_abs']} != "
                f"(sold_list_value − sold_realized_value) {expect}."
            )
        if scope["sold_units_with_contract_count"] > scope["sold_units_count"]:
            raise RuntimeError(
                "Value reconciliation FAILED: sold_units_with_contract_count "
                f"{scope['sold_units_with_contract_count']} > sold_units_count "
                f"{scope['sold_units_count']}."
            )


async def get_value_area_overview(client: Optional[OdooClient] = None) -> dict:
    """Return the Value & Area overview (combined + per project) for New Capital +
    Cassette. La Puerta is excluded entirely.

    Args:
        client: optional injected OdooClient (tests pass a mock; production opens and
            closes its own).

    Returns a dict matching schemas.ValueAreaOverview.

    Raises:
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated.
        OdooQueryError: if the Odoo RPC fails.
        RuntimeError: on an unmapped unit state, a leaked excluded project, or any
            reconciliation failure (explicit raises so they survive python -O).
    """
    _assert_read_only()

    cairo_today = datetime.now(_CAIRO_TZ).date()
    cache_key = _cache.make_key(_CACHE_KEY_PREFIX)
    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"Value cache hit: {cache_key}")
        return {**cached, "cache_status": "cached", "rpc_duration_ms": 0}
    logger.info(f"Value cache miss: {cache_key} — querying Odoo")

    _client = client if client is not None else OdooClient()

    t0 = time.monotonic()
    try:
        all_units = await _get_units_cached(_client)

        # Scope to NC + Cassette; La Puerta (and anything else) is dropped here.
        scope_units = [
            u for u in all_units if _m2o(u.get("project_id"))[0] in VALUE_SCOPE_PROJECT_IDS
        ]
        # Defence-in-depth: the excluded project must never leak into a value figure.
        leaked = [u["id"] for u in scope_units
                  if _m2o(u.get("project_id"))[0] in VALUE_EXCLUDED_PROJECT_IDS]
        if leaked:
            raise RuntimeError(
                f"Excluded project leaked into the value scope (unit ids {leaked[:10]}). "
                "Refusing to publish a value figure that includes La Puerta."
            )

        # Every scoped unit's state must map to a known bucket (never silently dropped).
        _classify_states(scope_units)

        sold_ids = sorted(u["id"] for u in scope_units if u["state"] in SOLD_STATES)
        contract_rows = await _get_contracts_cached(_client, sold_ids)
    except (ReadOnlyViolationError, RuntimeError):
        raise
    except Exception as exc:
        raise OdooQueryError(f"get_value_area_overview() RPC failed: {exc}") from exc
    finally:
        if client is None:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)

    realized = _realized_by_unit(contract_rows)

    # Per-project scopes (sorted by sold_list_value desc — the board's "biggest book"
    # ordering), then the combined scope over all scoped units.
    by_project: dict[int, list[dict]] = {pid: [] for pid in VALUE_SCOPE_PROJECT_IDS}
    project_name: dict[int, str] = {}
    for u in scope_units:
        pid, pname = _m2o(u.get("project_id"))
        by_project.setdefault(pid, []).append(u)
        project_name.setdefault(pid, (pname or "—").strip() or "—")

    projects: list[dict] = []
    for pid, units in by_project.items():
        if not units:
            continue
        metrics = _compute_scope(units, realized)
        projects.append({"project_id": pid, "project_name": project_name.get(pid, "—"), **metrics})
    projects.sort(key=lambda p: (-p["sold_list_value"], p["project_name"]))

    combined = _compute_scope(scope_units, realized)
    _reconcile(combined, projects)

    logger.info(
        f"Value & Area: {combined['total_units']:,} units (NC+Cassette) | "
        f"avail list {combined['available_list_value']:,.0f} | sold realized "
        f"{combined['sold_realized_value']:,.0f} | sold@list {combined['sold_list_value']:,.0f} "
        f"| gap {combined['gap_pct']:.2f}% | sold {combined['sold_units_count']:,} "
        f"({combined['sold_units_with_contract_count']:,} w/contract) | RPC {rpc_ms}ms"
    )

    result: dict = {
        **combined,
        "projects": projects,
        "project_count": len(projects),
        "reference_date": cairo_today.isoformat(),
        "as_of": datetime.now(timezone.utc).isoformat(),
        "cache_status": "fresh",
        "rpc_duration_ms": rpc_ms,
    }

    _cache.set(cache_key, result)
    return result
