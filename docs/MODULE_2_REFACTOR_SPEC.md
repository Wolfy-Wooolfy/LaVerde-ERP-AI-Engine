# Module 2 — Collections Dashboard Refactor Specification

> **Status:** Draft — Pending Khaled approval before Stage 1 begins
> **Version:** 1.1
> **Date:** 2026-05-18
> **Author:** Khaled (Product) + Claude Chat (CTO/Architect)
> **Prerequisite Tags:** `checkpoint-A-D1-complete`, `checkpoint-B-D2-complete`
> **Supersedes Sections:** `MODULE_2_MVP_DESIGN.md` §3.2 (KPI 3 spec), §3.3 (Layout)
> **Parent Documents:**
>   - `docs/MODULE_2_BUSINESS_CONTEXT.md`
>   - `docs/MODULE_2_MVP_DESIGN.md`
>   - `docs/MODULE_2_IMPLEMENTATION_DECISIONS.md`

---

> ⚠️ **Cross-check notice (2026-05-19) — this document is partially stale**
>
> Decisions 11.13 through 11.17 in `docs/MODULE_2_IMPLEMENTATION_DECISIONS.md` Session 11 override portions of this spec. Most notably:
>
> - **§7.6 / Decision 10.1 (PATH C on KPI 2)** is reversed by **Decision 11.13** (PATH A — KPI 2 formula will be redefined from `Amount - paid_amount` to `Amount - actual_paid_amount` in Stage 2.5; the cheques amount becomes a subset annotation on the new total).
> - **§9 Stage sequence** is amended to insert **Stage 2.5** between Stages 3 and 4 (see `docs/STAGE_2_5_PLAN.md`).
> - **§9 Stage 5 deliverables** now include the deferred KPI 7 `cheques_record_count` per **Decision 11.14**.
>
> Treat this spec as the architectural baseline, but always cross-check `MODULE_2_IMPLEMENTATION_DECISIONS.md` Session 11+ and `docs/MODULE_2_STAGE_TRACKER.md` before acting on any specific section. A full spec refresh (v1.2) is scheduled for the post-Stage-2.5 documentation pass.

## Changelog
- **v1.2 (2026-06-11)** — KPI 7 v2 per **Decision 19.1** (Session 19 / N3): §4 v1 definition (forward-looking `[today, period_end]` windows, unpaid/partial filter) is **superseded** by full-period `[period_start, period_end]` three-segment buckets (period_total / collected_cleared / cheques_pending / remaining). Section title becomes «مستحقات وتحصيل الفترات الحالية» / "Dues & Collections — Current Periods". §4 amendment banner added; v1 text retained for history.
- **v1.1 (2026-05-18)** — Applied PATH C from Phase 0.5 findings. KPI 7 cheques annotation removed from frontend cards (backend response unchanged). Section 7.1 layout updated. Sections 4.5, 4.6, 7.1, 7.4, 7.6, 8.2, 8.5 amended. §11 open questions resolved. §16 cross-references added.
- **v1.0 (2026-05-18)** — Initial draft, approved by Khaled.

---

## 1. Executive Summary

### What changed

During Session 8 Checkpoint B browser verification, Khaled raised a fundamental concern about the existing KPI 3 (Pending Check Exposure). The 518.2M EGP figure was technically correct but **semantically misleading** for the Board audience because it conflated three distinct risk profiles:

- Overdue cheques (real risk)
- Currently-due cheques (Treasury workload)
- Postdated cheques for future years (no current risk — normal payment plan)

A single number representing this mixture would lead a Chairman to a wrong panic conclusion. Discussing alternative framings (rename, aging hint, time buckets) led to a deeper realization: the dashboard was structured around the wrong axis.

### The core insight

The existing dashboard mixed two organizing axes:

| KPI | Axis |
|---|---|
| KPI 2 (Late Uncollected) | Time — installments past due |
| KPI 3 (Pending Cheques) | Payment method — cheques not yet cashed |
| KPI 4 (Collection Rate) | Performance — rate over period |
| KPI 6 (6-Month Trend) | Time — historical |

Mixing these axes forces the Board to context-switch between "what's late?" and "what's in the cheque pipeline?" — two questions with overlapping but non-identical answer sets.

### The decision

**Reorganize the dashboard around a single coherent axis: installment lifecycle (time-based).** Cheques become contextual annotation inside each time bucket, not a standalone KPI.

The new mental model for the Board:

> "Show me what's already late, what's due in the near future, and how much of each is dependent on cheques that haven't cleared."

### Net structural changes

1. **KPI 3 (Pending Cheque Exposure) is removed from the UI.** The endpoint stays alive for drill-down use, but it no longer occupies a card on the dashboard.
2. **KPI 2 (Late Installments) is extended** with a `cheques_in_pipeline` field showing how much of the late amount is dependent on uncashed cheques.
3. **KPI 7 (Expected Collections Forecast) is added** with 4 time buckets (Month / Quarter / Half / Year), each annotated with its `cheques_in_pipeline` portion.

   > *Amended v1.1: Cheques annotation removed from KPI 7 forecast cards per Phase 0.5 PATH C decision. The `cheques_in_pipeline` field remains in the backend response for future use, but the frontend does not render the amber annotation. Rationale: 2.02% of future unpaid installments carry check records — annotation would show 0 EGP on 3 of 4 cards.*

4. **Frontend is restructured into 4 sections** organized by reading priority:
   - إجمالي المحفظة (Portfolio scale)
   - المخاطرة الحالية (Current risk)
   - المتوقع تحصيله (Expected collections)
   - الأداء والاتجاه (Performance and trend)
5. **Drill-down architecture is implemented as part of this refactor** rather than deferred to D3/D4 separately. Every bucket on the dashboard is clickable and opens a side panel (desktop) or modal (mobile).

### Effort estimate

~49 hours across 6 stages, executed in sequential Claude Code sessions with checkpoint verification between each stage.

---

## 2. The Cheques-in-Pipeline Formula (Canonical Definition)

This is the single most important definition in the refactor because it's referenced by KPI 2 and all 4 KPI 7 buckets.

### Definition

