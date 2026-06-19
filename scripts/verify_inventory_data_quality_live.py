"""
scripts/verify_inventory_data_quality_live.py — Inventory Data Quality identity-equal
LIVE verification (READ-ONLY, $0 AI).

Proves data_quality_service's three checks match an INDEPENDENT direct recomputation from
Odoo. Every "ODOO" figure is recomputed by THIS script straight from rs.structure.unit +
rs.contract + the three parent models; the "MODULE" figures come from
get_data_quality_overview() (injected with the same read-only client). NOTHING writes.

What it checks, for the COMBINED portfolio AND per project — the flagged-unit SETS
(by id AND by code) and the counts must be identity-equal:
  A — no_contract       sold (state ∈ SOLD_STATES) with NO non-cancel rs.contract via unit_id
  B — broken_hierarchy  authoritative parent-record chain: phase_id→project == project_id ;
                        zone_id→phase == phase_id ; building_id→zone == zone_id (first break wins)
  C — no_list_price     sold unit whose `amount` is 0 / falsy
Plus TRIPLE-AGREEMENT: independent search_count for total/sold unit counts (portfolio +
per project) and read_group for contract coverage; and a SANITY GATE A==5 / B==8 / C==0.

Method discipline: READ-ONLY (search_read / search_count / read_group only). ALLOWED_METHODS
untouched. No FastAPI. No OpenAI. AI cost = $0.00. Talks to Odoo directly — does not require
uvicorn (still: kill python + purge __pycache__ before any live run).

Usage (from project root):
    python scripts/verify_inventory_data_quality_live.py
"""

import asyncio
import io
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.modules.projects_inventory.domain import (  # noqa: E402
    CONTRACT_CANCEL_STATE,
    CONTRACT_MODEL,
    CONTRACT_STATE_FIELD,
    CONTRACT_UNIT_FIELD,
    SOLD_STATES,
    UNIT_AMOUNT_FIELD,
    UNIT_MODEL,
)
from backend.modules.projects_inventory.services import cache as _cache  # noqa: E402
from backend.modules.projects_inventory.services.data_quality_service import (  # noqa: E402
    get_data_quality_overview,
)
from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient  # noqa: E402

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_SEP = "=" * 100
_SEP2 = "-" * 100
_PAGE = 5000
_CHUNK = 200

# Sanity gate — the discovery's locked portfolio counts (re-derived 2026-06-19).
_EXPECT = {"no_contract": 5, "broken_hierarchy": 8, "no_list_price": 0}

# Authoritative chain links, canonical order (first break a unit has names its defect).
# (defect_type, unit child m2o, parent MODEL, parent's own upward m2o, unit field to equal).
_CHAIN = [
    ("phase_project", "phase_id", "rs.structure.phase", "project_id", "project_id"),
    ("zone_phase", "zone_id", "rs.structure.zone", "phase_id", "phase_id"),
    ("building_zone", "building_id", "rs.structure.building", "zone_id", "zone_id"),
]


def _ok(flag: bool) -> str:
    return "PASS" if flag else "**FAIL**"


def _num(v) -> float:
    return float(v) if isinstance(v, (int, float)) else 0.0


def _m2o_id(v):
    if isinstance(v, (list, tuple)) and len(v) == 2:
        return int(v[0])
    return None


def _proj_name(v) -> str:
    if isinstance(v, (list, tuple)) and len(v) == 2:
        return str(v[1])
    return "—"


async def _fetch_all_units(client) -> list[dict]:
    rows, offset = [], 0
    fields = ["id", "code", "state", "project_id", "phase_id", "zone_id",
              "building_id", UNIT_AMOUNT_FIELD]
    while True:
        page = await client.execute_kw(
            UNIT_MODEL, "search_read", args=[[]],
            kwargs={"fields": fields, "order": "id", "limit": _PAGE, "offset": offset},
        )
        rows.extend(page)
        if len(page) < _PAGE:
            break
        offset += _PAGE
    return rows


async def _fetch_parent_map(client, model: str, parent_field: str) -> dict:
    rows = await client.execute_kw(
        model, "search_read", args=[[]],
        kwargs={"fields": ["id", parent_field], "order": "id"},
    )
    return {int(r["id"]): _m2o_id(r.get(parent_field)) for r in rows}


