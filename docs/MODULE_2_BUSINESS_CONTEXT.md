# Module 2 — Collections: Business Context

> **Status**: Discovery Phase 1 Complete (2026-05-14)  
> **Source**: Khaled's walkthrough of Projects Mgmt, Contracts Mgmt, Collections Mgmt, RS Accounting, and Accounting apps  
> **Snapshot Date**: 2026-05-14  
> **Next Step**: Discovery script against live Odoo will verify all `[OPEN QUESTION — Discovery]` items

---

## 1. Module Identity

| Field | Value |
|-------|-------|
| Slug | `module_2_collections` |
| Display Name (EN) | Collections |
| Display Name (AR) | التحصيلات |
| Primary User | Board of Directors (Chairman, CEO, CFO) — مجلس الإدارة (رئيس مجلس الإدارة، الرئيس التنفيذي، المدير المالي) |
| Secondary User | Khaled — Sales Manager, builder and curator of the module, not the daily target user |

> **Note / ملاحظة:** The daily Collections Officer (موظف تحصيلات) workflow is explicitly deferred to a future module / phase — see [Out of Scope](#16-out-of-scope-explicit).

**Why "Collections" and not "Accounting":** The module name matches the existing Odoo app "Collections Mgmt" that the daily user (Collections Officer) already uses. This provides immediate familiarity and clearly distinguishes the scope from Standard Odoo Accounting. See `docs/MODULE_2_NAMING_DECISION.md`.

**Discovery status:** Phase 1 complete (2026-05-14). See [`docs/MODULE_2_DISCOVERY_PHASE_1.md`](MODULE_2_DISCOVERY_PHASE_1.md).

---

## 2. Target User Persona

**Primary: Board of Directors — مجلس الإدارة**
- Role: Chairman (primary), CEO, CFO
- Use frequency: on-demand, not daily — opens the tool for review, decisions, and ad-hoc questions
- Needs: aggregate KPIs, project-level performance comparison, trend visibility, on-demand drill-down into any detail
- Explicit Chairman request: "complete visibility into any detail at any time"

**Builder / Curator: Khaled (Sales Manager — موظف مبيعات)**
- Builds and maintains the tool on his own initiative
- Validates every figure against live Odoo before presenting to the Board
- Not a daily consumer of the tool's output

**Deferred: Collections Officer (موظف تحصيلات)**
- Daily operational user of the existing Collections Mgmt app
- Will NOT be a target user of this MVP
- A future module or future phase may address this persona, but it is explicitly out of scope here

**NOT a target user in this MVP:** Standard Odoo Accounting users, and Collections Officers using the daily Collections Mgmt workflow.

### 2.1 Persona Evolution

**Original assumption (initial discovery phase):** The primary target persona was the Collections Officer (موظف تحصيلات) — the daily operational user of the existing Collections Mgmt app. The initial framing assumed the module would address operational needs: overdue installment tracking, follow-up workflows, and per-installment status views.

**What changed:** A subsequent strategy discussion confirmed that the actual driver was a Board-level request for executive visibility. The Chairman's explicit request — "complete visibility into any detail at any time" — reframed the entire MVP from an operational tool to an executive intelligence layer. The Collections Officer persona is explicitly deferred.

**Technical implication:** Phase 1 discovery findings remain fully valid. The data sources, Odoo models, field names, record counts, and linkage paths do not change as a result of this pivot. The feature scope, KPIs, and AI interaction patterns — which shift from operational to executive — are to be defined in `MODULE_2_MVP_DESIGN.md` (Work Item 2).

---

## 3. The Three Existing Odoo Apps

This module is an **AI intelligence layer that reads from** these three existing apps. It does not replace any of them.

| App | Type | Role | Primary Data Models |
|-----|------|------|---------------------|
| **Accounting** | Standard Odoo | General ledger, journals, bank reconciliation, VAT, financial reports | `account.move`, `account.move.line`, `account.journal`, `account.payment` |
| **RS Accounting** | Custom (La Verde) | Operational receivables: checks management, payments, penalties, discounts | `rs.account.check` (5,179), `rs.account.payment.*`, `rs.penalty` (170), `rs.discount` |
| **Collections Mgmt** | Custom (La Verde) | List/filter view of installments by status (All / Due / Late / Draft / Cancelled / Checks / Termination) | `rs.installment` (42,970), `rs.reservation` (1,479), `rs.contract` (1,409) |

---

## 4. Real Estate Structure Hierarchy

```
Project (مشروع)
   └── Phase (مرحلة)
        └── Zone (منطقة)
             └── Building (مبنى)
                  └── Unit (وحدة)
```

**Each level carries:**
- Area, start meter price, license info, property type
- Accounting details, discount config, penalty config
- BOQs (Bill of Quantities), notes

**Each level has stages:** `New → Under Creation → Under Review → Approved → Launched`

**The Project owns:** Phases, Zones, Buildings, Units, Documents, Gallery, Payment Plans.

---

## 5. Sales Workflow: From Reservation to Contract

**Two entry points for a reservation:**
- Projects Mgmt → Unit → "Create Reservation" button
- CRM → Opportunity → "Create Reservation" tab

Both paths converge on the same Reservation form.

**Reservation lifecycle stages:**

```
Draft → Initial → Confirmed → Contracted
```

**Trigger conditions:**

| Transition | Trigger |
|------------|---------|
| `Draft → Initial` | Reservation `Confirm` button pressed. Requires a valid Payment Term to exist (with approved Payment Plan). |
| `Initial → Confirmed` | Any payment received — even 1 EGP. |
| `Confirmed → Contracted` | Full Down Payment paid. User presses "Convert to Contract". Creates an `rs.contract` record and starts the Contract Approval Cycle. |

Once the reservation reaches `Initial`, installments become visible to the Accounting team.

### Contract Approval Cycle (5 Stages)

Once a Reservation is converted to a Contract, the contract record (`rs.contract`) goes through a 5-stage approval cycle before becoming active:

```
Draft → Legal Review → Finance Review → Engineering Review → Confirmed
```

| Stage | Technical value | Reviewer | Purpose |
|-------|-----------------|----------|---------|
| Draft | `draft` | Sales | Initial contract drafting |
| Legal Review | `[REQUIRES VERIFICATION]` | Legal Affairs | Contract terms, compliance, legal validity |
| Finance Review | `finance` | Finance/Accounting | Pricing, payment terms, financial commitments |
| Engineering Review | `[REQUIRES VERIFICATION]` | Engineering | Unit specifications, delivery dates, technical details |
| Confirmed | `confirm` | — | All reviews passed; contract is active |

The contract can be `Cancelled` (`cancel`) from any stage.

**Live snapshot (2026-05-14):** 1,409 total contracts. Distribution: Draft (3), Finance Review (1), Confirmed (1,402), Cancelled (3). The vast majority of historical contracts are in `Confirmed` because they completed the cycle. The `legal` and `engineering` intermediate states were not observed in live data — no contracts are currently at those stages.

---

## 6. Payment Term and Payment Plans

A Payment Term must exist before a Reservation can be confirmed. The Payment Term contains a Payment Plan and generates the installment schedule.

### Standard Payment Plans
- Defined in: Projects Mgmt → Configuration → Payment Plans
- Apply automatically when unit conditions match
- Standard discounts are embedded here (e.g., higher down payment → predefined discount)

### Special Payment Plans
- Per-customer, created from inside the Payment Term form
- **Trigger:** toggle "Special" → click gear icon → opens Special Plan dialog
- **Tabs in Special Plan:** Installments, Maintenance, Facilities, Other, Rounding, Discount
- **Approval chain:** `Draft → Waiting → Direct Manager Approved → Sales Manager Approved`
- Reaching final approval is significant — indicates a high-value or special-case customer

**Constraint (enforced by Odoo):** The sum of all installments in a Payment Plan must equal the unit price exactly. The Payment Term cannot be confirmed otherwise.

After defining the plan, user presses `Calculate` to generate the installment schedule, which appears in the Payment Term's Installments tab.

---

## 7. Installment Types (8 Categories)

| # | Type (EN) | Type (AR) | Description |
|---|-----------|-----------|-------------|
| 1 | Down Payment | المقدمة | First payment. Must be fully paid before contract conversion. |
| 2 | Regular | قسط دوري | Periodic installments (monthly, quarterly, or any cadence). The bulk of installments. |
| 3 | Maintenance | وديعة الصيانة | Maintenance deposit. |
| 4 | Administration Fees | مصاريف إدارية | Admin fees. Also generated during terminations. |
| 5 | Garage | الجراج | Parking. |
| 6 | Club | النادي | Club membership. |
| 7 | Facilities | مرافق | Pool, gym, and similar amenities. |
| 8 | Penalties | الغرامات | Late payment penalties. Currently added manually via Amendment because grace periods are not yet standardized across contracts. Target future state: automatic. |

---

## 8. The 5 Amount Columns (Critical — Used in Every Report)

These columns appear in Collections Mgmt → All Installments → Pivot View. Their precise definitions are the foundation of all financial reporting in this module.

| Column | Precise Meaning |
|--------|-----------------|
| **Amount** | The total of all installments of every type, whether paid or unpaid. |
| **Paid Amount** | The total of everything paid, INCLUDING checks not yet collected. (Checks received but not yet deposited count here.) |
| **Actual Paid Amount** | The total actually collected: checks that have been cashed + cash payments. (Pending checks do NOT count here.) |
| **Due Amount** | The total still owed in cash only. The cash gap. |
| **Total Due Amount** | The total still owed in both forms: uncollected cash + uncollected checks. |

### Reconciliation Equations (both must hold)

```
Amount = Paid Amount + Due Amount               (cash-basis view)
Amount = Actual Paid Amount + Total Due Amount  (cash + uncollected checks view)
```

**Pending check exposure:**  
`Paid Amount − Actual Paid Amount` = value of checks received but not yet cashed.  
This gap is the "pending check exposure" — a key risk metric for the Collections Officer.

### Additional Check-Specific Fields (Discovered in Phase 1)

Beyond the 5 primary columns, `rs.installment` carries two Odoo Studio custom fields that quantify check collection status with greater precision:

| Field (Odoo) | Label | Meaning |
|--------------|-------|---------|
| `x_studio_bank_collected_amount` | Bank Collected Amount | Value of checks successfully cashed by the bank |
| `x_studio_executive_outstanding_amount` | Executive Outstanding Amount | Value of checks not yet collected — held by customer or in transit |

These fields enable a finer-grained breakdown:

```
Paid Amount ≈ Bank Collected Amount + Executive Outstanding Amount + Cash Received
Actual Paid Amount ≈ Bank Collected Amount + Cash Received
```

The `≈` is intentional — exact reconciliation against the 5 primary columns is a Phase 2 open item (see Section 15).

Note: `x_studio_actual_paid_amount` is also an Odoo Studio field — it maps to the "Actual Paid Amount" column in the table above. The `x_studio_` prefix indicates these were added via Odoo Studio (low-code field editor). They are queryable identically to native fields.

---

## 9. Live Snapshot Baseline

**Snapshot date:** 2026-05-14  
**Source:** Collections Mgmt → All Installments → Pivot View, taken by Khaled

### All Installments (entire portfolio)

| Column | Amount (EGP) |
|--------|-------------|
| Amount | 6,123,549,625.23 |
| Paid Amount | 3,491,180,448.95 |
| Actual Paid Amount | 2,970,724,764.85 |
| Due Amount | 2,632,369,176.28 |
| Total Due Amount | 3,152,824,860.38 |

### Late Installments (overdue subset)

| Column | Amount (EGP) |
|--------|-------------|
| Amount | 373,147,294.00 |
| Paid Amount | 60,542,414.60 |
| Actual Paid Amount | 60,542,414.60 |
| Due Amount | 312,604,879.40 |
| Total Due Amount | 312,604,879.40 |

**Note on Late Installments:** Paid Amount = Actual Paid Amount, and Due Amount = Total Due Amount. This is because the Late Installments view shows only two clear states:
- Late but fully collected (counted in Paid = Actual Paid)
- Late and not collected at all (counted in Due = Total Due)

The "late with pending check" intermediate state does not appear distinctly in this view — pending checks against late installments need to be investigated separately. `[OPEN QUESTION — Discovery]`: confirm where late installments with pending checks are tracked.

**Key business insight:** ~312M EGP in late uncollected receivables. This is the primary value the module must surface and manage.

---

## 10. Installment Status — Two Independent Fields

The installment has TWO status fields that work together:

**Field 1: Accounting Status** (column "Status")
- Field name: `state` (on `rs.installment`)
- Values: `draft` (19 records), `post` / "Posted" (42,443 records), `cancel` (508 records)
- `post` = treasury staff has recorded a payment against this installment
- Set automatically by RS Accounting when a payment is registered

**Field 2: Payment Status** (column "Payment Status")
- Field name: `payment_state` (on `rs.installment`)
- Values: `unpaid` (12,994 records), `partial` / "Partially Paid" (418 records), `paid` / "Fully Paid" (29,558 records)
- Reflects how much of the installment amount has been received
- Set automatically based on the running total of payments

The Collections Officer does NOT change either status manually.

**Phase 1 Discovery:** Both field names and all state values confirmed. The `post` status is driven by RS Accounting (`rs.account.payment.*`), not Standard Odoo Accounting — `account.payment` has 0 records. The exact sequence between `rs.account.payment` creation and `rs.installment.state → post` remains open for Phase 2 investigation.

---

## 11. Penalty Mechanism

| | State |
|-|-------|
| **Current** | Manual. Penalty period (grace period) is not standardized across contracts. |
| **Future** | Automatic, once each contract's grace period is documented in the system. |

**How a penalty is added:** via `Create Amendment` on the contract. The amendment generates a penalty installment line.

**Penalty appears as:** installment type #8 (Penalties / الغرامات) in the installment schedule.

---

## 12. Discount Mechanism

**Standard discounts:**
- Defined inside Standard Payment Plans
- Applied automatically when conditions match (e.g., higher down payment triggers a predefined discount tier)

**Special discounts:**
- Part of Special Payment Plans
- Subject to the 3-stage approval chain: `Draft → Waiting → Direct Manager Approved → Sales Manager Approved`

---

## 13. Termination Mechanism

When a customer cancels a contract:
- **Action:** `Create Amendment` on the contract
- **Generates:** refund + Administration Fees installment line
- **Same mechanism** is used to manually add penalties

The Termination Installments tab in Collections Mgmt shows installments related to terminated contracts.

---

## 14. The Payment-to-Posting Flow

```
Customer pays (check or cash)
   ↓
RS Accounting → Checks Management OR Payments (or Cash Payments)
   ↓
Treasury staff posts the payment
   ↓
Installment status updates automatically (Draft → Posted → Partial/Full Paid)
   ↓
Standard Accounting reflects the journal entry (account.move)
   ↓
Collections Mgmt shows updated installment status
```

In Egyptian real estate, **checks are the primary payment channel**, not bank transfers. The Checks Management section in RS Accounting handles the check lifecycle (received → pending → cashed → bounced/posted).

---

## 15. Open Questions — Discovery Status

### Answered by Phase 1 Discovery (2026-05-14)

- [x] **Exact Odoo model names** — Confirmed:

  | Entity | Model | Records |
  |--------|-------|---------|
  | Installment | `rs.installment` | 42,970 |
  | Reservation | `rs.reservation` | 1,479 |
  | Contract | `rs.contract` | 1,409 |
  | Amendment | `rs.contract.amendment` | not counted |
  | Payment Term | `rs.payment.term` | 1,497 |
  | Payment Plan | `rs.payment.plan` | 121 |
  | Checks | `rs.account.check` | 5,179 |
  | Check Lot | `rs.account.check.lot` | 77 |
  | Penalty | `rs.penalty` | 170 |
  | Discount | `rs.discount` | 0 (model exists, no live records) |
  | Termination | `rs.termination` | not counted |

  Custom module prefix in Python packages: `pl_realestate_*`.
  Odoo model prefix: `rs.*`.
  Likely vendor: Plementus (inferred from `pl_` prefix).
  See `docs/MODULE_2_DISCOVERY_PHASE_1.md` for full model inventory.

- [x] **Field name linking installment to salesperson** — A direct field
  does not exist on `rs.installment`. A 2-hop join is required:
  ```
  rs.installment.reservation_id
      → rs.reservation.opportunity_id
          → crm.lead.user_id  (salesperson)
  ```
  The `pl_realestate_crm` module provides this linkage by design.
  Both `reservation_id` and `contract_id` are available on
  `rs.installment`; both paths converge on `rs.reservation.opportunity_id`.

- [x] **Whether RS Accounting and Standard Accounting double-record
  payments** — No double-recording. Standard `account.payment`
  has 0 live records. La Verde does not use the standard Odoo payment
  model. All payments go through the custom `rs.account.payment.*`
  family. RS Accounting is the canonical payment system.

- [x] **Exact field names and all possible values for both installment
  status fields** — Confirmed on `rs.installment`:

  | Business Label | Field Name | Technical Values |
  |----------------|------------|-----------------|
  | Status (Accounting) | `state` | `draft` (19), `post` (42,443), `cancel` (508) |
  | Payment Status | `payment_state` | `unpaid` (12,994), `partial` (418), `paid` (29,558) |

  Note: the UI label for `post` is "Posted". The UI label for `partial`
  is "Partially Paid". Both are set automatically; the Collections
  Officer does not set them manually.

- [x] **All custom `x_*` fields on the installment model** — Three
  Odoo Studio fields found on `rs.installment`:

  | Field | Type | Label |
  |-------|------|-------|
  | `x_studio_actual_paid_amount` | monetary | Actual Paid Amount |
  | `x_studio_bank_collected_amount` | monetary | Bank Collected Amount |
  | `x_studio_executive_outstanding_amount` | monetary | Executive Outstanding Amount |

  No `x_*` fields found on `rs.reservation` or `rs.contract` in Phase 1.
  Custom payment models not yet inventoried.

- [x] **Record counts per model** — See `docs/MODULE_2_DISCOVERY_PHASE_1.md`
  Section 14. Pagination required for `rs.installment` (42,970) and
  `rs.account.check` (5,179). All other operational models are under
  the 5,000 threshold.

---

### Partially Answered — Follow-up Required in Phase 2

- [~] **How "Check Lot" groups checks** — `rs.account.check.lot`
  exists (77 records, states: `draft`/`validate`). Its relationship
  to individual checks in `rs.account.check` was not deep-dived.
  The `check_ids` field on `rs.installment` links directly to
  `rs.account.check` (many2many), not through lots. Phase 2 should
  examine `rs.account.check.lot` fields to confirm grouping mechanism.

- [~] **`x_*` fields on contract, reservation, and payment models** —
  Only `rs.installment` was inventoried for Studio fields in Phase 1.
  The custom payment models (`rs.account.payment.*`) were not
  deep-dived.

- [~] **Where late installments with pending checks are tracked** —
  `rs.installment` carries both `check_ids` (many2many to
  `rs.account.check`) and `check_pending_amount` (monetary). Late
  installments with pending checks are likely identifiable by
  combining `payment_state = unpaid/partial` with
  `check_pending_amount > 0`. Exact query and reconciliation against
  the live snapshot numbers requires verification in Phase 2.

- [~] **Whether `Posted` status comes from Standard Accounting or RS
  Accounting** — Since `account.payment` has 0 records, the Standard
  Accounting posting path is not used. The `rs.account.payment.*`
  family is the trigger source. However, the sequence of
  `rs.account.payment` creation vs. `rs.installment.state → post`
  was not verified. Phase 2 should confirm which event fires first.

---

### Remaining Open — Phase 2 Required

- [ ] **Exact records in `rs.installment.type`** — The 8 installment
  types (Down Payment, Regular, Maintenance, etc.) are stored as
  Many2One to `rs.installment.type`, not as a selection field.
  The actual record IDs, names, and sequence have not been fetched.
  A `search_read` on `rs.installment.type` with no domain is needed.

- [ ] **`rs.account.payment` and `rs.account.payment.installment` full
  inventory** — These are the canonical payment models but were not
  deep-dived in Phase 1. Field inventory, record counts, and linkage
  to `rs.installment` via `payment_line` (one2many to
  `rs.account.payment.installment.line`) are all unknown.

- [ ] **`rs.structure.project` and `rs.structure.unit` field inventory**
  — Phase 1 picked the wrong sub-models (`rs.structure.project.type`
  and `rs.unit.search`). The actual project count, unit count, and
  parent-link fields need re-discovery. From indirect evidence:
  3 live projects (New Capital, Cassette, La puerta) and several
  thousand units are likely.

- [ ] **Special Payment Plan intermediate approval states** — Live data
  shows only `draft` (112), `s_sales_manager_approved` (7), `approve`
  (1), `cancel` (1) on `rs.payment.plan`. The intermediate states
  described in the UI (`Waiting`, `Direct Manager Approved`) were not
  observed because no plans are currently at those stages. Their
  technical values need verification.

- [ ] **Reconciliation of the 5+2 amount columns** — The 5 standard
  columns and the 2 Studio check fields should satisfy:
  ```
  Paid Amount ≈ Bank Collected Amount + Executive Outstanding Amount + Cash Received
  ```
  The exact formula and whether "Cash Received" is a separate field
  requires verification against sample records.

- [ ] **`account.analytic.account` usage** — Whether analytic accounts
  are used per project or compound was not examined in Phase 1.

- [ ] **Sequence of events: `rs.account.payment` vs installment `state`**
  — Does `rs.account.payment` creation drive
  `rs.installment.state → post`, or vice versa? Which model holds
  the most current truth at any given moment?

---

## 16. Out of Scope (Explicit)

This module will NOT do:

- General ledger reporting — Standard Odoo handles this
- Tax / VAT handling — Standard Odoo handles this
- Vendor bills / accounts payable — not a collections concern
- Bank reconciliation
- Manual installment status changes — system handles automatically
- Writing back to Odoo — READ-ONLY architectural rule is absolute
- Replacing Collections Mgmt, RS Accounting, or Accounting apps — this is an intelligence layer on top of them
- Collections Officer daily workflow — operational follow-up logging, per-installment manual actions, and the daily filter-by-status views of Collections Mgmt remain in the existing Odoo app
- Per-salesperson operational dashboards — sales team management views are not a Board-level concern in this MVP
- Predictive analytics, alerts, and notifications — deferred until the Board confirms what is "alert-worthy" through actual use
