# HR Cluster Discovery — Read-Only Pre-Implementation
## Discovery Evidence Artifact — PHASED

> **Revision 2026-05-28:** §3 (Contracts) and §9 (Cross-Cutting Pattern) corrected after business owner confirmation that `hr.contract.date_end` uniform date reflects real annual labor-office renewal policy, not a bulk-entry artifact. See git log for prior version.

> **Status:** Phased. Discovery complete 2026-05-28.
> - **Phase 1 (active):** Build on stable data — employees, departments, jobs, contracts (with Renewal KPI). Module deliverable target.
> - **Phase 2 (post-June 2026):** Extend with attendance, payroll, overtime, time-off once real entry is confirmed. Attendance/payroll data remains test data until then.
> - **Contracts:** real annual renewal cycle confirmed — Contract Renewal KPI is in scope (see §3).
> **Script:** `scripts/discover_hr_cluster.py` — commit b7f8c61
> **Log:** `logs/hr_discovery.log` — canonical run 2026-05-28T13:43:49Z (76 RPC calls)
> **Method:** JSON-RPC read-only (`search_count`, `search_read`, `read_group`, `fields_get` only — no writes, no AI, no PII).

---

## 1. Model Inventory (Section S1 + S2)

All counts from canonical run 2026-05-28T13:43:49Z.

| Model | Installed? | Count | Data type | Stable? |
|-------|-----------|-------|-----------|---------|
| `hr.employee` | ✅ YES | 136 active / 24 inactive | Real | ✅ Stable |
| `hr.contract` | ✅ YES | 149 total (136 open, 13 close) | Real | ✅ Real — annual renewal cycle, see §3 |
| `hr.attendance` | ✅ YES | 21,800 | **TEST DATA** | ❌ Provisional — real entry begins Jun 2026 |
| `hr.leave` | ✅ YES | 1 | Empty (expected 0) | ✅ Stable |
| `hr.leave.allocation` | ✅ YES | 0 | Empty | ✅ Stable |
| `hr.payslip` | ⚠️ ACCESS ERROR | N/A | Provisional | ❌ Model installed; API user lacks read permission — see §6 |
| `hr.payslip.run` | ✅ YES | 0 | Provisional | ❌ Provisional |
| `hr.applicant` | ✅ YES | 0 | Empty | ✅ Stable |
| `hr.job` | ✅ YES | 84 | Real | ✅ Stable |
| `hr.department` | ✅ YES | 24 | Real | ✅ Stable — NOT flat; see §3 |

### Custom / Overtime models (S2)

Discovered via `ir.model` keyword search.

| Technical model | Label | State | Count | HR relevance |
|----------------|-------|-------|-------|-------------|
| `hr.attendance.overtime` | Attendance Overtime | base | 9,005 | **TEST DATA** — auto-generated from test attendance |
| `hr.overtime.request` | Overtime Request | base | 0 | Workflow model — empty; real use post-Jun 2026 |
| `hr.overtime.request.config` | Overtime Request Config | base | 0 | Configuration — empty |
| `hr.overtime.rule` | Over time Rules | base | 3 | Config rules — 3 records present |
| `hr.policy.overtime.line` | Overtime Policy Lines | base | 0 | Policy config — empty |
| `mission.request` | Mission Request | base | 0 | Business Missions workflow — empty |
| `mission.request.config` | Mission Request Config | base | 0 | Configuration — empty |
| `commission.*` | Various | base | 0 | **NOT HR** — real-estate sales commission; see §7 |
| `contract.commission.*` | Various | base | 0 | **NOT HR** — real-estate sales commission; see §7 |

---

## 2. Employee Data Quality (Section S3)

All checks NON-PII: IDs, states, counts, dates only. No names, emails, wages read.

### S3.1 — Active vs inactive

| Category | Count |
|----------|-------|
| active = True | **136** |
| active = False | **24** |
| **Total (incl. archived)** | **160** |

### S3.2 — Structural gaps (active + inactive)

> ⚠️ **CORRECTION (2026-06-02):** The 'active employees' counts below use `hr.employee.active`, which §3.6 establishes is NOT an employment signal. Retained for history; see §3.6.

| Gap | Count | Note |
|-----|-------|------|
| No department | 4 | Includes archived employees |
| No job title | 3 | Minor gap |
| No manager | 4 | Includes top-level roles |

