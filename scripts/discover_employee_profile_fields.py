"""
Complementary F3 employee profile field discovery.

Fills gaps left by discover_employee_profile_shape.py for HR F3 design:

  SECTION 1 — FULL-115 PROFILE FIELD POPULATION COUNTS
    Current populated-vs-empty counts across all 115 Running-contract
    employees for all key F3 candidate fields (including gender,
    allowance_count, vehicle — absent from AREA 3 of the prior script).

  SECTION 2 — 5-EMPLOYEE SAMPLE FIELD-BY-FIELD ASSESSMENT
    One employee from each of 5 departments: Finance, Sales 2, HR,
    Fleet, Admin/Services. Per-employee POPULATED/EMPTY + schema-label
    values. PII char fields and gender: POPULATED/EMPTY only.

  SECTION 3 — CONTRACT HISTORY DEPTH (5-employee sample)
    Total hr.contract records (all states) per sample employee.
    Confirms §3.6.C: in-place renewal → one record per employee.

  SECTION 4 — DATE TRUST: date_end DISTRIBUTION (all 115)
    Distinct date_end values with counts across all Running contracts.
    Verdict: can per-employee contract-expiry be shown, caveated, or
    suppressed on F3?

  SECTION 5 — allowance_count SANITY CHECK
    Confirm type + label: integer '# Assets' (F3 candidate) or
    monetary (exclude). Distribution over 115.

  SECTION 6 — MANAGER LINE CHECK (parent_id)
    For sample employees: parent_id display name from search_read
    many2one result (no extra RPC). F3 viability verdict.

Privacy invariants (structural):
  - wage field never read.
  - PII char fields (work_email, work_phone, mobile_phone, vehicle):
    POPULATED/EMPTY only — value never printed.
  - gender: aggregate distribution over 115 only. Per-sample output
    shows POPULATED/EMPTY — never links value to a named employee.
  - identification_id / barcode: NOT probed (PII, no board purpose).

Read-only: fields_get / search_read only. 4 RPCs total.

Pre-flight (Decision 6.4): purge __pycache__, restart without --reload.
Usage: python scripts/discover_employee_profile_fields.py
"""

import asyncio
import io
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from backend.shared.odoo.client import OdooClient  # noqa: E402

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

CAIRO_TZ          = ZoneInfo("Africa/Cairo")
_SEP              = "═" * 72
_SEP2             = "─" * 72
_EXPECTED_RUNNING = 115
_INFO             = "[INFO]"
_PASS             = "[PASS]"
_FAIL             = "[FAIL]"
_WARN             = "[WARN]"

# Profile fields to read from hr.employee.
# identification_id / barcode deliberately excluded (PII, no board use).
_PROFILE_FIELDS: list[str] = [
    "name",
    "job_id",
    "job_title",
    "department_id",
    "parent_id",
    "coach_id",
    "work_email",
    "work_phone",
    "mobile_phone",
    "employee_type",
    "gender",
    "work_location_id",
    "allowance_count",
    "vehicle",
    "first_contract_date",
]

# char/text fields — print POPULATED/EMPTY only (never the actual value)
_CHAR_REDACT = frozenset({"work_email", "work_phone", "mobile_phone", "vehicle"})

# Target departments for the 5-employee sample (substring match on full path)
_TARGET_DEPT_SPECS: list[tuple[str, list[str]]] = [
    ("Finance",        ["Finance"]),
    ("Sales 2",        ["Sales 2"]),
    ("HR",             ["/ HR"]),
    ("Fleet",          ["Fleet"]),
    ("Admin/Services", ["Administration / Services", "Administration"]),
]

_STATE_LABELS = {
    "open":   "Running",
    "close":  "Expired",
    "draft":  "New",
    "cancel": "Cancelled",
}

FIELD_DESC: dict[str, str] = {
    "name":               "Display name",
    "job_id":             "Job position (many2one → hr.job)",
    "job_title":          "Job title freetext (char)",
    "department_id":      "Department (many2one)",
    "parent_id":          "Manager / Reports To (many2one)",
    "coach_id":           "Coach / Mentor (many2one)",
    "work_email":         "Work email [value redacted]",
    "work_phone":         "Work phone [value redacted]",
    "mobile_phone":       "Mobile phone [value redacted]",
    "employee_type":      "Employee type (selection)",
    "gender":             "Gender [aggregate only — not on F3 profile]",
    "work_location_id":   "Work location (many2one)",
    "allowance_count":    "# Assets integer count (see Sec 5)",
    "vehicle":            "Company Vehicle (char) [value redacted]",
    "first_contract_date": "First contract date (known: 11 nulls)",
}


