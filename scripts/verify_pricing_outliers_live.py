"""
scripts/verify_pricing_outliers_live.py — Projects Inventory Pricing Outliers
(Slice 2.5) identity-equal LIVE verification (READ-ONLY, $0 AI).

Proves pricing_outliers_service's flagged sets + every count match an INDEPENDENT direct
recomputation straight from rs.structure.unit + rs.contract + rs.payment.term. The
"MODULE" numbers come from get_pricing_outliers_overview() (injected with the same
read-only client). NOTHING here writes to Odoo.

What it checks (triple-agreement / identity-equal):
  • In-scope population — unit-id set (sold, area>0, ≥1 non-cancel contract, resolvable
    sale date), recomputed independently and asserted equal.
  • Section A — flagged {unit_id: direction} map (peer realized price/m² Tukey + min-dev,
    vintage-bucketed peer groups), recomputed and asserted equal.
  • Section B — flagged {unit_id: kind} map (discount vs own list), recomputed and equal.
  • Confirmed — A∩B unit-id set, recomputed and equal.
  • All counts: section A below/above, section B deep/premium, confirmed, insufficient
    peers, population, eligible groups, per project.
Plus it PRINTS the dry-run summary (counts + a 3-row sample of each section) so the
thresholds (named constants in domain.py) can be tuned.

Method discipline: READ-ONLY (search_read only). ALLOWED_METHODS untouched. No FastAPI.
No OpenAI. AI cost = $0.00. Talks to Odoo directly — does not require uvicorn (still:
kill python + purge __pycache__ before any live run).

Usage (from project root):
    python scripts/verify_pricing_outliers_live.py
"""

import asyncio
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.modules.projects_inventory.domain import (  # noqa: E402
    CONTRACT_CANCEL_STATE,
    CONTRACT_MODEL,
    CONTRACT_PAYMENT_TERM_FIELD,
    CONTRACT_PRICE_FIELD,
    CONTRACT_STATE_FIELD,
    CONTRACT_UNIT_FIELD,
    OUTLIER_DEEP_DISCOUNT_PCT,
    OUTLIER_IQR_MULT,
    OUTLIER_MIN_DEV_PCT,
    OUTLIER_MIN_GROUP_SIZE,
    OUTLIER_PREMIUM_PCT,
    PAYMENT_TERM_DATE_FIELD,
    PAYMENT_TERM_MODEL,
    SOLD_STATES,
    UNIT_AMOUNT_FIELD,
    UNIT_AREA_FIELD,
    UNIT_MODEL,
    UNIT_TYPE_FIELD,
    VALUE_SCOPE_PROJECT_IDS,
    VINTAGE_BUCKET_YEARS,
)
from backend.modules.projects_inventory.services import cache as _cache  # noqa: E402
from backend.modules.projects_inventory.services.pricing_outliers_service import (  # noqa: E402
    get_pricing_outliers_overview,
)
from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient  # noqa: E402

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_SEP = "=" * 100
_SEP2 = "-" * 100
_CHUNK = 200


def _ok(flag: bool) -> str:
    return "PASS" if flag else "**FAIL**"


def _num(v) -> float:
    return float(v) if isinstance(v, (int, float)) else 0.0


def _c2(v: float) -> float:
    return round(v, 2)


def _m2o_id(v):
    if isinstance(v, (list, tuple)) and len(v) == 2:
        return int(v[0])
    return None


def _m2o_name(v):
    if isinstance(v, (list, tuple)) and len(v) == 2:
        return str(v[1])
    return None


def _quantile(sorted_vals, q):
    """Independent reimplementation of the inclusive linear quantile (same math the
    service uses) — floats are deterministic on identical sorted inputs."""
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    pos = (n - 1) * q
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


