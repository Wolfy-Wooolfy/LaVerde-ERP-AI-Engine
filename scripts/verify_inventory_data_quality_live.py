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

  D — implausible_list_price  PRICED units (sold AND unsold, NC + Cassette) whose list
      price/m² is implausibly high vs comparable realized prices. Three deduped tiers
      (peer → type → impossible). The flagged {unit_id: signal} map, per-tier counts and
      the total are recomputed INDEPENDENTLY here (no service-private imports) and asserted
      identity-equal; the dry-run counts (total/per tier/studio-regime/unevaluable) print.

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
    CONTRACT_PAYMENT_TERM_FIELD,
    CONTRACT_PRICE_FIELD,
    CONTRACT_STATE_FIELD,
    CONTRACT_UNIT_FIELD,
    DQ_LIST_IMPOSSIBLE_K,
    DQ_LIST_TYPE_K,
    DQ_LIST_TYPE_SPREAD_MAX,
    OUTLIER_LIST_TRUST_K,
    OUTLIER_MIN_GROUP_SIZE,
    PAYMENT_TERM_DATE_FIELD,
    PAYMENT_TERM_MODEL,
    SOLD_STATES,
    UNIT_AMOUNT_FIELD,
    UNIT_AREA_FIELD,
    UNIT_METER_PRICE_FIELD,
    UNIT_MODEL,
    UNIT_TYPE_FIELD,
    VALUE_SCOPE_PROJECT_IDS,
    VINTAGE_BUCKET_YEARS,
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

# Sanity gate — the discovery's portfolio counts. B/C are stable; A drifts with live data
# (sold-no-contract is transient — units acquire/lose contracts). Re-derived 2026-06-23:
# A is now 0 (every one of the 1,400 sold units carries a non-cancel contract — confirmed
# independently below by the read_group triple-check; the 2026-06-19 snapshot was A=5).
_EXPECT = {"no_contract": 0, "broken_hierarchy": 8, "no_list_price": 0}

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


def _c2(v: float) -> float:
    return round(v, 2)


def _quantile(sorted_vals, q):
    """Independent reimplementation of the inclusive linear quantile (same math the
    service uses) — deterministic on identical sorted inputs."""
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    pos = (n - 1) * q
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def _median(vals):
    return _quantile(sorted(vals), 0.5)


