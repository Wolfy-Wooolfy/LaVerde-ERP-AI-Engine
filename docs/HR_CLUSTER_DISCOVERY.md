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

**The 12 Expired contracts** are confirmed ex-employees (`employee.active = False`). No current payroll-blocking incidents — no active employee holds an expired contract as of 2026-05-29 (active = 136, running = 136 verified 2026-05-29).

**Operational implication:** KPI C (Contract Renewal) is a **PAYROLL-RISK DASHBOARD**, not a renewal calendar. Its primary purpose is to surface which running contracts are approaching expiry so HR can prioritize renewals before payslip generation is blocked.

*Sources: Khaled (business owner), Payroll → Contracts UI screenshot 2026-05-29.*

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
