"""
scripts/discover_vintage_sensitivity_check_d.py — DISCOVERY ONLY (READ-ONLY, $0 AI).

Stress-tests the time-control assumption behind Check D (Inventory Data Quality, Projects/
Inventory). Tier 1 (peer) is already vintage-aware. Tier 2a (type baseline) and Tier 2b
(impossible) currently compare a unit's LIST price/m² against the unit-type's realized
price/m² across ALL years 2018-2025 (NOT time-controlled). This script measures, WITHOUT
changing any production logic, what happens if Tier 2a/2b instead compare against a per-type
baseline restricted to each unit's OWN vintage bucket (the same 2-year windowing Slice 2.5
Section A uses, applied at TYPE granularity instead of zone×type).

Method discipline (mirrors scripts/verify_inventory_data_quality_live.py):
  - READ-ONLY. Only search_read is issued. ALLOWED_METHODS untouched. No writes, ever.
  - No OpenAI / no AI calls. AI cost = $0.00.
  - Reuses the EXACT production expressions (price/m², bucketing, thresholds) so Part 2
    reproduces the live 84 / T1=22 / T2a=55 / T2b=7 exactly. It imports domain CONSTANTS
    only (read-only config) and recomputes everything else independently from Odoo.
  - Cross-checks Part 2 against the live get_data_quality_overview() module result too.

Deliverables (printed in order): PART 0 source confirmation + scope counts; PART 1 yearly
price/m² snapshot (frozen-vs-recomputed test); PART 2 all-history baseline reproduction
(self-check gate); PART 3 vintage-controlled Tier 2a/2b (ENTERED / LEFT / UNEVALUABLE);
PART 4 sensitivity (every flip); PART 5 stability verdict.

Usage (from project root):
    python scripts/discover_vintage_sensitivity_check_d.py
"""

import asyncio
import io
import sys
from collections import Counter, defaultdict
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

# The production baseline this run MUST reproduce (the self-check gate in Part 2).
_EXPECT_TOTAL, _EXPECT_T1, _EXPECT_T2A, _EXPECT_T2B = 84, 22, 55, 7


# ── Shared helpers (identical math to the service + the live verify) ───────────


def _num(v) -> float:
    return float(v) if isinstance(v, (int, float)) else 0.0


def _m2o_id(v):
    if isinstance(v, (list, tuple)) and len(v) == 2:
        return int(v[0])
    return None


def _m2o_name(v) -> str:
    if isinstance(v, (list, tuple)) and len(v) == 2:
        return str(v[1])
    return "—"


def _c2(v: float) -> float:
    return round(v, 2)


