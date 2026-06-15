# Campaign Performance — Live-Data Discovery (numbers)

**Status:** Live read-only discovery run **2026-06-15** against `laverde.odoo.com`
(`crm.lead`, `utm.campaign`, `crm.stage`). **Discovery only** — no build, no app/router
change, no product decision. This document records the raw findings for product
review (Khaled + me) of a **campaign-CENTRIC** performance view — the sibling to
the shipped **media-buyer** attribution view.

**Method:** read-only script
[scripts/discover_campaign_performance.py](../scripts/discover_campaign_performance.py)
(`fields_get` / `search_count` / `read_group` / `search_read` only — `ALLOWED_METHODS`
untouched; no `create`/`write`/`unlink`; no FastAPI; no OpenAI; AI cost = **$0.00**).
The stage→group classification and the CONFIRMED/DENYLIST campaign config are
**imported read-only** from `backend/modules/marketing_attribution`, so every
per-campaign number is defined **identically** to the shipped module. Monthly
bucketing uses `search_read` + Python-side Cairo-local regroup (Decision 5.10).

**Locked definitions preserved (same as the shipped module):**
- Population = **ALL leads incl. archived** (`context active_test=False`).
- Stage groups (literal Arabic API values): **جديد** {New, New X, no-stage} ·
  **مهتم** {Follow up, Interested} · **اشترى** = `crm.stage.is_won=True` (dynamic) ·
  **بلا نتيجة** {the rest} — via `domain.classify_stage`.

> **Note on numbers:** the live DB mutates continuously. All figures are "as of
> 2026-06-15" and reproducible via the printed domains. Counts drift by a handful
> between runs (see the identity-check drift note in §A.4).

---

## A. Campaign inventory & funnel (core feasibility)

### A.1 Headline counts

| metric | value |
|---|---:|
| `utm.campaign` records (incl. archived) | **296** |
| `utm.campaign` records with ≥1 lead | **212** |
| `crm.lead` population (incl. archived) | **146,872** |

### A.2 Top 25 campaigns by lead volume — funnel + dominant buyer + attribution status

Funnel columns are the 4 locked groups: **New** = جديد · **Intrst** = مهتم ·
**Won** = اشترى · **NoRes** = بلا نتيجة. "conc" = dominant-buyer concentration
(blank = no buyer signal). Status is from the imported module config (§A.3).

| campaign | leads | New | Intrst | Won | NoRes | dominant buyer | conc | status |
|---|---:|---:|---:|---:|---:|---|---:|---|
| FB-AY | 39,544 | 9,881 | 2,066 | 81 | 27,516 | Ahmed Aymen | 100% | **confirmed** |
| **None** | 17,385 | 4,261 | 470 | 605 | 12,049 | — | — | none |
| Outsource-Y | 15,586 | 3,510 | 1,425 | 3 | 10,648 | Yomna Musaad | 100% | **confirmed** |
| FB-AM | 14,977 | 3,569 | 682 | 16 | 10,710 | Abdallah Maher | 100% | **confirmed** |
| FB-KM | 8,467 | 1,950 | 168 | 74 | 6,275 | — | — | none |
| FB-LA | 6,936 | 1,540 | 725 | 1 | 4,670 | Ali shaban | 100% | **confirmed** |
| FB-BM | 5,807 | 1,096 | 33 | 11 | 4,667 | — | — | none |
| FB-AD | 5,747 | 664 | 106 | 101 | 4,876 | — | — | none |
| FB-BA | 1,839 | 439 | 8 | 4 | 1,388 | — | — | none |
| Laverde team International | 1,506 | 302 | 44 | 7 | 1,153 | — | — | none |
| FB-OK | 1,348 | 307 | 167 | 0 | 874 | — | — | none |
| Broker camp | 1,105 | 327 | 5 | 0 | 773 | — | — | none |
| Casette | 1,048 | 255 | 8 | 14 | 771 | — | — | none |
| Gulf IN | 1,041 | 479 | 2 | 0 | 560 | — | — | none |
| International - Globalx | 831 | 148 | 5 | 4 | 674 | — | — | none |
| New Whatsapp | 828 | 161 | 19 | 23 | 625 | — | — | none |
| Whatsapp leads | 813 | 157 | 21 | 11 | 624 | — | — | none |
| Apartment | 800 | 153 | 16 | 7 | 624 | — | — | none |
| New Team Squad | 723 | 111 | 13 | 19 | 580 | — | — | none |
| Saudi Arabia 2020 | 634 | 128 | 8 | 3 | 495 | — | — | none |
| FB-AO | 589 | 105 | 16 | 7 | 461 | — | — | none |
| Data Retargeting | 565 | 138 | 14 | 2 | 411 | — | — | none |
| Al Khobar Sep Suadi 2025 | 552 | 277 | 72 | 0 | 203 | — | — | none |
| Future EXPO Feb 2025 | 538 | 232 | 46 | 1 | 259 | — | — | none |
| Google Ads | 535 | 129 | 6 | 20 | 380 | — | — | none |

