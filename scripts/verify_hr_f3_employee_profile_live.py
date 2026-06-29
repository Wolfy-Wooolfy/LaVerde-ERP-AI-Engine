"""
Live verification for HR F3 — Employee Profile Drill-Down (2026-06-07).

Proves that GET /api/v1/hr/employee/{employee_id} returns correct, wage-free
profile data for 5 sample Running-contract employees, and that boundary guards
work correctly.

Usage:
    python scripts/verify_hr_f3_employee_profile_live.py [--url http://localhost:8000]

Requires the FastAPI server to be running and reachable.
Set VERIFY_USERNAME / VERIFY_PASSWORD env vars (or .env) to override credentials.

Pre-flight (Decision 6.4): purge all __pycache__, start uvicorn WITHOUT --reload.

Exits 0 (always). Findings use [PASS] / [FAIL] / [INFO] markers.
Appends one TSV summary row to logs/hr_f3_employee_profile_verification.log.

Sample employees (employee_ids): 1057, 1179, 1181, 1173, 1380

Hard checks ([FAIL]):
  Per sample employee:
    F3-1  HTTP 200 on GET /api/v1/hr/employee/{id} (with auth)
    F3-2  name field non-empty AND equals direct Odoo read (PASS/FAIL — value never printed)
    F3-3  department_name matches open contract department_id display name
    F3-4  manager_name equals direct Odoo hr.employee parent_id read (PASS/FAIL — value never printed)
    F3-5  hire_date matches open contract date_start (ISO)
    F3-6  contract_end + is_open_ended match open contract date_end
    F3-7  ZERO wage/comp keys anywhere in profile response

  Boundary guards:
    F3-8  GET /api/v1/hr/employee/0 → 400
    F3-9  GET /api/v1/hr/employee/999999 → 404
    F3-10 GET /api/v1/hr/employee/{sample_id} without auth → 401

Privacy:
  employee names never printed (F3-2: compare by value, report PASS/FAIL only).
  manager names never printed (F3-4: compare by value, report PASS/FAIL only).
  department_name, hire_date, contract_end, location: echoed in check output (not PII).
  No wage values printed anywhere.
"""

import argparse
import asyncio
import io
import os
import sys
from datetime import datetime, timezone

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
LOG_FILE    = "logs/hr_f3_employee_profile_verification.log"

F3_ENDPOINT_TPL = "/api/v1/hr/employee/{employee_id}"

_SAMPLE_IDS: list[int] = [1057, 1179, 1181, 1173, 1380]

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


def _m2o_name(raw: object) -> str | None:
    """Extract display_name from Odoo many2one [id, name] pair, or None."""
    if isinstance(raw, (list, tuple)) and len(raw) > 1:
        name = raw[1]
        if name and name is not False:
            return str(name).strip()
    return None


# ── Direct Odoo ground truth ──────────────────────────────────────────────────

async def _direct_odoo_query(sample_ids: list[int]) -> dict:
    """
    Query Odoo directly for the 5 sample employees.

    Returns:
        {
          "contracts": {emp_id: {"date_start", "date_end", "dept_name", "job_name"}},
          "employees": {emp_id: {"name", "manager_name", "location"}},
        }

    NO wage field touched. Domain: state='open' per contract.
    """
    async with OdooClient() as client:
        # RPC A: open contracts for sample employees
        contracts_raw: list[dict] = await client.execute_kw(
            "hr.contract",
            "search_read",
            args=[[("employee_id", "in", sample_ids), ("state", "=", "open")]],
            kwargs={
                "fields": ["employee_id", "department_id", "job_id",
                           "date_start", "date_end", "state"],
            },
        )
        # RPC B: employee profile fields for sample employees
        employees_raw: list[dict] = await client.execute_kw(
            "hr.employee",
            "search_read",
            args=[[("id", "in", sample_ids)]],
            kwargs={
                "fields": ["name", "parent_id", "work_location_id"],
                "context": {"active_test": False},
            },
        )

    contracts: dict[int, dict] = {}
    for c in contracts_raw:
        emp_raw = c.get("employee_id")
        if isinstance(emp_raw, (list, tuple)) and emp_raw:
            emp_id = int(emp_raw[0])
            contracts[emp_id] = {
                "date_start": c.get("date_start") or None,
                "date_end":   c.get("date_end"),       # False or ISO string
                "dept_name":  _m2o_name(c.get("department_id")),
                "job_name":   _m2o_name(c.get("job_id")),
            }

    employees: dict[int, dict] = {}
    for e in employees_raw:
        emp_id = int(e.get("id", 0))
        if emp_id:
            raw_name = e.get("name") or ""
            employees[emp_id] = {
                "name":         str(raw_name).strip(),
                "manager_name": _m2o_name(e.get("parent_id")),
                "location":     _m2o_name(e.get("work_location_id")),
            }

    return {"contracts": contracts, "employees": employees}


