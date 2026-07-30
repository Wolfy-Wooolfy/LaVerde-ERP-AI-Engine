"""
scripts/verify_projects_inventory_drill_live.py — Projects Inventory (Slice 1b,
six-bucket DOCUMENT-DRIVEN model) hierarchy drill-down identity-equal LIVE
verification (READ-ONLY, $0 AI).

Proves get_inventory_drill() reconciles with an INDEPENDENT recomputation at every level
of Project -> Phase -> Zone -> Building -> Unit. Every "SCRIPT" number is computed by
THIS file from its own raw Odoo reads; the "MODULE" numbers come from
get_inventory_drill() / get_inventory_overview() (injected with the same read-only
client). NOTHING here writes to Odoo.

INDEPENDENCE RULE
    Imports CONSTANTS from domain.py only — model/field names, the level maps,
    CONTRACT_RANK, RANK_TO_BUCKET, RESERVATION_LIVE_STATES, BUCKET_ORDER, SOLD_BUCKETS.
    Imports NO LOGIC: classify_unit(), _tally_by(), _contract_ranks(),
    _live_reservation_units() and _paged_search_read() are NOT imported. The
    classification precedence, the scope filter, the child grouping, the sort order and
    the paging are all reimplemented below, so a service bug cannot hide by being
    present on both sides.

What it checks, walking the first reachable node at each level for every project:
  SCOPE HEADER — scope total + all SIX buckets + sold% vs an independent recompute over
                 this script's own scope filter; the project scope is additionally
                 asserted HARD against the board's per-project total.
  CROSSCHECK   — the scope total re-proved a third way by search_count (different RPC).
  CHILD ROWS   — the child group-id SET, every child's total + six buckets + sold%, and
                 the documented row ORDER (total desc, then name asc).
  Sigma        — Sigma(child totals) == scope total.
  LEAF PARITY  — at the building level the unit LIST is compared by MEMBERSHIP, not just
                 by count: the same unit_id set, and per unit the same code, name, raw
                 state AND derived bucket, plus the documented sort by code.
  404 GUARD    — an impossible scope id raises InventoryScopeNotFoundError.

A NOTE, never a failure: a child row's total can differ from that child's own scope
total when the same denormalised m2o id also appears outside the parent scope. That is
an upstream data quirk, not a module error; the REQUIRED identity (scope total ==
independent count over the node's own m2o) is always asserted hard.

Method discipline: READ-ONLY (search_read / search_count only). ALLOWED_METHODS
untouched. No FastAPI. No OpenAI. AI cost = $0.00.

Usage (from project root):
    python scripts/verify_projects_inventory_drill_live.py
"""