**Concentration / long tail:**

| metric | value |
|---|---:|
| campaigns with ≥50 leads | 95 |
| campaigns with ≥500 leads | 27 |
| leads in the **top 10** campaigns | 117,794 (**80.2%** of all leads) |
| leads with **no campaign** (`campaign_id=False`) | **15** (0.01%) |

The distribution is heavily concentrated — the top 10 campaigns hold ~80% of all
leads, with a long tail of small campaigns below.

> **⚠ Data-quality flag:** the 2nd-largest "campaign" is a `utm.campaign` record
> (id=1677, active) **literally named `"None"`**, holding **17,385 leads (11.8%)**.
> This is a junk/placeholder label, *distinct* from the 15 leads that genuinely
> have no campaign (`campaign_id=False`). Several other large entries are also
> generic (`Apartment`, `Whatsapp leads`, `New Whatsapp`, `Broker camp`). How a
> campaign-centric view treats these is a product call, not made here.

### A.3 Attribution status (config imported read-only from the shipped module)

- Confirmed names `['FB-AM','FB-AY','FB-LA','Outsource-Y']` → ids `[1566,1569,1587,1688]`
- Denylist names `['BV - Daima','Website - Daima']` → ids `[1802,1803]`

| status | #campaigns | #leads |
|---|---:|---:|
| confirmed | 4 | 77,043 |
| pending (≥90% buyer, not yet confirmed) | 0 | 0 |
| denied | 2 | 250 |
| none (no ≥90% dominant buyer) | 206 | 69,564 |

Only the **4 confirmed campaigns carry a dominant media buyer** at ≥90%
concentration; every other campaign (206 of them, ~69.6k leads) has **no buyer
attribution** under the module's gate. A campaign-centric *funnel* does not require
a buyer, so this does not block a count-based view — but a per-campaign "media
buyer" column would be blank for ~90% of campaigns.

### A.4 Identity checks (PASS/FAIL)

| # | check | result |
|---|---|:--:|
| 1 | Σ per-campaign lead counts == total population (146,872 == 146,872) | **PASS** |
| 2 | each campaign's 4 stage-groups sum to its lead_count (213 buckets, 0 mismatches) | **PASS** |
| 3 | ATTRIBUTING-campaign rollup by dominant buyer == **live shipped module** | **PASS** |

Check #3 detail — the independent per-campaign rollup reproduces the shipped
module's `get_attribution_overview()` **exactly**, proving the campaign-centric
definition does **not** diverge from the module:

| buyer | rollup (independent) | live module | locked snapshot (2026-06-14) | drift |
|---|---:|---:|---:|---:|
| Ahmed Aymen | 39,544 | 39,544 | 39,522 | +22 |
| Yomna Musaad | 15,586 | 15,586 | 15,562 | +24 |
| Abdallah Maher | 14,977 | 14,977 | 14,977 | +0 |
| Ali shaban | 6,936 | 6,936 | 6,932 | +4 |

> The small **+drift vs the 2026-06-14 locked snapshot is live-DB growth** (all
> deltas small and positive — new leads since the lock), **not a definition
> divergence**. The rollup and the live module agree to the unit. Module reports
> `total_attributed=77,043`, `attribution_pct=52.46%`, `pending=0`, no integrity
> alerts, no config warnings.

