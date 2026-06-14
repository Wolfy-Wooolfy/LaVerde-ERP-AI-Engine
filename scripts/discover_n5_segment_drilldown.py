"""
scripts/discover_n5_segment_drilldown.py — N5 Discovery: per-installment segment
drill-down for KPI 7 v2 (Dues & Collections — Current Periods, Decision 19.1).

READ-ONLY discovery. Numbers only — NO production code, NO service edits, NO
commits. The goal is to PROVE (or disprove) a row-level Odoo domain for each of
the three card segments whose individual installment rows SUM EXACTLY to the
card's aggregate figure.

Truth lifted VERBATIM from backend/modules/collections/services/kpi_service.py
(KPI 7 v2 — _fetch_bucket / _compute_period_bounds / get_expected_collections_forecast):
  model      : rs.installment            (_MODEL, line 28)
  date field : "date"                    (plain 'date' field — D0.3, no UTC conv)
  base domain: [("state","=","post"), ("date",">=",start), ("date","<=",end)]
               (NO payment_state filter — Decision 19.1)
  summed     : amount, paid_amount, x_studio_actual_paid_amount, due_amount
  buckets    : this_month ⊆ this_quarter ⊆ this_half ⊆ this_year
  tz         : ZoneInfo("Africa/Cairo")  (today_cairo)

Segment definitions (locked Module 2 field semantics):
  per record:  cleared   := x_studio_actual_paid_amount   (cash + cleared cheques)
               pending   := paid_amount − x_studio_actual_paid_amount  (postdated pipeline)
               remaining := due_amount                    (amount − paid)
  aggregate:   agg_cleared   = SUM(x_studio_actual_paid_amount)
               agg_pending   = SUM(paid_amount) − SUM(x_studio_actual_paid_amount)
               agg_remaining = SUM(due_amount)
               agg_total     = SUM(amount)

Proposed row-level drill-down domains (the thing being proved):
  cleared   : base + [("x_studio_actual_paid_amount", ">", 0)]   metric = x_studio_actual_paid_amount
  pending   : base + [paid_amount > x_studio_actual_paid_amount]  metric = paid_amount − x_studio_actual_paid_amount
  remaining : base + [("due_amount", ">", 0)]                     metric = due_amount

Hard constraints:
  - READ-ONLY: search_read / read_group / search_count / fields_get only.
  - NO create/write/unlink. ALLOWED_METHODS untouched.
  - No FastAPI. No OpenAI. AI cost = $0.00.

Usage (from project root, server NOT required — talks to Odoo directly):
    python scripts/discover_n5_segment_drilldown.py
"""

import asyncio
import calendar
import io
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# sys.path.insert so the script runs without PYTHONPATH set (settled convention).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient  # noqa: E402

# Force UTF-8 stdout (Windows consoles default to cp1252).
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Constants (mirrored verbatim from kpi_service.py) ───────────────────────────

_MODEL = "rs.installment"
_LA_VERDE_TZ = ZoneInfo("Africa/Cairo")

_SUM_FIELDS = ["amount", "paid_amount", "x_studio_actual_paid_amount", "due_amount"]

_BUCKET_NAMES = ("this_month", "this_quarter", "this_half", "this_year")

_SEP = "═" * 110
_SEP2 = "─" * 110
_PASS = "[PASS]"
_FAIL = "[FAIL]"
_FLAG = "[FLAG]"
_INFO = "[INFO]"

# Match tolerance for "row_sum reproduces aggregate" (EGP).
_EPS = 1.0
# Tolerance for classifying a per-row value as effectively zero (EGP).
_ZERO = 0.005


# ── Period boundary computation (verbatim from kpi_service._compute_period_bounds) ──

