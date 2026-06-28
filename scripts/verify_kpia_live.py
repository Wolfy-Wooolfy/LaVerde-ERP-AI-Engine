"""
Live verification for KPI A — Total Customer Receivables.

Usage:
    python scripts/verify_kpia_live.py [--url http://localhost:8000]

Requires the FastAPI server to be running and reachable.
Set VERIFY_USERNAME / VERIFY_PASSWORD env vars (or .env) to override
the default admin credentials.

Exit 0  — all assertions passed
Exit 1  — at least one assertion failed or the server was unreachable

Appends one tab-separated row to logs/kpia_verification.log on each run.
The last column (Odoo UI value) is left blank; Khaled fills it manually
after browser verification.

Baseline (M3-S1 discovery, 2026-05-23, commit 00f3abf):
    value          = 2,634,209,716.28 EGP
    customer_count = 1,272
    record_count   = 42,413 installments
"""

import argparse
import io
import os
import sys
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv

from _lib.api_session import ApiLoginError, login as api_login

load_dotenv(dotenv_path=".env")

# Force UTF-8 stdout (Windows consoles default to cp1252)
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Config ────────────────────────────────────────────────────────────────────

DEFAULT_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")
USERNAME    = os.environ.get("VERIFY_USERNAME", "admin")
PASSWORD    = os.environ.get("VERIFY_PASSWORD", "password")
ENDPOINT    = "/api/v1/customer-accounts/kpi/total-receivables"
LOG_FILE    = "logs/kpia_verification.log"

# M3-S1 discovery baseline (2026-05-23, commit 00f3abf)
BASELINE_VALUE_EGP    = 2_634_209_716.28
BASELINE_CUSTOMERS    = 1_272
BASELINE_RECORDS      = 42_413
BASELINE_DATE         = "2026-05-23"