def _vintage_bucket(year: int) -> int:
    """2-year bucket floor (2022 & 2023 -> 2022) — the SAME bucketing Slice 2.5 uses."""
    return (year // VINTAGE_BUCKET_YEARS) * VINTAGE_BUCKET_YEARS


def _bucket_label(bucket) -> str:
    if bucket is None:
        return "—"
    return f"{bucket}-{bucket + VINTAGE_BUCKET_YEARS - 1}"


def _quantile(sorted_vals, q):
    """Inclusive linear quantile — identical to the service + live verify."""
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    pos = (n - 1) * q
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def _median(vals):
    return _quantile(sorted(vals), 0.5)


# ── Live read (read-only) — units, contracts, payment-term dates ───────────────


async def _fetch_scope_units(client) -> list[dict]:
    """All NC + Cassette units (priced or not, sold or not), in id pages."""
    rows, offset = [], 0
    fields = ["id", "code", "state", "project_id", "zone_id", UNIT_TYPE_FIELD,
              UNIT_AMOUNT_FIELD, UNIT_AREA_FIELD, UNIT_METER_PRICE_FIELD]
    while True:
        page = await client.execute_kw(
            UNIT_MODEL, "search_read",
            args=[[("project_id", "in", list(VALUE_SCOPE_PROJECT_IDS))]],
            kwargs={"fields": fields, "order": "id", "limit": _PAGE, "offset": offset},
        )
        rows.extend(page)
        if len(page) < _PAGE:
            break
        offset += _PAGE
    return rows


async def _fetch_realized_and_terms(client, sold_ids):
    """Σ sales_price + payment_term_ids per sold unit, over NON-cancel contracts."""
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
    return realized, term_ids


async def _fetch_term_dates(client, term_ids):
    referenced = sorted({t for ts in term_ids.values() for t in ts})
    out: dict[int, str] = {}
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
                out[int(r["id"])] = str(d)[:10]
    return out


# ── Baselines & evaluation (ported verbatim from _check_d / live verify) ───────


def _build_sold_pop(units, realized, term_ids, term_dates):
    """Sold + area>0 + realized population. Each carries zone/type/bucket/year/realized_pm2.
    Identical derivation to _check_d step (1)."""
    def _sale_date(uid):
        dates = [term_dates[t] for t in term_ids.get(uid, set()) if t in term_dates]
        return min(dates) if dates else None

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
        year = int(sd[:4]) if sd else None
        sold_pop.append({
            "unit_id": uid,
            "zone_id": _m2o_id(u.get("zone_id")),
            "type_id": _m2o_id(u.get(UNIT_TYPE_FIELD)),
            "year": year,
            "bucket": _vintage_bucket(year) if year is not None else None,
            "realized_pm2": _c2(_c2(realized[uid]) / area),
        })
    return sold_pop


def _peer_median(sold_pop):
    """Tier-1 peer baseline (zone, type, bucket) — eligible groups' median realized_pm2.
    UNCHANGED from production; used by both the all-history and vintage runs."""
    peer_vals = defaultdict(list)
    for m in sold_pop:
        if m["bucket"] is None:
            continue
        peer_vals[(m["zone_id"], m["type_id"], m["bucket"])].append(m["realized_pm2"])
    out = {}
    for key, vals in peer_vals.items():
        if len(vals) >= OUTLIER_MIN_GROUP_SIZE:
            med = _median(vals)
            if med > 0:
                out[key] = med
    return out


def _type_baseline_allhist(sold_pop):
    """All-history type baseline (per type_id, all years) — median/max/spread. Production."""
    type_vals = defaultdict(list)
    for m in sold_pop:
        type_vals[m["type_id"]].append(m["realized_pm2"])
    out = {}
    for tid, vals in type_vals.items():
        if len(vals) >= OUTLIER_MIN_GROUP_SIZE:
            med = _median(vals)
            mx = max(vals)
            if med > 0:
                out[tid] = {"median": med, "max": mx, "spread": mx / med, "n": len(vals)}
    return out


def _type_baseline_vintage(sold_pop):
    """Per-(type_id, vintage_bucket) baseline — median/max/spread, >= MIN_GROUP_SIZE sold of
    that type IN that bucket. Also returns the member count for every (type, bucket) cell so
    a thin bucket can be reported with its actual size."""
    cell_vals = defaultdict(list)
    for m in sold_pop:
        if m["bucket"] is None:
            continue   # sold-but-unresolvable date: cannot place in a bucket
        cell_vals[(m["type_id"], m["bucket"])].append(m["realized_pm2"])
    baseline = {}
    counts = {}
    for key, vals in cell_vals.items():
        counts[key] = len(vals)
        if len(vals) >= OUTLIER_MIN_GROUP_SIZE:
            med = _median(vals)
            mx = max(vals)
            if med > 0:
                baseline[key] = {"median": med, "max": mx, "spread": mx / med, "n": len(vals)}
    return baseline, counts


def _evaluate(list_pm2, peer_anchor, tb):
    """The EXACT production tier logic (data_quality_service._check_d step 4). `tb` is the
    type baseline dict (all-history OR vintage cell) or None. peer_anchor is Tier-1's anchor.
    Returns (decision, anchor) where decision ∈ {peer,type,impossible,unflagged,unevaluable}."""
    if peer_anchor is None and tb is None:
        return "unevaluable", None
    fires_t1 = peer_anchor is not None and list_pm2 > OUTLIER_LIST_TRUST_K * peer_anchor
    fires_t2a = (
        tb is not None
        and tb["spread"] < DQ_LIST_TYPE_SPREAD_MAX
        and list_pm2 > DQ_LIST_TYPE_K * tb["median"]
    )
    fires_t2b = tb is not None and list_pm2 > DQ_LIST_IMPOSSIBLE_K * tb["max"]
    if fires_t1:
        return "peer", peer_anchor
    if fires_t2a:
        return "type", tb["median"]
    if fires_t2b:
        return "impossible", tb["max"]
    return "unflagged", None


def _ok(flag: bool) -> str:
    return "PASS" if flag else "**FAIL**"


# ── Main report ────────────────────────────────────────────────────────────────


async def main():
    print(_SEP)
    print("  CHECK D — VINTAGE SENSITIVITY DISCOVERY (READ-ONLY, $0 AI)")
    print(f"  ALLOWED_METHODS : {sorted(ALLOWED_METHODS)}")
    print(f"  Scope projects  : {list(VALUE_SCOPE_PROJECT_IDS)} (New Capital + Cassette; La Puerta excluded)")
    print(f"  Thresholds      : LIST_TRUST_K={OUTLIER_LIST_TRUST_K} TYPE_K={DQ_LIST_TYPE_K} "
          f"TYPE_SPREAD_MAX={DQ_LIST_TYPE_SPREAD_MAX} IMPOSSIBLE_K={DQ_LIST_IMPOSSIBLE_K} "
          f"MIN_GROUP_SIZE={OUTLIER_MIN_GROUP_SIZE} VINTAGE_BUCKET_YEARS={VINTAGE_BUCKET_YEARS}")
    print(_SEP)

    _cache.clear()

    async with OdooClient() as client:
        # Live module result (authoritative production numbers for the Part 2 cross-check).
        module_d = (await get_data_quality_overview(client=client))["check_d"]

        # Independent fresh live read for everything below.
        units = await _fetch_scope_units(client)
        type_name = {u["id"]: _m2o_name(u.get(UNIT_TYPE_FIELD)) for u in units}
        proj_name = {u["id"]: _m2o_name(u.get("project_id")) for u in units}
        sold_ids = sorted(u["id"] for u in units if u["state"] in SOLD_STATES)
        realized, term_ids = await _fetch_realized_and_terms(client, sold_ids)
        term_dates = await _fetch_term_dates(client, term_ids)

    sold_pop = _build_sold_pop(units, realized, term_ids, term_dates)

    def _sale_date(uid):
        dates = [term_dates[t] for t in term_ids.get(uid, set()) if t in term_dates]
        return min(dates) if dates else None

    # ── PART 0 — SOURCE CONFIRMATION ─────────────────────────────────────────
    print("\n" + _SEP)
    print("  PART 0 — SOURCE CONFIRMATION")
    print(_SEP)
    print("  0(a) actual price/m² and realized-value source (VERBATIM from the service):")
    print("       realized sale value  : Σ rs.contract.sales_price over NON-cancel contracts")
    print("                              realized[uid] = realized.get(uid,0.0) + _num(ct.get(CONTRACT_PRICE_FIELD))")
    print("                              CONTRACT_PRICE_FIELD = \"sales_price\"")
    print("       actual price/m²      : realized_pm2 = _c2(_c2(realized[uid]) / area)   (area = total_area)")
    print("       list price/m² tested : list_pm2     = _c2(amount / area)               (amount = unit list price)")
    print()
    print("  0(b) vintage period bucketing (VERBATIM from Slice 2.5 Section A):")
    print("       def _vintage_bucket(year): return (year // VINTAGE_BUCKET_YEARS) * VINTAGE_BUCKET_YEARS")
    print("       VINTAGE_BUCKET_YEARS = 2   (2022 & 2023 -> 2022, label '2022-2023')")
    print("       sale year = int(sale_date[:4]); sale_date = earliest contract_date over the")
    print("       unit's non-cancel payment terms. Section A peer key = (zone_id, unit_type_id,")
    print("       vintage_bucket); here applied at TYPE granularity -> (unit_type_id, vintage_bucket).")
    print()

    priced = [u for u in units if _num(u.get(UNIT_AMOUNT_FIELD)) > 0 and _num(u.get(UNIT_AREA_FIELD)) > 0]
    priced_sold = [u for u in priced if u["state"] in SOLD_STATES]
    priced_unsold = [u for u in priced if u["state"] not in SOLD_STATES]
    by_proj = Counter(proj_name[u["id"]] for u in priced)
    print("  0(c) scope counts (priced = amount>0 AND total_area>0):")
    print(f"       total scope units (NC+Cassette)      : {len(units):>6,}")
    print(f"       priced units                         : {len(priced):>6,}")
    print(f"         sold                               : {len(priced_sold):>6,}")
    print(f"         unsold (available/reserved)        : {len(priced_unsold):>6,}")
    for p, n in sorted(by_proj.items()):
        print(f"         priced in {p:<22}: {n:>6,}")
    print(f"       sold realized population (baselines) : {len(sold_pop):>6,}")

    # ── PART 1 — yearly price/m² snapshot ────────────────────────────────────
    print("\n" + _SEP)
    print("  PART 1 — SNAPSHOT CONFIRMATION (frozen-vs-recomputed test)")
    print(_SEP)
    print("  Realized (actual) price/m² of SOLD units, grouped by contract_date YEAR:")
    print(f"    {'year':<8}{'count':>8}{'min':>14}{'median(p50)':>16}{'max':>16}")
    by_year = defaultdict(list)
    no_year = 0
    for m in sold_pop:
        if m["year"] is None:
            no_year += 1
            continue
        by_year[m["year"]].append(m["realized_pm2"])
    year_medians = {}
    for y in sorted(by_year):
        vals = by_year[y]
        year_medians[y] = _median(vals)
        print(f"    {y:<8}{len(vals):>8}{min(vals):>14,.0f}{_median(vals):>16,.0f}{max(vals):>16,.0f}")
    if no_year:
        print(f"    (sold-with-realized but unresolvable sale date: {no_year})")
    yrs = sorted(year_medians)
    escalates = False
    if len(yrs) >= 2:
        first, last = year_medians[yrs[0]], year_medians[yrs[-1]]
        ratio = (last / first) if first > 0 else 0.0
        escalates = ratio >= 2.0
        print()
        print(f"  median {yrs[0]} = {first:,.0f}/m²  ->  median {yrs[-1]} = {last:,.0f}/m²   "
              f"(×{ratio:,.1f} across the span)")
        print(f"  VERDICT: {'ESCALATES across years => sale value is stored HISTORICALLY (frozen) => vintage bucketing is VALID.' if escalates else 'FLAT at today level => values look RECOMPUTED => FLAG and do not proceed.'}")
    if not escalates:
        print("\n  *** Price/m² does NOT escalate — per the spec, stopping before Part 3. ***")
        return 1

    # ── PART 2 — all-history baseline reproduction (self-check) ──────────────
    print("\n" + _SEP)
    print("  PART 2 — BASELINE REPRODUCTION (all-history type baselines, as production)")
    print(_SEP)
    peer_median = _peer_median(sold_pop)
    tb_allhist = _type_baseline_allhist(sold_pop)

    allhist = {}   # uid -> record
    for u in priced:
        uid = u["id"]
        area = _num(u.get(UNIT_AREA_FIELD))
        amount = _num(u.get(UNIT_AMOUNT_FIELD))
        is_sold = u["state"] in SOLD_STATES
        zone_id = _m2o_id(u.get("zone_id"))
        tid = _m2o_id(u.get(UNIT_TYPE_FIELD))
        list_pm2 = _c2(amount / area)

        peer_anchor = None
        own_bucket = None
        if is_sold and uid in realized:
            sd = _sale_date(uid)
            if sd is not None:
                own_bucket = _vintage_bucket(int(sd[:4]))
                peer_anchor = peer_median.get((zone_id, tid, own_bucket))

        tb = tb_allhist.get(tid)
        decision, anchor = _evaluate(list_pm2, peer_anchor, tb)
        allhist[uid] = {
            "uid": uid, "code": u.get("code") or "", "project": proj_name[uid],
            "type_id": tid, "type_name": type_name[uid], "area": area,
            "list_pm2": list_pm2, "is_sold": is_sold, "own_bucket": own_bucket,
            "peer_anchor": peer_anchor, "tb": tb, "decision": decision, "anchor": anchor,
        }

    a_t1 = sum(1 for r in allhist.values() if r["decision"] == "peer")
    a_t2a = sum(1 for r in allhist.values() if r["decision"] == "type")
    a_t2b = sum(1 for r in allhist.values() if r["decision"] == "impossible")
    a_total = a_t1 + a_t2a + a_t2b

    print(f"    INDEPENDENT recompute : total={a_total}  T1(peer)={a_t1}  T2a(type)={a_t2a}  T2b(impossible)={a_t2b}")
    print(f"    LIVE module result    : total={module_d['count']}  T1={module_d['tier1_count']}  "
          f"T2a={module_d['tier2a_count']}  T2b={module_d['tier2b_count']}")
    print(f"    EXPECTED (production) : total={_EXPECT_TOTAL}  T1={_EXPECT_T1}  T2a={_EXPECT_T2A}  T2b={_EXPECT_T2B}")
    gate = (
        (a_total, a_t1, a_t2a, a_t2b) == (_EXPECT_TOTAL, _EXPECT_T1, _EXPECT_T2A, _EXPECT_T2B)
        and (module_d["count"], module_d["tier1_count"], module_d["tier2a_count"], module_d["tier2b_count"])
        == (_EXPECT_TOTAL, _EXPECT_T1, _EXPECT_T2A, _EXPECT_T2B)
    )
    print(f"    SELF-CHECK GATE       : {_ok(gate)}")
    if not gate:
        print("\n  *** Part 2 did NOT reproduce 84/22/55/7 — harness is wrong, downstream is meaningless. STOP. ***")
        return 1

    allhist_flagged = {uid for uid, r in allhist.items() if r["decision"] in ("peer", "type", "impossible")}

    # ── PART 3 — vintage-controlled Tier 2a & 2b ─────────────────────────────
    print("\n" + _SEP)
    print("  PART 3 — VINTAGE-CONTROLLED TIER 2a & 2b (per-type baseline in each unit's own bucket)")
    print(_SEP)
    tb_vintage, cell_counts = _type_baseline_vintage(sold_pop)

    vintage = {}
    for u in priced:
        uid = u["id"]
        a = allhist[uid]
        tid = a["type_id"]
        own_bucket = a["own_bucket"]      # sold->own bucket; unsold/no-date -> None
        peer_anchor = a["peer_anchor"]    # Tier 1 UNCHANGED

        tb_v = tb_vintage.get((tid, own_bucket)) if own_bucket is not None else None
        decision, anchor = _evaluate(a["list_pm2"], peer_anchor, tb_v)

        # Reason a Tier-2 baseline is unavailable under the vintage rule (for reporting).
        unevaluable_reason = None
        thin_n = None
        if tb_v is None:
            if own_bucket is None:
                unevaluable_reason = "no_own_vintage(unsold)" if not a["is_sold"] else "no_own_vintage(no_date)"
            else:
                thin_n = cell_counts.get((tid, own_bucket), 0)
                unevaluable_reason = f"thin_bucket({thin_n}<{OUTLIER_MIN_GROUP_SIZE})"

        vintage[uid] = {
            **a, "decision": decision, "anchor": anchor, "tb_v": tb_v,
            "unevaluable_reason": unevaluable_reason, "thin_n": thin_n,
        }

    v_t1 = sum(1 for r in vintage.values() if r["decision"] == "peer")
    v_t2a = sum(1 for r in vintage.values() if r["decision"] == "type")
    v_t2b = sum(1 for r in vintage.values() if r["decision"] == "impossible")
    v_total = v_t1 + v_t2a + v_t2b
    vintage_flagged = {uid for uid, r in vintage.items() if r["decision"] in ("peer", "type", "impossible")}

    print(f"    VINTAGE recompute : total={v_total}  T1(peer, unchanged)={v_t1}  "
          f"T2a(type)={v_t2a}  T2b(impossible)={v_t2b}")
    print(f"    (all-history was : total={a_total}  T1={a_t1}  T2a={a_t2a}  T2b={a_t2b})")
    print()

    entered = sorted(vintage_flagged - allhist_flagged)
    left = sorted(allhist_flagged - vintage_flagged)
    print(f"  ENTERED (flagged now, not under all-history) — {len(entered)}:")
    if not entered:
        print("      (none)")
    for uid in entered:
        r = vintage[uid]
        print(f"      uid={uid:<6} {r['code']:<16} {r['type_name']:<22} area={r['area']:>7,.0f}  "
              f"list/m²={r['list_pm2']:>10,.0f}  now={r['decision']:<11} bucket={_bucket_label(r['own_bucket'])}")

    print(f"\n  LEFT (flagged under all-history, not now) — {len(left)}:")
    if not left:
        print("      (none)")
    for uid in left:
        a = allhist[uid]
        v = vintage[uid]
        why = v["unevaluable_reason"] or f"now {v['decision']}"
        print(f"      uid={uid:<6} {a['code']:<16} {a['type_name']:<22} area={a['area']:>7,.0f}  "
              f"list/m²={a['list_pm2']:>10,.0f}  was={a['decision']:<11} now={v['decision']:<11} ({why})")

    unevaluable = sorted(uid for uid, r in vintage.items()
                         if r["decision"] == "unevaluable")
    # Of the UNEVALUABLE, split by reason for transparency.
    unev_thin = [uid for uid in unevaluable if vintage[uid]["thin_n"] is not None]
    unev_novintage = [uid for uid in unevaluable if vintage[uid]["thin_n"] is None]
    print(f"\n  UNEVALUABLE under the vintage rule (no peer anchor AND no vintage type baseline) — {len(unevaluable)}:")
    print(f"      reason split: thin_bucket(<5 sold of type)={len(unev_thin)}  "
          f"no_own_vintage(unsold/no-date)={len(unev_novintage)}")
    # Only enumerate those that were previously evaluable/flagged or are otherwise notable;
    # to honor the deliverable, list thin-bucket ones (the genuine '<5 sold of type' set).
    print(f"    thin-bucket units (uid, type, area, the bucket that was too thin) — {len(unev_thin)}:")
    for uid in sorted(unev_thin):
        r = vintage[uid]
        print(f"      uid={uid:<6} {r['code']:<16} {r['type_name']:<22} area={r['area']:>7,.0f}  "
              f"bucket={_bucket_label(r['own_bucket'])} had {r['thin_n']} sold of type")
    if not unev_thin:
        print("      (none)")

    # ── PART 4 — sensitivity (every flip) ────────────────────────────────────
    print("\n" + _SEP)
    print("  PART 4 — SENSITIVITY (every unit whose FLAG STATUS flips)")
    print(_SEP)

    def _flagged(d):
        return d in ("peer", "type", "impossible")

    def _tier_cut(tb):
        """The binding Tier-2 cut for a type baseline (or '—'): 3×median (T2a) / 5×max (T2b)."""
        if tb is None:
            return "—"
        return f"T2a 3×med={DQ_LIST_TYPE_K * tb['median']:,.0f} / T2b 5×max={DQ_LIST_IMPOSSIBLE_K * tb['max']:,.0f}"

    # A flag-status flip = flagged<->not-flagged, OR a tier change while still flagged.
    # (unflagged<->unevaluable is NOT a flag-status change — both are 'not flagged'.)
    flips = sorted(
        uid for uid in allhist
        if _flagged(allhist[uid]["decision"]) != _flagged(vintage[uid]["decision"])
        or (_flagged(allhist[uid]["decision"]) and _flagged(vintage[uid]["decision"])
            and allhist[uid]["decision"] != vintage[uid]["decision"])
    )
    print(f"  {len(flips)} unit(s) flip flag-status between all-history and vintage-controlled.")
    print("  (one line each: uid | type | area | list/m² | ALL-HIST decision + cut | VINTAGE decision + cut | bucket)\n")
    if not flips:
        print("      (no flips)")
    for uid in flips:
        a = allhist[uid]
        v = vintage[uid]
        peer_cut_a = f" peer 2×={OUTLIER_LIST_TRUST_K * a['peer_anchor']:,.0f}" if a["peer_anchor"] else ""
        print(f"  uid={uid:<6} {a['code']:<16} {a['type_name']:<22} area={a['area']:>7,.0f} "
              f"list/m²={a['list_pm2']:>11,.0f}")
        print(f"        ALL-HIST [{a['decision']:<11}] cut: {_tier_cut(a['tb'])}{peer_cut_a}")
        print(f"        VINTAGE  [{v['decision']:<11}] cut: {_tier_cut(v['tb_v'])}"
              f"   bucket={_bucket_label(v['own_bucket'])}"
              + (f"  [{v['unevaluable_reason']}]" if v["unevaluable_reason"] else ""))

    # ── PART 5 — stability verdict (numbers only) ────────────────────────────
    print("\n" + _SEP)
    print("  PART 5 — STABILITY VERDICT (numbers only)")
    print(_SEP)
    unchanged = sorted(uid for uid in allhist_flagged
                       if allhist[uid]["decision"] == vintage[uid]["decision"])
    print(f"  Of the original {len(allhist_flagged)} flagged units, UNCHANGED by time-control "
          f"(same tier, still flagged): {len(unchanged)}")
    # Tier breakdown of the unchanged set.
    u_by_tier = Counter(allhist[uid]["decision"] for uid in unchanged)
    print(f"    unchanged by tier: peer={u_by_tier.get('peer', 0)}  "
          f"type={u_by_tier.get('type', 0)}  impossible={u_by_tier.get('impossible', 0)}")
    studio_unchanged = sum(1 for uid in unchanged if "studio" in (allhist[uid]["type_name"] or "").lower())
    studio_total = sum(1 for uid in allhist_flagged if "studio" in (allhist[uid]["type_name"] or "").lower())
    a_sold_flagged = sum(1 for uid in allhist_flagged if allhist[uid]["is_sold"])
    a_unsold_flagged = len(allhist_flagged) - a_sold_flagged
    print(f"    of the {studio_total} originally-flagged STUDIO units, unchanged: {studio_unchanged}")
    print(f"    changed (flipped) from the original {len(allhist_flagged)}: {len(allhist_flagged) - len(unchanged)}")
    print(f"    NOTE — original 84 by state: sold={a_sold_flagged}  unsold={a_unsold_flagged}")
    print(f"           original 84 by tier×state: peer all-sold; ALL {a_t2a}+{a_t2b} Tier-2a/2b units are UNSOLD")
    print(f"           => the literal 'own sale-period' rule cannot evaluate them (no contract → no vintage),")
    print(f"           so they drop to UNEVALUABLE for a COVERAGE reason, not because time changed the verdict.")
    print(f"    module-reported unevaluable (all-history): {module_d['unevaluable_count']}  "
          f"vs vintage-rule unevaluable: {len(unevaluable)}")

    # ── SUPPLEMENTARY — mission-intent model (NOT a spec'd Part) ─────────────
    # The literal rule blinds Tier 2a/2b (its population is 100% unsold). The mission asks
    # whether the GROSS errors survive time-control. An unsold list price is a PRESENT-DAY
    # asking price, so the apt time-control benchmarks it against the type's CURRENT-ERA
    # realized baseline (latest vintage bucket with ≥5 sold). Sold units keep their own
    # bucket. This answers "do the studio-65k and area=1 errors survive vintage control?"
    print("\n" + _SEP)
    print("  SUPPLEMENTARY — CURRENT-ERA vintage model (unsold → type's latest ≥5-sold bucket)")
    print("  (not one of the spec'd Parts 0-5; added because the literal rule cannot score unsold units)")
    print(_SEP)
    latest_bucket = {}   # type_id -> latest vintage bucket that has a baseline
    for (tid, b) in tb_vintage:
        if tid not in latest_bucket or b > latest_bucket[tid]:
            latest_bucket[tid] = b

    alt = {}
    for u in priced:
        uid = u["id"]
        a = allhist[uid]
        tid = a["type_id"]
        peer_anchor = a["peer_anchor"]
        if a["is_sold"] and a["own_bucket"] is not None:
            bkt = a["own_bucket"]
        else:
            bkt = latest_bucket.get(tid)          # current-era benchmark for unsold
        tb_a = tb_vintage.get((tid, bkt)) if bkt is not None else None
        decision, _ = _evaluate(a["list_pm2"], peer_anchor, tb_a)
        alt[uid] = {"decision": decision, "bucket": bkt}

    alt_t1 = sum(1 for r in alt.values() if r["decision"] == "peer")
    alt_t2a = sum(1 for r in alt.values() if r["decision"] == "type")
    alt_t2b = sum(1 for r in alt.values() if r["decision"] == "impossible")
    alt_total = alt_t1 + alt_t2a + alt_t2b
    alt_flagged = {uid for uid, r in alt.items() if r["decision"] in ("peer", "type", "impossible")}
    print(f"    CURRENT-ERA total : {alt_total}  T1(peer)={alt_t1}  T2a(type)={alt_t2a}  T2b(impossible)={alt_t2b}")
    print(f"    (all-history was  : total={a_total}  T1={a_t1}  T2a={a_t2a}  T2b={a_t2b})")
    survive = len(allhist_flagged & alt_flagged)
    studio_alt = sum(1 for uid in (allhist_flagged & alt_flagged)
                     if "studio" in (allhist[uid]["type_name"] or "").lower())
    imposs_survive = sum(1 for uid in (allhist_flagged & alt_flagged)
                         if allhist[uid]["decision"] == "impossible")
    imposs_total = sum(1 for uid in allhist_flagged if allhist[uid]["decision"] == "impossible")
    new_in_alt = sorted(alt_flagged - allhist_flagged)
    print(f"    of the original 84, STILL flagged under current-era model : {survive}")
    print(f"      studios still flagged: {studio_alt} of {studio_total}   "
          f"impossible(area-error) still flagged: {imposs_survive} of {imposs_total}")
    print(f"    NEWLY flagged (entered) under current-era model: {len(new_in_alt)}  {new_in_alt[:20]}")

    # Auditable mechanism: per-bucket realized baseline for the types that dominate the LEFT
    # set, with the all-history baseline alongside — shows whether each era's 3×median / 5×max
    # cut clears the unit's list price/m².
    print("\n    Per-bucket baselines for the dominant LEFT types (median / max / spread / n sold):")
    left_type_ids = Counter(allhist[uid]["type_id"] for uid in left)
    for tid, n_left in left_type_ids.most_common(4):
        tname = next((allhist[uid]["type_name"] for uid in left if allhist[uid]["type_id"] == tid), "—")
        ah = tb_allhist.get(tid)
        ah_s = (f"med={ah['median']:,.0f} max={ah['max']:,.0f} spread={ah['spread']:.2f} n={ah['n']}"
                if ah else "no all-history baseline")
        print(f"      [{tname}]  ({n_left} units LEFT)  latest-bucket used={_bucket_label(latest_bucket.get(tid))}")
        print(f"         ALL-HISTORY : {ah_s}   -> T2a cut 3×med={3*ah['median']:,.0f}" if ah else f"         ALL-HISTORY : {ah_s}")
        for b in sorted({bb for (tt, bb) in tb_vintage if tt == tid}):
            cell = tb_vintage[(tid, b)]
            print(f"         {_bucket_label(b)}   : med={cell['median']:,.0f} max={cell['max']:,.0f} "
                  f"spread={cell['spread']:.2f} n={cell['n']}   -> T2a cut 3×med={3*cell['median']:,.0f}")

    print("\n" + _SEP)
    print("  DISCOVERY COMPLETE — numbers only, no logic/label changes. Review and decide next.")
    print(_SEP)
    return 0


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
