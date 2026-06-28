"""
Live verification for HR KPI C — Payroll Risk Dashboard (re-foundation 2026-06-04).

Employment definition (§3.6): an employee is employed at La Verde IFF they hold
a contract in state='open' (Running). hr.employee.active is a UI/archive flag,
NOT an employment signal. True headcount = distinct Running-contract employees = 115.

Usage:
    python scripts/verify_kpi_c_payroll_risk_live.py [--url http://localhost:8000]

Requires the FastAPI server to be running and reachable.
Set VERIFY_USERNAME / VERIFY_PASSWORD env vars (or .env) to override
the default admin credentials.

Exits 0 always. Findings printed with [PASS]/[FAIL]/[INFO]/[ALERT] markers
and appended as one TSV row to logs/hr_kpi_c_payroll_risk_verification.log.

Structural invariants ([FAIL] regardless of drift):
    * sum(6 bucket counts) == total_employed
    * 6 buckets present in fixed order
    * all bucket counts >= 0, total_employed >= 0
    * archived_with_running_count >= 0
    * active_flag_no_running_count >= 0
    * active_flag_no_running_exit_gap + active_flag_no_running_incoming
      + active_flag_no_running_data_gap == active_flag_no_running_count
    * reference_date == Cairo today
    * cache_status in {fresh, cached}, rpc_duration_ms >= 0
    * HTTP 200, required keys present

[ALERT] (not FAIL) conditions:
    * bucket 'expired' > 0: employed employees have payroll-blocking contracts

Independent Odoo cross-check ([FAIL] if mismatch):
    Re-implements corrected bucketing from scratch via OdooClient — fetches all
    Running contracts regardless of archive flag (active_test inert on hr.contract,
    Item 0, 2026-06-04), replicates distinct-employee bucketing logic, compares
    bucket counts directly against endpoint response.

Cross-KPI consistency ([INFO] only):
    total_employed should equal KPI A headcount (both count distinct
    Running-contract employees). Mismatch signals cache skew or divergence.

Baselines — set to None until first live run establishes values.
Old baselines (pre-re-foundation, based on hr.employee.active=True population,
total_active=136, 7 buckets including active_without_contract) are INCOMPATIBLE
with this script.
"""

import argparse
import asyncio
import io
import os
import sys
from datetime import date, datetime, timezone
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

DEFAULT_URL    = os.environ.get("BACKEND_URL", "http://localhost:8000")
USERNAME       = os.environ.get("VERIFY_USERNAME", "admin")
PASSWORD       = os.environ.get("VERIFY_PASSWORD", "password")
ENDPOINT       = "/api/v1/hr/kpi/payroll-risk-dashboard"
KPI_A_ENDPOINT = "/api/v1/hr/kpi/headcount"
LOG_FILE       = "logs/hr_kpi_c_payroll_risk_verification.log"
CAIRO_TZ       = ZoneInfo("Africa/Cairo")

# Fixed bucket label order — any deviation is a structural FAIL
EXPECTED_BUCKET_LABELS = [
    "expired",
    "expiring_45d",
    "expiring_90d",
    "expiring_135d",
    "beyond_135d",
    "open_ended",
]

# Baselines — established 2026-06-04T08:34:18Z (first re-foundation run).
BASELINE_TOTAL_EMPLOYED: int | None = 115

_SEP  = "═" * 72
_SEP2 = "─" * 72

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


def _drift(label: str, value: int, baseline: int | None) -> None:
    if baseline is None:
        _log(_INFO, f"{label}: {value}  (no baseline yet — first run establishes this)")
        return
    delta = value - baseline
    if delta == 0:
        _log(_INFO, f"{label}: {value}  (= baseline {baseline})")
    else:
        _log(_INFO, f"{label}: {value}  (Delta {delta:+d} vs baseline {baseline})")


def _append_log_row(
    run_at: str,
    total_employed: int | str,
    bucket_sum: int | str,
    num_buckets: int | str,
    expired_count: int | str,
    archived_with_running_count: int | str,
    active_flag_no_running_count: int | str,
    direct_total_employed: int | str,
    buckets_match: str,
    kpi_a_headcount: int | str,
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
                "run_at\ttotal_employed\tbucket_sum\tnum_buckets\t"
                "expired_count\tarchived_with_running_count\tactive_flag_no_running_count\t"
                "direct_total_employed\tbuckets_match\t"
                "kpi_a_headcount\treference_date\tcache_status\trpc_duration_ms\terror\n"
            )
        f.write(
            f"{run_at}\t{total_employed}\t{bucket_sum}\t{num_buckets}\t"
            f"{expired_count}\t{archived_with_running_count}\t{active_flag_no_running_count}\t"
            f"{direct_total_employed}\t{buckets_match}\t"
            f"{kpi_a_headcount}\t{reference_date}\t{cache_status}\t{rpc_ms}\t{error}\n"
        )