> **Active-only reconciliation (added 2026-05-29):** The figures above
> are combined active+inactive counts. The HR Module KPI A endpoint
> (`get_headcount()`) computes its breakdowns on active employees only.
> The active-only equivalents are:
> - no department (active only): **2**
> - no job (active only): **2**
> - no manager (active only): see §3.2 if implemented in a future KPI
>
> Reconciled against live verification run 2026-05-29T10:56:42Z
> (`logs/hr_kpi_a_verification.log`, commit bdd8843).

> **Additional gap discovered 2026-05-29 (M5-S2 live verification):**
> 11 active employees have `first_contract_date = False`. This field
> was not part of the original S3.2 data-quality scan; the gap
> surfaced when KPI B (Tenure Distribution) excluded records missing
> the date. Reference: logs/hr_kpi_b_tenure_verification.log, commit
> 80b7afc + this session's verification run 2026-05-29T11:37:31Z.

### S3.3 — Department distribution (active employees, 24 groups)

The UI described departments as "flat — all under Board / top Management." **RPC confirms this is wrong.** There are 24 real sub-departments with a genuine hierarchy. Top groups by headcount:

| Department | Count |
|-----------|-------|
| Board / top Management / Finance | 18 |
| Board / top Management / Commercial / Sales 2 | 14 |
| Board / top Management / Administration / Services | 14 |
| Board / top Management / Commercial / Sales 1 | 12 |
| Board / top Management / Commercial / Sales 3 | 12 |
| Board / top Management / Fleet | 11 |
| Board / top Management / Commercial / Marketing | 8 |
| Board / top Management / HR | 7 |
| *(17 more groups — 1–5 employees each)* | — |

### S3.4 — Job title distribution (67 groups)

Top titles: Senior Sales Executive (15), Driver (9), Sales Supervisor (9), Cleaner (7), Office Boy (6), Sales Executive (6), Sales Manager (4).

### S3.5 — Tenure date field

`hire_date` does **NOT** exist on `hr.employee`. Discovery found two date candidates: `start_date`, `first_contract_date`. Selected: **`first_contract_date`**.

| Date field | Earliest | Latest |
|-----------|---------|--------|
| `first_contract_date` (active employees) | 2017-12-26 | 2025-11-17 |
| `create_date` (active employees) | 2025-07-07 | 2026-05-25 |

---

## 3. Contracts — Annual Renewal Cycle (Egyptian Labor Office Procedure)

### S4.1 — State distribution

| State key | Count | UI label |
|-----------|-------|---------|
| `open` | **136** | Running |
| `close` | **13** | Expired |
| **Total** | **149** | — |

**UI discrepancy:** Odoo UI showed "124 Running / 12 Expired." RPC count (149 total / 136 open / 13 close) is authoritative. Confirmed by Khaled 2026-05-28 — RPC is ground truth.

**13 close contracts:** Minor count discrepancy vs UI (12 expired shown). Likely a UI filter difference. Non-blocking.

### S4.2 — Key fields confirmed

| Field | Type | Label |
|-------|------|-------|
| `date_start` | date | Start Date |
| `date_end` | date | End Date |
| `state` | selection | Status |

`wage` field NOT read (PII guard — type confirmed via fields_get only).

### S4.3 — Contract Renewal: Annual Labor-Office Cycle

**114 of 136 running contracts share `date_end` = 2026-06-30.**

This is a **real annual renewal date** driven by Egyptian labor-office procedure (مكتب العمل / Ministry of Manpower). Egyptian labor law requires periodic employee contract renewals processed in person at the labor office. La Verde HR consolidates all renewals into a single uniform date each year (currently 30/06) to minimize labor-office trips for HR staff. This is intentional operational policy, not a data-quality artifact.

**Confirmed by Khaled (business owner), 2026-05-28.**

**Consequence:** Contract Renewal IS a valid Board KPI — high operational value (HR readiness for the upcoming renewal wave, departmental renewal load, capacity planning).

**Edge cases:**
- 1 open contract has `date_end = False` — open-ended contract; flag for review.
- 13 close contracts: minor count discrepancy vs UI (12 expired shown); likely UI filter difference. Non-blocking.