# ── Log append ────────────────────────────────────────────────────────────────

def _append_log(
    run_at: str,
    employees_checked: int,
    name_pass: int,
    name_fail: int,
    dept_pass: int,
    dept_fail: int,
    hire_pass: int,
    hire_fail: int,
    no_wage_pass: int,
    no_wage_fail: int,
    guard_400: str,
    guard_404: str,
    guard_401: str,
    error: str = "",
) -> None:
    os.makedirs("logs", exist_ok=True)
    exists = os.path.isfile(LOG_FILE)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        if not exists:
            f.write(
                "run_at\temployees_checked\t"
                "name_pass\tname_fail\tdept_pass\tdept_fail\t"
                "hire_pass\thire_fail\tno_wage_pass\tno_wage_fail\t"
                "guard_400\tguard_404\tguard_401\terror\n"
            )
        f.write(
            f"{run_at}\t{employees_checked}\t"
            f"{name_pass}\t{name_fail}\t{dept_pass}\t{dept_fail}\t"
            f"{hire_pass}\t{hire_fail}\t{no_wage_pass}\t{no_wage_fail}\t"
            f"{guard_400}\t{guard_404}\t{guard_401}\t{error}\n"
        )


# ── Main ──────────────────────────────────────────────────────────────────────

def main(base_url: str) -> None:
    run_at = datetime.now(timezone.utc).isoformat()
    auth = (USERNAME, PASSWORD)

    name_pass = name_fail = 0
    dept_pass = dept_fail = 0
    hire_pass = hire_fail = 0
    no_wage_pass = no_wage_fail = 0
    guard_400 = guard_404 = guard_401 = "n/a"
    employees_checked = 0
    error_msg = ""

    try:
        # ── Section 1: Direct Odoo ground truth ──────────────────────────────
        _section("Section 1 — Direct Odoo ground truth (2 RPCs, no wage)")
        _log(_INFO, f"Sample employee IDs: {_SAMPLE_IDS}")
        _log(_INFO, "Querying hr.contract (state=open) and hr.employee directly …")

        odoo = asyncio.run(_direct_odoo_query(_SAMPLE_IDS))
        odoo_contracts = odoo["contracts"]
        odoo_employees = odoo["employees"]

        _log(_INFO, f"Open contracts found : {len(odoo_contracts)}")
        _log(_INFO, f"Employee records found: {len(odoo_employees)}")

        found_all = _check(
            "All 5 sample employees have an open contract",
            all(eid in odoo_contracts for eid in _SAMPLE_IDS),
            f"missing contracts for: {[e for e in _SAMPLE_IDS if e not in odoo_contracts]}",
        )
        if not found_all:
            _log(_INFO, "Some samples lack an open contract — those IDs will get 404 from F3 endpoint.")

        # ── Section 2: Per-employee endpoint checks ───────────────────────────
        _section("Section 2 — Per-employee F3 endpoint checks")

        # Login once (limiter 10/minute); reuse the sync client for all authed probes.
        client = api_login(base_url)
        try:

            for emp_id in _SAMPLE_IDS:
                print()
                print(f"  Employee ID {emp_id}")

                url = F3_ENDPOINT_TPL.format(employee_id=emp_id)
                r = client.get(url, timeout=30)

                # F3-1: HTTP 200
                ok_200 = _check(
                    f"    F3-1  HTTP 200",
                    r.status_code == 200,
                    f"got {r.status_code}",
                )
                if not ok_200:
                    _log(_INFO, f"    Skipping field checks for employee {emp_id} (no 200)")
                    continue

                profile = r.json()
                employees_checked += 1

                # F3-2: name — compare value against Odoo direct, NEVER print
                odoo_name = (odoo_employees.get(emp_id) or {}).get("name")
                profile_name = profile.get("name", "")
                ok = _check(
                    f"    F3-2  name non-empty and matches Odoo direct (value not printed)",
                    bool(profile_name) and (profile_name == odoo_name),
                    "name mismatch or empty — values differ" if (not profile_name or profile_name != odoo_name) else "",
                )
                name_pass += ok
                name_fail += not ok

                # F3-3: department_name (not PII — may be echoed)
                odoo_dept = (odoo_contracts.get(emp_id) or {}).get("dept_name")
                profile_dept = profile.get("department_name")
                ok = _check(
                    f"    F3-3  department_name='{profile_dept}' matches Odoo contract dept",
                    profile_dept == odoo_dept,
                    f"expected '{odoo_dept}', got '{profile_dept}'" if profile_dept != odoo_dept else "",
                )
                dept_pass += ok
                dept_fail += not ok

                # F3-4: manager_name — compare value against Odoo direct, NEVER print
                odoo_mgr = (odoo_employees.get(emp_id) or {}).get("manager_name")
                profile_mgr = profile.get("manager_name")
                ok = _check(
                    f"    F3-4  manager_name matches Odoo direct (value not printed)",
                    profile_mgr == odoo_mgr,
                    "manager_name mismatch — values differ" if profile_mgr != odoo_mgr else "",
                )
                name_pass += ok   # counted alongside name checks (PII identity group)
                name_fail += not ok

                # F3-5: hire_date (not PII — echo safe)
                odoo_ds = (odoo_contracts.get(emp_id) or {}).get("date_start")
                ok = _check(
                    f"    F3-5  hire_date='{profile.get('hire_date')}' matches contract date_start",
                    profile.get("hire_date") == odoo_ds,
                    f"expected '{odoo_ds}', got '{profile.get('hire_date')}'" if profile.get("hire_date") != odoo_ds else "",
                )
                hire_pass += ok
                hire_fail += not ok

                # F3-6: contract_end + is_open_ended
                odoo_de       = (odoo_contracts.get(emp_id) or {}).get("date_end")
                exp_open      = not odoo_de
                exp_end       = str(odoo_de) if odoo_de else None
                ok_open = _check(
                    f"    F3-6a is_open_ended={profile.get('is_open_ended')} correct",
                    profile.get("is_open_ended") == exp_open,
                    f"expected is_open_ended={exp_open}",
                )
                ok_end = _check(
                    f"    F3-6b contract_end='{profile.get('contract_end')}' matches Odoo",
                    profile.get("contract_end") == exp_end,
                    f"expected '{exp_end}', got '{profile.get('contract_end')}'" if profile.get("contract_end") != exp_end else "",
                )
                hire_pass += (ok_open and ok_end)
                hire_fail += not (ok_open and ok_end)

                # F3-7: ZERO wage/comp keys in profile
                wage_found = _WAGE_KEYS & set(profile.keys())
                ok = _check(
                    f"    F3-7  ZERO wage/comp keys in profile",
                    not wage_found,
                    f"violations: {wage_found}" if wage_found else "",
                )
                no_wage_pass += ok
                no_wage_fail += not ok

                # Info: safe non-PII fields
                _log(_INFO,
                     f"    job_title='{profile.get('job_title')}'"
                     f" | dept='{profile.get('department_name')}'"
                     f" | hire_date='{profile.get('hire_date')}'"
                     f" | tenure={profile.get('tenure_years')}yrs"
                     f" | location='{profile.get('location')}'"
                     f" | open_ended={profile.get('is_open_ended')}"
                     f" | rpc_duration_ms={profile.get('rpc_duration_ms')}"
                )

            # ── Section 3: Boundary / guard checks ───────────────────────────
            _section("Section 3 — Boundary / guard checks")

            # F3-8: id=0 → 400
            r_zero = client.get("/api/v1/hr/employee/0", timeout=30)
            ok_400 = _check(
                "F3-8  GET /api/v1/hr/employee/0 → 400",
                r_zero.status_code == 400,
                f"got {r_zero.status_code}",
            )
            guard_400 = "PASS" if ok_400 else "FAIL"

            # F3-9: unknown id → 404
            r_unk = client.get("/api/v1/hr/employee/999999", timeout=30)
            ok_404 = _check(
                "F3-9  GET /api/v1/hr/employee/999999 → 404",
                r_unk.status_code == 404,
                f"got {r_unk.status_code}",
            )
            guard_404 = "PASS" if ok_404 else "FAIL"

            # F3-10: no auth → 401.  Under session-cookie auth the cookie lives in
            # the authed client's jar, and auth=None does NOT clear it — so the
            # authed client would still send the cookie and get 200.  Issue this
            # one request from a SEPARATE, cookie-free client to prove the
            # endpoint still rejects unauthenticated requests.
            with httpx.Client(base_url=base_url) as anon:
                r_noauth = anon.get(F3_ENDPOINT_TPL.format(employee_id=_SAMPLE_IDS[0]))
            ok_401 = _check(
                f"F3-10 GET /api/v1/hr/employee/{_SAMPLE_IDS[0]} without auth → 401",
                r_noauth.status_code == 401,
                f"got {r_noauth.status_code}",
            )
            guard_401 = "PASS" if ok_401 else "FAIL"
        finally:
            client.close()

    except Exception as exc:
        error_msg = str(exc)
        print(f"\n[ERROR] {exc}", flush=True)
        import traceback
        traceback.print_exc()

    # ── Summary ───────────────────────────────────────────────────────────────
    _section("Summary")
    _log(_INFO, f"Employees with 200 response:   {employees_checked}")
    _log(_INFO, f"Name/manager identity checks:  {name_pass} PASS / {name_fail} FAIL")
    _log(_INFO, f"Dept/hire-date/end checks:     {dept_pass + hire_pass} PASS / {dept_fail + hire_fail} FAIL")
    _log(_INFO, f"No-wage checks:                {no_wage_pass} PASS / {no_wage_fail} FAIL")
    _log(_INFO, f"Guard 400 (id=0):              {guard_400}")
    _log(_INFO, f"Guard 404 (unknown id):        {guard_404}")
    _log(_INFO, f"Guard 401 (no auth):           {guard_401}")

    total_fail = name_fail + dept_fail + hire_fail + no_wage_fail
    all_guards = (guard_400 == "PASS" and guard_404 == "PASS" and guard_401 == "PASS")

    if total_fail == 0 and all_guards and not error_msg:
        _log(_PASS, "ALL F3 PROFILE CHECKS PASSED")
    else:
        _log(_FAIL, f"FAILURES DETECTED: {total_fail} field check(s) failed; guards: 400={guard_400} 404={guard_404} 401={guard_401}")

    _append_log(
        run_at=run_at,
        employees_checked=employees_checked,
        name_pass=name_pass,
        name_fail=name_fail,
        dept_pass=dept_pass,
        dept_fail=dept_fail,
        hire_pass=hire_pass,
        hire_fail=hire_fail,
        no_wage_pass=no_wage_pass,
        no_wage_fail=no_wage_fail,
        guard_400=guard_400,
        guard_404=guard_404,
        guard_401=guard_401,
        error=error_msg,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="HR F3 live verification — employee profile drill-down"
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help=f"FastAPI base URL (default: {DEFAULT_URL})",
    )
    args = parser.parse_args()
    main(args.url)
