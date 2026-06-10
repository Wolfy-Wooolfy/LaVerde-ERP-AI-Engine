# CA Drill-Down Anomaly Findings

**Session:** Nav Audit + Sidebar Active-State Fix + Customer Drill-down 500 Diagnosis  
**Date:** 2026-06-10  
**Script:** `scripts/diagnose_ca_drilldown_anomaly.py`  
**Status:** ✅ **RESOLVED 2026-06-10 (Session 18 / N2, Decision 18.2).** See
"Resolution" section at the bottom. The original diagnosis below is preserved
verbatim for the record.

---

## Issue Summary

`GET /api/v1/customer-accounts/customer/{partner_id}` returns HTTP 500 for at least
one customer. The service raises `AssertionError` when
`|late_due + future_due − all_posted_due| >= 1.0 EGP`, mapped to HTTP 500 by the
endpoint handler.

---

## Affected Partner (Primary)

| Field | Value |
|---|---|
| Name | يوسف بدر شرهان دخيل |
| `partner_id` | 62112 |
| `all_posted_due` | 4,559,557.00 EGP |
| `late_due` | 2,976,187.00 EGP |
| `future_due` | 1,583,820.00 EGP |
| `late + future` | 4,560,007.00 EGP |
| **delta** | **450.00 EGP** ← assertion fires |

Log confirmation (first occurrence today, `logs/app.log`):
```
2026-06-10 12:26:23 | ERROR | Integrity assertion FAILED (partner_id=62112):
  late(2976187.00) + future(1583820.00) = 4560007.00
  but all_posted_due = 4559557.00  delta=450.0000
```

A second partner (`partner_id=999`) has also been hitting the assertion since
2026-06-08 with delta=1000.00 EGP.

---

## Root Cause

### Anomaly Class (a) — confirmed

Installment id=66422 has `payment_state=paid` **and** `due_amount=-450.00 EGP`:

| id | date | type_id | state | payment_state | amount | paid_amount | actual_paid | due_amount |
|---|---|---|---|---|---|---|---|---|
| 66422 | 2024-04-21 | 3 | post | paid | 259,000.00 | 259,450.00 | 259,450.00 | **-450.00** |

**Mechanism:** The customer paid EGP 450 more than the installment amount
(overpayment/refund scenario). Odoo stores `due_amount = amount − paid_amount =
259,000 − 259,450 = -450`. The record's `payment_state` is correctly `paid`, so
the service's `unpaid_domain` (`payment_state IN ['unpaid','partial']`) **excludes**
it from `late_due` and `future_due`. But `base_all` (no payment_state filter) **includes**
it in `all_posted_due`, where its `-450` reduces the total.

Result: `late + future` counts 0 EGP for this record; `all_due` counts −450 EGP.
The difference is 450 EGP → assertion fires.

### Anomaly Class (b) — not present for this partner

No `unpaid/partial` installments with `date = False` (null). Section D count = 0.

---

## per-state Breakdown for Partner 62112

| payment_state | count | due_amount |
|---|---|---|
| unpaid | 15 | 4,241,860.00 EGP |
| partial | 3 | 318,147.00 EGP |
| paid | 20 | **-450.00 EGP** ← the anomaly |

---

## Portfolio-Wide Scan (all partners)

### Class (a): `payment_state NOT IN [unpaid,partial]` AND `due_amount != 0`

| payment_state | row count | sum(due_amount) |
|---|---|---|
| paid | 54 | **-382,183.00 EGP** |

- **Distinct affected partners: 45**
- All 54 rows have `payment_state=paid` with a **negative** `due_amount` —
  confirming these are all overpayment/refund scenarios, not data corruption.
- First 10 affected partner names:
  - احمد ابراهيم زيان (id=889308)
  - احمد زين عبدالونيس الشهيبي (id=890368)
  - احمد عبد السميع محمد حسنين (id=890829)
  - احمد عثمان احمد رمضان/ اشرف حامد كامل (id=890994)
  - احمد علي السيد اسماعيل (id=862348)
  - احمد محمد عبد الحليم القادوم (id=862727)
  - احمد محمد يوسف عبد الرحمن (id=672052)
  - احمد هاشم على ابو الهدى (id=862992)
  - اسماء عبدالله حسن (id=863877)
  - اسماء محمد شوقي السيد الجميلي (id=863888)

### Class (b): `payment_state IN [unpaid,partial]` AND `date = False`

- **Total matching rows: 0**
- **Distinct affected partners: 0**
- Null-date anomaly class does **not** exist in this dataset.

---

## Impact

All 45 partners with overpayment records will return HTTP 500 when their
drill-down is requested. Partners without overpayments are unaffected.

---

## Assertion Logic Analysis

The assertion in `drilldown_service.py` line 264–280 states:

> `late_due + future_due == SUM(due_amount for all posted installments)`
> 
> "This holds because paid installments have due_amount=0 by definition."

This assumption is **false** for overpayment cases. A paid installment where
`paid_amount > amount` gets `due_amount < 0` in Odoo — it is never zero.
The assertion comment says "paid installments have due_amount=0 by definition"
but Odoo's field semantics allow negative due_amount for overpayments.

---

## Fix Options (pending product decision — NOT implemented this session)