# ── Independent bucketing algorithm (NOT imported from service) ───────────────

def _assign_bucket_ind(date_end_raw: str | bool | None, cairo_today: date) -> str:
    """Replicate KPI C bucketing logic from scratch."""
    if not date_end_raw:
        return "open_ended"
    delta = (date.fromisoformat(str(date_end_raw)) - cairo_today).days
    if delta < 0:
        return "expired"
    if delta <= 45:
        return "expiring_45d"
    if delta <= 90:
        return "expiring_90d"
    if delta <= 135:
        return "expiring_135d"
    return "beyond_135d"


async def _direct_odoo_buckets(cairo_today: date) -> dict:
    """
    Re-implement corrected KPI C bucketing from scratch via OdooClient.

    active_test is inert on hr.contract (Item 0, 2026-06-04): Running contracts
    on archived employees are returned without any flag. No context flag added.
    Distinct-employee guard ensures each employee counted exactly once.

    Returns:
        {
            "bucket_counts": {"expired": n, "expiring_45d": n, ...},
            "total_employed": n,
        }
    """
    async with OdooClient() as client:
        contract_records: list[dict] = await client.execute_kw(
            "hr.contract",
            "search_read",
            args=[[("state", "=", "open")]],
            kwargs={"fields": ["id", "employee_id", "date_end"]},
        )

    bucket_counts: dict[str, int] = {label: 0 for label in EXPECTED_BUCKET_LABELS}
    seen_emp_ids: set[int] = set()

    for c in contract_records:
        emp_raw = c.get("employee_id")
        if isinstance(emp_raw, (list, tuple)) and emp_raw:
            emp_id = int(emp_raw[0])
        elif emp_raw and emp_raw is not False:
            emp_id = int(emp_raw)
        else:
            continue
        if not emp_id:
            continue
        if emp_id in seen_emp_ids:
            continue
        seen_emp_ids.add(emp_id)
        bucket = _assign_bucket_ind(c.get("date_end"), cairo_today)
        bucket_counts[bucket] += 1

    return {
        "bucket_counts": bucket_counts,
        "total_employed": sum(bucket_counts.values()),
    }


# ── KPI A cross-check ─────────────────────────────────────────────────────────

