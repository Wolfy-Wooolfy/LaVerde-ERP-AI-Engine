"""
Live verification for HR KPI B — Tenure Distribution (re-foundation 2026-06-03).

Employment definition (§3.6): an employee is employed at La Verde IFF they hold
a contract in state='open' (Running). hr.employee.active is a UI/archive flag,
NOT an employment signal. True headcount = distinct Running-contract employees.

Net-accumulated tenure (§3.7): sum of worked periods (date_start → date_end-or-today)
across ALL hr.contract records per employee, with overlaps clamped and gaps
naturally excluded.

Usage:
    python scripts/verify_kpi_b_tenure_live.py [--url http://localhost:8000]

Requires the FastAPI server to be running and reachable.
Set VERIFY_USERNAME / VERIFY_PASSWORD env vars (or .env) to override
the default admin credentials.

Exits 0 always. Findings printed with [PASS]/[FAIL]/[INFO] markers and
appended as one TSV row to logs/hr_kpi_b_tenure_refounded_verification.log.

Baselines — set to None until first live run establishes values.
Old verify_kpi_b_tenure_verification.log baselines (pre-re-foundation,
based on hr.employee.active=True population, total 136) are INCOMPATIBLE
with this script.

Structural invariants ([FAIL] regardless of drift):
    * sum(band counts) + missing_date_count == total_employed
    * 5 bands present in fixed order: <1y, 1-3y, 3-5y, 5-10y, 10+y
    * all band counts >= 0, missing_date_count >= 0, total_employed >= 0
    * reference_date == Cairo today
    * cache_status in {fresh, cached}, rpc_duration_ms >= 0
    * HTTP 200, required keys

Independent Odoo cross-check ([FAIL] if mismatch):
    Re-implements net-accumulated tenure algorithm from scratch (NOT imported
    from service). Fetches all hr.contract with active_test=False, replicates
    the Running-contract population and interval-merge logic, compares band
    counts directly against endpoint response.

Cross-KPI consistency ([INFO] only):
    total_employed should equal KPI A headcount (both = distinct Running-contract
    employees). Mismatch signals a population or caching discrepancy.
"""

import argparse
import asyncio
import io
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
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
ENDPOINT       = "/api/v1/hr/kpi/tenure-distribution"
KPI_A_ENDPOINT = "/api/v1/hr/kpi/headcount"
LOG_FILE       = "logs/hr_kpi_b_tenure_refounded_verification.log"
CAIRO_TZ       = ZoneInfo("Africa/Cairo")

# Baselines — established 2026-06-03T14:05:33Z (first re-foundation run).
BASELINE_TOTAL_EMPLOYED: int | None = 115
BASELINE_MISSING:        int | None = 0

EXPECTED_BAND_LABELS = ["<1y", "1-3y", "3-5y", "5-10y", "10+y"]
_RUNNING_STATE       = "open"

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
    missing_date_count: int | str,
    band_sum: int | str,
    direct_total_employed: int | str,
    direct_missing: int | str,
    bands_match: str,
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
                "run_at\ttotal_employed\tmissing_date_count\tband_sum\t"
                "direct_total_employed\tdirect_missing\tbands_match\t"
                "kpi_a_headcount\treference_date\tcache_status\trpc_duration_ms\terror\n"
            )
        f.write(
            f"{run_at}\t{total_employed}\t{missing_date_count}\t{band_sum}\t"
            f"{direct_total_employed}\t{direct_missing}\t{bands_match}\t"
            f"{kpi_a_headcount}\t{reference_date}\t{cache_status}\t{rpc_ms}\t{error}\n"
        )


# ── Independent tenure algorithm (NOT imported from service) ──────────────────

def _tenure_years_ind(hire_date: date, today: date) -> int:
    """Completed full calendar years between hire_date and today (anniversary method)."""
    years = today.year - hire_date.year
    if (today.month, today.day) < (hire_date.month, hire_date.day):
        years -= 1
    return years


def _assign_band_ind(years: int) -> str:
    """Map completed years to the fixed tenure band label."""
    if years < 1:
        return "<1y"
    if years < 3:
        return "1-3y"
    if years < 5:
        return "3-5y"
    if years < 10:
        return "5-10y"
    return "10+y"


