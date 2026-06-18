"""
Projects Inventory service — unit INVENTORY by sales STATUS (read-only).

Slice 1: board-level counts only — overall + per project. Data source:
rs.structure.unit via the shared read-only OdooClient. No method ever calls
create / write / unlink. _assert_read_only() runs at entry.

Algorithm (LOCKED — docs/PROJECTS_INVENTORY_DISCOVERY.md §1/§2):
  1. ONE paged search_read of every unit's [id, state, project_id, phase_id,
     zone_id, building_id] (no read_group — regroup in Python, consistent with the
     campaign windowing's single-query path). All 4 hierarchy links are fetched
     now so per-phase/per-zone/per-building grouping is a one-line change later.
  2. Fold each unit's `state` into a board BUCKET via STATE_TO_BUCKET
     (available / reserved{+initial} / contracted{+delivered}). An unmapped or
     empty state is a material data change → explicit raise (never miscounted).
  3. _tally_by(units, group_field) is the single reusable bucketing primitive,
     keyed by ANY denormalised hierarchy field — called with None for the overall
     totals and with "project_id" for the per-project breakdown. Slice 2 reuses it
     verbatim with "phase_id" / "zone_id".
  4. sold% = contracted ÷ total (overall and per project). A project below the
     early-stage threshold is flagged for a subtle UI badge (display only).
  5. Reconcile: Σ(bucket counts) == total, and Σ(per-project totals) == overall
     total — explicit raises (survive python -O).
"""

import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from loguru import logger

from backend.core.exceptions import OdooQueryError, ReadOnlyViolationError
from backend.modules.projects_inventory import domain
from backend.modules.projects_inventory.domain import (
    BUCKET_ORDER,
    EARLY_STAGE_SOLD_PCT_THRESHOLD,
    SOLD_BUCKET,
    STATE_TO_BUCKET,
    UNIT_MODEL,
)
from backend.modules.projects_inventory.services import cache as _cache
from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient

# Methods that must never appear in ALLOWED_METHODS.
_FORBIDDEN_WRITE_METHODS = frozenset({"create", "write", "unlink"})

_UNIT_FIELDS = ["id", "state", "project_id", "phase_id", "zone_id", "building_id"]

_CACHE_KEY_PREFIX = "projects_inventory:overview"
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
    """Materialise the BUCKET_ORDER list of {key, count, pct} rows for a total."""
    return [
        {
            "key": b,
            "count": counts[b],
            "pct": round(100.0 * counts[b] / total, 2) if total else 0.0,
        }
        for b in BUCKET_ORDER
    ]


def _sold_pct(counts: dict[str, int], total: int) -> float:
    return round(100.0 * counts[SOLD_BUCKET] / total, 2) if total else 0.0


async def _fetch_all_units(client: OdooClient) -> list[dict]:
    """search_read every unit in pages of _PAGE, ordered by id — the SAME paged
    pattern the campaign windowing uses for its single lead fetch. ~1,873 units fit
    one page today; paging keeps it correct if inventory grows past the threshold."""
    rows, offset = [], 0
    while True:
        page = await client.execute_kw(
            UNIT_MODEL,
            "search_read",
            args=[[]],
            kwargs={"fields": _UNIT_FIELDS, "order": "id", "limit": _PAGE, "offset": offset},
        )
        rows.extend(page)
        if len(page) < _PAGE:
            break
        offset += _PAGE
    return rows


def _classify_states(units: list[dict]) -> None:
    """Validate that every unit's state maps to a known bucket. The 5 live states are
    LOCKED (§2); an unmapped/empty value is raised on so it can never be silently
    miscounted (explicit raise — survives python -O)."""
    unknown: dict[str, int] = defaultdict(int)
    for u in units:
        if u.get("state") not in STATE_TO_BUCKET:
            unknown[str(u.get("state"))] += 1
    if unknown:
        raise RuntimeError(
            f"rs.structure.unit carries state value(s) outside the locked bucket map "
            f"{sorted(STATE_TO_BUCKET)}: {dict(unknown)}. Refusing to return an "
            f"inventory breakdown that would silently drop these units."
        )


def _tally_by(units: list[dict], group_field: Optional[str]) -> list[dict]:
    """THE reusable bucketing primitive. Tally units into status buckets, optionally
    grouped by a denormalised hierarchy m2o on the unit.

    Args:
        units: rows from _fetch_all_units (each carries `state` + the hierarchy m2os).
        group_field: None for a single all-units group, or one of domain.GROUP_FIELDS
            ("project_id" / "phase_id" / "zone_id" / "building_id"). Slice 1 uses
            None (overall) and "project_id"; the rest are ready for Slice 2.

    Returns a list of group dicts, each:
        {"group_id": int|None, "group_name": str|None,
         "total": int, "buckets": {bucket: count, ...}}
    sorted by total desc, then group_name asc. For group_field=None the single entry
    has group_id/group_name = None.
    """
    if group_field is None:
        counts = _empty_buckets()
        for u in units:
            counts[STATE_TO_BUCKET[u["state"]]] += 1
        return [{"group_id": None, "group_name": None, "total": len(units), "buckets": counts}]

    groups: dict[int, dict] = {}
    for u in units:
        gid, gname = _m2o(u.get(group_field))
        entry = groups.setdefault(
            gid, {"group_id": gid, "group_name": gname, "total": 0, "buckets": _empty_buckets()}
        )
        entry["total"] += 1
        entry["buckets"][STATE_TO_BUCKET[u["state"]]] += 1
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
        RuntimeError: if a state is unmapped, or the bucket/per-project counts fail to
            reconcile to the total (explicit raises so they survive python -O).
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
        units = await _fetch_all_units(_client)
    except ReadOnlyViolationError:
        raise
    except Exception as exc:
        raise OdooQueryError(f"get_inventory_overview() RPC failed: {exc}") from exc
    finally:
        if client is None:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)

    # Every state must map to a bucket before we count anything.
    _classify_states(units)

    # Overall totals (single group) + per-project breakdown (same primitive).
    overall = _tally_by(units, None)[0]
    total_units = overall["total"]
    overall_counts = overall["buckets"]

    project_groups = _tally_by(units, "project_id")
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

    logger.info(
        f"Projects inventory: {total_units:,} units across {len(projects)} projects | "
        f"available={overall_counts['available']:,} reserved={overall_counts['reserved']:,} "
        f"contracted={overall_counts['contracted']:,} | sold={_sold_pct(overall_counts, total_units):.1f}% "
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
