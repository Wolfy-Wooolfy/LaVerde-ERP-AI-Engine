"""
Read-only re-discovery verification: employment foundation.

Establishes the CORRECTED employment numbers based on the authoritative
definition confirmed by Khaled (business owner, 2026-06-02):

  An employee is CURRENTLY EMPLOYED at La Verde IF AND ONLY IF they hold
  a contract in state='open' (Running).  hr.employee.active is an archive/UI
  flag — NOT an employment signal.

Seven computations (all read-only, no PII beyond item 6 name check):

  1. Contract state distribution — empirical Counter; no hardcoded state keys.
  2. True employed headcount: distinct employee_ids with state='open' contract.
     HARD CHECK: no employee holds >1 running contract.
  3. hr.employee.active vs running-contract divergence (re-confirms 821a7d6).
  4. Breakdown of "active=True but no running contract" by sub-state:
       incoming (has draft contract) / exited (has only close/cancel) /
       data-gap (no contract at all).
  5. Returning employees: same employee record holds both a running contract
     AND at least one prior expired/cancelled contract.
     NOTE: this ONLY catches rehires who returned on the SAME employee record.
     Rehires who received a new duplicate record (data-entry error) will NOT
     appear here — their running contract is on a new ID, their old expired
     contract is on the old ID, so the intersection misses them. Those cases
     appear in item 6, not here. Item 5 is NOT a complete rehire count.
  6. Data-quality: duplicate employee records by name (PII-minimum: name+IDs).
  7. Open contracts with date_end < today (Cairo) — expired-but-still-running
     bug cases (Odoo auto-flip to 'close' did not fire; payroll-blocking risk).

Two RPCs:
  RPC 1 — search_read(hr.employee, [], ['id','name','active'],
           context={'active_test': False})       # all ~160 employees
  RPC 2 — search_read(hr.contract,  [], ['id','state','employee_id','date_end'],
           context={'active_test': False})       # all ~149+ contracts (all states)

Output:
  - Structured console summary with [INFO]/[PASS]/[FAIL] markers.
    Most lines are [INFO] — this is DISCOVERY, not assertion testing.
    Only item 2's structural check ([PASS]/[FAIL]) is a hard assertion.
  - TSV log: logs/employment_foundation_verification.log
  - Exit 0 always.

Pre-flight (Decision 6.4): kill python, purge __pycache__, no uvicorn needed
(script hits Odoo directly — no FastAPI server required).

Usage:
    python scripts/verify_employment_foundation.py
"""

import asyncio
import io
import os
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from backend.shared.odoo.client import OdooClient

# Force UTF-8 stdout (Windows cp1252 default)
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Constants ──────────────────────────────────────────────────────────────────

CAIRO_TZ   = ZoneInfo("Africa/Cairo")
_SEP       = "═" * 72
_SEP2      = "─" * 72
_LOG_FILE  = "logs/employment_foundation_verification.log"
_MAX_IDS   = 15          # IDs shown inline before log-file truncation note

_PASS = "[PASS]"
_FAIL = "[FAIL]"
_INFO = "[INFO]"

# Known exit states (person has left the company).
# Discovered empirically from data — these are the Odoo standard keys.
_EXIT_STATES    = frozenset({"close", "cancel"})
_RUNNING_STATE  = "open"
_INCOMING_STATE = "draft"

# All state keys that are part of the Odoo standard schema.
_KNOWN_STATES = frozenset({"open", "close", "draft", "cancel"})


# ── Helpers ────────────────────────────────────────────────────────────────────

def _info(msg: str) -> None:
    print(f"{_INFO} {msg}", flush=True)


def _pass(label: str) -> None:
    print(f"{_PASS} {label}", flush=True)


def _fail(label: str) -> None:
    print(f"{_FAIL} {label}", flush=True)


def _check(label: str, condition: bool, detail: str = "") -> bool:
    if condition:
        _pass(label)
    else:
        _fail(f"{label}{(' — ' + detail) if detail else ''}")
    return condition


def _section(title: str) -> None:
    print(f"\n{_SEP}")
    print(title)
    print(_SEP2)


