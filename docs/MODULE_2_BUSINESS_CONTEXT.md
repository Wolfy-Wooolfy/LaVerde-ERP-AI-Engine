# Module 2 — Collections: Business Context

> **Status**: Pre-Discovery Reference Document  
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
| Primary User | Collections Officer (موظف تحصيلات) |
| Secondary User | Khaled — Sales Manager, high-level dashboards only |

**Why "Collections" and not "Accounting":** The module name matches the existing Odoo app "Collections Mgmt" that the daily user (Collections Officer) already uses. This provides immediate familiarity and clearly distinguishes the scope from Standard Odoo Accounting. See `docs/MODULE_2_NAMING_DECISION.md`.

---

## 2. Target User Persona

**Primary: Collections Officer (موظف تحصيلات)**
- Role: Accountant under the Accounting department
- Uses the system daily
- Needs: real-time installment status, overdue tracking, check status, outstanding balances per customer
- Current tool: Collections Mgmt app (Odoo custom)

**Secondary: Khaled (Sales Manager — موظف مبيعات)**
- Uses high-level dashboards only
- Needs: portfolio summary, total outstanding, late receivables, trend analysis

**NOT a target user:** Standard Odoo Accounting users. They have native Odoo financial reporting and do not need this module.

---

## 3. The Three Existing Odoo Apps

This module is an **AI intelligence layer that reads from** these three existing apps. It does not replace any of them.

| App | Type | Role | Primary Data Models |
|-----|------|------|---------------------|
| **Accounting** | Standard Odoo | General ledger, journals, bank reconciliation, VAT, financial reports | `account.move`, `account.move.line`, `account.journal`, `account.payment` |
| **RS Accounting** | Custom (La Verde) | Operational receivables: checks management, payments, penalties, discounts | Real estate-specific check/payment models `[OPEN QUESTION — Discovery]` |
| **Collections Mgmt** | Custom (La Verde) | List/filter view of installments by status (All / Due / Late / Draft / Cancelled / Checks / Termination) | Installment model `[OPEN QUESTION — Discovery]` |

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
| `Confirmed → Contracted` | Full Down Payment paid. User presses "Convert to Contract". |

Once the reservation reaches `Initial`, installments become visible to the Accounting team.

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
- Values: Draft, Posted, possibly Cancelled
- Posted = treasury staff has recorded a payment against this installment
- Set automatically when a payment is registered

**Field 2: Payment Status** (column "Payment Status")
- Values: Partially Paid, Fully Paid, possibly Unpaid
- Reflects how much of the installment amount has been received
- Set automatically based on the running total of payments

The Collections Officer does NOT change either status manually.

`[OPEN QUESTION — Discovery]`: confirm exact field names and all possible values for both status fields. Also confirm whether Posted is driven by Standard Accounting (account.move state) or RS Accounting (check/payment record).

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

## 15. Open Questions (To Resolve in Discovery)

The following must be answered by the Discovery script against live Odoo:

- [ ] Exact Odoo model names for: installments, checks (receivable/payable/suspension/real estate), reservations, contracts, amendments, payment terms, payment plans, special payment plans
- [ ] Field name(s) linking installment to salesperson (for CRM↔Receivables attribution)
- [ ] Whether `account.analytic.account` is used per project/compound
- [ ] Whether the `Posted` status comes from Standard Accounting (`account.move`) or from RS Accounting (check approval)
- [ ] Record counts per model (pagination strategy depends on these)
- [ ] Whether RS Accounting and Standard Accounting double-record the same payment, or only one is canonical
- [ ] All custom `x_*` fields on installment, contract, reservation, and payment models
- [ ] How "Check Lot" and "Receivable Checks Lot" group checks (mentioned in RS Accounting → Checks Management)
- [ ] Confirm where late installments with pending checks are tracked (see Section 9 note)
- [ ] Sequence of events: does account.move creation precede, follow, or run in parallel with installment status change to "Posted"? This affects which model to query for the most current truth.
- [ ] Confirm exact field names and all possible values for both status fields (Status and Payment Status) on the installment model

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
