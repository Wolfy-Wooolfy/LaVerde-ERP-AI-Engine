"""
scripts/verify_projects_inventory_live.py — Projects Inventory (Slice 1)
identity-equal LIVE verification (READ-ONLY, $0 AI).

Proves the module's inventory-by-status numbers match independent direct Odoo
queries. Every "ODOO" number is an independent search_count issued by THIS script;
the "MODULE" numbers come from get_inventory_overview() (injected with the same
read-only client). NOTHING here writes to Odoo.

What it checks:
  STATES   — independent search_count per live state value; folded into the LOCKED
             buckets (available / reserved{+initial} / contracted{+delivered}).
  OVERALL  — MODULE total + 3 bucket counts + sold% vs ODOO, side by side.
  IDENTITY — (available + reserved + contracted) == total, AND that this equals the
             independent Σ of per-state search_counts.
  PER-PROJECT — for every project the module returns: MODULE total + 3 buckets vs
             independent ODOO search_count over (project_id, state) folded to buckets.
  RECONCILE — Σ per-project totals == overall total.

Method discipline: READ-ONLY (search_count / search_read only). ALLOWED_METHODS
untouched. No FastAPI. No OpenAI. AI cost = $0.00.

Pre-flight (run manually BEFORE this script): kill all python processes; purge all
__pycache__; start uvicorn WITHOUT --reload (if used — this script talks to Odoo
directly and does not require uvicorn).

Usage (from project root):
    python scripts/verify_projects_inventory_live.py
"""

