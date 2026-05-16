# Module 2 — Collections: MVP Design Document

> **Status**: Draft — Pending Khaled approval before implementation
> **Version**: 1.0
> **Date**: 2026-05-15
> **Parent Documents**:
>   - [`docs/MODULE_2_BUSINESS_CONTEXT.md`](MODULE_2_BUSINESS_CONTEXT.md)
>   - [`docs/MODULE_2_DISCOVERY_PHASE_1.md`](MODULE_2_DISCOVERY_PHASE_1.md)

---

## 1. Document Purpose and Scope

This document is the MVP design specification for the Collections module
(Module 2). It translates the business context established in
`MODULE_2_BUSINESS_CONTEXT.md` and the technical findings from
`MODULE_2_DISCOVERY_PHASE_1.md` into a concrete, actionable design for
the MVP. It exists to answer the question: "given what we know about the
business and the Odoo data, exactly what should we build?" It is not a
re-statement of the business context, not an implementation guide (no
code appears here), and not a Phase 2 discovery document. All business
decisions — the Board of Directors as primary persona, the two-pillar
structure, the read-only rule, and the out-of-scope list — are already
settled in the parent documents and are carried forward here without
re-debate.

---

## 2. MVP Pillars Overview

The Collections module MVP is organized around two pillars derived from
the Board of Directors' use case (see `MODULE_2_BUSINESS_CONTEXT.md`
§2.1 Persona Evolution):

**Pillar 1 — Executive Dashboard (the core deliverable)**
A purpose-built dashboard surfacing 6 financial KPIs drawn directly from
the Odoo Collections Mgmt data. Designed for the Chairman, CEO, and CFO
to review portfolio health, late uncollected exposure, and project-level
performance at a glance, on demand. Drill-down is a navigational feature
within this pillar — it is not a separate pillar.

**Pillar 2 — AI Chat in Arabic and English (the enhancement)**
A bilingual conversational interface layered on top of the dashboard,
allowing Board members to ask natural-language questions about the same
data. The AI chat reads from the identical Odoo sources as the dashboard.
It adds no new data access — it provides a different interaction modality
on top of what the dashboard already exposes.

The deferred persona — the Collections Officer (موظف تحصيلات) with their
daily operational workflow — is explicitly out of scope for both pillars.

---

## 3. Pillar 1 — Executive Dashboard

### 3.1 Design Philosophy

The Collections dashboard is an executive intelligence layer, not an
operational tool. Every design decision must be evaluated from the
perspective of a Chairman reading the screen for two minutes before a
board meeting, not an analyst working through a spreadsheet. This means:
large, unambiguous numbers; color immediately signaling good vs.
alarming; no tables of individual records at the top level; no filter
panels cluttering the primary view. The CRM module dashboard
(`backend/modules/crm/` + `frontend/templates/dashboard.html`) is the
visual and architectural baseline — the same component library, grid
system, and KPI card macro are reused — but the Collections dashboard
will be more refined: fewer KPIs with more visual weight per card, no
operational heatmaps or salesperson matrices, and number formatting
calibrated for billion-EGP values rather than lead counts.

---

### 3.2 The 6 KPIs — Detailed Specification

---

#### KPI 1 — Total Portfolio Value / إجمالي قيمة المحفظة

**Definition**
The sum of all installment amounts across the entire portfolio,
regardless of payment status, installment type, or project.

**Formula**
```
SUM(rs.installment.amount) WHERE state = 'post'
```
Domain: `[('state', '=', 'post')]` — ~42,443 posted installments.

**Data Source**
Model: `rs.installment`
Domain: `[('state', '=', 'post')]`
Aggregation: `SUM` on the `amount` field (native monetary field,
confirmed in `MODULE_2_DISCOVERY_PHASE_1.md` §3)

**Baseline Value (from 2026-05-14 snapshot)**
6,123,549,625.23 EGP
(Source: `MODULE_2_BUSINESS_CONTEXT.md` §9, All Installments row,
Amount column)

**Display Format**
Large KPI card, `info` variant (blue tone), abbreviated display:
"6.12B EGP" with full value available on hover. Sparkline showing
portfolio growth over trailing 6 months (populated by the same trend
query as KPI 6). Uses the `kpi_card` macro from
`frontend/templates/components/_kpi_card.html`.

**Refresh Frequency**
60-second cache (`CACHE_TTL_SECONDS`). Portfolio total changes only
when new installments are created (new contracts), which is an
infrequent event — but the 60s standard is appropriate for consistency.

**Drill-Down Target**
Top 50 customers (`partner_id` from `rs.installment`) sorted by
`SUM(amount)` descending. Columns: Customer Name, Project, Total
Amount (EGP), Paid Amount (EGP), Due Amount (EGP). Filterable by
`project_id`. Read-only list — no actions.

**Open Questions / Phase 2 Dependencies**
None. The `amount` field is confirmed as a native monetary field on
`rs.installment` (`MODULE_2_DISCOVERY_PHASE_1.md` §3).

