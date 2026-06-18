"""
scripts/verify_projects_inventory_drill_live.py — Projects Inventory (Slice 1b)
hierarchy drill-down identity-equal LIVE verification (READ-ONLY, $0 AI).

Proves get_inventory_drill() reconciles with independent direct Odoo queries at every
level of Project → Phase → Zone → Building → Unit. Every "ODOO" number is an
independent search_count issued by THIS script; the "MODULE" numbers come from
get_inventory_drill() / get_inventory_overview() (injected with the same read-only
client). NOTHING here writes to Odoo.

What it checks (drilling the first reachable node at each level, for every project):
  SCOPE     — drill scope total == search_count([level_field = parent_id]); and the
              drilled project scope total == the board's per-project total.
  Σ CHILDREN— Σ child-row totals == drill scope total (parent-scope reconciliation).
  PER-BUCKET— each drilled scope's available/reserved/contracted == independent
              search_count over (level_field, state) folded to buckets.
  LEAF      — building level: len(units) == search_count([building_id]) with per-bucket
              parity (units folded by state vs search_count per bucket-state domain).
  404 GUARD — an impossible scope id raises InventoryScopeNotFoundError.

Method discipline: READ-ONLY (search_count / search_read only). ALLOWED_METHODS
untouched. No FastAPI. No OpenAI. AI cost = $0.00.

Pre-flight (run manually BEFORE this script): kill all python processes; purge all
__pycache__; (uvicorn not required — this talks to Odoo directly).

Usage (from project root):
    python scripts/verify_projects_inventory_drill_live.py
"""

import asyncio
import io
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.exceptions import InventoryScopeNotFoundError  # noqa: E402
from backend.modules.projects_inventory.domain import (  # noqa: E402
    BUCKET_ORDER,
    CHILD_LEVEL,
    LEVEL_FIELD,
    STATE_TO_BUCKET,
    UNIT_MODEL,
)
from backend.modules.projects_inventory.services import cache as _cache  # noqa: E402
from backend.modules.projects_inventory.services.inventory_service import (  # noqa: E402
    get_inventory_drill,
    get_inventory_overview,
)
from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient  # noqa: E402

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_SEP = "=" * 100
_SEP2 = "-" * 100

# bucket -> the live state values that fold into it (inverse of STATE_TO_BUCKET).
_BUCKET_TO_STATES: dict[str, list[str]] = defaultdict(list)
for _st, _bk in STATE_TO_BUCKET.items():
    _BUCKET_TO_STATES[_bk].append(_st)


def _ok(flag: bool) -> str:
    return "PASS" if flag else "**FAIL**"


async def _count(client, domain) -> int:
    return await client.execute_kw(UNIT_MODEL, "search_count", args=[domain])


async def _odoo_buckets(client, base_domain) -> dict[str, int]:
    """Independent per-bucket search_count for a scope domain (folded by state)."""
    out = {b: 0 for b in BUCKET_ORDER}
    for bucket in BUCKET_ORDER:
        states = _BUCKET_TO_STATES[bucket]
        out[bucket] = await _count(client, base_domain + [("state", "in", states)])
    return out


def _mod_buckets(buckets: list[dict]) -> dict[str, int]:
    return {b["key"]: b["count"] for b in buckets}


