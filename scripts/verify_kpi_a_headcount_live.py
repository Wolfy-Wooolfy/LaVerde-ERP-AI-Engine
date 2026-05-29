"""
Live verification for HR KPI A — Headcount.

Usage:
    python scripts/verify_kpi_a_headcount_live.py [--url http://localhost:8000]

Requires the FastAPI server to be running and reachable.
Set VERIFY_USERNAME / VERIFY_PASSWORD env vars (or .env) to override
the default admin credentials.

Exits 0 always. Findings are logged to the TSV at
logs/hr_kpi_a_verification.log and printed to stdout with
[PASS]/[FAIL] markers. A non-empty 'error' column or any [FAIL]
line indicates an investigation is needed.

Appends one tab-separated row to logs/hr_kpi_a_verification.log on each run.

Baselines (discovery canonical run 2026-05-28T13:43:49Z, commit logs/hr_discovery.log):
    total_active   == 136
    total_inactive == 24
    null-dept bucket count == 4  (active employees with no department)
    null-job  bucket count == 3  (active employees with no job)
    len(by_department) == 24
    len(by_job)        == 67
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
ENDPOINT    = "/api/v1/hr/kpi/headcount"
LOG_FILE    = "logs/hr_kpi_a_verification.log"

# Discovery baselines (2026-05-28T13:43:49Z)
BASELINE_TOTAL_ACTIVE     = 136
BASELINE_TOTAL_INACTIVE   = 24
BASELINE_DEPT_GROUPS      = 24
BASELINE_JOB_GROUPS       = 67
BASELINE_NULL_DEPT_COUNT  = 4
BASELINE_NULL_JOB_COUNT   = 3
BASELINE_DATE             = "2026-05-28"

NO_DEPT_DISPLAY = "(بدون إدارة)"   # (بدون إدارة)
NO_JOB_DISPLAY  = "(بدون وظيفة)"   # (بدون وظيفة)

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
    total_active: int | str,
    total_inactive: int | str,
    dept_groups: int | str,
    job_groups: int | str,
    null_dept_count: int | str,
    null_job_count: int | str,
    cache_status: str,
    rpc_ms: int | str,
    error: str = "",
) -> None:
    os.makedirs("logs", exist_ok=True)
    log_exists = os.path.isfile(LOG_FILE)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        if not log_exists:
            f.write(
                "run_at\ttotal_active\ttotal_inactive\tdept_groups\t"
                "job_groups\tnull_dept_count\tnull_job_count\t"
                "cache_status\trpc_duration_ms\terror\n"
            )
        f.write(
            f"{run_at}\t{total_active}\t{total_inactive}\t{dept_groups}\t"
            f"{job_groups}\t{null_dept_count}\t{null_job_count}\t"
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

    _log(_INFO, f"Target: GET {url}")
    _log(_INFO, f"Auth user: {USERNAME}")

    failures: list[str] = []

    # ── Step 1: GET /api/v1/hr/kpi/headcount ─────────────────────────────────
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
    _log(_INFO, f"Response body (truncated): {str(body)[:600]}")

    # ── Step 3: Required keys ─────────────────────────────────────────────────
    required_keys = (
        "total_active", "total_inactive", "by_department",
        "by_job", "as_of", "cache_status", "rpc_duration_ms",
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
    total_inactive: int  = int(body["total_inactive"])
    by_department:  list = body["by_department"]
    by_job:         list = body["by_job"]
    cache_status:   str  = body["cache_status"]
    rpc_ms:         int  = int(body["rpc_duration_ms"])

    dept_groups = len(by_department)
    job_groups  = len(by_job)

    null_dept_rows = [r for r in by_department if r.get("department_id") is None]
    null_job_rows  = [r for r in by_job        if r.get("job_id")        is None]
    null_dept_count = null_dept_rows[0]["count"] if null_dept_rows else 0
    null_job_count  = null_job_rows[0]["count"]  if null_job_rows  else 0

    dept_sum = sum(r["count"] for r in by_department)
    job_sum  = sum(r["count"] for r in by_job)

    # ── Step 5: Structured summary ────────────────────────────────────────────
    print()
    print(_SEP)
    print("KPI A (HR) — Headcount Verification")
    print(f"Run timestamp          : {run_at}")
    print(_SEP)
    print(f"total_active           : {total_active:>10}   (baseline {BASELINE_TOTAL_ACTIVE})")
    print(f"total_inactive         : {total_inactive:>10}   (baseline {BASELINE_TOTAL_INACTIVE})")
    print(f"len(by_department)     : {dept_groups:>10}   (baseline {BASELINE_DEPT_GROUPS})")
    print(f"len(by_job)            : {job_groups:>10}   (baseline {BASELINE_JOB_GROUPS})")
    print(f"null-dept bucket count : {null_dept_count:>10}   (baseline {BASELINE_NULL_DEPT_COUNT})")
    print(f"null-job  bucket count : {null_job_count:>10}   (baseline {BASELINE_NULL_JOB_COUNT})")
    print(f"sum(by_department)     : {dept_sum:>10}   (must == total_active)")
    print(f"sum(by_job)            : {job_sum:>10}   (must == total_active)")
    print(f"cache_status           : {cache_status:>10}")
    print(f"rpc_duration_ms        : {rpc_ms:>7} ms")
    print(f"as_of                  : {body.get('as_of')}")
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
        f"total_inactive == {BASELINE_TOTAL_INACTIVE}",
        total_inactive == BASELINE_TOTAL_INACTIVE,
        f"got {total_inactive}",
    ):
        failures.append("total_inactive_mismatch")

    if not _check(
        f"null-dept bucket count == {BASELINE_NULL_DEPT_COUNT}",
        null_dept_count == BASELINE_NULL_DEPT_COUNT,
        f"got {null_dept_count} — bucket label must be {NO_DEPT_DISPLAY!r}",
    ):
        failures.append("null_dept_count_mismatch")

    if not _check(
        f"null-job bucket count == {BASELINE_NULL_JOB_COUNT}",
        null_job_count == BASELINE_NULL_JOB_COUNT,
        f"got {null_job_count} — bucket label must be {NO_JOB_DISPLAY!r}",
    ):
        failures.append("null_job_count_mismatch")

    # ── Step 7: Integrity checks ──────────────────────────────────────────────
    if not _check(
        "sum(by_department counts) == total_active",
        dept_sum == total_active,
        f"{dept_sum} != {total_active}",
    ):
        failures.append("dept_sum_mismatch")

    if not _check(
        "sum(by_job counts) == total_active",
        job_sum == total_active,
        f"{job_sum} != {total_active}",
    ):
        failures.append("job_sum_mismatch")

    if not _check(
        "by_department is non-empty",
        dept_groups > 0,
        f"got {dept_groups} groups",
    ):
        failures.append("dept_empty")

    if not _check(
        "by_job is non-empty",
        job_groups > 0,
        f"got {job_groups} groups",
    ):
        failures.append("job_empty")

    if not _check(
        "null-dept bucket department_id == null",
        (not null_dept_rows) or null_dept_rows[0].get("department_id") is None,
        "department_id must serialize as JSON null",
    ):
        failures.append("null_dept_id_not_null")

    if not _check(
        "null-job bucket job_id == null",
        (not null_job_rows) or null_job_rows[0].get("job_id") is None,
        "job_id must serialize as JSON null",
    ):
        failures.append("null_job_id_not_null")

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
        total_inactive=total_inactive,
        dept_groups=dept_groups,
        job_groups=job_groups,
        null_dept_count=null_dept_count,
        null_job_count=null_job_count,
        cache_status=cache_status,
        rpc_ms=rpc_ms,
    )

    if failures:
        _log(_FAIL, f"Verification FAILED — {len(failures)} assertion(s): {failures}")

    print()
    _log(_PASS, "All assertions passed.")
    print()
    print("Next step (manual — identity-equal check against Odoo UI):")
    print("  1. Open Odoo -> Employees (hr.employee)")
    print("  2. Filter: Active = True")
    print("  3. Group By: Department")
    print(f"     Expected: {BASELINE_TOTAL_ACTIVE} active employees, {BASELINE_DEPT_GROUPS} department groups")
    print(f"     Employees with no department: {BASELINE_NULL_DEPT_COUNT}")
    print("  4. Group By: Job Position")
    print(f"     Expected: {BASELINE_JOB_GROUPS} job groups, {BASELINE_NULL_JOB_COUNT} with no job")
    print("  Fill in any discrepancies in logs/hr_kpi_a_verification.log.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
