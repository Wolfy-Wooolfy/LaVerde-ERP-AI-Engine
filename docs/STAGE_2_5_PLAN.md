# Stage 2.5 — KPI 2 Redefinition Plan

**Status:** Tentative plan. Stage 2.5 will begin with a
Pre-Implementation Discovery script (per Decision 3.2) before
any service code change. Numbers in this document are baselines
from the Stage 3 smoke test review and may shift after discovery.

**Date drafted:** 2026-05-19
**Trigger decision:** Decision 11.13
**Reverses:** Decision 10.1
**Target checkpoint tag:** `checkpoint-C-stage2-5-kpi2-redefined`

## 1. Executive Summary

Stage 2.5 redefines KPI 2 (Late Uncollected) from
`Amount - paid_amount` to `Amount - actual_paid_amount`. This
brings the headline value from 326.4M EGP (current) to a target
of ~328.3M EGP (tentative, pending discovery), and adds an
annotation surfacing that 1.929M of that total is postdated
cheques received but not yet cleared. The change fully reverses
Decision 10.1 (PATH C) and adopts PATH A — show the cheques as
a categorically-distinct subset rather than suppress them as
"visual noise."

## 2. Business Rationale

### 2.1 Math derivation (smoke test 2026-05-19 baselines)

| Quantity | Value (EGP) | Definition |
|---|---|---|
| Total Amount (Late subset) | 388.8M | SUM of amount on Late records |
| Paid Amount | 62.4M | cash + cleared cheques + postdated cheques received |
| Actual Paid Amount | 60.5M | cash + cleared cheques only |
| Postdated cheques in pipeline | 1.9M | paid_amount - actual_paid_amount |
| Current KPI 2 (Decision 10.1 PATH C) | 326.4M | Amount - paid_amount |
| Tentative new KPI 2 (Decision 11.13 PATH A) | 328.3M | Amount - actual_paid_amount |

Note: these baselines are derived from the Stage 3 smoke test
display. Exact values pending Pre-Implementation Discovery
re-confirmation.

### 2.2 Why the change matters

Egyptian real estate uses postdated cheques as the primary
payment channel. A cheque received but not yet cleared is
fundamentally different from cash received — the cheque can
bounce, be cancelled, or be delayed by bank processing. Treating
"cheque received" as "paid" understates the at-risk portfolio
and creates a Chairman-level question we cannot answer:
"if 326M is the late number, why am I told customers owe 328M?"

The current formula collapses this categorical distinction.
The redefined formula preserves it and surfaces the 1.9M as an
explicit annotation.

### 2.3 Why "منهم" annotation is mathematically correct only with the new formula

Under the current formula, "منهم 1.9 مليون شيكات" would be a
false statement — those 1.9M EGP are NOT part of the 326.4M;
they were subtracted out. Under the new formula, the 1.9M IS
included in the 328.3M, making "منهم" (subset notation)
mathematically true.

### 2.4 Finding 8b — Structural Mismatch Risk (Phase 2 Discovery)

The Phase 2 Module 2 discovery established that
`x_studio_actual_paid_amount` does NOT satisfy
"Amount = actual_paid_amount + due_amount" on the full
portfolio. The Studio field appears to use different
aggregation scope, timing, or inclusion criteria.

**Hypothesis for Stage 2.5:** The mismatch occurs on
draft/cancelled installments in the full portfolio, but
DISAPPEARS on the Late subset (state='post', payment status
unpaid/partial).

**Why this hypothesis matters:** If true,
`x_studio_actual_paid_amount` is safe to use for KPI 2
redefinition. If false, the formula change is invalid and
Decision 11.13 must be re-evaluated.

**Verification:** Pre-Implementation Discovery script must
prove this hypothesis on the Late subset BEFORE any service
code change or Odoo UI work.

**If hypothesis fails:** Discovery script outputs a structured
report. Claude Code stops. Khaled and Claude Chat decide path
forward (could be: alternative field, different formula, or
confirm Decision 10.1 PATH C was correct after all).

## 3. Scope Breakdown (~4 hours, 9 sub-tasks)

