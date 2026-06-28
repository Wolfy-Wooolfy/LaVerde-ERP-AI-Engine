"""
Live verification for HR KPI A — Headcount (re-foundation 2026-06-03).

Usage:
    python scripts/verify_kpi_a_headcount_live.py [--url http://localhost:8000]

Requires the FastAPI server to be running and reachable.
Set VERIFY_USERNAME / VERIFY_PASSWORD env vars (or .env) to override
the default admin credentials.

Exits 0 always. Findings printed with [PASS]/[FAIL]/[INFO] markers and
appended as one TSV row to logs/hr_kpi_a_headcount_verification.log.

Employment definition (§3.6): headcount = distinct employees holding a
contract in state='open' (Running). hr.employee.active is NOT an
employment signal.

Baselines (employment-foundation run 2026-06-03T08:22:41Z, post Dev-fix):
    headcount              == 115   (distinct Running-contract employees)
    incoming_count         == 0     (employees with only draft contracts)
    active_flag_count      == 136   (hr.employee.active=True — NOT headcount)
    active_without_running == 34    (exit-gap 23 + data-gap 11)

Drift policy:
  Baseline comparisons are [INFO] only — data may have shifted since
  2026-06-03 as HR acts on exit-gap employees. A headcount of e.g. 113
  is drift to report, not a failure.

Structural invariants ([FAIL] regardless of drift):
  * sum(by_department counts) == headcount
  * sum(by_job counts)        == headcount
  * headcount >= 0
  * active_without_running <= active_flag_count
  * HTTP 200, required keys, valid cache_status, rpc_ms >= 0

Independent Odoo cross-check:
  Queries hr.contract state='open' directly via OdooClient, counts
  distinct employee_ids, compares to endpoint headcount.
  * endpoint == direct AND both == 115   -> clean (no change)
  * endpoint == direct AND both != 115   -> clean drift (data changed)
  * endpoint != direct                   -> [FAIL] SERVICE BUG
"""

import argparse
import asyncio
import io
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# sys.path.insert so script runs without PYTHONPATH set
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import httpx
from dotenv import load_dotenv

from _lib.api_session import ApiLoginError, login as api_login
from backend.shared.odoo.client import OdooClient

load_dotenv(dotenv_path=".env")

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Config ────────────────────────────────────────────────────────────────────

DEFAULT_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")
USERNAME    = os.environ.get("VERIFY_USERNAME", "admin")
PASSWORD    = os.environ.get("VERIFY_PASSWORD", "password")
ENDPOINT    = "/api/v1/hr/kpi/headcount"
LOG_FILE    = "logs/hr_kpi_a_headcount_verification.log"
CAIRO_TZ    = ZoneInfo("Africa/Cairo")

# Baselines (2026-06-03T08:22:41Z — post Dev-fix)
BASELINE_HEADCOUNT              = 115
BASELINE_INCOMING_COUNT         = 0
BASELINE_ACTIVE_FLAG_COUNT      = 136
BASELINE_ACTIVE_WITHOUT_RUNNING = 34

_SEP  = "═" * 72
_SEP2 = "─" * 72

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


def _drift(label: str, value: int, baseline: int) -> None:
    delta = value - baseline
    if delta == 0:
        _log(_INFO, f"{label}: {value}  (= baseline {baseline})")
    else:
        _log(_INFO, f"{label}: {value}  (Delta {delta:+d} vs baseline {baseline})")


def _append_log_row(
    run_at: str,
    headcount: int | str,
    incoming_count: int | str,
    active_flag_count: int | str,
    active_without_running: int | str,
    dept_sum: int | str,
    job_sum: int | str,
    direct_odoo_count: int | str,
    endpoint_matches_odoo: str,
    cache_status: str,
    rpc_ms: int | str,
    error: str = "",
) -> None:
    os.makedirs("logs", exist_ok=True)
    log_exists = os.path.isfile(LOG_FILE)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        if not log_exists:
            f.write(
                "run_at\theadcount\tincoming_count\tactive_flag_count\t"
                "active_without_running\tdept_sum\tjob_sum\tdirect_odoo_count\t"
                "endpoint_matches_odoo\tcache_status\trpc_duration_ms\terror\n"
            )
        f.write(
            f"{run_at}\t{headcount}\t{incoming_count}\t{active_flag_count}\t"
            f"{active_without_running}\t{dept_sum}\t{job_sum}\t{direct_odoo_count}\t"
            f"{endpoint_matches_odoo}\t{cache_status}\t{rpc_ms}\t{error}\n"
        )


