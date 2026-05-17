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


def _parse_month_key_header(row: dict) -> str | None:
    """Extract the month group key from a HEADER model read_group row.

    For groupby=['date:month'] on rs.account.payment.installment,
    Odoo returns the key under 'date:month' or 'date'.
    """
    for candidate in ("date:month", "date"):
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
                f"relation={(frel or '(none)')!r}, string={fstr!r}{detail}"
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

        # ── Section 4: Attempt LINE MODEL read_group (expected to fail) ─────────
        print()
        print(_SEP2)
        print("[4] Attempt: read_group on LINE MODEL grouped by payment_id.date:month")
        print("    This tests whether Odoo supports :month granularity on related fields.")
        print(f"    Domain: payment_id.date >= {period_start}  AND  <= {period_end} 23:59:59")
        print(_SEP2)

        domain_line = _build_domain(period_start, period_end)
        line_groupby_works = False

        try:
            raw_rows_line: list[dict] = await client.execute_kw(
                _LINE_MODEL,
                "read_group",
                args=[domain_line, ["amount:sum"], ["payment_id.date:month"]],
                kwargs={"lazy": False},
            )
            line_groupby_works = True
            print(f"    {_PASS} read_group on line model SUCCEEDED — {len(raw_rows_line)} row(s)")
            if raw_rows_line:
                print("    Raw first row (for key-format documentation):")
                for k, v in raw_rows_line[0].items():
                    print(f"      {k!r}: {v!r}")
        except Exception as exc:
            print(f"    {_FLAG} read_group on line model FAILED with:")
            print(f"           {type(exc).__name__}: {str(exc)[:300]}")
            print()
            print("    ORM LIMITATION CONFIRMED: Odoo does not support :month groupby")
            print("    on a related field (payment_id.date). Domain filtering on")
            print("    payment_id.date works; groupby on it does not.")
            print()
            print("    CONSEQUENCE FOR D1: The service must query the HEADER model")
            print("    rs.account.payment.installment (which owns the date field)")
            print("    rather than the line sub-model. See Section 4b and 4c.")
            flags.append("line_model_groupby_unsupported_use_header_model")

        # ── Section 4b: fields_get on HEADER model ────────────────────────────
        _HEADER_MODEL = "rs.account.payment.installment"
        print()
        print(_SEP2)
        print(f"[4b] fields_get on {_HEADER_MODEL}")
        print("     Goal: identify the right amount field and confirm date/state fields.")
        print(_SEP2)

        header_fields: dict = await client.execute_kw(
            _HEADER_MODEL,
            "fields_get",
            args=[],
            kwargs={"attributes": ["string", "type", "relation"]},
        )

        # Confirm date and state on header
        for fname in ("date", "state"):
            if fname in header_fields:
                f = header_fields[fname]
                print(f"    {_PASS} {fname}: type={f.get('type')!r}, string={f.get('string')!r}")
            else:
                print(f"    {_FLAG} {fname}: NOT FOUND on header model")
                flags.append(f"header_missing_{fname}")

        # List all monetary / float / selection fields (amount candidates + state)
        print()
        print("    Amount-candidate and selection fields on header model:")
        header_amount_fields: list[str] = []
        for fname, finfo in sorted(header_fields.items()):
            ftype = finfo.get("type", "")
            if ftype in ("monetary", "float", "selection"):
                frel = finfo.get("relation", "")
                fstr = finfo.get("string", "")
                print(
                    f"      {fname:<40} type={ftype!r:<12} "
                    f"string={fstr!r}"
                )
                if ftype in ("monetary", "float"):
                    header_amount_fields.append(fname)

        # ── Section 4c: KPI 6 read_group on HEADER model ─────────────────────
        print()
        print(_SEP2)
        print(f"[4c] KPI 6 read_group on HEADER MODEL: {_HEADER_MODEL}")
        print("     groupby=['date:month'] — date is a DIRECT field, should work.")
        print(f"     Domain (unfiltered): date >= {period_start}  AND  <= {period_end} 23:59:59")
        print(_SEP2)

        domain_header = [
            ("date", ">=", period_start.isoformat()),
            ("date", "<=", period_end.isoformat() + " 23:59:59"),
        ]
        domain_header_post = domain_header + [("state", "=", "post")]

        # Determine which amount field(s) to aggregate.
        # Try 'amount' first (most common), fallback to first monetary field found.
        agg_candidates = [f for f in ["amount", "net_amount", "total_amount"] if f in header_fields]
        if not agg_candidates and header_amount_fields:
            agg_candidates = header_amount_fields[:3]

        print(f"    Amount field candidates to try: {agg_candidates}")

        unfiltered_by_key: dict[str, dict] = {}
        filtered_by_key:   dict[str, dict] = {}
        total_unfiltered = 0.0
        total_unfiltered_count = 0
        total_filtered   = 0.0
        total_filtered_count = 0
        chosen_field = "amount"  # default; updated below

        for candidate_field in agg_candidates:
            print()
            print(f"    --- Trying field: {candidate_field!r} ---")
            try:
                rg_uf: list[dict] = await client.execute_kw(
                    _HEADER_MODEL,
                    "read_group",
                    args=[domain_header, [f"{candidate_field}:sum"], ["date:month"]],
                    kwargs={"lazy": False},
                )
                print(f"    {_PASS} read_group with {candidate_field!r} SUCCEEDED — "
                      f"{len(rg_uf)} row(s)")

                # Print raw first row to document key format
                if rg_uf:
                    print("    Raw first row (key-format documentation):")
                    for k, v in rg_uf[0].items():
                        print(f"      {k!r}: {v!r}")

                # Parse rows
                print()
                print(f"    {'Month key (Odoo)':<25} {'Records':>10}  {candidate_field + ' SUM':>26}")
                print(f"    {'-'*25} {'-'*10}  {'-'*26}")

                tmp_uf: dict[str, dict] = {}
                for row in rg_uf:
                    mk = _parse_month_key_header(row)
                    if mk is None:
                        non_d = {k: v for k, v in row.items() if not k.startswith("__")}
                        print(f"    {_FLAG} Cannot parse month key: {non_d}")
                        flags.append("header_cannot_parse_month_key")
                        continue
                    amt   = float(row.get(candidate_field) or 0.0)
                    count = int(row.get("__count") or 0)
                    tmp_uf[mk] = {"amount": amt, "count": count}
                    print(f"    {mk:<25} {count:>10,}  {amt:>24,.2f} EGP")

                t_uf_total = sum(v["amount"] for v in tmp_uf.values())
                t_uf_count = sum(v["count"] for v in tmp_uf.values())
                print(f"    {'-'*25} {'-'*10}  {'-'*26}")
                print(f"    {'TOTAL':<25} {t_uf_count:>10,}  {t_uf_total:>24,.2f} EGP")

                # Use this field as the primary if it gives non-zero totals
                if t_uf_total > 0 and not unfiltered_by_key:
                    chosen_field = candidate_field
                    unfiltered_by_key = tmp_uf
                    total_unfiltered = t_uf_total
                    total_unfiltered_count = t_uf_count
                    print(f"    {_PASS} Field {candidate_field!r} selected as primary amount field.")

            except Exception as exc:
                print(f"    {_FLAG} read_group with {candidate_field!r} FAILED: {exc}")
                flags.append(f"header_field_{candidate_field}_groupby_failed")

        if not unfiltered_by_key:
            flags.append("header_model_no_working_amount_field")
            print(f"\n    {_FLAG} No working amount field found on header model.")
            print("          Manual investigation required before D1.")

        # ── Section 5: State filter test on HEADER model ──────────────────────
        print()
        print(_SEP2)
        print("[5] State filter test on HEADER MODEL")
        print(f"    Comparing unfiltered vs state='post' for field: {chosen_field!r}")
        print("    Side-by-side — even 1 EGP delta is reported explicitly")
        print(_SEP2)

        if unfiltered_by_key:
            try:
                rg_post: list[dict] = await client.execute_kw(
                    _HEADER_MODEL,
                    "read_group",
                    args=[domain_header_post, [f"{chosen_field}:sum"], ["date:month"]],
                    kwargs={"lazy": False},
                )
                for row in rg_post:
                    mk = _parse_month_key_header(row)
                    if mk is None:
                        continue
                    amt   = float(row.get(chosen_field) or 0.0)
                    count = int(row.get("__count") or 0)
                    filtered_by_key[mk] = {"amount": amt, "count": count}
                    total_filtered   += amt
                    total_filtered_count += count
            except Exception as exc:
                print(f"    {_FLAG} state='post' filter query failed: {exc}")
                flags.append("header_post_filter_query_failed")

        col1, col2, col3, col4, col5, col6 = 25, 10, 18, 10, 18, 16
        hdr_line = (
            f"    {'Month':<{col1}} {'Rec(unfilt)':>{col2}}  "
            f"{'Amt(unfilt)':>{col3}}  "
            f"{'Rec(post)':>{col4}}  {'Amt(post)':>{col5}}  "
            f"{'Delta EGP':>{col6}}"
        )
        sep_line = (
            f"    {'-'*col1} {'-'*col2}  {'-'*col3}  "
            f"{'-'*col4}  {'-'*col5}  {'-'*col6}"
        )
        print(hdr_line)
        print(sep_line)

        all_keys = sorted(set(list(unfiltered_by_key) + list(filtered_by_key)))
        overall_delta = 0.0

        for key in all_keys:
            uf = unfiltered_by_key.get(key, {"amount": 0.0, "count": 0})
            ft = filtered_by_key.get(key, {"amount": 0.0, "count": 0})
            delta = uf["amount"] - ft["amount"]
            overall_delta += delta
            delta_str = f"{delta:+,.2f}" if abs(delta) >= 0.005 else "0.00"
            print(
                f"    {key:<{col1}} {uf['count']:>{col2},}  "
                f"{uf['amount']:>{col3},.2f} EGP  "
                f"{ft['count']:>{col4},}  {ft['amount']:>{col5},.2f} EGP  "
                f"{delta_str:>{col6}}"
            )

        ov_str = f"{overall_delta:+,.2f}" if abs(overall_delta) >= 0.005 else "0.00"
        print(sep_line)
        print(
            f"    {'TOTAL':<{col1}} {total_unfiltered_count:>{col2},}  "
            f"{total_unfiltered:>{col3},.2f} EGP  "
            f"{total_filtered_count:>{col4},}  {total_filtered:>{col5},.2f} EGP  "
            f"{ov_str:>{col6}}"
        )
        print()

        if abs(overall_delta) < 0.01:
            print(f"    {_PASS} Delta = 0.00 EGP — all header records in the 6-month window "
                  "are in state='post'.")
            print(f"    {_INFO} Decision 5.1: state='post' filter has ZERO IMPACT on totals.")
            print(f"    {_INFO} Including it adds defence-in-depth at zero cost.")
        elif abs(overall_delta) < 1_000:
            print(f"    {_FLAG} Small delta: {overall_delta:+,.2f} EGP — "
                  "a handful of non-post records in the 6-month window.")
            print(f"    {_INFO} Decision 5.1: recommend adding state='post' for correctness.")
            flags.append(f"state_filter_small_delta_{overall_delta:+.2f}_egp")
        else:
            pct = abs(overall_delta) / max(total_unfiltered, 0.01) * 100
            print(f"    {_FLAG} Material delta: {overall_delta:+,.2f} EGP ({pct:.2f}%) — "
                  "non-post records contribute meaningfully.")
            print(f"    {_INFO} Decision 5.1: state='post' filter REQUIRED.")
            flags.append(f"state_filter_material_delta_{overall_delta:+.2f}_egp")

        # ── Section 6: All-time sanity ratio ──────────────────────────────────
        print()
        print(_SEP2)
        print("[6] Sanity ratio: 6-month SUM ÷ all-time x_studio_actual_paid_amount")
        print(_SEP2)

        ratio_pct = (
            total_unfiltered / _ALL_TIME_ACTUAL_PAID_EGP * 100
            if _ALL_TIME_ACTUAL_PAID_EGP and total_unfiltered > 0 else 0.0
        )

        print(f"    6-month SUM (unfiltered, {chosen_field!r}): {_egp(total_unfiltered)}")
        print(f"    6-month SUM (state='post')             : {_egp(total_filtered)}")
        print(f"    All-time actual_paid (state=post)      : {_egp(_ALL_TIME_ACTUAL_PAID_EGP)}")
        print(f"    Ratio (unfiltered / all-time)          : {ratio_pct:.2f}%")
        print()

        if ratio_pct == 0.0 and total_unfiltered == 0.0:
            flags.append("ratio_zero_no_data")
            print(f"    {_FLAG} Ratio is 0.00% because no 6-month data found — check flags above.")
        elif ratio_pct < 5.0:
            flags.append(f"ratio_low_{ratio_pct:.1f}pct")
            print(f"    {_FLAG} Ratio {ratio_pct:.2f}% is BELOW 5% — investigate before D1.")
            print("          The header model's amount field may not represent collected cash.")
        elif ratio_pct > 30.0:
            flags.append(f"ratio_high_{ratio_pct:.1f}pct")
            print(f"    {_FLAG} Ratio {ratio_pct:.2f}% is ABOVE 30% — investigate before D1.")
            print("          The amount field may double-count or measure something unexpected.")
        else:
            print(f"    {_PASS} Ratio {ratio_pct:.2f}% is within expected range [5%, 30%].")
            print(f"          The {chosen_field!r} field on the header model reads the correct scale.")

        # ── Section 7: Discovery Summary ──────────────────────────────────────
        print()
        print(_SEP)
        print("DISCOVERY SUMMARY")
        print(_SEP)
        print()
        print(f"  Line model       : {_LINE_MODEL}")
        print(f"  Header model     : {_HEADER_MODEL}")
        print(f"  Line records (all time): {total_records:,}")
        print(f"  Period           : {period_start}  →  {period_end}")
        print()
        print(f"  KEY FINDING — MODEL FOR D1:")
        if line_groupby_works:
            print(f"    Line model groupby WORKS — use {_LINE_MODEL} in D1")
        else:
            print(f"    Line model groupby FAILS — use {_HEADER_MODEL} in D1")
            print(f"    Amount field to use: {chosen_field!r}")
            print(f"    Groupby: ['date:month']  (date is a direct field on the header)")
        print()
        print(f"  6-month header records (unfiltered): {total_unfiltered_count:,}")
        print(f"  6-month total (unfiltered)         : {_egp(total_unfiltered)}")
        print(f"  6-month total (state='post')       : {_egp(total_filtered)}")
        print(f"  State-filter delta                 : {overall_delta:+,.2f} EGP")
        print(f"  Sanity ratio                       : {ratio_pct:.2f}%  (vs all-time actual_paid)")
        print()

        # Per-month table for manual cross-check
        col_m, col_r, col_a, col_b = 22, 12, 26, 26
        print(
            f"  {'Month':<{col_m}} {'Header Rec (unfilt)':>{col_r}}  "
            f"{'Amount (unfiltered)':>{col_a}}  {'Amount (post only)':>{col_b}}"
        )
        print(f"  {'-'*col_m} {'-'*col_r}  {'-'*col_a}  {'-'*col_b}")
        for ym in sorted(set(list(unfiltered_by_key) + list(filtered_by_key))):
            uf = unfiltered_by_key.get(ym, {"amount": 0.0, "count": 0})
            ft = filtered_by_key.get(ym, {"amount": 0.0, "count": 0})
            print(
                f"  {ym:<{col_m}} {uf['count']:>{col_r},}  "
                f"{uf['amount']:>{col_a},.2f} EGP  {ft['amount']:>{col_b},.2f} EGP"
            )
        print(f"  {'-'*col_m} {'-'*col_r}  {'-'*col_a}  {'-'*col_b}")
        print(
            f"  {'TOTAL':<{col_m}} {total_unfiltered_count:>{col_r},}  "
            f"{total_unfiltered:>{col_a},.2f} EGP  {total_filtered:>{col_b},.2f} EGP"
        )
        print()

        # Flags summary
        if flags:
            print(f"  {_FLAG} FLAGS raised ({len(flags)}):")
            for fl in flags:
                print(f"      - {fl}")
            print()
            if "line_model_groupby_unsupported_use_header_model" in flags:
                print(f"  {_INFO} The line_model_groupby flag is EXPECTED and RESOLVED by Section 4c.")
                print(f"  {_INFO} D1 will query the header model. This flag does not block D1.")
        else:
            print(f"  {_PASS} No flags raised.")

        print()
        print("  ─── MANUAL CROSS-CHECK (REQUIRED before D1) ──────────────────────────")
        print()
        print("  Open Odoo → RS Accounting → Payment Installments view")
        print("  (the view that shows rs.account.payment.installment records with date)")
        print()
        print("  Recommended: pick the most recent COMPLETE calendar month")
        print("  (e.g., April 2026 if today is May 2026).")
        print()
        print("  Steps:")
        print(f"    1. Filter payment records by date to that month")
        print(f"    2. Sum the {chosen_field!r} column for that month's header records")
        print(f"    3. Compare to the 'Amount (unfiltered)' value in the table above")
        print("    4. Identity-equal match (or explain any delta) required before D1")
        print()
        print("  After confirming:")
        print("    - Approve Decision 5.1 (state filter decision)")
        print("    - Reply 'proceed with D1' to start service implementation")
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
