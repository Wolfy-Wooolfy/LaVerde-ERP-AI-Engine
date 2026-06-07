"""
Live verification for HR KPI D — Department Payroll Cost (2026-06-07).

Employment definition (§3.6): an employee is employed at La Verde IFF they hold
a contract in state='open' (Running). hr.employee.active is a UI/archive flag,
NOT an employment signal. True headcount = distinct Running-contract employees = 115.

Usage:
    python scripts/verify_department_cost_live.py [--url http://localhost:8000]

Requires the FastAPI server to be running and reachable.
Set VERIFY_USERNAME / VERIFY_PASSWORD env vars (or .env) to override
the default admin credentials.

Exits 0 always. Findings printed with [PASS]/[FAIL]/[INFO] markers
and appended as one TSV row to logs/hr_kpi_d_department_cost_verification.log.

Hard checks ([FAIL]):
    * HTTP 200, required keys present
    * total_running_contracts == 115  (§3.6.D post-fix baseline)
    * total_running_contracts == KPI A headcount  (population identity invariant —
      both KPIs derive from the same Running-contract query; any divergence is a bug)
    * abs(service grand_total_wage - direct Odoo SUM(wage)) < 0.01  (float tolerance)
    * sum(row.running_contract_count) == total_running_contracts  (count reconciliation)
    * structural shape (non-negative counts, currency="EGP", basis="monthly", etc.)
    * reference_date == Cairo today
    * cache_status in {fresh, cached}

Calibration output (first real look at the Running population):
    Per-department DISTINCT-EMPLOYEE COUNT distribution from a direct Odoo query
    (counts only — this is the D4 calibration deferred from FIRST OUTPUT; use it
    to confirm k-anon pooling on real data and lock the small-dept policy at D5).

Privacy:
    * Counts: printed freely.
    * Monetary output: (i) org-level grand_total_wage cross-check value only;
      (ii) the service's already-k-anon-applied rows echoed as-is.
    * Raw per-department grouping prints COUNTS ONLY — no per-dept wage sums ever.
    * No individual wages, ever.
"""

import argparse
import asyncio
import io
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# sys.path.insert so script runs without PYTHONPATH set
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import httpx
from dotenv import load_dotenv

from backend.shared.odoo.client import OdooClient

load_dotenv(dotenv_path=".env")

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Config ────────────────────────────────────────────────────────────────────

DEFAULT_URL    = os.environ.get("BACKEND_URL", "http://localhost:8000")
USERNAME       = os.environ.get("VERIFY_USERNAME", "admin")
PASSWORD       = os.environ.get("VERIFY_PASSWORD", "password")
ENDPOINT       = "/api/v1/hr/kpi/department-cost"
KPI_A_ENDPOINT = "/api/v1/hr/kpi/headcount"
LOG_FILE       = "logs/hr_kpi_d_department_cost_verification.log"
CAIRO_TZ       = ZoneInfo("Africa/Cairo")

# Established post-fix 2026-06-03; hard check fails if service disagrees
BASELINE_TOTAL_RUNNING_CONTRACTS: int | None = 115
WAGE_FLOAT_TOLERANCE: float = 0.01   # abs(service - direct) < this → match

_SEP  = "═" * 72
_SEP2 = "─" * 72

# ── Helpers ───────────────────────────────────────────────────────────────────

_PASS = "[PASS]"
_FAIL = "[FAIL]"
_INFO = "[INFO]"


def _log(prefix: str, msg: str) -> None:
    print(f"{prefix} {msg}", flush=True)


def _section(title: str) -> None:
    print()
    print(_SEP)
    print(title)
    print(_SEP2)


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
    total_running_contracts: int | str,
    row_count_sum: int | str,
    num_rows: int | str,
    grand_total_wage: float | str,
    direct_grand_total_wage: float | str,
    wage_match: str,
    kpi_a_headcount: int | str,
    population_match: str,
    reference_date: str,
    cache_status: str,
    rpc_duration_ms: int | str,
    error: str = "",
) -> None:
    os.makedirs("logs", exist_ok=True)
    log_exists = os.path.isfile(LOG_FILE)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        if not log_exists:
            f.write(
                "run_at\ttotal_running_contracts\trow_count_sum\tnum_rows\t"
                "grand_total_wage\tdirect_grand_total_wage\twage_match\t"
                "kpi_a_headcount\tpopulation_match\t"
                "reference_date\tcache_status\trpc_duration_ms\terror\n"
            )
        f.write(
            f"{run_at}\t{total_running_contracts}\t{row_count_sum}\t{num_rows}\t"
            f"{grand_total_wage}\t{direct_grand_total_wage}\t{wage_match}\t"
            f"{kpi_a_headcount}\t{population_match}\t"
            f"{reference_date}\t{cache_status}\t{rpc_duration_ms}\t{error}\n"
        )


