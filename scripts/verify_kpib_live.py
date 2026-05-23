"""
Live verification for KPI B — Top Overdue Customers.

Usage:
    python scripts/verify_kpib_live.py [--url http://localhost:8000]

Requires the FastAPI server to be running and reachable.
Set VERIFY_USERNAME / VERIFY_PASSWORD env vars (or .env) to override
the default admin credentials.

Exit 0  — all assertions passed
Exit 1  — at least one assertion failed or the server was unreachable

Appends one tab-separated row to logs/kpib_verification.log on each run.
The last column (Odoo UI value) is left blank; Khaled fills it manually
after browser verification.

Baseline (M3-S1 discovery, 2026-05-23, commit 00f3abf):
    total_overdue          = 333,271,714.40 EGP
    overdue_customer_count = 797
    top10_pct              = 21.8%   (72,536,983.00 EGP / 333,271,714.40 EGP)
    top1_amount            = 18,202,000.00 EGP  (rank-1 sanity check)

Note on drift: data is updated daily. Expect small deltas from the baseline.
The verification uses range bounds, not identity-equal, for total_overdue
and customer_count. Top10_pct is checked within ±5pp of baseline.
Concentration ratio and sort order are structural checks — they must always hold.
"""

import argparse
import io
import os
import sys
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

# Force UTF-8 stdout (Windows consoles default to cp1252)
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Config ────────────────────────────────────────────────────────────────────

DEFAULT_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")
USERNAME    = os.environ.get("VERIFY_USERNAME", "admin")
PASSWORD    = os.environ.get("VERIFY_PASSWORD", "password")
ENDPOINT    = "/api/v1/customer-accounts/kpi/top-overdue-customers"
LOG_FILE    = "logs/kpib_verification.log"

# M3-S1 discovery baseline (2026-05-23, commit 00f3abf)
BASELINE_TOTAL        = 333_271_714.40
BASELINE_CUSTOMERS    = 797
BASELINE_TOP10_PCT    = 21.8
BASELINE_TOP1_AMOUNT  = 18_202_000.00
BASELINE_DATE         = "2026-05-23"

# Sanity bounds — late portfolio fluctuates daily; 200M–500M covers drift
MIN_TOTAL        = 200_000_000.0
MAX_TOTAL        = 500_000_000.0
MIN_CUSTOMERS    = 500
MAX_CUSTOMERS    = 1_500
# Top-10 concentration: baseline 21.8%. Allow ±10pp for data drift.
MIN_TOP10_PCT    = 10.0
MAX_TOP10_PCT    = 40.0
CONCENTRATION_N  = 10

_SEP = "═" * 63

# ── Helpers ───────────────────────────────────────────────────────────────────

_PASS = "[PASS]"
_FAIL = "[FAIL]"
_INFO = "[INFO]"


def _log(prefix: str, msg: str) -> None:
    print(f"{prefix} {msg}", flush=True)


def _check(label: str, condition: bool, detail: str = "") -> bool:
    if condition:
        _log(_PASS, f"{label}{(' — ' + detail) if detail else ''}")
    else:
        _log(_FAIL, f"{label}{(' — ' + detail) if detail else ''}")
    return condition


