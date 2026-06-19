"""
scripts/verify_projects_inventory_value_live.py — Projects Inventory Value & Area
(Slice 2) identity-equal LIVE verification (READ-ONLY, $0 AI).

Proves value_service's numbers match independent direct Odoo queries. Every "ODOO"
number is recomputed by THIS script straight from rs.structure.unit + rs.contract;
the "MODULE" numbers come from get_value_area_overview() (injected with the same
read-only client). NOTHING here writes to Odoo.

What it checks, for COMBINED (NC+Cassette) and EACH project (New Capital, Cassette):
  a available_list_value      Σ unit.amount over available units
  b available_area            Σ unit.total_area over available units
  c sold_realized_value       Σ (per-unit Σ non-cancel contract.sales_price) over sold
  d sold_contracted_area      Σ unit.total_area over sold units
  e sold_list_value           Σ unit.amount over sold units
  f gap_abs / gap_pct         e − c  and  (e − c)/e
  g pct_units_below_list      below ÷ sold-with-contract
  h avg_price_per_m2_realized c ÷ d
  i sold_units_count / sold_units_with_contract_count
Plus TRIPLE-AGREEMENT: independent search_count for the available / sold unit counts,
and Σ per-project == combined for every additive metric.

Method discipline: READ-ONLY (search_read / search_count only). ALLOWED_METHODS
untouched. No FastAPI. No OpenAI. AI cost = $0.00. Talks to Odoo directly — does not
require uvicorn (still: kill python + purge __pycache__ before any live run).

Usage (from project root):
    python scripts/verify_projects_inventory_value_live.py
"""

import asyncio
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.modules.projects_inventory.domain import (  # noqa: E402
    AVAILABLE_STATES,
    CONTRACT_CANCEL_STATE,
    CONTRACT_MODEL,
    CONTRACT_PRICE_FIELD,
    CONTRACT_STATE_FIELD,
    CONTRACT_UNIT_FIELD,
    SOLD_STATES,
    UNIT_AMOUNT_FIELD,
    UNIT_AREA_FIELD,
    UNIT_MODEL,
    VALUE_SCOPE_PROJECT_IDS,
)
from backend.modules.projects_inventory.services import cache as _cache  # noqa: E402
from backend.modules.projects_inventory.services.value_service import (  # noqa: E402
    get_value_area_overview,
)
from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient  # noqa: E402

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_SEP = "=" * 100
_SEP2 = "-" * 100
_CHUNK = 200

# Sanity anchors (ballpark — gates a wildly-off computation; not an equality target).
_ANCHOR_AVAIL_LIST = (4.0e9, 5.0e9)
_ANCHOR_SOLD_REAL = (5.5e9, 6.5e9)
_ANCHOR_GAP_PCT = (5.0, 15.0)


def _ok(flag: bool) -> str:
    return "PASS" if flag else "**FAIL**"


def _num(v) -> float:
    return float(v) if isinstance(v, (int, float)) else 0.0


def _m2o_id(v):
    if isinstance(v, (list, tuple)) and len(v) == 2:
        return int(v[0])
    return None


def _c2(v: float) -> float:
    return round(v, 2)


