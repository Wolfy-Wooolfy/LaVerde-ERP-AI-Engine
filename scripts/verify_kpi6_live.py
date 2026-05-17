"""
Live verification for KPI 6 — 6-Month Collection Trend.

Usage:
    python scripts/verify_kpi6_live.py [--url http://localhost:8000]

Requires the FastAPI server to be running and reachable.
Set VERIFY_USERNAME / VERIFY_PASSWORD env vars (or .env) to override
the default admin credentials.

Exit 0  — all assertions passed
Exit 1  — at least one assertion failed or the server was unreachable

Appends one tab-separated row to logs/kpi6_verification.log on each run.

NOTE — Empty months in 2026 are EXPECTED (Decision 5.7):
Operations staff are entering historical payment data retroactively.
Zero-amount months are truthful data, not bugs to investigate.
The manual cross-check target is December 2025 (47,465,098 EGP, 431 records)
as established in D0 Part 1. Khaled must confirm this value against
Odoo → RS Accounting → Payment Installments, filtered to December 2025,
state=posted, SUM(amount).
"""

import argparse
import io
import os
import sys
from datetime import date, datetime, timezone

import httpx
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Config ────────────────────────────────────────────────────────────────────

DEFAULT_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")
USERNAME    = os.environ.get("VERIFY_USERNAME", "admin")
PASSWORD    = os.environ.get("VERIFY_PASSWORD", "password")
ENDPOINT    = "/api/v1/collections/kpi/collection-trend-6m"
LOG_FILE    = "logs/kpi6_verification.log"

# December 2025 baseline values reflect the state='post' filter mandated by
# Decision 5.1. The unfiltered baseline from D0 Part 1 (431 records /
# 47,465,098 EGP) included 2 non-post records totaling 83,000 EGP, which are
# excluded from the production KPI 6 query per Decision 5.1.
_DEC_2025_BASELINE_AMOUNT    = 47_382_098.00  # state='post' filtered
_DEC_2025_BASELINE_RECORDS   = 429            # state='post' filtered
_DEC_2025_BASELINE_TOLERANCE = 0.01   # EGP — identity-equal expected

_EXPECTED_MONTHS = 6

_SEP  = "═" * 70
_SEP2 = "─" * 68
_PASS = "[PASS]"
_FAIL = "[FAIL]"
_INFO = "[INFO]"
_WARN = "[WARN]"


def _log(prefix: str, msg: str) -> None:
    print(f"{prefix} {msg}", flush=True)


def _check(label: str, condition: bool, detail: str = "") -> bool:
    marker = _PASS if condition else _FAIL
    _log(marker, f"{label}{(' — ' + detail) if detail else ''}")
    return condition


