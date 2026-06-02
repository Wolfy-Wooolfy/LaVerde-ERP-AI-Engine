"""
Live verification for HR KPI C — Payroll Risk Dashboard.

Usage:
    python scripts/verify_kpi_c_payroll_risk_live.py [--url http://localhost:8000]

Requires the FastAPI server to be running and reachable.
Set VERIFY_USERNAME / VERIFY_PASSWORD env vars (or .env) to override
the default admin credentials.

Exits 0 always. Findings are logged to the TSV at
logs/hr_kpi_c_payroll_risk_verification.log and printed to stdout with
[PASS]/[FAIL]/[INFO]/[ALERT] markers.

Hard FAILs are limited to structural invariants:
  - total_active == sum(bucket counts 1..7)
  - num_buckets == 7
  - bucket labels in fixed order
  - all bucket counts >= 0
  - orphan_contracts_count >= 0
  - cache_status in {fresh, cached}
  - rpc_duration_ms >= 0

[ALERT] (not FAIL) conditions:
  - bucket 2 (expired) > 0: active employees with payroll-blocking contracts

Drift vs baselines is reported as [INFO] only — never a hard FAIL.

Appends one TSV row to logs/hr_kpi_c_payroll_risk_verification.log.

Baselines (locked from verification run 2026-05-29):
  bucket active_without_contract  : 17
  bucket expired                  : 0   (alert if > 0)
  bucket open_ended               : 1
  orphan_contracts_count          : 17
  sum(buckets 1..7)               : 136
"""

import argparse
import io
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

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
ENDPOINT    = "/api/v1/hr/kpi/payroll-risk-dashboard"
LOG_FILE    = "logs/hr_kpi_c_payroll_risk_verification.log"
CAIRO_TZ    = ZoneInfo("Africa/Cairo")

# Fixed bucket label order — any deviation is a structural FAIL
EXPECTED_BUCKET_LABELS = [
    "active_without_contract",
    "expired",
    "expiring_45d",
    "expiring_90d",
    "expiring_135d",
    "beyond_135d",
    "open_ended",
]

# Drift-reference baselines (locked 2026-05-29).
# Used for [INFO] delta lines only — NOT hard FAIL checks.
BASELINE_2026_05_29 = {
    "active_without_contract": 17,
    "expired":                  0,
    "expiring_45d":            None,   # not locked — wave changes daily
    "expiring_90d":            None,
    "expiring_135d":           None,
    "beyond_135d":             None,
    "open_ended":               1,
    "orphan_contracts_count":  17,
    "total_active":           136,
}

_SEP = "═" * 68

# ── Helpers ───────────────────────────────────────────────────────────────────

_PASS  = "[PASS]"
_FAIL  = "[FAIL]"
_INFO  = "[INFO]"
_ALERT = "[ALERT]"


def _log(prefix: str, msg: str) -> None:
    print(f"{prefix} {msg}", flush=True)


def _check(label: str, condition: bool, detail: str = "") -> bool:
    if condition:
        _log(_PASS, label)
    else:
        _log(_FAIL, f"{label}{(' — ' + detail) if detail else ''}")
    return condition


