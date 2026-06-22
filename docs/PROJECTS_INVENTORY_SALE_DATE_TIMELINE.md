# Projects / Inventory — Sale-Date & Vintage Reference (CANONICAL)

**Status:** Reference doc. Distilled from the Slice 2.5 Pricing Outliers work — the one
data-model gotcha that must not be re-derived wrong.
**Odoo:** laverde.odoo.com (Odoo 17), JSON-RPC, READ-ONLY &nbsp;|&nbsp; **AI cost:** $0.00
**Companion to:** `docs/PROJECTS_INVENTORY_PRICING_DISCOVERY.md` (LIST vs REALIZED price).

---

## 1. The true sale date

The real sale/contract date for a sold unit is **`rs.payment.term.contract_date`**, reached by:

```
unit
  → rs.contract (non-cancel).payment_term_id
    → rs.payment.term (state = 'confirm')
      → contract_date
```

- **100% coverage** on the sold-with-contract population.
- **Spans 2018–2025** — a real, multi-year timeline.

This is the *only* field that reflects when a deal actually happened.

## 2. Fields that are migration artifacts — NEVER use for chronology

- **`rs.contract.reservation_date`** — a bulk migration stamp (e.g. 846 units all
  "reserved" `2025-07-15`). Not the real reservation date.
- **`rs.contract.create_date`** — spikes on the data-load date (e.g. `2026-02-11`). It is
  when the record was *loaded into Odoo*, not when the deal happened.

Using either for "when was this sold / absorbed / priced" collapses a multi-year history
into a false single cohort. It is wrong.

## 3. Why vintage control matters

Real prices escalated **~6×** across 2018→2025:

| Sale year | price/m² (EGP) |
|---|---|
| 2018 | ~7,800 |
| 2024 | ~33,500 &nbsp; *(step reflects the EGP devaluation)* |
| 2025 | ~47,000 |

Comparing price/m² across units of different sale years is therefore misleading. Bucketing
peers by sale-year **collapsed within-peer-group price spread (IQR/median) from ~47% to ~9%**
— most of the apparent spread was *vintage*, not deal-to-deal noise.

This is why the Pricing Outliers peer key is **`(zone, unit_type, 2-year vintage bucket)`**,
with vintage taken from `contract_date`.

## 4. Implications for future features

- Any **absorption-over-time, price-trend, or cohort** analysis MUST use
  `rs.payment.term.contract_date` for chronology.
- Only genuinely post-migration, natively-entered sales would carry a real
  `reservation_date` / `create_date`. Until that is confirmed, treat **both fields as
  load-time metadata only.**
