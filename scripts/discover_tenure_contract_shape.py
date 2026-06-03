"""
Read-only discovery: hr.contract shape for KPI B (Tenure) re-foundation.

Reveals how contract data is actually structured so net-accumulated-tenure
calculation can be designed on reality, not assumptions.

Six discovery items — all derived from ONE RPC:

  1. CONTRACTS PER EMPLOYEE — distribution: how many employees hold 1, 2, 3+
     contract records.
  2. DATE FIELD COMPLETENESS — null date_start / date_end overall and on
     Running contracts (the tenure-computation population).
  3. IN-PLACE RENEWAL PATTERN — among Running-contract employees: count with
     1 contract total (in-place renewal, §3.7 D5) vs 2+ (returning). For the
     single-contract group: per-year date_start distribution to detect any
     suspiciously recent recreations.
  REFINEMENT — Multi-contract employees with any null date_start or date_end:
     separate section listing employee_id + contract_id + which field is null.
  4. GAPS BETWEEN CONTRACTS — for multi-contract employees: concrete date
     ranges and gap_days (>0 real gap / 0 abut / <0 overlap / None null date).
  5. OVERLAPPING CONTRACTS — derived from item 4; negative gap_days.
  6. STATE COMBINATIONS — for multi-contract employees: state tuples with
     concrete employee_id examples.

One RPC only:
  search_read(hr.contract, [], ['id','employee_id','state','date_start',
              'date_end'], context={'active_test': False})

RPC 2 (hr.employee) NOT needed — employee_id is embedded in each contract
record as a many2one; all 6 items are computable from contract data alone.

One hard structural check (PASS/FAIL):
  All returned contract records have a valid employee_id (no silent drops).
Everything else is [INFO] discovery output.

Output:
  Structured console summary + TSV to logs/tenure_contract_shape_discovery.log
  Exit 0 always.

Pre-flight (Decision 6.4): kill python, purge __pycache__. No uvicorn.

Design authority:
  §3.6 — employment = Running contract, NOT hr.employee.active.
  §3.7 D2 — tenure = net accumulated service (sum of worked periods − gaps).
  §3.7 D4 — Resume tab unreliable; contracts are the only tenure source.
  §3.7 D5 — in-place renewal is the correct workflow; AI Engine adapts.
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

CAIRO_TZ  = ZoneInfo("Africa/Cairo")
_SEP      = "═" * 72
_SEP2     = "─" * 72
_LOG_FILE = "logs/tenure_contract_shape_discovery.log"
_MAX_IDS  = 20

_PASS = "[PASS]"
_FAIL = "[FAIL]"
_INFO = "[INFO]"

_RUNNING_STATE = "open"
_KNOWN_STATES  = frozenset({"open", "close", "draft", "cancel"})


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
    """Format an ID list; truncate with log-file reference if long."""
    if len(ids) <= _MAX_IDS:
        return str(ids)
    shown = ids[:_MAX_IDS]
    remaining = len(ids) - _MAX_IDS
    return f"{shown}  (... {remaining} more — see {log_ref})"


def _emp_id(raw: object) -> int | None:
    """Extract integer employee ID from Odoo many2one ([id, name] or scalar)."""
    if isinstance(raw, (list, tuple)) and raw:
        return int(raw[0])
    if raw and raw is not False:
        return int(raw)
    return None


def _parse_date(raw: object) -> date | None:
    """Parse Odoo date ('YYYY-MM-DD' string or False sentinel) → date or None."""
    if not raw or raw is False:
        return None
    try:
        return date.fromisoformat(str(raw))
    except (ValueError, TypeError):
        return None


# ── TSV log ────────────────────────────────────────────────────────────────────

def _append_tsv(
    run_at: str,
    cairo_today: str,
    total_contracts: int,
    total_emp_with_contracts: int,
    state_distribution: str,
    null_date_start_all: int,
    null_date_end_all: int,
    running_null_date_start: int,
    running_null_date_end: int,
    single_contract_running: int,
    multi_contract_running: int,
    multi_contract_employees: int,
    multi_null_date_contracts: int,
    gap_pairs_total: int,
    gap_pairs_positive: int,
    gap_pairs_zero: int,
    gap_pairs_overlap: int,
    gap_pairs_null: int,
    state_combo_summary: str,
    hard_check_pass: bool,
) -> None:
    os.makedirs("logs", exist_ok=True)
    log_exists = os.path.isfile(_LOG_FILE)
    with open(_LOG_FILE, "a", encoding="utf-8") as f:
        if not log_exists:
            f.write(
                "run_at\tcairo_today\ttotal_contracts\ttotal_emp_with_contracts\t"
                "state_distribution\tnull_date_start_all\tnull_date_end_all\t"
                "running_null_date_start\trunning_null_date_end\t"
                "single_contract_running\tmulti_contract_running\t"
                "multi_contract_employees\tmulti_null_date_contracts\t"
                "gap_pairs_total\tgap_pairs_positive\tgap_pairs_zero\t"
                "gap_pairs_overlap\tgap_pairs_null\tstate_combo_summary\t"
                "hard_check_pass\n"
            )
        f.write(
            f"{run_at}\t{cairo_today}\t{total_contracts}\t{total_emp_with_contracts}\t"
            f"{state_distribution}\t{null_date_start_all}\t{null_date_end_all}\t"
            f"{running_null_date_start}\t{running_null_date_end}\t"
            f"{single_contract_running}\t{multi_contract_running}\t"
            f"{multi_contract_employees}\t{multi_null_date_contracts}\t"
            f"{gap_pairs_total}\t{gap_pairs_positive}\t{gap_pairs_zero}\t"
            f"{gap_pairs_overlap}\t{gap_pairs_null}\t{state_combo_summary}\t"
            f"{'PASS' if hard_check_pass else 'FAIL'}\n"
        )
    print(f"\n{_INFO} TSV row appended to {_LOG_FILE}")


# ── Main ───────────────────────────────────────────────────────────────────────

async def run() -> None:
    run_at      = datetime.now(timezone.utc).isoformat()
    cairo_today = datetime.now(CAIRO_TZ).date()

    print(_SEP)
    print("Tenure Contract Shape Discovery — KPI B Re-Foundation Phase 1")
    print(f"Run timestamp  : {run_at}")
    print(f"Cairo today    : {cairo_today}")
    print(f"RPCs planned   : 1  (hr.contract, active_test=False)")
    print(_SEP)
    _info("SCOPE: Read-only discovery. All output is [INFO] findings.")
    _info("       One hard structural check: all contracts have a valid employee_id.")
    _info("       No writes. No PII beyond employee_id.")

    # ── RPC 1 ─────────────────────────────────────────────────────────────────

    print(f"\n{_SEP2}")
    print("RPC CALLS")
    print(_SEP2)

    async with OdooClient() as client:
        _info(
            "RPC 1: search_read(hr.contract, [], "
            "['id','employee_id','state','date_start','date_end'], "
            "context={'active_test': False})"
        )
        contract_records: list[dict] = await client.execute_kw(
            "hr.contract",
            "search_read",
            args=[[]],
            kwargs={
                "fields": ["id", "employee_id", "state", "date_start", "date_end"],
                "context": {"active_test": False},
            },
        )
    _info(f"       → {len(contract_records)} contract records returned")

    # ── Parse: group by employee_id ────────────────────────────────────────────

    contracts_by_emp: dict[int, list[dict]] = defaultdict(list)
    no_eid_contracts: list[int] = []

    for c in contract_records:
        eid = _emp_id(c.get("employee_id"))
        if eid is not None:
            contracts_by_emp[eid].append(c)
        else:
            no_eid_contracts.append(int(c["id"]))

    state_counter: Counter = Counter(c.get("state", "MISSING") for c in contract_records)
    running_contracts = [c for c in contract_records if c.get("state") == _RUNNING_STATE]
    running_emp_ids   = {
        eid for c in running_contracts
        if (eid := _emp_id(c.get("employee_id"))) is not None
    }
    multi_contract_emp_ids: list[int] = sorted(
        eid for eid, cs in contracts_by_emp.items() if len(cs) >= 2
    )

    # ── SANITY CHECK ──────────────────────────────────────────────────────────

    _section("SANITY CHECK — all returned contract records have a valid employee_id")

    hard_check_pass = _check(
        f"contracts with valid employee_id ({len(contract_records) - len(no_eid_contracts)}) "
        f"== records returned ({len(contract_records)})",
        len(no_eid_contracts) == 0,
        detail=(
            f"contract IDs with no employee_id: {no_eid_contracts}"
            if no_eid_contracts else ""
        ),
    )
    _info(f"Records returned     : {len(contract_records)}")
    _info(f"Records grouped      : {len(contract_records) - len(no_eid_contracts)}")
    _info(f"No-employee_id count : {len(no_eid_contracts)}")

    unexpected_keys = set(state_counter.keys()) - _KNOWN_STATES
    if unexpected_keys:
        _fail(f"Unexpected state keys discovered — need investigation: {unexpected_keys}")
    else:
        _info("All discovered state keys are Odoo standard schema keys.")

    # ── COMPUTATION 1 — Contracts per employee distribution ───────────────────

    _section("COMPUTATION 1 — Contracts per employee (distribution)")

    total_emp_with_contracts = len(contracts_by_emp)
    count_dist: Counter = Counter(len(v) for v in contracts_by_emp.values())

    _info(f"Total contract records (all states)   : {len(contract_records)}")
    _info(f"Employees with ≥1 contract record     : {total_emp_with_contracts}")
    _info(
        f"State distribution (empirical)        : "
        f"{dict(sorted(state_counter.items(), key=lambda x: -x[1]))}"
    )
    _info("")
    _info("Contracts-per-employee distribution:")
    for n_contracts in sorted(count_dist.keys()):
        label = (
            "in-place renewal candidate"
            if n_contracts == 1 else
            "returning employee candidate"
        )
        _info(
            f"  {n_contracts} contract(s)  →  "
            f"{count_dist[n_contracts]:>4} employee(s)  ({label})"
        )

    # ── COMPUTATION 2 — Date field completeness ───────────────────────────────

    _section("COMPUTATION 2 — Date field completeness")

    null_start_all     = [c for c in contract_records if not c.get("date_start")]
    null_end_all       = [c for c in contract_records if not c.get("date_end")]
    running_null_start = [c for c in running_contracts if not c.get("date_start")]
    running_null_end   = [c for c in running_contracts if not c.get("date_end")]

    _info(f"All contracts ({len(contract_records)} total):")
    _info(f"  null date_start : {len(null_start_all)}")
    _info(f"  null date_end   : {len(null_end_all)}")
    _info("")
    _info(f"Running contracts ({len(running_contracts)} total — the tenure-computation population):")
    _info(
        f"  null date_start : {len(running_null_start)}"
        + (
            "  ⚠ COMPUTATION BLOCKER — tenure uncomputable for these"
            if running_null_start else
            "  (none — all Running contracts have date_start)"
        )
    )
    _info(
        f"  null date_end   : {len(running_null_end)}"
        + (
            "  (open-ended contract — expected; §3.4.3 documents 1 known case)"
            if running_null_end else
            "  (none)"
        )
    )
    if running_null_start:
        rnull_s_eids = sorted(
            eid for c in running_null_start
            if (eid := _emp_id(c.get("employee_id"))) is not None
        )
        _info(f"  Running null-start employee_ids : {_fmt_ids(rnull_s_eids)}")
    if running_null_end:
        rnull_e_eids = sorted(
            eid for c in running_null_end
            if (eid := _emp_id(c.get("employee_id"))) is not None
        )
        _info(f"  Running null-end employee_ids   : {_fmt_ids(rnull_e_eids)}")

    # ── COMPUTATION 3 — In-place renewal pattern ──────────────────────────────

    _section("COMPUTATION 3 — In-place renewal pattern (Running employees)")

    single_running_eids: list[int] = []
    multi_running_eids:  list[int] = []

    for eid in running_emp_ids:
        if len(contracts_by_emp[eid]) == 1:
            single_running_eids.append(eid)
        else:
            multi_running_eids.append(eid)

    _info(f"Running-contract employees (true headcount basis): {len(running_emp_ids)}")
    _info(f"  ├── 1 contract total (in-place renewal)       : {len(single_running_eids)}")
    _info(f"  └── 2+ contracts total (returning employee)   : {len(multi_running_eids)}")
    if multi_running_eids:
        _info(f"      IDs: {_fmt_ids(sorted(multi_running_eids))}")
    _info("")
    _info("date_start year distribution — single-contract Running employees:")
    _info("  (A suspiciously recent year for a long-tenured employee would suggest")
    _info("   the contract was recreated rather than updated in-place — §3.7 D5 not universal.)")
    _info("")

    year_counter: Counter = Counter()
    no_start_single = 0

    for eid in single_running_eids:
        ds = _parse_date(contracts_by_emp[eid][0].get("date_start"))
        if ds is not None:
            year_counter[ds.year] += 1
        else:
            no_start_single += 1

    for year in sorted(year_counter.keys()):
        _info(f"  {year}  →  {year_counter[year]:>4} employee(s)")
    if no_start_single:
        _info(
            f"  null  →  {no_start_single:>4} employee(s)"
            "  ⚠ date_start missing — tenure uncomputable"
        )

    valid_starts = sorted(
        ds for eid in single_running_eids
        if (ds := _parse_date(contracts_by_emp[eid][0].get("date_start"))) is not None
    )
    if valid_starts:
        _info("")
        _info(f"  date_start range : {valid_starts[0]}  →  {valid_starts[-1]}")
        _info(
            f"  with valid date_start: {len(valid_starts)} / {len(single_running_eids)}"
        )

    # ── REFINEMENT 1 — Multi-contract employees with null dates ───────────────

    _section(
        "REFINEMENT 1 — Multi-contract employees with null date_start or date_end"
    )
    _info("These are the cases where tenure period-summing will fail or give wrong results.")
    _info("Listed per contract: employee_id | contract_id | state | null fields")
    _info("")

    multi_null_rows: list[dict] = []
    for eid in multi_contract_emp_ids:
        for c in contracts_by_emp[eid]:
            null_start = not c.get("date_start")
            null_end   = not c.get("date_end")
            if null_start or null_end:
                multi_null_rows.append({
                    "eid":        eid,
                    "cid":        c["id"],
                    "state":      c.get("state", "?"),
                    "date_start": c.get("date_start"),
                    "date_end":   c.get("date_end"),
                    "null_fields": (
                        ["date_start", "date_end"] if (null_start and null_end) else
                        ["date_start"] if null_start else
                        ["date_end"]
                    ),
                })

    if not multi_null_rows:
        _info("  None found — all multi-contract employees have complete date fields.")
    else:
        _info(
            f"  {len(multi_null_rows)} contract(s) with null dates "
            "on multi-contract employees:"
        )
        _info("")
        for row in multi_null_rows:
            _info(
                f"  emp_id={row['eid']:<6}  contract_id={row['cid']:<6}  "
                f"state='{row['state']}'  "
                f"date_start={str(row['date_start']):<12}  "
                f"date_end={str(row['date_end']):<12}  "
                f"NULL: {', '.join(row['null_fields'])}"
            )

    # ── COMPUTATION 4 — Gaps between contracts ────────────────────────────────

    _section(
        "COMPUTATION 4 — Gaps between consecutive contracts (multi-contract employees)"
    )
    _info("gap_days > 0  = real gap (person was absent from La Verde; SUBTRACT from tenure)")
    _info("gap_days = 0  = contracts abut exactly (no gap)")
    _info("gap_days < 0  = contracts OVERLAP (see COMPUTATION 5)")
    _info("gap_days = None = a date is null — uncomputable (see REFINEMENT 1)")
    _info("")

    gap_rows: list[dict] = []

    for eid in multi_contract_emp_ids:
        sorted_cs = sorted(
            contracts_by_emp[eid],
            key=lambda c: (_parse_date(c.get("date_start")) or date.max),
        )
        for i in range(len(sorted_cs) - 1):
            a = sorted_cs[i]
            b = sorted_cs[i + 1]
            a_end   = _parse_date(a.get("date_end"))
            b_start = _parse_date(b.get("date_start"))
            gap_days = (b_start - a_end).days if (a_end and b_start) else None
            gap_rows.append({
                "eid":     eid,
                "a_id":    a["id"],
                "a_state": a.get("state", "?"),
                "a_start": a.get("date_start"),
                "a_end":   a.get("date_end"),
                "b_id":    b["id"],
                "b_state": b.get("state", "?"),
                "b_start": b.get("date_start"),
                "b_end":   b.get("date_end"),
                "gap_days": gap_days,
            })

    if not gap_rows:
        _info("  No multi-contract employees found — no gaps to analyze.")
        _info("")
        _info("  FINDING: Zero multi-contract employees means the gap-subtraction logic")
        _info("  is a correctness safeguard for a currently-absent edge case. It must")
        _info("  still be built correctly — returning employees may appear in future data.")
    else:
        _info(f"  {len(gap_rows)} consecutive contract pair(s):")
        _info("")
        for row in gap_rows:
            if row["gap_days"] is None:
                gap_label = "None  (null date — uncomputable)"
            elif row["gap_days"] > 0:
                gap_label = f"{row['gap_days']:>6} days  (REAL GAP — subtract from tenure)"
            elif row["gap_days"] == 0:
                gap_label = "     0 days  (ABUT — no gap)"
            else:
                gap_label = f"{row['gap_days']:>6} days  (OVERLAP ⚠ — see COMPUTATION 5)"
            _info(
                f"  emp_id={row['eid']:<6} | "
                f"A: id={row['a_id']:<6} state='{row['a_state']}'  "
                f"[{row['a_start']} -> {row['a_end']}]  ->  "
                f"B: id={row['b_id']:<6} state='{row['b_state']}'  "
                f"[{row['b_start']} -> {row['b_end']}]  "
                f"| gap={gap_label}"
            )

    # ── COMPUTATION 5 — Overlapping contracts ─────────────────────────────────

    _section("COMPUTATION 5 — Overlapping contracts (gap_days < 0)")

    overlaps = [r for r in gap_rows if r["gap_days"] is not None and r["gap_days"] < 0]

    if not overlaps:
        _info("  No overlapping contract pairs.")
        if gap_rows:
            _info(
                "  Period-summing can proceed without overlap-clamping for current data."
            )
            _info(
                "  NOTE: Overlap-clamping guard should still be implemented — "
                "data may change."
            )
        else:
            _info("  (No multi-contract employees to check.)")
    else:
        _info(
            f"  ⚠ {len(overlaps)} overlapping pair(s) — "
            "naive period-summing would double-count:"
        )
        _info("")
        for row in overlaps:
            overlap_days = abs(row["gap_days"])
            _info(
                f"  emp_id={row['eid']:<6} | "
                f"A: id={row['a_id']:<6} [{row['a_start']} -> {row['a_end']}]  "
                f"B: id={row['b_id']:<6} [{row['b_start']} -> {row['b_end']}]  "
                f"overlap={overlap_days} day(s)"
            )
        _info("")
        _info("  Correct approach: b_effective_start = max(b_start, a_end)")

    # ── COMPUTATION 6 — State combinations on multi-contract employees ─────────

    _section("COMPUTATION 6 — State combinations (multi-contract employees)")

    state_combo_counter:  Counter = Counter()
    state_combo_examples: dict[tuple, list[int]] = defaultdict(list)
    label_map = {
        "open":   "Running",
        "close":  "Expired",
        "draft":  "New",
        "cancel": "Cancelled",
    }

    for eid in multi_contract_emp_ids:
        combo = tuple(sorted(c.get("state", "MISSING") for c in contracts_by_emp[eid]))
        state_combo_counter[combo] += 1
        state_combo_examples[combo].append(eid)

    if not state_combo_counter:
        _info("  No multi-contract employees — no state combinations to report.")
    else:
        _info(f"  Employees with 2+ contract records: {len(multi_contract_emp_ids)}")
        _info("")
        for combo, count in sorted(state_combo_counter.items(), key=lambda x: -x[1]):
            readable = " + ".join(label_map.get(s, f"UNKNOWN({s})") for s in combo)
            examples  = state_combo_examples[combo]
            _info(
                f"  {str(combo):<30}  ({readable})  ->  {count} employee(s)  "
                f"IDs: {_fmt_ids(examples)}"
            )

    # ── Final summary ──────────────────────────────────────────────────────────

    gap_pos  = sum(1 for r in gap_rows if r["gap_days"] is not None and r["gap_days"] > 0)
    gap_zero = sum(1 for r in gap_rows if r["gap_days"] == 0)
    gap_null = sum(1 for r in gap_rows if r["gap_days"] is None)

    print(f"\n{_SEP}")
    print("CONTRACT SHAPE DISCOVERY — SUMMARY")
    print(_SEP2)
    print(f"  Run at (UTC)                              : {run_at}")
    print(f"  Cairo today                               : {cairo_today}")
    print(_SEP2)
    print(f"  Total contract records                    : {len(contract_records)}")
    print(
        f"  State distribution                        : "
        f"{dict(sorted(state_counter.items(), key=lambda x: -x[1]))}"
    )
    print(f"  Employees with >=1 contract               : {total_emp_with_contracts}")
    print(_SEP2)
    print(f"  ITEM 1 — Contracts-per-employee distribution:")
    for n in sorted(count_dist.keys()):
        print(f"    {n} contract(s)  ->  {count_dist[n]} employee(s)")
    print(_SEP2)
    print(f"  ITEM 2 — Null dates:")
    print(
        f"    null date_start (all / Running)         : "
        f"{len(null_start_all)} / {len(running_null_start)}"
    )
    print(
        f"    null date_end   (all / Running)         : "
        f"{len(null_end_all)} / {len(running_null_end)}"
    )
    print(_SEP2)
    print(f"  ITEM 3 — Running emp contract count:")
    print(
        f"    single-contract (in-place renewal)      : {len(single_running_eids)}"
    )
    print(
        f"    multi-contract  (returning employee)    : {len(multi_running_eids)}"
    )
    print(_SEP2)
    print(
        f"  REFINEMENT 1 — Multi-contract null dates  : "
        f"{len(multi_null_rows)} contract(s) affected"
    )
    print(_SEP2)
    print(f"  ITEM 4 — Gap pairs (multi-contract employees):")
    print(f"    total pairs analyzed                    : {len(gap_rows)}")
    print(f"    positive gaps (real absence)            : {gap_pos}")
    print(f"    abut (gap = 0)                          : {gap_zero}")
    print(f"    overlaps (gap < 0)                      : {len(overlaps)}")
    print(f"    null-date (uncomputable)                : {gap_null}")
    print(_SEP2)
    print(f"  ITEM 5 — Overlapping contract pairs       : {len(overlaps)}")
    print(f"  ITEM 6 — Distinct state combos            : {len(state_combo_counter)}")
    print(_SEP2)
    print(
        f"  Hard structural check                     : "
        f"{'[PASS]' if hard_check_pass else '[FAIL]'}"
    )
    print(_SEP)

    # ── TSV log ────────────────────────────────────────────────────────────────

    _append_tsv(
        run_at=run_at,
        cairo_today=str(cairo_today),
        total_contracts=len(contract_records),
        total_emp_with_contracts=total_emp_with_contracts,
        state_distribution=str(
            dict(sorted(state_counter.items(), key=lambda x: -x[1]))
        ),
        null_date_start_all=len(null_start_all),
        null_date_end_all=len(null_end_all),
        running_null_date_start=len(running_null_start),
        running_null_date_end=len(running_null_end),
        single_contract_running=len(single_running_eids),
        multi_contract_running=len(multi_running_eids),
        multi_contract_employees=len(multi_contract_emp_ids),
        multi_null_date_contracts=len(multi_null_rows),
        gap_pairs_total=len(gap_rows),
        gap_pairs_positive=gap_pos,
        gap_pairs_zero=gap_zero,
        gap_pairs_overlap=len(overlaps),
        gap_pairs_null=gap_null,
        state_combo_summary=str(dict(sorted(
            ((str(k), v) for k, v in state_combo_counter.items()),
            key=lambda x: -x[1],
        ))),
        hard_check_pass=hard_check_pass,
    )


if __name__ == "__main__":
    asyncio.run(run())