def _compute_period_bounds(today: date) -> dict[str, tuple[date, date]]:
    """Return (period_start, period_end) for each of the 4 KPI 7 calendar buckets.

    Ported VERBATIM from backend/modules/collections/services/kpi_service.py
    (only the bucket keys are renamed to the canonical this_* form already used
    there). Pure calendar math on plain date objects; ISO strings go straight
    into domains (rs.installment.date is a plain 'date' field — D0.3).
    """
    _, last_day = calendar.monthrange(today.year, today.month)
    month = (date(today.year, today.month, 1),
             date(today.year, today.month, last_day))

    quarter_idx = (today.month - 1) // 3        # 0=Q1, 1=Q2, 2=Q3, 3=Q4
    q_start_month = quarter_idx * 3 + 1         # 1, 4, 7, 10
    q_end_month = (quarter_idx + 1) * 3         # 3, 6, 9, 12
    _, q_last_day = calendar.monthrange(today.year, q_end_month)
    quarter = (date(today.year, q_start_month, 1),
               date(today.year, q_end_month, q_last_day))

    if today.month <= 6:
        half = (date(today.year, 1, 1), date(today.year, 6, 30))
    else:
        half = (date(today.year, 7, 1), date(today.year, 12, 31))

    year = (date(today.year, 1, 1), date(today.year, 12, 31))

    return {
        "this_month": month,
        "this_quarter": quarter,
        "this_half": half,
        "this_year": year,
    }


def _base_domain(start_str: str, end_str: str) -> list:
    """KPI 7 v2 base domain — verbatim from _fetch_bucket (Decision 19.1)."""
    return [
        ("state", "=", "post"),
        ("date", ">=", start_str),
        ("date", "<=", end_str),
    ]


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _egp(v: float) -> str:
    return f"{v:>20,.2f}"


def _num(row: dict, key: str) -> float:
    return float(row.get(key) or 0.0)


def _m2o(row: dict, key: str) -> str:
    """Render a many2one [id, name] pair (or False) for display."""
    val = row.get(key)
    if isinstance(val, (list, tuple)) and len(val) == 2:
        return f"{val[1]} (id={val[0]})"
    return str(val)


# ── Field discovery ─────────────────────────────────────────────────────────────

async def _discover_fields(client: OdooClient) -> dict:
    """fields_get on rs.installment; print candidate partner/unit/state/numeric fields."""
    fields = await client.execute_kw(
        _MODEL, "fields_get",
        args=[],
        kwargs={"attributes": ["string", "type", "relation", "selection"]},
    )

    partner_cands, unit_cands, state_cands, numeric_cands, char_cands = [], [], [], [], []
    for fname, meta in sorted(fields.items()):
        ftype = meta.get("type")
        relation = meta.get("relation")
        label = meta.get("string")
        low = fname.lower()
        if ftype == "many2one":
            if relation == "res.partner" or any(k in low for k in ("partner", "customer", "client")):
                partner_cands.append((fname, label, relation))
            if any(k in low for k in ("unit", "property", "apartment", "villa", "asset",
                                      "product", "lot", "chalet", "plot")):
                unit_cands.append((fname, label, relation))
        if ftype in ("float", "monetary"):
            numeric_cands.append((fname, label))
        if ftype == "selection":
            state_cands.append((fname, label, list((meta.get("selection") or []))))
        if ftype in ("char", "text") and any(k in low for k in
                                             ("unit", "name", "ref", "code", "number", "no")):
            char_cands.append((fname, label, ftype))

    print(_SEP)
    print("  FIELD DISCOVERY — fields_get(rs.installment)")
    print(_SEP)
    print()
    print(f"  Partner candidates (many2one→res.partner or *partner/customer/client*):")
    for n, l, r in partner_cands:
        print(f"     - {n:<40} relation={r!s:<20} label={l!r}")
    if not partner_cands:
        print("     (none found)")
    print()
    print(f"  Unit / property / reference candidates (many2one):")
    for n, l, r in unit_cands:
        print(f"     - {n:<40} relation={r!s:<20} label={l!r}")
    if not unit_cands:
        print("     (none found among many2one)")
    print()
    print(f"  Char/text reference candidates (name/ref/code/unit/number):")
    for n, l, t in char_cands:
        print(f"     - {n:<40} type={t:<8} label={l!r}")
    if not char_cands:
        print("     (none found)")
    print()
    print(f"  Selection (state/type) fields:")
    for n, l, sel in state_cands:
        opts = ", ".join(k for k, _ in sel) if sel else ""
        print(f"     - {n:<40} label={l!r}  options=[{opts}]")
    print()
    print(f"  Numeric (float/monetary) fields — scan for a stored postdated/cheque/pending field:")
    for n, l in numeric_cands:
        print(f"     - {n:<40} label={l!r}")
    print()

    # Pick display fields for the drill-down list (validated against fields_get).
    def _pick(cands, prefer):
        names = [c[0] for c in cands]
        for p in prefer:
            if p in names:
                return p
        return names[0] if names else None

    chosen_partner = _pick(partner_cands, ["partner_id", "x_partner_id", "customer_id"])
    chosen_unit = _pick(unit_cands, ["x_studio_unit", "unit_id", "property_id", "product_id"])
    if chosen_unit is None and char_cands:
        chosen_unit = char_cands[0][0]
    chosen_state = "state" if "state" in fields else (state_cands[0][0] if state_cands else None)

    print(f"  CHOSEN for drill-down display:")
    print(f"     partner field : {chosen_partner}")
    print(f"     unit field    : {chosen_unit}")
    print(f"     state field   : {chosen_state}")
    print()

    return {
        "fields": fields,
        "partner": chosen_partner,
        "unit": chosen_unit,
        "state": chosen_state,
    }


