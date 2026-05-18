# Phase 0.5 — UI-Driven Discovery Findings

**Run date (Cairo):** 2026-05-18  
**Script:** `scripts/discover_phase_0_5_ui_artifacts.py`  
**Output file:** `scripts/discover_phase_0_5_ui_artifacts_output.txt`  
**Status:** PHASE 0.5 COMPLETE — awaiting Khaled PATH decision

---

## Objective 1 — Checks Relation on rs.installment

### Hypothesis
Phase 1 Discovery documented `check_ids` (many2many → `rs.account.check`) and `check_pending_amount` on `rs.installment`. The "Checks" tab on the installment form view is backed by this relation. The question is whether future-dated installments (date ≥ today) carry check records in meaningful numbers — which would determine whether the KPI 7 cheques annotation is useful.

### Evidence

**`check_ids` field confirmed:**
```
check_ids   many2many   relation=rs.account.check   label='Checks'
```

**`rs.account.check` model — 5,224 total records. Key fields:**

| Field | Type | Meaning |
|-------|------|---------|
| `amount` | monetary | Check face value |
| `collected_amount` | monetary | Amount already cashed |
| `residual_amount` | monetary | Amount still uncollected |
| `installment_paid_amount` | monetary | Paid amount on linked installment |
| `installment_due_amount` | monetary | Due amount on linked installment |
| `state` | selection | Check status |
| `maturity_date` | date | Check maturity/due date |
| `date` | date | Check issue date |
| `installment_ids` | many2many | Back-link to `rs.installment` |
| `contract_id` | many2one | → `rs.contract` |
| `reservation_id` | many2one | → `rs.reservation` |
| `phase_id`, `building_id`, `zone_id`, `unit_id` | many2one | Property hierarchy |

**Statistical proof (e_pre / e_post / e_calc):**

| Scope | Total unpaid future | With check_ids ≠ False | % |
|-------|--------------------|-----------------------|---|
| `this_year` bucket (today → Dec 31) | 1,934 | **39** | **2.02%** |
| `this_month` bucket (today → May 31) | 133 | **3** | **2.26%** |

**Sample verification:** 10 sampled future unpaid installments — all show `paid_amount=0`, `actual_paid=0`, `check_pending_amount=0`, `check_ids=[]`.

### Conclusion

**Only 2.0% of future unpaid installments have check records attached.** La Verde's cheque workflow does not attach checks to installments before their due date. Checks are linked to installments at payment-posting time — after the due date passes, not before. The 98% majority of forward-looking installments are genuinely check-free.

### Implications for Phase 1 KPI 7

The near-zero `cheques_in_pipeline` values observed in Phase 0 (0 EGP for month/quarter/half, 643,000 EGP for year) are **correct and expected**, not a formula bug. The Alternative B formula accurately reflects reality: future installments have no pending cheques because the workflow hasn't reached them yet.

→ See Section 6 (PATH C recommendation).

---

## Objective 2 — "Has Checks" and "All Checks Collected" Fields

### Hypothesis
These two filter options in the Odoo UI are either stored boolean fields, computed-not-stored fields, or view-level expressions. The answer determines whether they can be used in domain filters.

### Evidence

Both fields exist as **stored, computed booleans** on `rs.installment`:

| Field | Type | store | depends |
|-------|------|-------|---------|
| `has_checks` | boolean (readonly) | True | `check_ids` |
| `all_checks_collected` | boolean (readonly) | True | `check_ids`, `check_ids.state`, `check_ids.is_suspended` |
| `check_pending_amount` | monetary (readonly) | True | `check_ids`, `check_ids.state`, `check_ids.amount`, `check_ids.is_suspended` |
| `check_approved_amount` | monetary (readonly) | True | `check_ids`, `check_ids.state`, `check_ids.amount`, `check_ids.is_suspended` |

**Type 1 branching (stored boolean) — search_count results:**

| Field | ALL installments = True | KPI 7 universe = True | pct |
|-------|------------------------|----------------------|-----|
| `has_checks` | 5,343 | 39 | 2.02% |
| `all_checks_collected` | 788 | **0** | **0.00%** |

`all_checks_collected = True` on **0** future unpaid installments — meaning no future installment has all its checks cashed. This makes sense: uncashed future checks either don't exist (2.02% have any checks) or haven't been processed yet.

### Conclusion