---

## B. Cost / spend (make-or-break for any ROAS)

### B.1 `utm.campaign` fields (all 17 — none monetary)

`fields_get('utm.campaign')` returns **17 fields**. **No money/cost/budget/spend/
expense/amount field exists, and no `x_studio_*` field.** Full list:

`active`, `color`, `create_date`, `create_uid`, `crm_lead_count` (int, computed
"Leads/Opportunities count"), `display_name`, `id`, `is_auto_campaign`,
`medium_ids`, `name` (label "Campaign Identifier" — this is the campaign string
shown above), `stage_id`, `tag_ids`, `title` (label "Campaign Name"), `use_leads`,
`user_id`, `write_date`, `write_uid`.

### B.2 `crm.lead` flagged money-type / cost-like fields

`fields_get('crm.lead')` returns **172 fields**. The only monetary / cost-like
ones are **all revenue/forecast** — none represent ad-spend:

| field | type | label |
|---|---|---|
| `expected_revenue` | monetary | Expected Revenue |
| `prorated_revenue` | monetary | Prorated Revenue |
| `recurring_revenue` | monetary | Recurring Revenues |
| `recurring_revenue_monthly` | monetary | Expected MRR |
| `recurring_revenue_monthly_prorated` | monetary | Prorated MRR |
| `recurring_revenue_prorated` | monetary | Prorated Recurring Revenues |
| `payment_term_count` | integer | Payment Terms |
| `payment_term_ids` | one2many | Payment Terms |

`expected_revenue` is a **forecast** of revenue, **not** ad-spend (kept distinct).

### B.3 Verdict

> **VERDICT B — Ad-spend present in Odoo: NO.** There is no cost/budget/spend field
> on `utm.campaign` or `crm.lead` (or any `x_studio_*` money field). Any spend
> figure would have to come from an external source (e.g. the Meta/Google Ads
> platform), outside the current read-only Odoo scope.

---

## C. Revenue linkage (so اشترى can be EGP, not just a count)

WON (اشترى) is defined by `crm.stage.is_won=True`; the live won stages are
**Down Payment Confirm & Contracted, Draft Reservation, Initial Reservation,
Reservation**. **1,268** leads are currently in a won stage.

### C.1 `expected_revenue` (forecast)

- Present on `crm.lead`: **yes** (monetary).
- **Fill-rate on WON leads: 0 / 1,268 = 0.0%** — the field is **entirely empty** on
  won leads. Sum per campaign is therefore **0 for every campaign** (top 15 all 0).
- Even if filled, it is a *forecast*, not realized value.

### C.2 Realized-value relations on `crm.lead`

There is **no relation to `sale.order` or `account.move`** on `crm.lead`. The only
realized-value-adjacent relations are to the `rs.*` reservation/unit models, and
all fill at **≤2.1% on won leads**:

| field | → model | fill on won |
|---|---|---:|
| `unit_ids` | rs.structure.unit | 2.1% (26/1,268) |
| `building_id` | rs.structure.building | 1.6% (20/1,268) |
| `phase_id` | rs.structure.phase | 1.6% |
| `project_id` | rs.structure.project | 1.6% |
| `project_type_ids` | rs.structure.project.type | 1.6% |
| `reservation_ids` | rs.reservation | 1.6% (20/1,268) |
| `unit_id` | rs.structure.unit | 1.6% |
| `unit_type_id` | rs.structure.unit.type | 1.6% |
| `unit_view_id` | rs.structure.unit.view | 1.5% |
| `unit_finishing_type_id` | rs.structure.unit.finishing.type | 0.7% |
| `payment_term_ids` | rs.payment.term | 0.1% |
| `category_ids` | rs.structure.type | 0.0% |
| `zone_id` | rs.structure.zone | 1.6% |

`reservation_ids → rs.reservation` is the most semantically promising path to a
realized deal/price, but at **1.6% (20 of 1,268 won leads)** it is unusable for a
per-campaign value view. Reaching an actual EGP **price** would also require
read access to an amount field on the target `rs.*` model — not asserted here.

### C.3 Verdict

