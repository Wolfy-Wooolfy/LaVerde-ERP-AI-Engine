"""
Read-only pre-implementation discovery for KPI 6 — 6-Month Collection Trend.

Goal: Verify every assumption about rs.account.payment.installment.line
BEFORE writing service code for get_collection_trend_6m():
  - Field inventory: installment_id, payment_id, amount exist with expected types
  - Record count: establishes the full scan scale
  - Sample records: confirms model is queryable and sanity-checks field values
  - KPI 6 query: executes the exact read_group that D1 will use (unfiltered)
  - State filter test: side-by-side comparison with/without payment_id.state='post'
  - Sanity ratio: 6-month sum ÷ all-time x_studio_actual_paid_amount

Decision 5.1 (state filter) and manual cross-check approval required before D1.

This script:
  - Calls ONLY read methods (fields_get, search_count, search_read, read_group).
  - Writes nothing to Odoo.
  - Costs $0 in AI.
  - Prints no PII (no customer names, IDs, or addresses).
  - Appends TSV rows to logs/kpi6_discovery.log.
  - Exits 0 on completion regardless of findings.

Usage:
    python scripts/discover_kpi6_join_and_trend.py
"""

import asyncio
import calendar
import io
import os
import sys
from datetime import date, datetime, timezone

from backend.shared.odoo.client import OdooClient

# Force UTF-8 stdout (Windows consoles default to cp1252)
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Constants ─────────────────────────────────────────────────────────────────

_LINE_MODEL = "rs.account.payment.installment.line"
_INSTALLMENT_MODEL = "rs.installment"
_SEP  = "═" * 78
_SEP2 = "─" * 78
_LOG_FILE = "logs/kpi6_discovery.log"

# All-time SUM(x_studio_actual_paid_amount) at state='post', confirmed at
# Session 4 D0 (2026-05-16). Used for 6-month sanity ratio in Section 6.
_ALL_TIME_ACTUAL_PAID_EGP = 2_970_599_264.85

_INFO = "[INFO]"
_PASS = "[PASS]"
_FLAG = "[FLAG]"


# ── Period computation (stdlib only — no python-dateutil) ─────────────────────

def _compute_period(today: date) -> tuple[date, date]:
    """Return (period_start, period_end) for the trailing 6 calendar months.

    Includes current month as the 6th (newest) month.
    period_start = first day of (current_month − 5)
    period_end   = today

    Example: today = 2026-05-17
      start_month = 5 - 5 = 0 → wraps to December 2025 → period_start = 2025-12-01
      Months covered: Dec-25, Jan-26, Feb-26, Mar-26, Apr-26, May-26
    """
    start_month = today.month - 5
    start_year = today.year
    if start_month <= 0:
        start_month += 12
        start_year -= 1
    return date(start_year, start_month, 1), today


def _all_6_month_keys(period_start: date, period_end: date) -> list[str]:
    """Return the list of 6 YYYY-MM keys oldest→newest for the window."""
    keys = []
    y, m = period_start.year, period_start.month
    while (y, m) <= (period_end.year, period_end.month):
        keys.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return keys


def _month_label_en(year: int, month: int) -> str:
    return f"{calendar.month_abbr[month]} {year}"


# ── Domain builders ───────────────────────────────────────────────────────────

def _build_domain(period_start: date, period_end: date) -> list:
    return [
        ("payment_id.date", ">=", period_start.isoformat()),
        ("payment_id.date", "<=", period_end.isoformat() + " 23:59:59"),
    ]


def _build_domain_post(period_start: date, period_end: date) -> list:
    return _build_domain(period_start, period_end) + [
        ("payment_id.state", "=", "post"),
    ]


# ── Formatting helpers ────────────────────────────────────────────────────────

def _egp(v: float) -> str:
    return f"{v:>26,.2f} EGP"