# ── Odoo cross-check ──────────────────────────────────────────────────────────

async def _direct_odoo_headcount() -> int:
    """Query hr.contract state='open' directly; return distinct employee_id count."""
    async with OdooClient() as client:
        records = await client.execute_kw(
            "hr.contract",
            "search_read",
            args=[[("state", "=", "open")]],
            kwargs={"fields": ["employee_id"]},
        )
    seen: set[int] = set()
    for c in records:
        emp_raw = c.get("employee_id")
        if isinstance(emp_raw, (list, tuple)) and emp_raw:
            seen.add(int(emp_raw[0]))
        elif emp_raw and emp_raw is not False:
            seen.add(int(emp_raw))
    return len(seen)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_URL, help="Backend base URL")
    args = parser.parse_args()
    base_url: str = args.url.rstrip("/")

    url         = f"{base_url}{ENDPOINT}"
    run_at      = datetime.now(timezone.utc).isoformat()
    cairo_today = datetime.now(CAIRO_TZ).date().isoformat()

    print(_SEP)
    print("KPI A (HR) — Headcount Live Verification")
    print(f"Employment definition : distinct employees with state='open' contract")
    print(f"Run timestamp         : {run_at}")
    print(f"Cairo today           : {cairo_today}")
    print(f"Baselines (2026-06-03): headcount={BASELINE_HEADCOUNT}, "
          f"active_flag={BASELINE_ACTIVE_FLAG_COUNT}, "
          f"active_without_running={BASELINE_ACTIVE_WITHOUT_RUNNING}")
    print(_SEP)
    print()

    _log(_INFO, f"Target: GET {url}")
    _log(_INFO, f"Auth user: {USERNAME}")
    print()

    failures: list[str] = []

    # ── Step 1: ONE login per process (limiter 10/minute), then GET ──────────
    try:
        http = api_login(base_url)
    except ApiLoginError as exc:
        msg = f"Session login failed: {exc}"
        _log(_FAIL, msg)
        _append_log_row(run_at, "", "", "", "", "", "", "", "", "", "", error=msg)
        return 0
    except httpx.ConnectError as exc:
        msg = f"Cannot reach {base_url} — is the server running? ({exc})"
        _log(_FAIL, msg)
        _append_log_row(run_at, "", "", "", "", "", "", "", "", "", "", error=msg)
        return 0

    try:
        r = http.get(ENDPOINT, timeout=60)

        # ── Step 2: Status code ───────────────────────────────────────────────────
        ok = _check("HTTP 200", r.status_code == 200, f"got {r.status_code}")
        if not ok:
            _log(_INFO, f"Response body: {r.text[:500]}")
            _append_log_row(run_at, "", "", "", "", "", "", "", "", "", "",
                            error=f"HTTP {r.status_code}")
            return 0

        body: dict = r.json()

        # ── Step 3: Required keys ─────────────────────────────────────────────────
        required_keys = (
            "headcount", "by_department", "by_job",
            "incoming_count", "active_flag_count", "active_without_running",
            "reference_date", "as_of", "cache_status", "rpc_duration_ms",
        )
        for k in required_keys:
            if not _check(f"key '{k}' present", k in body):
                failures.append(f"missing_key_{k}")

        if failures:
            _append_log_row(run_at, "", "", "", "", "", "", "", "", "", "",
                            error=f"missing keys: {failures}")
            return 0

        # ── Step 4: Extract values ────────────────────────────────────────────────
        headcount:              int  = int(body["headcount"])
        incoming_count:         int  = int(body["incoming_count"])
        active_flag_count:      int  = int(body["active_flag_count"])
        active_without_running: int  = int(body["active_without_running"])
        by_department:          list = body["by_department"]
        by_job:                 list = body["by_job"]
        reference_date:         str  = body["reference_date"]
        cache_status:           str  = body["cache_status"]
        rpc_ms:                 int  = int(body["rpc_duration_ms"])

        dept_sum    = sum(row["count"] for row in by_department)
        job_sum     = sum(row["count"] for row in by_job)
        dept_groups = len(by_department)
        job_groups  = len(by_job)

        # ── Step 5: Structured summary ────────────────────────────────────────────
        print(_SEP)
        print("ENDPOINT RESPONSE SUMMARY")
        print(_SEP2)
        print(f"  headcount              : {headcount:>6}   (baseline {BASELINE_HEADCOUNT})")
        print(f"  incoming_count         : {incoming_count:>6}   (baseline {BASELINE_INCOMING_COUNT})")
        print(f"  active_flag_count      : {active_flag_count:>6}   (baseline {BASELINE_ACTIVE_FLAG_COUNT})")
        print(f"  active_without_running : {active_without_running:>6}   (baseline {BASELINE_ACTIVE_WITHOUT_RUNNING})")
        print(f"  sum(by_department)     : {dept_sum:>6}   (must == headcount {headcount})")
        print(f"  sum(by_job)            : {job_sum:>6}   (must == headcount {headcount})")
        print(f"  len(by_department)     : {dept_groups:>6}")
        print(f"  len(by_job)            : {job_groups:>6}")
        print(f"  reference_date         : {reference_date}   (cairo today: {cairo_today})")
        print(f"  cache_status           : {cache_status}")
        print(f"  rpc_duration_ms        : {rpc_ms} ms")
        print(f"  as_of                  : {body.get('as_of')}")
        print(_SEP)
        print()

        # ── Step 6: Drift reporting (INFO only) ───────────────────────────────────
        print("DRIFT vs 2026-06-03 BASELINES  [INFO only — not structural]:")
        _drift("headcount             ", headcount,              BASELINE_HEADCOUNT)
        _drift("incoming_count        ", incoming_count,         BASELINE_INCOMING_COUNT)
        _drift("active_flag_count     ", active_flag_count,      BASELINE_ACTIVE_FLAG_COUNT)
        _drift("active_without_running", active_without_running, BASELINE_ACTIVE_WITHOUT_RUNNING)
        print()

        # ── Step 7: Structural integrity (hard FAIL) ──────────────────────────────
        print("STRUCTURAL INTEGRITY  [hard checks — must hold regardless of drift]:")

        if not _check("headcount >= 0", headcount >= 0, f"got {headcount}"):
            failures.append("negative_headcount")

        if not _check("incoming_count >= 0", incoming_count >= 0, f"got {incoming_count}"):
            failures.append("negative_incoming_count")

        if not _check("active_flag_count >= 0", active_flag_count >= 0, f"got {active_flag_count}"):
            failures.append("negative_active_flag_count")

        if not _check("active_without_running >= 0",
                      active_without_running >= 0, f"got {active_without_running}"):
            failures.append("negative_active_without_running")

        if not _check(
            "active_without_running <= active_flag_count",
            active_without_running <= active_flag_count,
            f"{active_without_running} > {active_flag_count}",
        ):
            failures.append("active_without_running_exceeds_active_flag")

        if not _check(
            "sum(by_department counts) == headcount",
            dept_sum == headcount,
            f"{dept_sum} != {headcount}",
        ):
            failures.append("dept_sum_mismatch")

        if not _check(
            "sum(by_job counts) == headcount",
            job_sum == headcount,
            f"{job_sum} != {headcount}",
        ):
            failures.append("job_sum_mismatch")

        if not _check(
            "reference_date == Cairo today",
            reference_date == cairo_today,
            f"got {reference_date!r}, expected {cairo_today!r}",
        ):
            failures.append("reference_date_mismatch")

        if not _check(
            "cache_status in {fresh, cached}",
            cache_status in {"fresh", "cached"},
            f"got {cache_status!r}",
        ):
            failures.append("bad_cache_status")

        if not _check("rpc_duration_ms >= 0", rpc_ms >= 0, f"got {rpc_ms}"):
            failures.append("negative_rpc_ms")

        print()

        # ── Step 8: Response headers ──────────────────────────────────────────────
        print("HTTP HEADERS:")
        cc  = r.headers.get("cache-control", "")
        xcs = r.headers.get("x-cache-status", "")
        _check("Cache-Control: private",        "private"    in cc,  f"header: {cc!r}")
        _check("Cache-Control: max-age=60",     "max-age=60" in cc,  f"header: {cc!r}")
        _check("X-Cache-Status header present", bool(xcs),           f"got {xcs!r}")
        print()

        # ── Step 9: Second request — cache hit ───────────────────────────────────
        print("CACHE HIT CHECK:")
        _log(_INFO, "Issuing second request to verify cache hit ...")
        try:
            r2 = http.get(ENDPOINT, timeout=30)
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
        except Exception as exc:
            _log(_FAIL, f"Second request failed: {exc}")
            failures.append("second_request_failed")
        print()
    finally:
        http.close()

    # ── Step 10: Independent Odoo cross-check ─────────────────────────────────
    print("INDEPENDENT ODOO CROSS-CHECK:")
    _log(_INFO, "Querying hr.contract state='open' directly via OdooClient ...")
    direct_count: int | str = "error"
    endpoint_matches_odoo   = "error"
    try:
        direct_count = asyncio.run(_direct_odoo_headcount())
        _log(_INFO, f"Direct Odoo count (distinct Running-contract employees) : {direct_count}")
        _log(_INFO, f"Endpoint headcount                                       : {headcount}")

        if headcount == direct_count:
            _log(_PASS, f"endpoint headcount == direct Odoo count ({headcount})")
            if headcount == BASELINE_HEADCOUNT:
                _log(_INFO, f"Both match baseline {BASELINE_HEADCOUNT} — no drift.")
            else:
                _log(_INFO,
                     f"Both differ from baseline {BASELINE_HEADCOUNT} "
                     f"(Delta {headcount - BASELINE_HEADCOUNT:+d}) — clean drift (data changed, not a bug).")
            endpoint_matches_odoo = "MATCH"
        else:
            _log(_FAIL,
                 f"endpoint headcount ({headcount}) != direct Odoo count ({direct_count}) "
                 "— SERVICE BUG: endpoint logic diverges from a fresh Odoo query")
            failures.append(f"endpoint_odoo_mismatch:{headcount}/{direct_count}")
            endpoint_matches_odoo = f"MISMATCH:{headcount}/{direct_count}"

    except Exception as exc:
        _log(_FAIL, f"Direct Odoo query failed — cross-check skipped: {exc}")
        failures.append("direct_odoo_query_failed")
        endpoint_matches_odoo = f"error:{type(exc).__name__}"
    print()

    # ── Result ────────────────────────────────────────────────────────────────
    _append_log_row(
        run_at=run_at,
        headcount=headcount,
        incoming_count=incoming_count,
        active_flag_count=active_flag_count,
        active_without_running=active_without_running,
        dept_sum=dept_sum,
        job_sum=job_sum,
        direct_odoo_count=direct_count,
        endpoint_matches_odoo=endpoint_matches_odoo,
        cache_status=cache_status,
        rpc_ms=rpc_ms,
    )

    print(_SEP)
    if failures:
        _log(_FAIL,
             f"Verification complete — {len(failures)} structural issue(s): {failures}")
    else:
        _log(_PASS,
             "All structural checks passed. "
             "Review [INFO] drift lines above if headcount != 115.")
    print(_SEP)

    return 0


if __name__ == "__main__":
    sys.exit(main())