async def _direct_odoo_tenure(cairo_today: date) -> dict:
    """
    Re-implement net-accumulated tenure from scratch via OdooClient.

    Fetches ALL hr.contract with active_test=False (includes contracts on
    archived employees — 13 Running contracts would be silently dropped
    without this context flag).

    Returns:
        {
            "band_counts":        {"<1y": n, "1-3y": n, ...},
            "missing_date_count": n,
            "total_employed":     n,
        }
    """
    async with OdooClient() as client:
        all_contracts: list[dict] = await client.execute_kw(
            "hr.contract",
            "search_read",
            args=[[]],
            kwargs={
                "fields": ["employee_id", "state", "date_start", "date_end"],
                "context": {"active_test": False},
            },
        )

    # Group all contracts by employee_id
    contracts_by_emp: dict[int, list[dict]] = defaultdict(list)
    for c in all_contracts:
        emp_raw = c.get("employee_id")
        if isinstance(emp_raw, (list, tuple)) and emp_raw:
            eid = int(emp_raw[0])
        elif emp_raw and emp_raw is not False:
            eid = int(emp_raw)
        else:
            continue
        contracts_by_emp[eid].append(c)

    # Distinct employees with at least one Running contract
    running_emp_ids: set[int] = {
        eid for eid, cs in contracts_by_emp.items()
        if any(c.get("state") == _RUNNING_STATE for c in cs)
    }

    band_counts: dict[str, int] = {label: 0 for label in EXPECTED_BAND_LABELS}
    missing_date_count = 0

    for eid in running_emp_ids:
        periods: list[tuple[date, date]] = []
        running_null_start = False

        for c in contracts_by_emp[eid]:
            ds_raw = c.get("date_start")
            if not ds_raw:
                if c.get("state") == _RUNNING_STATE:
                    running_null_start = True
                continue
            ds = date.fromisoformat(str(ds_raw))
            de_raw = c.get("date_end")
            de = date.fromisoformat(str(de_raw)) if de_raw else cairo_today
            periods.append((ds, de))

        if running_null_start:
            missing_date_count += 1
            continue
        if not periods:
            missing_date_count += 1
            continue

        periods.sort(key=lambda p: p[0])

        merged: list[list[date]] = [list(periods[0])]
        for (start, end) in periods[1:]:
            if start < merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])

        total_days = sum((end - start).days for (start, end) in merged)
        virtual_start = cairo_today - timedelta(days=total_days)
        band_counts[_assign_band_ind(_tenure_years_ind(virtual_start, cairo_today))] += 1

    return {
        "band_counts":        band_counts,
        "missing_date_count": missing_date_count,
        "total_employed":     sum(band_counts.values()) + missing_date_count,
    }


# ── KPI A cross-check ─────────────────────────────────────────────────────────

