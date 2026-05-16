"""
Live verification for KPI 3 — Pending Check Exposure.

Usage:
    python scripts/verify_kpi3_live.py [--url http://localhost:8000]

Requires the FastAPI server to be running and reachable.
Set VERIFY_USERNAME / VERIFY_PASSWORD env vars (or .env) to override
the default admin credentials.

Exit 0  — all assertions passed
Exit 1  — at least one assertion failed or the server was unreachable

Appends one tab-separated row to logs/kpi3_verification.log on each run.
The last column (odoo_ui_value) is left blank; Khaled fills it manually
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
ENDPOINT = "/api/v1/collections/kpi/pending-check-exposure"
LOG_FILE = "logs/kpi3_verification.log"

# D0 confirmed value (2026-05-16): 518,235,384.10 EGP (state='post' domain).
# Daily drift expected as payments are posted in RS Accounting.
BASELINE_EGP = 518_235_384.10
BASELINE_DATE = "2026-05-16"

# Sanity bounds — 520.5M EGP baseline (2026-05-14) ± 100M for realistic drift.
MIN_VALUE_EGP = 400_000_000.0
MAX_VALUE_EGP = 700_000_000.0

# Exact derivation note string the service must return (Decision 4.3).
EXPECTED_DERIVATION_NOTE = "value = paid_amount_sum - actual_paid_sum"

_SEP = "═" * 66
_PASS = "[PASS]"
_FAIL = "[FAIL]"
_INFO = "[INFO]"


def _log(prefix: str, msg: str) -> None:
    print(f"{prefix} {msg}", flush=True)


def _check(label: str, condition: bool, detail: str = "") -> bool:
    marker = _PASS if condition else _FAIL
    _log(marker, f"{label}{(' — ' + detail) if detail else ''}")
    return condition


def _append_log_row(
    run_at: str,
    backend_value: "float | str",
    paid_sum: "float | str",
    actual_sum: "float | str",
    record_count: "int | str",
    cache_status: str,
    rpc_ms: "int | str",
    result: str,
    error: str = "",
) -> None:
    os.makedirs("logs", exist_ok=True)
    log_exists = os.path.isfile(LOG_FILE)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        if not log_exists:
            f.write(
                "run_at\tresult\tbackend_value\tpaid_amount_sum\tactual_paid_sum\t"
                "record_count\tcache_status\trpc_duration_ms\todoo_ui_value\terror\n"
            )
        odoo_ui = ""  # Khaled fills this manually after browser verification
        f.write(
            f"{run_at}\t{result}\t{backend_value}\t{paid_sum}\t{actual_sum}\t"
            f"{record_count}\t{cache_status}\t{rpc_ms}\t{odoo_ui}\t{error}\n"
        )


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

    # ── Step 1: GET /api/v1/collections/kpi/pending-check-exposure ───────────
    try:
        with httpx.Client(timeout=60) as client:
            r = client.get(url, auth=(USERNAME, PASSWORD))
    except httpx.ConnectError as exc:
        msg = f"Cannot reach {base_url} — is the server running? ({exc})"
        _log(_FAIL, msg)
        _append_log_row(run_at, "", "", "", "", "", "", "FAIL", error=msg)
        return 1

    # ── Step 2: Status code ───────────────────────────────────────────────────
    ok = _check("HTTP 200", r.status_code == 200, f"got {r.status_code}")
    if not ok:
        _log(_INFO, f"Response body: {r.text[:500]}")
        _append_log_row(run_at, "", "", "", "", "", "", "FAIL",
                        error=f"HTTP {r.status_code}")
        return 1

    body: dict = r.json()
    _log(_INFO, f"Response body: {body}")

    # ── Step 3: Required keys (standard + KPI 3 specific) ────────────────────
    standard_keys = ("value", "currency", "record_count", "as_of",
                     "cache_status", "rpc_duration_ms", "domain")
    kpi3_keys = ("paid_amount_sum", "actual_paid_sum",
                 "derivation_note", "data_quality_warning")
    for k in standard_keys + kpi3_keys:
        if not _check(f"key '{k}' present", k in body):
            failures.append(f"missing_key_{k}")

    if failures:
        _append_log_row(run_at, "", "", "", "", "", "", "FAIL",
                        error=f"missing keys: {failures}")
        return 1

    # ── Step 4: Extract values ────────────────────────────────────────────────
    value: float = float(body["value"])
    paid_sum: float = float(body["paid_amount_sum"])
    actual_sum: float = float(body["actual_paid_sum"])
    record_count: int = int(body["record_count"])
    cache_status: str = body["cache_status"]
    rpc_ms: int = int(body["rpc_duration_ms"])
    delta = value - BASELINE_EGP
    delta_sign = "+" if delta >= 0 else ""

    # ── Step 5: Structured summary ────────────────────────────────────────────
    print()
    print(_SEP)
    print("KPI 3 — Pending Check Exposure Verification")
    print(f"Run timestamp     : {run_at}")
    print(_SEP)
    print(f"Backend value     : {value:>20,.2f} EGP")
    print(f"D0 baseline       : {BASELINE_EGP:>20,.2f} EGP ({BASELINE_DATE})")
    print(f"Delta vs baseline : {delta_sign}{delta:>19,.2f} EGP")
    print(f"  paid_amount_sum : {paid_sum:>20,.2f} EGP")
    print(f"  actual_paid_sum : {actual_sum:>20,.2f} EGP")
    print(f"Record count      : {record_count:>20,} installments")
    print(f"Cache status      : {cache_status:>20}")
    print(f"RPC duration      : {rpc_ms:>17} ms")
    print(f"Domain used       : {body.get('domain')}")
    print(f"Derivation note   : {body.get('derivation_note')!r}")
    print(f"DQ warning        : {body.get('data_quality_warning')!r}")
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

    if not _check("record_count > 0",
                  record_count > 0,
                  f"got {record_count:,}"):
        failures.append("record_count_zero")

    if not _check("currency == 'EGP'",
                  body.get("currency") == "EGP",
                  f"got {body.get('currency')!r}"):
        failures.append("wrong_currency")

    if not _check("cache_status in {fresh, cached}",
                  cache_status in {"fresh", "cached"},
                  f"got {cache_status!r}"):
        failures.append("bad_cache_status")

    # ── Step 7: KPI 3 specific — domain shape ─────────────────────────────────
    # Decision 4.1: single-clause state='post' (same as KPI 1).
    expected_domain = [["state", "=", "post"]]
    domain: list = body.get("domain", None)
    if not _check(
        "domain == [['state','=','post']]",
        domain == expected_domain,
        f"got {domain!r}",
    ):
        failures.append("domain_wrong")

    # ── Step 8: KPI 3 specific — derivation correctness ──────────────────────
    if not _check(
        "paid_amount_sum > actual_paid_sum (positive exposure)",
        paid_sum > actual_sum,
        f"paid={paid_sum:,.2f}, actual={actual_sum:,.2f}",
    ):
        failures.append("negative_exposure")

    derived_check = abs(paid_sum - actual_sum - value)
    if not _check(
        "paid_amount_sum - actual_paid_sum == value (within 0.01 EGP)",
        derived_check < 0.01,
        f"delta={derived_check:.4f}",
    ):
        failures.append("derivation_mismatch")

    # ── Step 9: KPI 3 specific — derivation_note field ───────────────────────
    if not _check(
        f"derivation_note == {EXPECTED_DERIVATION_NOTE!r}",
        body.get("derivation_note") == EXPECTED_DERIVATION_NOTE,
        f"got {body.get('derivation_note')!r}",
    ):
        failures.append("derivation_note_wrong")

    # ── Step 10: KPI 3 specific — data_quality_warning field ─────────────────
    # Normal case: None (no anomaly). "value_is_negative" only if derived < 0.
    dqw = body.get("data_quality_warning")
    if value >= 0:
        if not _check(
            "data_quality_warning is None (value >= 0, no anomaly)",
            dqw is None,
            f"got {dqw!r}",
        ):
            failures.append("unexpected_dq_warning")
    else:
        if not _check(
            "data_quality_warning == 'value_is_negative' (value < 0)",
            dqw == "value_is_negative",
            f"got {dqw!r}",
        ):
            failures.append("missing_dq_warning")

    # ── Step 11: Response headers ─────────────────────────────────────────────
    cc = r.headers.get("cache-control", "")
    _check("Cache-Control: private", "private" in cc, f"header: {cc!r}")
    _check("Cache-Control: max-age=60", "max-age=60" in cc, f"header: {cc!r}")
    xcs = r.headers.get("x-cache-status", "")
    _check("X-Cache-Status header present", bool(xcs), f"got {xcs!r}")

    # ── Step 12: Second request — cache hit ───────────────────────────────────
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
        paid_sum=f"{paid_sum:.2f}",
        actual_sum=f"{actual_sum:.2f}",
        record_count=record_count,
        cache_status=cache_status,
        rpc_ms=rpc_ms,
        result="PASS" if not failures else "FAIL",
        error="; ".join(failures) if failures else "",
    )

    if failures:
        _log(_FAIL, f"Verification FAILED — {len(failures)} assertion(s): {failures}")
        return 1

    print()
    _log(_PASS, "All assertions passed.")
    print()
    print("Next step (manual cross-check for Khaled):")
    print("  Open Odoo → Collections Mgmt → All Installments")
    print("  Set Measures to: Paid Amount, Actual Paid Amount")
    print("  Compute manually: (Paid Amount Total) - (Actual Paid Amount Total)")
    print("  Compare to Backend value above.")
    print()
    print("  Note: Odoo UI 'All Installments' view uses state='post' (Decision 2.4),")
    print("  which is exactly the domain this backend uses (Decision 4.1).")
    print("  The manual subtraction should match identity-equal at EGP level.")
    print("  Odoo has no single 'Pending Check Exposure' measure — the derivation")
    print("  must be done manually from the two column totals.")
    print()
    print(f"  Backend value     : {value:>20,.2f} EGP")
    print(f"  paid_amount_sum   : {paid_sum:>20,.2f} EGP")
    print(f"  actual_paid_sum   : {actual_sum:>20,.2f} EGP")
    return 0


if __name__ == "__main__":
    sys.exit(main())
