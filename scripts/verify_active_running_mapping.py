"""
Read-only record-level verification: active employees <-> running contracts (1:1 mapping).

Goal: Prove the 1:1 mapping between active hr.employee records and running
hr.contract records at the RECORD level — not just at the count level.

Count match (active=136, running=136) is necessary but not sufficient:
hypothetically, one employee could hold two running contracts while another
holds zero, and the counts would still match. This script rules that out.

Three checks:
  Check 1 — Every active employee has at least one running contract.
  Check 2 — No active employee has more than one running contract.
  Check 3 — No running contract belongs to an inactive employee.

Calls ONLY read methods (search_read — 2 RPCs).
Writes nothing to Odoo.
Prints no PII (employee IDs only — never names, emails, wages, or other fields).
Appends one TSV row to logs/active_running_mapping.log.
  - TSV data row contains counts only; violation ID lists are never written
    to the data row (would break TSV parsing).
  - When a violation list exceeds 10 entries and is truncated in console
    output, the full list is written as a # comment line below the TSV row.
Exits 0 always (PASS or FAIL — caller reads the summary).

Usage:
    python scripts/verify_active_running_mapping.py
"""

import asyncio
import io
import os
import sys
from collections import Counter
from datetime import datetime, timezone

from backend.shared.odoo.client import OdooClient

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_SEP  = "═" * 72
_SEP2 = "─" * 72
_LOG_FILE = "logs/active_running_mapping.log"
_MAX_DISPLAY_IDS = 10


def _fmt_ids(ids: list, check_label: str) -> str:
    """Format a violation ID list for console output.

    If len > _MAX_DISPLAY_IDS, shows first 10 and a continuation marker
    directing the reader to the log file for the full list.
    """
    if len(ids) <= _MAX_DISPLAY_IDS:
        return str(ids)
    shown = ids[:_MAX_DISPLAY_IDS]
    remaining = len(ids) - _MAX_DISPLAY_IDS
    return (
        f"{shown}  (... {remaining} more — see {_LOG_FILE} for full list)"
    )


def _append_tsv(
    run_at: str,
    active_count: int,
    running_count: int,
    no_running: list,
    multiple_running: list,
    inactive_contract: list,
    overall: str,
) -> None:
    os.makedirs("logs", exist_ok=True)
    log_exists = os.path.isfile(_LOG_FILE)
    with open(_LOG_FILE, "a", encoding="utf-8") as f:
        if not log_exists:
            f.write(
                "run_at\tactive_count\trunning_count\t"
                "no_running_n\tmultiple_running_n\tinactive_contract_n\t"
                "overall\n"
            )
        # Data row — counts only; lists would break TSV parsing
        f.write(
            f"{run_at}\t{active_count}\t{running_count}\t"
            f"{len(no_running)}\t{len(multiple_running)}\t{len(inactive_contract)}\t"
            f"{overall}\n"
        )
        # Annotation lines — only when the list was truncated in console output
        if len(no_running) > _MAX_DISPLAY_IDS:
            f.write(f"# check1 no_running IDs (full): {no_running}\n")
        if len(multiple_running) > _MAX_DISPLAY_IDS:
            f.write(f"# check2 multiple_running IDs (full): {multiple_running}\n")
        if len(inactive_contract) > _MAX_DISPLAY_IDS:
            f.write(f"# check3 inactive_contract IDs (full): {inactive_contract}\n")
    print(f"\n[INFO] TSV row appended to {_LOG_FILE}")


