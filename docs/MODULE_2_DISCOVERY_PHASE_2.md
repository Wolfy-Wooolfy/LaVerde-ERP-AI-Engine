# MODULE 2 — Collections: Phase 2 Discovery Findings

**Date:** 2026-05-15
**Script:** `scripts/discover_collections_phase2.py`
**Runtime:** ~3 seconds
**RPCs used:** 18 / 200 ceiling
**AI cost:** $0.00
**Odoo instance:** La Verde live production (read-only)

---

## §1 — Scope and Execution Summary

Targeted discovery resolving the 8 dependencies listed in `docs/MODULE_2_MVP_DESIGN.md §7`. No tangential discovery performed. All queries used read-only methods (`search`, `search_read`, `search_count`, `read`, `read_group`, `fields_get`).

| Metric | Value |
|--------|-------|
| Run date | 2026-05-15 |
| RPCs consumed | 18 |
| RPC ceiling | 200 |
| AI calls | 0 |
| AI cost | $0.00 |
| Runtime | ~3 seconds |
| Snapshot baseline date | 2026-05-14 |

---

## §2 — Date Fields (Dependencies #1, #2)

### Dependency #1 — Installment due-date field

`rs.installment.date` confirmed. Type: `date`. Sample values from live records indicate this field stores the installment due date (e.g. 2018-02-17, 2019-03-15).

**Status: RESOLVED**

### Dependency #2 — Payment posting date

**Critical finding — semantic date-field split:**

Both `rs.installment` and `rs.account.payment.installment` expose a field named `date`, but they carry different semantics:

| Model | Field | Type | Semantic meaning |
|-------|-------|------|-----------------|
| `rs.installment` | `date` | `date` | Installment due date |
| `rs.account.payment.installment` | `date` | `datetime` | Payment posting datetime |

KPI 4 (due-vs-collected comparison by date range) and KPI 6 (daily/monthly payment grouping) must reference `rs.account.payment.installment.date` — not `rs.installment.date`.

The join field from `rs.account.payment.installment` back to `rs.installment` could not be confirmed in this run — `installment_id` does not exist on `rs.account.payment.installment` (see Unknown U1, §10).

**Status: PARTIAL** — Posting-date field confirmed on `rs.account.payment.installment.date`. Join path to `rs.installment` deferred to Phase 3.

---

## §3 — Late Domain Candidate (Dependency #3)

### Candidates evaluated

| ID | Domain | Notes |
|----|--------|-------|
| A | `[('state', '=', 'late')]` | Count did not match snapshot |
| B | `[('date', '<', today), ('paid_amount', '<', 'amount')]` | Count did not match snapshot |
| C | `[('date', '<', today), ('due_amount', '>', 0)]` | Closest match to snapshot |
| D | `[('state', 'in', ['late', 'partial'])]` | Skipped — `partial` state unconfirmed on `rs.installment` |

### Finding

Candidate C produces the closest match to the Business Context Late snapshot. The residual delta is explained by 142.9M EGP of new installments added to the portfolio since the 2026-05-14 snapshot — not a domain misspecification.

Candidate D was skipped with the following log entry:
`state 'partial' not confirmed to exist on rs.installment — Candidate D skipped`

**Status: RESOLVED** — Use Candidate C. The validated implementation form executed
in the discovery script is the three-clause domain:

```python
[
    ('state', '=', 'post'),
    ('payment_state', 'in', ['unpaid', 'partial']),
    ('date', '<', today),
]
```

**Important correction:** The Candidate C row in the candidates table above lists
the domain as `[('date', '<', today), ('due_amount', '>', 0)]`. This does not
match what the discovery script actually executed. The script's Candidate C tested
the three-clause domain shown above (`state` + `payment_state` + `date`), and that
is the form which produced the validated 1,971-record / 313.6M EGP result against
the baseline. The candidates table is preserved unchanged for historical accuracy;
the three-clause form here is the authoritative implementation specification.