def _fetch_kpi_a_headcount(http: httpx.Client) -> int | None:
    try:
        r = http.get(KPI_A_ENDPOINT, timeout=30)
        if r.status_code == 200:
            return int(r.json().get("headcount", -1))
    except Exception:
        pass
    return None


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_URL, help="Backend base URL")
    args = parser.parse_args()
    base_url: str = args.url.rstrip("/")

    url             = f"{base_url}{ENDPOINT}"
    run_at          = datetime.now(timezone.utc).isoformat()
    cairo_today     = datetime.now(CAIRO_TZ).date()
    cairo_today_str = cairo_today.isoformat()

    print(_SEP)
    print("KPI C (HR) — Payroll Risk Dashboard Live Verification (re-foundation 2026-06-04)")
    print(f"Employment def  : distinct employees with state='open' (Running) contract")
    print(f"archived-running: employed (archive flag stale) — bucketed normally, counted in metadata")
    print(f"active-no-running: NOT employed — metadata only (3-way: exit_gap/incoming/data_gap)")
    print(f"Run timestamp   : {run_at}")
    print(f"Cairo today     : {cairo_today_str}")
    baseline_note = (
        "not set — first run establishes them"
        if BASELINE_TOTAL_EMPLOYED is None
        else f"total_employed={BASELINE_TOTAL_EMPLOYED}"
    )
    print(f"Baselines       : {baseline_note}")
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
        _append_log_row(run_at, "", "", "", "", "", "", "", "", "", "", "", "", error=msg)
        return 0
    except httpx.ConnectError as exc:
        msg = f"Cannot reach {base_url} — is the server running? ({exc})"
        _log(_FAIL, msg)
        _append_log_row(run_at, "", "", "", "", "", "", "", "", "", "", "", "", error=msg)
        return 0

    try:
        r = http.get(ENDPOINT, timeout=60)

        # ── Step 2: Status code ───────────────────────────────────────────────────
        ok = _check("HTTP 200", r.status_code == 200, f"got {r.status_code}")
        if not ok:
            _log(_INFO, f"Response body: {r.text[:500]}")
            _append_log_row(run_at, "", "", "", "", "", "", "", "", "", "", "", "",
                            error=f"HTTP {r.status_code}")
            return 0

        body: dict = r.json()

        # ── Step 3: Required keys ─────────────────────────────────────────────────
        required_keys = (
            "buckets",
            "department_breakdown_expired",
            "department_breakdown_expiring_45d",
            "archived_with_running_count",
            "active_flag_no_running_count",
            "active_flag_no_running_exit_gap",
            "active_flag_no_running_incoming",
            "active_flag_no_running_data_gap",
            "total_employed",
            "reference_date",
            "as_of",
            "cache_status",
            "rpc_duration_ms",
        )
        for k in required_keys:
            if not _check(f"key '{k}' present", k in body):
                failures.append(f"missing_key_{k}")

        if failures:
            _append_log_row(run_at, "", "", "", "", "", "", "", "", "", "", "", "",
                            error=f"missing keys: {failures}")
            return 0

        # ── Step 4: Extract values ────────────────────────────────────────────────
        total_employed:                int  = int(body["total_employed"])
        archived_with_running_count:   int  = int(body["archived_with_running_count"])
        active_flag_no_running_count:  int  = int(body["active_flag_no_running_count"])
        awc_exit_gap:                  int  = int(body["active_flag_no_running_exit_gap"])
        awc_incoming:                  int  = int(body["active_flag_no_running_incoming"])
        awc_data_gap:                  int  = int(body["active_flag_no_running_data_gap"])
        buckets:                       list = body["buckets"]
        reference_date:                str  = body["reference_date"]
        cache_status:                  str  = body["cache_status"]
        rpc_ms:                        int  = int(body["rpc_duration_ms"])

        num_buckets   = len(buckets)
        bucket_sum    = sum(b["count"] for b in buckets)
        bucket_labels = [b["label"] for b in buckets]
        bucket_dict   = {b["label"]: b["count"] for b in buckets}
        expired_count = bucket_dict.get("expired", -1)

        # ── Step 5: Structured summary ────────────────────────────────────────────
        print(_SEP)
        print("ENDPOINT RESPONSE SUMMARY")
        print(_SEP2)
        bl_note = "(no baseline)" if BASELINE_TOTAL_EMPLOYED is None else f"(baseline {BASELINE_TOTAL_EMPLOYED})"
        print(f"  total_employed                   : {total_employed:>6}   {bl_note}")
        print(f"  sum(6 bucket counts)             : {bucket_sum:>6}   (must == total_employed)")
        print(f"  num_buckets                      : {num_buckets:>6}   (must == 6)")
        print(f"  archived_with_running_count      : {archived_with_running_count:>6}   (employed, stale archive flag)")
        print(f"  active_flag_no_running_count     : {active_flag_no_running_count:>6}   (NOT employed — metadata only)")
        print(f"    ↳ exit_gap                     : {awc_exit_gap:>6}   (departed, unarchived)")
        print(f"    ↳ incoming                     : {awc_incoming:>6}   (draft contract, new hire)")
        print(f"    ↳ data_gap                     : {awc_data_gap:>6}   (no contract record at all)")
        print(f"  awc_exit_gap+incoming+data_gap   : {awc_exit_gap+awc_incoming+awc_data_gap:>6}   (must == active_flag_no_running_count {active_flag_no_running_count})")
        print(f"  reference_date                   : {reference_date}   (cairo today: {cairo_today_str})")
        print(f"  cache_status                     : {cache_status}")
        print(f"  rpc_duration_ms                  : {rpc_ms} ms")
        print(f"  as_of                            : {body.get('as_of')}")
        print(_SEP2)
        print("  Bucket breakdown (endpoint):")
        for b in buckets:
            print(f"    {b['label']:<20}  :  {b['count']:>5}")
        print(_SEP2)
        print("  Department breakdown (expired):")
        for row in body.get("department_breakdown_expired", []):
            print(f"    {row.get('department_name', '?'):<30} : {row.get('count', 0):>5}")
        if not body.get("department_breakdown_expired"):
            print("    (empty — expected when expired bucket == 0)")
        print("  Department breakdown (expiring_45d):")
        for row in body.get("department_breakdown_expiring_45d", []):
            print(f"    {row.get('department_name', '?'):<30} : {row.get('count', 0):>5}")
        if not body.get("department_breakdown_expiring_45d"):
            print("    (empty)")
        print(_SEP)
        print()

        # ── Step 6: Drift reporting (INFO only) ───────────────────────────────────
        print("DRIFT SECTION  [INFO only — no hard fail; set BASELINE_TOTAL_EMPLOYED after first run]:")
        _drift("total_employed", total_employed, BASELINE_TOTAL_EMPLOYED)
        print()

        # ── Step 7: expired == 0 expected (ALERT if > 0) ──────────────────────────
        if expired_count > 0:
            _log(_ALERT, (
                f"bucket 'expired' == {expired_count} (> 0). "
                "Employed employees have payroll-blocking expired contracts. "
                "HR must renew immediately. This is an ALERT, not a script FAIL."
            ))
        else:
            _log(_INFO, f"bucket 'expired' == 0  (expected — no payroll-blocking expired contracts)")

        # ── Step 8: Structural invariants (hard FAIL) ─────────────────────────────
        print()
        print("STRUCTURAL INTEGRITY  [hard checks — must hold regardless of drift]:")

        if not _check("total_employed >= 0", total_employed >= 0, f"got {total_employed}"):
            failures.append("negative_total_employed")

        if not _check("all bucket counts >= 0",
                      all(b["count"] >= 0 for b in buckets),
                      "at least one bucket count is negative"):
            failures.append("negative_bucket_count")

        if not _check("num_buckets == 6", num_buckets == 6, f"got {num_buckets}"):
            failures.append("wrong_bucket_count")

        if not _check(
            "bucket labels in fixed order",
            bucket_labels == EXPECTED_BUCKET_LABELS,
            f"got {bucket_labels}",
        ):
            failures.append("bucket_label_order_wrong")

        if not _check(
            f"sum(buckets) == total_employed ({total_employed})",
            bucket_sum == total_employed,
            f"bucket_sum={bucket_sum} != total_employed={total_employed}",
        ):
            failures.append("sanity_invariant_violated")

        if not _check("archived_with_running_count >= 0",
                      archived_with_running_count >= 0,
                      f"got {archived_with_running_count}"):
            failures.append("negative_archived_with_running")

        if not _check("active_flag_no_running_count >= 0",
                      active_flag_no_running_count >= 0,
                      f"got {active_flag_no_running_count}"):
            failures.append("negative_active_flag_no_running")

        awc_three_way_sum = awc_exit_gap + awc_incoming + awc_data_gap
        if not _check(
            f"awc_exit_gap + awc_incoming + awc_data_gap == active_flag_no_running_count ({active_flag_no_running_count})",
            awc_three_way_sum == active_flag_no_running_count,
            f"{awc_exit_gap}+{awc_incoming}+{awc_data_gap}={awc_three_way_sum} != {active_flag_no_running_count}",
        ):
            failures.append("awc_three_way_split_mismatch")

        if not _check(
            "reference_date == Cairo today",
            reference_date == cairo_today_str,
            f"got {reference_date!r}, expected {cairo_today_str!r}",
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

        # ── Step 9: Response headers ──────────────────────────────────────────────
        print("HTTP HEADERS:")
        cc  = r.headers.get("cache-control", "")
        xcs = r.headers.get("x-cache-status", "")
        _check("Cache-Control: private",        "private"    in cc, f"header: {cc!r}")
        _check("Cache-Control: max-age=60",     "max-age=60" in cc, f"header: {cc!r}")
        _check("X-Cache-Status header present", bool(xcs),          f"got {xcs!r}")
        print()

        # ── Step 10: Second request — cache hit ───────────────────────────────────
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

        # ── Step 11: Cross-KPI consistency (INFO only) ────────────────────────────
        print("CROSS-KPI CONSISTENCY  [INFO only — both KPIs query same Running population]:")
        kpi_a_headcount: int | str = "n/a"
        kpi_a_count = _fetch_kpi_a_headcount(http)
        if kpi_a_count is None:
            _log(_INFO, "KPI A endpoint unreachable — cross-KPI check skipped")
            kpi_a_headcount = "error"
        else:
            kpi_a_headcount = kpi_a_count
            if total_employed == kpi_a_count:
                _log(_INFO,
                     f"total_employed ({total_employed}) == KPI A headcount ({kpi_a_count}) — consistent")
            else:
                _log(_INFO,
                     f"total_employed ({total_employed}) != KPI A headcount ({kpi_a_count}) "
                     f"— investigate: possible cache skew or population divergence")
        print()
    finally:
        http.close()

    # ── Step 12: Independent Odoo cross-check (hard FAIL if mismatch) ─────────
    print("INDEPENDENT ODOO CROSS-CHECK  [FAIL if endpoint != direct Odoo computation]:")
    _log(_INFO,
         "Re-implementing KPI C bucketing from scratch via OdooClient "
         "(active_test inert on hr.contract, Item 0, 2026-06-04) ...")

    direct_total:   int | str = "error"
    buckets_match              = "error"

    try:
        direct_result = asyncio.run(_direct_odoo_buckets(cairo_today))
        direct_bucket_counts:  dict[str, int] = direct_result["bucket_counts"]
        direct_total_employed: int            = direct_result["total_employed"]

        direct_total = direct_total_employed

        _log(_INFO, f"Direct computation — total_employed: {direct_total_employed}")
        print("  Direct bucket breakdown:")
        for label in EXPECTED_BUCKET_LABELS:
            print(f"    {label:<20}  :  {direct_bucket_counts.get(label, 0):>5}")
        print()

        endpoint_bucket_counts = {b["label"]: b["count"] for b in buckets}
        bucket_mismatches: list[str] = []
        for label in EXPECTED_BUCKET_LABELS:
            ep_val = endpoint_bucket_counts.get(label, 0)
            dr_val = direct_bucket_counts.get(label, 0)
            if ep_val != dr_val:
                bucket_mismatches.append(f"{label}: endpoint={ep_val} direct={dr_val}")

        if total_employed == direct_total_employed and not bucket_mismatches:
            _log(_PASS,
                 f"endpoint == direct Odoo computation "
                 f"(total_employed={total_employed}, all 6 buckets match)")
            buckets_match = "MATCH"
        else:
            detail_parts = []
            if total_employed != direct_total_employed:
                detail_parts.append(
                    f"total_employed: endpoint={total_employed} direct={direct_total_employed}"
                )
            if bucket_mismatches:
                detail_parts.append(f"bucket mismatches: {bucket_mismatches}")
            detail = "; ".join(detail_parts)
            _log(_FAIL,
                 f"endpoint != direct Odoo computation — SERVICE BUG: {detail}")
            failures.append(f"endpoint_odoo_mismatch:{detail}")
            buckets_match = f"MISMATCH:{detail}"

    except Exception as exc:
        _log(_FAIL, f"Direct Odoo computation failed — cross-check skipped: {exc}")
        failures.append(f"direct_odoo_failed:{type(exc).__name__}")
        buckets_match = f"error:{type(exc).__name__}"
    print()

    # ── Result ────────────────────────────────────────────────────────────────
    _append_log_row(
        run_at=run_at,
        total_employed=total_employed,
        bucket_sum=bucket_sum,
        num_buckets=num_buckets,
        expired_count=expired_count,
        archived_with_running_count=archived_with_running_count,
        active_flag_no_running_count=active_flag_no_running_count,
        direct_total_employed=direct_total,
        buckets_match=buckets_match,
        kpi_a_headcount=kpi_a_headcount,
        reference_date=reference_date,
        cache_status=cache_status,
        rpc_ms=rpc_ms,
    )

    print(_SEP)
    if failures:
        _log(_FAIL,
             f"Verification complete — {len(failures)} structural issue(s): {failures}")
        _log(_INFO, "Review [FAIL] lines above. Exit 0 (script always exits 0).")
    else:
        _log(_PASS,
             "All structural checks passed. Review [INFO] lines above for context.")
        _log(_INFO,
             "Next step: set BASELINE_TOTAL_EMPLOYED in this script with "
             "total_employed from this run, then commit.")
    print(_SEP)

    return 0


if __name__ == "__main__":
    sys.exit(main())