import asyncio
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.exceptions import InventoryScopeNotFoundError  # noqa: E402
from backend.modules.projects_inventory.domain import (  # noqa: E402
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
    LEAF_LEVEL,
    LEVEL_FIELD,
    RANK_TO_BUCKET,
    RESERVATION_LIVE_STATES,
    RESERVATION_MODEL,
    RESERVATION_STATE_FIELD,
    RESERVATION_UNIT_FIELD,
    SOLD_BUCKETS,
    UNIT_MODEL,
    UNIT_STATE_AVAILABLE,
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
_PAGE = 5000
_UNIT_FIELDS = ["id", "state", "code", "name",
                "project_id", "phase_id", "zone_id", "building_id"]


def _ok(flag: bool) -> str:
    return "PASS" if flag else "**FAIL**"


# ── local re-implementations (NOT imported from the service) ──────────────────


def _m2o_id(value):
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return int(value[0])
    return None


def _m2o_name(value):
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return str(value[1])
    return None


async def _read_all(client, model, domain, fields) -> list[dict]:
    """This script's OWN paged search_read."""
    rows, offset = [], 0
    while True:
        page = await client.execute_kw(
            model, "search_read", args=[domain],
            kwargs={"fields": fields, "order": "id", "limit": _PAGE, "offset": offset},
        )
        rows.extend(page)
        if len(page) < _PAGE:
            break
        offset += _PAGE
    return rows


async def _count(client, domain) -> int:
    return await client.execute_kw(UNIT_MODEL, "search_count", args=[domain])


def _max_contract_rank(contracts: list[dict]) -> dict[int, int]:
    ranks: dict[int, int] = {}
    unknown: dict[str, int] = {}
    for ct in contracts:
        state = ct.get(CONTRACT_STATE_FIELD)
        rank = CONTRACT_RANK.get(state)
        if rank is None:
            unknown[str(state)] = unknown.get(str(state), 0) + 1
            continue
        uid = _m2o_id(ct.get(CONTRACT_UNIT_FIELD))
        if uid is None:
            continue
        if rank > ranks.get(uid, 0):
            ranks[uid] = rank
    if unknown:
        raise RuntimeError(
            f"{CONTRACT_MODEL} carries non-cancel state(s) outside CONTRACT_RANK: "
            f"{unknown}. The locked contract vocabulary has changed — STOP."
        )
    return ranks


def _reserved_unit_ids(reservations: list[dict]) -> set[int]:
    out: set[int] = set()
    for rv in reservations:
        uid = _m2o_id(rv.get(RESERVATION_UNIT_FIELD))
        if uid is not None:
            out.add(uid)
    return out


def _bucket_of_unit(unit_state, max_rank, has_live_reservation: bool) -> str:
    """The document-driven precedence, written out here rather than imported."""
    if max_rank is not None:
        return RANK_TO_BUCKET[max_rank]
    if has_live_reservation:
        return BUCKET_RESERVED
    if unit_state == UNIT_STATE_AVAILABLE:
        return BUCKET_AVAILABLE
    return BUCKET_UNCLASSIFIED


def _empty() -> dict[str, int]:
    return {b: 0 for b in BUCKET_ORDER}


def _sold_pct(counts: dict[str, int], total: int) -> float:
    return round(100.0 * sum(counts[b] for b in SOLD_BUCKETS) / total, 2) if total else 0.0


def _mod_buckets(buckets: list[dict]) -> dict[str, int]:
    return {b["key"]: b["count"] for b in buckets}


async def _verify_scope(client, level, parent_id, units, bucket_of,
                        hard_total=None, tree_total=None):
    """Drill one scope and reconcile it against this script's own recompute.

    Returns (fail_count, module_result, child_rows_or_units)."""
    fail = 0
    field = LEVEL_FIELD[level]
    res = await get_inventory_drill(level, parent_id, client=client)

    # SCRIPT side: filter, tally, group — all local.
    scope = [u for u in units if _m2o_id(u.get(field)) == parent_id]
    s_counts = _empty()
    for u in scope:
        s_counts[bucket_of[u["id"]]] += 1
    s_total = len(scope)
    s_name = _m2o_name(scope[0].get(field)) if scope else None

    print(f"  [{level}={parent_id}] {res['parent_name']!r} -> child={res['child_level']} "
          f"(is_leaf={res['is_leaf']})")

    n_ok = res["parent_name"] == (s_name or "—")
    fail += 0 if n_ok else 1
    print(f"     {'parent_name':<16} MODULE={res['parent_name']!r}  SCRIPT={(s_name or '—')!r}  {_ok(n_ok)}")

    cl_ok = res["child_level"] == CHILD_LEVEL[level] and res["is_leaf"] == (level == LEAF_LEVEL)
    fail += 0 if cl_ok else 1
    print(f"     {'child_level':<16} MODULE={res['child_level']}  SCRIPT={CHILD_LEVEL[level]}  {_ok(cl_ok)}")

    t_ok = res["total_units"] == s_total
    fail += 0 if t_ok else 1
    print(f"     {'scope total':<16} MODULE={res['total_units']:>8,}  SCRIPT={s_total:>8,}  {_ok(t_ok)}")

    odoo_total = await _count(client, [(field, "=", parent_id)])
    x_ok = odoo_total == s_total
    fail += 0 if x_ok else 1
    print(f"     {'CROSSCHECK':<16} search_count({field}={parent_id}) = {odoo_total:>8,}  {_ok(x_ok)}")

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
                  f"NOTE: differs by {d:+,} — this node's m2o id also appears outside the "
                  f"parent scope (upstream denormalised-link quirk); not a module error")

    m_counts = _mod_buckets(res["buckets"])
    for b in BUCKET_ORDER:
        bk_ok = m_counts.get(b, 0) == s_counts[b]
        fail += 0 if bk_ok else 1
        print(f"     {b:<16} MODULE={m_counts.get(b, 0):>8,}  SCRIPT={s_counts[b]:>8,}  {_ok(bk_ok)}")

    sp = _sold_pct(s_counts, s_total)
    sp_ok = res["sold_pct"] == sp
    fail += 0 if sp_ok else 1
    print(f"     {'sold_pct':<16} MODULE={res['sold_pct']:>8}  SCRIPT={sp:>8}  {_ok(sp_ok)}")

    # ── LEAF: full membership parity, unit by unit ─────────────────────────────
    if res["is_leaf"]:
        m_units = res["units"]
        len_ok = len(m_units) == s_total
        fail += 0 if len_ok else 1
        print(f"     {'leaf len':<16} MODULE={len(m_units):>8,}  SCRIPT={s_total:>8,}  {_ok(len_ok)}")

        m_by_id = {u["unit_id"]: u for u in m_units}
        s_by_id = {u["id"]: u for u in scope}
        set_ok = set(m_by_id) == set(s_by_id)
        fail += 0 if set_ok else 1
        print(f"     {'leaf MEMBERSHIP':<16} unit_id sets identical  {_ok(set_ok)}")
        if not set_ok:
            print(f"        only in MODULE: {sorted(set(m_by_id) - set(s_by_id))[:20]}")
            print(f"        only in SCRIPT: {sorted(set(s_by_id) - set(m_by_id))[:20]}")

        mismatched = []
        for uid, mu in m_by_id.items():
            su = s_by_id.get(uid)
            if su is None:
                continue
            if (mu["bucket"] != bucket_of[uid]
                    or mu["state"] != su.get("state")
                    or mu["code"] != (su.get("code") or "")
                    or mu["name"] != (su.get("name") or "")):
                mismatched.append((uid, mu["bucket"], bucket_of[uid], mu["state"], su.get("state")))
        per_ok = not mismatched
        fail += 0 if per_ok else 1
        print(f"     {'leaf PER-UNIT':<16} bucket+state+code+name identical for all "
              f"{len(m_by_id):,}  {_ok(per_ok)}")
        for row in mismatched[:10]:
            print(f"        unit {row[0]}: bucket MODULE={row[1]} SCRIPT={row[2]} | "
                  f"state MODULE={row[3]} SCRIPT={row[4]}")

        sort_ok = [u["code"] for u in m_units] == sorted(u["code"] for u in m_units)
        fail += 0 if sort_ok else 1
        print(f"     {'leaf ORDER':<16} sorted by code  {_ok(sort_ok)}")

        leaf_counts = _empty()
        for u in m_units:
            leaf_counts[u["bucket"]] = leaf_counts.get(u["bucket"], 0) + 1
        for b in BUCKET_ORDER:
            lb_ok = leaf_counts[b] == s_counts[b]
            fail += 0 if lb_ok else 1
            print(f"     {'leaf ' + b:<16} MODULE={leaf_counts[b]:>8,}  SCRIPT={s_counts[b]:>8,}  {_ok(lb_ok)}")
        print()
        return fail, res, m_units

    # ── GROUP LEVEL: child-row parity ──────────────────────────────────────────
    child_field = CHILD_FIELD[level]
    s_children: dict[int, dict] = {}
    for u in scope:
        gid = _m2o_id(u.get(child_field))
        gid_key = gid if gid is not None else 0
        e = s_children.setdefault(
            gid_key, {"name": _m2o_name(u.get(child_field)) or "—", "total": 0, "buckets": _empty()}
        )
        e["total"] += 1
        e["buckets"][bucket_of[u["id"]]] += 1

    rows = res["rows"]
    m_ids = [r["group_id"] for r in rows]
    set_ok = set(m_ids) == set(s_children)
    fail += 0 if set_ok else 1
    print(f"     {'child SET':<16} {len(m_ids)} {res['child_level']}(s), ids identical  {_ok(set_ok)}")

    bad = 0
    for r in rows:
        sc = s_children.get(r["group_id"])
        if sc is None:
            bad += 1
            continue
        if r["total_units"] != sc["total"]:
            bad += 1
            continue
        rb = _mod_buckets(r["buckets"])
        if any(rb.get(b, 0) != sc["buckets"][b] for b in BUCKET_ORDER):
            bad += 1
            continue
        if r["sold_pct"] != _sold_pct(sc["buckets"], sc["total"]):
            bad += 1
    rows_ok = bad == 0
    fail += 0 if rows_ok else 1
    print(f"     {'child ROWS':<16} total+six buckets+sold% identical for all "
          f"{len(rows)}  {_ok(rows_ok)}")

    expected_order = [
        gid for gid, e in sorted(
            s_children.items(), key=lambda kv: (-kv[1]["total"], kv[1]["name"] or "")
        )
    ]
    ord_ok = m_ids == expected_order
    fail += 0 if ord_ok else 1
    print(f"     {'child ORDER':<16} total desc, then name asc  {_ok(ord_ok)}")

    sigma = sum(r["total_units"] for r in rows)
    s_ok = sigma == res["total_units"]
    fail += 0 if s_ok else 1
    print(f"     {'Sigma children':<16} {sigma:,} == scope {res['total_units']:,}  {_ok(s_ok)}")
    print()
    return fail, res, rows