def _append_log_row(
    run_at: str,
    total_active: int | str,
    bucket_sum: int | str,
    num_buckets: int | str,
    expired_count: int | str,
    orphan_count: int | str,
    reference_date: str,
    cache_status: str,
    rpc_ms: int | str,
    error: str = "",
) -> None:
    os.makedirs("logs", exist_ok=True)
    log_exists = os.path.isfile(LOG_FILE)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        if not log_exists:
            f.write(
                "run_at\ttotal_active\tbucket_sum\tnum_buckets\t"
                "expired_count\torphan_count\treference_date\t"
                "cache_status\trpc_duration_ms\terror\n"
            )
        f.write(
            f"{run_at}\t{total_active}\t{bucket_sum}\t{num_buckets}\t"
            f"{expired_count}\t{orphan_count}\t{reference_date}\t"
            f"{cache_status}\t{rpc_ms}\t{error}\n"
        )


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_URL, help="Backend base URL")
    args = parser.parse_args()
    base_url: str = args.url.rstrip("/")

    url    = f"{base_url}{ENDPOINT}"
    run_at = datetime.now(timezone.utc).isoformat()
    cairo_today = datetime.now(CAIRO_TZ).date().isoformat()

    _log(_INFO, f"Target: GET {url}")
    _log(_INFO, f"Auth user: {USERNAME}")
    _log(_INFO, f"Expected reference_date (Cairo today): {cairo_today}")

    failures: list[str] = []

    # ── Step 1: GET /api/v1/hr/kpi/payroll-risk-dashboard ────────────────────
    try:
        with httpx.Client(timeout=60) as http:
            r = http.get(url, auth=(USERNAME, PASSWORD))
    except httpx.ConnectError as exc:
        msg = f"Cannot reach {base_url} — is the server running? ({exc})"
        _log(_FAIL, msg)
        _append_log_row(run_at, "", "", "", "", "", "", "", "", error=msg)
        return 0

    # ── Step 2: Status code ───────────────────────────────────────────────────
    ok = _check("HTTP 200", r.status_code == 200, f"got {r.status_code}")
    if not ok:
        _log(_INFO, f"Response body: {r.text[:500]}")
        _append_log_row(run_at, "", "", "", "", "", "", "", "",
                        error=f"HTTP {r.status_code}")
        return 0

    body: dict = r.json()
    _log(_INFO, f"Response body (truncated): {str(body)[:800]}")

    # ── Step 3: Required keys ─────────────────────────────────────────────────
    required_keys = (
        "buckets", "department_breakdown_expired", "department_breakdown_expiring_45d",
        "orphan_contracts_count", "total_active", "reference_date",
        "as_of", "cache_status", "rpc_duration_ms",
    )
    for k in required_keys:
        if not _check(f"key '{k}' present", k in body):
            failures.append(f"missing_key_{k}")

    if failures:
        _append_log_row(run_at, "", "", "", "", "", "", "", "",
                        error=f"missing keys: {failures}")
        return 0

    # ── Step 4: Extract values ────────────────────────────────────────────────
    total_active:   int  = int(body["total_active"])
    orphan_count:   int  = int(body["orphan_contracts_count"])
    buckets:        list = body["buckets"]
    reference_date: str  = body["reference_date"]
    cache_status:   str  = body["cache_status"]
    rpc_ms:         int  = int(body["rpc_duration_ms"])

    num_buckets  = len(buckets)
    bucket_sum   = sum(b["count"] for b in buckets)
    bucket_labels = [b["label"] for b in buckets]
    bucket_dict  = {b["label"]: b["count"] for b in buckets}
    expired_count = bucket_dict.get("expired", -1)

    # ── Step 5: Structured summary ────────────────────────────────────────────
    print()
    print(_SEP)
    print("KPI C (HR) — Payroll Risk Dashboard Verification")
    print(f"Run timestamp          : {run_at}")
    print(_SEP)
    print(f"total_active           : {total_active:>10}   (baseline {BASELINE_2026_05_29['total_active']})")
    print(f"orphan_contracts_count : {orphan_count:>10}   (baseline {BASELINE_2026_05_29['orphan_contracts_count']})")
    print(f"num_buckets            : {num_buckets:>10}   (expected 7)")
    print(f"sum(bucket counts)     : {bucket_sum:>10}   (must == total_active)")
    print(f"reference_date         : {reference_date:>10}   (expected {cairo_today})")
    print(f"cache_status           : {cache_status:>10}")
    print(f"rpc_duration_ms        : {rpc_ms:>7} ms")
    print(f"as_of                  : {body.get('as_of')}")
    print(_SEP)
    print("Bucket breakdown:")
    for b in buckets:
        label     = b["label"]
        count     = b["count"]
        baseline  = BASELINE_2026_05_29.get(label)
        if baseline is None:
            delta_str = "  (no locked baseline)"
        else:
            delta = count - baseline
            delta_str = f"  (Δ {delta:+d} vs 2026-05-29 baseline {baseline})" if delta != 0 else f"  (= baseline {baseline})"
        print(f"  {label:<28} : {count:>5}{delta_str}")
    orphan_delta = orphan_count - BASELINE_2026_05_29["orphan_contracts_count"]
    orphan_delta_str = (
        f"  (Δ {orphan_delta:+d} vs 2026-05-29 baseline {BASELINE_2026_05_29['orphan_contracts_count']})"
        if orphan_delta != 0
        else f"  (= baseline {BASELINE_2026_05_29['orphan_contracts_count']})"
    )
    print(f"  {'orphan_contracts_count':<28} : {orphan_count:>5}{orphan_delta_str}")
    print(_SEP)
    print("Department breakdown (expired):")
    for row in body.get("department_breakdown_expired", []):
        print(f"  {row.get('department_name', '?'):<30} : {row.get('count', 0):>5}")
    if not body.get("department_breakdown_expired"):
        print("  (empty — expected when expired bucket == 0)")
    print("Department breakdown (expiring_45d):")
    for row in body.get("department_breakdown_expiring_45d", []):
        print(f"  {row.get('department_name', '?'):<30} : {row.get('count', 0):>5}")
    if not body.get("department_breakdown_expiring_45d"):
        print("  (empty)")
    print(_SEP)
    print()

    # ── Step 6: Alert — expired bucket > 0 ───────────────────────────────────
    if expired_count > 0:
        _log(_ALERT, (
            f"bucket 'expired' == {expired_count} (> 0). "
            "Active employees have payroll-blocking expired contracts. "
            "HR must renew immediately. This is an ALERT, not a script FAIL."
        ))
    else:
        _log(_PASS, "bucket 'expired' == 0 (no payroll-blocking expired contracts)")

    # ── Step 7: Structural invariants (hard FAIL) ─────────────────────────────
    if not _check(
        f"total_active == sum(bucket counts) ({bucket_sum})",
        total_active == bucket_sum,
        f"total_active={total_active}, bucket_sum={bucket_sum}",
    ):
        failures.append("sanity_invariant_violated")

    if not _check("num_buckets == 7", num_buckets == 7, f"got {num_buckets}"):
        failures.append("wrong_bucket_count")

    if not _check(
        "bucket labels in fixed order",
        bucket_labels == EXPECTED_BUCKET_LABELS,
        f"got {bucket_labels}",
    ):
        failures.append("bucket_label_order_wrong")

    if not _check(
        "all bucket counts >= 0",
        all(b["count"] >= 0 for b in buckets),
        "at least one bucket count is negative",
    ):
        failures.append("negative_bucket_count")

    if not _check(
        "orphan_contracts_count >= 0",
        orphan_count >= 0,
        f"got {orphan_count}",
    ):
        failures.append("negative_orphan_count")

    if not _check(
        "cache_status in {fresh, cached}",
        cache_status in {"fresh", "cached"},
        f"got {cache_status!r}",
    ):
        failures.append("bad_cache_status")

    if not _check("rpc_duration_ms >= 0", rpc_ms >= 0, f"got {rpc_ms}"):
        failures.append("negative_rpc_ms")

    if not _check(
        "reference_date == Cairo today",
        reference_date == cairo_today,
        f"got {reference_date!r}, expected {cairo_today!r}",
    ):
        failures.append("reference_date_mismatch")

    # ── Step 8: Drift deltas for total_active + orphan (INFO only) ───────────
    total_delta = total_active - BASELINE_2026_05_29["total_active"]
    if total_delta != 0:
        _log(_INFO, f"total_active drifted {total_delta:+d} vs 2026-05-29 baseline (136)")

    # ── Step 9: Response headers ──────────────────────────────────────────────
    cc  = r.headers.get("cache-control", "")
    xcs = r.headers.get("x-cache-status", "")
    _check("Cache-Control: private",    "private"    in cc,  f"header: {cc!r}")
    _check("Cache-Control: max-age=60", "max-age=60" in cc,  f"header: {cc!r}")
    _check("X-Cache-Status header present", bool(xcs), f"got {xcs!r}")

    # ── Step 10: Second request — cache hit ───────────────────────────────────
    _log(_INFO, "Issuing second request to verify cache hit ...")
    with httpx.Client(timeout=30) as http:
        r2 = http.get(url, auth=(USERNAME, PASSWORD))
    body2: dict = r2.json()
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

    # ── Result ────────────────────────────────────────────────────────────────
    _append_log_row(
        run_at=run_at,
        total_active=total_active,
        bucket_sum=bucket_sum,
        num_buckets=num_buckets,
        expired_count=expired_count,
        orphan_count=orphan_count,
        reference_date=reference_date,
        cache_status=cache_status,
        rpc_ms=rpc_ms,
    )

    if failures:
        _log(_FAIL, f"Verification FAILED — {len(failures)} assertion(s): {failures}")
    else:
        print()
        _log(_PASS, "All structural invariants passed.")
        print()
        print("Next step (manual — identity-equal check against Odoo UI):")
        print("  1. Open Odoo -> Payroll -> Contracts (or HR -> Employees)")
        print(f"     Expected running contracts for active employees: {total_active}")
        print(f"     Expected onboarding-limbo (no contract): {bucket_dict.get('active_without_contract', '?')}")
        print(f"     Expected orphan contracts (on inactive employees): {orphan_count}")
        print("  2. If expired > 0, open each expired contract and renew immediately.")
        print("  3. Review expiring_45d count — these need scheduling within the")
        print("     45-day labor-office response window.")
        print("  Fill in any discrepancies in logs/hr_kpi_c_payroll_risk_verification.log.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