# ── Independent Odoo computation ──────────────────────────────────────────────

async def _direct_odoo_computation() -> dict:
    """
    Fetch raw Running-contract data from Odoo for cross-checks.

    Domain: [('state','=','open')] — no active_test flag; identical to service
    (active_test inert on hr.contract, Item 0, discover_payroll_risk_shape.py
    2026-06-04). All 115 Running contracts returned regardless of employee
    archive flag.

    Returns:
        grand_total_wage         — SUM(wage) over all open contracts  [monetary]
        total_distinct_employees — distinct employee_id count          [count]
        dept_dist                — per-department calibration list:
            [{'department_id', 'department_name', 'distinct_employee_count'}, ...]
            COUNTS ONLY — no per-department wage sums stored or returned.
    """
    async with OdooClient() as client:
        contracts: list[dict] = await client.execute_kw(
            "hr.contract",
            "search_read",
            args=[[("state", "=", "open")]],
            kwargs={"fields": ["department_id", "wage", "employee_id"]},
        )

    grand_total_wage: float             = 0.0
    all_emp_ids:      set[int]          = set()
    dept_emp_ids: dict[tuple, set[int]] = defaultdict(set)

    for c in contracts:
        emp_raw = c.get("employee_id")
        if isinstance(emp_raw, (list, tuple)) and emp_raw:
            emp_id = int(emp_raw[0])
        elif emp_raw and emp_raw is not False:
            emp_id = int(emp_raw)
        else:
            continue  # skip orphaned contracts (no employee_id)

        dept_raw = c.get("department_id")
        if isinstance(dept_raw, (list, tuple)) and len(dept_raw) >= 2:
            dept_key = (int(dept_raw[0]), str(dept_raw[1]))
        else:
            dept_key = (None, "(بدون إدارة)")

        grand_total_wage += float(c.get("wage") or 0)
        all_emp_ids.add(emp_id)
        dept_emp_ids[dept_key].add(emp_id)

    # Counts only — wages deliberately not stored per department
    dept_dist = [
        {
            "department_id":          dept_id,
            "department_name":        dept_name,
            "distinct_employee_count": len(emp_ids),
        }
        for (dept_id, dept_name), emp_ids in dept_emp_ids.items()
    ]
    dept_dist.sort(key=lambda d: (-d["distinct_employee_count"], d["department_name"]))

    return {
        "grand_total_wage":         grand_total_wage,
        "total_distinct_employees": len(all_emp_ids),
        "dept_dist":                dept_dist,
    }


# ── KPI A headcount fetch ─────────────────────────────────────────────────────