async def _odoo_flagged(client) -> dict[str, list[dict]]:
    """Independent recomputation of A/B/C flagged units, straight from Odoo. Returns
    {check_key: [ {id, code, project} ... ]} with the SAME first-break-wins B logic."""
    units = await _fetch_all_units(client)
    sold = [u for u in units if u["state"] in SOLD_STATES]
    sold_ids = sorted(u["id"] for u in sold)

    # A — non-cancel contract coverage over sold units.
    covered: set[int] = set()
    for i in range(0, len(sold_ids), _CHUNK):
        chunk = sold_ids[i:i + _CHUNK]
        rows = await client.execute_kw(
            CONTRACT_MODEL, "search_read",
            args=[[(CONTRACT_UNIT_FIELD, "in", chunk)]],
            kwargs={"fields": [CONTRACT_UNIT_FIELD, CONTRACT_STATE_FIELD]},
        )
        for ct in rows:
            if ct.get(CONTRACT_STATE_FIELD) == CONTRACT_CANCEL_STATE:
                continue
            uid = _m2o_id(ct.get(CONTRACT_UNIT_FIELD))
            if uid is not None:
                covered.add(uid)
    flagged_a = [u for u in sold if u["id"] not in covered]

    # B — authoritative parent-record chain (first break wins).
    pmaps = {}
    for defect_type, _cf, model, pfield, _uf in _CHAIN:
        pmaps[defect_type] = await _fetch_parent_map(client, model, pfield)
    flagged_b: list[dict] = []
    for u in units:
        for defect_type, child_field, _model, _pfield, unit_field in _CHAIN:
            cid = _m2o_id(u.get(child_field))
            claimed = _m2o_id(u.get(unit_field))
            if cid is None:
                flagged_b.append(u)
                break
            actual = pmaps[defect_type].get(cid, "MISSING")
            if actual != claimed:
                flagged_b.append(u)
                break

    # C — sold units with no list price.
    flagged_c = [u for u in sold if not _num(u.get(UNIT_AMOUNT_FIELD))]

    def _shape(rows):
        return [{"id": u["id"], "code": u.get("code") or "",
                 "project": _proj_name(u.get("project_id"))} for u in rows]

    return {
        "no_contract": _shape(flagged_a),
        "broken_hierarchy": _shape(flagged_b),
        "no_list_price": _shape(flagged_c),
    }, units, sold


def _index(items: list[dict], id_key: str, proj_key: str):
    """(set of ids, set of codes, Counter by project) for a flagged list."""
    ids = {it[id_key] for it in items}
    codes = {it["code"] for it in items}
    by_proj = Counter(it[proj_key] for it in items)
    return ids, codes, by_proj


_CHECK_TITLES = {
    "no_contract": "A — sold unit without a contract",
    "broken_hierarchy": "B — broken hierarchy chain",
    "no_list_price": "C — sold unit without a list price",
}


def _compare_check(key: str, mod_items: list[dict], odoo_items: list[dict]) -> int:
    fails = 0
    m_ids, m_codes, m_proj = _index(mod_items, "unit_id", "project_name")
    o_ids, o_codes, o_proj = _index(odoo_items, "id", "project")

    print(_SEP2)
    print(f"  CHECK {_CHECK_TITLES[key]}")
    print(_SEP2)

    cnt_ok = len(mod_items) == len(odoo_items)
    ids_ok = m_ids == o_ids
    codes_ok = m_codes == o_codes
    proj_ok = m_proj == o_proj
    for label, good in (("count", cnt_ok), ("id set", ids_ok),
                        ("code set", codes_ok), ("per-project counts", proj_ok)):
        fails += 0 if good else 1
        print(f"    {label:<22} {_ok(good)}")

    print(f"    MODULE count={len(mod_items):>3}   ODOO count={len(odoo_items):>3}")
    print(f"    MODULE codes : {sorted(m_codes)}")
    print(f"    ODOO   codes : {sorted(o_codes)}")
    if not ids_ok:
        print(f"    ID DIFF  module-only={sorted(m_ids - o_ids)}  odoo-only={sorted(o_ids - m_ids)}")
    # Per-project breakdown (combined view: every project that appears in either side).
    projects = sorted(set(m_proj) | set(o_proj))
    if projects:
        print("    per project (MODULE | ODOO):")
        for p in projects:
            pg = m_proj.get(p, 0) == o_proj.get(p, 0)
            fails += 0 if pg else 1
            print(f"      {p:<22} {m_proj.get(p, 0):>3} | {o_proj.get(p, 0):>3}  {_ok(pg)}")
    return fails