async def _odoo_check_d(client) -> dict:
    """INDEPENDENT end-to-end recompute of Check D (implausible list price/m²), straight
    from rs.structure.unit + rs.contract + rs.payment.term — does NOT import the service's
    private functions. Returns {unit_id: signal} flag map + per-tier counts + the
    evaluated/unevaluable/studio diagnostics for identity comparison with the module."""
    # Scope units (NC + Cassette), priced or not — Check D evaluates priced sold + unsold.
    units = await client.execute_kw(
        UNIT_MODEL, "search_read",
        args=[[("project_id", "in", list(VALUE_SCOPE_PROJECT_IDS))]],
        kwargs={"fields": ["id", "code", "state", "project_id", "zone_id", UNIT_TYPE_FIELD,
                           UNIT_AMOUNT_FIELD, UNIT_AREA_FIELD, UNIT_METER_PRICE_FIELD],
                "order": "id"},
    )
    sold_ids = sorted(u["id"] for u in units if u["state"] in SOLD_STATES)

    # Realized value + payment-term ids over the sold units' non-cancel contracts.
    realized: dict[int, float] = {}
    term_ids: dict[int, set] = {}
    for i in range(0, len(sold_ids), _CHUNK):
        chunk = sold_ids[i:i + _CHUNK]
        rows = await client.execute_kw(
            CONTRACT_MODEL, "search_read",
            args=[[(CONTRACT_UNIT_FIELD, "in", chunk)]],
            kwargs={"fields": [CONTRACT_UNIT_FIELD, CONTRACT_PRICE_FIELD,
                               CONTRACT_STATE_FIELD, CONTRACT_PAYMENT_TERM_FIELD]},
        )
        for ct in rows:
            if ct.get(CONTRACT_STATE_FIELD) == CONTRACT_CANCEL_STATE:
                continue
            uid = _m2o_id(ct.get(CONTRACT_UNIT_FIELD))
            if uid is None:
                continue
            realized[uid] = realized.get(uid, 0.0) + _num(ct.get(CONTRACT_PRICE_FIELD))
            ptid = _m2o_id(ct.get(CONTRACT_PAYMENT_TERM_FIELD))
            if ptid is not None:
                term_ids.setdefault(uid, set()).add(ptid)

    referenced = sorted({t for ts in term_ids.values() for t in ts})
    term_dates: dict[int, str] = {}
    for i in range(0, len(referenced), _CHUNK):
        chunk = referenced[i:i + _CHUNK]
        rows = await client.execute_kw(
            PAYMENT_TERM_MODEL, "search_read",
            args=[[("id", "in", chunk)]],
            kwargs={"fields": ["id", PAYMENT_TERM_DATE_FIELD]},
        )
        for r in rows:
            d = r.get(PAYMENT_TERM_DATE_FIELD)
            if d:
                term_dates[int(r["id"])] = str(d)[:10]

    def _bucket(year):
        return (year // VINTAGE_BUCKET_YEARS) * VINTAGE_BUCKET_YEARS

    def _sale_date(uid):
        dates = [term_dates[t] for t in term_ids.get(uid, set()) if t in term_dates]
        return min(dates) if dates else None

    # Sold realized population → realized_pm2 (+ vintage where resolvable).
    sold_pop = []
    for u in units:
        if u["state"] not in SOLD_STATES:
            continue
        area = _num(u.get(UNIT_AREA_FIELD))
        if area <= 0:
            continue
        uid = u["id"]
        if uid not in realized:
            continue
        sd = _sale_date(uid)
        sold_pop.append({
            "zone_id": _m2o_id(u.get("zone_id")),
            "type_id": _m2o_id(u.get(UNIT_TYPE_FIELD)),
            "bucket": _bucket(int(sd[:4])) if sd else None,
            "realized_pm2": _c2(_c2(realized[uid]) / area),
        })

    # Peer baseline (zone, type, vintage) — eligible groups' median realized_pm2.
    peer_vals = {}
    for m in sold_pop:
        if m["bucket"] is None:
            continue
        peer_vals.setdefault((m["zone_id"], m["type_id"], m["bucket"]), []).append(m["realized_pm2"])
    peer_median = {}
    for key, vals in peer_vals.items():
        if len(vals) >= OUTLIER_MIN_GROUP_SIZE:
            med = _median(vals)
            if med > 0:
                peer_median[key] = med

    # Type baseline — median, max, spread per type with >= MIN_GROUP_SIZE sold members.
    type_vals = {}
    for m in sold_pop:
        type_vals.setdefault(m["type_id"], []).append(m["realized_pm2"])
    type_baseline = {}
    for tid, vals in type_vals.items():
        if len(vals) >= OUTLIER_MIN_GROUP_SIZE:
            med = _median(vals)
            mx = max(vals)
            if med > 0:
                type_baseline[tid] = {"median": med, "max": mx, "spread": mx / med}

    flags: dict[int, str] = {}
    studio_units: set[int] = set()
    tier1 = tier2a = tier2b = 0
    evaluated = 0
    unevaluable = 0
    for u in units:
        area = _num(u.get(UNIT_AREA_FIELD))
        amount = _num(u.get(UNIT_AMOUNT_FIELD))
        if amount <= 0 or area <= 0:
            continue
        evaluated += 1
        uid = u["id"]
        is_sold = u["state"] in SOLD_STATES
        zone_id = _m2o_id(u.get("zone_id"))
        type_id = _m2o_id(u.get(UNIT_TYPE_FIELD))
        type_name = _proj_name(u.get(UNIT_TYPE_FIELD))   # reuse m2o-name helper
        list_pm2 = _c2(amount / area)

        peer_anchor = None
        if is_sold and uid in realized:
            sd = _sale_date(uid)
            if sd is not None:
                peer_anchor = peer_median.get((zone_id, type_id, _bucket(int(sd[:4]))))

        tb = type_baseline.get(type_id)
        if peer_anchor is None and tb is None:
            unevaluable += 1
            continue

        fires_t1 = peer_anchor is not None and list_pm2 > OUTLIER_LIST_TRUST_K * peer_anchor
        fires_t2a = (tb is not None and tb["spread"] < DQ_LIST_TYPE_SPREAD_MAX
                     and list_pm2 > DQ_LIST_TYPE_K * tb["median"])
        fires_t2b = tb is not None and list_pm2 > DQ_LIST_IMPOSSIBLE_K * tb["max"]
        if not (fires_t1 or fires_t2a or fires_t2b):
            continue

        if fires_t1:
            flags[uid] = "peer"; tier1 += 1
        elif fires_t2a:
            flags[uid] = "type"; tier2a += 1
        else:
            flags[uid] = "impossible"; tier2b += 1
        if "studio" in (type_name or "").lower():
            studio_units.add(uid)

    return {
        "flags": flags,
        "tier1": tier1, "tier2a": tier2a, "tier2b": tier2b,
        "evaluated": evaluated, "unevaluable": unevaluable,
        "studio": len(studio_units),
        "scope_unit_count": len(units),
    }


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
        print("  SANITY GATE — combined counts vs the discovery (A=0 / B=8 / C=0, 2026-06-23)")
        print(_SEP2)
        for key in ("no_contract", "broken_hierarchy", "no_list_price"):
            got = len(mod_checks.get(key, []))
            exp = _EXPECT[key]
            good = got == exp
            fail += 0 if good else 1
            print(f"    {key:<18} MODULE={got:>3}  expected={exp:>3}  {_ok(good)}")
        print()

        # ── CHECK D — implausible list price/m² (NC + Cassette), identity-equal ───
        print(_SEP2)
        print("  CHECK D — implausible list price/m² (NC + Cassette) — IDENTITY-EQUAL")
        print(f"  Thresholds : LIST_TRUST_K={OUTLIER_LIST_TRUST_K} TYPE_K={DQ_LIST_TYPE_K} "
              f"TYPE_SPREAD_MAX={DQ_LIST_TYPE_SPREAD_MAX} IMPOSSIBLE_K={DQ_LIST_IMPOSSIBLE_K} "
              f"MIN_GROUP_SIZE={OUTLIER_MIN_GROUP_SIZE}")
        print(_SEP2)
        d = result["check_d"]
        mod_d_flags = {r["unit_id"]: r["signal"] for r in d["items"]}
        odoo_d = await _odoo_check_d(client)

        d_studio = sum(1 for r in d["items"] if "studio" in (r["unit_type_name"] or "").lower())
        d_sold = sum(1 for r in d["items"] if r["state"] == "sold")
        d_unsold = sum(1 for r in d["items"] if r["state"] == "unsold")

        checks_d = [
            ("flagged {unit_id: signal} map equal", mod_d_flags == odoo_d["flags"]),
            ("count == |independent flags|", d["count"] == len(odoo_d["flags"])),
            ("count == len(items)", d["count"] == len(d["items"])),
            ("Tier 1 (peer) count match", d["tier1_count"] == odoo_d["tier1"]),
            ("Tier 2a (type) count match", d["tier2a_count"] == odoo_d["tier2a"]),
            ("Tier 2b (impossible) count match", d["tier2b_count"] == odoo_d["tier2b"]),
            ("tier1+tier2a+tier2b == count",
             d["tier1_count"] + d["tier2a_count"] + d["tier2b_count"] == d["count"]),
            ("evaluated_count match", d["evaluated_count"] == odoo_d["evaluated"]),
            ("unevaluable_count match", d["unevaluable_count"] == odoo_d["unevaluable"]),
            ("studio-regime count match", d_studio == odoo_d["studio"]),
        ]
        for label, good in checks_d:
            fail += 0 if good else 1
            print(f"    {label:<40} {_ok(good)}")
        if mod_d_flags != odoo_d["flags"]:
            only_mod = {k: mod_d_flags[k] for k in set(mod_d_flags) - set(odoo_d["flags"])}
            only_odoo = {k: odoo_d["flags"][k] for k in set(odoo_d["flags"]) - set(mod_d_flags)}
            diff_sig = {k: (mod_d_flags[k], odoo_d["flags"][k])
                        for k in set(mod_d_flags) & set(odoo_d["flags"])
                        if mod_d_flags[k] != odoo_d["flags"][k]}
            print(f"    FLAG DIFF  module-only={only_mod}  odoo-only={only_odoo}  signal-diff={diff_sig}")
        print()

        # ── CHECK D — dry-run summary (the counts to report) ─────────────────────
        print(_SEP2)
        print("  CHECK D — DRY-RUN counts (at spec thresholds)")
        print(_SEP2)
        print(f"    scope priced units evaluated           : {d['evaluated_count']:>6,}")
        print(f"    TOTAL flagged                          : {d['count']:>6,}")
        print(f"      Tier 1 (peer, sold)                  : {d['tier1_count']:>6,}")
        print(f"      Tier 2a (type, low-spread)           : {d['tier2a_count']:>6,}")
        print(f"      Tier 2b (impossible / area error)    : {d['tier2b_count']:>6,}")
        print(f"      sold {d_sold} / unsold {d_unsold}")
        print(f"    studio-regime (HS-Studio) flagged      : {d_studio:>6,}")
        print(f"    unevaluable priced units (footnote)    : {d['unevaluable_count']:>6,}")
        print(f"    Section A sample (top 5 by ratio):")
        for r in d["items"][:5]:
            print(f"      {r['code']:<16} {r['project_name']:<12} {r['unit_type_name']:<18} "
                  f"{r['state']:<6} list/m²={r['list_pm2']:>10,.0f}  meter={r['meter_price']:>10,.0f}  "
                  f"anchor={r['anchor_realized_pm2']:>10,.0f}  ×{r['ratio']:>6,.1f}  {r['signal']}")
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