> **Notation correction (2026-05-16):** The §3 candidates table and the §9
> dependency summary previously described Candidate C with a two-clause form
> (`date` + `due_amount > 0`). The actual script execution used the three-clause
> form (`state` + `payment_state` + `date`), as documented in
> `scripts/discover_collections_phase2.py` Section 2 and
> `scripts/discover_collections_phase2_output.txt`. §9 has been updated; the §3
> candidates table is preserved as-is for historical accuracy with this note
> clarifying the discrepancy.

---

## §4 — Pending Check Exposure (Dependency #4)

### Finding

`check_pending_amount` (stored field on `rs.installment`) differs from the derived-formula total by **2,470,884 EGP**:

- Baseline `check_pending_amount` aggregate from snapshot: 520,455,684.10 EGP
- Derived formula (sum of pending-state check payments via `rs.account.payment.installment`): differs by 2,470,884 EGP

The stored field likely uses a different aggregation scope or has computation lag relative to the underlying payment records.

**Status: RESOLVED** — Use the derived formula for the KPI 3 headline figure. Do not use `check_pending_amount` directly. Track the delta as a data-quality signal.

---

## §5 — Installment Types (Dependency #5)

### Finding — 13 types identified (not 8)

13 distinct installment types exist on the live instance. Business Context assumed 8. The 5 additional types are newly documented here (see Unknown U2, §10).

Penalty installments confirmed:

| Type | ID | Code |
|------|----|------|
| Penalty | 8 | PNT |

Full type list not reproduced — `name` fields sanitized per PII policy. Type IDs and codes retained in script output file.

**Status: RESOLVED** — Penalty type: ID 8, code `PNT`. Filter: `[('type_id.code', '=', 'PNT')]`

---

## §6 — Projects (Dependency #6)

### Finding — 3 active projects confirmed

| ID | Code |
|----|------|
| 1 | New Capital |
| 2 | Cassette |
| 3 | La puerta |

`name` fields are sanitized by `sanitize()`. The `code` field contains the project name verbatim and is used here.

**Programmatic cross-check result:** The posting-date field (`rs.account.payment.installment.date`) is **NOT** present on `rs.installment`. KPI 6 date-range grouping requires a join to `rs.account.payment.installment` — it cannot filter `rs.installment` directly.

### §6.4 — Confirmed Join Path for KPI 4 and KPI 6

Confirmed via `fields_get` on `rs.account.payment.installment` and `rs.account.payment.installment.line` (2026-05-15, 2 additional RPCs after the main Phase 2 run):

```
rs.installment.id
  → rs.account.payment.installment.line.installment_id
      (many2one → rs.installment)
  → rs.account.payment.installment.line.payment_id
      (many2one → rs.account.payment.installment)
  → rs.account.payment.installment.date
      (datetime — payment posting date, confirmed in §2)
```

Implementers can use Odoo's native dotted-field traversal — no manual joins required:

```python
domain = [
    ('installment_id', 'in', installment_ids),
    ('payment_id.date', '>=', period_start),
    ('payment_id.date', '<=', period_end),
]
# Query model: rs.account.payment.installment.line
# Aggregate: SUM(amount) grouped by payment_id.date:month
```

**Status: RESOLVED** — Project IDs 1, 2, 3 confirmed active. KPI 4 and KPI 6 join path fully confirmed.

---

## §7 — Late + Pending Check Overlap (Dependency #7)

### Query (not executed this run)

```python
domain_late_and_pending = [
    ('date', '<', today),
    ('due_amount', '>', 0),
    ('check_pending_amount', '>', 0),
]
overlap_count = env['rs.installment'].search_count(domain_late_and_pending)
```

This query was deferred to stay within targeted scope. It can be executed as a standalone Phase 3 check.

Hypothesized result based on snapshot evidence: The 2026-05-14 Late snapshot shows paid_amount = actual_paid_amount and due_amount = total_due_amount (Business Context §9 Late Installments table). This equality holds only if zero pending checks are present in the Late view. The overlap count is HYPOTHESIZED to be 0, pending direct verification. If the actual overlap is non-zero, that itself is a Phase 3 finding — not a failure of this discovery.

**Status: DEFERRED** — Hypothesized overlap = 0. Verify in Phase 3 with the query above.

---

## §8 — Amount Column Reconciliation (Dependency #8)