### §3.5 — Contract State → Payroll Dependency (added 2026-05-29)

> ⚠️ **CORRECTION (2026-06-03):** Describing active-without-running employees as 'onboarding limbo' is wrong in both characterisation and count. As of 2026-06-03 (post-Dev-fix): 34 such employees (23 exit-gap + 11 no-contract + 0 incoming). None are onboarding. See §3.6 for the corrected definition and full breakdown. Original text retained for history.

**Contract states — live counts (Payroll → Contracts UI, 2026-05-29):**

| State | Odoo key | Count | Meaning |
|-------|----------|-------|---------|
| Running | `open` | **136** | Active; payslip generation **ENABLED** |
| Expired | `close` | **12** | Past `date_end`; payslip generation **DISABLED** |
| New | `?` | **0** | Draft, not yet activated |
| Cancelled | `?` | **0** | Voided |

*Exact Odoo keys for New and Cancelled states not RPC-verified (zero records in both); inferred from Odoo standard schema.*

> **RPC vs UI discrepancy:** `search_count([('state','=','close')])` returns 13; the Payroll → Contracts UI shows 12 Expired. The 1-record difference is non-blocking — likely a cancelled/archived contract visible to RPC but filtered out in the UI "Expired" view. See R4 in §6.

**PAYROLL DEPENDENCY:** Odoo does NOT generate a payslip for any employee whose contract is not in "Running" state. **Contract renewal is NOT administrative housekeeping — it directly controls payroll continuity.** A contract that expires without renewal will block that employee's payslip generation until the contract is updated to Running.

**Renewal mechanics (option A — in-place update):** When a contract is renewed at the labor office, the **existing record's `date_end` is updated in place.** A new contract record with a new ID is NOT created. Evidence: active employees (136) == running contracts (136) — a 1:1 count match. Record-level 1:1 mapping verified by `scripts/verify_active_running_mapping.py` (see commit log).

**Record-level mapping — actual state (2026-05-29)**

17 active employees have zero running contracts. This is a deliberate forcing function in La Verde's HR workflow: Odoo refuses to generate a payslip for an employee without a Running contract, which pressures HR to finalize contract paperwork for new hires. These 17 employees are in onboarding limbo — their employee records exist but contract paperwork is incomplete. They do NOT receive payroll until their contract is created. Source: Khaled (business owner), 2026-05-29.

17 running contracts reference employee IDs that are no longer active (`employee.active=False`). When an employee is archived in Odoo, the HR workflow does not auto-close their contract — so contracts remain in `state='open'` indefinitely after exit. This is paperwork debt, NOT a payroll-blocking issue (inactive employees are not on payroll). The numerical match with onboarding-limbo employees (17 = 17 today) is coincidental — they are unrelated phenomena.

Reference: `scripts/verify_active_running_mapping.py` + `logs/active_running_mapping.log`, verification run 2026-05-29 12:50:02Z. Sanity invariant for KPI C must account for both findings — see MODULE_5_PLAN.md §3 KPI C.

**The 12 Expired contracts** are confirmed ex-employees (`employee.active = False`). No expired-contract incidents on active employees — no active employee holds an expired contract as of 2026-05-29. (See 'Record-level mapping' above for the 17 onboarding-limbo cases, which are by-design pre-payroll state, not contract-expiry incidents.)

**Operational implication:** KPI C (Contract Renewal) is a **PAYROLL-RISK DASHBOARD**, not a renewal calendar. Its primary purpose is to surface which running contracts are approaching expiry so HR can prioritize renewals before payslip generation is blocked.

*Sources: Khaled (business owner), Payroll → Contracts UI screenshot 2026-05-29.*

### §3.6 — Employment Status Definition (corrected 2026-06-03)

> **⚠️ THIS SECTION SUPERSEDES all prior uses of `hr.employee.active` as an employment
> measure in this document. It also supersedes the limbo description in §3.5.
> See annotations at the top of §3.2 and §3.5.**

#### (A) The Single Most Important Lesson of the Re-Foundation

True headcount on 2026-06-03 = **115** (distinct `employee_id`s holding a Running contract).

`hr.employee.active` count on the same date = **136**.

**These are NOT the same population.** The overlap (active=True AND Running contract) is only **102**. The delta is +21 — 21 employees flagged active in the UI who hold no Running contract and are therefore NOT currently employed by the correct definition:

