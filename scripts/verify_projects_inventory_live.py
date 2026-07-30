"""
scripts/verify_projects_inventory_live.py — Projects Inventory (Slice 1, six-bucket
DOCUMENT-DRIVEN model) identity-equal LIVE verification (READ-ONLY, $0 AI).

Proves the board's inventory-by-status numbers match an INDEPENDENT recomputation.
Every "SCRIPT" number is computed by THIS file from its own raw Odoo reads; the
"MODULE" numbers come from get_inventory_overview() (injected with the same read-only
client). NOTHING here writes to Odoo.

INDEPENDENCE RULE
    This script imports CONSTANTS from domain.py — the model/field names, CONTRACT_RANK,
    RANK_TO_BUCKET, RESERVATION_LIVE_STATES, BUCKET_ORDER, SOLD_BUCKETS, the early-stage
    threshold. Those ARE the locked spec, and copying them here would only let the spec
    and the check drift apart.
    It imports NO LOGIC. classify_unit(), _tally_by(), _contract_ranks(),
    _live_reservation_units() and _paged_search_read() are deliberately NOT imported:
    the classification precedence, the per-project tally and even the paging are
    reimplemented below. A bug in the service's implementation therefore cannot hide
    itself by also being present on this side of the comparison.

The document-driven precedence, reimplemented locally in _bucket_of_unit():
    (a) any NON-cancel contract  -> RANK_TO_BUCKET[max rank over that unit's contracts]
    (b) else a LIVE reservation  -> reserved
    (c) else state == available  -> available
    (d) else                     -> unclassified
The old rs.structure.unit.state -> bucket map is GONE and is never consulted beyond
step (c); a unit sitting in `reserved` with no reservation and no contract behind it is
`unclassified`, and that is the point.

What it checks:
  RAW        — the three independent fetches, with the live vocabularies they found.
  OVERALL    — MODULE total + all SIX bucket counts + sold% vs SCRIPT, side by side.
  IDENTITY   — Sigma(six buckets) == total, on BOTH sides.
  CROSSCHECK — total re-proved a THIRD way, by search_count (a different RPC).
  PER-PROJECT— every project's total + six buckets + sold% + is_early_stage.
  ORDER      — projects come back sorted by total_units desc (then name asc).
  RECONCILE  — Sigma per-project totals == overall total, on both sides.

Method discipline: READ-ONLY (search_read / search_count only). ALLOWED_METHODS
untouched. No FastAPI. No OpenAI. AI cost = $0.00.

Usage (from project root):
    python scripts/verify_projects_inventory_live.py
"""

import asyncio
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.modules.projects_inventory.domain import (  # noqa: E402
    BUCKET_AVAILABLE,
    BUCKET_ORDER,
    BUCKET_RESERVED,
    BUCKET_UNCLASSIFIED,
    CONTRACT_CANCEL_STATE,
    CONTRACT_MODEL,
    CONTRACT_RANK,
    CONTRACT_STATE_FIELD,
    CONTRACT_UNIT_FIELD,
    EARLY_STAGE_SOLD_PCT_THRESHOLD,
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
    get_inventory_overview,
)
from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient  # noqa: E402

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_SEP = "=" * 100
_SEP2 = "-" * 100
_PAGE = 5000


def _ok(flag: bool) -> str:
    return "PASS" if flag else "**FAIL**"


# ── local re-implementations (NOT imported from the service) ──────────────────


def _m2o_id(value):
    """Odoo many2one [id, name] (or False) -> id, else None. Local copy."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return int(value[0])
    return None


def _m2o_name(value):
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return str(value[1])
    return None


async def _read_all(client, model, domain, fields) -> list[dict]:
    """This script's OWN paged search_read — deliberately not the service's."""
    rows, offset = [], 0
    while True:
        page = await client.execute_kw(
            model,
            "search_read",
            args=[domain],
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
    """unit_id -> MAX CONTRACT_RANK over that unit's non-cancel contracts. Local."""
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
    """The unit ids on a LIVE reservation hold. Local."""
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


