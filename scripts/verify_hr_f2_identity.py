"""
Live identity verification for HR F2 — Department Staff Drill-Down (2026-06-07).

Proves that GET /api/v1/hr/department/{department_id} returns the correct
population for every named department: count AND employee-id-set must exactly
match the Running-contract population used by KPI A (get_headcount).

Usage:
    python scripts/verify_hr_f2_identity.py [--url http://localhost:8000]

Requires the FastAPI server to be running and reachable.
Set VERIFY_USERNAME / VERIFY_PASSWORD env vars (or .env) to override credentials.

Exits 0 (always). Findings use [PASS] / [FAIL] / [INFO] markers.
Appends one TSV summary row to logs/hr_f2_identity_verification.log.

Hard checks ([FAIL]):
  Per named department:
    F2-1  HTTP 200 on GET /api/v1/hr/department/{id}
    F2-2  staff count == KPI A by_department count for this department
    F2-3  staff count == KPI D running_contract_count for this department
    F2-4  employee_id set from F2 == employee_id set from direct Odoo query
    F2-5  No wage/comp key in any staff row
    F2-6  department_id in F2 response == queried department_id

  Grand totals:
    F2-7  sum(named_dept staff counts) + other_pool_count == KPI A headcount
    F2-8  GET /api/v1/hr/department/0 returns 400 (invalid dept guard)
    F2-9  GET /api/v1/hr/department/{unknown_id} returns 404 (no-staff guard)

Privacy:
  No individual wage values printed. Staff count and employee IDs are used for
  identity checks; names are not printed in output (counts only).
  Department-level aggregates echoed from the F2 response header as-is
  (already k-anon-filtered by KPI D).
"""

import argparse
import asyncio
import io
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import httpx
from dotenv import load_dotenv

from backend.shared.odoo.client import OdooClient

load_dotenv(dotenv_path=".env")

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Config ────────────────────────────────────────────────────────────────────

DEFAULT_URL  = os.environ.get("BACKEND_URL", "http://localhost:8000")
USERNAME     = os.environ.get("VERIFY_USERNAME", "admin")
PASSWORD     = os.environ.get("VERIFY_PASSWORD", "password")
LOG_FILE     = "logs/hr_f2_identity_verification.log"

KPI_A_ENDPOINT   = "/api/v1/hr/kpi/headcount"
KPI_D_ENDPOINT   = "/api/v1/hr/kpi/department-cost"
F2_ENDPOINT_TPL  = "/api/v1/hr/department/{department_id}"
OTHER_DEPT_LABEL = "Other (small departments)"

_WAGE_KEYS = frozenset({
    "wage", "total_wage", "l10n_eg_housing_allowance",
    "l10n_eg_transportation_allowance", "l10n_eg_other_allowances",
    "basic_salary", "allowances", "contract_wage", "hourly_wage",
})

_SEP  = "═" * 72
_SEP2 = "─" * 72

_PASS = "[PASS]"
_FAIL = "[FAIL]"
_INFO = "[INFO]"


# ── Helpers ───────────────────────────────────────────────────────────────────

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


# ── Direct Odoo query (ground truth) ─────────────────────────────────────────

async def _direct_odoo_query() -> dict:
    """
    Query Odoo directly for all Running-contract records, grouped by department.

    Domain: [('state','=','open')] — identical to get_headcount().
    Fields: ['employee_id', 'department_id'] — NO wage.

    Returns:
        {
          department_id: set(employee_ids),  # for each dept_id found
          ...
          "total": set(all_employee_ids),
        }
    """
    async with OdooClient() as client:
        contracts: list[dict] = await client.execute_kw(
            "hr.contract",
            "search_read",
            args=[[("state", "=", "open")]],
            kwargs={"fields": ["employee_id", "department_id"]},
        )

    dept_emp_ids: dict[int, set[int]] = defaultdict(set)
    all_emp_ids: set[int] = set()

    for c in contracts:
        emp_raw = c.get("employee_id")
        if isinstance(emp_raw, (list, tuple)) and emp_raw:
            emp_id = int(emp_raw[0])
        elif emp_raw and emp_raw is not False:
            emp_id = int(emp_raw)
        else:
            continue

        dept_raw = c.get("department_id")
        if isinstance(dept_raw, (list, tuple)) and dept_raw:
            dept_id = int(dept_raw[0])
        else:
            dept_id = 0  # unknown department sentinel

        dept_emp_ids[dept_id].add(emp_id)
        all_emp_ids.add(emp_id)

    return {**dept_emp_ids, "total": all_emp_ids}


