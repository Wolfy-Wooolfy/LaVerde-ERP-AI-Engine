# Odoo UI Verification Guide — La Verde Module 2

Practical reference for cross-checking Module 2 KPIs and data points against the live Odoo UI. Used when an identity-equal verification script needs a manual ground-truth comparison.

---

## 1. Access Path

- URL: laverde.odoo.com
- User: Khaled El Masry (or designated read-only Board reviewer)
- Database: plementus-laverde1-master-... (visible in top-right of every Odoo page)

Navigation:
1. Log in to laverde.odoo.com
2. From the apps grid (default landing), click **Collections Mgmt**
3. From Collections Mgmt top-bar, click **All Installments**
4. URL pattern after navigation: `/odoo/action-676`

---

## 2. View Modes

- **List view** (default) — shows individual installments
- **Pivot view** — toggle in top-right of action bar (the spreadsheet icon). Use this for SUM aggregates.
- **Form view** — opens by clicking any individual installment

Switch to Pivot view for KPI cross-checks. The Total row at the bottom of the Pivot is the SUM aggregate.

---

## 3. Adding Filters

Three filter types appear on All Installments search bar dropdown:

### 3.1 Quick Filters (preset)

- **Filters** → Date / Draft / Unpaid / Partially Paid / Fully Paid / Cancelled — preset toggles. Click to apply, click X on chip to remove.

### 3.2 Custom Filters

For exact domain specification:
1. Click search bar dropdown (arrow icon on right)
2. Click **Filters** → **Add Custom Filter**
3. Choose field, operator, value
4. For DATE RANGES (most common KPI need): select Date field, operator "is between", and enter start and end dates.

### 3.3 Group By

Group By is a separate axis from Filter. It controls Pivot row grouping. Available group-by options:
- State, Payment State, Customer, Installment Type, Payment Type
- Project, Phase, Zone, Building, Unit
- Has Checks, All Checks Collected

The "Has Checks" and "All Checks Collected" group-by options reflect stored boolean fields on `rs.installment` (per Phase 0.5 Objective 2 findings).

---

## 4. Measures

The **Measures** dropdown (next to view-mode icons) controls which aggregate columns appear in Pivot view. Available measures for All Installments:

| UI Measure | Technical Field | Used by Module 2 KPI |
|---|---|---|
| Count | `__count` | Record counts on all KPIs |
| Amount | `amount` | KPI 1, KPI 7 |
| Paid Amount | `paid_amount` | KPI 3, KPI 4 |
| Due Amount | `due_amount` | KPI 2, KPI 5 |
| Actual Paid Amount | `x_studio_actual_paid_amount` | KPI 3 |
| Total Due Amount | `total_due_amount` | (drill-down only) |

Default measure: Amount.

---

## 5. UI Label → Technical Field Reference

Use this table to map between UI labels (what the user sees) and technical Odoo fields (what scripts query). Verified identity-equal in Phase 0.5 Section 5.

| UI Label | Technical Field | Type | Notes |
|---|---|---|---|
| Date | `date` | date | Installment due date |
| Status | `state` | selection | Posted = 'post', Draft = 'draft', Cancelled = 'cancel' |
| Payment Status | `payment_state` | selection | Unpaid = 'unpaid', Partially Paid = 'partial', Fully Paid = 'paid' |
| Customer | `partner_id` | many2one → res.partner | |
| Amount | `amount` | monetary | |
| Paid Amount | `paid_amount` | monetary | Includes uncashed checks received |
| Due Amount | `due_amount` | monetary | Cash gap only |
| Actual Paid Amount | `x_studio_actual_paid_amount` | monetary | Cashed payments only (Odoo Studio field) |
| Total Due Amount | `total_due_amount` | monetary | Cash + uncashed checks |
| Payment Period | `payment_type_id` | many2one → rs.payment.type | Display: "Monthly", "Quarterly", etc. |
| Installment Type | `installment_type_id` | many2one → rs.installment.type | |
| Reservation | `reservation_id` | many2one → rs.reservation | |
| Contract | `contract_id` | many2one → rs.contract | |
| Project | `project_id` | many2one → rs.structure.project | |
| Phase | `phase_id` | many2one → rs.structure.phase | |
| Zone | `zone_id` | many2one → rs.structure.zone | |
| Building | `building_id` | many2one → rs.structure.building | |
| Unit | `unit_id` | many2one → rs.structure.unit | |

---

## 6. Selection Field Values

When a script queries a selection field, it uses the technical value (left column). When viewing in UI, you see the label (right column).

### state
| Technical | UI Label | Record Count (2026-05-18) |
|---|---|---|
| post | Posted | 42,443 |
| draft | Draft | 19 |
| cancel | Cancelled | 508 |

### payment_state
| Technical | UI Label | Record Count (2026-05-18) |
|---|---|---|
| unpaid | Unpaid | 12,994 |
| partial | Partially Paid | 418 |
| paid | Fully Paid | 29,558 |

---

## 7. Cross-Check Workflow (Standard Procedure)

When a verification script reports a KPI value, follow these steps to manually verify against Odoo UI:

1. Read the script's printed domain. Example:
   `[('state','=','post'), ('payment_state','in',['unpaid','partial']),
     ('date','>=','2026-05-18'), ('date','<=','2026-05-31')]`

2. Translate to UI filters:
   - `('state','=','post')` → Filters → Status = Posted
   - `('payment_state','in',['unpaid','partial'])` → Filters → Unpaid (and) Partially Paid
   - Date range → Add Custom Filter → Date is between [start] and [end]

3. Switch to Pivot view (top-right icon).

4. Confirm Measures includes the field the script reports (default: Amount).

5. Compare the Total row's value with the script's reported SUM. Tolerance for identity-equal: ±1 EGP per bucket.

---

## 8. Pivot Cross-Reference Examples

### Example A — KPI 2 (Late Uncollected)

Script reports: 322.2M EGP, 1,995 records (as of date d).

UI workflow:
1. Apply: Status = Posted, Payment Status = Unpaid + Partially Paid, Date <= [yesterday relative to d]
2. Pivot, Measures = Due Amount + Count
3. Total row → match the 322.2M (within ±1 EGP)

### Example B — KPI 7 (Expected Forecast, this_month bucket)

Script reports: 22.7M EGP, 133 records (for May 18-31, 2026).

UI workflow:
1. Apply: Status = Posted, Payment Status = Unpaid + Partially Paid, Date is between 2026-05-18 and 2026-05-31
2. Pivot, Measures = Amount + Count
3. Total row → match the 22.7M

---

## 9. Troubleshooting

- **Numbers don't match by a large factor (>10x):** Check the date filter. The most common error is applying only a lower bound (Date >=) without the upper bound (Date <=). This captures all future installments to infinity, not just the intended bucket.

- **Numbers don't match by a small factor (~1%):** Live data drift. The Odoo instance is mutable; if the verification script ran 2 hours ago and you check now, a new installment may have been posted. Re-run the script and compare against UI within the same minute.

- **A filter chip is grey/disabled:** The filter conflicts with another active filter. Remove conflicting filters first.

- **Pivot Total row shows 0:** No records match. Check filter chips — likely an over-restrictive combination.

---

## 10. Document Maintenance

Update this guide whenever:
- A new field is added to `rs.installment` that scripts use
- A new selection value appears in `state` or `payment_state`
- The Odoo UI labels change after an Odoo version upgrade
- A new measure is added to the Measures dropdown

Always reference the source: Phase number + finding section.
