"""
scripts/discover_kpi7.py — Phase 0 Discovery: KPI 7 Expected Collections Forecast.

Pre-implementation discovery for KPI 7. Verifies the following before any
service code is written:

  Section 1  — rs.installment.date field type (must be 'date', not 'datetime')
  Section 2  — Field-to-field domain test: ('paid_amount','>','x_studio_actual_paid_amount')
  Section 3  — Bucket boundary arithmetic + counts (nesting invariant)
  Section 3b — KPI 2 / KPI 7 mutual exclusivity sanity check
  Section 4  — Khaled cross-check baseline (Odoo-UI-paste format)
  Section 4b — Cheques baseline per bucket (approach depends on Section 2 outcome)
  Section 5  — Summary + PHASE 0 COMPLETE message

Hard constraints:
  - READ-ONLY: no create, write, or unlink RPCs.
  - Does NOT import from backend.modules.collections.services.
  - No OpenAI calls. AI cost = $0.00.
  - No PII (no customer names or partner IDs in output).
  - Tees stdout to scripts/discover_kpi7_output.txt.

Usage (from project root, after Decision 6.4 clean restart):
    python scripts/discover_kpi7.py
"""

import asyncio
import calendar
import io
import sys
import time
from datetime import date, datetime, timezone
from io import StringIO
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient

# Force UTF-8 stdout (Windows consoles default to cp1252).
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Constants ─────────────────────────────────────────────────────────────────

_MODEL = "rs.installment"
_LA_VERDE_TZ = ZoneInfo("Africa/Cairo")
_OUTPUT_FILE = Path(__file__).parent / "discover_kpi7_output.txt"

_SEP  = "═" * 78
_SEP2 = "─" * 76
_PASS = "[PASS]"
_FLAG = "[FLAG]"
_INFO = "[INFO]"
_STOP = "[STOP]"

_BUCKET_NAMES = ["this_month", "this_quarter", "this_half", "this_year"]


# ── Bucket boundary computation ───────────────────────────────────────────────