def _append_log(
    run_at: str,
    n_months: int,
    n_nonzero: int,
    total_6m: "float | str",
    dec25_amount: "float | str",
    dec25_records: "int | str",
    cache_status: str,
    rpc_ms: "int | str",
    failures: "list[str]",
) -> None:
    os.makedirs("logs", exist_ok=True)
    log_exists = os.path.isfile(LOG_FILE)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        if not log_exists:
            f.write(
                "run_at\tn_months\tn_nonzero\ttotal_6m\t"
                "dec25_amount\tdec25_records\tcache_status\trpc_ms\tfailures\n"
            )
        f.write(
            f"{run_at}\t{n_months}\t{n_nonzero}\t{total_6m}\t"
            f"{dec25_amount}\t{dec25_records}\t{cache_status}\t{rpc_ms}\t"
            f"{','.join(failures) if failures else 'none'}\n"
        )


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_URL)
    args = parser.parse_args()
    base_url: str = args.url.rstrip("/")
    url = f"{base_url}{ENDPOINT}"
    run_at = datetime.now(timezone.utc).isoformat()

    _log(_INFO, f"Target : GET {url}")
    _log(_INFO, f"Auth   : {USERNAME}")

    failures: list[str] = []

    # ── Step 1: GET endpoint ──────────────────────────────────────────────────
    try:
        with httpx.Client(timeout=60) as client:
            r = client.get(url, auth=(USERNAME, PASSWORD))
    except httpx.ConnectError as exc:
        _log(_FAIL, f"Cannot reach {base_url} — is the server running? ({exc})")
        _append_log(run_at, 0, 0, "", "", "", "", "", ["connect_error"])
        return 1

    # ── Step 2: HTTP 200 ──────────────────────────────────────────────────────
    if not _check("HTTP 200", r.status_code == 200, f"got {r.status_code}"):
        _log(_INFO, f"Body: {r.text[:500]}")
        _append_log(run_at, 0, 0, "", "", "", "", "", [f"http_{r.status_code}"])
        return 1

    body: dict = r.json()
    _log(_INFO, f"Top-level keys: {list(body.keys())}")

    # ── Step 3: Required top-level keys ───────────────────────────────────────
    required_keys = (
        "months", "total_6m", "total_record_count", "average_monthly",
        "period_start", "period_end", "currency", "as_of",
        "cache_status", "cache_ttl_seconds", "rpc_duration_ms", "domain",
    )
    for k in required_keys:
        if not _check(f"key '{k}' present", k in body):
            failures.append(f"missing_key_{k}")

    if failures:
        _append_log(run_at, 0, 0, "", "", "", "", "", failures)
        return 1

    # ── Step 4: Extract values ────────────────────────────────────────────────
    months: list   = body["months"]
    total_6m       = float(body["total_6m"])
    total_count    = int(body["total_record_count"])
    avg_monthly    = float(body["average_monthly"])
    period_start   = body["period_start"]
    period_end     = body["period_end"]
    cache_status   = body["cache_status"]
    cache_ttl      = int(body["cache_ttl_seconds"])
    rpc_ms         = int(body["rpc_duration_ms"])

    # ── Step 5: months array — shape and length ───────────────────────────────
    if not _check("months is a list", isinstance(months, list)):
        failures.append("months_not_list")
    elif not _check(f"months has exactly {_EXPECTED_MONTHS} entries", len(months) == _EXPECTED_MONTHS,
                    f"got {len(months)}"):
        failures.append("months_count_wrong")

    per_month_keys = {"month", "label_en", "label_ar", "amount", "record_count"}
    for i, entry in enumerate(months):
        missing = per_month_keys - set(entry.keys())
        if not _check(f"months[{i}] has all 5 keys", not missing, f"missing: {missing}"):
            failures.append(f"months_{i}_missing_keys")

    # ── Step 6: months ordering — oldest first, 6 consecutive months ──────────
    if len(months) == _EXPECTED_MONTHS:
        today = date.today()
        sm    = today.month - 5
        sy    = today.year
        if sm <= 0:
            sm += 12
            sy -= 1
        y, m = sy, sm
        for i, entry in enumerate(months):
            expected_ym = f"{y:04d}-{m:02d}"
            _check(
                f"months[{i}].month == {expected_ym}",
                entry.get("month") == expected_ym,
                f"got {entry.get('month')!r}",
            )
            if entry.get("month") != expected_ym:
                failures.append(f"months_{i}_wrong_ym")
            m += 1
            if m > 12:
                m = 1
                y += 1

    # ── Step 7: Zero-month handling (Decision 5.7) ────────────────────────────
    n_nonzero = sum(1 for e in months if float(e.get("amount", 0)) > 0)
    n_zero    = _EXPECTED_MONTHS - n_nonzero
    _log(_INFO, f"Non-zero months: {n_nonzero}/{_EXPECTED_MONTHS}  (zero months expected during data-entry period)")
    if n_zero > 0:
        _log(_WARN, f"{n_zero} month(s) have zero amount — EXPECTED per Decision 5.7, not a bug")

    # ── Step 8: total_6m == SUM(months.amount) ────────────────────────────────
    computed_total = sum(float(e.get("amount", 0)) for e in months)
    if not _check(
        "total_6m == SUM(months.amount)",
        abs(total_6m - computed_total) < 0.01,
        f"total={total_6m:.2f}, sum={computed_total:.2f}",
    ):
        failures.append("total_inconsistency")

    computed_count = sum(int(e.get("record_count", 0)) for e in months)
    if not _check(
        "total_record_count == SUM(months.record_count)",
        total_count == computed_count,
        f"total={total_count}, sum={computed_count}",
    ):
        failures.append("count_inconsistency")

    # ── Step 9: average_monthly == total_6m / 6 ──────────────────────────────
    expected_avg = total_6m / 6
    if not _check(
        "average_monthly == total_6m / 6",
        abs(avg_monthly - expected_avg) < 0.01,
        f"got {avg_monthly:.2f}, expected {expected_avg:.2f}",
    ):
        failures.append("average_wrong")

    # ── Step 10: cache_ttl_seconds == 3600 ────────────────────────────────────
    if not _check("cache_ttl_seconds == 3600", cache_ttl == 3600, f"got {cache_ttl}"):
        failures.append("wrong_cache_ttl")

    # ── Step 11: currency ─────────────────────────────────────────────────────
    if not _check("currency == 'EGP'", body.get("currency") == "EGP"):
        failures.append("wrong_currency")

    if not _check(
        "cache_status in {fresh, cached}",
        cache_status in {"fresh", "cached"},
        f"got {cache_status!r}",
    ):
        failures.append("bad_cache_status")

    # ── Step 12: Response headers ─────────────────────────────────────────────
    cc = r.headers.get("cache-control", "")
    _check("Cache-Control: private", "private" in cc, f"header: {cc!r}")
    _check("Cache-Control: max-age=3600", "max-age=3600" in cc, f"header: {cc!r}")
    xcs = r.headers.get("x-cache-status", "")
    _check("X-Cache-Status header present", bool(xcs), f"got {xcs!r}")

    # ── Step 13: Domain shape ─────────────────────────────────────────────────
    domain: list = body.get("domain", [])
    if _check("domain has 3 clauses", len(domain) == 3, f"got {len(domain)}"):
        _check("domain[0] == state=post", domain[0] == ["state", "=", "post"])
        _check("domain[1][0] == date", domain[1][0] == "date")
        _check("domain[1][1] == >=",   domain[1][1] == ">=")
        _check("domain[2][0] == date", domain[2][0] == "date")
        _check("domain[2][1] == <=",   domain[2][1] == "<=")
    else:
        failures.append("domain_shape")

    # ── Step 14: December 2025 baseline cross-check ───────────────────────────
    print()
    _log(_INFO, "December 2025 baseline cross-check (D0 Part 1 discovery baseline)")
    dec25_entry = next((e for e in months if e.get("month") == "2025-12"), None)
    dec25_amount  = float(dec25_entry.get("amount",       0.0)) if dec25_entry else 0.0
    dec25_records = int(dec25_entry.get("record_count",    0))   if dec25_entry else 0

    if not _check(
        "December 2025 entry present in months array",
        dec25_entry is not None,
    ):
        failures.append("dec25_missing")
    else:
        delta = abs(dec25_amount - _DEC_2025_BASELINE_AMOUNT)
        if not _check(
            f"Dec 2025 amount == {_DEC_2025_BASELINE_AMOUNT:,.2f} EGP (D0 baseline)",
            delta <= _DEC_2025_BASELINE_TOLERANCE,
            f"got {dec25_amount:,.2f}, delta={delta:,.2f}",
        ):
            failures.append(f"dec25_amount_mismatch_delta_{delta:.2f}")

        if not _check(
            f"Dec 2025 record_count == {_DEC_2025_BASELINE_RECORDS} (D0 baseline)",
            dec25_records == _DEC_2025_BASELINE_RECORDS,
            f"got {dec25_records}",
        ):
            failures.append(f"dec25_records_mismatch_{dec25_records}")

    # ── Step 15: Second request — cache hit ───────────────────────────────────
    _log(_INFO, "Second request — verifying cache hit ...")
    with httpx.Client(timeout=30) as client:
        r2 = client.get(url, auth=(USERNAME, PASSWORD))
    body2 = r2.json()
    if not _check(
        "second call cache_status == 'cached'",
        body2.get("cache_status") == "cached",
        f"got {body2.get('cache_status')!r}",
    ):
        failures.append("cache_not_hit_on_second_call")
    if not _check(
        "second call rpc_duration_ms == 0",
        int(body2.get("rpc_duration_ms", -1)) == 0,
        f"got {body2.get('rpc_duration_ms')}",
    ):
        failures.append("cache_rpc_ms_nonzero")

    # ── Structured output ─────────────────────────────────────────────────────
    print()
    print(_SEP)
    print("KPI 6 — 6-Month Collection Trend Verification")
    print(f"Run timestamp : {run_at}")
    print(f"Period        : {period_start}  →  {period_end}")
    print(_SEP)
    print(f"  {'Month':<12} {'Label (EN)':<12} {'Label (AR)':<12} {'Amount (EGP)':>22}  {'Records':>8}")
    print(f"  {'-'*12} {'-'*12} {'-'*12} {'-'*22}  {'-'*8}")
    for entry in months:
        ym   = entry.get("month", "")
        en   = entry.get("label_en", "")
        ar   = entry.get("label_ar", "")
        amt  = float(entry.get("amount", 0))
        cnt  = int(entry.get("record_count", 0))
        zero_note = "  ← zero (expected, Decision 5.7)" if amt == 0 else ""
        print(f"  {ym:<12} {en:<12} {ar:<12} {amt:>22,.2f}  {cnt:>8,}{zero_note}")
    print(f"  {'-'*12} {'-'*12} {'-'*12} {'-'*22}  {'-'*8}")
    print(f"  {'TOTAL':<12} {'':<12} {'':<12} {total_6m:>22,.2f}  {total_count:>8,}")
    print(f"  Average/month                              {avg_monthly:>22,.2f}")
    print(_SEP)
    print()
    print(f"  cache_ttl_seconds : {cache_ttl}  (expected: 3600)")
    print(f"  cache_status      : {cache_status}")
    print(f"  rpc_duration_ms   : {rpc_ms}")
    print()

    print("  ─── MANUAL CROSS-CHECK (REQUIRED for Checkpoint 2) ──────────────")
    print()
    print("  Target: December 2025 — 47,382,098.00 EGP, 429 records (state='post' only)")
    print()
    print("  Steps:")
    print("    1. Open Odoo → RS Accounting → Payment Installments")
    print("    2. Filter: date >= 2025-12-01 AND date <= 2025-12-31, state = Posted")
    print("    3. Sum the 'Amount' column (Posted records only)")
    print("    4. Compare to 47,382,098.00 EGP shown above")
    print("    5. Identity-equal match expected (or explain any delta)")
    print()
    print("  NOTE: Jan-May 2026 showing zero is EXPECTED (Decision 5.7).")
    print("  The operations team is entering historical data retroactively.")
    print("  Do NOT raise a bug for zero months — they are truthful data.")
    print()
    print(_SEP)

    # ── Log row ───────────────────────────────────────────────────────────────
    _append_log(
        run_at=run_at,
        n_months=len(months),
        n_nonzero=n_nonzero,
        total_6m=f"{total_6m:.2f}",
        dec25_amount=f"{dec25_amount:.2f}",
        dec25_records=dec25_records,
        cache_status=cache_status,
        rpc_ms=rpc_ms,
        failures=failures,
    )

    if failures:
        _log(_FAIL, f"Verification FAILED — {len(failures)} assertion(s): {failures}")
        return 1

    _log(_PASS, "All assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
