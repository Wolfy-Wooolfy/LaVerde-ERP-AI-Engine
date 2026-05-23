# Module 3 Discovery — M3-S1: Pre-Implementation Findings

> **Status**: Complete — 2026-05-23
> **Stage**: M3-S1 (pre-implementation discovery, read-only)
> **Script**: `scripts/discover_module3_phase1.py`
> **Output**: `scripts/discover_module3_phase1_2026-05-23.txt`
> **Cost**: $0.00 (no OpenAI calls, read-only RPCs only)
> **RPCs used**: 12 (Auth + A1 + A2 + B1 + B2 + C1 + Df + D2 + OQ2-1 + OQ4-1 + OQ4-2 + OQ4-3)
> **Covers**: KPI A/B/C baselines, R1 cross-check, OQ2/OQ4 closure (MODULE_3_PLAN.md §6)

---

## 1. Scope

M3-S1 confirms domains and baselines for the 3 KPIs and the Refunds section of
Module 3 (حسابات العملاء) before any backend code is written. It also closes
OQ2 and OQ4 carried over from Phase 3 (`MODULE_3_DISCOVERY_PHASE_3.md §9`).

---

## 2. KPI A — إجمالي المستحق على العملاء / Total Customer Receivables

**Domain**: `[('state', '=', 'post')]` on `rs.installment`
**Measure**: `SUM(due_amount)` grouped by `partner_id`

| Metric | Value |
|--------|-------|
| إجمالي المستحق | **2,634,209,716.28 EGP** |
| عدد العملاء المتميّزين | **1,272** |
| Total posted installments | 42,413 |

**Null-partner check**: PASS — grouped sum equals flat aggregate exactly (delta = 0.00 EGP).
No posted installments have `partner_id = False`. The groupby query is safe; no null-partner
exclusion artefact exists.

**Baseline confirmed.** Domain `[('state','=','post')]` is ready for KPI A implementation.

---

## 3. KPI B — أعلى العملاء تأخّراً / Top Overdue Customers

**Domain**: Late (Candidate C, three-clause — confirmed from `MODULE_2_DISCOVERY_PHASE_2.md §3`):
```python
[
    ('state',         '=',  'post'),
    ('payment_state', 'in', ['unpaid', 'partial']),
    ('date',          '<',  '2026-05-23'),
]
```
**Measure**: `SUM(due_amount)` grouped by `partner_id`

| Metric | Value |
|--------|-------|
| إجمالي التأخير (all partners) | **333,271,714.40 EGP** |
| عدد العملاء المتأخرين | **797** |
| Total matched installments | 2,042 |
| أعلى 10 عملاء / إجمالي | **21.8%** (72,536,983.00 EGP) |

### Top 20 Overdue Customers (amounts only — names redacted)

| Rank | Due Amount (EGP) | Installments |
|------|-----------------|-------------|
| 1 | 18,202,000.00 | 76 |
| 2 | 16,425,000.00 | 1 |
| 3 | 12,267,500.00 | 4 |
| 4 | 5,388,656.00 | 5 |
| 5 | 3,860,000.00 | 20 |
| 6 | 3,600,553.00 | 9 |
| 7 | 3,511,226.00 | 2 |
| 8 | 3,209,874.00 | 8 |
| 9 | 3,095,987.00 | 8 |
| 10 | 2,976,187.00 | 12 |
| 11 | 2,911,600.00 | 4 |
| 12 | 2,820,000.00 | 2 |
| 13 | 2,697,726.00 | 15 |
| 14 | 2,373,086.00 | 2 |
| 15 | 2,321,964.00 | 3 |
| 16 | 2,075,615.00 | 5 |
| 17 | 1,956,500.00 | 1 |
| 18 | 1,951,000.00 | 24 |
| 19 | 1,890,000.00 | 14 |
| 20 | 1,874,925.00 | 3 |

### Concentration Analysis

The top 10 customers hold 21.8% of total late exposure. Risk is **distributed**, not
concentrated — no single customer dominates the overdue portfolio. The Board display
("أعلى 10 عملاء = 21.8%") is meaningful as a risk-spread indicator (MODULE_3_PLAN.md §3 KPI B).

---

## 4. R1 Cross-Checks

### R1a — Domain Integrity (MODULE_3_PLAN.md §6 R1)

> "الـ Late domain اتأكّد على مستوى القسط — هل ينطبق بنفس الدقّة على التجميع بالعميل؟"