async def _odoo_compute(client, project_ids: list[int]) -> dict:
    """Independent recomputation of a–i for a project scope, straight from Odoo.
    Mirrors value_service's rounding (round(raw, 2)) so the comparison is identity."""
    units = await client.execute_kw(
        UNIT_MODEL, "search_read",
        args=[[("project_id", "in", project_ids)]],
        kwargs={"fields": ["id", "state", UNIT_AMOUNT_FIELD, UNIT_AREA_FIELD],
                "order": "id"},
    )
    available = [u for u in units if u["state"] in AVAILABLE_STATES]
    sold = [u for u in units if u["state"] in SOLD_STATES]
    sold_ids = sorted(u["id"] for u in sold)

    contracts: list[dict] = []
    for i in range(0, len(sold_ids), _CHUNK):
        chunk = sold_ids[i:i + _CHUNK]
        contracts += await client.execute_kw(
            CONTRACT_MODEL, "search_read",
            args=[[(CONTRACT_UNIT_FIELD, "in", chunk)]],
            kwargs={"fields": [CONTRACT_UNIT_FIELD, CONTRACT_PRICE_FIELD, CONTRACT_STATE_FIELD]},
        )
    realized: dict[int, float] = {}
    for ct in contracts:
        if ct.get(CONTRACT_STATE_FIELD) == CONTRACT_CANCEL_STATE:
            continue
        uid = _m2o_id(ct.get(CONTRACT_UNIT_FIELD))
        if uid is not None:
            realized[uid] = realized.get(uid, 0.0) + _num(ct.get(CONTRACT_PRICE_FIELD))

    available_list_value = sum(_num(u[UNIT_AMOUNT_FIELD]) for u in available)
    available_area = sum(_num(u[UNIT_AREA_FIELD]) for u in available)
    sold_list_value = sum(_num(u[UNIT_AMOUNT_FIELD]) for u in sold)
    sold_contracted_area = sum(_num(u[UNIT_AREA_FIELD]) for u in sold)
    sold_with_contract = [u for u in sold if u["id"] in realized]
    sold_realized_value = sum(realized[u["id"]] for u in sold_with_contract)
    gap_abs = sold_list_value - sold_realized_value
    gap_pct = (gap_abs / sold_list_value * 100.0) if sold_list_value else 0.0
    capture_pct = (sold_realized_value / sold_list_value * 100.0) if sold_list_value else 0.0
    below = sum(1 for u in sold_with_contract
                if _c2(realized[u["id"]]) < _c2(_num(u[UNIT_AMOUNT_FIELD])))
    pct_below = (below / len(sold_with_contract) * 100.0) if sold_with_contract else 0.0
    avg_m2 = (sold_realized_value / sold_contracted_area) if sold_contracted_area else 0.0
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
        "pct_units_below_list": round(pct_below, 2),
        "avg_price_per_m2_realized": round(avg_m2, 2),
        "sold_pct_units": round(sold_pct_units, 2),
    }


_METRICS = [
    ("total_units", "int"), ("available_units_count", "int"),
    ("sold_units_count", "int"), ("sold_units_with_contract_count", "int"),
    ("sold_units_below_list_count", "int"),
    ("available_list_value", "money"), ("available_area", "area"),
    ("sold_realized_value", "money"), ("sold_contracted_area", "area"),
    ("sold_list_value", "money"), ("gap_abs", "money"), ("gap_pct", "pct"),
    ("capture_pct", "pct"), ("pct_units_below_list", "pct"),
    ("avg_price_per_m2_realized", "money"), ("sold_pct_units", "pct"),
]


def _fmt(v, kind: str) -> str:
    if kind == "int":
        return f"{int(v):>18,}"
    if kind in ("money", "area"):
        return f"{v:>18,.2f}"
    return f"{v:>17,.2f}%"


def _compare(title: str, mod: dict, odoo: dict) -> int:
    fails = 0
    print(_SEP2)
    print(f"  {title}")
    print(_SEP2)
    print(f"  {'metric':<32} | {'MODULE':>19} | {'ODOO':>19} | result")
    for key, kind in _METRICS:
        m, o = mod[key], odoo[key]
        good = (m == o)
        fails += 0 if good else 1
        print(f"  {key:<32} | {_fmt(m, kind)} | {_fmt(o, kind)} | {_ok(good)}")
    return fails


