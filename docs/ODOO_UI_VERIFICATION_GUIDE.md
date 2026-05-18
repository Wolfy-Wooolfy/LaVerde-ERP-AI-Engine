# Odoo UI Verification Guide — Collections Module

> **Purpose:** Practical reference for manually verifying KPI figures against the Odoo UI.  
> **Audience:** Khaled (verification) and future contributors (new KPI cross-checks).  
> **Last updated:** 2026-05-18

---

## 1. Navigating to All Installments

**Menu path:**  
Apps grid → **Collections Mgmt** → top navigation bar → **All Installments**

**Direct action ID:** `/odoo/action-676`

---

## 2. Switching to Pivot View

1. Open All Installments (list view by default)
2. Top-right corner: click the **grid/pivot icon** (squares grid — between list and chart icons)
3. You are now in Pivot view with default groupings

**To change the measure:**  
Click **Measures** dropdown (top-right of pivot) → select the column you want to sum:
- **Amount** → `amount`
- **Paid Amount** → `paid_amount`
- **Due Amount** → `due_amount`
- **Actual Paid Amount** → `x_studio_actual_paid_amount`
- **Total Due Amount** → `total_due_amount`

---

## 3. Applying Filters

### Standard filters (via Search bar)

Click the **search bar** (top-left, shows magnifying glass).  
Click the **down arrow** to see filter options:

| Filter option | Technical field | Common use |
|--------------|-----------------|------------|
| Status = Posted | `state = post` | Used by all our KPIs |
| Payment Status = Unpaid | `payment_state = unpaid` | KPI 2, KPI 7 |
| Payment Status = Partially Paid | `payment_state = partial` | KPI 2, KPI 7 |
| Has Checks | `has_checks = True` | Checks pipeline analysis |
| All Checks Collected | `all_checks_collected = True` | Fully cleared check installments |

### Adding a custom date filter

1. In search bar, click **down arrow** → **Filters** → **Add Custom Filter**
2. Field: **Date** (installment due date = `rs.installment.date`)
3. Operator: **≥** (is after or on) or **≤** (is before or on)
4. Value: type the date in `YYYY-MM-DD` format
5. Click **Add** then **Confirm**

**For a date RANGE (two conditions):**  
Add first condition (date ≥ start) → click **Add Filter** again → add second condition (date ≤ end) → **Confirm**. Both conditions are joined with AND by default.

---

## 4. KPI 7 Verification Procedure

### Standard domain (all 4 buckets):
- Filter 1: **Status = Posted**
- Filter 2: **Payment Status = Unpaid** AND **Payment Status = Partially Paid** (use OR between these two)
- Filter 3: **Date ≥ today** (start of bucket)
- Filter 4: **Date ≤ bucket_end** (varies per bucket)
- Measure: **Amount**

### Bucket date ranges (2026-05-18 run):

| Bucket | Date ≥ | Date ≤ | Expected Records | Expected Amount EGP |
|--------|--------|--------|-----------------|---------------------|
| `this_month` | 2026-05-18 | 2026-05-31 | 133 | 22,719,871.00 |
| `this_quarter` | 2026-05-18 | 2026-06-30 | 355 | 55,527,209.00 |
| `this_half` | 2026-05-18 | 2026-06-30 | 355 | 55,527,209.00 |
| `this_year` | 2026-05-18 | 2026-12-31 | 1,934 | 337,946,411.00 |

> `this_quarter` and `this_half` are identical in May 2026 — Q2 and H1 both end Jun 30. This is correct.

---

## 5. KPI 2 Verification Procedure

- Filter 1: **Status = Posted**
- Filter 2: **Payment Status = Unpaid** + **Partially Paid**
- Filter 3: **Date < today** (all past-due dates)
- Measure: **Due Amount** (not Amount)

> **Important:** "KPI – Overdue Installments (Confirmed)" in the Favorites menu uses a **1-day window** (yesterday to today). This is NOT equivalent to our KPI 2. Our KPI 2 captures ALL historically accumulated overdue installments. Do not use that saved filter for KPI 2 verification.

---

## 6. KPI 1 Verification Procedure