> **Note (2026-05-16):** The original design specified domain `[]`
> and record count 42,970. Live verification during Session 2
> revealed that Odoo's "All Installments" view applies
> `state='post'` at the view layer, excluding 19 draft records
> (8.7M EGP) and 508 cancelled records (134.2M EGP). The
> corrected domain is `[('state', '=', 'post')]`, record count
> ~42,443. The baseline 6,123,549,625.23 EGP is unchanged — it
> was always the post-only total taken from the Odoo UI. See
> `MODULE_2_IMPLEMENTATION_DECISIONS.md` Decision 2.4.

---

#### KPI 2 — Late Uncollected / المتأخرات غير المحصلة

**Definition**
The total outstanding cash due on overdue installments — the primary
receivables risk number the Board tracks. This is the most important
single figure in the module.

**Formula**
```
SUM(rs.installment.due_amount)
  WHERE <Late installment domain>
  [PHASE 2 VERIFICATION REQUIRED — see Open Questions below]
```

**Data Source**
Model: `rs.installment`
Domain: the "Late" filter as defined in Collections Mgmt
(`MODULE_2_BUSINESS_CONTEXT.md` §9 distinguishes "All Installments"
from "Late Installments" as separate snapshot views — the Late domain
must be reproduced exactly)
Aggregation: `SUM` on `due_amount` (native monetary field)

**Baseline Value (from 2026-05-14 snapshot)**
312,604,879.40 EGP
(Source: `MODULE_2_BUSINESS_CONTEXT.md` §9, Late Installments row,
Due Amount column)

**Display Format**
Hero KPI card — the most visually prominent element on the page.
`danger` variant (red). Large font, abbreviated: "312.6M EGP" with
full value on hover. The Chairman's eye must land here first (see §3.3
for layout). Sparkline showing late uncollected trend over trailing 6
months. Uses the `kpi_card` macro from
`frontend/templates/components/_kpi_card.html`.

**Refresh Frequency**
60-second cache (`CACHE_TTL_SECONDS`). Late uncollected changes as
payments are posted in RS Accounting, which can happen throughout the
working day.

**Drill-Down Target**
Top 50 customers with the highest `due_amount` on late installments,
sorted by `due_amount` descending. Columns: Customer Name, Project,
Late Due Amount (EGP), Payment Status (`payment_state`). Filterable by
`project_id` and `payment_state` (`unpaid` / `partial`, both confirmed
in `MODULE_2_DISCOVERY_PHASE_1.md` §3). An aging summary (total late
due > 30 days, > 60 days, > 90 days) [PHASE 2 VERIFICATION REQUIRED
for date field] appears above the list. Read-only list.

**Open Questions / Phase 2 Dependencies**
[PHASE 2 VERIFICATION REQUIRED] The technical Odoo domain that
reproduces the "Late" filter in Collections Mgmt was not verified in
Phase 1. The 312.6M EGP baseline depends on this domain being
reproduced exactly in code. Without confirming the filter definition
(e.g., is it `date < today AND payment_state IN ('unpaid', 'partial')`
or does it rely on a dedicated flag?), this KPI cannot be safely
implemented or validated. This is the highest-priority Phase 2 item.

---

#### KPI 3 — Pending Check Exposure / مخاطر الشيكات المعلقة

**Definition**
The total value of checks received from customers that have not yet
been cashed by the bank. Checks recorded in RS Accounting as received
(counted in `paid_amount`) but not yet cleared (not counted in
`x_studio_actual_paid_amount`) represent a liquidity risk: La Verde
holds paper that has not converted to cash.

**Formula**
```
SUM(rs.installment.paid_amount)
  − SUM(rs.installment.x_studio_actual_paid_amount)
```
Portfolio-wide — no domain filter.

Reconciliation reference from `MODULE_2_BUSINESS_CONTEXT.md` §8:
```
Paid Amount − Actual Paid Amount
  = value of checks received but not yet cashed
```

**Data Source**
Model: `rs.installment`
Domain: none
Fields: `paid_amount` (native) and `x_studio_actual_paid_amount`
(Odoo Studio field, confirmed in `MODULE_2_DISCOVERY_PHASE_1.md` §3
and `MODULE_2_BUSINESS_CONTEXT.md` §8)