def _parse_month_key(row: dict) -> str | None:
    """Extract the month group key from a read_group row.

    Odoo may return the groupby expression key under different names
    depending on version and field type. We try the expected key first,
    then fall back to inspect the row for any date-shaped value.

    Returns a string (whatever Odoo returns) or None if not found.
    """
    for candidate in [
        "payment_id.date:month",
        "payment_id.date",
        "payment_id:month",
    ]:
        val = row.get(candidate)
        if val is not None and val is not False:
            return str(val)
    return None


# ── TSV log ───────────────────────────────────────────────────────────────────

def _append_tsv(rows: list[dict]) -> None:
    os.makedirs("logs", exist_ok=True)
    log_exists = os.path.isfile(_LOG_FILE)
    run_at = datetime.now(timezone.utc).isoformat()
    with open(_LOG_FILE, "a", encoding="utf-8") as f:
        if not log_exists:
            f.write(
                "run_at\tmonth_label\trecord_count\t"
                "amount_sum_egp\tstate_filtered\n"
            )
        for row in rows:
            f.write(
                f"{run_at}\t{row['month_label']}\t{row['record_count']}\t"
                f"{row['amount_sum_egp']:.2f}\t{row['state_filtered']}\n"
            )
    print(f"\n{_INFO} TSV appended to {_LOG_FILE}")


# ── Main ──────────────────────────────────────────────────────────────────────

