# Projects / Inventory — Pricing-Data Discovery (LIST vs REALIZED)

**Status:** Read-only discovery. No code, no commit. Decision document for two pending slices.
**Date:** 2026-06-19 &nbsp;|&nbsp; **Odoo:** laverde.odoo.com (Odoo 17), JSON-RPC, READ-ONLY &nbsp;|&nbsp; **AI cost:** $0.00
**Method:** `fields_get` / `search_count` / `search_read` / `read_group` only — every field confirmed via `fields_get` before use. No write methods touched. Probe scripts deleted (nothing committed).
**Companion to:** `docs/PROJECTS_INVENTORY_DISCOVERY.md` (hierarchy/field/readiness discovery).

---

## 0. Executive summary

There **are two different prices** in this data, and they diverge:

| | Field | What it is | Coverage |
|---|---|---|---|
| **LIST** | `rs.structure.unit.amount` | Current list price, bulk-reloaded May 2026. Uniform-ish, indicative. | 1,743 / 1,873 units (93.1%) |
| **REALIZED** | `rs.contract.sales_price` (stored) | The actual contracted deal price. `== installments_total` per contract. | 1,433 / 1,437 contracts (99.7%), ≈ all 1,435 contracted units |

**A reliable realized-sold-price source EXISTS** — `rs.contract.sales_price` — and it is **NOT** equal to `unit.amount`. Across 1,433 contracted+priced units: realized **==** list on 47.4%, realized **<** list on 47.8% (real deal discounts), realized **>** list on 4.8%. In aggregate, **realized is 9.0% below list** (Σ list 6.49 bn vs Σ realized 5.90 bn EGP).

**The unit-level discount fields are a dead end.** `unit.discount_amount` is non-zero on **only 1 of 1,873 units**, and `unit.amount_after_discount` is just `amount − discount_amount` (so it equals `amount` everywhere). **Real discounts live in the gap between `unit.amount` and `contract.sales_price`**, invisible on the unit.

**Verdicts:**
- **(a) Slice 2 (Value & Area): GO now** for New Capital + Cassette. Honestly shows LIST/indicative inventory value + area + avg price/m². It may **additionally** show *realized* value for **sold** units straight from `contract.sales_price` (a genuine upgrade — see §7). It must never present Σ `unit.amount` of sold units as revenue (~9% too high).
- **(b) Pricing-variance analysis: feasible**, with `contract.sales_price` (or `meter_price`) as the realized basis. Discounts must be **derived** as `list − realized`; payment-term normalization is **unnecessary** (≈99.6% of deals are installment — see §4). Peer grouping is workable but needs a coarse key (high `unit_type_id` cardinality + view/finishing coverage gaps fragment fine groups).

---

## 1. Confirmed field semantics (`fields_get`)

### `rs.structure.unit` — price / discount / peer fields (all `monetary`, stored, unless noted)

| field | label | role |
|---|---|---|
| `amount` | Total Unit Price | **LIST price** (May-2026 reload) — indicative |
| `sale_price` | Sale Price | ≈ `amount` (mirror) |
| `unit_amount` | Unit Price | ≈ `amount` |
| `official_price` | Official Price | registration value |
| `meter_price` | Meter Price | price / m² (the best normalizer) |
| `discount_amount` | Discount Amount | **empty** — non-zero on 1/1,873 |
| `amount_after_discount` | Price After Discount | **computed `amount − discount_amount` ⇒ == `amount`** |
| `avg_amount` | Average Price per Meter | computed |
| `discount_line_ids` | → `rs.structure.discount` | per-unit discount lines (unused at scale) |
| `unit_type_id` | → `rs.structure.unit.type` | peer attr |
| `view_id` | → `rs.structure.unit.view` | peer attr |
| `finishing_type_id` | → `rs.structure.unit.finishing.type` | peer attr |
| `floor` | Floor (`char`) | peer attr |

### `rs.contract` (101 fields) — the realized side

| field | label | role |
|---|---|---|
| `unit_id` | → `rs.structure.unit` | join key (1 hop) |
| **`sales_price`** | Sales Price (**stored**) | **REALIZED contract price — authoritative** |
| `installments_total` | Installments Total (stored) | == `sales_price` (the payment plan sums to the price) |
| `installments_total_paid` | … Total Paid | collected-to-date (money side) |
| `payment_term_id` | → `rs.payment.term` | **1-per-contract, coded `PTxxxxx` — NOT a cash/installment classifier** |
| `state` | Status | `draft / legal / finance / engineering / confirm / delivered / cancel` |
| `date`, `reservation_date`, `delivery_date` | — | for the deferred Absorption slice |