> **VERDICT C — Best per-campaign revenue signal = effectively NONE.** The
> auto-selected maximum is `unit_ids → rs.structure.unit` at **≈2.1%** fill on won
> leads; `expected_revenue` is **0%**. No signal reaches a usable fill-rate, so a
> per-campaign **EGP value** for اشترى is **not feasible on current data** —
> اشترى can only be reported as a **count**.

---

## D. Time feasibility (for current-period windows later)

- `crm.lead.create_date` present: **yes** (datetime).

### D.1 Lead-creation per month — last 12 Cairo-local months

| month | leads created |
|---|---:|
| 2025-07 … 2025-10 | 0 |
| **2025-11** | **129,151** |
| 2025-12 | 3,833 |
| 2026-01 | 3,723 |
| 2026-02 | 2,670 |
| 2026-03 | 1,460 |
| 2026-04 | 2,515 |
| 2026-05 | 2,422 |
| 2026-06 (partial) | 1,098 |
| **TOTAL** | **146,872** |

> **⚠ Critical caveat:** **129,151 of 146,872 leads (87.9%) share a create_date in
> 2025-11** — almost certainly a **bulk data migration/import**, not organic lead
> creation. So `create_date` reflects *when the row was imported*, not when the
> lead actually arrived, for the large majority of records. Genuinely organic
> monthly volume (post-import) is only ~1–4k/month (2025-12 onward).

### D.2 Activity span — top 10 campaigns (first & last lead, Cairo-local)

| campaign | leads | first lead | last lead |
|---|---:|---|---|
| FB-AY | 39,544 | 2025-11-15 15:57 | 2026-06-15 12:31 |
| None | 17,385 | 2025-11-15 15:57 | 2026-06-14 16:03 |
| Outsource-Y | 15,586 | 2025-11-15 15:57 | 2026-06-15 12:31 |
| FB-AM | 14,977 | 2025-11-15 15:57 | 2026-06-14 11:45 |
| FB-KM | 8,467 | 2025-11-15 15:57 | 2026-03-30 14:43 |
| FB-LA | 6,936 | 2025-11-15 15:57 | 2026-06-15 12:31 |
| FB-BM | 5,807 | 2025-11-15 15:57 | 2025-11-30 16:42 |
| FB-AD | 5,747 | 2025-11-15 15:57 | 2026-02-06 18:05 |
| FB-BA | 1,839 | 2025-11-15 15:57 | 2025-11-26 17:32 |
| Laverde team International | 1,506 | 2025-11-15 16:09 | 2026-01-31 14:39 |

> **Every** top-10 campaign's **first** lead is the **same import timestamp**
> (2025-11-15 15:57) — confirming the bulk import in §D.1, so "first lead" is **not**
> a usable cohort-start signal. **Last-lead** date, however, *is* informative for
> recency: FB-AY / Outsource-Y / FB-LA are still receiving leads today (2026-06-15);
> FB-BM (last 2025-11-30) and FB-BA (last 2025-11-26) look **dormant**.

---

## E. Feasibility per phase (NEUTRAL — what the DATA supports only)

