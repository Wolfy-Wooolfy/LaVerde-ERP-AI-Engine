"""
Read-only query: exit-gap employees for HR payroll action.

Exit-gap = active=True AND has at least one Expired/Cancelled contract
           AND has NO Running contract.

These employees will NOT receive a payslip at the next payroll run.
HR must decide per employee: renew contract (if still employed) or
archive the record (if departed).

Output: employee_id | name | most_recent_contract_end | days_since_lapse
Sorted by most_recent_contract_end ascending (longest overdue first).

Two RPCs (read-only, no writes):
  RPC 1 — hr.employee: active=True records, fields: id, name
  RPC 2 — hr.contract: all states, active_test=False,
           fields: id, state, employee_id, date_end

PII note: names read for the same justified reason as COMPUTATION 6 in
verify_employment_foundation.py — HR needs to recognise the people to
decide renew-vs-archive.  Names are not written to any log file.
"""

import asyncio
import io
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from backend.shared.odoo.client import OdooClient

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

CAIRO_TZ    = ZoneInfo("Africa/Cairo")
_SEP        = "═" * 72
_SEP2       = "─" * 72
_RUNNING    = "open"
_EXIT_STATES = frozenset({"close", "cancel"})


def _emp_id(raw: object) -> int | None:
    if isinstance(raw, (list, tuple)) and raw:
        return int(raw[0])
    if raw and raw is not False:
        return int(raw)
    return None


async def run() -> None:
    run_at      = datetime.now(timezone.utc).isoformat()
    cairo_today = datetime.now(CAIRO_TZ).date()

    print(_SEP)
    print("Exit-Gap Employee Query — for HR payroll action")
    print(f"Run timestamp : {run_at}")
    print(f"Cairo today   : {cairo_today}")
    print(_SEP)

    async with OdooClient() as client:

        print("RPC 1: hr.employee — active=True only")
        emp_records = await client.execute_kw(
            "hr.employee",
            "search_read",
            args=[[("active", "=", True)]],
            kwargs={"fields": ["id", "name"]},
        )
        print(f"       → {len(emp_records)} active employee records")

        print("RPC 2: hr.contract — all states (active_test=False)")
        contract_records = await client.execute_kw(
            "hr.contract",
            "search_read",
            args=[[]],
            kwargs={
                "fields": ["id", "state", "employee_id", "date_end"],
                "context": {"active_test": False},
            },
        )
        print(f"       → {len(contract_records)} contract records")

    # Index contracts by employee_id
    contracts_by_emp: dict[int, list[dict]] = defaultdict(list)
    for c in contract_records:
        eid = _emp_id(c.get("employee_id"))
        if eid is not None:
            contracts_by_emp[eid].append(c)

    emp_name: dict[int, str] = {int(e["id"]): e.get("name", "") for e in emp_records}

    # Find exit-gap: active=True, ≥1 exit-state contract, 0 running contracts
    exit_gap: list[tuple[int, str, date | None]] = []  # (id, name, most_recent_end)

    for emp in emp_records:
        eid = int(emp["id"])
        emp_contracts = contracts_by_emp.get(eid, [])

        states = {c.get("state") for c in emp_contracts}
        has_running = _RUNNING in states
        has_exit    = bool(states & _EXIT_STATES)

        if has_running or not has_exit:
            continue  # employed, or no exit contracts at all

        # Most recent closed/cancelled contract by date_end
        most_recent_end: date | None = None
        for c in emp_contracts:
            if c.get("state") in _EXIT_STATES:
                raw = c.get("date_end")
                if raw and raw is not False:
                    try:
                        d = date.fromisoformat(str(raw))
                        if most_recent_end is None or d > most_recent_end:
                            most_recent_end = d
                    except (ValueError, TypeError):
                        pass

        exit_gap.append((eid, emp.get("name", ""), most_recent_end))

    # Sort: None date_end last; otherwise ascending (longest overdue first)
    exit_gap.sort(key=lambda x: (x[2] is None, x[2] or date.min))

    print(f"\n{_SEP}")
    print(f"EXIT-GAP EMPLOYEES — active=True, no Running contract  ({len(exit_gap)} total)")
    print(f"HR ACTION REQUIRED: decide renew-vs-archive per employee before next payroll run.")
    print(_SEP2)
    print(f"  {'ID':<8}  {'Name':<40}  {'Last contract end':<18}  {'Days since lapse'}")
    print(_SEP2)

    for eid, name, end_date in exit_gap:
        if end_date is not None:
            delta = (cairo_today - end_date).days
            days_str = f"{delta} days ago"
            end_str  = str(end_date)
        else:
            days_str = "no date_end on record"
            end_str  = "—"
        print(f"  {eid:<8}  {name:<40}  {end_str:<18}  {days_str}")

    print(_SEP2)
    print(f"Total exit-gap employees: {len(exit_gap)}")
    print(_SEP)


if __name__ == "__main__":
    asyncio.run(run())