def _sec(title: str) -> None:
    print(f"\n{_SEP}")
    print(title)
    print(_SEP2)


def _populated(v: object) -> bool:
    if v is False or v is None:
        return False
    if isinstance(v, str) and not v.strip():
        return False
    if isinstance(v, (list, tuple)) and len(v) == 0:
        return False
    return True


def _m2o_name(raw: object) -> str | None:
    """Return display name from Odoo many2one [id, name] pair, or None."""
    if isinstance(raw, (list, tuple)) and len(raw) > 1:
        name = raw[1]
        if name and name is not False:
            return str(name)
    return None


def _emp_id(raw: object) -> int | None:
    if isinstance(raw, (list, tuple)) and raw and raw[0] is not False:
        try:
            return int(raw[0])
        except (ValueError, TypeError):
            return None
    return None


async def run() -> None:
    run_at      = datetime.now(timezone.utc).isoformat()
    cairo_today = datetime.now(CAIRO_TZ).date()

    print(_SEP)
    print("F3 Employee Profile Field Discovery")
    print("Script: scripts/discover_employee_profile_fields.py")
    print(f"Run timestamp : {run_at}")
    print(f"Cairo today   : {cairo_today}")
    print(f"RPCs planned  : 4")
    print(_SEP)
    print(f"{_INFO} SCOPE     : READ-ONLY. fields_get / search_read only.")
    print(f"{_INFO} PRIVACY   : No wage field touched.")
    print(f"{_INFO}             PII char fields (email/phone/vehicle): POPULATED/EMPTY only.")
    print(f"{_INFO}             gender: aggregate over 115 only — never per-named-employee.")
    print(f"{_INFO}             identification_id / barcode: NOT probed (PII, excluded).")
    print(f"{_INFO} BASELINE  : {_EXPECTED_RUNNING} Running-contract employees expected (post Dev-fix 2026-06-03).")

    # ── All RPCs inside a single OdooClient context ────────────────────────────
    async with OdooClient() as client:

        # ── RPC 1: hr.employee schema ─────────────────────────────────────────
        _sec("RPC 1 — fields_get(hr.employee)")
        emp_schema: dict = await client.execute_kw(
            "hr.employee",
            "fields_get",
            args=[],
            kwargs={"attributes": ["string", "type", "relation", "selection"]},
        )
        print(f"  → {len(emp_schema)} field definitions")

        # allowance_count type/label (used in Section 5)
        ac_meta  = emp_schema.get("allowance_count", {})
        ac_type  = ac_meta.get("type",   "NOT FOUND")
        ac_label = ac_meta.get("string", "NOT FOUND")
        print(f"\n  allowance_count: type='{ac_type}', label='{ac_label}'")

        # Confirm profile fields exist in schema
        missing_from_schema = [f for f in _PROFILE_FIELDS if f not in emp_schema]
        if missing_from_schema:
            print(f"  {_WARN} Fields absent from hr.employee schema: {missing_from_schema}")
        else:
            print(f"  {_PASS} All {len(_PROFILE_FIELDS)} profile candidate fields present in schema.")

        # Print selection options for transparency
        for sel_field in ("employee_type", "gender"):
            opts = emp_schema.get(sel_field, {}).get("selection") or []
            print(f"  {sel_field} selection options: {opts}")

        # ── RPC 2: All 115 Running contracts ─────────────────────────────────
        _sec("RPC 2 — search_read(hr.contract, state=open) — date_end + sample selection")
        running_contracts: list[dict] = await client.execute_kw(
            "hr.contract",
            "search_read",
            args=[[("state", "=", "open")]],
            kwargs={
                "fields": ["id", "employee_id", "department_id", "date_start", "date_end"],
                "context": {"active_test": False},
            },
        )
        print(f"  → {len(running_contracts)} Running contracts")

        # Deduplicate employee IDs preserving first-seen order
        seen_eids: set[int]          = set()
        all_115_emp_ids: list[int]   = []
        emp_to_contract: dict[int, dict] = {}
        for c in running_contracts:
            eid = _emp_id(c.get("employee_id"))
            if eid is not None and eid not in seen_eids:
                seen_eids.add(eid)
                all_115_emp_ids.append(eid)
                emp_to_contract[eid] = c

        n_running = len(all_115_emp_ids)
        if n_running == _EXPECTED_RUNNING:
            print(f"  {_PASS} Distinct Running-contract employees == {_EXPECTED_RUNNING}")
        else:
            print(f"  {_FAIL} Expected {_EXPECTED_RUNNING}, got {n_running}")

        # Sample selection — first employee per target department (no extra RPC)
        dept_to_first: dict[str, int] = {}   # dept_name → first eid seen
        for c in running_contracts:
            eid = _emp_id(c.get("employee_id"))
            if eid is None:
                continue
            dname = _m2o_name(c.get("department_id")) or "(no dept)"
            if dname not in dept_to_first:
                dept_to_first[dname] = eid

        sample_emp_ids:    list[int] = []
        sample_dept_labels: list[str] = []
        for target_label, keywords in _TARGET_DEPT_SPECS:
            matched = False
            for dname, eid in dept_to_first.items():
                if any(kw in dname for kw in keywords) and eid not in sample_emp_ids:
                    sample_emp_ids.append(eid)
                    sample_dept_labels.append(dname)
                    matched = True
                    break
            if not matched:
                print(f"  {_WARN} No match for target dept '{target_label}'")

        print(f"\n  Sample employees selected ({len(sample_emp_ids)}):")
        for eid, dlabel in zip(sample_emp_ids, sample_dept_labels):
            print(f"    Employee ID {eid:>5}  |  {dlabel}")

        # ── RPC 3: Full-115 hr.employee profile fields ────────────────────────
        _sec("RPC 3 — search_read(hr.employee, id in 115 ids) — profile fields")
        read_fields = [f for f in _PROFILE_FIELDS if f in emp_schema]
        print(f"  Fields requested: {read_fields}")
        employee_records: list[dict] = await client.execute_kw(
            "hr.employee",
            "search_read",
            args=[[("id", "in", all_115_emp_ids)]],
            kwargs={"fields": read_fields, "context": {"active_test": False}},
        )
        print(f"  → {len(employee_records)} employee records")

        # ── RPC 4: All contracts for 5 sample employees (all states) ─────────
        _sec("RPC 4 — search_read(hr.contract, employee_id in sample) — all states")
        print(f"  Querying all hr.contract records for employee IDs: {sample_emp_ids}")
        sample_contracts: list[dict] = await client.execute_kw(
            "hr.contract",
            "search_read",
            args=[[("employee_id", "in", sample_emp_ids)]],
            kwargs={
                "fields": ["employee_id", "state", "date_start", "date_end"],
                "context": {"active_test": False},
            },
        )
        print(f"  → {len(sample_contracts)} contract records across {len(sample_emp_ids)} employees")

    # ── All RPCs complete. Pure analysis below. ────────────────────────────────

    n = len(employee_records)
    emp_record_map: dict[int, dict] = {r["id"]: r for r in employee_records}

    sample_contracts_by_emp: dict[int, list[dict]] = defaultdict(list)
    for c in sample_contracts:
        eid = _emp_id(c.get("employee_id"))
        if eid is not None:
            sample_contracts_by_emp[eid].append(c)

    def _pop_count(fname: str) -> int:
        return sum(1 for r in employee_records if _populated(r.get(fname)))

    # ══════════════════════════════════════════════════════════════════════════
    _sec("SECTION 1 — FULL-115 PROFILE FIELD POPULATION COUNTS")

    W1, W2, W3 = 24, 9, 6
    print(f"  {'field':<{W1}} {'pop':>{W2}} {'%':>{W3}}  description")
    print(f"  {'─'*W1} {'─'*W2} {'─'*W3}  {'─'*44}")

    for fname in _PROFILE_FIELDS:
        if fname not in emp_schema:
            print(f"  {fname:<{W1}} {'NOT IN SCHEMA':>{W2}}")
            continue
        pop = _pop_count(fname)
        pct = round(100 * pop / n) if n else 0
        desc = FIELD_DESC.get(fname, "")
        print(f"  {fname:<{W1}} {f'{pop}/{n}':>{W2}} {f'{pct}%':>{W3}}  {desc}")

    # gender — aggregate distribution (never linked to individual employee)
    gender_meta = emp_schema.get("gender", {})
    gender_opts = dict(gender_meta.get("selection") or [])
    gender_ctr  = Counter(r.get("gender") for r in employee_records)
    print(f"\n  gender distribution (aggregate — all {n} employees):")
    for key, cnt in gender_ctr.most_common():
        lbl = gender_opts.get(key, str(key))
        print(f"    '{lbl}' (key={key!r}): {cnt}/{n}")

    # employee_type distribution
    et_meta = emp_schema.get("employee_type", {})
    et_opts = dict(et_meta.get("selection") or [])
    et_ctr  = Counter(r.get("employee_type") for r in employee_records)
    print(f"\n  employee_type distribution (all {n}):")
    for key, cnt in et_ctr.most_common():
        lbl = et_opts.get(key, str(key))
        print(f"    '{lbl}' (key={key!r}): {cnt}/{n}")

    # work_location distinct values
    loc_ctr: Counter = Counter()
    for r in employee_records:
        name = _m2o_name(r.get("work_location_id"))
        loc_ctr[name if name else "(empty)"] += 1
    print(f"\n  work_location_id distinct values (all {n}):")
    for lname, cnt in loc_ctr.most_common():
        print(f"    '{lname}': {cnt}")

    # allowance_count distribution
    ac_ctr: Counter = Counter(
        int(r.get("allowance_count") or 0) for r in employee_records
    )
    print(f"\n  allowance_count distribution (all {n}):")
    for val in sorted(ac_ctr.keys()):
        print(f"    count={val}: {ac_ctr[val]} employee(s)")

    # ══════════════════════════════════════════════════════════════════════════
    _sec("SECTION 2 — 5-EMPLOYEE SAMPLE FIELD-BY-FIELD ASSESSMENT")
    print("  Employees identified by ID + department only (names not printed).")
    print("  PII char fields: POPULATED/EMPTY only.  gender: POPULATED/EMPTY only per sample.")

    for eid, dept_label in zip(sample_emp_ids, sample_dept_labels):
        rec = emp_record_map.get(eid)
        if rec is None:
            print(f"\n  Employee ID {eid} ({dept_label}): {_WARN} NOT IN EMPLOYEE RECORDS")
            continue

        print(f"\n  ── Employee ID {eid:>5} | Dept: {dept_label} ──")

        for fname in _PROFILE_FIELDS:
            if fname not in emp_schema:
                continue
            val   = rec.get(fname)
            ftype = emp_schema[fname].get("type", "")

            if fname in _CHAR_REDACT:
                display = "POPULATED" if _populated(val) else "EMPTY"
            elif fname == "gender":
                # Never link gender value to a named/identified employee
                display = "POPULATED" if _populated(val) else "EMPTY"
            elif fname == "name":
                display = "POPULATED" if _populated(val) else "EMPTY"
            elif ftype == "many2one":
                name = _m2o_name(val)
                if name:
                    m2o_id = val[0] if isinstance(val, (list, tuple)) else "?"
                    display = f"[{m2o_id}] '{name}'"
                else:
                    display = "EMPTY"
            elif ftype == "selection":
                if not _populated(val):
                    display = "EMPTY"
                else:
                    opts = dict(emp_schema[fname].get("selection") or [])
                    display = f"'{opts.get(val, val)}' (key={val!r})"
            elif ftype == "integer":
                display = str(val) if val is not None else "EMPTY"
            else:
                display = str(val) if _populated(val) else "EMPTY"

            print(f"    {fname:<24}: {display}")

        # Contract dates from RPC 2 (not re-fetched)
        c = emp_to_contract.get(eid, {})
        print(f"    {'date_start (contract)':<24}: {c.get('date_start', '?')}")
        print(f"    {'date_end   (contract)':<24}: {c.get('date_end', False)}")

    # ══════════════════════════════════════════════════════════════════════════
    _sec("SECTION 3 — CONTRACT HISTORY DEPTH (5-employee sample)")
    print("  Confirms §3.6.C: in-place renewal → one record per continuing employee.")
    print("  Any employee with >1 record has historical closed/cancelled contracts.")

    for eid, dept_label in zip(sample_emp_ids, sample_dept_labels):
        contracts = sample_contracts_by_emp.get(eid, [])
        by_state  = Counter(c.get("state") for c in contracts)
        print(f"\n  Employee ID {eid} ({dept_label}): {len(contracts)} contract record(s) total")
        for state_key, cnt in sorted(by_state.items()):
            slabel = _STATE_LABELS.get(state_key, state_key)
            print(f"    State '{slabel}' ({state_key}): {cnt}")
        for c in sorted(contracts, key=lambda x: x.get("date_start") or ""):
            slabel = _STATE_LABELS.get(c.get("state"), c.get("state", "?"))
            de = c.get("date_end", False)
            print(
                f"    record: state={slabel:<10}"
                f"  date_start={c.get('date_start', '?')}"
                f"  date_end={de}"
            )

    # ══════════════════════════════════════════════════════════════════════════
    _sec("SECTION 4 — DATE TRUST: date_end DISTRIBUTION (all 115 Running contracts)")

    date_end_ctr: Counter = Counter()
    for c in running_contracts:
        de = c.get("date_end")
        key = str(de) if de and de is not False else "False (open-ended)"
        date_end_ctr[key] += 1

    print(f"  Distinct date_end values ({len(running_contracts)} Running contracts):")
    for val, cnt in date_end_ctr.most_common():
        print(f"    {val}: {cnt}")

    # date_start range
    all_start_dates: list[str] = sorted(
        str(c["date_start"])
        for c in running_contracts
        if c.get("date_start") and c["date_start"] is not False
    )
    n_distinct_start = len(set(all_start_dates))
    print(f"\n  date_start across all {len(running_contracts)} Running contracts:")
    print(f"    Earliest : {all_start_dates[0] if all_start_dates else 'N/A'}")
    print(f"    Latest   : {all_start_dates[-1] if all_start_dates else 'N/A'}")
    print(f"    Distinct : {n_distinct_start} of {len(all_start_dates)} records")

    print(f"\n  Sample date_start values:")
    for eid, dept_label in zip(sample_emp_ids, sample_dept_labels):
        c = emp_to_contract.get(eid, {})
        print(f"    Employee ID {eid} ({dept_label}): {c.get('date_start', '?')}")

    # Verdict
    top_date_end, top_count = date_end_ctr.most_common(1)[0] if date_end_ctr else ("unknown", 0)
    n_open_ended = date_end_ctr.get("False (open-ended)", 0)

    print(f"\n  DATE TRUST VERDICT:")
    print(f"    date_start → TRUSTWORTHY: {n_distinct_start} distinct values = real hire dates.")
    print(f"    date_end   → {top_count}/{len(running_contracts)} contracts share '{top_date_end}'.")
    print(f"                 Per §3.6.D: real annual labor-office renewal date (Egyptian")
    print(f"                 policy — all contracts renewed on one consolidated date).")
    print(f"                 {n_open_ended} contract(s) have date_end=False (open-ended).")
    print(f"\n    F3 RECOMMENDATIONS:")
    print(f"    OPTION A (Recommended): show 'Annual renewal: {top_date_end}'")
    print(f"             Makes the upcoming renewal wave visible at profile level.")
    print(f"             Label clearly as the batch renewal date, not individual expiry.")
    print(f"    OPTION B: Show contract status badge ('Running') only; suppress date_end.")
    print(f"    OPTION C: Days-to-renewal countdown — same number for most staff,")
    print(f"             low per-employee utility unless HR tracks staggered dates.")
    print(f"    Open-ended contracts ({n_open_ended}): show 'Open-ended contract'.")

    # ══════════════════════════════════════════════════════════════════════════
    _sec("SECTION 5 — allowance_count SANITY CHECK")

    print(f"  Field : allowance_count")
    print(f"  Label : '{ac_label}'")
    print(f"  Type  : {ac_type}")

    if ac_type == "integer":
        print(f"  {_PASS} INTEGER confirmed — encodes a COUNT, not a monetary value.")
        print(f"  F3 CANDIDATE: safe to show as an asset-count badge.")
    elif ac_type in ("monetary", "float"):
        print(f"  {_FAIL} Type '{ac_type}' — EXCLUDE from F3 (monetary/float, not a count).")
    else:
        print(f"  {_WARN} Unexpected type '{ac_type}' — investigate before using on F3.")

    ac_nonzero = sum(cnt for val, cnt in ac_ctr.items() if val > 0)
    print(f"\n  Employees with allowance_count > 0 : {ac_nonzero}/{n}")
    if ac_nonzero == 0:
        print(f"  NOTE: all employees have count=0 — no asset records entered yet.")
        print(f"        Omit from F3 profile until HR populates asset data in Odoo.")
    else:
        print(f"  F3 RECOMMENDATION: show '# Assets: N' badge when count > 0;")
        print(f"  omit or show '—' when count = 0.")

    # ══════════════════════════════════════════════════════════════════════════
    _sec("SECTION 6 — MANAGER LINE CHECK (parent_id resolution)")

    parent_pop = _pop_count("parent_id")
    print(f"  parent_id populated across all {n} employees: {parent_pop}/{n}")
    print()

    any_manager = False
    for eid, dept_label in zip(sample_emp_ids, sample_dept_labels):
        rec = emp_record_map.get(eid)
        if rec is None:
            continue
        parent_raw = rec.get("parent_id")
        mgr_name   = _m2o_name(parent_raw)
        if mgr_name:
            any_manager = True
            mgr_id = parent_raw[0] if isinstance(parent_raw, (list, tuple)) else "?"
            print(f"  Employee ID {eid} ({dept_label}):")
            print(f"    parent_id = [{mgr_id}] '{mgr_name}'")
            print(f"    F3 label  : 'Reports to: {mgr_name}'  (no extra RPC — from search_read many2one)")
        else:
            print(f"  Employee ID {eid} ({dept_label}): parent_id = EMPTY")
            print(f"    F3 label  : omit 'Reports to' row for this employee")

    print()
    if any_manager:
        print(f"  {_PASS} Manager name available from search_read many2one result — no extra RPC.")
        print(f"  When parent_id empty: omit 'Reports to' row from profile panel.")
    else:
        print(f"  {_WARN} No manager resolved in sample — check parent_id population above.")

    # ══════════════════════════════════════════════════════════════════════════
    _sec("FINAL SUMMARY — F3 Profile Field Verdicts")

    print(f"  Run at (UTC)      : {run_at}")
    print(f"  Cairo today       : {cairo_today}")
    print(f"  Running contracts : {len(running_contracts)}")
    print(f"  Distinct employees: {n_running} ({'PASS' if n_running == _EXPECTED_RUNNING else 'FAIL'})")
    print(f"  Employee records  : {n}")
    print()

    W_F, W_P, W_PCT = 24, 9, 6
    print(f"  {'field':<{W_F}} {'pop':>{W_P}} {'%':>{W_PCT}}  verdict")
    print(f"  {'─'*W_F} {'─'*W_P} {'─'*W_PCT}  {'─'*42}")

    for fname in _PROFILE_FIELDS:
        if fname not in emp_schema:
            print(f"  {fname:<{W_F}} {'N/A':>{W_P}} {'':>{W_PCT}}  NOT IN SCHEMA — exclude")
            continue
        pop = _pop_count(fname)
        pct = round(100 * pop / n) if n else 0

        if fname == "gender":
            verdict = "DO NOT surface on F3 (sensitive, no board purpose)"
        elif fname == "allowance_count" and ac_type not in ("integer",):
            verdict = "EXCLUDE — type not confirmed as integer count"
        elif pct >= 95:
            verdict = "STRONG CANDIDATE"
        elif pct >= 70:
            verdict = "CANDIDATE"
        elif pct >= 30:
            verdict = "WEAK — low coverage, caveat if shown"
        elif pct > 0:
            verdict = "SPARSE — show only when populated"
        else:
            verdict = "EXCLUDE — empty"

        print(f"  {fname:<{W_F}} {f'{pop}/{n}':>{W_P}} {f'{pct}%':>{W_PCT}}  {verdict}")

    print()
    print(f"  allowance_count  : type={ac_type!r}  label={ac_label!r}")
    print(f"  date_end         : UNIFORM — {top_count}/{len(running_contracts)} share '{top_date_end}'")
    print(f"                     → Not per-employee expiry; show as annual renewal or suppress.")
    print(f"  date_start       : TRUSTWORTHY — {n_distinct_start} distinct values → reliable hire date.")
    print(f"  parent_id        : {parent_pop}/{n} populated → 'Reports to' viable for most employees.")
    print(f"  gender           : EXCLUDED from F3 profile (sensitive, no board purpose).")
    print(f"  identification_id: EXCLUDED (PII, no board use).")
    print(f"  barcode          : EXCLUDED (PII, no board use).")
    print(_SEP)


if __name__ == "__main__":
    asyncio.run(run())
