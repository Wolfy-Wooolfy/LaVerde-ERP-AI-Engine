"""
D0 — KPI 4 Architecture Discovery: Collection Rate MTD & YTD.

Purpose: Verify the two-model architecture (HEADER numerator +
rs.installment denominator) before any service code is written.
Produces 4 totals that Khaled cross-checks against the Odoo UI
at Checkpoint 1.

Architecture under test (Decision 6.1):
  Collection Rate (%) =
    SUM(HEADER.amount) WHERE HEADER.date in period AND state='post'
    ÷
    SUM(rs.installment.amount) WHERE rs.installment.date in period AND state='post'
    × 100

  Numerator model : rs.account.payment.installment (HEADER — same as KPI 6)
  Numerator date  : HEADER.date (datetime, UTC — Decision 5.9 / 5.10)
  Denominator model: rs.installment
  Denominator date : rs.installment.date (date, no timezone — direct ISO filter)

This script:
  - Calls ONLY read methods (search_read) via OdooClient.
  - Writes NOTHING to Odoo (read-only).
  - Costs $0 in AI.
  - Prints no PII (amounts and record counts only — no customer names or IDs).
  - Applies Decision 5.10 to the numerator: converts UTC datetimes to
    Egypt local dates before summing and counting.
  - Reports min/max record dates for each query (Checkpoint 1 sanity check).
  - Appends one TSV row to logs/kpi4_discovery.log.
  - Exits 0 on completion regardless of findings.

Hard constraint: this script MUST NOT import anything from
backend.modules.collections.services (to keep it independent of
yet-to-be-written service code). _tz_period_bounds() logic is inlined.

Usage:
    python scripts/discover_kpi4_architecture.py
"""

import asyncio
import io
import os
import sys
import time
from datetime import date, datetime, time as dt_time, timezone
from zoneinfo import ZoneInfo

from backend.shared.odoo.client import OdooClient

# Force UTF-8 stdout (Windows consoles default to cp1252)
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Constants ─────────────────────────────────────────────────────────────────

_HEADER_MODEL = "rs.account.payment.installment"
_INST_MODEL = "rs.installment"

# Egypt observes DST: UTC+2 Nov-Apr, UTC+3 May-Oct (re-introduced 2023).
# ZoneInfo handles transitions automatically from tzdata. Decision 5.9.
_LA_VERDE_TZ = ZoneInfo("Africa/Cairo")
_UTC_TZ = ZoneInfo("UTC")

_LOG_FILE = "logs/kpi4_discovery.log"
_SEP = "═" * 78
_SEP2 = "─" * 76
_INFO = "[INFO]"
_PASS = "[PASS]"
_FLAG = "[FLAG]"
_WARN = "[WARN]"


# ── Period computation ────────────────────────────────────────────────────────