def _compute_bucket_ends(today: date) -> dict:
    """Compute the 4 KPI 7 bucket end dates using Cairo calendar arithmetic.

    No UTC conversion needed — rs.installment.date is a plain 'date' field
    (Phase 2 §2 Dependency #1). Cairo TZ is used only to determine 'today'.

    Boundaries (spec §4.2):
      this_month   : last day of current calendar month
      this_quarter : Mar 31 / Jun 30 / Sep 30 / Dec 31
      this_half    : Jun 30 or Dec 31
      this_year    : Dec 31 of current year
    """
    _, last_day = calendar.monthrange(today.year, today.month)
    end_of_month = date(today.year, today.month, last_day)

    quarter_idx = (today.month - 1) // 3          # 0=Q1, 1=Q2, 2=Q3, 3=Q4
    q_end_month = (quarter_idx + 1) * 3            # 3, 6, 9, 12
    _, last_q = calendar.monthrange(today.year, q_end_month)
    end_of_quarter = date(today.year, q_end_month, last_q)

    end_of_half = date(today.year, 6, 30) if today.month <= 6 else date(today.year, 12, 31)
    end_of_year = date(today.year, 12, 31)

    return {
        "this_month":   end_of_month,
        "this_quarter": end_of_quarter,
        "this_half":    end_of_half,
        "this_year":    end_of_year,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _egp(v: float) -> str:
    return f"{v:>24,.2f} EGP"


def _bucket_domain(today_str: str, end_str: str) -> list:
    return [
        ("state", "=", "post"),
        ("payment_state", "in", ["unpaid", "partial"]),
        ("date", ">=", today_str),
        ("date", "<=", end_str),
    ]


# ── Main async function ───────────────────────────────────────────────────────

async def main() -> None:

    run_at       = datetime.now(timezone.utc)
    today_cairo  = datetime.now(_LA_VERDE_TZ).date()
    today_str    = today_cairo.isoformat()
    bucket_ends  = _compute_bucket_ends(today_cairo)

    # ─────────────────────────────────────────────────────────────────────────
    # Section 0 — Header / Setup
    # ─────────────────────────────────────────────────────────────────────────
    print(_SEP)
    print("  KPI 7 — Expected Collections Forecast — Phase 0 Discovery")
    print(f"  Run at (UTC) : {run_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  Today (Cairo): {today_str}")
    print(f"  ALLOWED_METHODS : {sorted(ALLOWED_METHODS)}")
    print("  READ-ONLY. No writes. No OpenAI. AI cost = $0.00")
    print(_SEP)
    print()
    print("  KPI 7 domain uses rs.installment.date which Phase 2 §2 confirmed is type 'date'.")
    print("  Bucket boundaries therefore use plain ISO strings — no UTC conversion required.")
    print("  Africa/Cairo timezone is used only to compute 'today' (cache-key midnight safety).")
    print()
    print("  Computed bucket end dates (Cairo calendar arithmetic):")
    for name, end in bucket_ends.items():
        print(f"    {name:<14}: {today_str}  →  {end.isoformat()}")

    if bucket_ends["this_quarter"] == bucket_ends["this_half"]:
        print()
        print(
            f"  {_INFO} this_quarter and this_half both end on "
            f"{bucket_ends['this_quarter'].isoformat()}."
        )
        print("       This is expected nesting collapse: Q2 ends Jun 30, H1 also ends Jun 30.")
        print("       The two buckets will return identical Odoo UI counts — this is correct.")
    print()

    # ─────────────────────────────────────────────────────────────────────────
    async with OdooClient() as client:

        # ─────────────────────────────────────────────────────────────────────
        # Section 1 — rs.installment.date field type confirmation (D0.3)
        # ─────────────────────────────────────────────────────────────────────
        print(_SEP)
        print("SECTION 1 — rs.installment.date field type confirmation (D0.3)")
        print(_SEP)
        print()

        t0 = time.monotonic()
        fields_info = await client.execute_kw(
            _MODEL, "fields_get",
            args=[["date"]],
            kwargs={"attributes": ["type", "string", "required"]},
        )
        ms_s1 = int((time.monotonic() - t0) * 1000)

        date_field  = fields_info.get("date", {})
        field_type  = date_field.get("type",     "UNKNOWN")
        field_label = date_field.get("string",   "UNKNOWN")
        field_req   = date_field.get("required", False)

        print(f"  Model    : {_MODEL}")
        print(f"  Field    : date")
        print(f"  Label    : {field_label}")
        print(f"  Type     : {field_type}")
        print(f"  Required : {field_req}")
        print(f"  RPC time : {ms_s1} ms")
        print()

        if field_type == "date":
            print(f"  {_PASS} Field type confirmed as 'date' (not 'datetime').")
            print("       → Bucket boundary domains use plain ISO date strings.")
            print("       → _tz_period_bounds() is NOT needed for KPI 7 (unlike KPI 6).")
            print("       → Phase 2 §2 Dependency #1 status: CONFIRMED.")
        elif field_type == "datetime":
            print(f"  {_STOP} BLOCKING — field type is 'datetime', NOT 'date'!")
            print("       → Spec §4.4 ISO date string domains would be incorrect.")
            print("       → KPI 7 domains require UTC conversion (same as KPI 6).")
            print("       → Stop. Escalate to Khaled before writing any Phase 1 code.")
            raise SystemExit(1)
        else:
            print(f"  {_FLAG} Unexpected field type: {field_type!r}. Investigate before Phase 1.")
        print()

        # ─────────────────────────────────────────────────────────────────────
        # Section 2 — Field-to-field domain comparison test (D0.2)
        # ─────────────────────────────────────────────────────────────────────
        print(_SEP)
        print("SECTION 2 — Field-to-field domain comparison test (D0.2)")
        print(_SEP)
        print()
        print("  Spec §4.6 cheques_drill_down_domain includes:")
        print("    ('paid_amount', '>', 'x_studio_actual_paid_amount')")
        print("  Standard Odoo domain syntax expects a literal right-hand operand.")
        print("  This test determines whether Odoo accepts a field-name string as the RHS.")
        print()

        # Test 1b — control: posted records with any paid_amount > 0
        domain_1b = [("state", "=", "post"), ("paid_amount", ">", 0)]
        print(f"  Test 1b (control baseline)")
        print(f"    domain : {domain_1b}")
        t0 = time.monotonic()
        count_1b: int = await client.execute_kw(
            _MODEL, "search_count", args=[domain_1b], kwargs={},
        )
        ms_1b = int((time.monotonic() - t0) * 1000)
        print(f"    result : {count_1b:,} records  ({ms_1b} ms)")
        print(f"             posted installments with any paid_amount — sanity baseline")
        print()

        # Test 1a — field-to-field: paid_amount > x_studio_actual_paid_amount
        domain_1a = [("state", "=", "post"), ("paid_amount", ">", "x_studio_actual_paid_amount")]
        print(f"  Test 1a (field-to-field comparison)")
        print(f"    domain : {domain_1a}")

        field_to_field_works = False
        count_1a: "int | None" = None
        exception_1a: "str | None" = None

        t0 = time.monotonic()
        try:
            count_1a = await client.execute_kw(
                _MODEL, "search_count", args=[domain_1a], kwargs={},
            )
            ms_1a = int((time.monotonic() - t0) * 1000)
            print(f"    result : {count_1a:,} records  ({ms_1a} ms)")
            print()

            if count_1a > count_1b:
                verdict = "BROKEN — count exceeds control baseline (literal string matched all records)"
            elif count_1a == count_1b:
                verdict = "LIKELY BROKEN — equals control baseline (no filtering occurred)"
            elif count_1a == 0 and count_1b > 0:
                verdict = "LIKELY BROKEN — returned 0 when control has records (field name treated as literal non-existent value?)"
            else:
                verdict = "WORKS — plausible cheques subset (0 < count_1a < count_1b)"
                field_to_field_works = True

            lbl = _PASS if field_to_field_works else _FLAG
            print(f"    {lbl} Assessment: {verdict}")

        except Exception as exc:
            ms_1a = int((time.monotonic() - t0) * 1000)
            exception_1a = str(exc)
            print(f"    {_FLAG} Exception raised ({ms_1a} ms): {exception_1a}")
            print("         → Odoo rejected the domain. Field-to-field comparison is BROKEN.")

        print()
        if field_to_field_works:
            print(f"  CONCLUSION: Field-to-field domain comparison WORKS.")
            print(f"    count_1a ({count_1a:,}) is a plausible subset of count_1b ({count_1b:,}).")
            print()
            print("  Phase 1 plan:")
            print("    cheques aggregate domain = bucket_domain + ('paid_amount','>','x_studio_actual_paid_amount')")
            print("    read_group fields = ['paid_amount','x_studio_actual_paid_amount']")
            print("    cheques_in_pipeline = SUM(paid_amount) - SUM(x_studio_actual_paid_amount)")
            print("    cheques_record_count = __count from that read_group")
        else:
            print("  CONCLUSION: Field-to-field comparison DOES NOT WORK.")
            print()
            print("  Three alternative approaches for Phase 1:")
            print()
            print("  Alternative A — Python-side filter (search_read + Python sum):")
            print("    Fetch bucket installments: search_read(['paid_amount','x_studio_actual_paid_amount']).")
            print("    Python filter: records where paid_amount > x_studio_actual_paid_amount.")
            print("    cheques_in_pipeline = sum(p - a for p, a in records if p > a).")
            print("    PRO: exact formula, exact cheques_record_count.")
            print("    CON: data transfer scales with bucket size. Cap at 10,000 records; FLAG if exceeded.")
            print()
            print("  Alternative B — read_group net formula (no per-record filter):")
            print("    read_group on bucket domain, fields=['paid_amount','x_studio_actual_paid_amount'].")
            print("    cheques_in_pipeline = max(SUM(paid) - SUM(actual), 0).")
            print("    PRO: 1 RPC per bucket, same formula as KPI 3 (portfolio-wide).")
            print("    CON: cheques_record_count unavailable; may differ from Alt A if anomalous records exist.")
            print()
            print("  Alternative C — Two-query intersection:")
            print("    Q1: IDs in bucket. Q2: IDs where paid_amount > 0 AND actual < paid (separate filter).")
            print("    Intersect in Python, sum differences.")
            print("    PRO: exact, avoids field-to-field syntax.")
            print("    CON: 2 RPCs per bucket (8 extra RPCs total).")
            print()
            print("  RECOMMENDATION: Alternative B for the service aggregate RPC (matches KPI 3 pattern,")
            print("    1 RPC per bucket, cost consistent with spec §4.7 '8 read_group calls').")
            print("    cheques_record_count reported as None/omitted when Alt B is used.")
            print("    Section 4b uses Alternative B for the discovery baseline.")
        print()

        # ─────────────────────────────────────────────────────────────────────
        # Section 3 — Bucket boundary arithmetic and counts (D0.4)
        # ─────────────────────────────────────────────────────────────────────
        print(_SEP)
        print("SECTION 3 — Bucket boundary arithmetic and counts (D0.4)")
        print(_SEP)
        print()
        print(f"  today (Cairo): {today_str}")
        print()

        bucket_results: dict = {}
        nesting_ok = True

        for name in _BUCKET_NAMES:
            end     = bucket_ends[name]
            domain  = _bucket_domain(today_str, end.isoformat())
            t0      = time.monotonic()
            rows    = await client.execute_kw(
                _MODEL, "read_group",
                args=[domain, ["amount", "due_amount"], []],
                kwargs={"lazy": False},
            )
            ms = int((time.monotonic() - t0) * 1000)

            row        = rows[0] if rows else {}
            amount     = float(row.get("amount")     or 0.0)
            due_amount = float(row.get("due_amount") or 0.0)
            count      = int(row.get("__count")      or 0)

            bucket_results[name] = {
                "end": end, "amount": amount, "due_amount": due_amount, "count": count,
            }

            print(f"  ── {name}  ({today_str} → {end.isoformat()})  [{ms} ms]")
            print(f"     domain       : {domain}")
            print(f"     record_count : {count:,}")
            print(f"     SUM(amount)  : {_egp(amount)}")
            print(f"     SUM(due_amt) : {_egp(due_amount)}")
            print()

        # Nesting invariant
        print("  Nesting invariant — month ≤ quarter ≤ half ≤ year:")
        for i in range(len(_BUCKET_NAMES) - 1):
            a_n, b_n   = _BUCKET_NAMES[i], _BUCKET_NAMES[i + 1]
            a_r, b_r   = bucket_results[a_n], bucket_results[b_n]
            amt_ok     = a_r["amount"] <= b_r["amount"]
            cnt_ok     = a_r["count"]  <= b_r["count"]
            pair_ok    = amt_ok and cnt_ok
            lbl        = _PASS if pair_ok else _FLAG
            print(f"  {lbl} {a_n} ≤ {b_n}")
            print(f"       amount : {a_r['amount']:>20,.2f}  ≤  {b_r['amount']:>20,.2f}  "
                  f"{'✓' if amt_ok else '✗ VIOLATED'}")
            print(f"       count  : {a_r['count']:>14,}  ≤  {b_r['count']:>14,}  "
                  f"{'✓' if cnt_ok else '✗ VIOLATED'}")
            if not pair_ok:
                nesting_ok = False

        print()
        if nesting_ok:
            print(f"  {_PASS} All nesting invariants satisfied.")
        else:
            print(f"  {_FLAG} NESTING INVARIANT FAILED — investigate before Phase 1.")
        print()

        # ─────────────────────────────────────────────────────────────────────
        # Section 3b — Mutual exclusivity sanity check
        # ─────────────────────────────────────────────────────────────────────
        print(_SEP)
        print("SECTION 3b — KPI 2 / KPI 7 mutual exclusivity sanity check")
        print(_SEP)
        print()
        print("  Spec §4.4: KPI 2 uses date < today (past-due), KPI 7 uses date >= today (future).")
        print("  These two date clauses are mutually exclusive by construction.")
        print("  The impossible intersection domain must return exactly 0 records.")
        print()

        impossible_domain = [
            ("state", "=", "post"),
            ("payment_state", "in", ["unpaid", "partial"]),
            ("date", "<", today_str),
            ("date", ">=", today_str),
        ]
        print(f"  Domain : {impossible_domain}")

        t0 = time.monotonic()
        impossible_count: int = await client.execute_kw(
            _MODEL, "search_count", args=[impossible_domain], kwargs={},
        )
        ms_imp = int((time.monotonic() - t0) * 1000)

        print(f"  Result : {impossible_count:,} records  ({ms_imp} ms)")
        print()

        if impossible_count == 0:
            print(f"  {_PASS} Mutual exclusivity confirmed — 0 records match the impossible domain.")
            print("       Spec §4.4 claim verified: KPI 2 ∩ KPI 7 = ∅.")
        else:
            print(f"  {_STOP} BLOCKING — {impossible_count:,} records match the impossible domain!")
            print("       date < today AND date >= today cannot both be true simultaneously.")
            print("       This indicates a structural problem with Odoo date comparison semantics.")
            print("       Stop. Report to Khaled before proceeding to Section 4.")
            raise SystemExit(1)
        print()

        # ─────────────────────────────────────────────────────────────────────
        # Section 4 — Khaled cross-check baseline (D0.5)
        # ─────────────────────────────────────────────────────────────────────
        print(_SEP)
        print("SECTION 4 — Khaled cross-check baseline (D0.5)")
        print("            Paste these filter strings into the Odoo UI to verify.")
        print(_SEP)
        print()
        print("  Odoo UI path: Collections Mgmt → All Installments")
        print("  Apply filters: Status = Posted AND Payment Status IN [Unpaid, Partially Paid]")
        print("  Then add date filter per bucket below and check Pivot → SUM(Amount).")
        print()

        for name in _BUCKET_NAMES:
            res = bucket_results[name]
            print(f"  [{name}]")
            print(f"    Filter : date >= {today_str} AND date <= {res['end'].isoformat()}")
            print(f"    Expected record_count : {res['count']:,}")
            print(f"    Expected SUM(amount)  : {res['amount']:,.2f} EGP")
            print()

        if bucket_ends["this_quarter"] == bucket_ends["this_half"]:
            print(
                f"  {_INFO} this_quarter and this_half end on the same date "
                f"({bucket_ends['this_quarter'].isoformat()})."
            )
            print("       The Odoo UI will return identical results for both.")
            print("       This is expected — nesting collapse is correct behaviour in May 2026.")
            print()

        # ─────────────────────────────────────────────────────────────────────
        # Section 4b — Cheques baseline per bucket
        # ─────────────────────────────────────────────────────────────────────
        print(_SEP)
        print("SECTION 4b — Cheques baseline per bucket")
        print(_SEP)
        print()

        cheques_results: dict = {}

        if field_to_field_works:
            print("  Test 1a PASSED → cheques aggregate uses field-to-field domain filter.")
            print("  Approach: read_group on bucket_domain + ('paid_amount','>','x_studio_actual_paid_amount')")
            print("  Fields  : ['paid_amount', 'x_studio_actual_paid_amount']")
            print()

            for name in _BUCKET_NAMES:
                res = bucket_results[name]
                cheques_domain = _bucket_domain(today_str, res["end"].isoformat()) + [
                    ("paid_amount", ">", "x_studio_actual_paid_amount"),
                ]
                t0 = time.monotonic()
                rows = await client.execute_kw(
                    _MODEL, "read_group",
                    args=[cheques_domain, ["paid_amount", "x_studio_actual_paid_amount"], []],
                    kwargs={"lazy": False},
                )
                ms = int((time.monotonic() - t0) * 1000)

                row    = rows[0] if rows else {}
                crec   = int(row.get("__count")                         or 0)
                paid   = float(row.get("paid_amount")                   or 0.0)
                actual = float(row.get("x_studio_actual_paid_amount")   or 0.0)
                cip    = paid - actual
                pct    = (cip / res["amount"] * 100) if res["amount"] else 0.0

                cheques_results[name] = {
                    "approach": "field-to-field",
                    "cheques_record_count": crec,
                    "paid_sum":  paid,
                    "actual_sum": actual,
                    "cheques_in_pipeline": cip,
                    "pct": pct,
                }

                print(f"  ── {name}  ({today_str} → {res['end'].isoformat()})  [{ms} ms]")
                print(f"     cheques_record_count  : {crec:,}")
                print(f"     SUM(paid_amount)      : {_egp(paid)}")
                print(f"     SUM(actual_paid)      : {_egp(actual)}")
                print(f"     cheques_in_pipeline   : {_egp(cip)}")
                print(f"     % of bucket amount    : {pct:.2f}%")
                if cip < 0:
                    print(f"     {_FLAG} negative cheques_in_pipeline — data_quality_warning would fire in Phase 1!")
                if res["amount"] > 0 and cip > res["amount"]:
                    print(f"     {_FLAG} cheques_in_pipeline > bucket amount — data_quality_warning would fire!")
                print()

        else:
            # Alternative B: read_group net formula on bucket domain (no per-record filter)
            print("  Test 1a FAILED → using Alternative B for cheques baseline.")
            print("  Formula: cheques_in_pipeline = SUM(paid_amount) − SUM(x_studio_actual_paid_amount)")
            print("  Note   : This matches KPI 3's portfolio-wide formula (Decision 4.5),")
            print("           scoped to the bucket domain. Per Decision 4.5, the formula equals")
            print("           the per-record clamped sum when no anomalous records exist in the bucket.")
            print("           cheques_record_count is not available via this approach.")
            print()

            for name in _BUCKET_NAMES:
                res    = bucket_results[name]
                domain = _bucket_domain(today_str, res["end"].isoformat())
                t0     = time.monotonic()
                rows   = await client.execute_kw(
                    _MODEL, "read_group",
                    args=[domain, ["paid_amount", "x_studio_actual_paid_amount"], []],
                    kwargs={"lazy": False},
                )
                ms = int((time.monotonic() - t0) * 1000)

                row    = rows[0] if rows else {}
                paid   = float(row.get("paid_amount")                   or 0.0)
                actual = float(row.get("x_studio_actual_paid_amount")   or 0.0)
                cip    = paid - actual    # net; may be negative (data anomaly)
                pct    = (cip / res["amount"] * 100) if res["amount"] else 0.0

                cheques_results[name] = {
                    "approach": "Alternative B",
                    "cheques_record_count": None,
                    "paid_sum":  paid,
                    "actual_sum": actual,
                    "cheques_in_pipeline": cip,
                    "pct": pct,
                }

                print(f"  ── {name}  ({today_str} → {res['end'].isoformat()})  [{ms} ms]")
                print(f"     approach             : Alternative B — read_group net formula")
                print(f"     SUM(paid_amount)     : {_egp(paid)}")
                print(f"     SUM(actual_paid)     : {_egp(actual)}")
                print(f"     cheques_in_pipeline  : {_egp(cip)}  (net — negative = data anomaly)")
                print(f"     % of bucket amount   : {pct:.2f}%")
                if cip < 0:
                    print(f"     {_FLAG} negative net — actual_paid > paid_amount. Data anomaly in Odoo Studio fields.")
                print()

        # ── Markdown cross-check table (24 numbers × 4 buckets) ───────────────
        print(_SEP2)
        print("  KHALED CROSS-CHECK SHEET  (paste verbatim into docs/KPI7_DISCOVERY_FINDINGS.md)")
        print(_SEP2)
        print()
        approach_note = "field-to-field" if field_to_field_works else "Alternative B"
        print(f"  Cheques approach: {approach_note}")
        print()

        # Header
        h = (
            f"  | {'Bucket':<14} | {'Start':<10} | {'End':<10} "
            f"| {'Records':>8} | {'Amount EGP':>18} | {'Due Amt EGP':>18} "
            f"| {'Cheques EGP':>18} | {'Chq Recs':>8} | {'Chq %':>7} |"
        )
        sep_row = f"  |{'-'*15}--|{'-'*11}--|{'-'*11}--|{'-'*9}--|{'-'*19}--|{'-'*19}--|{'-'*19}--|{'-'*9}--|{'-'*8}--|"
        print(h)
        print(sep_row)

        for name in _BUCKET_NAMES:
            res  = bucket_results[name]
            cr   = cheques_results[name]
            cip  = cr["cheques_in_pipeline"]
            pct  = cr["pct"]
            crec = cr["cheques_record_count"]
            crec_str = f"{crec:>8,}" if crec is not None else "     N/A"
            print(
                f"  | {name:<14} | {today_str} | {res['end'].isoformat()} "
                f"| {res['count']:>8,} | {res['amount']:>18,.2f} | {res['due_amount']:>18,.2f} "
                f"| {cip:>18,.2f} | {crec_str} | {pct:>6.2f}% |"
            )
        print()

        # ─────────────────────────────────────────────────────────────────────
        # Section 5 — Summary + PHASE 0 COMPLETE
        # ─────────────────────────────────────────────────────────────────────
        print(_SEP)
        print("SECTION 5 — Summary of Phase 0 Findings")
        print(_SEP)
        print()

        print("  D0.3 — rs.installment.date field type:")
        print(f"    type = '{field_type}'  →  {_PASS if field_type == 'date' else _FLAG}")
        print("    ISO string domains work directly. No UTC conversion needed for KPI 7.")
        print()

        print("  D0.2 — Field-to-field domain comparison:")
        if field_to_field_works:
            print(f"    Test 1a PASSED.  {_PASS}")
            print(f"    count_1a = {count_1a:,}  (plausible cheques subset)")
            print(f"    count_1b = {count_1b:,}  (all paid > 0, control baseline)")
            print("    Phase 1: use ('paid_amount','>','x_studio_actual_paid_amount') in cheques domain.")
        else:
            print(f"    Test 1a FAILED.  {_FLAG}")
            if count_1a is not None:
                print(f"    count_1a = {count_1a:,}  count_1b = {count_1b:,}")
            if exception_1a:
                print(f"    Exception: {exception_1a}")
            print("    Phase 1: use Alternative B (read_group net formula).")
            print("    UNKNOWN U1: field-to-field comparison broken — document in findings.")
        print()

        print("  D0.4 — Bucket boundary arithmetic and counts:")
        for name in _BUCKET_NAMES:
            res = bucket_results[name]
            print(
                f"    {name:<14}: → {res['end'].isoformat()} "
                f"| {res['count']:,} records | {res['amount']:,.2f} EGP"
            )
        print(f"    Nesting invariant : {_PASS if nesting_ok else _FLAG + ' FAILED'}")
        print()

        print("  Section 3b — KPI 2 / KPI 7 mutual exclusivity:")
        print(f"    Impossible domain count = 0.  {_PASS}")
        print()

        print("  D0.5 / Section 4b — Cheques baseline per bucket:")
        for name in _BUCKET_NAMES:
            res = bucket_results[name]
            cr  = cheques_results[name]
            print(
                f"    {name:<14}: cheques_in_pipeline = {cr['cheques_in_pipeline']:>18,.2f} EGP "
                f"({cr['pct']:.2f}% of {res['amount']:,.2f})"
            )
        print()

        print("  UNKNOWNS surfaced:")
        unknowns: list[str] = []
        if not field_to_field_works:
            unknowns.append(
                "U1: Field-to-field Odoo domain ('paid_amount','>','x_studio_actual_paid_amount') "
                "does not work. Phase 1 must use Alternative B (or A for exact cheques_record_count)."
            )
        if not nesting_ok:
            unknowns.append("U2: Nesting invariant failed — investigate data before Phase 1.")
        if unknowns:
            for u in unknowns:
                print(f"    {_FLAG} {u}")
        else:
            print(f"    None.  {_PASS}")
        print()

        # ── HARD STOP ─────────────────────────────────────────────────────────
        print(_SEP)
        print()
        print("  ████████████████████████████████████████████████████████████████████████")
        print("  PHASE 0 COMPLETE. AWAITING KHALED CROSS-CHECK AND APPROVAL TO PROCEED.")
        print("  ████████████████████████████████████████████████████████████████████████")
        print()
        print("  Khaled — please verify the baseline numbers in Odoo UI:")
        print()
        print("  1. Open: Collections Mgmt → All Installments")
        print("  2. Filters: State = Posted  AND  Payment Status = Unpaid or Partially Paid")
        print("  3. Switch to Pivot view, measure = Amount")
        print("  4. For each bucket apply the date filter and confirm record count + SUM(amount):")
        print()
        for name in _BUCKET_NAMES:
            res = bucket_results[name]
            print(f"  [{name}]")
            print(f"    date >= {today_str} AND date <= {res['end'].isoformat()}")
            print(f"    Discovery  : {res['count']:,} records  |  {res['amount']:,.2f} EGP")
            print(f"    Odoo UI    : _______ records  |  _____________ EGP  (fill in)")
            print()
        print("  5. Reply with the Odoo UI figures.")
        print("  6. If all match (±1 EGP) → reply 'approved, proceed to Phase 1'.")
        print()
        print(_SEP)


# ── Entry point ───────────────────────────────────────────────────────────────

def run() -> None:
    """Sync wrapper: sets up stdout Tee, runs async main, saves output to file."""
    output_buffer = StringIO()

    class Tee:
        def __init__(self, *streams):
            self.streams = streams

        def write(self, data: str) -> None:
            for s in self.streams:
                s.write(data)

        def flush(self) -> None:
            for s in self.streams:
                s.flush()

    original_stdout = sys.stdout
    sys.stdout = Tee(original_stdout, output_buffer)

    exit_code = 0
    try:
        asyncio.run(main())
    except SystemExit as exc:
        exit_code = int(exc.code) if exc.code is not None else 1
    except Exception as exc:
        print(f"\n  FATAL ERROR: {exc}")
        import traceback
        traceback.print_exc()
        exit_code = 1
    finally:
        sys.stdout = original_stdout
        output_text = output_buffer.getvalue()
        try:
            _OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
            _OUTPUT_FILE.write_text(output_text, encoding="utf-8")
            print(f"\n  Output saved to: {_OUTPUT_FILE}")
        except Exception as write_exc:
            print(f"\n  WARNING: could not save output file: {write_exc}")

    if exit_code != 0:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    run()
