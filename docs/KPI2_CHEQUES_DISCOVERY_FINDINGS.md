# KPI 2 Cheques Distribution — Mini-Discovery Findings

**Date:** 2026-05-19  
**Run timestamp (UTC):** 2026-05-19 09:43:53 UTC  
**Run timestamp (Cairo):** 2026-05-19 12:43:53 EEST  
**Script:** `scripts/discover_kpi2_cheques.py`  
**Output file:** `scripts/discover_kpi2_cheques_output.txt`  
**Status:** DISCOVERY COMPLETE — PATH MIXED, awaiting Khaled decision

---

## Universe Summary

Late installment universe as of 2026-05-19 (Candidate C domain):

| Metric | Value |
|--------|-------|
| total_late_count | 2,006 records |
| SUM(amount) | 388,979,286.00 EGP |
| SUM(due_amount) | 326,551,703.40 EGP |
| SUM(paid_amount) | 62,427,582.60 EGP |
| SUM(actual_paid_amount) | 60,498,582.60 EGP |
| derived_cheques_in_pipeline | **1,929,000.00 EGP** |
| cheques_in_pipeline as % of amount | 0.50% |

> **Comparison vs baseline (2026-05-14 snapshot from MODULE_2_BUSINESS_CONTEXT §9):**
> The baseline had Paid Amount = Actual Paid Amount on the late set (cheques_in_pipeline = 0).
> Today's gap of 1,929,000 EGP represents cheques posted against late installments in the 5-day window.
> Apparent daily rate: ~385,800 EGP/day (illustrative only — not predictive; single observation window).

---

## Cheques Distribution Evidence

| Metric | Value |
|--------|-------|
| total_late_count | 2,006 |
| late_with_checks (has_checks=True) | **181** |
| late_with_checks_pct | **9.02%** |
| derived_cheques_in_pipeline | **1,929,000.00 EGP** |
| stored_check_pending_amount | 1,929,000.00 EGP |
| stored vs derived delta | **0.00 EGP** (exact identity) |

---

## Stored vs Derived Parity

Two cross-checks were performed in Section 4:

**Check A — Combined vs Standalone read_group:**  
The combined `read_group` (RPC 2, aggregating all 5 monetary fields at once) and a standalone `read_group` for `check_pending_amount` alone both returned **1,929,000.00 EGP**.  
Delta = 0.0000 EGP → **PASS — Combined read_group is trustworthy.**

**Check B — Derived formula vs Odoo computed field:**  
`max(SUM(paid_amount) − SUM(actual_paid_amount), 0)` = 1,929,000.00 EGP  
`SUM(check_pending_amount)` (Odoo computed, stored) = 1,929,000.00 EGP  
Delta = 0.0000 EGP → **PASS — Service formula is consistent with Odoo computed field.**

This confirms the KPI 2 Alternative B derivation formula is correct. No service code changes required for the formula.

---

## Section 3 Anomaly — Sample Inspection

The 5 sampled records with `has_checks=True` all showed `check_ids=[]` (empty list) and `check_pending_amount=0` in `search_read`. Yet the aggregate `SUM(check_pending_amount) = 1,929,000 EGP` is non-zero.

Likely explanations (non-exhaustive):

1. **The 5 samples are among the "historical" 181** — installments where `has_checks` was set True at some point (checks were attached) but the checks have since been collected or cancelled, leaving `check_pending_amount=0`. The `has_checks` stored boolean did not recompute to False after the check lifecycle completed.
2. **The non-zero pipeline is concentrated in a subset** of the 181 — possibly 5–20 installments with large individual check amounts, not in the first 5 results returned by Odoo's default sort.

This anomaly does not affect the PATH recommendation (aggregate parity is exact) but is worth monitoring if Stage 2 proceeds.

---

## PATH Recommendation

Thresholds applied:

| Signal | Value | PATH A threshold | PATH C threshold | Assessment |
|--------|-------|-----------------|-----------------|------------|
| late_with_checks_pct | 9.02% | ≥ 10% | < 5% | **Gray zone** (5%–10%) |
| derived_cheques_in_pipeline | 1,929,000 EGP | ≥ 10M EGP | < 5M EGP | **Clear PATH C** (< 5M) |

**Result: PATH MIXED**

Neither PATH A nor PATH C is fully triggered:
- Count signal (9.02%) falls just below the 10% PATH A threshold — borderline.
- Amount signal (1.929M EGP) is well below the 5M EGP PATH C threshold — clearly small.

The two signals point in slightly different directions. Khaled must decide.

**Arguments for PATH C (skip cheques annotation):**
- 1.929M EGP is only 0.50% of the late portfolio (389M EGP). Negligible in a 326M EGP due-amount view.
- Amount is 5.2× below the PATH C threshold (5M EGP).
- The workflow rate (~386K EGP/day) is low enough that the pipeline is unlikely to reach 5M EGP in the near term.

**Arguments for PATH A (keep cheques annotation):**
- 9.02% of late installments carry check flags — nearly 1 in 10. Meaningful from a count perspective.
- The count is approaching the 10% threshold and may cross it as more cheques are posted.

---

## Khaled Cross-Check Sheet

Verify `derived_cheques_in_pipeline = 1,929,000.00 EGP` directly in the Odoo UI:

| Step | Action |
|------|--------|
| 1 | Open: Collections Mgmt → All Installments |
| 2 | Filter: State = **Posted** |
| 3 | Filter: Payment Status = **Unpaid** + **Partially Paid** |
| 4 | Filter: Date = **before 2026-05-19** |
| 5 | Filter: Has Checks = **True** |
| 6 | Switch to **Pivot** view |
| 7 | Add measures: **Paid Amount** + **Actual Paid Amount** |
| 8 | Compute: Paid Amount − Actual Paid Amount |

**Expected result: 1,929,000.00 EGP ± 1 EGP**

> Note: The UI snapshot will differ slightly if new cheques were posted between the script run (12:43 Cairo) and your check. Differences > 1,000 EGP should be investigated.

---

## Implications for Stage 2 Implementation Path

| Decision | Consequence |
|----------|-------------|
| **PATH A (Khaled confirms annotation)** | Stage 2 extends KPI 2 with cheques annotation as originally planned. Add `cheques_in_pipeline` and `late_with_checks_count` fields to the KPI 2 response. |
| **PATH C (Khaled applies PATH C here too)** | Skip cheques extension. KPI 2 remains as-is. Document decision in Session 10. Revisit if the 9.02% count crosses 10% in a future discovery run. |

No service code changes in this session. Stage 2 implementation is gated on Khaled's PATH decision.
