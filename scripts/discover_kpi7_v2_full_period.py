"""
scripts/discover_kpi7_v2_full_period.py — N3 Discovery: KPI 7 v2 full-period numbers.

READ-ONLY discovery. Produces the OLD-vs-NEW window aggregates that inform the
KPI 7 v2 (full-period forecast) product decision. Numbers only — no service
code, no spec edits, no implementation.

Background (Decision 11.9 / Session N3):
  Production KPI 7 windows are [today, period_end]. In June, this_month /
  this_quarter / this_half all end on Jun 30, so the three forecast cards
  collapse to identical values. Khaled approved exploring a FULL-PERIOD
  redefinition: per period — total period dues + collected + remaining.

Two windows per calendar period (month / quarter / half / year):
  OLD (production replica):
      [state=post, payment_state in [unpaid, partial],
       date >= TODAY, date <= period_end]
      → SUM(amount), SUM(due_amount), __count
  NEW (full period — no payment_state filter, full calendar span):
      [state=post, date >= period_start, date <= period_end]
      → SUM(amount), SUM(paid_amount), SUM(x_studio_actual_paid_amount),
        SUM(due_amount), __count

Derived per NEW window (locked Module 2 field semantics):
  collected_cash     = SUM(x_studio_actual_paid_amount)   (cash + cleared cheques)
  collected_incl_chq = SUM(paid_amount)                   (+ postdated received)
  remaining          = SUM(due_amount)
  cheque_gap         = collected_incl_chq − collected_cash (postdated pipeline)
  identity           = |amount − (paid_amount + due_amount)| < 1.0 EGP
                       (FAIL = data anomaly; listed, never "fixed")
  D-4: rs.account.payment.installment is EMPTY — collected is derived from
  installment state (amount − due), never payment-event dates. BOTH collected
  axes are reported so Khaled chooses which the cards display.

Hard constraints:
  - READ-ONLY: direct JSON-RPC via OdooClient (ALLOWED_METHODS enforced).
  - NO FastAPI calls. No OpenAI. AI cost = $0.00.
  - Exactly 8 read_group RPCs (4 periods × 2 windows), no groupby.
  - Timezone: 'today' is Cairo-local via ZoneInfo("Africa/Cairo"); all
    boundary date strings computed Python-side. NEVER date:month groupby.

Usage (from project root):
    python scripts/discover_kpi7_v2_full_period.py
"""

import asyncio
import calendar
import io
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# sys.path.insert so script runs without PYTHONPATH set (settled convention)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient

# Force UTF-8 stdout (Windows consoles default to cp1252).
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Constants ─────────────────────────────────────────────────────────────────

_MODEL = "rs.installment"
_LA_VERDE_TZ = ZoneInfo("Africa/Cairo")

_SEP  = "═" * 100
_SEP2 = "─" * 100
_PASS = "[PASS]"
_FAIL = "[FAIL]"
_FLAG = "[FLAG]"
_INFO = "[INFO]"
_STOP = "[STOP]"

_PERIOD_NAMES = ("month", "quarter", "half", "year")

# Live anchor from verify_kpi7_live.py, 2026-06-11 (June collapse):
# OLD month ≈ OLD quarter ≈ OLD half ≈ 126 records / 21,014,883.00 EGP.
_ANCHOR_COUNT  = 126
_ANCHOR_AMOUNT = 21_014_883.00


# ── Period boundary computation (pure Cairo calendar arithmetic) ──────────────

def _compute_period_bounds(today: date) -> dict[str, tuple[date, date]]:
    """Return (period_start, period_end) for each of the 4 calendar periods.

    month   : 1st of current month  → last day of current month
    quarter : 1st of current quarter → last day of current quarter
    half    : Jan 1 → Jun 30 (H1) or Jul 1 → Dec 31 (H2)
    year    : Jan 1 → Dec 31

    Plain date objects only — rs.installment.date is a 'date' field (D0.3),
    so ISO strings go straight into domains with no UTC conversion.
    """
    _, last_day = calendar.monthrange(today.year, today.month)
    month = (date(today.year, today.month, 1),
             date(today.year, today.month, last_day))

    quarter_idx   = (today.month - 1) // 3        # 0=Q1, 1=Q2, 2=Q3, 3=Q4
    q_start_month = quarter_idx * 3 + 1           # 1, 4, 7, 10
    q_end_month   = (quarter_idx + 1) * 3         # 3, 6, 9, 12
    _, q_last_day = calendar.monthrange(today.year, q_end_month)
    quarter = (date(today.year, q_start_month, 1),
               date(today.year, q_end_month, q_last_day))

    if today.month <= 6:
        half = (date(today.year, 1, 1), date(today.year, 6, 30))
    else:
        half = (date(today.year, 7, 1), date(today.year, 12, 31))

    year = (date(today.year, 1, 1), date(today.year, 12, 31))

    return {"month": month, "quarter": quarter, "half": half, "year": year}