**Baseline Value (derived from 2026-05-14 snapshot)**
≈ 520.5M EGP  (3,491.18M Paid − 2,970.72M Actual Paid)
[PHASE 2 VERIFICATION REQUIRED — confirm this aggregate exists as a
queryable figure in Collections Mgmt, and that the subtraction matches
the live system's check pending exposure value]

**Display Format**
Large KPI card, `warning` variant (amber). Abbreviated: "520.5M EGP".
`warning` variant is appropriate because pending checks are not a
crisis but represent unresolved exposure. Uses the `kpi_card` macro.

**Refresh Frequency**
60-second cache (`CACHE_TTL_SECONDS`). Check status changes as the
treasury team processes checks in RS Accounting.

**Drill-Down Target**
Installments where (`paid_amount` − `x_studio_actual_paid_amount`) > 0,
sorted by that derived value descending. Columns: Customer Name,
Project, Pending Check Amount (EGP, derived), Paid Amount (EGP),
Actual Paid Amount (EGP).
[PHASE 2 NOTE: Once Phase 2 Gap #7 confirms that `check_pending_amount`
equals the derived subtraction, this drill-down query can be simplified
to filter on `check_pending_amount > 0` directly. Until then, the
derived formula is the canonical source.]

**Open Questions / Phase 2 Dependencies**
[PHASE 2 VERIFICATION REQUIRED] Discovery Phase 1 §3 reveals two
native check-related fields on `rs.installment`: `check_pending_amount`
and `check_approved_amount`. Whether `check_pending_amount` gives the
same aggregate as the `paid_amount − x_studio_actual_paid_amount`
subtraction — or a finer-grained breakdown — requires field-level
verification against a sample installment with known payment history
(Phase 1 Gap #5). Until confirmed, the derived formula is the
canonical source for both the KPI value and the drill-down filter.

---

#### KPI 4 — Collection Rate MTD & YTD / معدل التحصيل

**Definition**
The ratio of actual collected amount to total installment amount billed
in the period, expressed as a percentage. Two periods are shown
simultaneously: month-to-date (MTD) and year-to-date (YTD).

**Formula**
[PHASE 2 VERIFICATION REQUIRED for date field names]
```
Collection Rate % =
  SUM(rs.installment.x_studio_actual_paid_amount
      WHERE <payment_date_field> falls within period)
  ÷
  SUM(rs.installment.amount
      WHERE <due_date_field> falls within period)
  × 100
```
Period for MTD: first day of current calendar month to today.
Period for YTD: Jan 1 of current calendar year to today
(calendar-year assumption — pending confirmation, see Open Strategic
Question Q2).

**Data Source**
Model: `rs.installment`
Domain: date-range filter — exact field names not confirmed in Phase 1
(see Open Questions below)
Fields: `x_studio_actual_paid_amount` (Studio field, confirmed),
`amount` (native, confirmed)

Payment-date filtering note: The payment posting date is on
`rs.account.payment.installment`, NOT on `rs.installment`. The
payment-date period filter uses the join path documented in
`MODULE_2_DISCOVERY_PHASE_2.md §6.4`:
  rs.installment ← line.installment_id, line.payment_id → header.date
In Odoo domain terms: query `rs.account.payment.installment.line`
with `[('installment_id', 'in', <ids>), ('payment_id.date', ...)]`
and aggregate amount by month.

**Baseline Value (from 2026-05-14 snapshot)**
Not available. The 2026-05-14 snapshot in `MODULE_2_BUSINESS_CONTEXT.md`
§9 shows all-time portfolio totals, not period-specific figures.
Initial baseline values for MTD and YTD will be established by Khaled
running the query against live Odoo after Phase 2 confirmation.

**Display Format**
Dual-stat KPI card showing both percentages side by side on one card:
"MTD: XX%" and "YTD: XX%". Color variant: `success` if rate is above
a target threshold (threshold to be set by Khaled); `warning` if below.
Uses the `kpi_card` macro; dual-stat display may require a minor
extension to the macro's value slot.

**Refresh Frequency**
60-second cache (`CACHE_TTL_SECONDS`). Rate changes as payments post
throughout the day.

**Drill-Down Target**
Month-by-month YTD breakdown table: Month | Total Amount Due | Actual
Paid | Collection Rate %. A small per-month bar chart appears above the
table. Allows the Chairman to identify which months performed well or
poorly within the year.

**Open Questions / Phase 2 Dependencies**
Date field dependency resolved — see `MODULE_2_DISCOVERY_PHASE_2.md §6.4`.
Two remaining dependencies:
1. Denominator definition — see Open Strategic Question Q1.
2. YTD period definition — calendar year vs fiscal year, see Open
   Strategic Question Q2.

---

#### KPI 5 — Top 3 Projects Performance / أداء المشاريع الثلاثة

**Definition**
Side-by-side comparison of collection rate and late uncollected for
each of the three live projects (New Capital, Cassette, La puerta),
allowing the Board to compare portfolio performance across projects at
a glance.

**Formula**
For each project, filtered by `rs.installment.project_id`:

```
Late Uncollected (per project) =
  SUM(rs.installment.due_amount)
    WHERE project_id = <project_id>
    AND <Late installment domain>
    [PHASE 2 VERIFICATION REQUIRED — same Late domain as KPI 2]

Collection Rate (per project) =
  SUM(rs.installment.x_studio_actual_paid_amount
      WHERE project_id = <project_id>
      AND <date_field> in period)
  ÷ SUM(rs.installment.amount
        WHERE project_id = <project_id>
        AND <date_field> in period)
  × 100
  [PHASE 2 VERIFICATION REQUIRED — same date field and denominator
   questions as KPI 4]
```

**Data Source**
Model: `rs.installment`
Grouping field: `project_id` (many2one to `rs.structure.project`,
confirmed in `MODULE_2_DISCOVERY_PHASE_1.md` §3)
Three known projects from installment sample data
(`MODULE_2_DISCOVERY_PHASE_1.md` §10):
- New Capital (inferred id=1)
- Cassette (inferred id=2)
- La puerta (inferred id=3)

**Baseline Value (from 2026-05-14 snapshot)**
Not available per project. The snapshot in
`MODULE_2_BUSINESS_CONTEXT.md` §9 shows portfolio totals only.
Per-project baseline values will be established by Khaled after Phase 2
confirmation.

**Display Format**
Three equal-width project cards arranged side by side. Each card shows:
project name, collection rate % (color-coded by variant), and late
uncollected EGP (abbreviated). Uses the `kpi_card` macro with `href`
drill-down link. On small screens, cards stack vertically.

**Refresh Frequency**
60-second cache (`CACHE_TTL_SECONDS`).

**Drill-Down Target**
Clicking a project card expands to that project's full five-column
breakdown: Amount / Paid Amount / Actual Paid Amount / Due Amount /
Total Due Amount (all confirmed in `MODULE_2_BUSINESS_CONTEXT.md` §8
and `MODULE_2_DISCOVERY_PHASE_1.md` §3). Below the totals row: top 20
late customers for that project sorted by `due_amount` descending,
with `payment_state` column. Read-only.

**Open Questions / Phase 2 Dependencies**
[PHASE 2 VERIFICATION REQUIRED] Two dependencies:
1. `rs.structure.project` record IDs and names not formally confirmed
   — Phase 1 picked the wrong sub-model (`rs.structure.project.type`).
   The three project names (New Capital, Cassette, La puerta) and IDs
   (1, 2, 3) are inferred from `rs.installment` sample records, not
   from a verified `search_read` on `rs.structure.project`
   (`MODULE_2_DISCOVERY_PHASE_1.md` §10).
2. Same Late domain and date field dependencies as KPI 2 and KPI 4.

See also Open Strategic Question Q3 (should all 3 projects always
appear, or only those with active late uncollected > 0?).

---

#### KPI 6 — 6-Month Collection Trend / منحنى التحصيل — 6 أشهر

**Definition**
Monthly total of actual collected amounts over the trailing six
calendar months, showing whether the Collections department's cash
inflow is accelerating, stable, or declining.

**Formula**
[PHASE 2 VERIFICATION REQUIRED for date field name]
```
For each of the 6 trailing calendar months M:
  SUM(rs.installment.x_studio_actual_paid_amount)
    WHERE <date_field> falls within month M
```

**Data Source**
Model: `rs.account.payment.installment.line` (via join from `rs.installment`
— see `MODULE_2_DISCOVERY_PHASE_2.md §6.4`)
Domain: date-range filter covering trailing 6 months from today,
applied on `payment_id.date` (posting datetime on the header record)
Grouping: by `payment_id.date:month` (calendar month)
Field: `amount` on `rs.account.payment.installment.line`

Join path (`MODULE_2_DISCOVERY_PHASE_2.md §6.4`):
  rs.installment ← line.installment_id, line.payment_id → header.date

**Baseline Value (from 2026-05-14 snapshot)**
Not available. The 2026-05-14 snapshot shows cumulative totals only.
Monthly series will be populated from live Odoo after Phase 2
confirmation of the date field.

**Display Format**
Full-width line chart panel (canvas element, same pattern as the CRM
dashboard charts row in `frontend/templates/dashboard.html`). X-axis:
6 calendar month labels. Y-axis: EGP, abbreviated (M / B scale).
A subtle reference line at the period average helps the Chairman
identify above/below-average months instantly. Uses
`frontend/templates/components/_chart_container.html`.

**Refresh Frequency**
Hourly. The trend chart covers historical months and does not change
minute-by-minute. Hourly cache is appropriate and reduces Odoo load
for the most expensive query on the dashboard.

**Drill-Down Target**
Clicking the trend chart expands it to a full-width panel showing:
a larger chart canvas, plus a table below: Month | Total Amount Billed
| Actual Collected | Collection Rate %. This gives the Chairman
the exact figures behind each month's data point.

**Open Questions / Phase 2 Dependencies**
Date field dependency resolved — see `MODULE_2_DISCOVERY_PHASE_2.md §6.4`.
Remaining: see Open Strategic Question Q4 (should the month axis use
installment due date or payment posting date?).

---

### 3.3 Layout and Visual Hierarchy

The layout is designed so the Chairman's eye lands on the most
operationally critical number first — Late Uncollected — and
descends through decreasing urgency from there. Description follows
the visual order from top to bottom.

**Row 1 — Hero (full visual weight):**
Late Uncollected (KPI 2) occupies the first and most prominent
position. It is rendered as a full-width or half-width card at
substantially larger font than the secondary KPIs. The `danger`
variant (red) ensures it stands out even peripherally. No other
element on the page competes with it at first glance.

**Row 2 — Secondary KPI trio:**
Three equal-width cards in a single responsive row:
Total Portfolio Value (KPI 1) | Pending Check Exposure (KPI 3) |
Collection Rate MTD/YTD (KPI 4)

Total Portfolio Value (`info` variant) anchors the scale: it tells
the Chairman what the full pie is. Pending Check Exposure (`warning`
variant) sits adjacent as a risk metric against that pie. Collection
Rate rounds out the trio with the efficiency signal. On tablet (2-col
grid), KPI 4 wraps to a second line below KPIs 1 and 3.

**Row 3 — Project comparison:**
Top 3 Projects Performance (KPI 5) renders as three equal-width
project cards in a horizontal row, matching the width of Row 2.
Each card uses a condensed layout: project name prominent, collection
rate % and late uncollected EGP below it. On mobile the cards stack
vertically.

**Row 4 — Trend chart (full width):**
6-Month Collection Trend (KPI 6) occupies the full dashboard width as
a chart panel. It is the last element in the primary view because
trend analysis is secondary to snapshot numbers for Board review.

**Responsive grid classes** follow the same pattern as
`frontend/templates/dashboard.html`: `grid-cols-1 sm:grid-cols-2
lg:grid-cols-3` for the KPI rows, with the hero card spanning full
width or given a larger `col-span` class.

**Refresh indicator:** Live-update indicator (pulsing dot + timestamp)
is shown in the header bar, identical to the CRM dashboard. A manual
Refresh button allows Khaled to force a cache invalidation before
presenting to the Board.

---

### 3.4 Drill-Down Patterns

Drill-downs are accessed by clicking a KPI card. They open as an
in-page expanded panel or modal overlay (using
`frontend/templates/components/_modal.html`). All drill-down views
are read-only. No in-line editing, no action buttons that modify Odoo.

**KPI 1 — Total Portfolio Value drill-down:**
A paginated list of the top 50 customers (`partner_id` on
`rs.installment`) sorted by total `amount` descending. Table columns:
Customer Name, Project, Total Amount (EGP), Paid Amount (EGP), Due
Amount (EGP). Filterable by `project_id` via a dropdown. The Chairman
can see which customers represent the largest share of the portfolio
by committed value.

**KPI 2 — Late Uncollected drill-down:**
Top 50 customers with the highest `due_amount` on late installments,
sorted by `due_amount` descending. Table columns: Customer Name,
Project, Late Due Amount (EGP), Payment Status (`payment_state`).
Filterable by `project_id` and `payment_state`
(`unpaid` / `partial` — values confirmed in
`MODULE_2_DISCOVERY_PHASE_1.md` §3). An aging summary (total late due
> 30 days, > 60 days, > 90 days) [PHASE 2 VERIFICATION REQUIRED for
date field] appears above the list. The Chairman can see at a glance
which customers are the largest sources of late uncollected exposure.

**KPI 3 — Pending Check Exposure drill-down:**
Installments where (`paid_amount` − `x_studio_actual_paid_amount`) > 0,
sorted by that derived value descending. Columns: Customer Name,
Project, Pending Check Amount (EGP, derived), Paid Amount (EGP),
Actual Paid Amount (EGP).
[PHASE 2 NOTE: Once Phase 2 Gap #7 confirms that `check_pending_amount`
equals the derived subtraction, this drill-down query can be simplified
to filter on `check_pending_amount > 0` directly. Until then, the
derived formula is the canonical source.]

**KPI 4 — Collection Rate drill-down:**
Month-by-month YTD breakdown. A small bar chart (one bar per month)
appears at the top, with the current month highlighted. Below: a table
with columns Month | Total Amount | Actual Collected | Rate %. The
Chairman can identify which months performed above or below the YTD
average and spot seasonal patterns.

**KPI 5 — Top 3 Projects Performance drill-down:**
Clicking a project card opens that project's detail view. Top section:
five-column totals row for the project (Amount / Paid Amount / Actual
Paid Amount / Due Amount / Total Due Amount — all confirmed in
`MODULE_2_BUSINESS_CONTEXT.md` §8). Bottom section: top 20 late
customers for that project sorted by `due_amount` descending, with
`payment_state` column. The Chairman can assess any individual project
in depth without leaving the Collections module.

**KPI 6 — 6-Month Trend drill-down:**
Expanded full-width view with a larger chart canvas and a data table
below: Month | Billed (Amount EGP) | Collected (Actual Paid EGP) |
Rate %. The Chairman can read the exact figures behind each point on
the trend line and compare months numerically.

---

## 4. Pillar 2 — AI Chat (AR/EN)

### 4.1 Scope and Constraints

- **Bilingual:** Arabic (Egyptian dialect) and English. Language is
  detected from the user's question and the response is in the same
  language.
- **Data sources:** identical to the dashboard — `rs.installment`
  and related models via the Odoo read-only client.
- **Read-only absolute:** the AI chat cannot modify any Odoo record.
  `ALLOWED_METHODS` in `shared/odoo/client.py` never contains
  `create`, `write`, or `unlink`. Any user request that would require
  a write is declined with a clear explanation.
- **AI budget:** subject to `AI_MONTHLY_BUDGET_USD` ($10/month). The
  chat session tracks cost per message (`cost_usd` on `ChatMessage`,
  following the pattern in
  `backend/modules/crm/ai/chat/schemas.py`).
- **Intent caching:** `AICache` with 30-minute TTL. Identical phrasing
  within the TTL window returns a cached result at zero API cost,
  following the `IntentCache` pattern in
  `backend/modules/crm/ai/chat/intent_parser.py`.
- **WhatsApp-first:** any AI output that involves suggesting customer
  contact defaults to WhatsApp as the first channel. This applies
  even in Board-level responses that mention contacting a customer —
  the channel is not left ambiguous.

---

### 4.2 Intent Capability Matrix

Pattern reference: `backend/modules/crm/ai/chat/prompts.py`
(`ALLOWED_INTENTS`, `INTENT_PARSING_SYSTEM_PROMPT`).

| Intent Category | Example Questions (AR) | Example Questions (EN) | Data Needed | MVP Status |
|---|---|---|---|---|
| Portfolio snapshot | "إيه وضع المحفظة دلوقتي؟" / "كام إجمالي المتأخرات؟" / "عرضلي ملخص سريع للتحصيل" | "What's the current portfolio status?" / "What's the total late uncollected?" / "Give me a quick collections summary" | `rs.installment` aggregate fields (all 5 amount columns) | ✅ MVP |
| Project-level status | "إيه وضع التحصيل في La puerta؟" / "New Capital بتحصل كويس؟" / "عرضلي أداء Cassette" | "How is collection in La puerta?" / "What's New Capital's collection rate?" / "Show me Cassette performance" | `rs.installment` filtered by `project_id` | ✅ MVP |
| Top late customers | "مين أكبر 10 عملاء متأخرين؟" / "عرضلي العملاء اللي عندهم أكبر متأخرات" / "رتبلي العملاء حسب المتأخر" | "Who are the top 10 late customers?" / "Show me customers with the highest overdue amounts" / "Rank customers by late uncollected" | `rs.installment` with Late domain filter + `partner_id` [Late domain: PHASE 2] | ✅ MVP (Late domain dependent) |
| Collection trends | "إيه منحنى التحصيل في الستة أشهر اللي فاتت؟" / "التحصيل بيتحسن ولا بيتراجع؟" / "قارن التحصيل شهر يناير بفبراير" | "What's the collection trend over the last 6 months?" / "Is collection improving or declining?" / "Compare January vs February collection" | `rs.installment` with date grouping on `x_studio_actual_paid_amount` [date field: PHASE 2] | ✅ MVP (date-field dependent) |
| Specific customer lookup | "عرضلي تاريخ أقساط العميل X" / "كام قسط متأخر عند العميل ده؟" / "إيه payment_state للعميل ده؟" | "Show me customer X's installment history" / "How many late installments does customer X have?" / "What is customer X's payment status?" | `rs.installment` filtered by `partner_id` | ✅ MVP |
| Penalty summary | "كام غرامة في المحفظة؟" / "إيه إجمالي الغرامات المستحقة؟" / "عرضلي الغرامات حسب المشروع" | "How many penalties are in the portfolio?" / "What's the total penalty due amount?" / "Show penalties by project" | `rs.installment` filtered by `installment_type_id` for Penalties type [type IDs: PHASE 2 Gap #3] | ✅ MVP (installment type IDs dependent) |
| Check exposure summary | "إيه قيمة الشيكات المعلقة؟" / "كام شيك لسه ما اتصرفش؟" / "عرضلي الأقساط اللي عندها شيكات معلقة" | "What's the total pending check exposure?" / "How many checks are still undeposited?" / "Show installments with pending checks" | `rs.installment.paid_amount` − `rs.installment.x_studio_actual_paid_amount` | ✅ MVP |
| Conversational (greeting / thanks / farewell / help) | "أهلاً" / "شكراً" / "مع السلامة" / "إيه اللي تقدر تعمله؟" | "Hello" / "Thank you" / "Goodbye" / "What can you help me with?" | None | ✅ MVP |
| Write operations (any action modifying Odoo) | — | "Add a penalty for customer X" / "Mark installment #123 as paid" / "Send a reminder to customer X" | — | ❌ Out of scope — `ALLOWED_METHODS` in `shared/odoo/client.py` never contains `create`, `write`, or `unlink`. This rule is architectural and absolute. |
| Sales employee performance in collections | — | "Which sales employee collected the most this month?" / "Show me collection performance by موظفي مبيعات" | — | ❌ Out of scope — موظفي المبيعات do not perform collections. Collection is owned exclusively by the Collections department under Accounting. Attributing collection performance, activity, or responsibility to sales employees is organizationally invalid. This row is included to make the exclusion explicit at the intent level. |

---

### 4.3 AI Output Tone and Conventions

**Conciseness:** The Chairman is not a data analyst. AI responses are
capped at three short paragraphs. If the answer is a single number,
lead with the number in the first sentence — do not bury it.

**Arabic responses:** Egyptian dialect (عامية مصرية), not formal
Modern Standard Arabic. Matches how Board members communicate
internally. Example: "إجمالي المتأخرات دلوقتي 312 مليون جنيه" — not
"يبلغ إجمالي المتأخرات حالياً ثلاثمائة واثنا عشر مليون جنيه".

**Number formatting:** Always in EGP. Abbreviated for readability:
"312.6M EGP" or "312.6 مليون جنيه". Exact figures available on
request. No other currency unless explicitly asked.

**No emojis:** Consistent with the Egyptian corporate register and
the Board audience. No emojis in any AI output.

**WhatsApp-first:** When a response involves suggesting customer
contact, the default channel is WhatsApp
("تواصل معاه على واتساب"), not phone calls, email, or in-person
visits. This is a hard convention regardless of context.

**Follow-up suggestions:** Each response ends with 2–3 concrete,
data-grounded follow-up questions the Chairman might ask next —
following the same pattern as `FALLBACK_FOLLOWUPS` in
`backend/modules/crm/ai/chat/prompts.py`. Questions must be
answerable by one of the defined intents. No open-ended meta-questions
("هل تحتاج أي شيء آخر؟").

---

## 5. Out of Scope (Explicit)

### From MODULE_2_BUSINESS_CONTEXT.md §16 (relevant to design)

- General ledger reporting — Standard Odoo Accounting handles this
- Tax / VAT handling — Standard Odoo Accounting handles this
- Vendor bills / accounts payable — not a collections concern
- Bank reconciliation — Standard Odoo handles this
- Manual installment status changes — `state` and `payment_state` on
  `rs.installment` are set automatically by the system; the
  Collections Officer does not set them manually and neither does
  this module
- Writing back to Odoo — read-only rule is absolute
- Replacing Collections Mgmt, RS Accounting, or Accounting apps —
  this module is an intelligence layer on top of them
- Collections Officer daily operational workflow — per-installment
  manual actions, follow-up logging (`rs.followup`), and the
  daily filter-by-status views of Collections Mgmt remain in
  the existing Odoo app
- Per-salesperson operational dashboards — not a Board-level concern
  in this MVP
- Predictive analytics, alerts, and notifications — deferred until
  the Board confirms what is "alert-worthy" through actual use

### Design-specific exclusions

- Predictive late payment scoring — no historical training data has
  been prepared; adding an unvalidated model to a Board-level tool
  is premature
- Cash flow projections beyond the current snapshot — projection
  models require assumptions about future payment behavior that
  have not been validated
- Alert and notification delivery (email, push, WhatsApp broadcast) —
  deferred; the Board must first establish what constitutes an
  alert-worthy threshold through actual use of the snapshot view
- Multi-language beyond Arabic and English — not a stated Board
  requirement
- Mobile-first UI — the Board uses desktops and tablets; mobile-first
  optimization is deferred
- Custom report generation or export by the Chairman — read access
  to the Odoo source data is sufficient for MVP; export features
  deferred
- Sales-employee attribution of collection performance —
  موظفي المبيعات do not collect receivables. Collection responsibility
  belongs to the Collections department under Accounting.
  Any KPI, AI intent, drill-down, or feature that attributes
  collection performance, activity, or responsibility to sales
  employees is organizationally invalid and explicitly excluded from
  every layer of this design.

---

## 6. Design References

### Primary visual reference

The CRM module dashboard (`backend/modules/crm/` +
`frontend/templates/dashboard.html`) is the visual and architectural
baseline for the Collections dashboard. Both modules share the same
Jinja templating system, Tailwind CSS utility classes, and Chart.js
canvas approach. The Collections dashboard is a more refined version
of this pattern: fewer, larger KPI cards with more visual weight,
calibrated for an executive audience rather than an operational one.

### Component reuse from frontend/templates/components/

| Component | File | Usage in Collections dashboard |
|---|---|---|
| KPI card | `_kpi_card.html` | All 6 KPIs. The `kpi_card` macro's `variant` parameter (`danger`, `warning`, `success`, `info`, `default`), `sparkline_metric`, and `href` drill-down parameters are all used. |
| Badge | `_badge.html` | `payment_state` labels (`unpaid`, `partial`, `paid`) on drill-down list views; project name labels on project cards. |
| Button | `_button.html` | Manual Refresh button in the dashboard header; drill-down close buttons. |
| Chart container | `_chart_container.html` | 6-Month Trend (KPI 6) line chart; per-month bar chart in the Collection Rate drill-down. |
| Empty state | `_empty_state.html` | Shown when a KPI query returns zero records (e.g., no late installments — a success state for the portfolio). |
| Skeleton loader | `_skeleton.html` | Displayed while KPI data loads on initial page render, using the same `animate-pulse` pattern as the CRM dashboard. |
| Toast | `_toast.html` | Cache-staleness warnings if Odoo is unreachable; data error notifications. |
| Modal | `_modal.html` | Drill-down overlay panels for all 6 KPIs; AI chat panel. |

### Collections vs CRM dashboard — key differences

The CRM dashboard is operational-grade: it includes a salesperson
heatmap, a data quality section, and an AI priority queue widget
that auto-surfaces individual leads. These are team-management tools
inappropriate for a Board audience. The Collections dashboard omits
all of these. In their place: larger KPI numbers, abbreviated EGP
values in the billion/million range, and a single chart panel for
trend visibility. The visual register is elevated — less dense, more
deliberate.

---

## 7. Phase 2 Discovery Dependencies

All items marked [PHASE 2 VERIFICATION REQUIRED] in this document are
consolidated here. This list is the primary input for Work Item #3
(Targeted Phase 2 Discovery). Items marked "New" were surfaced by the
design exercise and do not appear in Phase 1's gap list.

| # | Dependency | Blocks | Origin |
|---|---|---|---|
| 1 | **"Late" installment domain** — The exact Odoo filter that identifies an installment as "Late" in Collections Mgmt. Without this, KPI 2 cannot be implemented or validated against the 312.6M EGP baseline. | KPI 2, KPI 5 per-project late, AI intent `top_late_customers`, KPI 2 drill-down aging | New — surfaced by design |
| 2 | **Date field names on `rs.installment`** — No date field (due date, payment date, posting date) was confirmed in Phase 1. Both the MTD/YTD Collection Rate and the 6-Month Trend require a reliable date field to slice by period. | KPI 4, KPI 6, AI intent `collection_trend`, KPI 2 drill-down aging | New — surfaced by design |
| 3 | **Collection Rate denominator definition** — "paid vs billed for the period" requires defining what "billed" means in Odoo terms (Open Strategic Question Q1). | KPI 4, KPI 5 per-project rate, AI intent `project_collection_status` | New — surfaced by design |
| 4 | **`rs.structure.project` record IDs, names, and active status** — Phase 1 picked the wrong sub-model. Three project names and IDs (1 / 2 / 3) are inferred from installment samples, not formally confirmed. | KPI 5, AI intent `project_collection_status` | Phase 1 Gap #1 |
| 5 | **`rs.account.payment` and `rs.account.payment.installment` inventory** — The canonical payment models were not deep-dived. Understanding these is required to confirm what field holds the payment date (which drives Dependency #2). | KPI 4, KPI 6 date axis | Phase 1 Gap #2 |
| 6 | **`rs.installment.type` actual records (IDs, names, sequence)** — `installment_type_id` is a many2one to `rs.installment.type`, not a selection field. The 8 type names and their IDs must be fetched before any query can filter by type (e.g., penalty installments for the penalty summary intent). | AI intent `penalty_summary`, any type-filtered query | Phase 1 Gap #3 |
| 7 | **Reconciliation of `paid_amount − x_studio_actual_paid_amount` vs `check_pending_amount`** — KPI 3's formula is derivable from the snapshot, but the native `check_pending_amount` field on `rs.installment` may provide a more direct (and already-computed) value. Whether both approaches agree requires field-level verification against sample records. | KPI 3 formula validation, KPI 3 drill-down filter | Phase 1 Gap #5 |
| 8 | **Late installments with pending checks — query approach** — `MODULE_2_BUSINESS_CONTEXT.md` §15 notes this as an open item. Installments that are both late and have a pending check may be undercounted in the "Late" view. The exact query to identify them must be verified. | KPI 2 accuracy, KPI 3 overlap with KPI 2 | Business Context §15 open item |

**Note on Phase 1 Gap #4** (Special Payment Plan intermediate states):
Not a dependency of any MVP KPI or AI intent. Retained in
`MODULE_2_DISCOVERY_PHASE_1.md` but not a blocker for this design.

---

## 8. Open Strategic Questions

The following questions are not resolved by `MODULE_2_BUSINESS_CONTEXT.md`
or `MODULE_2_DISCOVERY_PHASE_1.md`. They are questions for Khaled.
No answers are proposed here — the questions themselves are the
deliverable. This design document cannot proceed to implementation
until all five questions are resolved by Khaled or explicitly deferred
with a documented decision.

---

**Q1 — Collection Rate denominator definition**

Should Collection Rate (KPI 4) denominator be: (a) all installments
whose due date falls in the period, or (b) the total portfolio amount
as of period end? These give very different percentages. Option (a)
measures "how much of what was due this month did we collect?" Option
(b) measures "how much of the total portfolio have we collected in
this period?" Neither is wrong — they answer different Board questions.

---

**Q2 — YTD period definition**

Should YTD reset on calendar year (January 1) or La Verde's fiscal
year? `MODULE_2_BUSINESS_CONTEXT.md` does not specify La Verde's
fiscal year start date. If La Verde's fiscal year differs from the
calendar year, the YTD figure shown to the Board will be
misinterpreted unless the period definition is explicit.

---

**Q3 — Top 3 Projects: always show all 3, or only active ones?**

Should Top 3 Projects (KPI 5) always display all three projects
(New Capital, Cassette, La puerta), or only those with active late
uncollected > 0? If one project becomes fully collected, should its
card remain on the dashboard as a "zero" — which would signal success
— or disappear to avoid clutter?

---

**Q4 — 6-Month Trend date axis: due date or payment date?**

Should the 6-Month Trend (KPI 6) use installment due date (what was
scheduled to be collected each month per the payment plan) or payment
posting date (when cash or a check was actually received)? The
due-date trend shows collection demand; the payment-date trend shows
actual cash inflow. Both are meaningful, but they tell different
stories and could give substantially different charts.

---

**Q5 — Pending Check Exposure baseline validation**

The Pending Check Exposure (KPI 3) baseline is derivable as ≈ 520.5M
EGP from the 2026-05-14 snapshot math (3,491.18M Paid −
2,970.72M Actual Paid). Was this figure reviewed with Khaled against
the live Collections Mgmt UI, or does it need explicit validation
before surfacing to the Board? A number of this magnitude (half a
billion EGP in un-cashed checks) may warrant a direct verification
call before it appears on a Board dashboard.

---

*End of document.*