# ── Pending-domain feasibility probe ────────────────────────────────────────────

async def _probe_pending_domain(client: OdooClient, base: list) -> None:
    """Test whether 'paid_amount > x_studio_actual_paid_amount' is expressible as
    a native Odoo domain. Field-to-field comparison is NOT supported by the ORM;
    we send it once to demonstrate the failure mode for the report."""
    print(_SEP2)
    print("  PENDING-DOMAIN FEASIBILITY PROBE")
    print(_SEP2)
    probe = base + [("paid_amount", ">", "x_studio_actual_paid_amount")]
    print(f"  Trying field-to-field domain: {probe[-1]}")
    try:
        n = await client.execute_kw(_MODEL, "search_count", args=[probe], kwargs={})
        print(f"  {_FLAG} Server ACCEPTED the domain and returned count={n}.")
        print("        WARNING: Odoo compared paid_amount to the LITERAL STRING")
        print("        'x_studio_actual_paid_amount', NOT to the field — count is meaningless.")
    except Exception as exc:
        print(f"  {_INFO} Server REJECTED the field-to-field domain (as expected).")
        print(f"        {type(exc).__name__}: {str(exc)[:160]}")
    print("  → 'pending' has NO native single-field Odoo domain. The drill-down must")
    print("    fetch a server-side superset (paid_amount > 0) and compute the metric")
    print("    (paid − actual) client-side, keeping rows where it is > 0. This script")
    print("    derives the authoritative pending row set from the FULL base fetch.")
    print()


# ── Per-bucket analysis ─────────────────────────────────────────────────────────