| Population                          | Count   | Definition                               |
|-------------------------------------|---------|------------------------------------------|
| `active=True` employees             | 136     | `hr.employee.active` is True             |
| Running-contract employees          | **115** | Has a contract in `state='open'`         |
| **Overlap** (both conditions)       | **102** | Active=True AND Running contract         |
| `active=True`, no Running contract  | 34      | Flagged active in UI; NOT employed       |
| Running contract on `active=False`  | 13      | Employed; HR archiving not yet reflected |

In the original 2026-06-02 pre-fix run the two counts happened to both be 136 — a coincidental equality that concealed the divergence entirely. After the Devs' auto-flip fix landed (2026-06-03), the numbers separated: 136 vs 115, delta +21. The populations were always different; the bug was hiding it.

**The lesson:** `hr.employee.active` is not an employment signal under any circumstances — not when it matches the headcount and especially not when it diverges from it. Employment status is determined solely by Running contracts. Always.

#### (B) The Definition: What "Currently Employed" Means

An employee is currently employed at La Verde **IF AND ONLY IF** they hold at least one contract in `state='open'` (Running).

`hr.employee.active` is a **UI/archive flag** — it controls whether the employee record is visible in Odoo's default filtered views. It carries no employment meaning. An archived employee (`active=False`) may hold a Running contract (data issue — see §3.6.E, Issue 1). A visible employee (`active=True`) may have no Running contract and may have already left the company (see §3.6.E, Issues 3 and 4).

**Contract states — Odoo standard schema:**

| Odoo key | UI label  | Employment meaning                                                                            |
|----------|-----------|-----------------------------------------------------------------------------------------------|
| `open`   | Running   | **Currently employed; payslip generation ENABLED**                                            |
| `draft`  | New       | Hired, not yet started — counted separately as *incoming*, NOT in headcount; Khaled activates to Running manually on the employee's first work day |
| `close`  | Expired   | Left the company (contract ran out or auto-flipped)                                           |
| `cancel` | Cancelled | Contract terminated (employee request or company-initiated)                                   |

`draft` and `cancel` had zero records on 2026-06-03; keys confirmed from Odoo standard schema (`scripts/verify_employment_foundation.py` — `_KNOWN_STATES` constant).

#### (C) Contracts Over Time: Two Renewal Patterns

**Continuing employees (no employment gap):**
On the annual labor-office renewal date (currently 30 June), the **existing contract record's `date_end` is updated in place**. No new contract record is created. The employee holds one contract record for their entire tenure at La Verde. `date_end` on that record reflects only the current renewal expiry; `date_start` reflects the original hire date and is the correct basis for tenure computation.

**Returning employees (employment gap):**
An employee who left and rejoined may hold a second Running contract on the same employee record alongside one or more prior Expired/Cancelled contracts. This is detectable: `COMPUTATION 5` in the re-discovery script identifies records carrying both a Running contract and at least one prior exit-state contract. As of 2026-06-03: **0 same-record rehires**. (Rehires who received a new duplicate employee record instead are detectable via `COMPUTATION 6` name-collision check; also 0 found.)

#### (D) Current Data State (2026-06-03, post-fix)

Source: `scripts/verify_employment_foundation.py` + `logs/employment_foundation_verification.log`, run 2026-06-03T08:22:41Z (2 RPCs).

| Metric                                                   | Count   |
|----------------------------------------------------------|---------|
| Total employee records (active + archived)               | 160     |
| — `active=True`                                          | 136     |
| — `active=False` (archived)                              | 24      |
| Total contract records (all states)                      | 149     |
| — `state='open'` (Running)                               | **115** |
| — `state='close'` (Expired)                              | **34**  |
| **True headcount (distinct Running-contract employees)** | **115** |
| Overlap: `active=True` AND Running contract              | 102     |
| `active=True`, no Running contract                       | 34      |
| Running contract on `active=False` employee              | 13      |

RPC method: `search_read` with `context={'active_test': False}` on both models — retrieves all records regardless of archive flag.

**Pre-fix vs post-fix delta (Dev fix applied 2026-06-03):**