def _fetch_kpi_a_headcount(base_url: str) -> int | None:
    try:
        with httpx.Client(timeout=30) as http:
            r = http.get(f"{base_url}{KPI_A_ENDPOINT}", auth=(USERNAME, PASSWORD))
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
    print("KPI D (HR) — Department Payroll Cost  Live Verification (2026-06-07)")
    print(f"Employment def  : distinct employees with state='open' (Running) contract")
    print(f"Wage basis      : hr.contract.wage (monthly EGP, §3.8 W1)")
    print(f"k-anon rule     : depts < 3 employees pooled into Other; pool < 3 -> suppressed")
    print(f"Run timestamp   : {run_at}")
    print(f"Cairo today     : {cairo_today_str}")
    baseline_note = (
        "not set — first run establishes them"
        if BASELINE_TOTAL_RUNNING_CONTRACTS is None
        else f"total_running_contracts={BASELINE_TOTAL_RUNNING_CONTRACTS}"
    )
    print(f"Baselines       : {baseline_note}")
    print(_SEP)
    print()
    _log(_INFO, f"Target: GET {url}")
    _log(_INFO, f"Auth user: {USERNAME}")
    print()

    failures: list[str] = []

    # ── Step 1: GET /api/v1/hr/kpi/department-cost ────────────────────────────
    try:
        with httpx.Client(timeout=60) as http:
            r = http.get(url, auth=(USERNAME, PASSWORD))
    except httpx.ConnectError as exc:
        msg = f"Cannot reach {base_url} — is the server running? ({exc})"
        _log(_FAIL, msg)
        _append_log_row(run_at, "", "", "", "", "", "", "", "", "", "", "", error=msg)
        return 0

    # ── Step 2: Status code ───────────────────────────────────────────────────
    ok = _check("HTTP 200", r.status_code == 200, f"got {r.status_code}")
    if not ok:
        _log(_INFO, f"Response body: {r.text[:500]}")
        _append_log_row(run_at, "", "", "", "", "", "", "", "", "", "", "",
                        error=f"HTTP {r.status_code}")
        return 0

    body: dict = r.json()

    # ── Step 3: Required keys ─────────────────────────────────────────────────
    required_keys = (
        "rows", "grand_total_wage", "total_running_contracts",
        "currency", "basis", "reference_date", "as_of",
        "cache_status", "rpc_duration_ms",
    )
    for k in required_keys:
        if not _check(f"key '{k}' present", k in body):
            failures.append(f"missing_key_{k}")

    if failures:
        _append_log_row(run_at, "", "", "", "", "", "", "", "", "", "", "",
                        error=f"missing keys: {failures}")
        return 0

    # ── Step 4: Extract values ────────────────────────────────────────────────
    rows:                    list  = body["rows"]
    grand_total_wage:        float = float(body["grand_total_wage"])
    total_running_contracts: int   = int(body["total_running_contracts"])
    currency:                str   = body["currency"]
    basis:                   str   = body["basis"]
    reference_date:          str   = body["reference_date"]
    cache_status:            str   = body["cache_status"]
    rpc_ms:                  int   = int(body["rpc_duration_ms"])

    row_count_sum = sum(
        int(row.get("running_contract_count", 0))
        for row in rows
        if isinstance(row, dict)
    )
    num_rows = len(rows)

    # ── Step 5: Structured summary ────────────────────────────────────────────
    _section("ENDPOINT RESPONSE SUMMARY")
    bl_note = (
        f"(baseline {BASELINE_TOTAL_RUNNING_CONTRACTS})"
        if BASELINE_TOTAL_RUNNING_CONTRACTS is not None
        else "(no baseline)"
    )
    print(f"  total_running_contracts : {total_running_contracts:>6}   {bl_note}")
    print(f"  sum(row counts)         : {row_count_sum:>6}   (must == total_running_contracts)")
    print(f"  num_rows                : {num_rows:>6}   (departments + possible Other row)")
    print(f"  grand_total_wage        : {grand_total_wage:>12.2f} EGP  (org-level aggregate)")
    print(f"  currency                : {currency}")
    print(f"  basis                   : {basis}")
    print(f"  reference_date          : {reference_date}   (cairo today: {cairo_today_str})")
    print(f"  cache_status            : {cache_status}")
    print(f"  rpc_duration_ms         : {rpc_ms} ms")
    print(f"  as_of                   : {body.get('as_of')}")
    print(_SEP2)
    print("  Service rows (k-anon already applied — Board-safe):")
    for row in rows:
        if not isinstance(row, dict):
            continue
        dept_name = row.get("department_name", "?")
        count     = row.get("running_contract_count", 0)
        wage_val  = row.get("total_wage")
        wage_str  = f"{wage_val:>12.2f} EGP" if wage_val is not None else "   [suppressed]"
        print(f"    {dept_name:<38} count={count:>4}   wage={wage_str}")

    # ── Step 6: Drift (INFO only) ─────────────────────────────────────────────
    _section("DRIFT  [INFO only — no hard fail]")
    _drift("total_running_contracts", total_running_contracts, BASELINE_TOTAL_RUNNING_CONTRACTS)

    # ── Step 7: Structural invariants (hard FAIL) ─────────────────────────────
    _section("STRUCTURAL INTEGRITY  [hard checks — must hold regardless of drift]")

    if not _check("rows is a list", isinstance(rows, list)):
        failures.append("rows_not_list")

    if not _check(
        "total_running_contracts >= 0",
        total_running_contracts >= 0,
        f"got {total_running_contracts}",
    ):
        failures.append("negative_total_running_contracts")

    if not _check(
        "grand_total_wage >= 0",
        grand_total_wage >= 0.0,
        f"got {grand_total_wage}",
    ):
        failures.append("negative_grand_total_wage")

    if not _check(
        "all row running_contract_count >= 0",
        all(
            isinstance(row, dict) and int(row.get("running_contract_count", 0)) >= 0
            for row in rows
        ),
        "at least one row has a negative running_contract_count",
    ):
        failures.append("negative_row_count")

    if not _check(
        f"sum(row.running_contract_count) == total_running_contracts ({total_running_contracts})",
        row_count_sum == total_running_contracts,
        f"row_count_sum={row_count_sum} != total_running_contracts={total_running_contracts}",
    ):
        failures.append("count_reconciliation_failed")

    if not _check('currency == "EGP"', currency == "EGP", f"got {currency!r}"):
        failures.append("wrong_currency")

    if not _check('basis == "monthly"', basis == "monthly", f"got {basis!r}"):
        failures.append("wrong_basis")

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

    # ── Step 8: Baseline hard check ───────────────────────────────────────────
    _section("BASELINE HARD CHECK  [FAIL if not 115]")
    if not _check(
        "total_running_contracts == 115",
        total_running_contracts == 115,
        f"got {total_running_contracts} — expected 115 (§3.6.D post-fix baseline, 2026-06-03)",
    ):
        failures.append(f"total_running_contracts_not_115:got={total_running_contracts}")

    # ── Step 9: HTTP headers ──────────────────────────────────────────────────
    _section("HTTP HEADERS")
    cc  = r.headers.get("cache-control", "")
    xcs = r.headers.get("x-cache-status", "")
    _check("Cache-Control: private",        "private"    in cc, f"header: {cc!r}")
    _check("Cache-Control: max-age=60",     "max-age=60" in cc, f"header: {cc!r}")
    _check("X-Cache-Status header present", bool(xcs),          f"got {xcs!r}")

    # ── Step 10: Cache hit check ──────────────────────────────────────────────
    _section("CACHE HIT CHECK")
    _log(_INFO, "Issuing second request to verify cache hit ...")
    try:
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
    except Exception as exc:
        _log(_FAIL, f"Second request failed: {exc}")
        failures.append("second_request_failed")

    # ── Step 11: KPI A population identity — HARD FAIL ───────────────────────
    _section("POPULATION IDENTITY — KPI A CROSS-CHECK  [FAIL if mismatch]")
    _log(_INFO, "Fetching KPI A headcount to verify population identity ...")
    _log(_INFO, "Both KPIs derive from [('state','=','open')] on hr.contract — must be equal.")
    kpi_a_headcount: int | str = "n/a"
    population_match           = "n/a"
    kpi_a_count = _fetch_kpi_a_headcount(base_url)
    if kpi_a_count is None:
        _log(_FAIL, "KPI A endpoint unreachable — population identity check skipped")
        failures.append("kpi_a_unreachable")
        population_match = "error"
    else:
        kpi_a_headcount = kpi_a_count
        if not _check(
            f"total_running_contracts ({total_running_contracts}) "
            f"== KPI A headcount ({kpi_a_count})",
            total_running_contracts == kpi_a_count,
            "population identity violated — KPI D and KPI A must count identical employees",
        ):
            failures.append(
                f"population_identity_violated:"
                f"kpi_d={total_running_contracts},kpi_a={kpi_a_count}"
            )
            population_match = f"MISMATCH:kpi_d={total_running_contracts},kpi_a={kpi_a_count}"
        else:
            population_match = "MATCH"

    # ── Step 12: Independent Odoo cross-check — HARD FAIL ─────────────────────
    _section("INDEPENDENT ODOO CROSS-CHECK  [FAIL if mismatch]")
    _log(_INFO, "Fetching all Running contracts directly from Odoo (1 RPC) ...")
    _log(_INFO, "Domain: [('state','=','open')] | Fields: department_id, wage, employee_id")
    _log(_INFO, "No active_test flag — identical to service (Item 0, 2026-06-04)")

    direct_grand_total: float | str = "error"
    wage_match                      = "error"

    try:
        direct = asyncio.run(_direct_odoo_computation())
        direct_grand_total_wage:    float      = direct["grand_total_wage"]
        direct_total_distinct_emps: int        = direct["total_distinct_employees"]
        dept_dist:                  list[dict] = direct["dept_dist"]
        direct_grand_total                     = direct_grand_total_wage

        # ── Per-department calibration — COUNTS ONLY ──────────────────────────
        print()
        print(_SEP)
        print("PER-DEPARTMENT DISTINCT-EMPLOYEE COUNT DISTRIBUTION  [D4 calibration]")
        print(_SEP2)
        print("  (counts only — first live look at the Running population per department)")
        print("  Departments with count < 3 will be pooled into Other by the service.")
        print()
        print(f"  {'Department':<42} {'Dist. Emp.':>10}")
        print(f"  {'─' * 42} {'─' * 10}")
        below_threshold: list[tuple[str, int]] = []
        for d in dept_dist:
            count  = d["distinct_employee_count"]
            marker = "  <- count < k=3; pooled" if count < 3 else ""
            print(f"  {d['department_name']:<42} {count:>10}{marker}")
            if count < 3:
                below_threshold.append((d["department_name"], count))
        print()
        _log(_INFO, f"Total departments with >= 1 Running contract: {len(dept_dist)}")
        _log(_INFO, f"Departments with count < 3 (-> Other pool)  : {len(below_threshold)}")
        _log(_INFO, f"Direct total distinct employees              : {direct_total_distinct_emps}")
        if below_threshold:
            pool_count = sum(c for _, c in below_threshold)
            pool_note  = "total_wage suppressed (null)" if pool_count < 3 else "total_wage returned"
            _log(_INFO, f"Combined Other pool count                   : {pool_count}")
            _log(_INFO, f"Other row in service                        : {pool_note}")

        # ── Cross-checks ──────────────────────────────────────────────────────
        _section("CROSS-CHECK RESULTS")

        wage_abs_diff = abs(grand_total_wage - direct_grand_total_wage)
        if not _check(
            f"service grand_total_wage == direct Odoo SUM(wage)  "
            f"(abs diff {wage_abs_diff:.4f} EGP, tolerance {WAGE_FLOAT_TOLERANCE})",
            wage_abs_diff < WAGE_FLOAT_TOLERANCE,
            f"service={grand_total_wage:.2f}  direct={direct_grand_total_wage:.2f}  "
            f"diff={wage_abs_diff:.4f} EGP",
        ):
            failures.append(
                f"grand_total_wage_mismatch:"
                f"service={grand_total_wage:.2f},direct={direct_grand_total_wage:.2f}"
            )
            wage_match = f"MISMATCH:diff={wage_abs_diff:.4f}"
        else:
            _log(_INFO,
                 f"grand_total_wage: service={grand_total_wage:.2f}  "
                 f"direct={direct_grand_total_wage:.2f}  EGP  diff={wage_abs_diff:.4f}")
            wage_match = "MATCH"

        if not _check(
            f"total_running_contracts ({total_running_contracts}) "
            f"== direct distinct employee count ({direct_total_distinct_emps})",
            total_running_contracts == direct_total_distinct_emps,
            "service and direct Odoo disagree on the employed population size",
        ):
            failures.append(
                f"distinct_emp_count_mismatch:"
                f"service={total_running_contracts},direct={direct_total_distinct_emps}"
            )

    except Exception as exc:
        _log(_FAIL, f"Direct Odoo computation failed — cross-check skipped: {exc}")
        failures.append(f"direct_odoo_failed:{type(exc).__name__}")
        wage_match     = f"error:{type(exc).__name__}"
        direct_grand_total = "error"

    # ── Log row ───────────────────────────────────────────────────────────────
    _append_log_row(
        run_at=run_at,
        total_running_contracts=total_running_contracts,
        row_count_sum=row_count_sum,
        num_rows=num_rows,
        grand_total_wage=f"{grand_total_wage:.2f}",
        direct_grand_total_wage=(
            f"{direct_grand_total:.2f}"
            if isinstance(direct_grand_total, float)
            else str(direct_grand_total)
        ),
        wage_match=wage_match,
        kpi_a_headcount=kpi_a_headcount,
        population_match=population_match,
        reference_date=reference_date,
        cache_status=cache_status,
        rpc_duration_ms=rpc_ms,
    )

    # ── Result ────────────────────────────────────────────────────────────────
    print()
    print(_SEP)
    if failures:
        _log(_FAIL,
             f"Verification complete — {len(failures)} structural issue(s): {failures}")
        _log(_INFO, "Review [FAIL] lines above. Exit 0 (script always exits 0).")
    else:
        _log(_PASS, "All structural checks passed. Review [INFO] lines above for context.")
        _log(_INFO,
             "Next: if baselines look right, update BASELINE_TOTAL_RUNNING_CONTRACTS "
             "in this script with the live value and commit.")
    print(_SEP)

    return 0


if __name__ == "__main__":
    sys.exit(main())