def _fmt_ids(ids: list, log_ref: str = _LOG_FILE) -> str:
    """Format an ID list for console; truncate with log reference if long."""
    if len(ids) <= _MAX_IDS:
        return str(ids)
    shown = ids[:_MAX_IDS]
    remaining = len(ids) - _MAX_IDS
    return f"{shown}  (... {remaining} more — see {log_ref} for full list)"


def _emp_id(raw: object) -> int | None:
    """Extract integer employee ID from Odoo many2one (list/tuple or scalar)."""
    if isinstance(raw, (list, tuple)) and raw:
        return int(raw[0])
    if raw and raw is not False:
        return int(raw)
    return None


def _append_tsv(
    run_at: str,
    cairo_today: str,
    total_employees: int,
    active_employees: int,
    inactive_employees: int,
    total_contracts: int,
    discovered_states: str,
    true_headcount: int,
    dup_running_employees: int,
    active_no_running: int,
    running_on_inactive: int,
    limbo_incoming: int,
    limbo_exited: int,
    limbo_no_contract: int,
    returning_employees: int,
    duplicate_record_names: int,
    expired_but_running: int,
    hard_check_pass: bool,
    overflow_comment: str,
) -> None:
    os.makedirs("logs", exist_ok=True)
    log_exists = os.path.isfile(_LOG_FILE)
    with open(_LOG_FILE, "a", encoding="utf-8") as f:
        if not log_exists:
            f.write(
                "run_at\tcairo_today\ttotal_employees\tactive_employees\t"
                "inactive_employees\ttotal_contracts\tdiscovered_states\t"
                "true_headcount\tdup_running_employees\tactive_no_running\t"
                "running_on_inactive\tlimbo_incoming\tlimbo_exited\t"
                "limbo_no_contract\treturning_employees\tduplicate_record_names\t"
                "expired_but_running\thard_check_pass\n"
            )
        f.write(
            f"{run_at}\t{cairo_today}\t{total_employees}\t{active_employees}\t"
            f"{inactive_employees}\t{total_contracts}\t{discovered_states}\t"
            f"{true_headcount}\t{dup_running_employees}\t{active_no_running}\t"
            f"{running_on_inactive}\t{limbo_incoming}\t{limbo_exited}\t"
            f"{limbo_no_contract}\t{returning_employees}\t{duplicate_record_names}\t"
            f"{expired_but_running}\t{'PASS' if hard_check_pass else 'FAIL'}\n"
        )
        if overflow_comment:
            f.write(overflow_comment)
    print(f"\n{_INFO} TSV row appended to {_LOG_FILE}")


# ── Main ───────────────────────────────────────────────────────────────────────

