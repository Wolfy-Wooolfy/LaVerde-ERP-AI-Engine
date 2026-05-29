"""
Live verification for HR KPI B — Tenure Distribution.

Usage:
    python scripts/verify_kpi_b_tenure_live.py [--url http://localhost:8000]

Requires the FastAPI server to be running and reachable.
Set VERIFY_USERNAME / VERIFY_PASSWORD env vars (or .env) to override
the default admin credentials.

Exits 0 always. Findings are logged to the TSV at
logs/hr_kpi_b_tenure_verification.log and printed to stdout with
[PASS]/[FAIL] markers. A non-empty 'error' column or any [FAIL]
line indicates an investigation is needed.

Appends one tab-separated row to logs/hr_kpi_b_tenure_verification.log
on each run.

Baselines (from KPI A live verification run 2026-05-29 + discovery
canonical run 2026-05-28T13:43:49Z):
    total_active == 136          (matches KPI A — same active employees)
    sum(bands) + missing_date_count == 136  (sanity invariant)
    5 bands present in fixed order
    reference_date == today's Cairo date

Band-count baselines NOT hardcoded: discovery only recorded the
first_contract_date range (2017-12-26 → 2025-11-17), not a per-band
breakdown. An unexpected distribution (e.g. all 136 in one band) is a
FINDING — stop and report, do not adjust band thresholds.

Note on "10+y" band: the earliest first_contract_date in discovery is
2017-12-26. As of 2026-05-29 that is ~8.4y — still in "5-10y". The
"10+y" band may have count=0 on today's run. This is expected and NOT
a bug. Document if confirmed.
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
ENDPOINT    = "/api/v1/hr/kpi/tenure-distribution"
LOG_FILE    = "logs/hr_kpi_b_tenure_verification.log"

# Baselines
BASELINE_TOTAL_ACTIVE  = 136
EXPECTED_BAND_LABELS   = ["<1y", "1-3y", "3-5y", "5-10y", "10+y"]
CAIRO_TZ               = ZoneInfo("Africa/Cairo")

_SEP = "═" * 63

# ── Helpers ───────────────────────────────────────────────────────────────────

_PASS = "[PASS]"
_FAIL = "[FAIL]"
_INFO = "[INFO]"


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
    missing_date_count: int | str,
    band_sum: int | str,
    num_bands: int | str,
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
                "run_at\ttotal_active\tmissing_date_count\tband_sum\t"
                "num_bands\treference_date\tcache_status\trpc_duration_ms\terror\n"
            )
        f.write(
            f"{run_at}\t{total_active}\t{missing_date_count}\t{band_sum}\t"
            f"{num_bands}\t{reference_date}\t{cache_status}\t{rpc_ms}\t{error}\n"
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

    # ── Step 1: GET /api/v1/hr/kpi/tenure-distribution ───────────────────────
    try:
        with httpx.Client(timeout=60) as http:
            r = http.get(url, auth=(USERNAME, PASSWORD))
    except httpx.ConnectError as exc:
        msg = f"Cannot reach {base_url} — is the server running? ({exc})"
        _log(_FAIL, msg)
        _append_log_row(run_at, "", "", "", "", "", "", "", error=msg)
        return 0

    # ── Step 2: Status code ───────────────────────────────────────────────────
    ok = _check("HTTP 200", r.status_code == 200, f"got {r.status_code}")
    if not ok:
        _log(_INFO, f"Response body: {r.text[:500]}")
        _append_log_row(run_at, "", "", "", "", "", "", "",
                        error=f"HTTP {r.status_code}")
        return 0

    body: dict = r.json()
    _log(_INFO, f"Response body (truncated): {str(body)[:600]}")

    # ── Step 3: Required keys ─────────────────────────────────────────────────
    required_keys = (
        "bands", "missing_date_count", "total_active",
        "reference_date", "as_of", "cache_status", "rpc_duration_ms",
    )
    for k in required_keys:
        if not _check(f"key '{k}' present", k in body):
            failures.append(f"missing_key_{k}")

    if failures:
        _append_log_row(run_at, "", "", "", "", "", "", "",
                        error=f"missing keys: {failures}")
        return 0

    # ── Step 4: Extract values ────────────────────────────────────────────────
    total_active:       int  = int(body["total_active"])
    missing_date_count: int  = int(body["missing_date_count"])
    bands:              list = body["bands"]
    reference_date:     str  = body["reference_date"]
    cache_status:       str  = body["cache_status"]
    rpc_ms:             int  = int(body["rpc_duration_ms"])

    num_bands = len(bands)
    band_sum  = sum(b["count"] for b in bands)
    band_labels = [b["band"] for b in bands]

    # ── Step 5: Structured summary ────────────────────────────────────────────
    print()
    print(_SEP)
    print("KPI B (HR) — Tenure Distribution Verification")
    print(f"Run timestamp          : {run_at}")
    print(_SEP)
    print(f"total_active           : {total_active:>10}   (baseline {BASELINE_TOTAL_ACTIVE})")
    print(f"missing_date_count     : {missing_date_count:>10}")
    print(f"num_bands              : {num_bands:>10}   (expected 5)")
    print(f"sum(band counts)       : {band_sum:>10}   (must == total_active - missing)")
    print(f"band_sum + missing     : {band_sum + missing_date_count:>10}   (must == total_active)")
    print(f"reference_date         : {reference_date:>10}   (expected {cairo_today})")
    print(f"cache_status           : {cache_status:>10}")
    print(f"rpc_duration_ms        : {rpc_ms:>7} ms")
    print(f"as_of                  : {body.get('as_of')}")
    print(_SEP)
    print("Band breakdown:")
    for b in bands:
        print(f"  {b['band']:>6}  :  {b['count']:>4}")
    print(_SEP)
    print()

    # ── Step 6: Baseline assertions ───────────────────────────────────────────
    if not _check(
        f"total_active == {BASELINE_TOTAL_ACTIVE}",
        total_active == BASELINE_TOTAL_ACTIVE,
        f"got {total_active}",
    ):
        failures.append("total_active_mismatch")

    if not _check(
        "missing_date_count >= 0",
        missing_date_count >= 0,
        f"got {missing_date_count}",
    ):
        failures.append("negative_missing_date_count")

    if not _check(
        "reference_date == Cairo today",
        reference_date == cairo_today,
        f"got {reference_date!r}, expected {cairo_today!r}",
    ):
        failures.append("reference_date_mismatch")

    # ── Step 7: Integrity checks ──────────────────────────────────────────────
    if not _check(
        "num_bands == 5",
        num_bands == 5,
        f"got {num_bands}",
    ):
        failures.append("wrong_band_count")

    if not _check(
        "band labels in fixed order",
        band_labels == EXPECTED_BAND_LABELS,
        f"got {band_labels}",
    ):
        failures.append("band_label_order_wrong")

    if not _check(
        "band_sum + missing_date_count == total_active",
        band_sum + missing_date_count == total_active,
        f"{band_sum} + {missing_date_count} = {band_sum + missing_date_count} != {total_active}",
    ):
        failures.append("sanity_invariant_violated")

    if not _check(
        "all band counts >= 0",
        all(b["count"] >= 0 for b in bands),
        "at least one band count is negative",
    ):
        failures.append("negative_band_count")

    if not _check(
        "cache_status in {fresh, cached}",
        cache_status in {"fresh", "cached"},
        f"got {cache_status!r}",
    ):
        failures.append("bad_cache_status")

    if not _check(
        "rpc_duration_ms >= 0",
        rpc_ms >= 0,
        f"got {rpc_ms}",
    ):
        failures.append("negative_rpc_ms")

    # ── Step 8: Response headers ──────────────────────────────────────────────
    cc  = r.headers.get("cache-control", "")
    xcs = r.headers.get("x-cache-status", "")
    _check("Cache-Control: private",    "private"    in cc,  f"header: {cc!r}")
    _check("Cache-Control: max-age=60", "max-age=60" in cc,  f"header: {cc!r}")
    _check("X-Cache-Status header present", bool(xcs), f"got {xcs!r}")

    # ── Step 9: Second request — cache hit ───────────────────────────────────
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
        missing_date_count=missing_date_count,
        band_sum=band_sum,
        num_bands=num_bands,
        reference_date=reference_date,
        cache_status=cache_status,
        rpc_ms=rpc_ms,
    )

    if failures:
        _log(_FAIL, f"Verification FAILED — {len(failures)} assertion(s): {failures}")
    else:
        print()
        _log(_PASS, "All assertions passed.")
        print()
        print("Next step (manual — identity-equal check against Odoo UI):")
        print("  1. Open Odoo -> Employees (hr.employee)")
        print("  2. Filter: Active = True")
        print(f"     Expected: {BASELINE_TOTAL_ACTIVE} active employees total")
        print("  3. Note any employees shown without a Contract Start Date")
        print(f"     These should equal missing_date_count ({missing_date_count}) above")
        print("  4. Band distribution above is the live finding — no per-band")
        print("     baseline was established in discovery (range only: 2017-12-26")
        print("     to 2025-11-17). If a band looks skewed, report as a finding.")
        print("  Fill in any discrepancies in logs/hr_kpi_b_tenure_verification.log.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