import asyncio
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.modules.projects_inventory.domain import (  # noqa: E402
    BUCKET_ORDER,
    SOLD_BUCKET,
    STATE_TO_BUCKET,
    UNIT_MODEL,
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
_LIVE_STATES = sorted(STATE_TO_BUCKET)   # the 5 LOCKED state values


def _ok(flag: bool) -> str:
    return "PASS" if flag else "**FAIL**"


def _empty_buckets() -> dict[str, int]:
    return {b: 0 for b in BUCKET_ORDER}


async def _count(client, domain) -> int:
    return await client.execute_kw(UNIT_MODEL, "search_count", args=[domain])


def _bucket_of(buckets: list[dict]) -> dict[str, int]:
    """MODULE bucket list [{key,count,pct}] -> {key: count}."""
    return {b["key"]: b["count"] for b in buckets}


async def main():
    print(_SEP)
    print("  PROJECTS INVENTORY (Slice 1) — IDENTITY-EQUAL LIVE VERIFICATION (READ-ONLY, $0)")
    print(f"  ALLOWED_METHODS : {sorted(ALLOWED_METHODS)}")
    print(f"  Model           : {UNIT_MODEL}")
    print(f"  Bucket map      : {STATE_TO_BUCKET}")
    print(_SEP)
    print()

    fail = 0
    _cache.clear()   # ensure the module re-queries live, not a stale cache entry

    async with OdooClient() as client:
        # ── independent per-state counts (overall) -> folded to buckets ────────
        odoo_total = await _count(client, [])
        odoo_state_counts: dict[str, int] = {}
        odoo_buckets = _empty_buckets()
        for st in _LIVE_STATES:
            n = await _count(client, [("state", "=", st)])
            odoo_state_counts[st] = n
            odoo_buckets[STATE_TO_BUCKET[st]] += n
        odoo_state_sum = sum(odoo_state_counts.values())

        # ── run the MODULE (inject the same read-only client) ──────────────────
        result = await get_inventory_overview(client=client)
        mod_buckets = _bucket_of(result["buckets"])
        mod_total = result["total_units"]

        # ── OVERALL identity ───────────────────────────────────────────────────
        print(_SEP)
        print("  OVERALL — MODULE vs ODOO (independent search_count, folded to buckets)")
        print(_SEP)
        print(f"  {'metric':<16} | {'MODULE':>10} | {'ODOO':>10} | result")
        print(f"  {'-'*16}-+-{'-'*10}-+-{'-'*10}-+-{'-'*8}")
        t_ok = mod_total == odoo_total == odoo_state_sum
        fail += 0 if t_ok else 1
        print(f"  {'total_units':<16} | {mod_total:>10,} | {odoo_total:>10,} | {_ok(t_ok)}")
        for b in BUCKET_ORDER:
            b_ok = mod_buckets.get(b, 0) == odoo_buckets[b]
            fail += 0 if b_ok else 1
            print(f"  {b:<16} | {mod_buckets.get(b, 0):>10,} | {odoo_buckets[b]:>10,} | {_ok(b_ok)}")

        # sold% — recompute independently from ODOO buckets.
        odoo_sold_pct = round(100.0 * odoo_buckets[SOLD_BUCKET] / odoo_total, 2) if odoo_total else 0.0
        s_ok = result["sold_pct"] == odoo_sold_pct
        fail += 0 if s_ok else 1
        print(f"  {'sold_pct (%)':<16} | {result['sold_pct']:>10} | {odoo_sold_pct:>10} | {_ok(s_ok)}")

        # bucket-sum identity (A + R + C == total), both sides.
        mod_sum = sum(mod_buckets.values())
        id_ok = mod_sum == mod_total and odoo_state_sum == odoo_total
        fail += 0 if id_ok else 1
        print()
        print(f"  IDENTITY (available + reserved + contracted == total):")
        print(f"    MODULE: {mod_buckets.get('available',0):,} + {mod_buckets.get('reserved',0):,} + "
              f"{mod_buckets.get('contracted',0):,} = {mod_sum:,}  (total {mod_total:,})  {_ok(mod_sum == mod_total)}")
        print(f"    ODOO  : Σ per-state {odoo_state_sum:,} == total {odoo_total:,}  {_ok(odoo_state_sum == odoo_total)}")
        print()

        # per-state breakdown (transparency).
        print(_SEP2)
        print("  Per-state ODOO counts (independent):")
        for st in _LIVE_STATES:
            print(f"    {st:<12} -> bucket {STATE_TO_BUCKET[st]:<11} : {odoo_state_counts[st]:>8,}")
        print()

        # ── PER-PROJECT identity ───────────────────────────────────────────────
        print(_SEP)
        print("  PER-PROJECT — MODULE vs ODOO (independent search_count over project_id × state)")
        print(_SEP)
        project_total_check = 0
        for p in result["projects"]:
            pid = p["project_id"]
            pname = p["project_name"]
            mb = _bucket_of(p["buckets"])
            odoo_pbuckets = _empty_buckets()
            for st in _LIVE_STATES:
                n = await _count(client, [("project_id", "=", pid), ("state", "=", st)])
                odoo_pbuckets[STATE_TO_BUCKET[st]] += n
            odoo_ptotal = await _count(client, [("project_id", "=", pid)])

            print(f"  [{pid}] {pname}  (early_stage={p['is_early_stage']}, sold={p['sold_pct']}%)")
            pt_ok = p["total_units"] == odoo_ptotal
            fail += 0 if pt_ok else 1
            print(f"     {'total':<14} MODULE={p['total_units']:>8,}  ODOO={odoo_ptotal:>8,}  {_ok(pt_ok)}")
            for b in BUCKET_ORDER:
                gb_ok = mb.get(b, 0) == odoo_pbuckets[b]
                fail += 0 if gb_ok else 1
                print(f"     {b:<14} MODULE={mb.get(b, 0):>8,}  ODOO={odoo_pbuckets[b]:>8,}  {_ok(gb_ok)}")
            # per-project bucket-sum reconcile.
            psum = sum(mb.values())
            pr_ok = psum == p["total_units"]
            fail += 0 if pr_ok else 1
            print(f"     {'RECONCILE Σ':<14} {psum:,} == total {p['total_units']:,}  {_ok(pr_ok)}")
            print()
            project_total_check += p["total_units"]

        # ── Σ per-project totals == overall total ──────────────────────────────
        sigma_ok = project_total_check == mod_total
        fail += 0 if sigma_ok else 1
        print(f"  Σ per-project totals {project_total_check:,} == overall total {mod_total:,}  {_ok(sigma_ok)}")
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