async def main():
    print(_SEP)
    print("  PROJECTS INVENTORY (Slice 1b, six-bucket document-driven) — DRILL-DOWN")
    print("  IDENTITY-EQUAL LIVE VERIFICATION (READ-ONLY, $0). SCRIPT numbers are recomputed")
    print("  here from raw reads; no classification/tally/grouping code is imported.")
    print(f"  ALLOWED_METHODS : {sorted(ALLOWED_METHODS)}")
    print(f"  Models          : {UNIT_MODEL} / {CONTRACT_MODEL} / {RESERVATION_MODEL}")
    print(f"  Level fields    : {LEVEL_FIELD}")
    print(f"  Bucket order    : {list(BUCKET_ORDER)}")
    print(_SEP)
    print()

    fail = 0
    _cache.clear()

    async with OdooClient() as client:
        # ── the three INDEPENDENT raw fetches, then a local classification ──────
        units = await _read_all(client, UNIT_MODEL, [], _UNIT_FIELDS)
        contracts = await _read_all(
            client, CONTRACT_MODEL,
            [(CONTRACT_STATE_FIELD, "!=", CONTRACT_CANCEL_STATE)],
            [CONTRACT_UNIT_FIELD, CONTRACT_STATE_FIELD],
        )
        reservations = await _read_all(
            client, RESERVATION_MODEL,
            [(RESERVATION_STATE_FIELD, "in", sorted(RESERVATION_LIVE_STATES))],
            [RESERVATION_UNIT_FIELD],
        )
        ranks = _max_contract_rank(contracts)
        held = _reserved_unit_ids(reservations)
        bucket_of = {
            u["id"]: _bucket_of_unit(u.get("state"), ranks.get(u["id"]), u["id"] in held)
            for u in units
        }

        print(_SEP2)
        print("  RAW (this script's own reads)")
        print(f"    units {len(units):,} | non-cancel contracts {len(contracts):,} | "
              f"live reservations {len(reservations):,}")
        tally = {b: 0 for b in BUCKET_ORDER}
        for b in bucket_of.values():
            tally[b] += 1
        print(f"    script-side classification: {tally}")
        print()

        overview = await get_inventory_overview(client=client)
        board_total = {p["project_id"]: p["total_units"] for p in overview["projects"]}

        for proj in overview["projects"]:
            pid = proj["project_id"]
            print(_SEP)
            print(f"  PROJECT [{pid}] {proj['project_name']!r}  (board total {proj['total_units']:,})")
            print(_SEP2)

            f, _res, phases = await _verify_scope(
                client, "project", pid, units, bucket_of, hard_total=board_total.get(pid))
            fail += f
            if not phases:
                print("     (no phases — skipping deeper levels)\n")
                continue

            phase_id = phases[0]["group_id"]
            f, _res, zones = await _verify_scope(
                client, "phase", phase_id, units, bucket_of, tree_total=phases[0]["total_units"])
            fail += f
            if not zones:
                continue

            zone_id = zones[0]["group_id"]
            f, _res, buildings = await _verify_scope(
                client, "zone", zone_id, units, bucket_of, tree_total=zones[0]["total_units"])
            fail += f
            if not buildings:
                continue

            bldg_id = buildings[0]["group_id"]
            f, _res, _u = await _verify_scope(
                client, "building", bldg_id, units, bucket_of, tree_total=buildings[0]["total_units"])
            fail += f

        # 404 guard — an impossible scope id must raise, never return empty.
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