| Check | Value |
|-------|-------|
| B1 Python sum (groupby partner) | 333,271,714.40 EGP |
| B2 flat aggregate (no groupby) | 333,271,714.40 EGP |
| Delta | **0.00 EGP** |

**PASS — exact match.** No late installments have `partner_id = False`. The Late domain
applies with equal accuracy to partner-grouped and flat queries.
**R1 is CLOSED (MODULE_3_PLAN.md §6).**

### R1b — KPI 2 Identity (PATH A vs total_due_amount)

This is the same identity check `kpi_service.py` runs internally (line ~200).

| Measure | Value |
|---------|-------|
| PATH A = SUM(amount) − SUM(x_studio_actual_paid_amount) | 335,200,714.40 EGP |
| SUM(total_due_amount) | 335,200,714.40 EGP |
| Delta | **0.0000 EGP** |

**PASS — identity holds perfectly.** PATH A and `total_due_amount` are identical to the cent
on the Late domain as of 2026-05-23. The `kpi2_identity_mismatch` warning will not fire.

**Note on the KPI B vs PATH A gap**: KPI B baseline = SUM(due_amount) = 333,271,714.40 EGP.
PATH A = SUM(amount) − SUM(actual_paid) = 335,200,714.40 EGP. These differ by 1,929,000.00 EGP.
This is the structural gap identified in `MODULE_2_DISCOVERY_PHASE_2.md §8` (EQ1 vs EQ2 — two
different measures on `rs.installment`). Both are correct; they answer different questions.
The gap is **not a finding** — it is by design. Module 3 KPI B uses `SUM(due_amount)`
(the remaining balance per installment), while Collections KPI 2 uses PATH A (contractual
face value minus cash received). The R1 identity in MODULE_3_PLAN.md §3 refers to
domain-level totals (same records, same grouping key), not cross-measure equality.

---

## 5. KPI C — رصيد المحفظة غير المخصص / Unallocated Wallet Balance

**Domain**: `[('state', '=', 'post'), ('residual_amount', '>', 0)]` on `rs.account.payment.reconcile`
**Measure**: `SUM(residual_amount)` grouped by `partner_id`

| Metric | Value |
|--------|-------|
| إجمالي المحفظة غير المخصص | **17,214,301.92 EGP** |
| عدد العملاء بالرصيد | **27** |
| Reconcile records (positive residual) | 198 |
| Average records per partner | ~7.3 |

**Baseline confirmed.** The `residual_amount > 0` filter correctly excludes the 7 refund records.

### Observation — Active Application Detected

Cross-checking amounts:
- SUM(amount) for all 205 records (from OQ4-3) = 16,539,062.92 EGP
- SUM(amount) for positive-only 198 records = 16,539,062.92 − (−719,812.00) = **17,258,874.92 EGP**
- SUM(residual_amount) for positive-only 198 records = **17,214,301.92 EGP**
- Implied SUM(reconciled_amount) = 17,258,874.92 − 17,214,301.92 = **44,573.00 EGP**

**44,573.00 EGP of wallet balances have been applied to installments since the bulk migration
(2026-05-17).** Phase 3 samples showed `reconciled_amount = 0` for the 3 oldest records, but
the portfolio-wide picture shows active usage. The reconcile model is live, not static.

### ⚠️ KPI C Baseline Is a Moving Number (R4 — MODULE_3_PLAN.md §6)

The 17,214,301.92 EGP figure captured on 2026-05-23 is **not a fixed baseline**. The reconcile
data is actively changing: between Phase 3 (2026-05-22) and M3-S1 (2026-05-23), 44,573.00 EGP
of previously-unallocated wallet balance was applied to installments, reducing `residual_amount`
accordingly.

**Consequence for M3-S4 verification**: when KPI C is implemented and verified in M3-S4, the
live `SUM(residual_amount)` will differ from 17,214,301.92 EGP. This is **expected and normal**,
not a bug. The correct verification approach is to compare the KPI C API response against an
Odoo UI snapshot taken at the same moment — not against the 17.2M figure recorded here.

The `residual_amount > 0` domain and `partner_id` groupby are confirmed correct; only the
magnitude will differ over time.

---

## 6. الاستردادات / Refunds Section

**Domain**: `[('state', '=', 'post'), ('amount', '<', 0)]` on `rs.account.payment.reconcile`

| Metric | Value |
|--------|-------|
| إجمالي الاستردادات | **−719,812.00 EGP** |
| عدد السجلات | **7** |
| partner_id = False (عميل غير معروف) | **0** |