async def main():
    print(_SEP)
    print("  INVENTORY DATA QUALITY — IDENTITY-EQUAL LIVE VERIFY (READ-ONLY, $0)")
    print(f"  ALLOWED_METHODS : {sorted(ALLOWED_METHODS)}")
    print(f"  SOLD states     : {sorted(SOLD_STATES)}")
    print(f"  Sanity gate     : A=={_EXPECT['no_contract']}  B=={_EXPECT['broken_hierarchy']}  "
          f"C=={_EXPECT['no_list_price']}")
    print(_SEP)

    fail = 0
    _cache.clear()

    async with OdooClient() as client:
        result = await get_data_quality_overview(client=client)
        mod_checks = {c["key"]: c["items"] for c in result["checks"]}

        odoo_flagged, units, sold = await _odoo_flagged(client)

        # ── Per-check identity (combined + per project) ──────────────────────────
        for key in ("no_contract", "broken_hierarchy", "no_list_price"):
            fail += _compare_check(key, mod_checks.get(key, []), odoo_flagged[key])
            print()

        # ── total_issues == Σ per-check counts (both sides) ──────────────────────
        print(_SEP2)
        print("  TOTALS")
        print(_SEP2)
        mod_total = result["total_issues"]
        mod_sum = sum(len(v) for v in mod_checks.values())
        odoo_sum = sum(len(v) for v in odoo_flagged.values())
        t_ok = mod_total == mod_sum == odoo_sum
        fail += 0 if t_ok else 1
        print(f"    total_issues MODULE={mod_total}  Σmodule={mod_sum}  Σodoo={odoo_sum}  {_ok(t_ok)}")
        print()

        # ── TRIPLE-AGREEMENT — independent search_count / read_group ─────────────
        print(_SEP2)
        print("  TRIPLE-CHECK — independent search_count / read_group vs MODULE")
        print(_SEP2)
        total_sc = await client.execute_kw(UNIT_MODEL, "search_count", args=[[]])
        sold_sc = await client.execute_kw(
            UNIT_MODEL, "search_count", args=[[("state", "in", sorted(SOLD_STATES))]])
        unpriced_sold_sc = await client.execute_kw(
            UNIT_MODEL, "search_count",
            args=[["&", ("state", "in", sorted(SOLD_STATES)),
                   "|", (UNIT_AMOUNT_FIELD, "=", 0), (UNIT_AMOUNT_FIELD, "=", False)]])
        sold_ids = sorted(u["id"] for u in sold)
        cov_groups = await client.execute_kw(
            CONTRACT_MODEL, "read_group",
            args=[["&", (CONTRACT_UNIT_FIELD, "in", sold_ids),
                   (CONTRACT_STATE_FIELD, "!=", CONTRACT_CANCEL_STATE)],
                  [CONTRACT_UNIT_FIELD], [CONTRACT_UNIT_FIELD]])
        distinct_covered = len(cov_groups)
        independent_a = len(sold) - distinct_covered

        tri = [
            ("total units (portfolio)", len(units), total_sc),
            ("sold units (portfolio)", len(sold), sold_sc),
            ("Check C — sold, no list price", len(odoo_flagged["no_list_price"]), unpriced_sold_sc),
            ("Check A — sold, no contract", len(odoo_flagged["no_contract"]), independent_a),
        ]
        for label, py, sc in tri:
            good = py == sc
            fail += 0 if good else 1
            print(f"    {label:<34} python={py:>6,}  independent={sc:>6,}  {_ok(good)}")

        # Per-project total units (independent search_count per live project id).
        proj_ids: dict[int, str] = {}
        for u in units:
            pid = _m2o_id(u.get("project_id"))
            if pid is not None:
                proj_ids.setdefault(pid, _proj_name(u.get("project_id")))
        py_proj = Counter(_proj_name(u.get("project_id")) for u in units)
        for pid, pname in sorted(proj_ids.items()):
            sc = await client.execute_kw(
                UNIT_MODEL, "search_count", args=[[("project_id", "=", pid)]])
            good = py_proj.get(pname, 0) == sc
            fail += 0 if good else 1
            print(f"    total units — {pname:<20} python={py_proj.get(pname, 0):>6,}  "
                  f"independent={sc:>6,}  {_ok(good)}")
        print()

        # ── SANITY GATE — locked discovery counts ────────────────────────────────
        print(_SEP2)
        print("  SANITY GATE — combined counts vs the locked discovery (A=5 / B=8 / C=0)")
        print(_SEP2)
        for key in ("no_contract", "broken_hierarchy", "no_list_price"):
            got = len(mod_checks.get(key, []))
            exp = _EXPECT[key]
            good = got == exp
            fail += 0 if good else 1
            print(f"    {key:<18} MODULE={got:>3}  expected={exp:>3}  {_ok(good)}")
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