async def main():
    print(_SEP)
    print("  PROJECTS INVENTORY — VALUE & AREA (Slice 2) — IDENTITY-EQUAL LIVE VERIFY (READ-ONLY, $0)")
    print(f"  ALLOWED_METHODS : {sorted(ALLOWED_METHODS)}")
    print(f"  Scope project ids : {list(VALUE_SCOPE_PROJECT_IDS)}  (La Puerta excluded)")
    print(f"  SOLD states : {sorted(SOLD_STATES)}   AVAILABLE states : {sorted(AVAILABLE_STATES)}")
    print(_SEP)

    fail = 0
    _cache.clear()

    async with OdooClient() as client:
        result = await get_value_area_overview(client=client)
        mod_combined = {k: result[k] for k, _ in _METRICS}
        mod_projects = {p["project_id"]: p for p in result["projects"]}

        # ── COMBINED ───────────────────────────────────────────────────────────
        odoo_combined = await _odoo_compute(client, list(VALUE_SCOPE_PROJECT_IDS))
        fail += _compare("COMBINED (New Capital + Cassette)", mod_combined, odoo_combined)
        print()

        # ── PER PROJECT ──────────────────────────────────────────────────────────
        for pid in VALUE_SCOPE_PROJECT_IDS:
            mp = mod_projects.get(pid)
            if mp is None:
                print(f"  **FAIL** — project id {pid} missing from MODULE output.")
                fail += 1
                continue
            odoo_p = await _odoo_compute(client, [pid])
            fail += _compare(f"PROJECT id={pid}  {mp['project_name']!r}",
                             {k: mp[k] for k, _ in _METRICS}, odoo_p)
            print()

        # ── TRIPLE-AGREEMENT: independent search_count for the unit counts ───────
        print(_SEP2)
        print("  TRIPLE-CHECK — independent search_count (counts) vs MODULE")
        print(_SEP2)
        ids = list(VALUE_SCOPE_PROJECT_IDS)
        cnt_avail = await client.execute_kw(
            UNIT_MODEL, "search_count",
            args=[[("project_id", "in", ids), ("state", "in", sorted(AVAILABLE_STATES))]])
        cnt_sold = await client.execute_kw(
            UNIT_MODEL, "search_count",
            args=[[("project_id", "in", ids), ("state", "in", sorted(SOLD_STATES))]])
        a_ok = cnt_avail == mod_combined["available_units_count"]
        s_ok = cnt_sold == mod_combined["sold_units_count"]
        fail += 0 if a_ok else 1
        fail += 0 if s_ok else 1
        print(f"  available_units_count  MODULE={mod_combined['available_units_count']:>8,}  "
              f"search_count={cnt_avail:>8,}  {_ok(a_ok)}")
        print(f"  sold_units_count       MODULE={mod_combined['sold_units_count']:>8,}  "
              f"search_count={cnt_sold:>8,}  {_ok(s_ok)}")
        print()

        # ── Σ per-project == combined (additive metrics) ─────────────────────────
        print(_SEP2)
        print("  RECONCILE — Σ per-project == combined")
        print(_SEP2)
        sum_keys = [
            "total_units", "available_units_count", "sold_units_count",
            "sold_units_with_contract_count", "sold_units_below_list_count",
            "available_list_value", "available_area", "sold_realized_value",
            "sold_contracted_area", "sold_list_value",
        ]
        for key in sum_keys:
            per_sum = round(sum(mod_projects[pid][key] for pid in mod_projects), 2)
            comb = round(mod_combined[key], 2)
            good = per_sum == comb
            fail += 0 if good else 1
            print(f"  {key:<32} Σproj={per_sum:>18,.2f}  combined={comb:>18,.2f}  {_ok(good)}")
        print()

        # ── SANITY GATE (display) ────────────────────────────────────────────────
        print(_SEP2)
        print("  SANITY GATE — combined vs ballpark anchors")
        print(_SEP2)
        al = mod_combined["available_list_value"]
        sr = mod_combined["sold_realized_value"]
        gp = mod_combined["gap_pct"]
        al_ok = _ANCHOR_AVAIL_LIST[0] <= al <= _ANCHOR_AVAIL_LIST[1]
        sr_ok = _ANCHOR_SOLD_REAL[0] <= sr <= _ANCHOR_SOLD_REAL[1]
        gp_ok = _ANCHOR_GAP_PCT[0] <= gp <= _ANCHOR_GAP_PCT[1]
        fail += 0 if al_ok else 1
        fail += 0 if sr_ok else 1
        fail += 0 if gp_ok else 1
        print(f"  available_list_value  {al:>18,.2f}  in [4.0bn, 5.0bn]   {_ok(al_ok)}")
        print(f"  sold_realized_value   {sr:>18,.2f}  in [5.5bn, 6.5bn]   {_ok(sr_ok)}")
        print(f"  gap_pct               {gp:>17,.2f}%  in [5%, 15%]        {_ok(gp_ok)}")
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