# ── Domains ───────────────────────────────────────────────────────────────────

def _old_domain(today_str: str, end_str: str) -> list:
    """Production KPI 7 replica: unpaid/partial dues from today to period end."""
    return [
        ("state", "=", "post"),
        ("payment_state", "in", ["unpaid", "partial"]),
        ("date", ">=", today_str),
        ("date", "<=", end_str),
    ]


def _new_domain(start_str: str, end_str: str) -> list:
    """Full period: ALL posted installments dated inside the calendar period."""
    return [
        ("state", "=", "post"),
        ("date", ">=", start_str),
        ("date", "<=", end_str),
    ]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _egp(v: float) -> str:
    return f"{v:>20,.2f}"


async def _read_group_totals(client: OdooClient, domain: list, fields: list) -> tuple[dict, int]:
    """One read_group, no groupby → single aggregate row. Returns (row, ms)."""
    t0 = time.monotonic()
    rows = await client.execute_kw(
        _MODEL, "read_group",
        args=[domain, fields, []],
        kwargs={"lazy": False},
    )
    ms = int((time.monotonic() - t0) * 1000)
    return (rows[0] if rows else {}), ms


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    run_at      = datetime.now(timezone.utc)
    today_cairo = datetime.now(_LA_VERDE_TZ).date()
    today_str   = today_cairo.isoformat()
    bounds      = _compute_period_bounds(today_cairo)

    print(_SEP)
    print("  N3 DISCOVERY — KPI 7 v2 Full-Period Forecast — OLD vs NEW window aggregates")
    print(f"  Run at (UTC) : {run_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  Today (Cairo): {today_str}")
    print(f"  ALLOWED_METHODS : {sorted(ALLOWED_METHODS)}")
    print("  READ-ONLY. Direct JSON-RPC. No FastAPI. No OpenAI. AI cost = $0.00")
    print(_SEP)
    print()

    # ── Exact boundary dates used ─────────────────────────────────────────────
    print("  EXACT WINDOW BOUNDARIES (all dates Cairo-local, ISO strings in domains):")
    print()
    print(f"  | {'Period':<8} | {'OLD start':<10} | {'OLD end':<10} | {'NEW start':<10} | {'NEW end':<10} |")
    print(f"  |{'-'*10}|{'-'*12}|{'-'*12}|{'-'*12}|{'-'*12}|")
    for name in _PERIOD_NAMES:
        start, end = bounds[name]
        print(f"  | {name:<8} | {today_str} | {end.isoformat()} | {start.isoformat()} | {end.isoformat()} |")
    print()
    print("  OLD domain: [state=post, payment_state in [unpaid,partial], date>=TODAY, date<=period_end]")
    print("  NEW domain: [state=post, date>=period_start, date<=period_end]   (no payment_state filter)")
    print()

    old_results: dict[str, dict] = {}
    new_results: dict[str, dict] = {}
    rpc_count = 0

    async with OdooClient() as client:

        # ── OLD windows (production replica) — 4 RPCs ─────────────────────────
        print(_SEP)
        print("  OLD WINDOWS — production KPI 7 replica  [today → period_end]")
        print(_SEP)
        print()
        for name in _PERIOD_NAMES:
            _, end = bounds[name]
            domain = _old_domain(today_str, end.isoformat())
            row, ms = await _read_group_totals(client, domain, ["amount", "due_amount"])
            rpc_count += 1
            res = {
                "count":  int(row.get("__count")    or 0),
                "amount": float(row.get("amount")     or 0.0),
                "due":    float(row.get("due_amount") or 0.0),
            }
            old_results[name] = res
            print(f"  ── OLD {name}  ({today_str} → {end.isoformat()})  [{ms} ms]")
            print(f"     domain          : {domain}")
            print(f"     record_count    : {res['count']:,}")
            print(f"     SUM(amount)     : {_egp(res['amount'])} EGP")
            print(f"     SUM(due_amount) : {_egp(res['due'])} EGP")
            print()

        # ── NEW windows (full period) — 4 RPCs ────────────────────────────────
        print(_SEP)
        print("  NEW WINDOWS — full period  [period_start → period_end]")
        print(_SEP)
        print()
        new_fields = ["amount", "paid_amount", "x_studio_actual_paid_amount", "due_amount"]
        for name in _PERIOD_NAMES:
            start, end = bounds[name]
            domain = _new_domain(start.isoformat(), end.isoformat())
            row, ms = await _read_group_totals(client, domain, new_fields)
            rpc_count += 1
            amount   = float(row.get("amount")                      or 0.0)
            paid     = float(row.get("paid_amount")                 or 0.0)
            actual   = float(row.get("x_studio_actual_paid_amount") or 0.0)
            due      = float(row.get("due_amount")                  or 0.0)
            delta    = amount - (paid + due)
            res = {
                "count":              int(row.get("__count") or 0),
                "amount":             amount,
                "collected_cash":     actual,        # cash + cleared cheques
                "collected_incl_chq": paid,          # + postdated received
                "remaining":          due,
                "cheque_gap":         paid - actual, # postdated pipeline
                "identity_delta":     delta,
                "identity_ok":        abs(delta) < 1.0,
            }
            new_results[name] = res
            print(f"  ── NEW {name}  ({start.isoformat()} → {end.isoformat()})  [{ms} ms]")
            print(f"     domain                  : {domain}")
            print(f"     record_count            : {res['count']:,}")
            print(f"     SUM(amount)             : {_egp(amount)} EGP   (إجمالي مستحقات الفترة)")
            print(f"     collected_cash          : {_egp(actual)} EGP   (SUM actual_paid — cash + cleared)")
            print(f"     collected_incl_chq      : {_egp(paid)} EGP   (SUM paid_amount — + postdated)")
            print(f"     remaining               : {_egp(due)} EGP   (SUM due_amount — المتبقي)")
            print(f"     cheque_gap              : {_egp(res['cheque_gap'])} EGP   (postdated cheques in pipeline)")
            ident_lbl = _PASS if res["identity_ok"] else _FAIL
            print(f"     identity amount≈paid+due: {ident_lbl}  delta = {delta:,.2f} EGP")
            print()

    print(f"  {_INFO} Total RPCs issued: {rpc_count}  (spec: 8)")
    print()

    # ── Combined comparison table ─────────────────────────────────────────────
    print(_SEP)
    print("  COMPARISON TABLE — OLD (production replica) vs NEW (full period) — all EGP")
    print(_SEP)
    print()
    hdr = (
        f"  | {'Period':<8} "
        f"| {'OLD cnt':>7} | {'OLD amount':>20} | {'OLD due':>20} "
        f"| {'NEW cnt':>7} | {'NEW amount':>20} | {'NEW collected_cash':>20} "
        f"| {'NEW coll_incl_chq':>20} | {'NEW remaining':>20} | {'NEW cheque_gap':>20} |"
    )
    sep_row = (
        f"  |{'-'*10}"
        f"|{'-'*9}|{'-'*22}|{'-'*22}"
        f"|{'-'*9}|{'-'*22}|{'-'*22}"
        f"|{'-'*22}|{'-'*22}|{'-'*22}|"
    )
    print(hdr)
    print(sep_row)
    for name in _PERIOD_NAMES:
        o, n = old_results[name], new_results[name]
        print(
            f"  | {name:<8} "
            f"| {o['count']:>7,} | {_egp(o['amount'])} | {_egp(o['due'])} "
            f"| {n['count']:>7,} | {_egp(n['amount'])} | {_egp(n['collected_cash'])} "
            f"| {_egp(n['collected_incl_chq'])} | {_egp(n['remaining'])} | {_egp(n['cheque_gap'])} |"
        )
    print()

    # ── Identity check summary ────────────────────────────────────────────────
    print(_SEP2)
    print("  IDENTITY CHECK — |SUM(amount) − (SUM(paid_amount) + SUM(due_amount))| < 1.0 EGP per NEW window")
    print(_SEP2)
    identity_failures: list[str] = []
    for name in _PERIOD_NAMES:
        n = new_results[name]
        lbl = _PASS if n["identity_ok"] else _FAIL
        print(f"  {lbl} NEW {name:<8}: delta = {n['identity_delta']:>16,.2f} EGP")
        if not n["identity_ok"]:
            identity_failures.append(name)
    if identity_failures:
        print()
        print(f"  {_FLAG} Identity FAIL on: {', '.join(identity_failures)}.")
        print("        This is a DATA ANOMALY (e.g. overpayment credit, Decision 18.2 territory).")
        print("        Listed for the spec amendment review — NOT corrected here.")
    print()

    # ── Sanity anchors ────────────────────────────────────────────────────────
    print(_SEP2)
    print("  SANITY ANCHORS")
    print(_SEP2)
    print()

    boundary_bug = False

    # Anchor 1 — OLD June collapse: month == quarter == half (same domain today),
    # each ≈ 126 / 21,014,883.00 (small intraday drift acceptable).
    om, oq, oh = old_results["month"], old_results["quarter"], old_results["half"]
    collapse_equal = (
        om["count"] == oq["count"] == oh["count"]
        and abs(om["amount"] - oq["amount"]) < 0.01
        and abs(oq["amount"] - oh["amount"]) < 0.01
    )
    lbl = _PASS if collapse_equal else _FLAG
    print(f"  {lbl} Anchor 1a — OLD month == OLD quarter == OLD half (June collapse replica):")
    print(f"        counts  : {om['count']:,} / {oq['count']:,} / {oh['count']:,}")
    print(f"        amounts : {om['amount']:,.2f} / {oq['amount']:,.2f} / {oh['amount']:,.2f}")
    if not collapse_equal:
        print(f"        {_FLAG} The three OLD windows differ — structurally unexpected in June. Investigate.")
    print()

    drift_count  = om["count"] - _ANCHOR_COUNT
    drift_amount = om["amount"] - _ANCHOR_AMOUNT
    drift_pct    = (abs(drift_amount) / _ANCHOR_AMOUNT * 100) if _ANCHOR_AMOUNT else 0.0
    structural   = abs(drift_count) > 13 or drift_pct > 10.0   # >~10% = structural
    lbl = _FLAG if structural else _PASS
    print(f"  {lbl} Anchor 1b — OLD month vs live anchor ({_ANCHOR_COUNT} / {_ANCHOR_AMOUNT:,.2f}):")
    print(f"        count drift  : {drift_count:+,} records")
    print(f"        amount drift : {drift_amount:+,.2f} EGP ({drift_pct:.2f}%)")
    if structural:
        print(f"        {_FLAG} Drift exceeds intraday tolerance — structurally different. Investigate.")
    else:
        print("        Within intraday-drift tolerance.")
    print()

    # Anchor 2 — NEW strict nesting: month < quarter < half ≤ year, all DIFFERENT.
    nm, nq, nh, ny = (new_results[p] for p in _PERIOD_NAMES)
    print("  Anchor 2 — NEW nesting: month < quarter < half ≤ year, all four DIFFERENT")
    print(f"        counts  : {nm['count']:,} < {nq['count']:,} < {nh['count']:,} ≤ {ny['count']:,}")
    print(f"        amounts : {nm['amount']:,.2f} < {nq['amount']:,.2f} < {nh['amount']:,.2f} ≤ {ny['amount']:,.2f}")

    if nm["count"] == nq["count"] and abs(nm["amount"] - nq["amount"]) < 0.01:
        boundary_bug = True
        print(f"  {_STOP} NEW month == NEW quarter — BOUNDARY BUG (quarter window not widening).")
        print("         Stopping per spec: report, do not trust the NEW numbers.")
    else:
        strict_ok = (
            nm["count"] < nq["count"] < nh["count"] <= ny["count"]
            and nm["amount"] < nq["amount"] < nh["amount"] <= ny["amount"]
        )
        lbl = _PASS if strict_ok else _FLAG
        print(f"  {lbl} Strict nesting on counts and amounts: {'holds' if strict_ok else 'VIOLATED — investigate'}")
        if nh["count"] == ny["count"] and abs(nh["amount"] - ny["amount"]) < 0.01:
            print(f"  {_FLAG} NEW half == NEW year — no H2 installments posted? Flagged for review.")
        else:
            print(f"  {_PASS} All four NEW windows are different.")
    print()

    # ── Wrap-up ───────────────────────────────────────────────────────────────
    print(_SEP)
    if boundary_bug:
        print(f"  {_STOP} DISCOVERY HALTED — boundary bug detected (see Anchor 2). Numbers above are suspect.")
        print(_SEP)
        raise SystemExit(1)
    print("  N3 DISCOVERY COMPLETE — numbers only. No spec change, no implementation.")
    print("  Next: Khaled reviews OLD vs NEW table + chooses collected axis (cash vs incl-cheques)")
    print("        → spec amendment → separate implementation session.")
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