def _append_log_row(
    run_at: str,
    total: float | str,
    customers: int | str,
    top10_pct: float | str,
    delta_total: float | str,
    cache_status: str,
    rpc_ms: int | str,
    error: str = "",
) -> None:
    os.makedirs("logs", exist_ok=True)
    log_exists = os.path.isfile(LOG_FILE)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        if not log_exists:
            f.write(
                "run_at\ttotal_overdue\toverdue_customer_count\ttop10_pct\t"
                "delta_vs_baseline\tcache_status\trpc_duration_ms\todoo_ui_value\terror\n"
            )
        f.write(
            f"{run_at}\t{total}\t{customers}\t{top10_pct}\t"
            f"{delta_total}\t{cache_status}\t{rpc_ms}\t\t{error}\n"
        )


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_URL, help="Backend base URL")
    args = parser.parse_args()
    base_url: str = args.url.rstrip("/")

    url    = f"{base_url}{ENDPOINT}"
    run_at = datetime.now(timezone.utc).isoformat()

    _log(_INFO, f"Target: GET {url}")
    _log(_INFO, f"Auth user: {USERNAME}")

    failures: list[str] = []

    # ── Step 1: GET endpoint ─────────────────────────────────────────────────
    try:
        with httpx.Client(timeout=60) as client:
            r = client.get(url, auth=(USERNAME, PASSWORD))
    except httpx.ConnectError as exc:
        msg = f"Cannot reach {base_url} — is the server running? ({exc})"
        _log(_FAIL, msg)
        _append_log_row(run_at, "", "", "", "", "", "", error=msg)
        return 1

    # ── Step 2: Status code ──────────────────────────────────────────────────
    ok = _check("HTTP 200", r.status_code == 200, f"got {r.status_code}")
    if not ok:
        _log(_INFO, f"Response body: {r.text[:500]}")
        _append_log_row(run_at, "", "", "", "", "", "", error=f"HTTP {r.status_code}")
        return 1

    body: dict = r.json()
    # Log counts only — no customer names in output (PII)
    _log(_INFO, (
        f"total_overdue={body.get('total_overdue')}, "
        f"overdue_customer_count={body.get('overdue_customer_count')}, "
        f"record_count={body.get('record_count')}, "
        f"cache_status={body.get('cache_status')}, "
        f"rpc_ms={body.get('rpc_duration_ms')}, "
        f"top_customers_count={len(body.get('top_customers', []))}"
    ))

    # ── Step 3: Required keys ────────────────────────────────────────────────
    required_keys = (
        "total_overdue", "overdue_customer_count", "record_count",
        "top_n_concentration", "top_customers",
        "currency", "as_of", "cache_status", "rpc_duration_ms", "domain",
    )
    for k in required_keys:
        if not _check(f"key '{k}' present", k in body):
            failures.append(f"missing_key_{k}")

    conc_keys = ("n", "amount", "pct")
    conc = body.get("top_n_concentration", {})
    for k in conc_keys:
        if not _check(f"top_n_concentration.{k} present", k in conc):
            failures.append(f"missing_conc_key_{k}")

    if failures:
        _append_log_row(run_at, "", "", "", "", "", "", error=f"missing keys: {failures}")
        return 1

    # ── Step 4: Extract values ───────────────────────────────────────────────
    total_overdue:    float = float(body["total_overdue"])
    customer_count:   int   = int(body["overdue_customer_count"])
    record_count:     int   = int(body["record_count"])
    cache_status:     str   = body["cache_status"]
    rpc_ms:           int   = int(body["rpc_duration_ms"])
    top_customers:    list  = body["top_customers"]
    conc_n:           int   = int(conc["n"])
    conc_amount:      float = float(conc["amount"])
    conc_pct:         float = float(conc["pct"])
    delta_total             = total_overdue - BASELINE_TOTAL
    delta_sign              = "+" if delta_total >= 0 else ""

    # ── Step 5: Structured summary ───────────────────────────────────────────
    print()
    print(_SEP)
    print("KPI B — Top Overdue Customers Verification")
    print(f"Run timestamp            : {run_at}")
    print(_SEP)
    print(f"Total overdue            : {total_overdue:>20,.2f} EGP")
    print(f"Baseline total           : {BASELINE_TOTAL:>20,.2f} EGP ({BASELINE_DATE})")
    print(f"Delta vs baseline        : {delta_sign}{delta_total:>19,.2f} EGP")
    print(f"Overdue customers        : {customer_count:>20,}")
    print(f"Baseline customers       : {BASELINE_CUSTOMERS:>20,}  ({BASELINE_DATE})")
    print(f"Record count             : {record_count:>20,}  installments")
    print(f"Concentration N          : {conc_n:>20}")
    print(f"Top-{conc_n} amount      : {conc_amount:>20,.2f} EGP")
    print(f"Top-{conc_n} pct         : {conc_pct:>19.2f}%")
    print(f"Baseline top-10 pct      : {BASELINE_TOP10_PCT:>19.1f}%  ({BASELINE_DATE})")
    print(f"Top customers returned   : {len(top_customers):>20}")
    print(f"Cache status             : {cache_status:>20}")
    print(f"RPC duration             : {rpc_ms:>17} ms")
    print(f"Domain used              : {body.get('domain')}")
    print(_SEP)
    print()

    # ── Step 6: Concentration N = _CONCENTRATION_N ───────────────────────────
    if not _check(
        f"top_n_concentration.n == {CONCENTRATION_N}",
        conc_n == CONCENTRATION_N,
        f"got {conc_n}",
    ):
        failures.append("concentration_n_wrong")

    # ── Step 7: Value sanity assertions ──────────────────────────────────────
    if not _check("total_overdue >= MIN_TOTAL",
                  total_overdue >= MIN_TOTAL,
                  f"{total_overdue:,.2f} >= {MIN_TOTAL:,.2f}"):
        failures.append("total_below_min")

    if not _check("total_overdue <= MAX_TOTAL",
                  total_overdue <= MAX_TOTAL,
                  f"{total_overdue:,.2f} <= {MAX_TOTAL:,.2f}"):
        failures.append("total_above_max")

    if not _check("overdue_customer_count >= MIN_CUSTOMERS",
                  customer_count >= MIN_CUSTOMERS,
                  f"got {customer_count:,}"):
        failures.append("customer_count_below_min")

    if not _check("overdue_customer_count <= MAX_CUSTOMERS",
                  customer_count <= MAX_CUSTOMERS,
                  f"got {customer_count:,}"):
        failures.append("customer_count_above_max")

    if not _check("record_count > 0",
                  record_count > 0,
                  f"got {record_count}"):
        failures.append("record_count_is_zero")

    if not _check("currency == 'EGP'",
                  body.get("currency") == "EGP",
                  f"got {body.get('currency')!r}"):
        failures.append("wrong_currency")

    if not _check("cache_status in {fresh, cached}",
                  cache_status in {"fresh", "cached"},
                  f"got {cache_status!r}"):
        failures.append("bad_cache_status")

    # ── Step 8: Concentration pct in expected range ───────────────────────────
    if not _check(
        f"top10_pct in [{MIN_TOP10_PCT:.0f}%, {MAX_TOP10_PCT:.0f}%]",
        MIN_TOP10_PCT <= conc_pct <= MAX_TOP10_PCT,
        f"got {conc_pct:.2f}%",
    ):
        failures.append("concentration_pct_out_of_range")

    # ── Step 9: Concentration amount <= total_overdue ─────────────────────────
    if not _check("top_n_concentration.amount <= total_overdue",
                  conc_amount <= total_overdue + 1.0,
                  f"{conc_amount:,.2f} <= {total_overdue:,.2f}"):
        failures.append("concentration_amount_exceeds_total")

    # ── Step 10: top_customers count <= 20 ───────────────────────────────────
    if not _check("len(top_customers) <= 20",
                  len(top_customers) <= 20,
                  f"got {len(top_customers)}"):
        failures.append("top_customers_exceeds_20")

    # ── Step 11: top_customers sorted descending ──────────────────────────────
    if top_customers:
        amounts = [float(row["due_amount"]) for row in top_customers]
        sorted_ok = all(amounts[i] >= amounts[i + 1] for i in range(len(amounts) - 1))
        if not _check("top_customers sorted desc by due_amount",
                      sorted_ok,
                      f"first={amounts[0]:,.2f}, last={amounts[-1]:,.2f}"):
            failures.append("top_customers_not_sorted")

    # ── Step 12: Rank-1 amount sanity (within 50% of baseline) ───────────────
    if top_customers:
        rank1_amount = float(top_customers[0]["due_amount"])
        rank1_ok = rank1_amount >= BASELINE_TOP1_AMOUNT * 0.5
        if not _check(
            "rank-1 due_amount >= 50% of baseline rank-1",
            rank1_ok,
            f"got {rank1_amount:,.2f}, baseline={BASELINE_TOP1_AMOUNT:,.2f}",
        ):
            failures.append("rank1_amount_too_low")

    # ── Step 13: Domain shape ─────────────────────────────────────────────────
    domain: list = body.get("domain", [])
    if not _check("domain has 3 clauses", len(domain) == 3, f"got {len(domain)}"):
        failures.append("domain_wrong_length")
    else:
        if not _check("domain[0] == ['state','=','post']",
                      domain[0] == ["state", "=", "post"],
                      f"got {domain[0]!r}"):
            failures.append("domain_clause0_wrong")
        if not _check("domain[1] == ['payment_state','in',...]",
                      domain[1][0] == "payment_state" and domain[1][1] == "in",
                      f"got {domain[1]!r}"):
            failures.append("domain_clause1_wrong")
        if not _check("domain[2] == ['date','<',today]",
                      domain[2][0] == "date" and domain[2][1] == "<",
                      f"got {domain[2]!r}"):
            failures.append("domain_clause2_wrong")

    # ── Step 14: Response headers ─────────────────────────────────────────────
    cc  = r.headers.get("cache-control", "")
    xcs = r.headers.get("x-cache-status", "")
    _check("Cache-Control: private",    "private"    in cc,  f"header: {cc!r}")
    _check("Cache-Control: max-age=60", "max-age=60" in cc,  f"header: {cc!r}")
    _check("X-Cache-Status present",    bool(xcs),           f"got {xcs!r}")

    # ── Step 15: Second request — cache hit ───────────────────────────────────
    _log(_INFO, "Issuing second request to verify cache hit ...")
    with httpx.Client(timeout=30) as client:
        r2 = client.get(url, auth=(USERNAME, PASSWORD))
    body2: dict = r2.json()
    if not _check("second call cache_status == 'cached'",
                  body2.get("cache_status") == "cached",
                  f"got {body2.get('cache_status')!r}"):
        failures.append("cache_not_hit_on_second_call")
    if not _check("second call rpc_duration_ms == 0",
                  int(body2.get("rpc_duration_ms", -1)) == 0,
                  f"got {body2.get('rpc_duration_ms')}"):
        failures.append("cache_rpc_ms_nonzero")

    # ── Result ────────────────────────────────────────────────────────────────
    _append_log_row(
        run_at=run_at,
        total=f"{total_overdue:.2f}",
        customers=customer_count,
        top10_pct=f"{conc_pct:.2f}",
        delta_total=f"{delta_total:.2f}",
        cache_status=cache_status,
        rpc_ms=rpc_ms,
    )

    if failures:
        _log(_FAIL, f"Verification FAILED — {len(failures)} assertion(s): {failures}")
        return 1

    print()
    _log(_PASS, "All assertions passed.")
    print()
    print("Next step (manual — identity-equal check against Odoo UI):")
    print("  1. Open Odoo → Installments (rs.installment)")
    print("  2. Filter: State = Posted, Payment State = Unpaid or Partial, Date < Today")
    print("  3. Group By: Customer (partner_id), Measure: Due Amount, sort descending")
    print("  4. Compare Total to 'total_overdue' above.")
    print("     Expected: identity-equal or < 1 EGP drift.")
    print("  5. Compare the top-10 concentration % to top_n_concentration.pct above.")
    print("  Fill in the 'odoo_ui_value' column in logs/kpib_verification.log.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