def _fetch_kpi_a_headcount(http: httpx.Client) -> int | None:
    """GET KPI A endpoint via the shared authed client; return headcount or None on failure."""
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
    print("KPI B (HR) — Tenure Distribution Live Verification (re-foundation 2026-06-03)")
    print(f"Employment def  : distinct employees with state='open' (Running) contract")
    print(f"Tenure def      : net-accumulated service (all contracts, overlaps clamped)")
    print(f"Run timestamp   : {run_at}")
    print(f"Cairo today     : {cairo_today_str}")
    baseline_note = (
        "not set — first run establishes them"
        if BASELINE_TOTAL_EMPLOYED is None
        else f"total_employed={BASELINE_TOTAL_EMPLOYED}, missing={BASELINE_MISSING}"
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
            "bands", "missing_date_count", "total_employed",
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
        total_employed:     int  = int(body["total_employed"])
        missing_date_count: int  = int(body["missing_date_count"])
        bands:              list = body["bands"]
        reference_date:     str  = body["reference_date"]
        cache_status:       str  = body["cache_status"]
        rpc_ms:             int  = int(body["rpc_duration_ms"])

        num_bands   = len(bands)
        band_sum    = sum(b["count"] for b in bands)
        band_labels = [b["band"] for b in bands]

        # ── Step 5: Structured summary ────────────────────────────────────────────
        print(_SEP)
        print("ENDPOINT RESPONSE SUMMARY")
        print(_SEP2)
        bl_note_emp = "(no baseline)" if BASELINE_TOTAL_EMPLOYED is None else f"(baseline {BASELINE_TOTAL_EMPLOYED})"
        bl_note_mis = "(no baseline)" if BASELINE_MISSING is None else f"(baseline {BASELINE_MISSING})"
        print(f"  total_employed     : {total_employed:>6}   {bl_note_emp}")
        print(f"  missing_date_count : {missing_date_count:>6}   {bl_note_mis}")
        print(f"  sum(band counts)   : {band_sum:>6}   (must == total_employed - missing_date_count)")
        print(f"  band_sum + missing : {band_sum + missing_date_count:>6}   (must == total_employed {total_employed})")
        print(f"  num_bands          : {num_bands:>6}   (must == 5)")
        print(f"  reference_date     : {reference_date}   (cairo today: {cairo_today_str})")
        print(f"  cache_status       : {cache_status}")
        print(f"  rpc_duration_ms    : {rpc_ms} ms")
        print(f"  as_of              : {body.get('as_of')}")
        print(_SEP2)
        print("  Band breakdown (endpoint):")
        for b in bands:
            print(f"    {b['band']:>6}  :  {b['count']:>4}")
        print(f"    missing  :  {missing_date_count:>4}  (Running-contract employees with null date_start)")
        print(_SEP)
        print()

        # ── Step 6: Drift reporting (INFO only — baselines not yet set) ───────────
        print("DRIFT SECTION  [INFO only — no hard fail; set baselines after first run]:")
        _drift("total_employed    ", total_employed,     BASELINE_TOTAL_EMPLOYED)
        _drift("missing_date_count", missing_date_count, BASELINE_MISSING)
        print()

        # ── Step 7: Structural integrity (hard FAIL) ──────────────────────────────
        print("STRUCTURAL INTEGRITY  [hard checks — must hold regardless of drift]:")

        if not _check("total_employed >= 0", total_employed >= 0, f"got {total_employed}"):
            failures.append("negative_total_employed")

        if not _check("missing_date_count >= 0", missing_date_count >= 0,
                      f"got {missing_date_count}"):
            failures.append("negative_missing_date_count")

        if not _check("all band counts >= 0",
                      all(b["count"] >= 0 for b in bands),
                      "at least one band count is negative"):
            failures.append("negative_band_count")

        if not _check("num_bands == 5", num_bands == 5, f"got {num_bands}"):
            failures.append("wrong_band_count")

        if not _check(
            "band labels in fixed order",
            band_labels == EXPECTED_BAND_LABELS,
            f"got {band_labels}",
        ):
            failures.append("band_label_order_wrong")

        if not _check(
            "band_sum + missing_date_count == total_employed",
            band_sum + missing_date_count == total_employed,
            f"{band_sum} + {missing_date_count} = {band_sum + missing_date_count} != {total_employed}",
        ):
            failures.append("sanity_invariant_violated")

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

        # ── Step 10: Cross-KPI consistency (INFO only) ────────────────────────────
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

    # ── Step 11: Independent Odoo cross-check (hard FAIL if mismatch) ─────────
    print("INDEPENDENT ODOO CROSS-CHECK  [FAIL if endpoint != direct Odoo computation]:")
    _log(_INFO, "Re-implementing net-accumulated tenure algorithm from scratch ...")
    _log(_INFO,
         "Fetching all hr.contract with active_test=False "
         "(includes Running contracts on archived employees) ...")

    direct_total:   int | str = "error"
    direct_missing: int | str = "error"
    bands_match                = "error"

    try:
        direct_result = asyncio.run(_direct_odoo_tenure(cairo_today))
        direct_band_counts:    dict[str, int] = direct_result["band_counts"]
        direct_missing_count:  int            = direct_result["missing_date_count"]
        direct_total_employed: int            = direct_result["total_employed"]

        direct_total   = direct_total_employed
        direct_missing = direct_missing_count

        _log(_INFO, f"Direct computation — total_employed    : {direct_total_employed}")
        _log(_INFO, f"Direct computation — missing_date_count: {direct_missing_count}")
        print("  Direct band breakdown:")
        for label in EXPECTED_BAND_LABELS:
            print(f"    {label:>6}  :  {direct_band_counts.get(label, 0):>4}")
        print(f"    missing  :  {direct_missing_count:>4}")
        print()

        endpoint_band_counts = {b["band"]: b["count"] for b in bands}
        band_mismatches: list[str] = []
        for label in EXPECTED_BAND_LABELS:
            ep_val = endpoint_band_counts.get(label, 0)
            dr_val = direct_band_counts.get(label, 0)
            if ep_val != dr_val:
                band_mismatches.append(f"{label}: endpoint={ep_val} direct={dr_val}")

        if total_employed == direct_total_employed and not band_mismatches:
            _log(_PASS,
                 f"endpoint == direct Odoo computation "
                 f"(total_employed={total_employed}, all bands match)")
            bands_match = "MATCH"
        else:
            detail_parts = []
            if total_employed != direct_total_employed:
                detail_parts.append(
                    f"total_employed: endpoint={total_employed} direct={direct_total_employed}"
                )
            if band_mismatches:
                detail_parts.append(f"band mismatches: {band_mismatches}")
            detail = "; ".join(detail_parts)
            _log(_FAIL,
                 f"endpoint != direct Odoo computation — SERVICE BUG: {detail}")
            failures.append(f"endpoint_odoo_mismatch:{detail}")
            bands_match = f"MISMATCH:{detail}"

    except Exception as exc:
        _log(_FAIL, f"Direct Odoo computation failed — cross-check skipped: {exc}")
        failures.append(f"direct_odoo_failed:{type(exc).__name__}")
        bands_match = f"error:{type(exc).__name__}"
    print()

    # ── Result ────────────────────────────────────────────────────────────────
    _append_log_row(
        run_at=run_at,
        total_employed=total_employed,
        missing_date_count=missing_date_count,
        band_sum=band_sum,
        direct_total_employed=direct_total,
        direct_missing=direct_missing,
        bands_match=bands_match,
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
             "All structural checks passed. "
             "Review [INFO] drift lines above for band distribution context.")
        _log(_INFO,
             "Next step: update BASELINE_TOTAL_EMPLOYED and BASELINE_MISSING "
             "in this script with values from this run, then commit.")
    print(_SEP)

    return 0


if __name__ == "__main__":
    sys.exit(main())