`rs.reservation` mirrors this (`unit_id`, stored `sales_price`, `payment_term_id`). `rs.contract.unit_amount` / `unit_type_id` / `unit_view_id` etc. are **non-stored** mirrors of the unit.

### `rs.installment` (42,940 rows)
`unit_id`, `contract_id`, `amount`, `paid_amount`, `installment_type_id` → `rs.installment.type`
(*Reservation / Down Payment / Regular / Maintenance / Club / Garage / Penalty / Administrative Fees*),
`payment_type_id` → `rs.payment.type` (*'Payment Period': Once / Monthly / Quarterly / Semi-Annual / Annual*),
`state` (`draft/post/cancel`), `payment_state` (`unpaid/partial/paid`).
⚠️ **Σ raw `rs.installment.amount` per unit is NOT a clean price** — it bundles maintenance/club/garage/penalty. Use `contract.sales_price` / `contract.installments_total` (which exclude those) for the deal price.

### Access / empties
- `rs.account.payment` → **AccessError** (confirmed denied to the API user — as warned). Not needed; `rs.contract` carries everything.
- `rs.discount` → **0 records**. `rs.payment.plan` → 121. `rs.structure.discount` is the unit `discount_line_ids` target (negligible use).

---

## 2. Q1 — LIST vs REALIZED price

**LIST** = `unit.amount` (bulk May-2026 reload, indicative). **REALIZED** = `rs.contract.sales_price` (stored, authoritative).

- Coverage: **1,433 / 1,437 contracts (99.7%)** have `sales_price > 0`, covering **1,435 distinct units** (≈ every contracted unit). `installments_total` matches `sales_price` to the EGP in aggregate (both **5,903,773,531**) → the installment schedule reconciles 1:1 to the contract price.
- **Per-unit join (`contract.sales_price` ↔ `unit.amount`, 1,433 units):**

| relationship | units | share |
|---|---:|---:|
| realized **==** list (±1 EGP) | 679 | 47.4% |
| realized **<** list (deal discount) | 685 | 47.8% |
| realized **>** list (deal premium) | 69 | 4.8% |

- Aggregate: Σ list **6,486,636,328** vs Σ realized **5,903,773,531** → **realized is 9.0% below list.**
- realized/list ratio distribution: p05 **0.750**, p25 0.948, p50 **1.000**, p75 1.000, p95 1.000 → when discounted, typically **5–25% off**, occasionally deeper.

> **Authoritative realized-sold price = `rs.contract.sales_price`** (= `contract.installments_total`). `unit.amount` is **list/indicative only** — ~9% above realized in aggregate and different from realized on **~53%** of contracted units.

---

## 3. Q2 — Discounts

| check | result |
|---|---|
| `unit.discount_amount > 0` | **1 / 1,873 (0.1%)** — effectively empty |
| `unit.amount_after_discount > 0` | 1,743 / 1,873 (93.1%) — i.e. == `amount` wherever priced |
| `amount − discount_amount == amount_after_discount` | **1,743 / 1,743 priced units (100%, exact)** |

`amount_after_discount` is purely **computed list-minus-(empty)-discount**, so it equals `amount` and is **NOT** the realized deal price. **Discounts are realized inside `contract.sales_price`, not on the unit** — to surface a discount you must compute `unit.amount − contract.sales_price` (the 47.8% of units priced below list in §2). Example: unit `AVG270-1-G3-103` — `unit.amount` 5,586,500, `discount_amount` 0, but `contract.sales_price` 5,418,925 (a real ~3% discount, invisible on the unit).

---

## 4. Q3 — Payment terms (cash vs installment)

**There is no explicit cash/installment flag, and it doesn't matter — the population is ~homogeneously installment.**

- `payment_term_id` is **1-per-contract** (1,436 distinct terms / 1,437 contracts), auto-coded `PTxxxxx` — not a classifier.
- `rs.payment.type` ('Payment Period') values exist — *Once / Monthly / Quarterly / Semi-Annual / Annual* — but apply to installment **lines**: Quarterly dominates (42,238 lines), 'Once' 692 (down-payment lines), Monthly 10.
- Derived proxy = # of 'Regular' installments per contract:

| Regular installments / contract | contracts |
|---|---:|
| 1 (cash-like) | 6 |
| 2–6 | 28 |
| 7–12 | 81 |
| 13–36 | **1,227** |
| 37+ | 74 |

**≈99.6% of contracts are multi-installment plans; only 6 are single-payment.** So cash-vs-installment is **not a material split** and needs **no normalization** in the variance analysis — comparing realized prices across deals is apples-to-apples on payment structure.

---

## 5. Q4 — Peer-group attributes (`rs.structure.unit`)