- Filter: **Status = Posted** only
- Measure: **Amount**
- No date filter

---

## 7. KPI 3 Verification Procedure (Pending Check Exposure)

- Filter: **Status = Posted** only
- Measures: **Paid Amount** AND **Actual Paid Amount** (add both to pivot)
- `cheques_in_pipeline = Paid Amount − Actual Paid Amount`

---

## 8. Pre-existing Saved Searches (Favorites)

Six saved searches exist on the All Installments view. Their domains are documented below for reference:

| Name | Domain | Notes |
|------|--------|-------|
| All Installments | `[]` | No filter |
| EXEC - KPI Base | `contract_id.state = confirm` | All confirmed-contract installments |
| KPI – Overdue Installments (Confirmed) | `date in [yesterday, today] AND due_amount > 0 AND contract_id.state = confirm` | **1-day window only — NOT equivalent to KPI 2** |
| KPI – Total Collected Amount (Confirmed) | `contract_id.state = confirm` | Measure = paid_amount |
| KPI – Total Contracted Value (Confirmed) | `[]` | Measure = amount |
| KPI – Total Outstanding Amount (Confirmed) | `contract_id.state = confirm` | Measure = due_amount |

> **Note on `contract_id.state = confirm`:** These EXEC filters scope to confirmed contracts only. Our KPI implementations scope to `state = post` on `rs.installment`. These are different scoping rules. See `docs/PHASE_0_5_UI_DISCOVERY_FINDINGS.md §Objective 4` for the discrepancy analysis.

---

## 9. Field Reference Table

| UI Label | Technical Field | Type | Notes |
|----------|----------------|------|-------|
| Amount | `amount` | monetary | Face value — includes paid and unpaid |
| Paid Amount | `paid_amount` | monetary | Includes uncashed cheques |
| Due Amount | `due_amount` | monetary | Cash still owed (not yet received) |
| Actual Paid Amount | `x_studio_actual_paid_amount` | monetary | Actually cashed (bank + cash) |
| Total Due Amount | `total_due_amount` | monetary | Cash owed + uncashed cheques |
| Check Pending Amount | `check_pending_amount` | monetary | Computed from check_ids |
| Check Approved Amount | `check_approved_amount` | monetary | Approved check total |
| Has Checks | `has_checks` | boolean (stored) | True if any check_ids linked |
| All Checks Collected | `all_checks_collected` | boolean (stored) | True if all linked checks are cashed |
| Payment Period | `payment_type_id` | many2one | e.g., "Quarterly" |
| Reservation | `reservation_id` | many2one → `rs.reservation` | Parent reservation |
| Contract | `contract_id` | many2one → `rs.contract` | Parent contract |
| Phase | `phase_id` | many2one → `rs.structure.phase` | 5 phases total |
| Building | `building_id` | many2one → `rs.structure.building` | 277 buildings total |
| Zone | `zone_id` | many2one → `rs.structure.zone` | 11 zones total |
| Unit | `unit_id` | many2one → `rs.structure.unit` | 1,873 units total |

---

## 10. Selection Field Values (Technical → UI Label)

### `state` (Accounting Status)

| Technical value | UI label | Count (2026-05-14) |
|----------------|----------|--------------------|
| `draft` | Draft | 19 |
| `post` | Posted | 42,443 |
| `cancel` | Cancelled | 508 |

### `payment_state` (Payment Status)

| Technical value | UI label | Count (2026-05-14) |
|----------------|----------|--------------------|
| `unpaid` | Unpaid | 12,994 |
| `partial` | Partially Paid | 418 |
| `paid` | Fully Paid | 29,558 |

---

## 11. Screenshot Placeholders

The following screenshots should be added to `docs/screenshots/odoo_ui/`:

1. `all_installments_list.png` — List view with search bar visible
2. `pivot_view_measures.png` — Pivot view with Measures dropdown open
3. `custom_filter_date_range.png` — Adding a custom date range filter
4. `kpi7_this_month_verification.png` — KPI 7 this_month bucket applied

> Placeholders only — Khaled to provide actual screenshots.

---

*Created by Phase 0.5 discovery — 2026-05-18*