| Metric                         | 2026-06-02 (pre-fix) | 2026-06-03 (post-fix) | Delta |
|--------------------------------|---------------------|-----------------------|-------|
| `state='open'` (Running)       | 136                 | 115                   | −21   |
| `state='close'` (Expired)      | 13                  | 34                    | +21   |
| True headcount                 | 136                 | 115                   | −21   |
| `active=True` count            | 136                 | 136                   | 0     |
| Overlap (active + running)     | 119                 | 102                   | −17   |
| `active=True`, no Running      | 17                  | 34                    | +17   |
| Running on `active=False`      | 17                  | 13                    | −4    |
| Expired-but-running            | 20                  | 0                     | −20   |
| Exit-gap (limbo_exited)        | 6                   | 23                    | +17   |
| Data-gap (limbo_no_contract)   | 11                  | 11                    | 0     |

The 21 contracts that moved from Running to Expired are the direct effect of the auto-flip fix (20 contracts with `date_end` < 2026-06-02 plus 1 with `date_end` = 2026-06-02 that flipped overnight). The exit-gap increase of +17 is the same population becoming visible — those 17 active employees' contracts were previously masking their departure status.

#### (E) Known Data Issues — Status as of 2026-06-03

**Issue 1 — Archived employee with Running contract**
Pre-fix: 17. Post-fix: **13 remain.**
Dev fix applied 2026-06-03: the archive procedure now **blocks archiving an employee who holds a Running contract** — future occurrences are prevented. The 4 resolved cases were among the expired-but-running bucket and auto-flipped when Issue 2 was fixed. The remaining 13 pre-existing cases require manual cleanup: each archived employee's orphan Running contract must be reviewed and moved to Expired or Cancelled.
Status: **future recurrence blocked; 13 pre-existing cases pending manual cleanup.**
Affected IDs: `logs/employment_foundation_verification.log` (2026-06-03 run), field `running_on_inactive`.

**Issue 2 — Expired-but-Running contracts (Odoo auto-flip not firing)**
Pre-fix: 20. Post-fix: **0. ✅ RESOLVED 2026-06-03.**
The Odoo automatic state transition `open → close` triggered by `date_end` passing is now confirmed working — verified by the 2026-06-03 live run showing zero expired-but-running cases. All 20 overdue contracts have correctly transitioned to `state='close'`.
Operational consequence: **17 active employees** whose contracts auto-flipped now have no Running contract and will not receive payslips at the next payroll run unless HR acts per employee (renew if still employed; archive if departed). See §3.7 D1 for payslip logic.
Status: **RESOLVED.** Operational follow-up required by HR for the 17 affected employees.

**Issue 3 — Exit-gap: `active=True` but left the company**
Pre-fix: 6. Post-fix: **23.**
The increase of +17 is not new deterioration — the auto-flip fix (Issue 2) revealed 17 employees whose "Running" contract status had been masking their actual exit-gap condition. Their contracts correctly flipped to Expired; those employees are now visible as what they always were: departed staff with unarchived records.
All 23 employees need HR archiving. None are in true headcount (no Running contract).
Status: **23 cases pending HR cleanup** (6 pre-existing + 17 revealed by fix).
Affected IDs: `logs/employment_foundation_verification.log` (2026-06-03 run), field `limbo_exited`; full named list with lapse dates via `scripts/query_exit_gap_employees.py`.

**Issue 4 — Data-gap: `active=True` but no contract record**
Pre-fix: 11. Post-fix: **11. Unchanged.**
11 employee records exist with no linked `hr.contract` record. These are NOT in true headcount. No Dev fix was applicable; HR data entry required.
Status: **11 cases pending HR cleanup.**
Affected IDs: `logs/employment_foundation_verification.log` (2026-06-03 run), field `limbo_no_contract`.

**Correction to §3.5 — limbo description (supersedes the former "17-limbo"):**
§3.5 (2026-05-29) described active-without-running employees as "onboarding limbo." After the fix, the population is 34. Actual breakdown, 2026-06-03:

| Category                         | Count   | Meaning                                    |
|----------------------------------|---------|--------------------------------------------|
| Incoming (`draft` contract)      | 0       | No new hires pending activation today      |
| Exit-gap (only `close`/`cancel`) | 23      | Departed; 6 pre-existing + 17 fix-revealed |
| Data-gap (no contract at all)    | 11      | No contract record ever linked             |
| **Total**                        | **34**  |                                            |

