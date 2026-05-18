# KPI 7 — Expected Collections Forecast — Phase 0 Discovery Findings

**Run date (Cairo):** 2026-05-18  
**Script:** `scripts/discover_kpi7.py`  
**Output file:** `scripts/discover_kpi7_output.txt`  
**Model:** `rs.installment` (42,970+ records)  
**Status:** PHASE 0 COMPLETE — awaiting Khaled cross-check and approval

---

## D0.1 — Context and Objective

KPI 7 is a forward-looking cash flow forecast across 4 nested calendar buckets: `this_month`, `this_quarter`, `this_half`, `this_year`. Each bucket shows:
- Total expected installment amount (unpaid/partial, posted, date ≥ today)
- Number of installment records
- Cheques-in-pipeline figure (paid_amount credited but not yet bank-cleared)

Phase 0 discovers the 3 unknowns required before writing any Phase 1 service code:
1. Field type of `rs.installment.date` → plain ISO string vs UTC-converted datetime
2. Whether Odoo accepts field-to-field domain comparison → affects cheques formula
3. Bucket boundary arithmetic and live record counts → cross-check baseline

---

## D0.2 — Field-to-Field Domain Comparison (UNKNOWN U1)

### What was tested

The spec's `cheques_drill_down_domain` includes the triplet `('paid_amount', '>', 'x_studio_actual_paid_amount')` — using a field name string as the right-hand operand. This test verified whether live Odoo accepts that syntax.

**Control test (Test 1b):**
```
domain: [('state', '=', 'post'), ('paid_amount', '>', 0)]
result: 29,931 records  (125 ms)  ← baseline confirmed live
```

**Field-to-field test (Test 1a):**
```
domain: [('state', '=', 'post'), ('paid_amount', '>', 'x_studio_actual_paid_amount')]
result: EXCEPTION raised (93 ms)
```

### Raw Odoo exception

```
builtins.ValueError: could not convert string to float: 'x_studio_actual_paid_amount'
```

Root cause (from traceback): Odoo's `paid_amount` field is type `Float`. When evaluating a domain condition, `odoo/fields.py:1775` calls `float(value or 0.0)` on the right-hand operand. When that operand is a field-name string, `float('x_studio_actual_paid_amount')` raises `ValueError`.

### Conclusion

**Field-to-field Odoo domain comparison is BROKEN for float fields.** The spec's literal `cheques_drill_down_domain` cannot be used as-is in live Odoo.

### Phase 1 approach — Alternative B (RECOMMENDED)

Use `read_group` on the bucket domain with fields `['paid_amount', 'x_studio_actual_paid_amount']`:

```python
cheques_in_pipeline = max(SUM(paid_amount) - SUM(x_studio_actual_paid_amount), 0)
```

- Matches KPI 3's portfolio-wide formula (Decision 4.5)
- 1 RPC per bucket (8 total for all 4 buckets — 4 for amounts, 4 for cheques, or combined)
- `cheques_record_count` is unavailable via this approach → returned as `null`
- Per Decision 4.4: if net is negative (data anomaly), return as-is with `data_quality_warning`

Alternative A (Python-side filter): `search_read` + Python sum — exact `cheques_record_count` available, but data transfer scales with bucket size. Rejected for Phase 1 in favour of consistency with KPI 3.

---

## D0.3 — rs.installment.date Field Type Confirmation

```
Model   : rs.installment
Field   : date
Label   : Date
Type    : date        ← confirmed plain date, NOT datetime
Required: True
RPC time: 797 ms
```

**[PASS]** Field type is `date`.

**Consequences for Phase 1:**
- Bucket boundary domains use plain ISO date strings: `('date', '>=', '2026-05-18')`
- `_tz_period_bounds()` (UTC conversion for `datetime` fields) is **NOT needed** for KPI 7
- Africa/Cairo timezone (`ZoneInfo("Africa/Cairo")`) is used **only** to compute "today" — no UTC conversion of domain values
- Decision 5.9 (UTC boundaries for datetime) does **not** apply to KPI 7

---

## D0.4 — Bucket Boundary Arithmetic and Live Counts

**Today (Cairo):** 2026-05-18

Bucket end dates computed by `_compute_bucket_ends()` via `calendar.monthrange`:

| Bucket | Start | End |
|--------|-------|-----|
| `this_month` | 2026-05-18 | 2026-05-31 |
| `this_quarter` | 2026-05-18 | 2026-06-30 |
| `this_half` | 2026-05-18 | 2026-06-30 |
| `this_year` | 2026-05-18 | 2026-12-31 |

> **Nesting collapse note:** In May 2026, `this_quarter` (Q2) and `this_half` (H1) both end Jun 30. This is correct — Q2 = Apr–Jun, H1 = Jan–Jun. Both buckets will return identical Odoo UI counts. This is expected behaviour, not a bug.

### Domain used per bucket

```python
[
    ('state', '=', 'post'),
    ('payment_state', 'in', ['unpaid', 'partial']),
    ('date', '>=', '<today_iso>'),
    ('date', '<=', '<bucket_end_iso>'),
]
```

### Live counts (via `read_group`, fields `['paid_amount', 'due_amount']`)