This is a data-capability statement, **not** a design or phasing decision (those
are Khaled's + mine on review).

### Phase 1 — per-campaign funnel (count-based)
**Supported by the data.** `campaign_id` covers ~100% of leads (only 15 of 146,872
are campaign-less); `stage_id` ~99%; the 4-group funnel reuses the module's
`classify_stage` and reconciles exactly (identity checks 1–3 all PASS). 212
campaigns carry leads; concentration is high (top 10 = 80.2%). **Data caveats for
review:** (a) the junk campaign literally named **"None"** (17,385 leads) and other
generic labels; (b) a per-campaign **dominant-buyer / media-buyer** column would be
populated for only the **4 confirmed campaigns** — every other campaign has no
≥90% buyer signal (status "none"). Neither caveat blocks a *count-based funnel*.

### Phase 2 — per-campaign revenue (اشترى in EGP)
**Not supported by current data.** `expected_revenue` is 0% filled on won leads;
realized-value links (`rs.*`) fill ≤2.1% on won leads; there is no `sale.order` /
`account.move` relation on `crm.lead`. اشترى can be a **count** but **not an EGP
value** read-only today.

### Phase 3 — ROAS (return on ad spend)
**Not supported by current data.** ROAS needs both a **spend** numerator and a
**realized-revenue** denominator. Spend: **absent from Odoo entirely** (Verdict B).
Revenue: **not reliably reachable** (Verdict C). Neither side exists, so ROAS would
require an external ad-spend source *and* a realized-revenue join — both outside the
current read-only Odoo scope.

---

## F. Bulk identification (feasibility of a per-bulk timeline)

**Status:** separate live read-only run **2026-06-15** via
[scripts/discover_campaign_bulks.py](../scripts/discover_campaign_bulks.py)
(`fields_get` / `search_count` / `search_read` only — `ALLOWED_METHODS` untouched;
no `create`/`write`/`unlink`; no FastAPI; no OpenAI; **$0.00 AI**). Same locked
definitions (population = ALL incl. archived, `active_test=False`; Cairo-local via
`ZoneInfo`; `marketing_attribution` config imported read-only). **Targets = the 4
CONFIRMED campaigns ∪ the top-5 by lead volume** = `FB-AY, None, Outsource-Y, FB-AM,
FB-KM, FB-LA`.

This section answers **only**: can per-campaign lead "bulks" (batch uploads) be
identified, by what, and are there enough? It makes **no design decision** — that is
Khaled's + mine on review.

### F.1 Legacy migration cluster (Probe 1)

The migration is **not** a single day — it is **three migration-scale days** of
2,000-row import chunks, all by one user:

| Cairo day | leads created | |
|---|---:|---|
| 2025-11-15 | 63,978 | ← migration-scale |
| 2025-11-16 | 33,287 | ← migration-scale |
| 2025-11-26 | 28,504 | ← migration-scale |
| 2025-11-19 | 632 | (organic) |
| 2025-11-21 | 404 | (organic) |
| … | | |

- **Legacy definition used:** Cairo day(s) with ≥ 10,000 leads = **{2025-11-15,
  2025-11-16, 2025-11-26}** → **125,769 leads (85.6%** of the 146,872 population**)**,
  excluded from Probes 2–4.
- **Burst shape:** 79 distinct exact `create_date` timestamps, the largest each
  holding **exactly 2,000 leads** (textbook import chunking), Cairo span
  **2025-11-15 15:57:34 → 2025-11-26 18:57:10**.
- **Author corroboration:** **99.9%** of legacy leads (125,598/125,769) were created
  by **`Administrator`** (3 distinct authors total) — consistent with a staff-run
  migration, not organic intake.

> This **refines §D.1**, which attributed the import to *2025-11-15 only*. The import
> actually ran across **three sessions** (15th, 16th, 26th). **Caveat for the design:**
> excluding whole calendar days is conservative — it may also drop a handful of
> *genuine same-day* organic bulks on those 3 dates. A production build could instead
> exclude the migration **surgically** (by `create_uid = Administrator`, the 2,000-row
> chunk signature, or the migration `import_batch_no` range); doing so would only
> **raise** the post-migration bulk counts below, never lower them.

### F.2 Post-migration bulk clustering (Probe 2 — the core question)

Excluding the legacy days, leads were grouped by **exact `create_date` timestamp**. A
**"clear" cluster** = a timestamp shared by **≥ 10 leads** (a candidate bulk).
Cleanliness = % of a campaign's post-migration leads that fall in clear clusters:

| campaign | post-mig leads | exact-sec % | same-min % | same-day % | clear clusters | singletons |
|---|---:|---:|---:|---:|---:|---:|
| FB-AY | 6,249 | **93.6%** | 93.6% | 98.4% | 167 | 208 |
| Outsource-Y | 6,009 | **95.6%** | 95.6% | 99.0% | 171 | 153 |
| FB-AM | 2,397 | **92.1%** | 92.1% | 96.5% | 82 | 67 |
| FB-LA | 3,386 | **88.9%** | 88.9% | 93.3% | 123 | 67 |
| None *(junk label)* | 258 | 51.2% | 51.2% | 68.6% | 5 | 82 |
| FB-KM *(dormant)* | 6 | 0.0% | — | — | 0 | 6 |
| **ALL TARGETS (agg.)** | **18,305** | **92.6%** | | | | |

Key observations:
- **Bulks land on a single second.** `exact-second %` equals `same-minute %` for every
  campaign — each bulk is one batched insert at one timestamp, so minute/day grouping
  add nothing for the real campaigns (day-level only *inflates* by merging distinct
  uploads).
- **Bulks are cross-campaign.** The very same timestamps recur across FB-AY /
  Outsource-Y / FB-AM / FB-LA (e.g. `2025-11-18 14:45:37`, `2025-11-19 16:30:05`,
  `2025-11-30 17:21:38`). A single **upload event** delivers leads to several
  campaigns at once; a per-campaign bulk is that campaign's **slice** of the event.
- The two non-qualifying targets are the known **junk `None`** label (PARTIAL, 5
  bulks) and **FB-KM**, which is effectively **legacy-only** (only 6 of its 8,467
  leads are post-migration — it stopped receiving bulks).

Example — FB-AY's first candidate bulks (timestamp Cairo · size): `2025-11-18 14:45:37
· 76`, `2025-11-19 16:30:05 · 80`, `2025-11-22 16:04:20 · 96`, `2025-12-03 15:52:18 ·
130`, `2025-12-06 14:33:36 · 117` … (167 clear clusters total).

> **VERDICT F.2 — Bulks identifiable via `create_date`: CLEAN.** Aggregate **92.6%**
> of post-migration target leads fall in clear ≥10-lead exact-timestamp clusters
> (CLEAN ≥ 80% / PARTIAL ≥ 40% / SCATTERED below). All four CONFIRMED campaigns are
> individually **88.9–95.6%** clean.

### F.3 Batch identifier field (Probe 3)

`crm.lead` carries a **dedicated** batch field — **`import_batch_no`** (char, label
*"Import Batch No."*, values like `IB00007 … IB22327`):

| field | kind | all-leads fill | post-mig fill |
|---|---|---:|---:|
| `import_batch_no` | **batch-like** | 47,390 (32%) | **18,898 (90%)** |
| `source_id` | channel | 146,822 (100%) | 21,094 (100%) |
| `medium_id` | channel | 146,749 (100%) | 20,996 (99%) |
| `referred` / `referred_by_id` | channel | ≤ 4 (0%) | ≤ 4 (0%) |

Tested against the Probe-2 clusters on post-migration target leads (16,169 filled):

- **263** distinct batch numbers vs **271** distinct exact timestamps;
- **value → single timestamp = 100%** and **timestamp → single value = 100%** — i.e.
  `import_batch_no` is **effectively 1:1 with the `create_date` clusters**;
- **208 / 263** batch numbers span **more than one campaign** — independently
  confirming bulks are **cross-campaign upload events**.

`source_id` / `medium_id` fill at ~100% but are marketing-**channel** labels (each
spans many bulks over time), so they are **not** per-bulk identifiers despite the
high fill.

> **VERDICT F.3 — Reliable batch-id field: `import_batch_no`.** It exists, is ~90%
> filled post-migration, and partitions leads **identically** to `create_date`
> clustering — the two signals corroborate each other. (Where `import_batch_no` is
> blank, `create_date` clustering remains the fallback.)

### F.4 Sizing & sufficiency (Probe 4)

A **bulk** = a clear exact-timestamp cluster (≥ 10 leads); **meaningful** = size ≥ 30;
**sufficient for a trend** = ≥ 3 meaningful bulks.

| campaign | bulks | meaningful (≥30) | min | median | max | span (Cairo) | enough for a trend? |
|---|---:|---:|---:|---:|---:|---|:--:|
| FB-AY | 167 | 80 | 10 | 28 | 130 | 2025-11-18 → 2026-06-15 | **YES** |
| Outsource-Y | 171 | 87 | 10 | 30 | 104 | 2025-11-18 → 2026-06-15 | **YES** |
| FB-AM | 82 | 24 | 10 | 22 | 106 | 2025-11-18 → 2026-04-19 | **YES** |
| FB-LA | 123 | 31 | 10 | 21 | 82 | 2025-11-18 → 2026-06-13 | **YES** |
| None *(junk)* | 5 | 1 | 20 | 21 | 45 | 2025-11-25 → 2026-05-19 | NO |
| FB-KM *(dormant)* | 0 | 0 | — | — | — | — | NO |

> **VERDICT F.4 — All four CONFIRMED campaigns have ample bulks for a trend** (24–87
> meaningful bulks each over a ~5–7 month span). The junk `None` label and the dormant
> FB-KM do not (and are not meaningful campaigns to trend).

### F.5 Maturation feasibility (Probe 5 — bonus)

Stage-timing / outcome-dating fields on `crm.lead`, with population fill-rate:

| field | type | fill | note |
|---|---|---:|---|
| `won_status` | selection | 100% | *current* state ("Is Won"), not a timestamp |
| `date_last_stage_update` | datetime | 100% | only the **last** transition (overwritten each move) |
| `date_open` | datetime | 100% | "Assignment Date" |
| `date_closed` | datetime | **59%** | "Closed Date" — set on won **or** lost/archived |
| `activity_date_deadline` | date | 9% | next-activity, not an outcome |
| `date_conversion` | datetime | 0% | (204 leads) |
| `date_deadline` / `my_activity_date_deadline` | date | ~0% | — |

> **VERDICT F.5 — Maturation is PARTIALLY feasible.** A "conversion at a fixed age
> (e.g. 30 days)" needs a per-lead **outcome timestamp** to age each bulk. The only
> outcome-dating field with real fill is **`date_closed` (59%)**, and it conflates
> **won and lost**; `date_last_stage_update` is 100% but records only the *latest*
> move (no per-stage entry history), and there is **no dedicated `date_won`**. So a
> *won-vs-lost-by-30-days* split is reachable only for the ~59% with `date_closed`;
> a precise "stage at exactly N days" is **not** reconstructable read-only. (Feasibility
> only — nothing computed here.)

### F.6 Bulk-identification feasibility verdict (NEUTRAL)

A data-capability statement for the **per-bulk-timeline** design — **not** a design or
phasing decision (those are Khaled's + mine on review):

- **Are bulks identifiable?** **YES — and via two independent, agreeing signals.**
  Exact `create_date` clustering is **CLEAN** (92.6% aggregate; 88.9–95.6% per
  CONFIRMED campaign), and a dedicated **`import_batch_no`** (`IBxxxxx`, ~90% filled)
  is **1:1** with those clusters.
- **Via what?** A **bulk = an upload event**, keyed by an exact `create_date`
  timestamp (≡ `import_batch_no`). It is **cross-campaign**: one upload feeds several
  campaigns, and a per-campaign bulk is that campaign's slice — a per-campaign timeline
  is the **campaign × upload-event** intersection.
- **Are there enough?** **YES for all four CONFIRMED campaigns** (24–87 meaningful
  ≥30-lead bulks each across ~5–7 months) — enough points for a per-campaign
  rise/fall trend. **Not** for the junk `None` label or the dormant FB-KM.
- **Is maturation measurable?** **PARTIALLY** — `date_closed` (59%, won+lost) is the
  only usable outcome timestamp; no `date_won` and no per-stage entry history, so raw
  current conversion is fully available but a precise fixed-age ("stage at 30 days")
  conversion is not reconstructable read-only.

**Open items for the design review (flagged, not decided):** (a) how to exclude the
3-day legacy migration — conservative whole-day vs surgical `create_uid`/`import_batch_no`;
(b) the **cross-campaign** nature of a bulk (shared upload event); (c) the junk
**`None`** campaign and dormant **FB-KM**; (d) whether maturation uses raw current
conversion or a `date_closed`-based won/lost-by-age proxy.

---

*Discovery only. No KPI designed, no product/phasing decision made, no Odoo write.
Read-only, aggregates only, $0.00 AI. Reproducible via the domains in
[scripts/discover_campaign_performance.py](../scripts/discover_campaign_performance.py)
and [scripts/discover_campaign_bulks.py](../scripts/discover_campaign_bulks.py).*
