"""
Live verification for KPI 1 — Total Portfolio Value.

Usage:
    python scripts/verify_kpi1_live.py [--url http://localhost:8000]

Requires the FastAPI server to be running and reachable.
Set VERIFY_USERNAME / VERIFY_PASSWORD env vars (or .env) to override
the default admin credentials.

Exit 0  — all assertions passed
Exit 1  — at least one assertion failed or the server was unreachable

Appends one tab-separated row to logs/kpi1_verification.log on each run.
The last column (Odoo UI value) is left blank; Khaled fills it manually
after browser verification.
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
USERNAME = os.environ.get("VERIFY_USERNAME", "admin")
PASSWORD = os.environ.get("VERIFY_PASSWORD", "password")
ENDPOINT = "/api/v1/collections/kpi/total-portfolio-value"
LOG_FILE = "logs/kpi1_verification.log"

BASELINE_EGP = 6_123_549_625.23  # 2026-05-14 snapshot
BASELINE_DATE = "2026-05-14"

# Sanity bounds — portfolio total is stable; 5B–8B covers any realistic drift
MIN_VALUE_EGP = 5_000_000_000.0
MAX_VALUE_EGP = 8_000_000_000.0
MIN_RECORD_COUNT = 40_000  # ~42,970 at baseline; hard floor for data integrity

_SEP = "═" * 63

# ── Helpers ───────────────────────────────────────────────────────────────────

_PASS = "[PASS]"
_FAIL = "[FAIL]"
_INFO = "[INFO]"


def _log(prefix: str, msg: str) -> None:
    print(f"{prefix} {msg}", flush=True)


def _append_log_row(
    run_at: str,
    backend_value: float | str,
    baseline: float,
    delta: float | str,
    record_count: int | str,
    cache_status: str,
    rpc_ms: int | str,
    error: str = "",
) -> None:
    os.makedirs("logs", exist_ok=True)
    log_exists = os.path.isfile(LOG_FILE)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        if not log_exists:
            f.write(
                "run_at\tbackend_value\tbaseline\tdelta\t"
                "record_count\tcache_status\trpc_duration_ms\todoo_ui_value\n"
            )
        odoo_ui = ""  # Khaled fills this manually after browser verification
        f.write(
            f"{run_at}\t{backend_value}\t{baseline}\t{delta}\t"
            f"{record_count}\t{cache_status}\t{rpc_ms}\t{odoo_ui}\n"
        )


def _check(label: str, condition: bool, detail: str = "") -> bool:
    if condition:
        _log(_PASS, f"{label}{(' — ' + detail) if detail else ''}")
    else:
        _log(_FAIL, f"{label}{(' — ' + detail) if detail else ''}")
    return condition


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_URL, help="Backend base URL")
    args = parser.parse_args()
    base_url: str = args.url.rstrip("/")

    url = f"{base_url}{ENDPOINT}"
    run_at = datetime.now(timezone.utc).isoformat()

    _log(_INFO, f"Target: GET {url}")
    _log(_INFO, f"Auth user: {USERNAME}")

    failures: list[str] = []

    # ── Step 1: GET /api/v1/collections/kpi/total-portfolio-value ────────────
    try:
        with httpx.Client(timeout=60) as client:
            r = client.get(url, auth=(USERNAME, PASSWORD))
    except httpx.ConnectError as exc:
        msg = f"Cannot reach {base_url} — is the server running? ({exc})"
        _log(_FAIL, msg)
        _append_log_row(run_at, "", BASELINE_EGP, "", "", "", "", error=msg)
        return 1

    # ── Step 2: Status code ───────────────────────────────────────────────────
    ok = _check("HTTP 200", r.status_code == 200, f"got {r.status_code}")
    if not ok:
        _log(_INFO, f"Response body: {r.text[:500]}")
        _append_log_row(run_at, "", BASELINE_EGP, "", "", "", "", error=f"HTTP {r.status_code}")
        return 1

    body: dict = r.json()
    _log(_INFO, f"Response body: {body}")

    # ── Step 3: Required keys ─────────────────────────────────────────────────
    required_keys = ("value", "currency", "record_count", "as_of",
                     "cache_status", "rpc_duration_ms", "domain")
    for k in required_keys:
        if not _check(f"key '{k}' present", k in body):
            failures.append(f"missing_key_{k}")

    if failures:
        _append_log_row(run_at, "", BASELINE_EGP, "", "", "", "", error=f"missing keys: {failures}")
        return 1

    # ── Step 4: Extract values ────────────────────────────────────────────────
    value: float = float(body["value"])
    record_count: int = int(body["record_count"])
    cache_status: str = body["cache_status"]
    rpc_ms: int = int(body["rpc_duration_ms"])
    delta = value - BASELINE_EGP
    delta_sign = "+" if delta >= 0 else ""

    # ── Step 5: Structured summary ────────────────────────────────────────────
    print()
    print(_SEP)
    print("KPI 1 — Total Portfolio Value Verification")
    print(f"Run timestamp     : {run_at}")
    print(_SEP)
    print(f"Backend value     : {value:>20,.2f} EGP")
    print(f"Snapshot baseline : {BASELINE_EGP:>20,.2f} EGP ({BASELINE_DATE})")
    print(f"Delta vs baseline : {delta_sign}{delta:>19,.2f} EGP")
    print(f"Record count      : {record_count:>20,} installments")
    print(f"Cache status      : {cache_status:>20}")
    print(f"RPC duration      : {rpc_ms:>17} ms")
    print(f"Domain used       : {body.get('domain')}")
    print(_SEP)
    print()

    # ── Step 6: Value sanity assertions ───────────────────────────────────────
    if not _check("value >= MIN_VALUE_EGP",
                  value >= MIN_VALUE_EGP,
                  f"{value:,.2f} >= {MIN_VALUE_EGP:,.2f}"):
        failures.append("value_below_min")

    if not _check("value <= MAX_VALUE_EGP",
                  value <= MAX_VALUE_EGP,
                  f"{value:,.2f} <= {MAX_VALUE_EGP:,.2f}"):
        failures.append("value_above_max")

    if not _check("record_count >= MIN_RECORD_COUNT",
                  record_count >= MIN_RECORD_COUNT,
                  f"got {record_count:,}"):
        failures.append("record_count_low")

    if not _check("currency == 'EGP'",
                  body.get("currency") == "EGP",
                  f"got {body.get('currency')!r}"):
        failures.append("wrong_currency")

    if not _check("cache_status in {fresh, cached}",
                  cache_status in {"fresh", "cached"},
                  f"got {cache_status!r}"):
        failures.append("bad_cache_status")

    # ── Step 7: Response headers ──────────────────────────────────────────────
    cc = r.headers.get("cache-control", "")
    _check("Cache-Control: private", "private" in cc, f"header: {cc!r}")
    _check("Cache-Control: max-age=60", "max-age=60" in cc, f"header: {cc!r}")
    xcs = r.headers.get("x-cache-status", "")
    _check("X-Cache-Status header present", bool(xcs), f"got {xcs!r}")

    # ── Step 8: Domain shape (must be empty list) ─────────────────────────────
    domain: list = body.get("domain", None)
    if not _check("domain is empty list []", domain == [], f"got {domain!r}"):
        failures.append("domain_not_empty")

    # ── Step 9: Second request — cache hit ───────────────────────────────────
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
        backend_value=f"{value:.2f}",
        baseline=BASELINE_EGP,
        delta=f"{delta:.2f}",
        record_count=record_count,
        cache_status=cache_status,
        rpc_ms=rpc_ms,
    )

    if failures:
        _log(_FAIL, f"Verification FAILED — {len(failures)} assertion(s): {failures}")
        return 1

    print()
    _log(_PASS, "All assertions passed.")
    print()
    print("Next step (manual):")
    print("  1. Open Odoo → Collections Mgmt → All Installments")
    print("  2. Set Measures: Amount")
    print("  3. Compare the \"Amount\" aggregate (top of the pivot or list view)")
    print("     to the Backend value above.")
    print(f"  4. Expected match: identity-equal at EGP level (Khaled confirmed")
    print(f"     the All Installments Amount total = baseline on 2026-05-16).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