`cheques_in_pipeline` for a set of installments = the EGP value of cheques **received from customers** that are **still in the company's possession** and **have not yet been cashed at the bank**, scoped to that set of installments.

### Formula

For any set of installments S:

```
cheques_in_pipeline(S) = SUM(paid_amount - x_studio_actual_paid_amount)
                        WHERE installment IN S
                          AND (paid_amount - x_studio_actual_paid_amount) > 0
```

This is mathematically equivalent to the existing KPI 3 formula, but scoped to a subset of installments rather than the entire portfolio.

### Why this specific formula (decision rationale)

During the design discussion, three options were considered:

| Option | Definition | Outcome |
|---|---|---|
| A | All installments paid via cheque (cashed or pending) | Rejected — measures structural payment pattern, not risk |
| **B** | Only cheques uncashed in company possession | **Chosen** — measures actual collection risk |
| C | Both A and B in each bucket | Rejected — overwhelms glance reading |

Option B was chosen because:
- The Board makes decisions based on risk, not structure
- The existing KPI 3 formula is already proven identity-equal vs Odoo (Decision 4.5)
- A single risk-focused number per bucket keeps cognitive load low
- The structural breakdown (Option A's information) remains available in the drill-down

### Edge cases

- If `paid_amount == x_studio_actual_paid_amount` for an installment → contributes 0 (no pending cheque)
- If `paid_amount < x_studio_actual_paid_amount` for an installment → contributes 0 (the WHERE clause filters this out; this case is theoretically anomalous and would trigger the existing `data_quality_warning` field per Decision 4.4)
- If `paid_amount == 0` → contributes 0 (customer hasn't paid anything yet)

### Record count

Alongside the EGP value, each bucket reports `cheques_record_count` — the number of installments contributing to the cheques pipeline value (i.e., installments where the difference is > 0).

---

## 3. KPI Inventory After Refactor

| # | Name | Status | Backend change? |
|---|---|---|---|
| 1 | Total Portfolio Value | ✅ Unchanged | No |
| 2 | Late Installments | 🔄 Extended | Add `cheques_in_pipeline` + `cheques_record_count` + `drill_down_domain` + `cheques_drill_down_domain` |
| ~~3~~ | ~~Pending Cheque Exposure~~ | ❌ Removed from UI | Endpoint preserved for drill-down internal use; remove from frontend fetch loop |
| 4 | Collection Rate MTD/YTD | ✅ Unchanged | No |
| 5 | Per-Project Performance | ✅ Unchanged | No |
| 6 | 6-Month Trend | ✅ Unchanged | No |
| **7** | **Expected Collections Forecast** | 🆕 New | Full new service + endpoint + tests + verification |

---

## 4. KPI 7 — Expected Collections Forecast (Full Specification)

> ⚠️ **SUPERSEDED — v2 amendment (2026-06-11, Decision 19.1, Session 19 / N3).**
> The v1 definition below (forward-looking `[today, period_end]` windows with
> `payment_state IN [unpaid, partial]`) is **no longer what production serves**.
> It collapsed to identical values on 3 of 4 cards whenever month/quarter/half
> shared an end date (e.g. all of June). Approved by Khaled 2026-06-11 on the
> N3 discovery numbers (`scripts/discover_kpi7_v2_full_period.py`, commit
> `bc0d2cd`), KPI 7 v2 is:
>
> - **Windows:** FULL PERIOD `[period_start, period_end]` per calendar
>   month / quarter / half (Jan–Jun / Jul–Dec) / year. Domain:
>   `[state=post, date>=start, date<=end]` — **no payment_state filter**.
>   Bucket keys unchanged: `this_month`, `this_quarter`, `this_half`,
>   `this_year`.
> - **Per-bucket payload:** `{period_start, period_end, record_count,
>   period_total_egp, collected_cleared_egp, cheques_pending_egp,
>   remaining_egp}` where `period_total_egp = SUM(amount)`,
>   `collected_cleared_egp = SUM(x_studio_actual_paid_amount)`,
>   `cheques_pending_egp = SUM(paid_amount) − SUM(x_studio_actual_paid_amount)`,
>   `remaining_egp = SUM(due_amount)`. Invariant: cleared + pending +
>   remaining == period_total. The v1 fields (`amount`, `due_amount`,
>   `cheques_in_pipeline`, `cheques_record_count`, `drill_down_domain`,
>   `cheques_drill_down_domain`, `type_breakdown`) are removed from the card
>   payload.
> - **The old forward-looking number is removed entirely** — that story
>   belongs to KPI 2.
> - **RPCs:** one `read_group` per bucket (4 total). Cache key:
>   `kpi:dues_collections_v2:<YYYY-MM-DD>` (Cairo-local). Guards warn via
>   `data_quality_warning`, never 500 (Decision 18.2 pattern).
> - **UI:** section title «مستحقات وتحصيل الفترات الحالية» / "Dues &
>   Collections — Current Periods"; each card = period_total prominent +
>   flat stacked bar (cleared / cheques pending / remaining) + 3 legend rows.
> - **Note:** the forecast drill-down endpoint
>   (`GET /drilldown/forecast/{bucket}`) still serves the v1 forward-looking
>   window; its redefinition is a separate product decision.
>
> §§4.1–4.8 below are the v1 historical record. See
> `MODULE_2_IMPLEMENTATION_DECISIONS.md` Decision 19.1 for the authoritative
> v2 specification.

### 4.1 Definition

The sum of installment amounts that are scheduled to be due within four forward-looking time windows, measured by installment due date. Provides a forward-looking view of cash flow expectations to complement the backward-looking KPI 2 (already late).

### 4.2 Time Buckets

All bucket boundaries are computed in **Africa/Cairo timezone** (per Decision 5.9).

| Bucket | Definition | Boundary |
|---|---|---|
| **this_month** | Installments due from today to end of current calendar month | `[today, end_of_month]` inclusive |
| **this_quarter** | Installments due from today to end of current calendar quarter | `[today, end_of_quarter]` inclusive |
| **this_half** | Installments due from today to end of current calendar half | `[today, end_of_half]` inclusive |
| **this_year** | Installments due from today to end of current calendar year | `[today, end_of_year]` inclusive |

### 4.3 Bucket Nesting Behavior

The buckets are **nested, not mutually exclusive**:

```
this_month ⊆ this_quarter ⊆ this_half ⊆ this_year
```

Example: An installment due on 2026-06-15 would be counted in `this_quarter` (Apr-Jun), `this_half` (Jan-Jun), and `this_year` (Jan-Dec). It would NOT be counted in `this_month` if today is 2026-05-18.

**Rationale:** Each bucket answers a separate question ("how much within X horizon?"). Nesting matches how a Board member reads them — broader windows naturally include narrower ones.

### 4.4 Domain Filter

Same as KPI 2 (Late) but with inverted date direction:

```python
domain = [
    ('state', '=', 'post'),
    ('payment_state', 'in', ['unpaid', 'partial']),
    ('date', '>=', today_cairo.isoformat()),
    ('date', '<=', bucket_end.isoformat()),
]
```

Key differences from KPI 2:
- KPI 2: `date < today` (already past due)
- KPI 7: `date >= today` (still in the future)

This means KPI 2 and KPI 7 are **mutually exclusive** by construction — an installment cannot be both late and future-due simultaneously.

### 4.5 Cheques-in-Pipeline per Bucket

For each bucket, in addition to the total amount, compute `cheques_in_pipeline` using the canonical formula from §2, scoped to installments in that bucket.

> **Amendment v1.1 (Phase 0.5 PATH C):** The `cheques_in_pipeline` field is preserved in the response shape for forward compatibility but is not rendered on the frontend cards. Frontend code MUST hide the amber annotation when `cheques_in_pipeline == 0`, which will be the case for ~98% of future installments based on the current La Verde cheque attachment workflow (checks attach to installments at posting time, not at receipt).

### 4.6 Response Shape

```json
{
  "as_of": "2026-05-18T14:30:00+02:00",
  "currency": "EGP",
  "cache_status": "fresh",
  "rpc_duration_ms": 1234,
  "this_month": {
    "amount": 48800000.00,
    "record_count": 263,
    "cheques_in_pipeline": 42000000.00,
    "cheques_record_count": null,
    "period_start": "2026-05-18",
    "period_end": "2026-05-31",
    "drill_down_domain": [
      ["state", "=", "post"],
      ["payment_state", "in", ["unpaid", "partial"]],
      ["date", ">=", "2026-05-18"],
      ["date", "<=", "2026-05-31"]
    ],
    "cheques_drill_down_domain": [
      ["state", "=", "post"],
      ["payment_state", "in", ["unpaid", "partial"]],
      ["date", ">=", "2026-05-18"],
      ["date", "<=", "2026-05-31"],
      ["paid_amount", ">", "x_studio_actual_paid_amount"]
    ]
  },
  "this_quarter": { ... same shape, broader date range ... },
  "this_half":    { ... same shape, broader date range ... },
  "this_year":    { ... same shape, broader date range ... }
}
```

> **Note (v1.1):** `cheques_in_pipeline` and `cheques_record_count` will typically be 0 / null for forecast buckets. This is correct behavior, not a bug. `cheques_record_count` is returned as `null` (not 0) when Alternative B aggregate formula is used, since the per-installment count is not available via `read_group`. Reference: PHASE_0_5_UI_DISCOVERY_FINDINGS §Objective 1.

### 4.7 Implementation Pattern

**Number of RPCs:** 8 read_group calls (2 per bucket: amount aggregate + cheques aggregate). Sequential is acceptable; parallel optimization deferred unless rpc_duration_ms exceeds 3 seconds in verification.

**Caching:** 60-second TTL, single cache key `kpi:expected_forecast:<YYYY-MM-DD>`, identical pattern to existing KPIs.

**Read-only enforcement:** Standard `_assert_read_only()` guard at function entry.

### 4.8 Pre-Implementation Discovery Requirements

Per Rule 6 (Mandatory Discovery), Stage 1 must verify:

1. The `date` field on `rs.installment` is the correct field for installment due date (vs. `expected_date`, `payment_date`, or any other date field — verify against Phase 2 Discovery §6.4)
2. `read_group` aggregation on this field works correctly with date range domain clauses
3. Identity-equal verification: manually compute the expected_forecast for each bucket via Odoo UI pivot and compare to backend output
4. The 4 bucket boundaries computed in Cairo timezone match the boundaries observable in Odoo UI

---

## 5. KPI 2 Extension Specification

### 5.1 What changes

The existing KPI 2 response is extended with 4 new fields:

| Field | Type | Description |
|---|---|---|
| `cheques_in_pipeline` | float | EGP value of uncashed cheques among late installments |
| `cheques_record_count` | int | Number of late installments contributing to cheques_in_pipeline |
| `drill_down_domain` | list | Pre-computed Odoo domain for KPI 2 drill-down (existing 3-clause domain) |
| `cheques_drill_down_domain` | list | Pre-computed Odoo domain for KPI 2 cheques drill-down |

### 5.2 What does NOT change

- The existing `value`, `record_count`, `as_of`, `cache_status`, `domain`, `currency`, `rpc_duration_ms` fields remain unchanged
- The identity-equal verification baseline (322.2M EGP late uncollected) is preserved
- The cache key structure is unchanged
- The unit tests for the existing fields must continue to pass

### 5.3 Implementation pattern

Add one additional `read_group` RPC inside `get_late_uncollected()`:

```python
# Existing RPC: SUM(due_amount) grouped by nothing
# New RPC: SUM(paid_amount - x_studio_actual_paid_amount) with cheques filter

cheques_domain = domain + [('paid_amount', '>', 'x_studio_actual_paid_amount')]
```

**Performance note:** The cheques aggregate adds one RPC. Verify that the combined `rpc_duration_ms` stays under 2 seconds; if not, parallelize.

---

## 6. KPI 3 Deprecation Plan

### 6.1 What stays

- The service function `get_pending_check_exposure()` in `backend/modules/collections/services/kpi_service.py` — **preserved unchanged**
- The endpoint `GET /api/v1/collections/kpi/pending-check-exposure` — **preserved unchanged**
- All KPI 3 unit tests — **preserved unchanged**
- The identity-equal verification script `verify_kpi3_live.py` — **preserved unchanged**
- The cache key, TTL, and storage path — **preserved unchanged**

**Rationale:** The endpoint may be needed by future drill-downs or AI Chat intents that ask about cheque exposure across the whole portfolio. Removing the backend would be premature optimization.

### 6.2 What goes

In `frontend/static/js/collections.js`:
- Remove `kpi3` from the `endpoints` array
- Remove `state[3]` from the fetch result processing
- Remove `renderRow2` references to KPI 3 (the Pending Check Exposure card)

In `frontend/templates/collections/dashboard.html`:
- Remove the `<div id="col-kpi3-container">` element entirely
- Remove the i18n keys `Pending Check Exposure` and `in pipeline` from COLLECTIONS_STRINGS (preserved in en.json/ar.json for AI Chat)

In `frontend/static/js/collections.js` banner logic:
- Update `shouldShowDataEntryBanner` to no longer reference KPI 3 (it currently doesn't; this is a precautionary audit)

### 6.3 Migration safety

The `state` array index assignments will shift. Currently:

```
state[0] = KPI 2
state[1] = KPI 1
state[2] = KPI 5a
state[3] = KPI 3  ← removed
state[4] = KPI 6
state[5] = KPI 4
state[6] = KPI 5b
```

After refactor:

```
state[0] = KPI 2 (with new cheques_in_pipeline fields)
state[1] = KPI 1
state[2] = KPI 5a
state[3] = KPI 6
state[4] = KPI 4
state[5] = KPI 5b
state[6] = KPI 7 (new)
```

**This is a breaking change to the `window.collectionsDashboard.state` shape.** External consumers (none currently, but documented for future) would need to update their indexes.

**Risk mitigation:** Stage 3 frontend refactor will rename `state` to be a named object instead of an array, eliminating index-based access entirely:

```javascript
window.collectionsDashboard.state = {
  late: {...},
  portfolio: {...},
  perProject: {...},
  trend: {...},
  rate: {...},
  rateByProject: {...},
  forecast: {...},
};
```

This is a one-time refactor with payoff in long-term maintainability.

---

## 7. Frontend Architecture After Refactor

### 7.1 The 4 Sections

The dashboard is reorganized into 4 visually distinct sections, each with a section header in Arabic/English:

```
┌─────────────────────────────────────────────────────────┐
│ Section 1 — إجمالي المحفظة / Portfolio Scale            │
│                                                          │
│   ┌─────────────────────────────────────────────┐       │
│   │ KPI 1 — Total Portfolio (6.12B EGP)         │       │
│   │ emerald accent, 42,443 installments         │       │
│   └─────────────────────────────────────────────┘       │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ Section 2 — المخاطرة الحالية / Current Risk             │
│                                                          │
│   ┌─────────────────────────────────────────────┐       │
│   │ KPI 2 — Late Installments (322.2M EGP)      │       │
│   │ danger accent, 1,995 installments           │       │
│   │ ⚠ منها شيكات في الـ pipeline: 45.8M        │       │
│   └─────────────────────────────────────────────┘       │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ Section 3 — التحصيل المتوقع / Expected Collections      │
│                                                          │
│   ┌──────────────┬──────────────┐                       │
│   │ هذا الشهر   │ هذا الربع   │                       │
│   │ 48.8M       │ 142.5M      │                       │
│   │ 263 قسط     │ 720 قسط     │                       │
│   └──────────────┴──────────────┘                       │
│   ┌──────────────┬──────────────┐                       │
│   │ النصف       │ هذا العام  │                       │
│   │ 287.3M      │ 521.7M      │                       │
│   │ 1450 قسط    │ 2980 قسط    │                       │
│   └──────────────┴──────────────┘                       │
└─────────────────────────────────────────────────────────┘

> **Amendment v1.1:** The amber cheques annotation previously shown on each KPI 7 forecast card is removed per Phase 0.5 PATH C. Each card now displays: bucket label, amount, and record count subtitle. The section header is amended from "المتوقع تحصيله" to "التحصيل المتوقع" per Q5 resolution (2026-05-18). The cheques annotation on KPI 2 (Late) card remains unchanged.

> **Amendment v1.2 (2026-06-11, Decision 19.1 — KPI 7 v2):** Section 3 is
> retitled «مستحقات وتحصيل الفترات الحالية» / "Dues & Collections — Current
> Periods". Each of the 4 cards now shows the FULL-PERIOD picture:
> `period_total_egp` prominent, a flat stacked bar with 3 segments
> (collected cleared / cheques pending clearance / remaining), and 3 legend
> rows with EGP values. The forward-looking amount and the card drill-down
> trigger of the v1 layout above are removed (the v1 forecast story belongs
> to KPI 2). The mockup above is the v1 historical record.

┌─────────────────────────────────────────────────────────┐
│ Section 4 — الأداء والاتجاه / Performance & Trend       │
│                                                          │
│   ┌──────────────┬──────────────┐                       │
│   │ معدل التحصيل│ Per Project │                       │
│   │ MTD/YTD     │ 3 projects  │                       │
│   └──────────────┴──────────────┘                       │
│   ┌─────────────────────────────────────────────┐       │
│   │ منحنى التحصيل — 6 أشهر                      │       │
│   │ [Chart.js line chart]                       │       │
│   └─────────────────────────────────────────────┘       │
└─────────────────────────────────────────────────────────┘
```

### 7.2 Section Header Styling

Each section header is:
- Text: `font-size: 0.75rem`, `font-weight: 600`, `text-transform: uppercase`, `letter-spacing: 0.05em`
- Color: `text-neutral-500 dark:text-neutral-400`
- Margin: `mt-8 mb-3` (generous space above, tight space below to its content)
- Position: left-aligned on LTR, right-aligned on RTL (via `text-start`)

### 7.3 Section Spacing

- 32px vertical gap between sections (`mt-8` on each section's header)
- 16px vertical gap between cards within a section (`gap-4`)
- The 4 sections combined are designed to fit a 1080px viewport without scrolling on desktop

### 7.4 Color Palette per Section

| Section | Primary accent | Card border-left color |
|---|---|---|
| 1 — Portfolio | Emerald | `border-emerald-500` |
| 2 — Current Risk | Danger (red) | `border-danger-500` |
| 3 — Expected Collections | Info (blue) | `border-info-500` (uses existing blue palette) |
| 4 — Performance | Neutral / no accent | No left border (informational only) |

**Cheques annotation** within sections 2 and 3 uses amber text (`text-amber-700 dark:text-amber-400`) to provide a consistent visual cue across all cheques references, without competing with the primary section color.

> **Amendment v1.1:** Cheques annotation in section 3 (Expected Collections) is REMOVED. The amber color treatment applies only to section 2 (Current Risk / KPI 2) cheques annotation.

### 7.5 Card Templates

Three card macros, each rendering a specific layout:

| Macro | File | Used by |
|---|---|---|
| `portfolio_card` | `_portfolio_card.html` (new) | KPI 1 |
| `risk_card` | `_risk_card.html` (new) | KPI 2 |
| `forecast_card(bucket_key)` | `_forecast_card.html` (new) | KPI 7's 4 buckets |

The existing `_kpi_card.html` macro is preserved for any future use but no longer driven by the Collections dashboard. The existing `_project_card.html` (from D2.6) remains for Section 4's project cards.

### 7.6 Card Component Details

**Risk Card (KPI 2):**

```
┌──────────────────────────────────────────────────┐
│ ▌ المتأخرات غير المحصلة                          │  ← label (small, uppercase)
│                                                   │
│   322.2 مليون جنيه                              │  ← value (text-5xl, danger color)
│                                                   │
│   1,995 قسط · اعتباراً من ١٨ مايو ٢٠٢٦         │  ← subtitle (records + as-of)
│                                                   │
│   ⚠ منها شيكات في الـ pipeline: 45.8M           │  ← cheques annotation (amber)
│                                                   │
│   [hover state: subtle elevation + border thicken]│
└──────────────────────────────────────────────────┘
```

**Forecast Card (one of 4 buckets):**

```
┌────────────────────────────────────┐
│ ▌ هذا الشهر                        │  ← bucket label
│                                     │
│   48.8 مليون                       │  ← amount (text-3xl, info color)
│                                     │
│   263 قسط · حتى ٣١ مايو            │  ← record count + end date
│                                     │
│   [hover state]                     │
└────────────────────────────────────┘
```

> **Amendment v1.1:** The "منها شيكات" line is removed from forecast cards. Card content: bucket label, amount (text-3xl, info color), record count + end date subtitle. The cheques annotation remains on the Risk Card (KPI 2) only.

### 7.7 Responsive Behavior

| Viewport | Section 1 | Section 2 | Section 3 | Section 4 |
|---|---|---|---|---|
| `< 640px` (mobile) | Full width | Full width | 1 col, 4 rows | Stacked |
| `640-1024px` (tablet) | Full width | Full width | 2 cols, 2 rows | 2 cols |
| `>= 1024px` (desktop) | Full width | Full width | 4 cols, 1 row | 2 cols (rate, project) + full chart |

---

## 8. Drill-Down Architecture

### 8.1 Interaction Pattern

Every clickable element on the dashboard opens a drill-down view containing the underlying installment list.

| Device | Drill-down container |
|---|---|
| Desktop (≥ 1024px) | Right-side panel sliding in from the inline-end edge (350-500px wide) |
| Mobile/Tablet (< 1024px) | Full-screen modal |

This matches the spec established during D2 (Khaled's decision in Checkpoint A review).

### 8.2 Drill-Down Targets

| Source element | Drill-down content |
|---|---|
| KPI 2 main amount | List of late installments (all 1,995) sorted by `due_amount` desc |
| KPI 2 cheques annotation | List of late installments with `paid_amount > actual_paid` (cheques pending subset) |
| KPI 7 month/quarter/half/year amount | List of installments due in that bucket sorted by `date` asc |
| ~~KPI 7 cheques annotation~~ | ~~List of installments due in that bucket with cheques pending~~ |
| KPI 1 value | Top 50 customers by portfolio value (existing MVP Design §3.4) |
| Project card (per project) | Per-project breakdown (existing MVP Design §3.4) |
| Trend chart point | Month-by-month breakdown (existing MVP Design §3.4) |

> *Removed v1.1 — no cheques annotation on KPI 7 cards per PATH C. The KPI 7 cheques annotation drill-down row above is struck through. KPI 7 bucket amounts open the drill-down with no preset filter.*

### 8.3 Drill-Down List Columns

| Column | Field | Display |
|---|---|---|
| Customer Name | `partner_id.name` | Plain text, truncate to 30 chars with full on hover |
| Project | `project_id.name` | Translated via project_names map |
| Due Date | `date` | Formatted per lang locale |
| Amount (EGP) | `amount` | Formatted EGP, abbreviated |
| Due Amount (EGP) | `due_amount` | Formatted EGP, abbreviated |
| Pending Cheque (EGP) | `paid_amount - actual_paid_amount` | Only shown when relevant; 0 hidden as "—" |
| Payment State | `payment_state` | Badge: `unpaid` (danger) / `partial` (warning) / `paid` (success) |

### 8.4 Pagination

- Default page size: 50 records
- Infinite scroll on mobile, classic pagination on desktop
- No "Load all" button (per existing MVP discipline)

### 8.5 Filters Inside Drill-Down

Each drill-down panel has a filter sidebar/header:

| Filter | Available for |
|---|---|
| Project (dropdown: All / New Capital / Cassette / La puerta) | All drill-downs |
| Payment State (toggle: all / unpaid / partial) | KPI 2, KPI 7 drill-downs |
| Has Pending Cheque (toggle) | All drill-downs |
| Sort field (Due Date / Amount / Due Amount) | All drill-downs |

When a user clicks the "منها شيكات في الـ pipeline" annotation on a card, the drill-down opens with the "Has Pending Cheque" filter pre-applied.

> **Amendment v1.1:** This behavior now applies ONLY to KPI 2's cheques annotation (the only remaining cheques annotation on the dashboard). KPI 7 cards have no cheques annotation; clicking the bucket amount opens the drill-down with no preset filter.

### 8.6 Backend Drill-Down Endpoints

| Endpoint | Source bucket | Returns |
|---|---|---|
| `GET /api/v1/collections/drilldown/late` | KPI 2 | Paginated late installment list |
| `GET /api/v1/collections/drilldown/forecast/{bucket}` | KPI 7 | Paginated bucket installments where `bucket` ∈ {month, quarter, half, year} |
| `GET /api/v1/collections/drilldown/portfolio` | KPI 1 | Top 50 customers |
| `GET /api/v1/collections/drilldown/project/{id}` | KPI 5 | Per-project list |
| `GET /api/v1/collections/drilldown/trend/{month}` | KPI 6 | Per-month installments |

Query params accepted on all:
- `page` (default 1)
- `page_size` (default 50, max 200)
- `project_id` (optional filter)
- `payment_state` (optional filter; `unpaid` | `partial`)
- `has_pending_cheque` (optional bool filter)
- `sort_by` (optional; `date` | `amount` | `due_amount`)
- `sort_dir` (optional; `asc` | `desc`)

### 8.7 Drill-Down Caching

Drill-down responses are NOT cached. Each request hits Odoo directly. Rationale:
- Cache cardinality explodes with filter combinations (project × payment_state × pending_cheque × sort × page)
- Drill-down access is infrequent (user clicks)
- 200-record pages are fast queries
- Stale drill-down data is more misleading than stale aggregates

### 8.8 Identity-Equal Verification for Drill-Downs

Each drill-down endpoint must satisfy: the SUM of `due_amount` across all pages with no filters applied = the parent KPI's reported value, to the cent.

Example: KPI 2 reports 322.2M EGP. The drill-down `/api/v1/collections/drilldown/late?page=1...N` must sum to 322.2M when summed across all pages.

A new verification script `verify_drilldowns_live.py` enforces this in Stage 5.

---

## 9. Six-Stage Implementation Roadmap

Each stage = one dedicated Claude Code session with a fresh context. Khaled tags a checkpoint after each stage is verified.

### Stage 1 — KPI 7 Backend (Pre-Implementation Discovery + Implementation)

**Goal:** Add KPI 7 backend service, endpoint, tests, and identity-equal verification.

**Deliverables:**
1. Pre-Implementation Discovery: `scripts/discover_kpi7.py` — verifies `date` field semantics, bucket boundary correctness, identity-equal manual check
2. Service function `get_expected_collections_forecast()` in `kpi_service.py`
3. Endpoint `GET /api/v1/collections/kpi/expected-forecast` in `collections.py`
4. Unit tests: 12-15 tests covering all 4 buckets, edge cases, cache hit, read-only assertion, timezone handling
5. Verification script `scripts/verify_kpi7_live.py`
6. Decisions log entry in `MODULE_2_IMPLEMENTATION_DECISIONS.md` Session 9
7. No frontend changes in this stage

**Effort estimate:** ~14 hours (4h discovery + 10h implementation)

**Acceptance criteria:**
- All 4 buckets return non-negative amounts
- Bucket nesting verified: `this_month ≤ this_quarter ≤ this_half ≤ this_year`
- Identity-equal vs Odoo manual count for each bucket
- `cheques_in_pipeline` per bucket ≤ bucket amount
- `drill_down_domain` and `cheques_drill_down_domain` are well-formed Odoo domains
- Cache hit on second request (`cache_status: "cached"`, `rpc_duration_ms: 0`)
- All 12-15 unit tests pass
- Read-only assertion still enforced

**Checkpoint tag:** `checkpoint-C-stage1-kpi7-backend-complete`

### Stage 2 — KPI 2 Extension

**Goal:** Extend KPI 2 service with `cheques_in_pipeline`, `cheques_record_count`, and drill-down domains.

**Deliverables:**
1. Extend `get_late_uncollected()` to compute additional fields
2. Add 3-4 new unit tests for the new fields
3. Update `verify_kpi2_live.py` to validate new fields
4. Decisions log entry: Decision 9.X — KPI 2 cheques annotation
5. No frontend changes in this stage

**Effort estimate:** ~4 hours

**Acceptance criteria:**
- `cheques_in_pipeline` ≤ `value` (cheques can't exceed total late)
- `cheques_record_count` ≤ `record_count`
- Existing 322.2M baseline preserved (no change to `value`)
- Existing 1,995 record_count preserved
- New cheques sub-figure verified identity-equal vs Odoo pivot

**Checkpoint tag:** `checkpoint-C-stage2-kpi2-extended`

### Stage 3 — Frontend Restructure (4 Sections + KPI 3 Removal)

**Goal:** Restructure dashboard into 4 sections, remove KPI 3 from UI, refactor `state` from array to named object.

**Deliverables:**
1. New macros: `_portfolio_card.html`, `_risk_card.html`, `_forecast_card.html`
2. Restructured `collections/dashboard.html` with 4 sections
3. Section headers with i18n keys
4. Updated `collections.js`:
   - `state` becomes named object (not array)
   - Remove KPI 3 fetch
   - Add KPI 7 fetch
   - New render functions per section
5. Updated `COLLECTIONS_STRINGS` with new keys
6. Updated `en.json` and `ar.json` with section header strings
7. Tailwind config: no changes expected (existing palette sufficient)

**Effort estimate:** ~10 hours

**Acceptance criteria:**
- All 4 sections render in correct order
- Section headers display in correct language
- KPI 3 card is gone (no `col-kpi3-container` element in DOM)
- KPI 7 cards (4 buckets) render with cheques annotation
- KPI 2 card shows cheques annotation
- `window.collectionsDashboard.state` is a named object, all KPIs accessible by name
- AR + Dark mode verified
- No console errors

**Checkpoint tag:** `checkpoint-D-stage3-frontend-restructure-complete`

### Stage 4 — Cheques Annotation Polish

**Goal:** Refine the cheques annotation visual treatment across all 5 cards (KPI 2 + KPI 7 × 4) for consistency.

**Deliverables:**
1. Cheques annotation styling consistency check
2. Tooltips on cheques annotation (hover shows full EGP value + percentage of bucket)
3. Cheques annotation responsive behavior (wrap on narrow viewports)
4. Empty-state handling: when `cheques_in_pipeline == 0`, hide the annotation entirely (don't display "0 cheques")
5. Accessibility: cheques annotation has its own `aria-label`

**Effort estimate:** ~3 hours

**Acceptance criteria:**
- All 5 annotations visually consistent
- Hover tooltip works on all 5
- Empty state correctly hides annotation
- Screen reader announces "cheques in pipeline: 42 million EGP"

**Checkpoint tag:** `checkpoint-D-stage4-cheques-polish-complete`

### Stage 5 — Backend Drill-Down Endpoints

**Goal:** Build all 5 drill-down endpoints with pagination, filters, and identity-equal verification.

**Deliverables:**
1. 5 new endpoints in `collections.py`:
   - `GET /api/v1/collections/drilldown/late`
   - `GET /api/v1/collections/drilldown/forecast/{bucket}`
   - `GET /api/v1/collections/drilldown/portfolio`
   - `GET /api/v1/collections/drilldown/project/{id}`
   - `GET /api/v1/collections/drilldown/trend/{month}`
2. Service functions for each drill-down in `drilldown_service.py` (new file)
3. Pydantic schemas for drill-down responses
4. Unit tests: ~25-30 tests across all endpoints + filter combinations
5. Verification script `scripts/verify_drilldowns_live.py` — confirms sum across pages = parent KPI value
6. Decisions log entry: Session 10 / Drilldown architecture

**Effort estimate:** ~10 hours

**Acceptance criteria:**
- All 5 endpoints return 200 with valid pagination
- All filters work correctly (project, payment_state, has_pending_cheque)
- Sum of all pages (no filters) = parent KPI value for each drill-down
- Pagination metadata accurate (total_count, page, page_size, has_next)
- Read-only assertion enforced on all endpoints
- All 25-30 unit tests pass

**Checkpoint tag:** `checkpoint-E-stage5-drilldown-backend-complete`

### Stage 6 — Frontend Drill-Down UI

**Goal:** Build the side panel (desktop) / modal (mobile) drill-down UI with filters.

**Deliverables:**
1. New macros: `_drilldown_panel.html` (desktop), reuse `_modal.html` (mobile)
2. JavaScript: `drilldown.js` — handles open/close, fetches data, renders rows, applies filters
3. Click handlers on all clickable elements in the dashboard
4. URL state: drill-downs reflect in URL hash (`#drilldown=late&project=1`)
5. Keyboard navigation: Escape closes, arrows navigate rows
6. Empty state: "No installments match these filters"
7. Loading state: skeleton while fetching
8. Error state: retry button if fetch fails

**Effort estimate:** ~8 hours

**Acceptance criteria:**
- Clicking KPI 2 opens drill-down with late installments
- Clicking cheques annotation opens drill-down with cheques filter pre-applied
- Clicking each of 4 forecast buckets opens correct drill-down
- Filters work end-to-end (UI → API → display)
- Desktop side panel, mobile modal — both functional
- URL state syncs (refresh preserves drill-down state)
- Escape key closes panel
- AR + Dark mode verified

**Checkpoint tag:** `checkpoint-E-stage6-drilldown-frontend-complete` — **= Module 2 MVP Complete**

---

## 10. Decision Log

This refactor is driven by the following decisions made during Session 8 conversations:

| ID | Decision | Made by | Rationale |
|---|---|---|---|
| R.1 | Reorganize dashboard around installment-lifecycle axis | Khaled (proposal) + Claude Chat (CTO concur) | The KPI 3 misleading number issue revealed that mixing axes (time-based + payment-method-based) creates cognitive friction. Single axis is cleaner. |
| R.2 | Remove KPI 3 from UI, preserve endpoint | CTO recommendation, Khaled approval | Endpoint may be needed for drill-downs and AI Chat; removing it is premature. UI removal solves the immediate misleading-number problem. |
| R.3 | Add KPI 7 with 4 time buckets | Khaled proposal | Forecasting answers a question the Board doesn't ask but would value highly ("نقلة نوعية"). Differentiates the product from Odoo standard reports. |
| R.4 | Use Option B for cheques-in-pipeline formula | CTO recommendation, Khaled approval after example walkthrough | Option B (uncashed only) measures actual risk; Option A measures structural pattern. Risk is decision-grade for the Board. |
| R.5 | Buckets are nested (not mutually exclusive) | CTO design decision | Each bucket answers a separate horizon question. Nesting matches Board reading mental model. |
| R.6 | Implement drill-downs as part of refactor (not D3/D4 separately) | Khaled requirement | Avoids rework: KPI 7 response shape and drill-down query design must be cohesive. |
| R.7 | 6-stage incremental rollout | CTO recommendation | Each stage independently verifiable; rollback per stage if issues arise; fresh Claude Code session per stage prevents context drift. |
| R.8 | Tags as rollback points before each stage | Standard practice | `checkpoint-B-D2-complete` is the pre-refactor baseline. Each stage adds a new checkpoint tag. |
| R.9 | `state` array becomes named object in Stage 3 | CTO recommendation | Index-based access is brittle (changes when KPIs are added/removed). Named access is self-documenting. |
| R.10 | Drill-downs not cached | CTO design decision | Cardinality explosion from filter combinations; infrequent access; cache staleness more misleading than aggregate staleness. |
| R.11 | Apply PATH C: remove cheques annotation from KPI 7 forecast cards | Phase 0.5 findings + CTO recommendation, Khaled approval 2026-05-18 | 2.02% of future unpaid installments carry check records. Annotation would show 0 EGP on 3 of 4 cards — visual clutter without information value. Backend response unchanged for forward compatibility. |

---

## 11. Open Questions / Pending Khaled Approval

All 8 questions below are now RESOLVED. Resolved per Khaled confirmation 2026-05-18.

| # | Question | Resolution | Resolved On |
|---|---|---|---|
| Q1 | Should bucket boundaries be "calendar" (Jan-Mar = Q1) or "fiscal" (e.g., Jul-Sep = Q1)? | **Calendar** (matches Decision 6.2 YTD assumption). Default adopted. | 2026-05-18 |
| Q2 | Should `this_year` include only future installments (today → Dec 31) or full year (Jan 1 → Dec 31 including already-collected)? | **Future only** (today → Dec 31) — consistent with the "forward-looking" framing. Default adopted. | 2026-05-18 |
| Q3 | When the user is on May 18, 2026: does `this_quarter` mean Apr 1 → Jun 30 (calendar quarter) or May 18 → Aug 17 (rolling 3 months)? | **Calendar quarter** (Apr 1 → Jun 30) filtered to future only (today → Jun 30). Default adopted. | 2026-05-18 |
| Q4 | Should the dashboard show "خلال 30 يوم" / "خلال 90 يوم" rolling windows as an alternative or replacement to calendar buckets? | **Calendar buckets only** for this iteration. Rolling windows deferred to future iteration. Default adopted. | 2026-05-18 |
| Q5 | Are the section headers ("إجمالي المحفظة" / "المخاطرة الحالية" / etc.) the final user-facing strings? | **Yes**, with one amendment: Section 3 header is "التحصيل المتوقع" (not "المتوقع تحصيله"). All other headers as proposed. Diagram in §7.1 updated accordingly. | 2026-05-18 |
| Q6 | Should KPI 2's cheques annotation include a tooltip showing the percentage (e.g., "14% of late amount")? | **Yes** — tooltip implemented in Stage 4. Default adopted. | 2026-05-18 |
| Q7 | Drill-down sort default — by date asc, due_amount desc, or partner name? | **Due date asc** for KPI 7 buckets; **due_amount desc** for KPI 2. Default adopted. | 2026-05-18 |
| Q8 | Should the Trend Chart (KPI 6) be re-imagined to align with the new buckets (e.g., show forecast + historical on same chart)? | **No change** in this refactor. Trend chart redesign deferred to a future iteration. Default adopted. | 2026-05-18 |

---

## 12. Rollback Plan

If any stage produces unrecoverable issues:

### Per-stage rollback

```bash
git checkout checkpoint-B-D2-complete  # the last known-good baseline
# Or roll back to any earlier checkpoint tag
```

The 4 sections layout and KPI 7 are additive — rolling back to `checkpoint-B-D2-complete` returns the dashboard to its current verified state (4 rows, 6 KPIs, working).

### Per-KPI rollback

KPI 7 endpoint can be disabled without removing it:
1. Remove the route registration in `collections.py`
2. Remove the fetch in `collections.js`
3. Hide Section 3 in the template (CSS `display: none`)

Re-enabling requires reversing the above 3 changes.

### KPI 3 restore

KPI 3 endpoint is preserved (per §6.1). To restore the UI:
1. Re-add `kpi3` to the endpoints array in `collections.js`
2. Re-add the `col-kpi3-container` element to the template
3. Re-add render code in `renderRow2` (or new equivalent)

This is approximately a 1-hour restoration if needed.

---

## 13. Success Criteria for the Refactor

The refactor is considered successful when:

1. ✅ All 6 stages have passed their respective checkpoints
2. ✅ The dashboard displays 4 sections in correct order with all KPIs rendering
3. ✅ KPI 7's 4 buckets show identity-equal values vs Odoo manual count
4. ✅ KPI 2's cheques annotation shows identity-equal value vs the existing KPI 3 endpoint's pending-cheques value (sanity check: KPI 2 cheques ⊆ KPI 3 total cheques)
5. ✅ All 5 drill-down endpoints sum identity-equal to their parent KPIs
6. ✅ Side panel (desktop) and modal (mobile) drill-downs work for all clickable elements
7. ✅ Filters within drill-downs work correctly
8. ✅ AR + Dark mode verified on the entire refactored dashboard
9. ✅ No `console.log` regressions (still only one Collections log per fetch cycle)
10. ✅ Performance: initial page load + 7 fetches under 3 seconds on cold cache
11. ✅ Khaled has performed end-to-end Checkpoint E browser verification with screenshots
12. ✅ The dashboard is Board-ready for the deferred launch (per Decision 1.3)

---

## 14. What's NOT in This Refactor

To prevent scope creep, the following are explicitly out of scope for the 6 stages:

- AI Chat integration with the new KPIs (Pillar 2 — separate effort)
- Print stylesheet
- Export to PDF/Excel
- Email digest of daily KPIs
- Custom date ranges (only calendar buckets in this refactor)
- Rolling windows as alternative to calendar buckets
- Comparison views (this month vs last month, this year vs last year)
- Trend chart redesign to incorporate forecast data
- Customizable section ordering by the user
- Annotations from the Collections team (notes on specific installments)
- WhatsApp notifications when late amount exceeds threshold

Each of these is a legitimate future enhancement and may be considered in subsequent modules or iterations.

---

## 15. Document Maintenance

This document is the canonical source for the refactor plan. If any decision changes during implementation:

1. Update the relevant section
2. Add an entry to §10 (Decision Log) with the change
3. Increment the version number at the top
4. Tag the document update in git: `git tag -a refactor-spec-v1.X -m "..."`

After Stage 6 completion, this document is archived (renamed to `MODULE_2_REFACTOR_SPEC_COMPLETED.md`) and a final `MODULE_2_AS_BUILT.md` is created documenting the as-implemented state.

---

## 16. Cross-References to Related Documents

- `docs/PHASE_0_5_UI_DISCOVERY_FINDINGS.md` — source of PATH C decision; documents 2.02% statistic, `has_checks` field semantics, EXEC favorites discrepancy
- `docs/KPI7_DISCOVERY_FINDINGS.md` — Phase 0 bucket baseline + Phase 0.5 outcomes summary
- `docs/MODULE_2_BUSINESS_CONTEXT.md` §19 — KPI definition canonical decisions
- `docs/ODOO_UI_VERIFICATION_GUIDE.md` — practical guide for any manual cross-check against Odoo UI

---

**END OF SPECIFICATION**