async def run() -> None:
    run_at      = datetime.now(timezone.utc).isoformat()
    cairo_today = datetime.now(CAIRO_TZ).date()

    print(_SEP)
    print("Employment Foundation Verification — Re-Discovery 2026-06-02")
    print(f"Run timestamp  : {run_at}")
    print(f"Cairo today    : {cairo_today}")
    print(f"RPCs planned   : 2  (hr.employee + hr.contract, both with active_test=False)")
    print(_SEP)
    _info("SCOPE: This is DISCOVERY. Most lines are [INFO] findings — not assertions.")
    _info("       Hard check (structural): no employee holds >1 running contract.")
    _info("       All counts are the CORRECTED employment baseline going forward.")

    # ── RPC calls ─────────────────────────────────────────────────────────────

    print(f"\n{_SEP2}")
    print("RPC CALLS")
    print(_SEP2)

    async with OdooClient() as client:

        _info(
            "RPC 1: search_read(hr.employee, [], "
            "['id','name','active'], context={'active_test': False})"
        )
        emp_records = await client.execute_kw(
            "hr.employee",
            "search_read",
            args=[[]],
            kwargs={
                "fields": ["id", "name", "active"],
                "context": {"active_test": False},
            },
        )
        _info(f"       → {len(emp_records)} employee records returned")

        _info(
            "RPC 2: search_read(hr.contract, [], "
            "['id','state','employee_id','date_end'], context={'active_test': False})"
        )
        contract_records = await client.execute_kw(
            "hr.contract",
            "search_read",
            args=[[]],
            kwargs={
                "fields": ["id", "state", "employee_id", "date_end"],
                "context": {"active_test": False},
            },
        )
        _info(f"       → {len(contract_records)} contract records returned")

    # ── Parse employees ────────────────────────────────────────────────────────

    active_emp_ids:   set[int] = {int(e["id"]) for e in emp_records if e.get("active") is True}
    inactive_emp_ids: set[int] = {int(e["id"]) for e in emp_records if e.get("active") is False}
    emp_name_by_id:   dict[int, str] = {int(e["id"]): e.get("name", "") for e in emp_records}

    # ── Parse contracts ────────────────────────────────────────────────────────

    # Empirical state key discovery
    state_counter: Counter = Counter(c.get("state", "MISSING") for c in contract_records)

    # All contracts indexed by employee_id (all states)
    contracts_by_emp_id: dict[int, list[dict]] = defaultdict(list)
    for c in contract_records:
        eid = _emp_id(c.get("employee_id"))
        if eid is not None:
            contracts_by_emp_id[eid].append(c)

    # Running contracts
    running_contracts = [c for c in contract_records if c.get("state") == _RUNNING_STATE]
    running_emp_id_list: list[int] = [
        eid
        for c in running_contracts
        if (eid := _emp_id(c.get("employee_id"))) is not None
    ]
    running_emp_id_counter = Counter(running_emp_id_list)
    running_emp_id_set:  set[int] = set(running_emp_id_list)

    # ── COMPUTATION 1 — Contract state distribution ────────────────────────────

    _section("COMPUTATION 1 — Contract state distribution (empirical)")
    _info(f"Total contracts (all states, incl. archived): {len(contract_records)}")
    _info("Discovered state keys (from Counter — no hardcoding):")
    label_map = {
        "open":   "Running",
        "close":  "Expired",
        "draft":  "New / incoming",
        "cancel": "Cancelled",
    }
    for state_key, count in sorted(state_counter.items(), key=lambda x: -x[1]):
        label = label_map.get(state_key, f"*** UNKNOWN KEY '{state_key}' — investigate ***")
        _info(f"  state='{state_key}'  ({label})  →  {count} contract(s)")

    unexpected_keys = set(state_counter.keys()) - _KNOWN_STATES
    if unexpected_keys:
        _fail(f"Unexpected state keys found — need investigation: {unexpected_keys}")
        _info("  The _EXIT_STATES and _INCOMING_STATE constants may need updating.")
    else:
        _info("All discovered state keys are from the known Odoo standard set.")

    # ── COMPUTATION 2 — True employed headcount ────────────────────────────────

    _section("COMPUTATION 2 — True employed headcount")

    true_headcount = len(running_emp_id_set)
    _info(f"Running (state='open') contracts total   : {len(running_contracts)}")
    _info(f"Distinct employee_ids in running contracts: {true_headcount}")
    _info(f"  ★ THIS IS THE REAL HEADCOUNT — employees currently employed at La Verde.")
    _info(f"  (prior 'active=True' headcount was a coincidentally equal but wrong population)")

    dup_running: list[int] = sorted(
        emp_id for emp_id, cnt in running_emp_id_counter.items() if cnt > 1
    )
    hard_check_pass = _check(
        "HARD CHECK: no employee holds >1 running contract",
        len(dup_running) == 0,
        detail=(
            f"{len(dup_running)} employee(s) with multiple running contracts: "
            f"{_fmt_ids(dup_running)}"
        ) if dup_running else "",
    )
    if dup_running:
        _info(f"  Employee IDs with >1 running: {_fmt_ids(dup_running)}")

    # ── COMPUTATION 3 — active vs running divergence ───────────────────────────

    _section("COMPUTATION 3 — hr.employee.active vs running-contract divergence")

    active_no_running_set: set[int] = active_emp_ids - running_emp_id_set
    running_on_inactive_set: set[int] = running_emp_id_set - active_emp_ids
    active_with_running_set: set[int] = active_emp_ids & running_emp_id_set

    active_no_running:   list[int] = sorted(active_no_running_set)
    running_on_inactive: list[int] = sorted(running_on_inactive_set)

    _info(f"active=True employee records                : {len(active_emp_ids)}")
    _info(f"Running contracts (distinct employee_ids)   : {true_headcount}")
    _info(f"Overlap (active=True AND running contract)  : {len(active_with_running_set)}")
    _info("")
    _info(f"active=True with NO running contract        : {len(active_no_running)}")
    if active_no_running:
        _info(f"  Employee IDs: {_fmt_ids(active_no_running)}")
        _info("  These are NOT currently employed by the contract definition.")
    _info("")
    _info(f"Running contract on active=False employee   : {len(running_on_inactive)}")
    if running_on_inactive:
        _info(f"  Employee IDs: {_fmt_ids(running_on_inactive)}")
        _info("  Orphan contracts — ex-employee paperwork debt, NOT payroll-active.")
    _info("")
    if len(active_emp_ids) != true_headcount:
        _info(
            f"active=True count ({len(active_emp_ids)}) ≠ true headcount ({true_headcount}) "
            f"[delta: {len(active_emp_ids) - true_headcount:+d}]"
        )
        _info("  CONFIRMED: these are different populations. active is NOT an employment signal.")
    else:
        _info(
            f"active=True count ({len(active_emp_ids)}) == true headcount ({true_headcount}) today."
        )
        _info(
            "  CAUTION: coincidental count equality — populations may still differ. "
            "  Check the no-running and orphan counts above."
        )

    # ── COMPUTATION 4 — Breakdown: active=True, no running contract ────────────

    _section("COMPUTATION 4 — Breakdown: active=True employees with NO running contract")

    limbo_incoming:    list[int] = []
    limbo_exited:      list[int] = []
    limbo_no_contract: list[int] = []
    limbo_unexpected:  list[tuple[int, set]] = []

    for eid in active_no_running:
        emp_contracts = contracts_by_emp_id.get(eid, [])
        if not emp_contracts:
            limbo_no_contract.append(eid)
            continue
        states = {c.get("state") for c in emp_contracts}
        if _INCOMING_STATE in states:
            # Has a draft (New) contract — onboarding not yet activated
            limbo_incoming.append(eid)
        elif states & _EXIT_STATES:
            # Has at least one exit-state contract and no draft — left the company
            limbo_exited.append(eid)
        else:
            # Has contracts but none match known states — unexpected
            limbo_unexpected.append((eid, states))
            limbo_exited.append(eid)  # conservative: treat as exited

    _info(f"Total active=True with no running contract: {len(active_no_running)}")
    _info(f"")
    _info(f"  ├── Has 'draft' (New/incoming) contract  : {len(limbo_incoming)}")
    if limbo_incoming:
        _info(f"  │   Employee IDs: {_fmt_ids(limbo_incoming)}")
        _info("  │   Hired but contract not yet activated. Pre-payroll (by-design forcing function).")
    _info(f"  │")
    _info(f"  ├── Has only Expired/Cancelled contract(s): {len(limbo_exited)}")
    if limbo_exited:
        _info(f"  │   Employee IDs: {_fmt_ids(limbo_exited)}")
        _info("  │   Left the company; employee record still flagged active=True (data-entry gap).")
        if limbo_unexpected:
            _info(f"  │   NOTE: {len(limbo_unexpected)} of the above have unexpected contract states:")
            for eid, states in limbo_unexpected:
                _info(f"  │     employee {eid} → states {states} (conservatively classified as exited)")
    _info(f"  │")
    _info(f"  └── NO contract record at all (data gap)  : {len(limbo_no_contract)}")
    if limbo_no_contract:
        _info(f"      Employee IDs: {_fmt_ids(limbo_no_contract)}")
        _info("      Employee record exists but no contract record was ever created.")

    # ── COMPUTATION 5 — Returning employees ───────────────────────────────────

    _section("COMPUTATION 5 — Returning employees (same-record rehires)")

    exit_states_in_data = _EXIT_STATES & set(state_counter.keys())
    emp_ids_with_exit: set[int] = {
        eid
        for c in contract_records
        if c.get("state") in exit_states_in_data
        and (eid := _emp_id(c.get("employee_id"))) is not None
    }
    returning_emp_ids: list[int] = sorted(running_emp_id_set & emp_ids_with_exit)

    _info("DEFINITION: employee_id appears in BOTH a running contract AND at least one")
    _info("  prior expired/cancelled contract on the SAME employee record.")
    _info("")
    _info("IMPORTANT CAVEAT: This only catches rehires who returned on the SAME record.")
    _info("  Rehires who received a NEW duplicate employee record (data-entry error) will NOT")
    _info("  appear here — their running contract is on a new ID, their old expired contract")
    _info("  is on the old ID, so the set intersection misses them. Those cases appear in")
    _info("  item 6 (name collision check), not here. Item 5 is NOT a complete rehire count.")
    _info("")
    _info(f"Exit states present in data: {exit_states_in_data}")
    _info(f"Employees with running + prior exit contract (same record): {len(returning_emp_ids)}")
    if returning_emp_ids:
        _info(f"  Employee IDs: {_fmt_ids(returning_emp_ids)}")
    else:
        _info("  None found — no same-record rehires detected.")

    # ── COMPUTATION 6 — Duplicate employee records by name ─────────────────────

    _section("COMPUTATION 6 — Potential duplicate employee records (name collision check)")

    _info("PII note: names used ONLY for this check. Output: name + IDs only. Nothing else.")
    _info("")

    name_bucket: dict[str, list[int]] = defaultdict(list)
    for e in emp_records:
        norm = e.get("name", "").strip().lower()
        if norm:
            name_bucket[norm].append(int(e["id"]))

    # Collect duplicates; retain original-case display name
    duplicates: dict[str, list[int]] = {}
    display_names: dict[str, str] = {}
    for norm_name, ids in name_bucket.items():
        if len(ids) > 1:
            duplicates[norm_name] = sorted(ids)
            display_names[norm_name] = next(
                (e.get("name", norm_name) for e in emp_records
                 if e.get("name", "").strip().lower() == norm_name),
                norm_name,
            )

    _info(f"Total employees checked (active + inactive): {len(emp_records)}")
    _info(f"Distinct names (case/whitespace-normalised) : {len(name_bucket)}")
    _info(f"Names with >1 employee record               : {len(duplicates)}")
    if duplicates:
        _info("  Candidates (manual review required — two unrelated people can share a name):")
        for norm_name in sorted(duplicates):
            _info(f"    '{display_names[norm_name]}'  →  IDs: {duplicates[norm_name]}")
    else:
        _info("  No name collisions found — no obvious duplicate records.")

    # ── COMPUTATION 7 — Open contracts with date_end < today (Cairo) ───────────

    _section("COMPUTATION 7 — Open contracts with date_end < today (expired-but-still-running)")

    _info(f"Reference date (Cairo today): {cairo_today}")
    _info("These contracts are state='open' but their date_end has already passed.")
    _info("Odoo's automatic state transition to 'close' did not fire.")
    _info("Impact: payroll-blocking bug — these employees' next payslip may fail.")
    _info("")

    expired_but_running_eids: list[int] = []
    parse_errors: list[tuple[int, object]] = []

    for c in running_contracts:
        raw_end = c.get("date_end")
        if not raw_end or raw_end is False:
            continue  # open-ended contract — not an error
        try:
            end_date = date.fromisoformat(str(raw_end))
            if end_date < cairo_today:
                eid = _emp_id(c.get("employee_id"))
                if eid is not None:
                    expired_but_running_eids.append(eid)
        except (ValueError, TypeError):
            parse_errors.append((int(c["id"]), raw_end))

    if parse_errors:
        _info(f"  date_end parse errors (contract_id, raw_value): {parse_errors}")

    if expired_but_running_eids:
        _fail(
            f"PAYROLL RISK: {len(expired_but_running_eids)} running contract(s) with "
            f"date_end < {cairo_today} (Odoo auto-flip not fired)"
        )
        _info(f"  Employee IDs: {_fmt_ids(sorted(expired_but_running_eids))}")
    else:
        _info(f"No running contracts with date_end < {cairo_today} — no expired-but-running cases.")

    # ── Final summary ──────────────────────────────────────────────────────────

    print(f"\n{_SEP}")
    print("CORRECTED EMPLOYMENT FOUNDATION — SUMMARY")
    print(_SEP2)
    print(f"  Run at (UTC)                         : {run_at}")
    print(f"  Cairo today                          : {cairo_today}")
    print(_SEP2)
    print(f"  Total employee records (all)         : {len(emp_records)}")
    print(f"    active=True                        : {len(active_emp_ids)}")
    print(f"    active=False (archived)            : {len(inactive_emp_ids)}")
    print(f"  Total contract records (all states)  : {len(contract_records)}")
    print(f"  State distribution                   : {dict(sorted(state_counter.items(), key=lambda x: -x[1]))}")
    print(_SEP2)
    print(f"  ★ TRUE HEADCOUNT (running contracts) : {true_headcount}")
    print(f"    active=True count (NOT headcount)  : {len(active_emp_ids)}")
    print(f"    delta (active − running)           : {len(active_emp_ids) - true_headcount:+d}")
    print(_SEP2)
    print(f"  active=True, no running contract     : {len(active_no_running)}")
    print(f"    ├ incoming (draft contract)        : {len(limbo_incoming)}")
    print(f"    ├ exited  (only close/cancel)      : {len(limbo_exited)}")
    print(f"    └ data gap (no contract at all)    : {len(limbo_no_contract)}")
    print(f"  Running contracts on inactive emp    : {len(running_on_inactive)}  (orphan / exit paperwork debt)")
    print(_SEP2)
    print(f"  Returning employees — item 5         : {len(returning_emp_ids)}  (same-record rehires only — see caveat)")
    print(f"  Duplicate name candidates — item 6   : {len(duplicates)}")
    print(f"  Expired-but-running — item 7         : {len(expired_but_running_eids)}")
    print(_SEP2)
    print(f"  Hard structural check (no dup running): {'[PASS]' if hard_check_pass else '[FAIL]'}")
    print(_SEP)

    # ── TSV log ────────────────────────────────────────────────────────────────

    overflow_lines: list[str] = []
    if len(active_no_running) > _MAX_IDS:
        overflow_lines.append(f"# item3 active_no_running IDs (full): {active_no_running}\n")
    if len(running_on_inactive) > _MAX_IDS:
        overflow_lines.append(f"# item3 running_on_inactive IDs (full): {running_on_inactive}\n")
    if len(limbo_incoming) > _MAX_IDS:
        overflow_lines.append(f"# item4 limbo_incoming IDs (full): {limbo_incoming}\n")
    if len(limbo_exited) > _MAX_IDS:
        overflow_lines.append(f"# item4 limbo_exited IDs (full): {limbo_exited}\n")
    if len(limbo_no_contract) > _MAX_IDS:
        overflow_lines.append(f"# item4 limbo_no_contract IDs (full): {limbo_no_contract}\n")
    if len(returning_emp_ids) > _MAX_IDS:
        overflow_lines.append(f"# item5 returning_emp_ids (full): {returning_emp_ids}\n")
    if duplicates:
        overflow_lines.append(f"# item6 duplicate_candidates (full): {dict(duplicates)}\n")
    if len(expired_but_running_eids) > _MAX_IDS:
        overflow_lines.append(
            f"# item7 expired_but_running IDs (full): {sorted(expired_but_running_eids)}\n"
        )

    _append_tsv(
        run_at=run_at,
        cairo_today=str(cairo_today),
        total_employees=len(emp_records),
        active_employees=len(active_emp_ids),
        inactive_employees=len(inactive_emp_ids),
        total_contracts=len(contract_records),
        discovered_states=str(dict(sorted(state_counter.items(), key=lambda x: -x[1]))),
        true_headcount=true_headcount,
        dup_running_employees=len(dup_running),
        active_no_running=len(active_no_running),
        running_on_inactive=len(running_on_inactive),
        limbo_incoming=len(limbo_incoming),
        limbo_exited=len(limbo_exited),
        limbo_no_contract=len(limbo_no_contract),
        returning_employees=len(returning_emp_ids),
        duplicate_record_names=len(duplicates),
        expired_but_running=len(expired_but_running_eids),
        hard_check_pass=hard_check_pass,
        overflow_comment="".join(overflow_lines),
    )


if __name__ == "__main__":
    asyncio.run(run())