| Task | Time | Owner |
|---|---|---|
| Pre-Implementation Discovery script (`scripts/discover_kpi2_redefinition.py`) | ~30 min | Claude Code |
| Backend KPI 2 service formula change | 30 min | Claude Code |
| Backend tests update (~12 tests) | 1 hour | Claude Code |
| `verify_kpi2_live.py` extension | 30 min | Claude Code |
| Odoo UI new view creation | 30 min | **Khaled** |
| Identity-equal re-verification | 30 min | Claude Code + Khaled |
| `_risk_card.html` annotation markup | 20 min | Claude Code |
| `renderSection2()` annotation logic | 20 min | Claude Code |
| Documentation (Session 12 decisions) | 30 min | Claude Code |

The Discovery script is mandatory and gates everything below it.

## 4. Pre-Implementation Discovery script — `scripts/discover_kpi2_redefinition.py`

Verify these 4 hypotheses on the Late subset ONLY (domain:
state='post', payment_state IN ['unpaid','partial'], date < today):

1. **H1 (EQ1):** On the Late subset,
   `SUM(amount) = SUM(paid_amount) + SUM(due_amount)` to the cent.
2. **H2 (EQ2):** On the Late subset,
   `SUM(amount) = SUM(actual_paid_amount) + SUM(?)` — identify
   what completes the equation. (Expected: `total_due_amount`.)
3. **H3 (cheques identity):** On the Late subset,
   `SUM(paid_amount) - SUM(actual_paid_amount) ≈ 1.929M EGP`
   (within tolerance), matching the cheques annotation value.
4. **H4 (record count invariance):** The record_count is the
   same (2,004) regardless of whether the formula uses
   `paid_amount` or `actual_paid_amount`. The redefinition is
   a VALUE change, not a domain change.

The script is read-only. Writes to stdout and optionally to
`logs/discover_kpi2_redefinition.log`. Does NOT modify any
service code, schema, or test.

**Pass criterion (all 4 must pass):** Stage 2.5 proceeds to
service code change.

**Fail criterion (any 1 fails):** Stop. Output structured
report. Khaled + Claude Chat re-evaluate Decision 11.13.

## 5. Khaled's Odoo UI requirement

Create a new Odoo view in the Collections module filtering by
`x_studio_actual_paid_amount` instead of `paid_amount`. This
view becomes the new ground truth for identity-equal
verification (was 326.4M, will be ~328.3M).

## 6. Identity-equal consequence

Stage 2 V5 verified 326,374,203.40 EGP against the original
Odoo view. Stage 2.5 V5 will verify ~328,303,000 EGP (exact
value TBD post-discovery) against the new Odoo view. This is
NOT a regression — it is the corrected ground truth per the
redefined formula.

## 7. Decision 10.1 reversal history

- **Original Decision 10.1 (Session 10, 2026-05-19):** PATH C
  applied to KPI 2 — suppress cheques annotation, keep 326.4M
  headline.
- **Rationale at the time:** 1.9M / 326.4M = 0.49%, "visual noise."
- **Trigger for reversal:** Khaled's Stage 3 smoke test review
  revealed semantic ambiguity. The 1.9M is categorically
  distinct (cheques received vs cash received) and risk-material
  regardless of percentage.
- **New decision (11.13 / Stage 2.5):** PATH A — redefine
  formula, show subset annotation.

## 8. Verification Checklist (post-implementation)

- [ ] Pre-Implementation Discovery script: all 4 hypotheses PASS
- [ ] Backend KPI 2 returns ~328.3M (or exact value per discovery)
- [ ] Backend tests pass (revised expected values)
- [ ] `verify_kpi2_live.py` PASSES identity-equal against the
      new Odoo view
- [ ] Frontend `_risk_card.html` shows annotation markup
- [ ] Browser visual: Section 2 shows new value + annotation line
- [ ] Documentation: Session 12 logged in
      `MODULE_2_IMPLEMENTATION_DECISIONS.md`
- [ ] `MODULE_2_BUSINESS_CONTEXT.md` §19 status note updated
      from "Tentative" to "Confirmed" with final value
- [ ] `MODULE_2_STAGE_TRACKER.md` Stage 2.5 row marked ✅ Closed