def _tz_period_bounds(period_start: date, period_end: date) -> tuple[str, str]:
    """Convert Egypt-local period boundaries to UTC datetime strings.

    Mirrors kpi_service._tz_period_bounds() (Decision 5.9). Inlined here
    because this script must not import from backend.modules.collections.services.

    A record at "2026-05-01 00:00:00" Egypt local (UTC+3 summer) is stored
    as "2026-04-30 21:00:00" UTC. A naive domain boundary of "2026-05-01"
    would exclude it. ZoneInfo handles DST transitions automatically.
    """
    start_local = datetime.combine(period_start, dt_time.min, tzinfo=_LA_VERDE_TZ)
    end_local = datetime.combine(period_end, dt_time(23, 59, 59), tzinfo=_LA_VERDE_TZ)
    return (
        start_local.astimezone(_UTC_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        end_local.astimezone(_UTC_TZ).strftime("%Y-%m-%d %H:%M:%S"),
    )


def _compute_period_bounds(today: date) -> dict:
    """Compute MTD and YTD period bounds for both query types.

    Returns:
      mtd_start / mtd_end             — date objects for the period
      ytd_start / ytd_end             — date objects for the period
      mtd_start_utc / mtd_end_utc     — UTC strings (HEADER datetime domain)
      ytd_start_utc / ytd_end_utc     — UTC strings (HEADER datetime domain)
      mtd_start_iso / mtd_end_iso     — ISO date strings (rs.installment domain)
      ytd_start_iso / ytd_end_iso     — ISO date strings (rs.installment domain)
    """
    mtd_start = today.replace(day=1)
    mtd_end = today
    ytd_start = today.replace(month=1, day=1)   # Decision 6.2: calendar year
    ytd_end = today

    mtd_start_utc, mtd_end_utc = _tz_period_bounds(mtd_start, mtd_end)
    ytd_start_utc, ytd_end_utc = _tz_period_bounds(ytd_start, ytd_end)

    return {
        "mtd_start": mtd_start,
        "mtd_end":   mtd_end,
        "ytd_start": ytd_start,
        "ytd_end":   ytd_end,
        "mtd_start_utc": mtd_start_utc,
        "mtd_end_utc":   mtd_end_utc,
        "ytd_start_utc": ytd_start_utc,
        "ytd_end_utc":   ytd_end_utc,
        "mtd_start_iso": mtd_start.isoformat(),
        "mtd_end_iso":   mtd_end.isoformat(),
        "ytd_start_iso": ytd_start.isoformat(),
        "ytd_end_iso":   ytd_end.isoformat(),
    }


# ── Query helpers ─────────────────────────────────────────────────────────────

async def _query_numerator(
    client: "OdooClient",
    domain: list,
    label: str,
    period_start: date,
    period_end: date,
) -> dict:
    """Query HEADER records; sum amounts by Egypt local date (Decision 5.10).

    Converts each record's UTC datetime to Africa/Cairo local date before
    deciding whether it falls in [period_start, period_end]. This reproduces
    the identity-equal result that the Odoo UI produces (which displays
    HEADER.date in the user's local timezone).

    Also tracks:
      boundary_crossing_count — records whose UTC month != Egypt local month
        (i.e., records that a naive UTC domain would have miscounted).
      out_of_period_count — records whose Egypt-local date falls outside the
        period despite passing the UTC domain filter (should be 0; > 0
        indicates a domain construction error).
    """
    t0 = time.monotonic()
    try:
        records = await client.execute_kw(
            _HEADER_MODEL,
            "search_read",
            args=[domain, ["date", "amount"]],
            kwargs={},
        )
    except Exception as exc:
        raise RuntimeError(f"Query {label} (HEADER numerator) failed: {exc}") from exc
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    total_amount = 0.0
    boundary_crossing_count = 0
    out_of_period_count = 0
    min_utc: "datetime | None" = None
    max_utc: "datetime | None" = None
    min_local: "date | None" = None
    max_local: "date | None" = None

    for rec in records:
        raw = rec.get("date")
        if not raw or raw is False:
            continue
        try:
            utc_dt = datetime.strptime(str(raw), "%Y-%m-%d %H:%M:%S").replace(tzinfo=_UTC_TZ)
        except ValueError:
            continue

        local_dt = utc_dt.astimezone(_LA_VERDE_TZ)
        local_d = local_dt.date()

        if min_utc is None or utc_dt < min_utc:
            min_utc = utc_dt
        if max_utc is None or utc_dt > max_utc:
            max_utc = utc_dt
        if min_local is None or local_d < min_local:
            min_local = local_d
        if max_local is None or local_d > max_local:
            max_local = local_d

        # Boundary crossing: record's UTC month != Egypt local month.
        # These are correctly captured by the UTC-shifted domain (Decision 5.9)
        # and correctly bucketed by Python-side regrouping (Decision 5.10).
        if utc_dt.month != local_dt.month or utc_dt.year != local_dt.year:
            boundary_crossing_count += 1

        if period_start <= local_d <= period_end:
            total_amount += float(rec.get("amount") or 0.0)
        else:
            # Egypt-local date is outside the period — should be 0.
            out_of_period_count += 1

    return {
        "amount":                  total_amount,
        "record_count":            len(records),
        "boundary_crossing_count": boundary_crossing_count,
        "out_of_period_count":     out_of_period_count,
        "min_date_utc":   min_utc.strftime("%Y-%m-%d %H:%M:%S") if min_utc   else None,
        "max_date_utc":   max_utc.strftime("%Y-%m-%d %H:%M:%S") if max_utc   else None,
        "min_date_local": min_local.isoformat()                  if min_local else None,
        "max_date_local": max_local.isoformat()                  if max_local else None,
        "elapsed_ms":     elapsed_ms,
    }


async def _query_denominator(
    client: "OdooClient",
    domain: list,
    label: str,
    period_start: date,
    period_end: date,
) -> dict:
    """Query rs.installment and sum rs.installment.amount directly.

    rs.installment.date is a plain date field (no timezone). The domain uses
    ISO date strings directly — no UTC conversion required.

    Denominator uses rs.installment.amount (contractual face value), NOT
    due_amount (remaining balance). Using due_amount would make the ratio
    self-referential and time-unstable — see Decision 6.1 clarification.
    """
    t0 = time.monotonic()
    try:
        records = await client.execute_kw(
            _INST_MODEL,
            "search_read",
            args=[domain, ["date", "amount"]],
            kwargs={},
        )
    except Exception as exc:
        raise RuntimeError(f"Query {label} (rs.installment denominator) failed: {exc}") from exc
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    total_amount = 0.0
    out_of_period_count = 0
    min_d: "date | None" = None
    max_d: "date | None" = None

    for rec in records:
        amt = float(rec.get("amount") or 0.0)
        total_amount += amt

        raw = rec.get("date")
        if raw and raw is not False:
            try:
                rec_date = date.fromisoformat(str(raw))
                if min_d is None or rec_date < min_d:
                    min_d = rec_date
                if max_d is None or rec_date > max_d:
                    max_d = rec_date
                if not (period_start <= rec_date <= period_end):
                    out_of_period_count += 1
            except ValueError:
                pass

    return {
        "amount":              total_amount,
        "record_count":        len(records),
        "out_of_period_count": out_of_period_count,
        "min_date":  min_d.isoformat() if min_d else None,
        "max_date":  max_d.isoformat() if max_d else None,
        "elapsed_ms": elapsed_ms,
    }


# ── Formatting helpers ────────────────────────────────────────────────────────

def _compute_rate(numerator: float, denominator: float) -> str:
    if denominator == 0:
        return "N/A"
    return f"{numerator / denominator * 100:.2f}%"


def _print_numerator_result(label: str, kind: str, domain: list, r: dict) -> None:
    print()
    print(f"  ── {label}: {kind}")
    print(f"     Model  : {_HEADER_MODEL}")
    print(f"     Domain : {domain}")
    print(f"     Elapsed: {r['elapsed_ms']} ms")
    print(f"     Records returned       : {r['record_count']:,}")
    print(f"     Amount (EGP, EGY-local): {r['amount']:>26,.2f}")

    if r["record_count"] == 0:
        print(f"     Date range (UTC)       : N/A (empty result set)")
        print(f"     Date range (EGY-local) : N/A (empty result set)")
        print(f"     {_INFO} Zero records — expected during data-entry phase (Decision 5.7 analog).")
    else:
        print(f"     Date range (UTC)       : {r['min_date_utc']}  →  {r['max_date_utc']}")
        print(f"     Date range (EGY-local) : {r['min_date_local']}  →  {r['max_date_local']}")

        if r["boundary_crossing_count"] > 0:
            print(
                f"     {_INFO} Boundary-crossing records (UTC month ≠ EGY-local month): "
                f"{r['boundary_crossing_count']}"
            )
            print(f"          → Correctly captured by UTC-shifted domain (Decision 5.9)")
            print(f"            and correctly bucketed by Python-side regrouping (Decision 5.10).")
        else:
            print(f"     {_INFO} No boundary-crossing records (UTC month == EGY-local month for all).")

        if r["out_of_period_count"] > 0:
            print(
                f"     {_FLAG} Out-of-period records (EGY-local date outside period): "
                f"{r['out_of_period_count']}"
            )
            print(f"          → This indicates a domain construction error — investigate before D1.")
        else:
            print(f"     {_PASS} All records fall within EGY-local period bounds.")


def _print_denominator_result(label: str, kind: str, domain: list, r: dict) -> None:
    print()
    print(f"  ── {label}: {kind}")
    print(f"     Model  : {_INST_MODEL}")
    print(f"     Domain : {domain}")
    print(f"     Elapsed: {r['elapsed_ms']} ms")
    print(f"     Records returned : {r['record_count']:,}")
    print(f"     Amount (EGP)     : {r['amount']:>26,.2f}")

    if r["record_count"] == 0:
        print(f"     Date range : N/A (empty result set)")
        print(f"     {_INFO} Zero denominator records — rate will be N/A (Decision 6.3).")
    else:
        print(f"     Date range : {r['min_date']}  →  {r['max_date']}")
        if r["out_of_period_count"] > 0:
            print(
                f"     {_FLAG} Out-of-period records: {r['out_of_period_count']} "
                f"— domain may be incorrect."
            )
        else:
            print(f"     {_PASS} All records fall within domain date bounds.")


def _print_table(
    b: dict,
    mtd_num: dict, mtd_den: dict,
    ytd_num: dict, ytd_den: dict,
) -> None:
    """Print the KPI 4 summary table."""
    mtd_rate = _compute_rate(mtd_num["amount"], mtd_den["amount"])
    ytd_rate = _compute_rate(ytd_num["amount"], ytd_den["amount"])
    total_ms = (
        mtd_num["elapsed_ms"] + mtd_den["elapsed_ms"]
        + ytd_num["elapsed_ms"] + ytd_den["elapsed_ms"]
    )

    print()
    print(_SEP)
    print("KPI 4 — COLLECTION RATE SUMMARY")
    print(_SEP)
    col_p = 7
    col_n = 24
    col_d = 24
    col_r = 10
    col_nr = 12
    col_dr = 12
    hdr = (
        f"  {'Period':<{col_p}}  {'Numerator (EGP)':>{col_n}}  "
        f"{'Denominator (EGP)':>{col_d}}  {'Rate %':>{col_r}}  "
        f"{'Num Recs':>{col_nr}}  {'Den Recs':>{col_dr}}"
    )
    sep = (
        f"  {'-'*col_p}  {'-'*col_n}  "
        f"{'-'*col_d}  {'-'*col_r}  "
        f"{'-'*col_nr}  {'-'*col_dr}"
    )
    print(hdr)
    print(sep)
    for period_label, num, den, rate in [
        ("MTD", mtd_num, mtd_den, mtd_rate),
        ("YTD", ytd_num, ytd_den, ytd_rate),
    ]:
        print(
            f"  {period_label:<{col_p}}  {num['amount']:>{col_n},.2f}  "
            f"{den['amount']:>{col_d},.2f}  {rate:>{col_r}}  "
            f"{num['record_count']:>{col_nr},}  {den['record_count']:>{col_dr},}"
        )
    print(sep)
    print(f"\n  Total RPC time: {total_ms} ms across 4 queries")
    print()

    # Rate interpretation per Checkpoint 0 Risk 2 guidance
    for period_label, rate_str in [("MTD", mtd_rate), ("YTD", ytd_rate)]:
        if rate_str == "N/A":
            print(
                f"  {_INFO} {period_label} rate = N/A (denominator is zero — "
                f"no installments due in this period)."
            )
            print(f"       Decision 6.3: rate_percent will be None in the service response.")
        else:
            rate_val = float(rate_str.rstrip("%"))
            if rate_val == 0.0:
                print(
                    f"  {_INFO} {period_label} rate = 0.00% (no payments posted; "
                    f"numerator is zero)."
                )
                print(
                    f"       Low rates during the historical data entry phase reflect "
                    f"back-entry lag between"
                )
                print(
                    f"       installments (mostly back-entered) and payments (still "
                    f"being back-entered)."
                )
                print(f"       This is expected, not a bug.")
            elif rate_val < 5.0:
                print(
                    f"  {_WARN} {period_label} rate = {rate_str} (below 5% — "
                    f"data entry lag, not a bug)."
                )
            elif rate_val > 200.0:
                print(
                    f"  {_WARN} {period_label} rate = {rate_str} (above 200% — "
                    f"flag for review)."
                )
            else:
                print(f"  {_PASS} {period_label} rate = {rate_str}")
    print()


def _print_cross_check_guide(b: dict, mtd_num: dict, mtd_den: dict, ytd_num: dict, ytd_den: dict) -> None:
    """Print Odoo UI navigation steps for Checkpoint 1 manual cross-check."""
    mtd_rate = _compute_rate(mtd_num["amount"], mtd_den["amount"])
    ytd_rate = _compute_rate(ytd_num["amount"], ytd_den["amount"])

    print(_SEP)
    print("MANUAL CROSS-CHECK GUIDE — Checkpoint 1")
    print(_SEP)
    print()
    print("  Open the 4 Odoo views below and compare each total to D0 output.")
    print("  Identity-equal at 2-decimal precision is required before D1 proceeds.")
    print("  Zero amounts are a valid result during the data-entry phase.")
    print()

    print(f"  {'─'*70}")
    print(f"  Query A — MTD Numerator (Payment Headers)")
    print(f"  {'─'*70}")
    print(f"  Open   : Odoo → RS Accounting → Payment Installments")
    print(f"  Filters: State = Posted")
    print(f"           Date >= {b['mtd_start_iso']}  (first day of this month, Egypt local)")
    print(f"           Date <= {b['mtd_end_iso']}   (today, Egypt local)")
    print(f"  Action : Sum the 'Amount' column for matching records.")
    print(f"  D0 says: {mtd_num['amount']:>26,.2f} EGP  ({mtd_num['record_count']:,} records)")
    print(f"  UTC domain used: [{b['mtd_start_utc']}  →  {b['mtd_end_utc']}]")
    print()

    print(f"  {'─'*70}")
    print(f"  Query B — MTD Denominator (rs.installment)")
    print(f"  {'─'*70}")
    print(f"  Open   : Odoo → Collections Mgmt → All Installments")
    print(f"  Filters: State = Posted")
    print(f"           Date >= {b['mtd_start_iso']}")
    print(f"           Date <= {b['mtd_end_iso']}")
    print(f"  Action : Sum the 'Amount' column (NOT 'Due Amount' — see Decision 6.1).")
    print(f"  D0 says: {mtd_den['amount']:>26,.2f} EGP  ({mtd_den['record_count']:,} records)")
    print()

    print(f"  {'─'*70}")
    print(f"  Query C — YTD Numerator (Payment Headers)")
    print(f"  {'─'*70}")
    print(f"  Open   : Odoo → RS Accounting → Payment Installments")
    print(f"  Filters: State = Posted")
    print(f"           Date >= {b['ytd_start_iso']}  (Jan 1, calendar year — Decision 6.2)")
    print(f"           Date <= {b['ytd_end_iso']}   (today, Egypt local)")
    print(f"  Action : Sum the 'Amount' column.")
    print(f"  D0 says: {ytd_num['amount']:>26,.2f} EGP  ({ytd_num['record_count']:,} records)")
    print(f"  UTC domain used: [{b['ytd_start_utc']}  →  {b['ytd_end_utc']}]")
    print()

    print(f"  {'─'*70}")
    print(f"  Query D — YTD Denominator (rs.installment)")
    print(f"  {'─'*70}")
    print(f"  Open   : Odoo → Collections Mgmt → All Installments")
    print(f"  Filters: State = Posted")
    print(f"           Date >= {b['ytd_start_iso']}")
    print(f"           Date <= {b['ytd_end_iso']}")
    print(f"  Action : Sum the 'Amount' column (NOT 'Due Amount').")
    print(f"  D0 says: {ytd_den['amount']:>26,.2f} EGP  ({ytd_den['record_count']:,} records)")
    print()

    print(f"  {'─'*70}")
    print(f"  Derived rates")
    print(f"  {'─'*70}")
    print(f"  MTD rate = A ÷ B × 100 = {mtd_rate}")
    print(f"  YTD rate = C ÷ D × 100 = {ytd_rate}")
    print()
    print(f"  If any total disagrees: STOP and report the delta.")
    print(f"  Do NOT proceed to D1 until all 4 totals are identity-equal.")
    print(_SEP)


def _append_tsv(
    run_at: str,
    b: dict,
    mtd_num: dict, mtd_den: dict,
    ytd_num: dict, ytd_den: dict,
) -> None:
    """Append one TSV row to logs/kpi4_discovery.log for audit trail."""
    os.makedirs("logs", exist_ok=True)
    log_exists = os.path.isfile(_LOG_FILE)
    mtd_rate = _compute_rate(mtd_num["amount"], mtd_den["amount"])
    ytd_rate = _compute_rate(ytd_num["amount"], ytd_den["amount"])
    total_ms = (
        mtd_num["elapsed_ms"] + mtd_den["elapsed_ms"]
        + ytd_num["elapsed_ms"] + ytd_den["elapsed_ms"]
    )

    with open(_LOG_FILE, "a", encoding="utf-8") as f:
        if not log_exists:
            f.write(
                "run_at\t"
                "mtd_start\tmtd_end\tytd_start\tytd_end\t"
                "mtd_num_egp\tmtd_num_recs\t"
                "mtd_den_egp\tmtd_den_recs\tmtd_rate\t"
                "ytd_num_egp\tytd_num_recs\t"
                "ytd_den_egp\tytd_den_recs\tytd_rate\t"
                "total_rpc_ms\n"
            )
        f.write(
            f"{run_at}\t"
            f"{b['mtd_start_iso']}\t{b['mtd_end_iso']}\t"
            f"{b['ytd_start_iso']}\t{b['ytd_end_iso']}\t"
            f"{mtd_num['amount']:.2f}\t{mtd_num['record_count']}\t"
            f"{mtd_den['amount']:.2f}\t{mtd_den['record_count']}\t{mtd_rate}\t"
            f"{ytd_num['amount']:.2f}\t{ytd_num['record_count']}\t"
            f"{ytd_den['amount']:.2f}\t{ytd_den['record_count']}\t{ytd_rate}\t"
            f"{total_ms}\n"
        )
    print(f"\n{_INFO} TSV row appended to {_LOG_FILE}")


# ── Main ──────────────────────────────────────────────────────────────────────

async def run() -> None:
    today = date.today()
    run_at = datetime.now(timezone.utc).isoformat()
    b = _compute_period_bounds(today)

    print(_SEP)
    print("KPI 4 — Collection Rate MTD & YTD: Architecture Discovery (D0)")
    print(f"Run timestamp : {run_at}")
    print(f"Today (local) : {today}")
    print(_SEP)
    print()
    print("  Period bounds:")
    print(f"    MTD : {b['mtd_start']}  →  {b['mtd_end']}  (first day of month → today)")
    print(f"    YTD : {b['ytd_start']}  →  {b['ytd_end']}  (Jan 1 calendar year → today, Decision 6.2)")
    print()
    print("  Numerator UTC boundaries (Africa/Cairo → UTC, Decision 5.9):")
    print(f"    MTD start UTC : {b['mtd_start_utc']}")
    print(f"    MTD end UTC   : {b['mtd_end_utc']}")
    print(f"    YTD start UTC : {b['ytd_start_utc']}")
    print(f"    YTD end UTC   : {b['ytd_end_utc']}")
    print()
    print("  Denominator ISO date bounds (rs.installment.date is a date field, no timezone):")
    print(f"    MTD : {b['mtd_start_iso']}  →  {b['mtd_end_iso']}")
    print(f"    YTD : {b['ytd_start_iso']}  →  {b['ytd_end_iso']}")
    print()

    domain_a = [
        ("state", "=", "post"),
        ("date", ">=", b["mtd_start_utc"]),
        ("date", "<=", b["mtd_end_utc"]),
    ]
    domain_b = [
        ("state", "=", "post"),
        ("date", ">=", b["mtd_start_iso"]),
        ("date", "<=", b["mtd_end_iso"]),
    ]
    domain_c = [
        ("state", "=", "post"),
        ("date", ">=", b["ytd_start_utc"]),
        ("date", "<=", b["ytd_end_utc"]),
    ]
    domain_d = [
        ("state", "=", "post"),
        ("date", ">=", b["ytd_start_iso"]),
        ("date", "<=", b["ytd_end_iso"]),
    ]

    print(_SEP2)
    print("[A] MTD numerator  — rs.account.payment.installment, state=post, UTC bounds")
    print("[B] MTD denominator — rs.installment, state=post, date bounds")
    print("[C] YTD numerator  — rs.account.payment.installment, state=post, UTC bounds")
    print("[D] YTD denominator — rs.installment, state=post, date bounds")
    print(_SEP2)

    async with OdooClient() as client:

        print(f"\n{_INFO} Running Query A ...")
        mtd_num = await _query_numerator(client, domain_a, "A", b["mtd_start"], b["mtd_end"])
        _print_numerator_result("A", "MTD Numerator (HEADER — cash receipts in current month)", domain_a, mtd_num)

        print(f"\n{_INFO} Running Query B ...")
        mtd_den = await _query_denominator(client, domain_b, "B", b["mtd_start"], b["mtd_end"])
        _print_denominator_result("B", "MTD Denominator (rs.installment — due in current month)", domain_b, mtd_den)

        print(f"\n{_INFO} Running Query C ...")
        ytd_num = await _query_numerator(client, domain_c, "C", b["ytd_start"], b["ytd_end"])
        _print_numerator_result("C", "YTD Numerator (HEADER — cash receipts Jan 1 to today)", domain_c, ytd_num)

        print(f"\n{_INFO} Running Query D ...")
        ytd_den = await _query_denominator(client, domain_d, "D", b["ytd_start"], b["ytd_end"])
        _print_denominator_result("D", "YTD Denominator (rs.installment — due Jan 1 to today)", domain_d, ytd_den)

    _print_table(b, mtd_num, mtd_den, ytd_num, ytd_den)
    _print_cross_check_guide(b, mtd_num, mtd_den, ytd_num, ytd_den)
    _append_tsv(run_at, b, mtd_num, mtd_den, ytd_num, ytd_den)


if __name__ == "__main__":
    asyncio.run(run())