async def _odoo_recompute(client) -> dict:
    """Independent end-to-end recomputation of the population + both flag sets + counts,
    straight from Odoo. Mirrors the service's locked method but reads on its own."""
    units = await client.execute_kw(
        UNIT_MODEL, "search_read",
        args=[[("project_id", "in", list(VALUE_SCOPE_PROJECT_IDS)),
               ("state", "in", sorted(SOLD_STATES))]],
        kwargs={"fields": ["id", "state", "project_id", "zone_id", UNIT_TYPE_FIELD,
                           "code", UNIT_AMOUNT_FIELD, UNIT_AREA_FIELD], "order": "id"},
    )
    sold_ids = sorted(u["id"] for u in units)

    contracts = []
    for i in range(0, len(sold_ids), _CHUNK):
        chunk = sold_ids[i:i + _CHUNK]
        contracts += await client.execute_kw(
            CONTRACT_MODEL, "search_read",
            args=[[(CONTRACT_UNIT_FIELD, "in", chunk)]],
            kwargs={"fields": [CONTRACT_UNIT_FIELD, CONTRACT_PRICE_FIELD,
                               CONTRACT_STATE_FIELD, CONTRACT_PAYMENT_TERM_FIELD]},
        )
    realized: dict[int, float] = {}
    term_ids: dict[int, set] = {}
    for ct in contracts:
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

    # Population
    pop: list[dict] = []
    for u in units:
        area = _num(u.get(UNIT_AREA_FIELD))
        if area <= 0:
            continue
        uid = u["id"]
        if uid not in realized:
            continue
        dates = [term_dates[t] for t in term_ids.get(uid, set()) if t in term_dates]
        if not dates:
            continue
        sale_date = min(dates)
        realized_total = _c2(realized[uid])
        amount = _num(u.get(UNIT_AMOUNT_FIELD))
        year = int(sale_date[:4])
        bucket = (year // VINTAGE_BUCKET_YEARS) * VINTAGE_BUCKET_YEARS
        disc = _c2((amount - realized_total) / amount * 100.0) if amount > 0 else None
        pop.append({
            "unit_id": uid,
            "code": u.get("code") or "",
            "project_id": _m2o_id(u.get("project_id")) or 0,
            "project_name": (_m2o_name(u.get("project_id")) or "—").strip() or "—",
            "zone_id": _m2o_id(u.get("zone_id")),
            "unit_type_id": _m2o_id(u.get(UNIT_TYPE_FIELD)),
            "sale_date": sale_date,
            "vintage_bucket": bucket,
            "realized_total": realized_total,
            "list_total": _c2(amount),
            "realized_pm2": _c2(realized_total / area),
            "discount_pct": disc,
        })

    # Section A
    groups: dict[tuple, list[dict]] = {}
    for u in pop:
        groups.setdefault((u["zone_id"], u["unit_type_id"], u["vintage_bucket"]), []).append(u)
    a_flags: dict[int, str] = {}
    insufficient = 0
    eligible = 0
    for members in groups.values():
        if len(members) < OUTLIER_MIN_GROUP_SIZE:
            insufficient += len(members)
            continue
        eligible += 1
        vals = sorted(m["realized_pm2"] for m in members)
        median = _quantile(vals, 0.5)
        q1 = _quantile(vals, 0.25)
        q3 = _quantile(vals, 0.75)
        iqr = q3 - q1
        lower = q1 - OUTLIER_IQR_MULT * iqr
        upper = q3 + OUTLIER_IQR_MULT * iqr
        for m in members:
            pm2 = m["realized_pm2"]
            below = pm2 < lower
            above = pm2 > upper
            if not (below or above) or median <= 0:
                continue
            if abs((pm2 - median) / median * 100.0) < OUTLIER_MIN_DEV_PCT:
                continue
            a_flags[m["unit_id"]] = "below" if below else "above"

    # Section B
    b_flags: dict[int, str] = {}
    for u in pop:
        disc = u["discount_pct"]
        if disc is None:
            continue
        if disc >= OUTLIER_DEEP_DISCOUNT_PCT:
            b_flags[u["unit_id"]] = "deep"
        elif disc <= OUTLIER_PREMIUM_PCT:
            b_flags[u["unit_id"]] = "premium"

    confirmed = set(a_flags) & set(b_flags)

    # Per-project counts
    pid_by_unit = {u["unit_id"]: u["project_id"] for u in pop}
    proj_names = {}
    for u in pop:
        proj_names.setdefault(u["project_id"], u["project_name"])
    per_project = {}
    for pid in proj_names:
        per_project[pid] = {
            "project_name": proj_names[pid],
            "section_a_count": sum(1 for uid in a_flags if pid_by_unit[uid] == pid),
            "section_b_count": sum(1 for uid in b_flags if pid_by_unit[uid] == pid),
            "confirmed_count": sum(1 for uid in confirmed if pid_by_unit[uid] == pid),
        }

    return {
        "population_ids": {u["unit_id"] for u in pop},
        "a_flags": a_flags,
        "b_flags": b_flags,
        "confirmed": confirmed,
        "a_below": sum(1 for d in a_flags.values() if d == "below"),
        "a_above": sum(1 for d in a_flags.values() if d == "above"),
        "b_deep": sum(1 for k in b_flags.values() if k == "deep"),
        "b_premium": sum(1 for k in b_flags.values() if k == "premium"),
        "insufficient": insufficient,
        "eligible": eligible,
        "per_project": per_project,
    }


def _check(label: str, good: bool) -> int:
    print(f"  {label:<58} {_ok(good)}")
    return 0 if good else 1


async def main():
    print(_SEP)
    print("  PROJECTS INVENTORY — PRICING OUTLIERS (Slice 2.5) — IDENTITY-EQUAL LIVE VERIFY (READ-ONLY, $0)")
    print(f"  ALLOWED_METHODS : {sorted(ALLOWED_METHODS)}")
    print(f"  Scope project ids : {list(VALUE_SCOPE_PROJECT_IDS)}  (La Puerta excluded)")
    print(f"  Thresholds : MIN_GROUP_SIZE={OUTLIER_MIN_GROUP_SIZE} IQR_MULT={OUTLIER_IQR_MULT} "
          f"MIN_DEV_PCT={OUTLIER_MIN_DEV_PCT} DEEP={OUTLIER_DEEP_DISCOUNT_PCT} "
          f"PREMIUM={OUTLIER_PREMIUM_PCT} VINTAGE_BUCKET_YEARS={VINTAGE_BUCKET_YEARS}")
    print(_SEP)

    fail = 0
    _cache.clear()

    async with OdooClient() as client:
        mod = await get_pricing_outliers_overview(client=client)
        odoo = await _odoo_recompute(client)

        mod_a = {r["unit_id"]: r["direction"] for r in mod["section_a"]}
        mod_b = {r["unit_id"]: r["kind"] for r in mod["section_b"]}
        mod_conf = {r["unit_id"] for r in mod["section_a"] if r["is_confirmed"]}

        # ── IDENTITY checks ──────────────────────────────────────────────────────
        print(_SEP2)
        print("  IDENTITY — MODULE output vs INDEPENDENT Odoo recompute")
        print(_SEP2)
        fail += _check("Section A flagged {unit_id: direction} map equal", mod_a == odoo["a_flags"])
        fail += _check("Section B flagged {unit_id: kind} map equal", mod_b == odoo["b_flags"])
        fail += _check("Confirmed (A∩B) unit-id set equal", mod_conf == odoo["confirmed"])
        fail += _check("Confirmed == module confirmed_count", len(mod_conf) == mod["confirmed_count"])
        fail += _check("population_count == |independent population|",
                       mod["population_count"] == len(odoo["population_ids"]))
        fail += _check("section_a_count == |independent A|",
                       mod["section_a_count"] == len(odoo["a_flags"]))
        fail += _check("section_b_count == |independent B|",
                       mod["section_b_count"] == len(odoo["b_flags"]))
        fail += _check("section A below/above match",
                       (mod["section_a_below_count"], mod["section_a_above_count"])
                       == (odoo["a_below"], odoo["a_above"]))
        fail += _check("section B deep/premium match",
                       (mod["section_b_deep_count"], mod["section_b_premium_count"])
                       == (odoo["b_deep"], odoo["b_premium"]))
        fail += _check("insufficient_peers_count match",
                       mod["insufficient_peers_count"] == odoo["insufficient"])
        fail += _check("eligible_group_count match",
                       mod["eligible_group_count"] == odoo["eligible"])
        print()

        # ── PER-PROJECT identity ─────────────────────────────────────────────────
        print(_SEP2)
        print("  PER-PROJECT — MODULE vs independent recompute")
        print(_SEP2)
        mod_proj = {p["project_id"]: p for p in mod["projects"]}
        all_pids = set(mod_proj) | set(odoo["per_project"])
        for pid in sorted(all_pids):
            mp = mod_proj.get(pid)
            op = odoo["per_project"].get(pid)
            if mp is None or op is None:
                fail += _check(f"project id={pid} present in both", False)
                continue
            good = (mp["section_a_count"] == op["section_a_count"]
                    and mp["section_b_count"] == op["section_b_count"]
                    and mp["confirmed_count"] == op["confirmed_count"])
            fail += _check(
                f"id={pid} {mp['project_name']!r:<16} A={mp['section_a_count']:>3} "
                f"B={mp['section_b_count']:>3} C={mp['confirmed_count']:>3}", good)
        print()

        # ── DRY-RUN SUMMARY (counts at spec thresholds + samples) ────────────────
        print(_SEP2)
        print("  DRY-RUN — flag counts at spec thresholds")
        print(_SEP2)
        print(f"  population (in-scope sold w/ contract + sale date) : {mod['population_count']:>6,}")
        print(f"  eligible peer groups (>= {OUTLIER_MIN_GROUP_SIZE})                      : {mod['eligible_group_count']:>6,}")
        print(f"  insufficient-peers units (footnote)                : {mod['insufficient_peers_count']:>6,}")
        print(f"  Section A  total : {mod['section_a_count']:>4}   (below {mod['section_a_below_count']}, above {mod['section_a_above_count']})")
        print(f"  Section B  total : {mod['section_b_count']:>4}   (deep {mod['section_b_deep_count']}, premium {mod['section_b_premium_count']})")
        print(f"  CONFIRMED (both) : {mod['confirmed_count']:>4}")
        print(f"  per project      : " + " | ".join(
            f"{p['project_name']}: A={p['section_a_count']} B={p['section_b_count']} C={p['confirmed_count']}"
            for p in mod["projects"]))
        print()

        print(f"  Section A sample (top 3 by |deviation|):")
        for r in mod["section_a"][:3]:
            print(f"    {r['code']:<14} {r['project_name']:<12} {r['zone_name']:<10} "
                  f"{r['unit_type_name']:<16} {r['vintage_bucket_label']} {r['sale_date']}  "
                  f"pm2={r['realized_pm2']:>10,.0f}  med={r['group_median_pm2']:>10,.0f}  "
                  f"dev={r['deviation_pct']:>7,.1f}%  {r['direction']}"
                  f"{'  [confirmed]' if r['is_confirmed'] else ''}")
        print(f"  Section B sample (top 3):")
        for r in mod["section_b"][:3]:
            print(f"    {r['code']:<14} {r['project_name']:<12} {r['unit_type_name']:<16} "
                  f"{r['sale_date']}  list={r['list_total']:>12,.0f}  real={r['realized_total']:>12,.0f}  "
                  f"disc={r['discount_pct']:>7,.1f}%  {r['kind']}"
                  f"{'  [confirmed]' if r['is_confirmed'] else ''}")
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