Two candidate approaches:

**Option A — Widen the assertion tolerance or remove it:**
Change `if _delta >= 1.0` to a warning-only log, or relax the tolerance to
accommodate overpayments. Risk: masks genuine data corruption.

**Option B — Fix the `all_due` aggregate to match the assertion's intent:**
Change `base_all` for the all_due read_group to also filter
`due_amount >= 0` (or `payment_state IN ['unpaid','partial','full_partial']`),
so that negative due_amount records (overpayments) are excluded from both
sides of the comparison. This preserves the assertion semantics.

**Option C — Clamp `due_amount` floor at 0.0 in the aggregate extraction:**
When extracting `all_due = float(all_row.get("due_amount") or 0.0)`, the
`read_group` sum already nets the negative. Instead, compute all_due as
`max(0, all_row["due_amount"])`. Simple but hides the overpayment in the UI.

The right fix depends on whether the UI should display overpayment credits
in the exposure section. This is a product decision.

---

## Log Evidence Summary

Partners hitting the assertion (from `logs/app.log` and `logs/errors.log`):

| partner_id | First seen | Last seen | delta | occurrence count |
|---|---|---|---|---|
| 999 | 2026-06-08 12:56 | 2026-06-10 13:18 | 1,000.00 EGP | 14 |
| 62112 (يوسف بدر شرهان دخيل) | 2026-06-10 12:26 | 2026-06-10 12:31 | 450.00 EGP | 6 |

---

## Endpoint Probe Note (Section G)

`GET /api/v1/customer-accounts/customer/62112` returned HTTP 401 during the
diagnostic run. The server was running (connection succeeded), but the endpoint
rejected Basic Auth credentials. The Section H log grep confirms the 500 has been
occurring via browser sessions (session-cookie auth). The 401 in section G does
not affect the diagnosis — root cause is fully established by Sections B, E, and H.

---

*Generated by `scripts/diagnose_ca_drilldown_anomaly.py` on 2026-06-10.*

---

## Resolution (Session 18 / N2 — 2026-06-10, Decision 18.2)

**Chosen design — none of Options A/B/C as written.** The overpayment is
converted into board-useful information instead of a crash or a masked figure:

1. Two new settled-row aggregates in `get_customer_drilldown` (RPC count
   6 → 8), split by `due_amount` sign so a positive settled residual (graver
   anomaly — understated exposure) can never net against the credit:
   `neg_sum`/`neg_count` (due < 0) and `pos_sum`/`pos_count` (due > 0).
2. Identity check v2: `late + future + neg_sum + pos_sum ≈ all_posted_due`
   (|delta| < 1.0 EGP). For partner 62112:
   4,560,007 + (−450) + 0 = 4,559,557 ✓.
3. On identity breach: `logger.error` + `meta.data_quality_warning =
   "exposure_identity_mismatch"` — **no AssertionError, no HTTP 500**.
   A positive settled residual alone sets `"settled_positive_residual"`.
   The R10 read-only assertion stays fail-loud.
4. New response fields: `exposure.overpaid_credit_egp` (= −neg_sum, 450.00 for
   62112), `exposure.overpaid_record_count` (= neg_count),
   `meta.data_quality_warning` (str | null). `total_due_egp` keeps its meaning
   (late + future) — the credit is a separate figure.
5. UI: emerald "رصيد مدفوع بالزيادة / Overpaid Credit" strip in the drill-down
   exposure section (only when credit > 0) + amber non-blocking data-quality
   note when the warning is set.

**Script-auth side issue (Section G's 401) — also resolved (Decision 18.1):**
the app is session-cookie-only post-A2; `scripts/_lib/api_session.py` performs
POST /login (303 + cookie) once per process. Section G migrated; re-run shows
HTTP 200. 17 other verify_* scripts remain on the dead Basic-auth pattern —
deferred as Stage Tracker item D-8.

**Verification (scripts/verify_ca_drilldown_fix_live.py, Decision 6.4 ritual
first): 13/13 PASS.**

| Check | Result |
|---|---|
| Partner 62112 HTTP status | 200 (was 500) |
| `overpaid_credit_egp` | 450.00 (± 0.01 vs direct −neg_sum) |
| `overpaid_record_count` | 1 (matches direct count) |
| `data_quality_warning` | null |
| Identity (triple agreement) | 2,976,187 + 1,583,820 + (−450) + 0 = 4,559,557 = direct all_posted_due, delta 0.0000 |
| Affected partner 889308 | HTTP 200, credit 1.00 ✓ |
| Affected partner 890368 | HTTP 200, credit 4.00 ✓ |
| Unaffected partner 643138 | HTTP 200, credit 0.0, warning null |
| Diagnose Section G re-run | HTTP 200 via session login (401 gone) |

Unit suite: 86/86 customer_accounts tests pass (identity test rewritten to
warn-not-raise; 5 new tests). Note: `partner_id=999` in the old log evidence
was the unit-test mock partner leaking into logs during test runs, not a real
second live partner.

**Commits:** `e4f4a12` (C1) · `9c3848b` (C2) · `73eda07` (C3) · C4 (verify
script + docs). Decisions log: Session 18, Decisions 18.1 + 18.2.