None are onboarding. §3.5 is annotated; see annotation at its top.

---

### §3.7 — Business Rules & Design Decisions (HR Module)

Durable facts and decisions confirmed by Khaled (business owner) 2026-06-02. These govern all HR KPI A/B/C re-foundation work and must not be rediscovered.

#### D1 — Payslip Logic (business fact)

Odoo generates a payslip for **any contract in `state='open'` (Running), regardless of `date_end`**. Payroll keys off the contract **state only** — the end-date field is irrelevant to payslip generation.

Consequence for Issue 2 (§3.6.E — expired-but-running): those 20 contracts were still paying until the auto-flip fix landed on 2026-06-03, because `state='open'` had not changed despite `date_end` having passed. Once the fix fired and those contracts transitioned to `state='close'`, payroll stops at the next run.

**This risk was confirmed real on 2026-06-03.** When the auto-flip fix landed, 17 active employees' contracts correctly moved to `state='close'`. Their next payslip will not be generated unless HR acts: renew the contract (if still employed) or archive the record (if departed). The 17 affected employees have been verified by direct query (`scripts/query_exit_gap_employees.py`, run 2026-06-03T08:50Z, Group A). IDs: 1175, 1194, 1196, 1205, 1207, 1222, 1250, 1251, 1391, 1393, 1415, 1416, 1417, 1420, 1421, 1425, 2954.

**Operational framing:** "Past end-date but still Running" is a **payroll-risk early warning**. KPI C (Contract Renewal) surfaces which Running contracts are approaching or past expiry so HR can renew before the auto-flip fires and payroll stops. The 2026-06-03 event is a concrete demonstration of exactly this risk materialising.

#### D2 — Tenure Calculation Method (design decision)

**Tenure = net accumulated service at La Verde = the sum of actual worked periods, with any out-of-company gaps subtracted.**

Three approaches considered and their defects:

| Approach                           | Why rejected                                                                   |
|------------------------------------|--------------------------------------------------------------------------------|
| "First contract date to today"     | Includes gap years when person was not at La Verde; overstates tenure for returning employees |
| "Current contract only"            | Ignores all prior service; understates tenure for employees with history       |
| Employee Resume tab work history   | Manually maintained; unreliable — see D4                                       |

**Correct approach:** Sum the `date_start → date_end` (or today, if Running) spans of all `hr.contract` records on the employee's record.

**Worked example:**
- Contract A: Jan 2019 – Dec 2021 (3 years), `state='close'` — Expired.
- Gap: Jan 2022 – Dec 2024 (3 years) — person was NOT at La Verde.
- Contract B: Jan 2025 – today (≈ 1.5 years as of mid-2026), `state='open'` — Running.
- **Correct tenure: 3.0 + 1.5 = 4.5 years.**
- Wrong (first-to-today): 7.5 years — includes the 3-year gap.
- Wrong (current contract only): 1.5 years — ignores 3 years of prior service.

**Note on in-place renewal:** Continuing employees have one contract record updated in place on each annual renewal. Their `date_start` reflects their original hire date — tenure for a non-returning employee is `today − contract.date_start`. Multi-contract employees are the exception; as of 2026-06-03, 0 same-record returning employees exist (see §3.6.D).

**Source of tenure data:** `hr.contract` records (`date_start`, `date_end`), **never** the employee Resume tab (see D4).

#### D3 — Data-Correction Philosophy (operating principle)

**Build on correct logic now; let the data catch up.**

The four known issues (§3.6.E) are in varying states of resolution (Dev fix applied; HR cleanup pending). The KPIs are written on the correct definition (Running = employed). This means they:

1. **Report current reality with its distortions** — useful, because it surfaces the issues directly to HR and Devs via the dashboard.
2. **Report clean numbers automatically as data is fixed** — no AI Engine code change needed when HR completes the cleanup or the Dev fixes propagate fully.

Khaled's words: *"This is not the final shape of the data. Once they fix it, everything will be correct."*

**Implication:** When a KPI figure reflects a known data issue (e.g., exit-gap employees inflating the "no-contract" count), the correct response is to document the issue and surface it in KPI output. Do NOT write application-layer workarounds to patch the data — that couples the AI Engine to the current broken state and breaks again when data is cleaned.