async def _analyze_bucket(client: OdooClient, name: str, start: date, end: date,
                          disp: dict) -> dict:
    base = _base_domain(start.isoformat(), end.isoformat())

    # ── A) AGGREGATE — single read_group (source of truth) ──────────────────────
    rg = await client.execute_kw(
        _MODEL, "read_group",
        args=[base, _SUM_FIELDS, []],
        kwargs={"lazy": False},
    )
    arow = rg[0] if rg else {}
    agg_amount = _num(arow, "amount")
    agg_paid = _num(arow, "paid_amount")
    agg_actual = _num(arow, "x_studio_actual_paid_amount")
    agg_due = _num(arow, "due_amount")
    agg_count = int(arow.get("__count") or 0)

    agg = {
        "cleared": agg_actual,
        "pending": agg_paid - agg_actual,
        "remaining": agg_due,
        "total": agg_amount,
        "count": agg_count,
    }

    # Display fields for the row fetches (only those that exist).
    disp_fields = ["id", "date", "amount", "paid_amount",
                   "x_studio_actual_paid_amount", "due_amount"]
    for extra in (disp.get("state"), disp.get("partner"), disp.get("unit")):
        if extra and extra not in disp_fields:
            disp_fields.append(extra)

    # ── B) ROW-LEVEL — server-side domains for cleared & remaining ──────────────
    cleared_rows = await client.execute_kw(
        _MODEL, "search_read",
        args=[base + [("x_studio_actual_paid_amount", ">", 0)]],
        kwargs={"fields": disp_fields, "order": "id"},
    )
    remaining_rows = await client.execute_kw(
        _MODEL, "search_read",
        args=[base + [("due_amount", ">", 0)]],
        kwargs={"fields": disp_fields, "order": "id"},
    )
    cleared_sum = sum(_num(r, "x_studio_actual_paid_amount") for r in cleared_rows)
    remaining_sum = sum(_num(r, "due_amount") for r in remaining_rows)

    # ── FULL base fetch — pending derivation + edge cases + cross-checks ─────────
    full_rows = await client.execute_kw(
        _MODEL, "search_read",
        args=[base],
        kwargs={"fields": ["id", "amount", "paid_amount",
                           "x_studio_actual_paid_amount", "due_amount"], "order": "id"},
    )
    # pending derived client-side (no native domain).
    pending_rows = []
    for r in full_rows:
        pend = _num(r, "paid_amount") - _num(r, "x_studio_actual_paid_amount")
        if pend > 0:
            pending_rows.append((r, pend))
    pending_sum = sum(p for _, p in pending_rows)

    seg = {
        "cleared": {"count": len(cleared_rows), "sum": cleared_sum,
                    "rows": cleared_rows, "mode": "server-side domain"},
        "pending": {"count": len(pending_rows), "sum": pending_sum,
                    "rows": [r for r, _ in pending_rows], "mode": "client-derived (paid−actual>0)"},
        "remaining": {"count": len(remaining_rows), "sum": remaining_sum,
                      "rows": remaining_rows, "mode": "server-side domain"},
    }

    # ── Overlap analysis (row SETS overlap; money partitions) ───────────────────
    in_c = in_p = in_r = 0
    in_none = 0
    union_ids = set()
    overlap_multi = 0
    for r in full_rows:
        rid = r["id"]
        cl = _num(r, "x_studio_actual_paid_amount")
        pe = _num(r, "paid_amount") - _num(r, "x_studio_actual_paid_amount")
        du = _num(r, "due_amount")
        flags = (cl > 0, pe > 0, du > 0)
        in_c += flags[0]
        in_p += flags[1]
        in_r += flags[2]
        if any(flags):
            union_ids.add(rid)
        else:
            in_none += 1
        if sum(flags) >= 2:
            overlap_multi += 1

    return {
        "name": name, "start": start, "end": end,
        "agg": agg, "seg": seg, "full_count": len(full_rows),
        "in_c": in_c, "in_p": in_p, "in_r": in_r,
        "in_none": in_none, "union": len(union_ids), "overlap_multi": overlap_multi,
        "full_rows": full_rows,
    }