async def run() -> None:
    run_at = datetime.now(timezone.utc).isoformat()

    print(_SEP)
    print("Active employees <-> Running contracts — Record-level 1:1 mapping verification")
    print(f"Run timestamp : {run_at}")
    print(f"RPCs planned  : 2 (search_read hr.employee, search_read hr.contract)")
    print(_SEP)

    async with OdooClient() as client:

        # RPC 1 — all active employees (IDs only — no PII)
        print("\n[RPC 1] search_read(hr.employee, [('active','=',True)], fields=['id'])")
        emp_records = await client.execute_kw(
            "hr.employee",
            "search_read",
            args=[[("active", "=", True)]],
            kwargs={"fields": ["id"]},
        )
        active_emp_ids: set = {int(r["id"]) for r in emp_records}
        active_count = len(active_emp_ids)
        print(f"         Active employees found : {active_count}")

        # RPC 2 — all running contracts (IDs + employee_id only — no PII)
        print("\n[RPC 2] search_read(hr.contract, [('state','=','open')], fields=['id','employee_id'])")
        contract_records = await client.execute_kw(
            "hr.contract",
            "search_read",
            args=[[("state", "=", "open")]],
            kwargs={"fields": ["id", "employee_id"]},
        )
        running_count = len(contract_records)
        print(f"         Running contracts found : {running_count}")

    # Build employee_id list from running contracts (extract ID only from [id, name] pair)
    contract_emp_id_list: list = []
    for rec in contract_records:
        emp_raw = rec.get("employee_id")
        if isinstance(emp_raw, (list, tuple)) and emp_raw:
            contract_emp_id_list.append(int(emp_raw[0]))
        elif emp_raw:
            contract_emp_id_list.append(int(emp_raw))

    contract_emp_id_counter = Counter(contract_emp_id_list)
    contract_emp_id_set: set = set(contract_emp_id_list)

    # Check 1 — every active employee has at least one running contract
    no_running: list = sorted(active_emp_ids - contract_emp_id_set)
    check1_pass = len(no_running) == 0

    # Check 2 — no active employee has multiple running contracts
    multiple_running: list = sorted(
        emp_id for emp_id, cnt in contract_emp_id_counter.items()
        if cnt > 1 and emp_id in active_emp_ids
    )
    check2_pass = len(multiple_running) == 0

    # Check 3 — no running contract belongs to an inactive employee
    inactive_contract: list = sorted(contract_emp_id_set - active_emp_ids)
    check3_pass = len(inactive_contract) == 0

    overall_pass = check1_pass and check2_pass and check3_pass
    overall_label = "PASS" if overall_pass else "FAIL"

    print(f"\n{_SEP}")
    print("VERIFICATION RESULTS")
    print(_SEP2)
    print(f"  Check 1 — Every active employee has >=1 running contract : [{'PASS' if check1_pass else 'FAIL'}]")
    if not check1_pass:
        print(f"    >> Employees with NO running contract       (IDs): {_fmt_ids(no_running, 'check1')}")
    print(f"  Check 2 — No active employee has >1 running contract    : [{'PASS' if check2_pass else 'FAIL'}]")
    if not check2_pass:
        print(f"    >> Employees with MULTIPLE running contracts (IDs): {_fmt_ids(multiple_running, 'check2')}")
    print(f"  Check 3 — No running contract on an inactive employee   : [{'PASS' if check3_pass else 'FAIL'}]")
    if not check3_pass:
        print(f"    >> Contracts on inactive employees          (IDs): {_fmt_ids(inactive_contract, 'check3')}")
    print(_SEP2)

    no_run_str = str(len(no_running)) + (
        f"  (IDs: {_fmt_ids(no_running, 'check1')})" if no_running else ""
    )
    multi_str = str(len(multiple_running)) + (
        f"  (IDs: {_fmt_ids(multiple_running, 'check2')})" if multiple_running else ""
    )
    inact_str = str(len(inactive_contract)) + (
        f"  (IDs: {_fmt_ids(inactive_contract, 'check3')})" if inactive_contract else ""
    )

    print(f"  Active employees (target)  :  {active_count}")
    print(f"  Running contracts (target) :  {running_count}")
    print(f"  Records-level 1:1 mapping  :  {overall_label}")
    print(f"  Employees with NO running  :  {no_run_str}")
    print(f"  Employees with MULTIPLE    :  {multi_str}")
    print(f"  Contracts on inactive emp  :  {inact_str}")
    print(_SEP)

    _append_tsv(
        run_at=run_at,
        active_count=active_count,
        running_count=running_count,
        no_running=no_running,
        multiple_running=multiple_running,
        inactive_contract=inactive_contract,
        overall=overall_label,
    )


if __name__ == "__main__":
    asyncio.run(run())