#### D4 — Resume Tab is Unreliable (business fact)

The employee Resume tab in Odoo is designed to hold full work history but is populated **manually by HR staff**, who typically keep the CV on file instead. It is **not a trustworthy data source** for any tenure or employment-history computation.

**Rule:** All tenure and service calculations use `hr.contract` records only. The Resume tab is never queried.

**Distinction to preserve:** "Tenure at La Verde" (derivable from contracts) is a different concept from "total professional experience" (which would include pre-La Verde employment). Total professional experience is not reliably captured anywhere in Odoo and is out of scope for the AI Engine.

#### D5 — Read-Only Layer Does Not Drive Workflow Changes (operating principle)

The AI Engine is a **read-only intelligence layer**: it observes Odoo reality and reports it. It does not push operational workflows to change to suit the dashboard.

**Specific decision (2026-06-02):** When asked whether HR should change the annual contract-renewal process (create a new contract record each year instead of updating `date_end` in place) to make tenure computation easier, the decision was **NO**. In-place renewal is simpler for HR and straightforward to compute. The AI Engine adapts to how Odoo is operated — never the reverse.

**General principle:** If a KPI is hard to compute because of how data is structured in Odoo, the solution is smarter queries in the AI Engine — not asking HR or Devs to restructure their workflow to suit the dashboard.

---

## 4. Business Missions (Section S2)

`mission.request` (Mission Request) **EXISTS** as a real model (state=base, 49 fields). Current count = 0 — no missions submitted yet.

Key fields on `mission.request`: `employee_id`, `department_id`, `job_position_id`, `date_from`, `date_to`, `duration`, `duration_type`, `state`, `config_id` (stage workflow), `attendance_sheet_id`.

The model is a full workflow: stages via `mission.request.config.line`, employee + department + job linkage, attendance sheet integration, manager approval. Usable for HR KPIs once real data exists.

---

## 5. Attendance / Payroll — PROVISIONAL (Section S5)

> **!! ALL FIGURES IN THIS SECTION ARE TEST DATA !!**
> Real HR entry begins June 2026. Do NOT use any number here as a KPI baseline.

### S5.1 — hr.attendance

- Total records: **21,800** (test data)
- Monthly distribution (test data span):

| Month | Count |
|-------|-------|
| November 2025 | 1,183 |
| December 2025 | 437 |
| January 2026 | 3,848 |
| February 2026 | 4,194 |
| March 2026 | 3,778 |
| April 2026 | 4,641 |
| May 2026 | 3,719 |

- Extra/overtime fields on `hr.attendance` (safe, non-relation): `no_validated_overtime_hours` (boolean), `overtime_hours` (float), `overtime_progress` (float), `overtime_status` (selection), `validated_overtime_hours` (float).

### S5.2 — Negative extra-hours hypothesis

The UI showed "Worked Extra Hours" values like −15,537:16. **Hypothesis (confirmed as test-data artifact):** The system working schedule defines expected weekly hours. Since test data was loaded without real check-ins for full scheduled hours, the formula `extra_hours = worked_hours − expected_hours` produces a large negative number per employee. NOT a real HR anomaly.

### S5.3 — hr.attendance.overtime

9,005 records — **auto-generated from test attendance**, not real overtime approvals. Real overtime requests go through `hr.overtime.request` (currently 0 records).

### S5.4 — hr.payslip

`hr.payslip` returned `AccessError` — the model IS installed in Odoo, but the API user account does not have HR/Payroll read permission. Count: unknown. `hr.payslip.run`: count = 0 (provisional).

---

## 6. Notable Findings

| # | Finding | Impact | Action |
|---|---------|--------|--------|
| F1 | `hr.department` is NOT flat — 24 real sub-departments with hierarchy | Department-level HR KPIs are feasible | No action needed; was a UI misread |
| F2 | `hire_date` absent — tenure field is `first_contract_date` | Any "tenure" KPI must use `first_contract_date` | Use confirmed field name in any HR module |
| F3 | 114/136 running contracts share `date_end` = 2026-06-30 — **real annual renewal date** (Egyptian labor-office policy, confirmed by Khaled 2026-05-28) | Contract Renewal KPI is valid and in scope for Phase 1 | Build Contract Renewal KPI — see §3 |
| F4 | `hr.payslip` blocked by AccessError | Payroll KPIs not currently possible via RPC | See §8 Action A1 |
| F5 | `mission.request` model exists and has full workflow schema | Business Missions KPIs are technically feasible | No action; ready for use once data exists |
| F6 | `hr.attendance` = 21,800 test records; `hr.attendance.overtime` = 9,005 auto-generated | All attendance figures provisional | Re-run discovery after June 2026 go-live |