# Sanity bounds — portfolio due is stable month-over-month; 2B–4B covers drift
MIN_VALUE_EGP    = 2_000_000_000.0
MAX_VALUE_EGP    = 4_000_000_000.0
MIN_CUSTOMERS    = 1_000
MAX_CUSTOMERS    = 2_000
MIN_RECORD_COUNT = 40_000   # hard floor; baseline = 42,413

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
    value: float | str,
    customers: int | str,
    records: int | str,
    delta_value: float | str,
    cache_status: str,
    rpc_ms: int | str,
    error: str = "",
) -> None:
    os.makedirs("logs", exist_ok=True)
    log_exists = os.path.isfile(LOG_FILE)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        if not log_exists:
            f.write(
                "run_at\tvalue\tcustomer_count\trecord_count\t"
                "delta_vs_baseline\tcache_status\trpc_duration_ms\todoo_ui_value\terror\n"
            )
        f.write(
            f"{run_at}\t{value}\t{customers}\t{records}\t"
            f"{delta_value}\t{cache_status}\t{rpc_ms}\t\t{error}\n"
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

    # ── Step 1: ONE login per process (limiter 10/minute), then GET ──────────
    try:
        client = api_login(base_url)
    except ApiLoginError as exc:
        _log(_FAIL, f"Session login failed: {exc}")
        _append_log_row(run_at, "", "", "", "", "", "", error=f"login failed: {exc}")
        return 1
    except httpx.ConnectError as exc:
        msg = f"Cannot reach {base_url} — is the server running? ({exc})"
        _log(_FAIL, msg)
        _append_log_row(run_at, "", "", "", "", "", "", error=msg)
        return 1

    try:
        r = client.get(ENDPOINT, timeout=60)

        # ── Step 2: Status code ───────────────────────────────────────────────────
        ok = _check("HTTP 200", r.status_code == 200, f"got {r.status_code}")
        if not ok:
            _log(_INFO, f"Response body: {r.text[:500]}")
            _append_log_row(run_at, "", "", "", "", "", "", error=f"HTTP {r.status_code}")
            return 1

        body: dict = r.json()
        _log(_INFO, f"Response body: {body}")

        # ── Step 3: Required keys ─────────────────────────────────────────────────
        required_keys = (
            "value", "customer_count", "record_count",
            "currency", "as_of", "cache_status", "rpc_duration_ms", "domain",
        )
        for k in required_keys:
            if not _check(f"key '{k}' present", k in body):
                failures.append(f"missing_key_{k}")

        if failures:
            _append_log_row(run_at, "", "", "", "", "", "", error=f"missing keys: {failures}")
            return 1

        # ── Step 4: Extract values ────────────────────────────────────────────────
        value:          float = float(body["value"])
        customer_count: int   = int(body["customer_count"])
        record_count:   int   = int(body["record_count"])
        cache_status:   str   = body["cache_status"]
        rpc_ms:         int   = int(body["rpc_duration_ms"])
        delta_value           = value - BASELINE_VALUE_EGP
        delta_sign            = "+" if delta_value >= 0 else ""

        # ── Step 5: Structured summary ────────────────────────────────────────────
        print()
        print(_SEP)
        print("KPI A — Total Customer Receivables Verification")
        print(f"Run timestamp          : {run_at}")
        print(_SEP)
        print(f"Backend value          : {value:>20,.2f} EGP")
        print(f"Snapshot baseline      : {BASELINE_VALUE_EGP:>20,.2f} EGP ({BASELINE_DATE})")
        print(f"Delta vs baseline      : {delta_sign}{delta_value:>19,.2f} EGP")
        print(f"Customer count         : {customer_count:>20,}")
        print(f"Baseline customers     : {BASELINE_CUSTOMERS:>20,}  ({BASELINE_DATE})")
        print(f"Record count           : {record_count:>20,}  installments")
        print(f"Baseline records       : {BASELINE_RECORDS:>20,}  ({BASELINE_DATE})")
        print(f"Cache status           : {cache_status:>20}")
        print(f"RPC duration           : {rpc_ms:>17} ms")
        print(f"Domain used            : {body.get('domain')}")
        print(_SEP)
        print()

        # ── Step 6: Value sanity assertions ──────────────────────────────────────
        if not _check("value >= MIN_VALUE_EGP",
                      value >= MIN_VALUE_EGP,
                      f"{value:,.2f} >= {MIN_VALUE_EGP:,.2f}"):
            failures.append("value_below_min")

        if not _check("value <= MAX_VALUE_EGP",
                      value <= MAX_VALUE_EGP,
                      f"{value:,.2f} <= {MAX_VALUE_EGP:,.2f}"):
            failures.append("value_above_max")

        if not _check("customer_count >= MIN_CUSTOMERS",
                      customer_count >= MIN_CUSTOMERS,
                      f"got {customer_count:,}"):
            failures.append("customer_count_below_min")

        if not _check("customer_count <= MAX_CUSTOMERS",
                      customer_count <= MAX_CUSTOMERS,
                      f"got {customer_count:,}"):
            failures.append("customer_count_above_max")

        if not _check("record_count >= MIN_RECORD_COUNT",
                      record_count >= MIN_RECORD_COUNT,
                      f"got {record_count:,}"):
            failures.append("record_count_below_min")

        if not _check("record_count > 0",
                      record_count > 0,
                      f"got {record_count} — would indicate __count misread"):
            failures.append("record_count_is_zero")

        if not _check("currency == 'EGP'",
                      body.get("currency") == "EGP",
                      f"got {body.get('currency')!r}"):
            failures.append("wrong_currency")

        if not _check("cache_status in {fresh, cached}",
                      cache_status in {"fresh", "cached"},
                      f"got {cache_status!r}"):
            failures.append("bad_cache_status")

        # ── Step 7: Domain shape ──────────────────────────────────────────────────
        expected_domain = [["state", "=", "post"]]
        domain: list = body.get("domain", None)
        if not _check(
            "domain == [['state','=','post']]",
            domain == expected_domain,
            f"got {domain!r}",
        ):
            failures.append("domain_wrong")

        # ── Step 8: Response headers ──────────────────────────────────────────────
        cc  = r.headers.get("cache-control", "")
        xcs = r.headers.get("x-cache-status", "")
        _check("Cache-Control: private",  "private"    in cc,  f"header: {cc!r}")
        _check("Cache-Control: max-age=60", "max-age=60" in cc,  f"header: {cc!r}")
        _check("X-Cache-Status header present", bool(xcs), f"got {xcs!r}")

        # ── Step 9: Second request — cache hit ───────────────────────────────────
        _log(_INFO, "Issuing second request to verify cache hit ...")
        r2 = client.get(ENDPOINT, timeout=30)
        body2: dict = r2.json()
        if not _check("second call cache_status == 'cached'",
                      body2.get("cache_status") == "cached",
                      f"got {body2.get('cache_status')!r}"):
            failures.append("cache_not_hit_on_second_call")
        if not _check("second call rpc_duration_ms == 0",
                      int(body2.get("rpc_duration_ms", -1)) == 0,
                      f"got {body2.get('rpc_duration_ms')}"):
            failures.append("cache_rpc_ms_nonzero")
    finally:
        client.close()

    # ── Result ────────────────────────────────────────────────────────────────
    _append_log_row(
        run_at=run_at,
        value=f"{value:.2f}",
        customers=customer_count,
        records=record_count,
        delta_value=f"{delta_value:.2f}",
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
    print("  2. Filter: State = Posted")
    print("  3. Group By: Customer (partner_id)")
    print("  4. Set Measures: Due Amount")
    print("  5. Compare the total aggregate to Backend value above.")
    print("     Expected: identity-equal (delta should be 0.00 EGP or < 1 EGP drift).")
    print("  6. Also compare Customer count to the customer_count above.")
    print("  Fill in the 'odoo_ui_value' column in logs/kpia_verification.log.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