async def run() -> None:
    today = date.today()
    period_start, period_end = _compute_period(today)
    run_at = datetime.now(timezone.utc).isoformat()

    print(_SEP)
    print("KPI 6 — 6-Month Collection Trend: Join-Path and Trend Discovery")
    print(f"Run timestamp  : {run_at}")
    print(f"Today          : {today}")
    print(f"Period start   : {period_start}  (first day of current_month − 5)")
    print(f"Period end     : {period_end} 23:59:59")
    expected_keys = _all_6_month_keys(period_start, period_end)
    print(f"Expected months: {expected_keys}")
    print(_SEP)

    flags: list[str] = []
    tsv_rows: list[dict] = []

    async with OdooClient() as client:

        # ── Section 1: Field inventory ────────────────────────────────────────
        print()
        print(_SEP2)
        print(f"[1] fields_get on {_LINE_MODEL}")
        print(_SEP2)

        all_fields: dict = await client.execute_kw(
            _LINE_MODEL,
            "fields_get",
            args=[],
            kwargs={"attributes": ["string", "type", "relation"]},
        )

        # Required fields for the KPI 6 join path
        required = {
            "installment_id": ("many2one", "rs.installment"),
            "payment_id":     ("many2one", "rs.account.payment.installment"),
            "amount":         (None, None),  # monetary or float — type varies
        }

        for fname, (exp_type, exp_rel) in required.items():
            if fname not in all_fields:
                print(f"    {_FLAG} MISSING: {fname!r} not found in fields_get")
                flags.append(f"missing_required_field_{fname}")
                continue
            f = all_fields[fname]
            ftype = f.get("type", "?")
            frel  = f.get("relation", "")
            fstr  = f.get("string", "")
            status = _PASS
            detail = ""
            if exp_type and ftype != exp_type:
                status = _FLAG
                detail = f" — expected type={exp_type!r}, got {ftype!r}"
                flags.append(f"field_{fname}_wrong_type_{ftype}")
            if exp_rel and frel != exp_rel:
                status = _FLAG
                detail += f" — expected relation={exp_rel!r}, got {frel!r}"
                flags.append(f"field_{fname}_wrong_relation_{frel}")
            print(
                f"    {status} {fname}: type={ftype!r}, "
                f"relation={frel!r or '(none)'}, string={fstr!r}{detail}"
            )

        # Additional amount / relational fields (informational only)
        print()
        print("    Additional amount / relational fields on the line model:")
        for fname, finfo in sorted(all_fields.items()):
            if fname in required:
                continue
            ftype = finfo.get("type", "")
            if ftype in ("monetary", "float", "integer", "many2one"):
                frel = finfo.get("relation", "")
                fstr = finfo.get("string", "")
                print(
                    f"      {fname:<40} type={ftype!r:<12} "
                    f"relation={frel!r:<42} string={fstr!r}"
                )

        # ── Section 2: Record count ───────────────────────────────────────────
        print()
        print(_SEP2)
        print(f"[2] search_count on {_LINE_MODEL} (empty domain — all-time)")
        print(_SEP2)

        total_records: int = await client.execute_kw(
            _LINE_MODEL,
            "search_count",
            args=[[]],
        )
        print(f"    Total records (all time): {total_records:,}")
        if total_records == 0:
            flags.append("zero_records_in_line_model")
            print(f"    {_FLAG} ZERO records — cannot proceed with any trend query")
        else:
            print(f"    {_INFO} This is the full table scan ceiling before date filtering")

        # ── Section 3: Sample 3 records (sanitized) ───────────────────────────
        print()
        print(_SEP2)
        print(f"[3] Sample 3 records from {_LINE_MODEL} (sanitized — no PII)")
        print(_SEP2)

        sample_rows: list[dict] = await client.execute_kw(
            _LINE_MODEL,
            "search_read",
            args=[[]],
            kwargs={
                "fields": ["id", "installment_id", "payment_id", "amount"],
                "limit": 3,
                "order": "id desc",
            },
        )

        if not sample_rows:
            print("    (no records returned)")
        else:
            print(
                f"    {'line_id':>8}  {'installment_id':>14}  "
                f"{'payment_id':>10}  {'amount (EGP)':>18}"
            )
            print(f"    {'-'*8}  {'-'*14}  {'-'*10}  {'-'*18}")
            for row in sample_rows:
                line_id = row.get("id")
                inst_raw = row.get("installment_id")
                pay_raw  = row.get("payment_id")
                amt      = float(row.get("amount") or 0.0)
                # Sanitize: show IDs only, not display names (PII discipline)
                inst_id = inst_raw[0] if isinstance(inst_raw, (list, tuple)) else inst_raw
                pay_id  = pay_raw[0]  if isinstance(pay_raw,  (list, tuple)) else pay_raw
                print(
                    f"    {line_id:>8}  {str(inst_id):>14}  "
                    f"{str(pay_id):>10}  {amt:>18,.2f}"
                )

        # ── Section 4: KPI 6 query — unfiltered ──────────────────────────────
        print()
        print(_SEP2)
        print("[4] KPI 6 read_group — SUM(amount) grouped by payment_id.date:month")
        print("    (UNFILTERED — no payment_id.state clause)")
        print(f"    Domain: payment_id.date >= {period_start}  AND  "
              f"<= {period_end} 23:59:59")
        print(_SEP2)

        domain = _build_domain(period_start, period_end)

        raw_rows: list[dict] = await client.execute_kw(
            _LINE_MODEL,
            "read_group",
            args=[domain, ["amount:sum"], ["payment_id.date:month"]],
            kwargs={"lazy": False},
        )

        print(f"    Odoo returned {len(raw_rows)} row(s)")

        # Print raw first row to document the actual key structure Odoo uses
        if raw_rows:
            print()
            print("    Raw first row (all keys) — documents actual Odoo groupby key format:")
            for k, v in raw_rows[0].items():
                print(f"      {k!r}: {v!r}")

        print()
        print(f"    {'Month key (Odoo)':<25} {'Records':>10}  {'Amount SUM (EGP)':>26}")
        print(f"    {'-'*25} {'-'*10}  {'-'*26}")

        unfiltered_by_key: dict[str, dict] = {}
        total_unfiltered = 0.0
        total_unfiltered_count = 0

        for row in raw_rows:
            month_key = _parse_month_key(row)
            if month_key is None:
                # Fallback: print all non-dunder keys so we can debug
                non_dunder = {k: v for k, v in row.items() if not k.startswith("__")}
                print(f"    {_FLAG} Could not parse month key from row: {non_dunder}")
                flags.append("cannot_parse_month_key")
                continue
            amt   = float(row.get("amount") or 0.0)
            count = int(row.get("__count") or 0)
            unfiltered_by_key[month_key] = {"amount": amt, "count": count}
            total_unfiltered       += amt
            total_unfiltered_count += count
            print(f"    {month_key:<25} {count:>10,}  {amt:>24,.2f} EGP")

        print(f"    {'-'*25} {'-'*10}  {'-'*26}")
        print(f"    {'TOTAL':<25} {total_unfiltered_count:>10,}  "
              f"{total_unfiltered:>24,.2f} EGP")

        # Check expected months present / missing
        print()
        for ym in expected_keys:
            if ym in unfiltered_by_key:
                print(f"    {_PASS} Month {ym} present in result")
            else:
                print(f"    {_INFO} Month {ym} absent — zero payments for this month (will be zero-padded in service)")

        # ── Section 5: State filter test ──────────────────────────────────────
        print()
        print(_SEP2)
        print("[5] State filter test — same domain + ('payment_id.state', '=', 'post')")
        print("    Side-by-side comparison — even 1 EGP delta is reported explicitly")
        print(_SEP2)

        domain_post = _build_domain_post(period_start, period_end)

        raw_rows_post: list[dict] = await client.execute_kw(
            _LINE_MODEL,
            "read_group",
            args=[domain_post, ["amount:sum"], ["payment_id.date:month"]],
            kwargs={"lazy": False},
        )

        print(f"    Odoo returned {len(raw_rows_post)} row(s) with state='post' filter")
        print()

        filtered_by_key: dict[str, dict] = {}
        total_filtered = 0.0
        total_filtered_count = 0

        for row in raw_rows_post:
            month_key = _parse_month_key(row)
            if month_key is None:
                continue
            amt   = float(row.get("amount") or 0.0)
            count = int(row.get("__count") or 0)
            filtered_by_key[month_key] = {"amount": amt, "count": count}
            total_filtered       += amt
            total_filtered_count += count

        # Side-by-side table
        col1 = 25   # month key
        col2 = 10   # unfiltered records
        col3 = 18   # unfiltered amount
        col4 = 10   # post-filtered records
        col5 = 18   # post-filtered amount
        col6 = 16   # delta

        header = (
            f"    {'Month':<{col1}} {'Rec(unfiltered)':>{col2}}  "
            f"{'Amt(unfiltered)':>{col3}}  "
            f"{'Rec(post)':>{col4}}  {'Amt(post)':>{col5}}  "
            f"{'Delta EGP':>{col6}}"
        )
        sep_line = (
            f"    {'-'*col1} {'-'*col2}  {'-'*col3}  "
            f"{'-'*col4}  {'-'*col5}  {'-'*col6}"
        )
        print(header)
        print(sep_line)

        all_keys = sorted(set(list(unfiltered_by_key) + list(filtered_by_key)))
        any_delta = False

        for key in all_keys:
            uf = unfiltered_by_key.get(key, {"amount": 0.0, "count": 0})
            ft = filtered_by_key.get(key, {"amount": 0.0, "count": 0})
            delta = uf["amount"] - ft["amount"]
            delta_str = f"{delta:+,.2f}" if abs(delta) >= 0.005 else "0.00"
            if abs(delta) >= 0.005:
                any_delta = True
            print(
                f"    {key:<{col1}} {uf['count']:>{col2},}  "
                f"{uf['amount']:>{col3},.2f} EGP  "
                f"{ft['count']:>{col4},}  {ft['amount']:>{col5},.2f} EGP  "
                f"{delta_str:>{col6}}"
            )

        overall_delta = total_unfiltered - total_filtered
        overall_delta_str = f"{overall_delta:+,.2f}" if abs(overall_delta) >= 0.005 else "0.00"
        print(sep_line)
        print(
            f"    {'TOTAL':<{col1}} {total_unfiltered_count:>{col2},}  "
            f"{total_unfiltered:>{col3},.2f} EGP  "
            f"{total_filtered_count:>{col4},}  {total_filtered:>{col5},.2f} EGP  "
            f"{overall_delta_str:>{col6}}"
        )

        print()
        if abs(overall_delta) < 0.01:
            print(f"    {_PASS} Delta = 0.00 EGP — all 6-month line records belong to "
                  "posted payment headers.")
            print(f"    {_INFO} Decision 5.1 candidate: state='post' filter has ZERO IMPACT on totals.")
            print(f"    {_INFO} Including it adds defence-in-depth at zero cost; excluding it saves one domain clause.")
        elif abs(overall_delta) < 1_000:
            any_delta = True
            print(f"    {_FLAG} Small delta: {overall_delta:+,.2f} EGP — "
                  "a handful of non-post records in the 6-month window.")
            print(f"    {_INFO} Decision 5.1: recommend adding state='post' for correctness.")
            flags.append(f"state_filter_small_delta_{overall_delta:+.2f}_egp")
        else:
            any_delta = True
            pct = abs(overall_delta) / max(total_unfiltered, 0.01) * 100
            print(f"    {_FLAG} Material delta: {overall_delta:+,.2f} EGP ({pct:.2f}% of unfiltered) — "
                  "non-post records contribute meaningfully.")
            print(f"    {_INFO} Decision 5.1: state='post' filter REQUIRED.")
            flags.append(f"state_filter_material_delta_{overall_delta:+.2f}_egp")

        # ── Section 6: All-time sanity ratio ──────────────────────────────────
        print()
        print(_SEP2)
        print("[6] Sanity ratio: 6-month unfiltered SUM ÷ all-time x_studio_actual_paid_amount")
        print(_SEP2)

        ratio_pct = (
            total_unfiltered / _ALL_TIME_ACTUAL_PAID_EGP * 100
            if _ALL_TIME_ACTUAL_PAID_EGP else 0.0
        )

        print(f"    6-month SUM (unfiltered)          : {_egp(total_unfiltered)}")
        print(f"    6-month SUM (state='post')        : {_egp(total_filtered)}")
        print(f"    All-time actual_paid (state=post) : {_egp(_ALL_TIME_ACTUAL_PAID_EGP)}")
        print(f"    Ratio (unfiltered / all-time)     : {ratio_pct:.2f}%")
        print()

        if ratio_pct < 5.0:
            flags.append(f"ratio_low_{ratio_pct:.1f}pct")
            print(f"    {_FLAG} Ratio {ratio_pct:.2f}% is BELOW the 5% lower bound.")
            print("          Possible causes:")
            print("          (a) payment_id.date is NOT the payment posting date in this Odoo instance")
            print("          (b) La Verde entered very few payments in the last 6 months")
            print("          (c) the line model's 'amount' field is not the collected amount")
            print("          Investigate before proceeding to D1.")
        elif ratio_pct > 30.0:
            flags.append(f"ratio_high_{ratio_pct:.1f}pct")
            print(f"    {_FLAG} Ratio {ratio_pct:.2f}% is ABOVE the 30% upper bound.")
            print("          Possible causes:")
            print("          (a) line model counts the same payment for multiple installments")
            print("          (b) 'amount' field stores something other than per-payment collected amount")
            print("          (c) La Verde had unusually high collections in the last 6 months")
            print("          Investigate before proceeding to D1.")
        else:
            print(f"    {_PASS} Ratio {ratio_pct:.2f}% is within expected range [5%, 30%].")
            print("          The 6-month trend query is reading the correct scale and field.")

        # ── Section 7: Discovery Summary ──────────────────────────────────────
        print()
        print(_SEP)
        print("DISCOVERY SUMMARY")
        print(_SEP)
        print()
        print(f"  Model                     : {_LINE_MODEL}")
        print(f"  Total records (all time)  : {total_records:,}")
        print(f"  Period                    : {period_start}  →  {period_end}")
        print(f"  6-month rows returned     : {len(raw_rows)}  (unfiltered)")
        print(f"  6-month record count      : {total_unfiltered_count:,}  (unfiltered)")
        print(f"  6-month total (unfiltered): {_egp(total_unfiltered)}")
        print(f"  6-month total (post only) : {_egp(total_filtered)}")
        print(f"  State-filter delta        : {overall_delta:+,.2f} EGP")
        print(f"  Sanity ratio              : {ratio_pct:.2f}%  (vs all-time actual_paid)")
        print()

        # Per-month table for manual cross-check
        col_m, col_r, col_a, col_b = 22, 12, 26, 26
        print(
            f"  {'Month':<{col_m}} {'Unfiltered Rec':>{col_r}}  "
            f"{'Unfiltered Amount':>{col_a}}  {'Post-filtered Amount':>{col_b}}"
        )
        print(
            f"  {'-'*col_m} {'-'*col_r}  {'-'*col_a}  {'-'*col_b}"
        )
        for ym in sorted(set(list(unfiltered_by_key) + list(filtered_by_key))):
            uf = unfiltered_by_key.get(ym, {"amount": 0.0, "count": 0})
            ft = filtered_by_key.get(ym, {"amount": 0.0, "count": 0})
            print(
                f"  {ym:<{col_m}} {uf['count']:>{col_r},}  "
                f"{uf['amount']:>{col_a},.2f} EGP  {ft['amount']:>{col_b},.2f} EGP"
            )
        print(
            f"  {'-'*col_m} {'-'*col_r}  {'-'*col_a}  {'-'*col_b}"
        )
        print(
            f"  {'TOTAL':<{col_m}} {total_unfiltered_count:>{col_r},}  "
            f"{total_unfiltered:>{col_a},.2f} EGP  {total_filtered:>{col_b},.2f} EGP"
        )
        print()

        # Flags summary
        if flags:
            print(f"  {_FLAG} FLAGS raised ({len(flags)}):")
            for f in flags:
                print(f"      - {f}")
            print(f"  {_FLAG} DO NOT proceed to D1 until all flags are reviewed by Khaled.")
        else:
            print(f"  {_PASS} No flags raised.")

        print()
        print("  ─── MANUAL CROSS-CHECK (REQUIRED before D1) ─────────────────────────")
        print()
        print("  Open Odoo → RS Accounting → Payment Installments (or the view that")
        print("  shows rs.account.payment.installment.line records with posting dates)")
        print()
        print("  Recommended: pick the most recent COMPLETE calendar month from the")
        print("  table above (e.g., April 2026 if today is May).")
        print()
        print("  Steps:")
        print("    1. Filter payment records to that month using the posting date")
        print("    2. Sum the 'amount' column for that month's line records")
        print("    3. Compare to the 'Unfiltered Amount' in the table above for that month")
        print("    4. Identity-equal match (or explain any delta) required before D1")
        print()
        print("  After confirming:")
        print("    - Approve Decision 5.1 (state filter: include or exclude non-post headers)")
        print("    - Reply 'proceed with D1' to start the service implementation")
        print()
        print(_SEP)

        # ── Build TSV rows ─────────────────────────────────────────────────────
        for ym in sorted(unfiltered_by_key):
            uf = unfiltered_by_key[ym]
            tsv_rows.append({
                "month_label": ym,
                "record_count": uf["count"],
                "amount_sum_egp": uf["amount"],
                "state_filtered": "no",
            })
        for ym in sorted(filtered_by_key):
            ft = filtered_by_key[ym]
            tsv_rows.append({
                "month_label": ym,
                "record_count": ft["count"],
                "amount_sum_egp": ft["amount"],
                "state_filtered": "post",
            })

    _append_tsv(tsv_rows)


if __name__ == "__main__":
    asyncio.run(run())