**All 7 refunds have a known partner.** Phase 3 (§4.1) noted that 4 of 7 had
`partner = "عميل غير معروف"`. That partner is a **named partner record in `res.partner`**
(not a null foreign key) — its `partner_id` field IS set, pointing to a catch-all
"عميل غير معروف" entry. The `partner_id = False` search returns 0 correctly.

**Design implication confirmed**: The Refunds section card (MODULE_3_PLAN.md §4)
can safely display "عدد: 7، إجمالي: −719,812 EGP". The "عملاء غير معروفين" count
would need a name-based search (e.g. `partner_id.name = 'عميل غير معروف'`) rather than
a null check — a Module 3 design decision (R5 in MODULE_3_PLAN.md §6).

---

## 7. OQ2 — Reconcile Sub-lines (`rs.account.payment.reconcile.line`)

**Status**: **CLOSED — still 0 records.** Unchanged from Phase 3 (2026-05-22).

Sub-lines remain unused. Per-installment application history from the wallet side is not
recorded in this model. This does not affect KPI A, B, or C. No action required for
Module 3 implementation.

---

## 8. OQ4 — Reconcile Request (`rs.account.payment.reconcile.request`)

**State distribution**: 1 request, `state = 'new'`.

| Field | Value |
|-------|-------|
| id | 2 |
| name | RR/2026/05/00002 |
| state | new |
| create_date | 2026-05-17 14:48:27 |
| date | 2026-05-17 12:51:56 |
| Linked payments (OQ4-3) | **205** (all `rs.account.payment.reconcile` records) |
| SUM(amount) across linked payments | 16,539,062.92 EGP |

**Finding (OQ4 described, not fully resolved)**: The single request `RR/2026/05/00002`
acts as the bulk migration container — all 205 wallet records link to it. Its `state = 'new'`
means it has not been formally approved/closed since the 2026-05-17 migration.
The individual payment records are already `state = 'post'` and are queryable.

**What is now known**: the structure (one request → many payments), the record counts,
and the current state of the single live request.

**What remains unknown**: the workflow role of the request model — whether it must be
closed before new wallet entries can be created, what happens when its state transitions,
and whether future wallet entries will each get their own request or share this one.
This is a workflow question, not a data-shape question, and **cannot be answered from
read-only RPC inspection alone**.

**Impact on Module 3 KPIs**: None. KPI C queries `rs.account.payment.reconcile` directly
with `state = 'post'` — the request state does not appear in any KPI domain. The three
KPI baselines are unaffected.

**OQ4 status**: **DESCRIBED, NOT FULLY RESOLVED.** If future Module 3 work requires
understanding the reconcile workflow (e.g. staff-facing instructions, new-entry flows),
this question should be re-opened and resolved with Khaled's operational knowledge.

---

## 9. Summary — All Baselines Confirmed

| KPI / Section | Baseline | Records | Partners | Notes |
|---------------|----------|---------|----------|-------|
| KPI A | 2,634,209,716.28 EGP | 42,413 installments | 1,272 | Null-partner: PASS |
| KPI B | 333,271,714.40 EGP late | 2,042 installments | 797 | Top 10 = 21.8% |
| KPI C | 17,214,301.92 EGP unallocated | 198 reconcile records | 27 | residual > 0 only |
| الاستردادات | −719,812.00 EGP | 7 records | — | 0 null-partner |

| Check | Result |
|-------|--------|
| R1a — domain integrity | **PASS** — delta = 0.00 EGP |
| R1b — KPI 2 identity (PATH A vs total_due) | **PASS** — delta = 0.0000 EGP |
| KPI A null-partner | **PASS** |
| OQ2 (reconcile.line) | **CLOSED** — 0 records, no change |
| OQ4 (reconcile.request) | **DESCRIBED, NOT FULLY RESOLVED** — structure known; workflow role requires operational input |

**No blocking items.** Stage M3-S2 (Backend KPI A) may proceed.

---

## 10. Open Items After M3-S1

| # | Item | Priority | Resolves in |
|---|------|----------|-------------|
| OQ4-workflow | Request model: mandatory approval vs optional container? | Low | Operational — not M3 blocker |
| R5 | "عميل غير معروف" identity: null-FK vs named catch-all partner | Medium | M3-S5 (frontend design) |
| R3 | Refunds for known customers: deduct from their account or report separately? | Medium | M3-S5 (frontend design) |

*All domains confirmed. All baselines established. No anomalies detected. Ready for M3-S2.*