async def main():
    print(_SEP)
    print("  PROJECTS INVENTORY (Slice 1, six-bucket document-driven) — IDENTITY-EQUAL LIVE")
    print("  VERIFICATION (READ-ONLY, $0). SCRIPT numbers are recomputed here from raw reads;")
    print("  no classification/tally code is imported from the service.")
    print(f"  ALLOWED_METHODS : {sorted(ALLOWED_METHODS)}")
    print(f"  Models          : {UNIT_MODEL} / {CONTRACT_MODEL} / {RESERVATION_MODEL}")
    print(f"  Bucket order    : {list(BUCKET_ORDER)}")
    print(f"  Contract ranks  : {CONTRACT_RANK}  ->  {RANK_TO_BUCKET}")
    print(f"  Live reservation states : {sorted(RESERVATION_LIVE_STATES)}")
    print(_SEP)
    print()

    fail = 0
    _cache.clear()   # force the module to re-query live, never a stale cache entry

    async with OdooClient() as client:
        # ── the three INDEPENDENT raw fetches ──────────────────────────────────
        units = await _read_all(client, UNIT_MODEL, [], ["id", "state", "project_id"])
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

        print(_SEP2)
        print("  RAW (this script's own reads)")
        print(f"    units                    : {len(units):>8,}")
        print(f"    non-cancel contracts     : {len(contracts):>8,}")
        print(f"    live reservations        : {len(reservations):>8,}")
        seen_unit_states = sorted({str(u.get('state')) for u in units})
        seen_ct_states = sorted({str(c.get(CONTRACT_STATE_FIELD)) for c in contracts})
        print(f"    unit states seen         : {seen_unit_states}")
        print(f"    contract states seen     : {seen_ct_states}")
        print()

        # ── INDEPENDENT classification + tally ─────────────────────────────────
        ranks = _max_contract_rank(contracts)
        held = _reserved_unit_ids(reservations)

        s_overall = _empty()
        s_projects: dict[int, dict] = {}
        for u in units:
            bucket = _bucket_of_unit(u.get("state"), ranks.get(u["id"]), u["id"] in held)
            s_overall[bucket] += 1
            pid = _m2o_id(u.get("project_id"))
            pid_key = pid if pid is not None else 0
            entry = s_projects.setdefault(
                pid_key,
                {"name": _m2o_name(u.get("project_id")) or "—", "total": 0, "buckets": _empty()},
            )
            entry["total"] += 1
            entry["buckets"][bucket] += 1
        s_total = len(units)

        # ── the MODULE (same read-only client injected) ────────────────────────
        result = await get_inventory_overview(client=client)
        m_buckets = _mod_buckets(result["buckets"])
        m_total = result["total_units"]

        # ── OVERALL ────────────────────────────────────────────────────────────
        print(_SEP)
        print("  OVERALL — MODULE vs SCRIPT (independent document-driven recompute)")
        print(_SEP)
        print(f"  {'metric':<16} | {'MODULE':>10} | {'SCRIPT':>10} | result")
        print(f"  {'-'*16}-+-{'-'*10}-+-{'-'*10}-+-{'-'*8}")
        t_ok = m_total == s_total
        fail += 0 if t_ok else 1
        print(f"  {'total_units':<16} | {m_total:>10,} | {s_total:>10,} | {_ok(t_ok)}")
        for b in BUCKET_ORDER:
            b_ok = m_buckets.get(b, 0) == s_overall[b]
            fail += 0 if b_ok else 1
            print(f"  {b:<16} | {m_buckets.get(b, 0):>10,} | {s_overall[b]:>10,} | {_ok(b_ok)}")
        s_sold = _sold_pct(s_overall, s_total)
        sp_ok = result["sold_pct"] == s_sold
        fail += 0 if sp_ok else 1
        print(f"  {'sold_pct (%)':<16} | {result['sold_pct']:>10} | {s_sold:>10} | {_ok(sp_ok)}")
        print()

        # Sigma(buckets) == total, both sides.
        m_sum, s_sum = sum(m_buckets.values()), sum(s_overall.values())
        id_ok = (m_sum == m_total) and (s_sum == s_total)
        fail += 0 if id_ok else 1
        print(f"  IDENTITY  Sigma(six buckets) == total")
        print(f"    MODULE: {m_sum:,} == {m_total:,}  {_ok(m_sum == m_total)}")
        print(f"    SCRIPT: {s_sum:,} == {s_total:,}  {_ok(s_sum == s_total)}")

        # A THIRD, orthogonal probe of the total: search_count, a different RPC.
        cnt_total = await _count(client, [])
        c_ok = cnt_total == s_total == m_total
        fail += 0 if c_ok else 1
        print(f"  CROSSCHECK  search_count(all units) = {cnt_total:,}  {_ok(c_ok)}")
        print()

        # ── PER-PROJECT ────────────────────────────────────────────────────────
        print(_SEP)
        print("  PER-PROJECT — MODULE vs SCRIPT")
        print(_SEP)
        m_pids = [p["project_id"] for p in result["projects"]]
        set_ok = set(m_pids) == set(s_projects)
        fail += 0 if set_ok else 1
        print(f"  project id sets match: MODULE {sorted(m_pids)} vs SCRIPT "
              f"{sorted(s_projects)}  {_ok(set_ok)}")
        print()

        m_project_sigma = 0
        for p in result["projects"]:
            pid = p["project_id"]
            mb = _mod_buckets(p["buckets"])
            sp = s_projects.get(pid)
            print(f"  [{pid}] {p['project_name']!r}")
            if sp is None:
                fail += 1
                print(f"     {'** the script found no such project — FAIL':<14}")
                print()
                continue
            pt_ok = p["total_units"] == sp["total"]
            fail += 0 if pt_ok else 1
            print(f"     {'total':<14} MODULE={p['total_units']:>8,}  SCRIPT={sp['total']:>8,}  {_ok(pt_ok)}")
            for b in BUCKET_ORDER:
                gb_ok = mb.get(b, 0) == sp["buckets"][b]
                fail += 0 if gb_ok else 1
                print(f"     {b:<14} MODULE={mb.get(b, 0):>8,}  SCRIPT={sp['buckets'][b]:>8,}  {_ok(gb_ok)}")
            ps = _sold_pct(sp["buckets"], sp["total"])
            ps_ok = p["sold_pct"] == ps
            fail += 0 if ps_ok else 1
            print(f"     {'sold_pct':<14} MODULE={p['sold_pct']:>8}  SCRIPT={ps:>8}  {_ok(ps_ok)}")
            es = ps < EARLY_STAGE_SOLD_PCT_THRESHOLD
            es_ok = p["is_early_stage"] == es
            fail += 0 if es_ok else 1
            print(f"     {'early_stage':<14} MODULE={str(p['is_early_stage']):>8}  SCRIPT={str(es):>8}  {_ok(es_ok)}")
            psum = sum(mb.values())
            pr_ok = psum == p["total_units"]
            fail += 0 if pr_ok else 1
            print(f"     {'RECONCILE':<14} Sigma buckets {psum:,} == total {p['total_units']:,}  {_ok(pr_ok)}")
            # Orthogonal per-project probe: a search_count on the m2o.
            odoo_pt = await _count(client, [("project_id", "=", pid)])
            oc_ok = odoo_pt == p["total_units"]
            fail += 0 if oc_ok else 1
            print(f"     {'CROSSCHECK':<14} search_count(project_id={pid}) = {odoo_pt:,}  {_ok(oc_ok)}")
            print()
            m_project_sigma += p["total_units"]

        # ── ORDER + RECONCILE ──────────────────────────────────────────────────
        expected_order = [
            pid for pid, e in sorted(
                s_projects.items(), key=lambda kv: (-kv[1]["total"], kv[1]["name"] or "")
            )
        ]
        ord_ok = m_pids == expected_order
        fail += 0 if ord_ok else 1
        print(f"  ORDER  projects sorted by total desc, then name asc: "
              f"MODULE {m_pids} vs SCRIPT {expected_order}  {_ok(ord_ok)}")

        sigma_ok = m_project_sigma == m_total == s_total
        fail += 0 if sigma_ok else 1
        print(f"  RECONCILE  Sigma per-project totals {m_project_sigma:,} == overall "
              f"{m_total:,}  {_ok(sigma_ok)}")
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