| attribute | coverage | distinct values |
|---|---:|---:|
| `unit_type_id` | 1,855 / 1,873 (99.0%) | **163** (high cardinality) |
| `view_id` | 1,648 / 1,873 (88.0%) | 9 |
| `finishing_type_id` | 1,531 / 1,873 (81.7%) | 4 |
| `floor` (char) | 1,625 / 1,873 (86.8%) | 17 |

Peer grouping is **workable but needs a coarse key.** A strict (type + zone + view + finishing) group fragments badly: 163 unit-types × zones, with 12% of units missing a view and 18% missing finishing, yields many singleton groups (no peers to compare against). **Recommend** a coarser key — e.g. `zone_id` + `unit_type_id` (or unit-type + area band) — and **normalize on `meter_price`** (price/m², 99.9% populated) rather than absolute `amount`, to remove area as a confound.

---

## 6. Q5 — Priced universe by project

| project | units | `amount > 0` | `meter_price > 0` | `amount_after_discount > 0` |
|---|---:|---:|---:|---:|
| **New Capital** | 1,401 | 1,400 (99.9%) | 1,401 | 1,400 |
| **Cassette** | 334 | 334 (100%) | 334 | 334 |
| **La puerta** | 138 | **9 (6.5%)** | **137 (99.3%)** | 9 |

La Puerta's value gap is **confirmed on `amount`** (9/138). ⚠️ **New trap:** La Puerta's `meter_price` is populated on **137/138** units *without* a matching `amount` — so `meter_price > 0` overstates pricing readiness. For any **value** figure use `amount` (→ La Puerta excluded); `meter_price` alone is not a "priced" signal for La Puerta.

---

## 7. Q6 — Reconciliation sample (codes + amounts only, no PII)

6 contracted units, New Capital + Cassette. Columns: `unit.amount` (list) / `unit.amount_after_discount` / `contract.sales_price` (realized) / `contract.installments_total` / Σ raw `rs.installment`:

| unit code | type | area | list `amount` | after_disc | **realized `sales_price`** | inst_total | Σ raw installments |
|---|---|---:|---:|---:|---:|---:|---:|
| AG155-1-G1 | AG-Garden Apt | 220 | 3,300,000 | 3,300,000 | 3,300,000 | 3,300,000 | 3,300,000 |
| AG165-1-G2 | AG-Garden Apt | 230 | 3,500,000 | 3,500,000 | 3,500,000 | 3,500,000 | 3,500,000 |
| **AVG270-1-G3-103** | AVG-Garden Duplex | 315 | 5,586,500 | 5,586,500 | **5,418,925** | 5,418,925 | 5,418,925 |
| AVG280-1-G4-104 | AVG-Garden Duplex | 325 | 6,346,000 | 6,346,000 | 6,346,000 | 6,346,000 | 6,346,000 |
| AF190-1-101 | AF-Apartment | 190 | 2,755,000 | 2,755,000 | 2,755,000 | 2,755,000 | 2,758,007 |
| AF155-1-102 | AF-Apartment | 155 | 3,100,000 | 3,100,000 | 3,100,000 | 3,100,000 | 3,100,000 |

Reads: `amount_after_discount` always tracks `amount` (discount field empty). `contract.sales_price == installments_total` always. **AVG270** shows the realized discount (5.59M list → 5.42M realized, ~3%) that is **invisible** in `unit.discount_amount`. **AF190** shows Σ raw installments (2,758,007) drifting above `sales_price` (2,755,000) by a small extra (penalty/fee) — confirming raw installment sums are not a clean price.

---

## 8. Verdicts

### (a) Slice 2 — "Value & Area" — **GO now** (New Capital + Cassette)

Can honestly show, all badged **"current list pricing — May 2026 reload, indicative":**
- **Remaining-inventory value** = Σ `unit.amount` over `available` + `reserved` units (list basis).
- **Sold / total / remaining sellable area** (`total_area`, 100% on NC+Cassette) and **avg price/m²** (`meter_price`).
- **La Puerta excluded** from all value/area (9/138 priced on `amount`; `meter_price` is a decoy here).

Must NOT do:
- Present Σ `unit.amount` of **contracted** units as revenue / sold value — it is **~9% above realized** and wrong on ~53% of units.

Optional upgrade (now unlocked): show **realized sold value** for contracted units directly from `rs.contract.sales_price` (99.7% coverage) — a cleaner number than the earlier discovery's "route everything to Collections." This lets Slice 2 present *both* a list-value (remaining inventory) and a realized-value (sold) column without contradiction.

### (b) Pricing-variance / outlier analysis — **feasible**