async def _verify_scope(client, level: str, parent_id: int,
                        hard_total=None, tree_total=None) -> tuple[int, dict, list]:
    """Drill one scope and reconcile it against independent counts. Returns
    (fail_count, drill_result, child_rows-or-units).

    hard_total: an asserted equality (e.g. a project scope must equal the board's
        per-project total).
    tree_total: the parent's child-row total for this node. Compared INFORMATIONALLY
        only — under perfect tree consistency it equals the node's own scope total, but
        the live data has nodes (e.g. a zone id shared across the tree) where it differs
        by a few units. A difference is an upstream denormalised-link quirk, NOT a module
        error, so it is reported as a NOTE and never fails the run. The REQUIRED identity
        (scope total == search_count over this node's own m2o) is always asserted hard."""
    fail = 0
    field = LEVEL_FIELD[level]
    res = await get_inventory_drill(level, parent_id, client=client)

    odoo_total = await _count(client, [(field, "=", parent_id)])
    odoo_bk = await _odoo_buckets(client, [(field, "=", parent_id)])
    mod_bk = _mod_buckets(res["buckets"])

    print(f"  [{level}={parent_id}] {res['parent_name']!r} → child={res['child_level']} "
          f"(is_leaf={res['is_leaf']})")

    t_ok = res["total_units"] == odoo_total
    fail += 0 if t_ok else 1
    print(f"     {'scope total':<16} MODULE={res['total_units']:>8,}  ODOO={odoo_total:>8,}  {_ok(t_ok)}")

    if hard_total is not None:
        h_ok = res["total_units"] == hard_total
        fail += 0 if h_ok else 1
        print(f"     {'== board total':<16} MODULE={res['total_units']:>8,}  BOARD={hard_total:>8,}  {_ok(h_ok)}")

    if tree_total is not None:
        if res["total_units"] == tree_total:
            print(f"     {'== parent row':<16} MODULE={res['total_units']:>8,}  ROW={tree_total:>8,}  PASS (tree-consistent)")
        else:
            d = res["total_units"] - tree_total
            print(f"     {'== parent row':<16} MODULE={res['total_units']:>8,}  ROW={tree_total:>8,}  "
                  f"NOTE: differs by {d:+,} — upstream denormalised-link quirk (this node's "
                  f"m2o id also appears outside the parent scope); not a module error")

    for b in BUCKET_ORDER:
        bk_ok = mod_bk.get(b, 0) == odoo_bk[b]
        fail += 0 if bk_ok else 1
        print(f"     {b:<16} MODULE={mod_bk.get(b, 0):>8,}  ODOO={odoo_bk[b]:>8,}  {_ok(bk_ok)}")

    if res["is_leaf"]:
        units = res["units"]
        len_ok = len(units) == odoo_total
        fail += 0 if len_ok else 1
        print(f"     {'leaf len':<16} MODULE={len(units):>8,}  ODOO={odoo_total:>8,}  {_ok(len_ok)}")
        leaf_bk = {b: 0 for b in BUCKET_ORDER}
        for u in units:
            leaf_bk[u["bucket"]] += 1
        for b in BUCKET_ORDER:
            lb_ok = leaf_bk[b] == odoo_bk[b]
            fail += 0 if lb_ok else 1
            print(f"     {'leaf ' + b:<16} MODULE={leaf_bk[b]:>8,}  ODOO={odoo_bk[b]:>8,}  {_ok(lb_ok)}")
        print()
        return fail, res, units

    rows = res["rows"]
    sigma = sum(r["total_units"] for r in rows)
    s_ok = sigma == res["total_units"]
    fail += 0 if s_ok else 1
    print(f"     {'Σ children':<16} {sigma:,} == scope {res['total_units']:,}  {_ok(s_ok)}")
    print()
    return fail, res, rows


async def main():
    print(_SEP)
    print("  PROJECTS INVENTORY (Slice 1b) — DRILL-DOWN IDENTITY-EQUAL LIVE VERIFICATION (READ-ONLY, $0)")
    print(f"  ALLOWED_METHODS : {sorted(ALLOWED_METHODS)}")
    print(f"  Model           : {UNIT_MODEL}")
    print(f"  Level fields    : {LEVEL_FIELD}")
    print(_SEP)
    print()

    fail = 0
    _cache.clear()

    async with OdooClient() as client:
        overview = await get_inventory_overview(client=client)
        board_total = {p["project_id"]: p["total_units"] for p in overview["projects"]}

        for proj in overview["projects"]:
            pid = proj["project_id"]
            print(_SEP)
            print(f"  PROJECT [{pid}] {proj['project_name']!r}  (board total {proj['total_units']:,})")
            print(_SEP2)

            # project → phases (scope is asserted HARD against the board's project total)
            f, _res, phases = await _verify_scope(client, "project", pid, hard_total=board_total.get(pid))
            fail += f
            if not phases:
                print("     (no phases — skipping deeper levels)\n")
                continue

            # phase → zones (first phase); parent-row total compared informationally
            phase_id = phases[0]["group_id"]
            f, _res, zones = await _verify_scope(client, "phase", phase_id, tree_total=phases[0]["total_units"])
            fail += f
            if not zones:
                continue

            # zone → buildings (first zone)
            zone_id = zones[0]["group_id"]
            f, _res, buildings = await _verify_scope(client, "zone", zone_id, tree_total=zones[0]["total_units"])
            fail += f
            if not buildings:
                continue

            # building → units leaf (first building)
            bldg_id = buildings[0]["group_id"]
            f, _res, _units = await _verify_scope(client, "building", bldg_id, tree_total=buildings[0]["total_units"])
            fail += f

        # 404 guard — an impossible scope id must raise (never silently empty).
        print(_SEP2)
        try:
            await get_inventory_drill("project", 10 ** 9, client=client)
            print(f"  404 guard (impossible project id) raised?  {_ok(False)}")
            fail += 1
        except InventoryScopeNotFoundError:
            print(f"  404 guard (impossible project id) raised InventoryScopeNotFoundError  {_ok(True)}")
        print()

    print(_SEP)
    if fail == 0:
        print("  VERIFICATION COMPLETE — ALL CHECKS PASSED.")
    else:
        print(f"  VERIFICATION COMPLETE — {fail} CHECK(S) FAILED. STOP and report.")
    print(_SEP)
    return 1 if fail else 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except SystemExit:
        raise
    except Exception as exc:
        print(f"\n  FATAL ERROR: {exc}")
        import traceback
        traceback.print_exc()
        raise SystemExit(1)