| Bucket | End | Records | SUM(amount) EGP | SUM(due_amt) EGP |
|--------|-----|---------|-----------------|------------------|
| `this_month` | 2026-05-31 | 133 | 22,719,871.00 | 22,693,463.00 |
| `this_quarter` | 2026-06-30 | 355 | 55,527,209.00 | 55,459,801.00 |
| `this_half` | 2026-06-30 | 355 | 55,527,209.00 | 55,459,801.00 |
| `this_year` | 2026-12-31 | 1,934 | 337,946,411.00 | 337,223,075.00 |

**Nesting invariant check (month ≤ quarter ≤ half ≤ year):** [PASS] all three pairs verified.

---

## D0.5 — KPI 2 / KPI 7 Mutual Exclusivity (Section 3b)

KPI 2 (Late Installments) uses `date < today`; KPI 7 uses `date >= today`. These are mutually exclusive by construction. The impossible intersection domain must return 0 records.

```
domain: [('state','=','post'), ('payment_state','in',['unpaid','partial']),
         ('date','<','2026-05-18'), ('date','>=','2026-05-18')]
result: 0 records  (93 ms)
```

**[PASS]** KPI 2 ∩ KPI 7 = ∅ confirmed.

---

## D0.6 — Cheques-in-Pipeline Baseline per Bucket (Section 4b)

Formula (Alternative B): `cheques_in_pipeline = SUM(paid_amount) − SUM(x_studio_actual_paid_amount)`

| Bucket | SUM(paid_amount) | SUM(actual_paid) | Cheques EGP | % of Amount |
|--------|-----------------|------------------|-------------|-------------|
| `this_month` | 26,408.00 | 26,408.00 | **0.00** | 0.00% |
| `this_quarter` | 67,408.00 | 67,408.00 | **0.00** | 0.00% |
| `this_half` | 67,408.00 | 67,408.00 | **0.00** | 0.00% |
| `this_year` | 723,336.00 | 80,336.00 | **643,000.00** | 0.19% |

Cheques in the near-term buckets (month, quarter, half) are zero — all paid amounts are fully bank-cleared. The full-year bucket has 643,000 EGP in uncashed cheques, concentrated in the Jul–Dec 2026 portion.

---

## Unknowns and Flags

| ID | Status | Description | Phase 1 action |
|----|--------|-------------|----------------|
| U1 | [FLAG] | Field-to-field Odoo domain `('paid_amount', '>', 'x_studio_actual_paid_amount')` raises `ValueError` for float fields | Use Alternative B (read_group net formula); `cheques_record_count` = null |

---

## Phase 1 Implementation Decisions (pre-approved by findings)

| Decision | Value | Basis |
|----------|-------|-------|
| Domain date format | Plain ISO string (`'YYYY-MM-DD'`) | D0.3: field type = `date` |
| Timezone usage | Cairo only for "today" computation | D0.3: no UTC conversion needed |
| Cheques formula | Alternative B: `max(SUM(paid) - SUM(actual), 0)` | U1: field-to-field broken |
| `cheques_record_count` | `null` (not available via Alt B) | U1 consequence |
| RPC count | 8 `read_group` calls (4 buckets × 2: amount + cheques) or combined | Alt B pattern |
| Cache key | `kpi:expected_forecast:<YYYY-MM-DD>` using Cairo local date | Existing cache pattern |
| Negative cheques | Return as-is + `data_quality_warning` | Decision 4.4 |

---

## Khaled Cross-Check Sheet

**Cross-check date:** 2026-05-18 (Cairo)  
**Cheques approach:** Alternative B (read_group net formula)  
**`cheques_record_count`:** N/A (not available via Alternative B)

Verify in Odoo UI: **Collections Mgmt → All Installments**  
Filters: **State = Posted AND Payment Status IN [Unpaid, Partially Paid]**  
Switch to **Pivot view**, measure = **Amount**  
Add date range filter per bucket below:

| Bucket | Start | End | Records | Amount EGP | Due Amt EGP | Cheques EGP | Chq Recs | Chq % |
|--------|-------|-----|---------|------------|-------------|-------------|----------|-------|
| `this_month` | 2026-05-18 | 2026-05-31 | 133 | 22,719,871.00 | 22,693,463.00 | 0.00 | N/A | 0.00% |
| `this_quarter` | 2026-05-18 | 2026-06-30 | 355 | 55,527,209.00 | 55,459,801.00 | 0.00 | N/A | 0.00% |
| `this_half` | 2026-05-18 | 2026-06-30 | 355 | 55,527,209.00 | 55,459,801.00 | 0.00 | N/A | 0.00% |
| `this_year` | 2026-05-18 | 2026-12-31 | 1,934 | 337,946,411.00 | 337,223,075.00 | 643,000.00 | N/A | 0.19% |

> **Note:** `this_quarter` and `this_half` are identical (nesting collapse in Q2/H1 of 2026). Odoo UI will return the same results for both — this is correct.

**Verification steps:**
1. Open: Collections Mgmt → All Installments
2. Filters: State = Posted **AND** Payment Status = Unpaid or Partially Paid
3. Switch to Pivot view, measure = Amount
4. For each bucket, add date filter and confirm record count + SUM(Amount)
5. If all buckets match (±1 EGP) → reply **"approved, proceed to Phase 1"**

---

*Generated by `scripts/discover_kpi7.py` — 2026-05-18 13:31 UTC*