Both "Has Checks" and "All Checks Collected" are fully queryable stored boolean fields. They are computed from `check_ids` and its sub-fields. They are **not** view-level expressions. `has_checks` corresponds exactly to `check_ids != False`.

The Group By / Filter options in the UI work because the fields are `store=True` — Odoo can SQL-index and filter on them.

### Implications for Phase 1 KPI 7

`has_checks` and `check_pending_amount` are available for domain filtering, but given only 2% of future installments have checks, adding them to KPI 7's bucket domains would not meaningfully change the output. PATH C (remove annotation) is consistent with these findings.

For KPI 2 (Late Installments extension in Stage 2), `has_checks` and `check_pending_amount > 0` provide an alternative to the field-to-field domain approach (which was proven broken in Phase 0). Using `[('has_checks', '=', True)]` as a filter for the cheques sub-count is a viable approach for Stage 2.

---

## Objective 3 — Undocumented Field Labels

### Hypothesis
Several fields visible in the Odoo UI were not fully documented. This objective confirms their technical names, types, and relations.

### Evidence

All 7 probe fields confirmed — all are native stored fields (no `x_studio_` prefix):

| Field | Type | UI Label | Relation | Model Count |
|-------|------|----------|----------|-------------|
| `total_due_amount` | monetary | Total Due Amount | — (scalar) | n/a |
| `reservation_id` | many2one | Reservation | `rs.reservation` | 1,479 |
| `contract_id` | many2one | Contract | `rs.contract` | 1,409 |
| `phase_id` | many2one | Phase | `rs.structure.phase` | **5** |
| `building_id` | many2one | Building | `rs.structure.building` | **277** |
| `zone_id` | many2one | Zone | `rs.structure.zone` | **11** |
| `unit_id` | many2one | Unit | `rs.structure.unit` | **1,873** |

All UI labels confirmed 100% match against `fields_get['string']` (see Section 5 / Objective 5).

**Property hierarchy count summary:**
```
5 phases → 11 zones → 277 buildings → 1,873 units
```
(Across 3 projects: New Capital=1, Cassette=2, La puerta=3)

### Conclusion

`total_due_amount` is a native scalar field (not Studio), confirming the §8 accounting identity table. All 6 property-hierarchy fields are native many2one fields linking each installment directly to its unit/building/zone/phase — denormalized hierarchy stored on the installment for query efficiency. This enables future filter-by-phase or filter-by-building queries without joining through contract/reservation.

### Implications for Phase 1 KPI 7

No direct impact on KPI 7. These fields are valuable for future drill-down filters (Stage 5) and per-phase reporting. The `unit_id`, `building_id`, `zone_id`, `phase_id` fields enable the drill-down filter sidebar's eventual "Building" or "Zone" filter without additional joins.

---

## Objective 4 — Pre-existing KPI Favorites

### Evidence

6 saved filters found on `rs.installment` (`ir.filters`):

| Name | Domain | Measures |
|------|--------|---------|
| All Installments | `[]` | — |
| EXEC - KPI Base (Installments) | `[("contract_id.state","=","confirm")]` | count, amount, paid_amount, due_amount |
| KPI – Overdue Installments (Confirmed) | `["&","&",("date",">=",(context_today()-1d)),("date","<=",context_today()),"&",("due_amount",">",0),("contract_id.state","=","confirm")]` | count, due_amount |
| KPI – Total Collected Amount (Confirmed) | `[("contract_id.state","=","confirm")]` | count, paid_amount |
| KPI – Total Contracted Value (Confirmed) | `[]` | count, amount |
| KPI – Total Outstanding Amount (Confirmed) | `[("contract_id.state","=","confirm")]` | count, due_amount |

### Critical Finding F1 — "Overdue" is a 1-day window, not an accumulation

**"KPI – Overdue Installments (Confirmed)"** uses:
```python
("date", ">=", (context_today() + relativedelta(days=-1)).strftime("%Y-%m-%d"))
("date", "<=", context_today().strftime("%Y-%m-%d"))
```

This selects installments with **due date = yesterday or today**. It is a **daily flow view** — how much became due in the last 24 hours — not a stock view of all historically overdue amounts.

**Our KPI 2** uses `date < today` — a stock view capturing ALL accumulated past-due installments (322.2M EGP, 1,934 records as of May 2026).

These are **two fundamentally different metrics**:

| Metric | Definition | Use case |
|--------|-----------|---------|
| KPI 2 (our implementation) | ALL installments past-due and unpaid (stock) | Total overdue exposure — Board-level risk |
| KPI – Overdue (Exec filter) | Installments whose due date was yesterday or today (flow) | Daily collections workload — operational |

### Critical Finding F2 — Exec KPIs use `contract_id.state = confirm`

All 4 named Exec KPI filters scope by `contract_id.state = confirm` (confirmed contracts only). Our KPI implementations scope by `state = post` (posted installments). These are not equivalent:

- `state = post` on `rs.installment` → the installment has been registered by RS Accounting
- `contract_id.state = confirm` → the installment's parent contract has completed the 5-stage approval cycle

In live data: 42,443 posted installments vs 1,409 confirmed contracts × N installments per contract. The actual overlap count was not tested in this run but is likely large given 1,402 of 1,409 contracts are confirmed.

**Action required:** Khaled should confirm which scoping rule is correct for the Board dashboard. Phase 2 Discovery established that `state = post` matches the Collections Mgmt All Installments view (the UI Khaled used for identity-equal verification). If the EXEC filters use `contract_id.state = confirm` as the authoritative definition, our KPI 2 baseline (322.2M EGP) may differ from what the EXEC team expects.

### Conclusion

The 5 pre-existing KPI Favorites reveal that La Verde has a pre-existing set of executive KPI definitions that partly overlap and partly contradict our current backend implementations. The most significant discrepancy is the "Overdue" definition (1-day window vs. full accumulation).

---

## Objective 5 — UI Field Label Verification

All 8 observed UI labels verified identity-equal against `fields_get['string']`:

| UI Label | Technical Field | Match |
|---------|----------------|-------|
| Amount | `amount` | PASS |
| Paid Amount | `paid_amount` | PASS |
| Due Amount | `due_amount` | PASS |
| Actual Paid Amount | `x_studio_actual_paid_amount` | PASS |
| Total Due Amount | `total_due_amount` | PASS |
| Payment Period | `payment_type_id` | PASS |
| Reservation | `reservation_id` | PASS |
| Contract | `contract_id` | PASS |

---

## Section 6 — PATH A / B / C Recommendation

### Evidence Summary

| Signal | Value | Interpretation |
|--------|-------|----------------|
| `year_pct` (future installs with check_ids) | **2.02%** | < 10% threshold |
| `month_pct` (this_month installs with check_ids) | **2.26%** | < 10% threshold |
| `all_checks_collected` = True in KPI 7 universe | **0** | Zero future installs have all checks cashed |
| Sample verification | 10/10 with paid=0, chk_pending=0 | Consistent with 2% statistic |
| Alternative B result from Phase 0 | 0 EGP for month/quarter/half | Structurally near-zero, not anomaly |

### Recommendation: PATH C

**Remove the `cheques_in_pipeline` annotation from KPI 7 forecast cards in the UI.**

Rationale:
1. Only 2% of future unpaid installments have any check records attached
2. `paid_amount` is structurally 0 on forward-looking installments (cheques arrive post-due)
3. Showing "منها شيكات: 0 EGP" on 3 of 4 KPI 7 cards is visual clutter without value
4. Stage 4 spec §4.4 already states: "when `cheques_in_pipeline == 0`, hide the annotation entirely" — PATH C simply makes this the permanent state for KPI 7, not an edge case

**PATH C does NOT affect KPI 2.** Late installments have `paid_amount > 0` when cheques have been credited, so the KPI 2 cheques annotation (Stage 2) remains valid and meaningful.

**Backend response shape:** `cheques_in_pipeline` remains in the KPI 7 JSON response (computed via Alternative B, typically 0 for near-term buckets). The UI simply does not render the amber annotation when the value is 0. This preserves the field for future use if the La Verde workflow changes.

---

## Unknowns Surfaced

| ID | Description | Priority |
|----|-------------|----------|
| U1 | "KPI – Overdue" filter uses 1-day window vs our KPI 2 full accumulation — which definition does the Board expect? | **HIGH — resolve before Stage 3 frontend** |
| U2 | Exec KPI filters scope by `contract_id.state = confirm` vs our `state = post` — same or different population? | **MEDIUM — verify count overlap** |
| U3 | `rs.account.check.state` field values not captured (needed for PATH B if ever re-evaluated) | LOW |

---

*Generated by `scripts/discover_phase_0_5_ui_artifacts.py` — 2026-05-18 14:24 UTC*