---

## 7. Exclusions

| Name | Why excluded |
|------|-------------|
| **Terminations** | Not HR — UI evidence (Terminations app screenshot, 2026-05-28) shows reservation/contract terminations with real-estate fields (Unit, Building, Project, Customer). Technical model not yet identified — to be resolved in a future Collections/Contracts cluster discovery. Out of HR scope. |
| **`commission.*` models** | Not HR — real-estate sales commission models (`commission.contract`, `commission.line`, `commission.request`, `commission.strategy`, etc.). False positive from "mission" keyword in `ir.model` search. All counts = 0. |
| **`contract.commission.*` models** | Same as above — real-estate commission distribution, not HR. |

---

## 8. Actions Before Module Build

| ID | Action | Owner | When |
|----|--------|-------|------|
| **A1** | Decide whether to grant the API user HR/Payroll read access. If granted, `hr.payslip` becomes readable via RPC and the cluster can include payroll KPIs. | Khaled | Before HR module design |
| **A2** | Confirm renewal day for upcoming years — whether it stays 30/06 or shifts to a different date. This affects KPI framing (the Contract Renewal KPI should display the upcoming renewal date dynamically, not hardcode 30/06). | Khaled | Before HR module build |
| **A3** | Re-run `discover_hr_cluster.py` after June 2026 go-live to establish real baselines for attendance, overtime, and payslip. This document is provisional until then. | AI Engine | Post-June 2026 |
| **A4** | Identify the technical model name behind the Terminations app (real-estate scope) — to be done in Collections/Contracts cluster discovery, not here. | AI Engine | Collections/Contracts discovery session |

---

## 9. Cross-Cutting Pattern: Uniform Dates Require Business Context

Three uniform-date observations emerged from this discovery run. They fall into distinct categories with different implications:

**1. Confirmed real operational policy — `hr.contract.date_end` = 2026-06-30 (114 contracts)**
La Verde HR consolidates all annual contract renewals into a single date each year to minimize trips to مكتب العمل (Egyptian Ministry of Manpower labor office). The uniform date is intentional operational policy. Confirmed by Khaled (business owner) 2026-05-28. **Contract Renewal KPI is valid and in scope for Phase 1.**

**2. Confirmed test data — `hr.attendance` records (21,800 entries)**
These records were loaded for workflow validation only. Real employee attendance entry begins June 2026. All attendance-derived figures (extra hours, overtime, monthly distributions) are provisional until then. **Re-run discovery post-June 2026 before implementing Phase 2.**

**3. Unexplained — outside HR scope — Terminations uniform timestamp (2026-01-12 13:48:09, all 14 records)**
The Terminations app uses real-estate fields (Unit, Building, Project, Customer) — it is NOT an HR model. Whether this timestamp reflects a bulk-entry day or a real operational batch is an open question. To be investigated in the Collections/Contracts cluster discovery, not here. **Not assumed artifact, not assumed real — open question.**

**Lesson:** A uniform date is a signal to ask, not a verdict. The same pattern can be real operational policy or a data artifact depending on business context. Always confirm with the business owner before flagging as problematic.

---

## 10. Discovery Metadata

| Item | Value |
|------|-------|
| Script | `scripts/discover_hr_cluster.py` |
| Script commit | b7f8c61 |
| Log file | `logs/hr_discovery.log` |
| Canonical run | 2026-05-28T13:43:49Z |
| Total RPC calls | 76 |
| Methods used | `search_count`, `search_read`, `read_group`, `fields_get`, `ir.model` reads only |
| PII read | None — no names, emails, wages, phone numbers, private fields |
| Writes to Odoo | None |

---

*Phase 1 artifact — stable data (employees, departments, jobs, contracts). Re-run discovery post-June 2026 before implementing Phase 2 (attendance, payroll, overtime, time-off).*