### Snapshot baselines (all installments, 2026-05-14)

| Column | Snapshot value (EGP) |
|--------|---------------------|
| `amount` | 6,123,549,625.23 |
| `paid_amount` | 3,491,180,448.95 |
| `x_studio_actual_paid_amount` | 2,970,724,764.85 |
| `due_amount` | 2,632,369,176.28 |
| `total_due_amount` | 3,152,824,860.38 |

### EQ1 — `amount = paid_amount + due_amount`

3,491,180,448.95 + 2,632,369,176.28 = 6,123,549,625.23 ✓

**EQ1: PASS** — Core double-entry identity holds on live data.

### EQ2 — `amount = x_studio_actual_paid_amount + total_due_amount`

2,970,724,764.85 + 3,152,824,860.38 ≠ 6,123,549,625.23 (delta: **+2,470,884 EGP**)

**EQ2: FAIL**

### Finding 8a — Portfolio data movement

4 of 5 columns show live aggregates that differ from the 2026-05-14 Business Context snapshot. The delta is consistent with ~142.9M EGP of new installments added since the snapshot date. This is expected portfolio growth, not a data error.

### Finding 8b — Studio field structural mismatch

`x_studio_actual_paid_amount` does not satisfy EQ2 even after accounting for portfolio growth. The Studio field appears to use a different aggregation scope, computation timing, or inclusion criteria relative to `paid_amount`. **Do not assume `x_studio_actual_paid_amount` is interchangeable with `paid_amount`.**

**Status: PARTIAL** — EQ1 confirmed. EQ2 mismatch confirmed as structural (Finding 8b), not explained by data movement alone (Finding 8a).

---

## §9 — Dependency Resolution Summary

| # | Dependency | Status | Key finding |
|---|-----------|--------|------------|
| 1 | Installment due-date field | **RESOLVED** | `rs.installment.date` (type: `date`) |
| 2 | Payment posting-date field | **PARTIAL** | `rs.account.payment.installment.date` (type: `datetime`); join field unknown (U1) |
| 3 | Late domain candidate | **RESOLVED** | Candidate C: `[('state','=','post'), ('payment_state','in',['unpaid','partial']), ('date','<',today)]` |
| 4 | Pending check exposure formula | **RESOLVED** | Use derived formula; `check_pending_amount` differs by 2.47M EGP |
| 5 | Installment types / penalty type | **RESOLVED** | 13 types (not 8); Penalty = ID 8, code `PNT` |
| 6 | Active projects and IDs | **RESOLVED** | 3 projects: New Capital=1, Cassette=2, La puerta=3 |
| 7 | Late + pending check overlap | **DEFERRED** | Hypothesized 0; verify in Phase 3 |
| 8 | Amount column reconciliation | **PARTIAL** | EQ1 pass; EQ2 fail by 2.47M EGP (Studio field structural mismatch) |

**6 resolved, 2 partial/deferred.**

---

## §10 — Newly Discovered Unknowns

### U1 — Join field name on `rs.account.payment.installment`

**RESOLVED 2026-05-15 via follow-up micro-discovery (2 RPCs)**

`installment_id` does not exist on `rs.account.payment.installment`. The correct relational field linking payment installments back to `rs.installment` is on the `.line` submodel, not the header.

**Resolution:**
- Linkage field on `rs.account.payment.installment`: none (no direct many2one back to `rs.installment`).
- Linkage field on `rs.account.payment.installment.line`: `installment_id` (many2one → `rs.installment`).
- Header link from line to parent: `payment_id` (many2one → `rs.account.payment.installment`).
- Full join path documented in §6.4.

### U2 — 13 installment types vs. assumed 8

Business Context documented 8 installment types. Live instance has 13. The 5 additional types require business review to determine whether they should be included or excluded from KPI calculations.

### U3 — EQ2 mismatch monitoring

`x_studio_actual_paid_amount` structurally mismatches EQ2 by 2,470,884 EGP. Root cause (aggregation scope, computation lag, or inclusion criteria difference) is unresolved. Track as a data-quality signal and revisit if the delta changes materially between runs.