def _print_bucket(b: dict, disp: dict) -> None:
    name = b["name"]
    agg = b["agg"]
    seg = b["seg"]
    print(_SEP)
    print(f"  BUCKET: {name}   [{b['start'].isoformat()} → {b['end'].isoformat()}]   "
          f"base records = {b['full_count']:,}")
    print(_SEP)
    print(f"  AGGREGATE (read_group, source of truth):")
    print(f"     period_total  SUM(amount)        : {_egp(agg['total'])} EGP")
    print(f"     cleared       SUM(actual_paid)   : {_egp(agg['cleared'])} EGP")
    print(f"     pending       SUM(paid)−SUM(act) : {_egp(agg['pending'])} EGP")
    print(f"     remaining     SUM(due_amount)    : {_egp(agg['remaining'])} EGP")
    print()
    print(f"  ROW-LEVEL vs AGGREGATE:")
    print(f"  | {'segment':<10} | {'agg_value':>20} | {'row_count':>9} | {'row_sum':>20} "
          f"| {'delta(agg−rows)':>18} | {'MATCH?':<6} | mode")
    print(f"  |{'-'*12}|{'-'*22}|{'-'*11}|{'-'*22}|{'-'*20}|{'-'*8}|{'-'*30}")
    for s in ("cleared", "pending", "remaining"):
        a = agg[s]
        rc = seg[s]["count"]
        rs = seg[s]["sum"]
        delta = a - rs
        match = _PASS if abs(delta) < _EPS else _FAIL
        print(f"  | {s:<10} | {_egp(a)} | {rc:>9,} | {_egp(rs)} | {delta:>18,.2f} "
              f"| {match:<6} | {seg[s]['mode']}")
    print()

    # Three-segment money reconciliation (amounts partition the total).
    three_sum = seg["cleared"]["sum"] + seg["pending"]["sum"] + seg["remaining"]["sum"]
    rdelta = agg["total"] - three_sum
    rmatch = _PASS if abs(rdelta) < _EPS else _FAIL
    print(f"  RECONCILIATION (row_sum_cleared + row_sum_pending + row_sum_remaining vs SUM(amount)):")
    print(f"     {seg['cleared']['sum']:,.2f} + {seg['pending']['sum']:,.2f} + "
          f"{seg['remaining']['sum']:,.2f} = {three_sum:,.2f}")
    print(f"     period_total = {agg['total']:,.2f}   delta = {rdelta:,.2f}   {rmatch}")
    print()

    # Overlap facts (row counts NOT mutually exclusive).
    seg_count_sum = b["in_c"] + b["in_p"] + b["in_r"]
    print(f"  OVERLAP (row SETS overlap on records; money partitions cleanly):")
    print(f"     rows in cleared / pending / remaining : {b['in_c']:,} / {b['in_p']:,} / {b['in_r']:,}")
    print(f"     Σ segment counts = {seg_count_sum:,}   distinct union = {b['union']:,}   "
          f"(rows in ≥2 segments = {b['overlap_multi']:,})")
    print(f"     rows in NO segment (cleared=pending=remaining=0) : {b['in_none']:,}")
    print()

    # Tiny display sample to prove the customer+unit fields render.
    sample = seg["cleared"]["rows"][:3] or seg["remaining"]["rows"][:3]
    if sample:
        pf, uf = disp.get("partner"), disp.get("unit")
        print(f"  SAMPLE rows (proves drill-down display fields resolve):")
        for r in sample:
            pv = _m2o(r, pf) if pf else "n/a"
            uv = _m2o(r, uf) if uf else "n/a"
            print(f"     id={r['id']:<8} date={r.get('date')!s:<12} "
                  f"amount={_num(r,'amount'):>14,.2f} | customer={pv} | unit={uv}")
        print()


# ── Main ────────────────────────────────────────────────────────────────────────

