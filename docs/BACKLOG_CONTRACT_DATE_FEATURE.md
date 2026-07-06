# BACKLOG — Contract-Date-Based Sales Feature (documented 2026-07-06, NOT yet built)

## Origin
Khaled flagged (with two Odoo screenshots) that a future feature is needed,
built around the **Contract Date** of each sale.

## Confirmed facts (from Khaled + screenshots)
- Each sale/contract carries a **Contract Date** (e.g. 19/01/2026 in the
  sample Payment Term PT03750).
- This date's authoritative source is the **payment term of the contract
  itself** — model `rs.payment.term`, field `contract_date`.
- This aligns with an already-documented project decision: the TRUE sale
  date = `rs.payment.term.contract_date`, NOT `reservation_date` and NOT
  `create_date` (both are migration artifacts and are misleading).
- Screenshot 1 (Projects Mgmt → Payment Term → pivot) shows sales data can
  be grouped by project and by year — Count / Price Before Disc. /
  Discount Amount / Sale Price across 2018–2026, split by
  New Capital / Cassette / La Puerta.

## What is NOT yet decided (blocks build)
- The actual OUTPUT of the feature: report? KPI card? date-range filter?
  a sales-over-time chart? a per-year/per-project breakdown page?
- Which figures matter (Count vs Sale Price vs Price-Before-Discount vs
  Discount), and at what grouping (year / project / phase / month).
- Where it lives in the app (which module, which page).

## When built (reminders)
- READ-ONLY only. Source contract_date from `rs.payment.term.contract_date`.
- Do a read-only discovery FIRST (confirm field population %, date range,
  how it maps to project/phase, timezone handling per the Africa/Cairo rule).
- Verify identity-equal against the Odoo Payment Term pivot screen before
  shipping.

## Status: PARKED — awaiting Khaled's product definition of the output.