# ── Log append ────────────────────────────────────────────────────────────────

def _append_log(
    run_at: str,
    named_depts_checked: int,
    count_pass: int,
    count_fail: int,
    id_set_pass: int,
    id_set_fail: int,
    no_wage_pass: int,
    no_wage_fail: int,
    grand_total_ok: str,
    guard_400_ok: str,
    guard_404_ok: str,
    error: str = "",
) -> None:
    os.makedirs("logs", exist_ok=True)
    exists = os.path.isfile(LOG_FILE)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        if not exists:
            f.write(
                "run_at\tnamed_depts_checked\tcount_pass\tcount_fail\t"
                "id_set_pass\tid_set_fail\tno_wage_pass\tno_wage_fail\t"
                "grand_total_ok\tguard_400_ok\tguard_404_ok\terror\n"
            )
        f.write(
            f"{run_at}\t{named_depts_checked}\t{count_pass}\t{count_fail}\t"
            f"{id_set_pass}\t{id_set_fail}\t{no_wage_pass}\t{no_wage_fail}\t"
            f"{grand_total_ok}\t{guard_400_ok}\t{guard_404_ok}\t{error}\n"
        )


# ── Main ──────────────────────────────────────────────────────────────────────

async def main(base_url: str) -> None:
    run_at = datetime.now(timezone.utc).isoformat()
    auth = (USERNAME, PASSWORD)

    count_pass = count_fail = 0
    id_set_pass = id_set_fail = 0
    no_wage_pass = no_wage_fail = 0
    grand_total_ok = guard_400_ok = guard_404_ok = "n/a"
    error_msg = ""

    try:
        async with httpx.AsyncClient(base_url=base_url, auth=auth, timeout=30) as http:

            # ── Section 0: Fetch KPI A and KPI D via the API ─────────────────
            _section("Section 0 — Fetch KPI A (headcount) + KPI D (dept cost)")

            r_a = await http.get(KPI_A_ENDPOINT)
            r_d = await http.get(KPI_D_ENDPOINT)
            _check("KPI A HTTP 200", r_a.status_code == 200,
                   f"got {r_a.status_code}")
            _check("KPI D HTTP 200", r_d.status_code == 200,
                   f"got {r_d.status_code}")
            if r_a.status_code != 200 or r_d.status_code != 200:
                raise SystemExit("Cannot proceed: KPI A or KPI D endpoint failed.")

            kpi_a = r_a.json()
            kpi_d = r_d.json()

            total_headcount = kpi_a["headcount"]
            _log(_INFO, f"KPI A headcount: {total_headcount}")

            # Build lookup: department_id → by_department row (from KPI A)
            kpi_a_by_dept: dict[int, dict] = {
                r["department_id"]: r
                for r in kpi_a.get("by_department", [])
                if r["department_id"] is not None
            }

            # Named departments: KPI D rows with department_id is not None
            named_rows = [
                r for r in kpi_d.get("rows", [])
                if r["department_id"] is not None
            ]
            other_row = next(
                (r for r in kpi_d.get("rows", [])
                 if r["department_id"] is None),
                None,
            )
            other_count = other_row["running_contract_count"] if other_row else 0

            _log(_INFO, f"Named departments in KPI D: {len(named_rows)}")
            _log(_INFO, f"Other pool count:           {other_count}")

            # ── Section 1: Direct Odoo query (ground truth) ──────────────────
            _section("Section 1 — Direct Odoo query (ground truth employee-id sets)")
            _log(_INFO, "Querying hr.contract state='open' directly via OdooClient ...")
            odoo_dept_map = await _direct_odoo_query()
            odoo_total = len(odoo_dept_map.get("total", set()))
            _log(_INFO, f"Direct Odoo: {odoo_total} distinct Running-contract employees")
            _check(
                "Direct Odoo total == KPI A headcount",
                odoo_total == total_headcount,
                f"direct={odoo_total}, KPI A={total_headcount}",
            )

            # ── Section 2: Per-department identity checks ─────────────────────
            _section("Section 2 — Per-department drill-down identity")

            for dept_row in named_rows:
                dept_id   = dept_row["department_id"]
                dept_name = dept_row["department_name"]
                kpi_d_count = dept_row["running_contract_count"]
                kpi_a_count = kpi_a_by_dept.get(dept_id, {}).get("count", -1)

                print()
                print(f"  Dept {dept_id}: {dept_name}")

                # Call F2 drill-down endpoint
                url = F2_ENDPOINT_TPL.format(department_id=dept_id)
                r_f2 = await http.get(url)
                ok_200 = _check(
                    f"    F2-1  HTTP 200",
                    r_f2.status_code == 200,
                    f"got {r_f2.status_code}",
                )
                if not ok_200:
                    count_fail += 1
                    continue

                f2 = r_f2.json()
                f2_count = f2["headcount"]
                f2_staff = f2.get("staff", [])

                # F2-2: count == KPI A by_department
                ok = _check(
                    f"    F2-2  staff count ({f2_count}) == KPI A count ({kpi_a_count})",
                    f2_count == kpi_a_count,
                    f"f2={f2_count}, kpi_a={kpi_a_count}",
                )
                count_pass += ok
                count_fail += not ok

                # F2-3: count == KPI D running_contract_count
                ok = _check(
                    f"    F2-3  staff count ({f2_count}) == KPI D count ({kpi_d_count})",
                    f2_count == kpi_d_count,
                    f"f2={f2_count}, kpi_d={kpi_d_count}",
                )
                count_pass += ok
                count_fail += not ok

                # F2-4: employee_id set == direct Odoo query
                f2_emp_ids   = {e["employee_id"] for e in f2_staff}
                odoo_emp_ids = odoo_dept_map.get(dept_id, set())
                ok = _check(
                    f"    F2-4  employee_id set identity (F2 vs Odoo direct)",
                    f2_emp_ids == odoo_emp_ids,
                    (
                        f"only in F2: {f2_emp_ids - odoo_emp_ids}, "
                        f"only in Odoo: {odoo_emp_ids - f2_emp_ids}"
                        if f2_emp_ids != odoo_emp_ids else ""
                    ),
                )
                id_set_pass += ok
                id_set_fail += not ok

                # F2-5: no wage/comp key in any staff row
                wage_violations = []
                for emp in f2_staff:
                    found = _WAGE_KEYS & set(emp.keys())
                    if found:
                        wage_violations.append(found)
                ok = _check(
                    f"    F2-5  no wage/comp key in any staff row",
                    not wage_violations,
                    f"violations: {wage_violations}" if wage_violations else "",
                )
                no_wage_pass += ok
                no_wage_fail += not ok

                # F2-6: department_id echoed correctly
                _check(
                    f"    F2-6  department_id echoed correctly",
                    f2["department_id"] == dept_id,
                    f"f2 returned {f2['department_id']}, expected {dept_id}",
                )

                # Info: dept aggregates from header (dept-level, no individual wage)
                tw = f2["total_wage"]
                tw_str = "N/A" if tw is None else f"{tw:,.0f} EGP"
                _log(_INFO,
                     f"    Dept agg — total_wage: {tw_str}"
                     f" | pct: {f2['pct_of_total_payroll']}%"
                     f" | avg/head: {f2['avg_cost_per_head']}"
                )

            named_depts_checked = len(named_rows)

            # ── Section 3: Grand total reconciliation ─────────────────────────
            _section("Section 3 — Grand total reconciliation")

            # Sum of named dept staff counts (queried via F2) must + other_pool == total
            named_sum = 0
            for dept_row in named_rows:
                dept_id = dept_row["department_id"]
                url = F2_ENDPOINT_TPL.format(department_id=dept_id)
                r_f2 = await http.get(url)
                if r_f2.status_code == 200:
                    named_sum += r_f2.json()["headcount"]

            grand_match = (named_sum + other_count) == total_headcount
            grand_total_ok = "PASS" if grand_match else "FAIL"
            _check(
                f"F2-7  named_dept_sum ({named_sum}) + other_pool ({other_count}) "
                f"== KPI A headcount ({total_headcount})",
                grand_match,
            )

            # ── Section 4: Guard checks ───────────────────────────────────────
            _section("Section 4 — Boundary / guard checks")

            # F2-8: dept_id=0 → 400
            r_zero = await http.get("/api/v1/hr/department/0")
            ok_400 = _check(
                "F2-8  GET /api/v1/hr/department/0 → 400",
                r_zero.status_code == 400,
                f"got {r_zero.status_code}",
            )
            guard_400_ok = "PASS" if ok_400 else "FAIL"

            # F2-9: unknown dept → 404
            r_unknown = await http.get("/api/v1/hr/department/999999")
            ok_404 = _check(
                "F2-9  GET /api/v1/hr/department/999999 → 404",
                r_unknown.status_code == 404,
                f"got {r_unknown.status_code}",
            )
            guard_404_ok = "PASS" if ok_404 else "FAIL"

    except Exception as exc:
        error_msg = str(exc)
        print(f"\n[ERROR] {exc}", flush=True)
        import traceback
        traceback.print_exc()

    # ── Summary ───────────────────────────────────────────────────────────────
    _section("Summary")
    _log(_INFO, f"Named departments checked:  {named_rows.__len__() if 'named_rows' in dir() else 0}")
    _log(_INFO, f"Count identity checks:      {count_pass} PASS / {count_fail} FAIL")
    _log(_INFO, f"ID-set identity checks:     {id_set_pass} PASS / {id_set_fail} FAIL")
    _log(_INFO, f"No-wage checks:             {no_wage_pass} PASS / {no_wage_fail} FAIL")
    _log(_INFO, f"Grand total reconciliation: {grand_total_ok}")
    _log(_INFO, f"Guard 400 (dept_id=0):      {guard_400_ok}")
    _log(_INFO, f"Guard 404 (unknown dept):   {guard_404_ok}")

    total_fail = count_fail + id_set_fail + no_wage_fail
    if total_fail == 0 and grand_total_ok == "PASS" and guard_400_ok == "PASS" and guard_404_ok == "PASS":
        _log(_PASS, "ALL F2 IDENTITY CHECKS PASSED")
    else:
        _log(_FAIL, f"FAILURES DETECTED: {total_fail} check(s) failed")

    _append_log(
        run_at=run_at,
        named_depts_checked=named_depts_checked if "named_depts_checked" in dir() else 0,
        count_pass=count_pass,
        count_fail=count_fail,
        id_set_pass=id_set_pass,
        id_set_fail=id_set_fail,
        no_wage_pass=no_wage_pass,
        no_wage_fail=no_wage_fail,
        grand_total_ok=grand_total_ok,
        guard_400_ok=guard_400_ok,
        guard_404_ok=guard_404_ok,
        error=error_msg,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="HR F2 identity verification — department staff drill-down"
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help=f"FastAPI base URL (default: {DEFAULT_URL})",
    )
    args = parser.parse_args()
    asyncio.run(main(args.url))