async def main() -> None:
    run_at = datetime.now(timezone.utc)
    today_cairo = datetime.now(_LA_VERDE_TZ).date()
    bounds = _compute_period_bounds(today_cairo)

    print(_SEP)
    print("  N5 DISCOVERY — Per-Installment Segment Drill-down for KPI 7 v2 (Decision 19.1)")
    print(f"  Run at (UTC) : {run_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  Today (Cairo): {today_cairo.isoformat()}")
    print(f"  Model        : {_MODEL}   date field: 'date'   base: state=post, date∈[start,end] (no payment_state)")
    print(f"  ALLOWED_METHODS: {sorted(ALLOWED_METHODS)}")
    print("  READ-ONLY. Direct JSON-RPC. No FastAPI. No OpenAI. AI cost = $0.00")
    print(_SEP)
    print()
    print("  BUCKET BOUNDARIES (Cairo-local ISO strings):")
    for nm in _BUCKET_NAMES:
        s, e = bounds[nm]
        print(f"     {nm:<13} {s.isoformat()} → {e.isoformat()}")
    print()

    results = []
    async with OdooClient() as client:
        disp = await _discover_fields(client)
        await _probe_pending_domain(
            client, _base_domain(bounds["this_month"][0].isoformat(),
                                 bounds["this_month"][1].isoformat()))
        for nm in _BUCKET_NAMES:
            s, e = bounds[nm]
            t0 = time.monotonic()
            b = await _analyze_bucket(client, nm, s, e, disp)
            b["ms"] = int((time.monotonic() - t0) * 1000)
            results.append(b)
            _print_bucket(b, disp)

    # ── Largest (bucket, segment) row_count — pagination sizing ─────────────────
    print(_SEP)
    print("  PAGINATION SIZING — largest (bucket, segment) row_count")
    print(_SEP)
    largest = (None, None, -1)
    for b in results:
        for s in ("cleared", "pending", "remaining"):
            rc = b["seg"][s]["count"]
            print(f"     {b['name']:<13} {s:<10} : {rc:>8,} rows")
            if rc > largest[2]:
                largest = (b["name"], s, rc)
    print()
    print(f"  ► LARGEST: {largest[0]} / {largest[1]} = {largest[2]:,} rows")
    print()

    # ── Edge-case scan on this_year (the superset bucket) ───────────────────────
    yb = next(b for b in results if b["name"] == "this_year")
    neg_pending = neg_cleared = neg_remaining = zero_amount = all_zero = 0
    for r in yb["full_rows"]:
        amt = _num(r, "amount")
        cl = _num(r, "x_studio_actual_paid_amount")
        pe = _num(r, "paid_amount") - _num(r, "x_studio_actual_paid_amount")
        du = _num(r, "due_amount")
        if pe < -_ZERO:
            neg_pending += 1
        if cl < -_ZERO:
            neg_cleared += 1
        if du < -_ZERO:
            neg_remaining += 1
        if abs(amt) < _ZERO:
            zero_amount += 1
        if abs(cl) < _ZERO and abs(pe) < _ZERO and abs(du) < _ZERO:
            all_zero += 1
    print(_SEP)
    print(f"  EDGE-CASE SCAN — this_year base set ({yb['full_count']:,} rows; superset of all buckets)")
    print(_SEP)
    print(f"     rows with negative pending (paid−actual < 0)   : {neg_pending:,}")
    print(f"     rows with negative cleared (actual_paid < 0)   : {neg_cleared:,}")
    print(f"     rows with negative remaining (due_amount < 0)  : {neg_remaining:,}")
    print(f"     rows with amount == 0                          : {zero_amount:,}")
    print(f"     rows all-zero (cleared=pending=remaining=0)    : {all_zero:,}")
    print(f"     rows in NO segment (this_year)                 : {yb['in_none']:,}")
    excl_match = _PASS if all_zero == yb["in_none"] else _FLAG
    print(f"     {excl_match} all-zero count == in-no-segment count "
          f"({all_zero:,} vs {yb['in_none']:,})")
    print()

    # ── Verdict per segment (across all buckets) ────────────────────────────────
    print(_SEP)
    print("  PER-SEGMENT VERDICT (does the proposed row-level domain reproduce the aggregate?)")
    print(_SEP)
    for s in ("cleared", "pending", "remaining"):
        worst = max(abs(b["agg"][s] - b["seg"][s]["sum"]) for b in results)
        ok = worst < _EPS
        verdict = "EXACT — safe to build the drill-down on this domain" if ok \
            else f"DIVERGES — max delta {worst:,.2f} EGP — needs a different definition"
        lbl = _PASS if ok else _FAIL
        mode = results[0]["seg"][s]["mode"]
        print(f"  {lbl} {s:<10} ({mode}): {verdict}")
    print()
    print(_SEP)
    print("  N5 DISCOVERY COMPLETE — numbers only. No spec change, no implementation, no commit.")
    print(_SEP)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except SystemExit:
        raise
    except Exception as exc:
        print(f"\n  FATAL ERROR: {exc}")
        import traceback
        traceback.print_exc()
        raise SystemExit(1)