- **Realized basis:** `rs.contract.sales_price` (= `installments_total`), 99.7% contract coverage ≈ all 1,435 contracted units. Reliable. (Or `meter_price` for an area-normalized list-policy view.)
- **Discounts:** derive as `unit.amount − contract.sales_price` — the unit discount fields are empty and unusable.
- **Payment terms:** **no normalization needed** — ≈99.6% installment; the 6 cash-like deals are immaterial.
- **Peer grouping + normalization:** group on a **coarse** key (`zone_id` + `unit_type_id`, or unit-type + area band) and compare on **`meter_price`** (price/m²), not absolute amount. Beware `unit_type_id` cardinality (163) and view/finishing coverage gaps (12%/18%) that thin fine-grained groups to singletons.
- **Two honest questions it can answer:** (1) *list-policy consistency* — are comparable units **listed** at consistent meter prices? (`unit.meter_price` by peer group); (2) *realized consistency* — are comparable units **selling** for consistent prices after discounts? (`contract.sales_price` / area by peer group). The realized view is the truer outlier signal.

### Caveats carried forward
- All pricing is **transitional** (bulk import Jul-2025, reprice May-2026). Keep the "indicative — under review" posture.
- Realized prices exist only where a contract exists (sold units). Available/reserved inventory has **list only** — that's correct and expected.

**(Original discovery — no code. Probe scripts deleted. Decision point for Khaled + Claude Chat.)**

---

## 9. Slice 2 — "Value & Area" BUILT + verified live (2026-06-19)

Slice 2 shipped as `backend/modules/projects_inventory/services/value_service.py` +
`/projects-inventory/value-area` (page) + `/api/v1/projects-inventory/value-area/overview`.
Every number below was recomputed **independently** against live Odoo and asserted
**identity-equal** to the service output by
`scripts/verify_projects_inventory_value_live.py` (READ-ONLY, $0 AI) — combined and per
project, plus an independent `search_count` triple-check and Σ-per-project == combined.

**Confirmed live IDs/fields:** New Capital = **1**, Cassette = **2**, La Puerta = **3**
(excluded). `unit.amount` (monetary, list), `unit.total_area` (float, m²), `unit.state`
(selection); `contract.sales_price` (monetary, == `installments_total` — 0 mismatches),
`contract.unit_id` (m2o → unit), `contract.state` (selection). Sold units (NC+Cassette)
carry **1,396 contracts, all `state='confirm'`**.

### Verified numbers (EGP / m²) — live 2026-06-19

| metric | Combined (NC+Cas) | New Capital | Cassette |
|---|---:|---:|---:|
| total units / available / sold | 1,735 / 287 / 1,400 | 1,401 / 201 / 1,166 | 334 / 86 / 234 |
| (a) available_list_value | 4,606,666,395.00 | 2,572,283,895.00 | 2,034,382,500.00 |
| (b) available_area (m²) | 67,724.07 | 45,003.07 | 22,721.00 |
| (c) sold_realized_value | 5,709,600,379.98 | 3,404,246,935.98 | 2,305,353,444.00 |
| (d) sold_contracted_area (m²) | 286,960.70 | 214,856.70 | 72,104.00 |
| (e) sold_list_value | 6,345,001,260.75 | 3,752,961,960.75 | 2,592,039,300.00 |
| (f) gap_abs / gap_pct | 635,400,880.77 / **10.01%** | 348,715,024.77 / 9.29% | 286,685,856.00 / 11.06% |
| capture_pct (realized ÷ list) | 89.99% | 90.71% | 88.94% |
| (g) % units below list | **47.60%** (664/1,395) | 46.26% (538/1,163) | 54.31% (126/232) |
| (h) avg realized / m² | 19,896.80 | 15,844.27 | 31,972.62 |
| (i) sold / with-contract | 1,400 / 1,395 | 1,166 / 1,163 | 234 / 232 |

### Locked computation decisions
- **Realized join:** per sold unit, realized = Σ `sales_price` over its **non-cancel**
  contracts. Exactly **one** sold unit (id 3608) carries two confirm contracts — one
  priced 0, one real (2,526,000) — so the per-unit sum is the clean deal value; no
  cancel contracts exist live, but they are excluded for robustness.
- **(g) denominator** = **sold-units-with-contract** (1,395), so numerator and
  denominator share the population that actually has a realized price.
- **Coverage:** **5 sold units have no contract at all** (ids 3637, 3936, 4235, 4800,
  4920). Per the locked formula, (e) sums `amount` over **all** sold units while (c)
  covers the 1,395 with a contract — so ≈74.7 M of the 635 M combined gap is missing
  contracts, not discount. Surfaced via the coverage line + the page caveats; never
  dropped silently.

### Read-only posture
`ALLOWED_METHODS` unchanged (no create/write/unlink). `value_service` reuses the shared
60s-TTL units cache (now carrying `amount` + `total_area`) and adds a 60s-TTL
`rs.contract` read for the scope's sold units. No write surface anywhere.
