# Module 2 — Naming Decision: "Collections" not "Accounting"

> **Decision date:** 2026-05-14  
> **Decision owner:** Khaled (Sales Manager, La Verde Real Estate)  
> **Status:** Approved — no folder rename needed; `collections/` already exists from Phase 6

---

## Background

When documenting the naming decision (commit 4a8b89a), it was assumed that Phase 6 had created a single placeholder folder named `backend/modules/accounting/` for Module 2. Inspection during the rename planning session revealed that Phase 6 had actually created two separate placeholder folders with distinct scopes:

- `backend/modules/collections/` — AR/receivables intelligence (already correctly named for Module 2)
- `backend/modules/accounting/` — general financial intelligence (different future module, not Module 2)

After a detailed business context walkthrough with Khaled, the confirmed scope of Module 2 is:

**This module is a receivables intelligence and collections management layer — not a general accounting module.**

---

## Why "Collections"

1. **La Verde already has Standard Odoo Accounting** — it is active, in use, and handles the general ledger, journals, bank reconciliation, VAT, and financial reports. This module does not replace it.

2. **La Verde already has RS Accounting** — a custom Odoo module handling operational receivables: checks management, payments, penalties, and discounts. This module does not replace it either.

3. **La Verde already has Collections Mgmt** — a custom Odoo app used daily by the Collections Officer (موظف تحصيلات) to view and filter installments by status. This is the primary data source for Module 2.

4. **Naming alignment with existing UX:** Calling this module "Collections" means the daily user — the Collections Officer — immediately recognizes it as the AI layer on top of the tool they already use. There is no learning curve on the name.

5. **Scope clarity:** "Accounting" implies general ledger, P&L, VAT, and balance sheets. "Collections" is unambiguous: it means receivables tracking, installment monitoring, and overdue management.

---

## The Decision

| | Old assumption | Confirmed reality |
|-|----------------|-------------------|
| Module name | Accounting | Collections |
| Slug | `module_2_accounting` | `module_2_collections` |
| Placeholder folder | (assumed `accounting/`) | `backend/modules/collections/` (already exists from Phase 6) |
| Display name (AR) | — | التحصيلات |

---

## Folder State (No Rename Required)

Inspection of `backend/modules/` during the rename planning session revealed that Phase 6 had already created `backend/modules/collections/` as a distinct placeholder for AR/receivables intelligence. The folder is already correctly named for Module 2.

The separate `backend/modules/accounting/` folder exists for a different future scope: general financial intelligence (budgets, cash flow, reconciliation). It is retained as a future module placeholder, not part of Module 2.

**Conclusion:** No folder rename was performed. This document confirms the naming, not a rename operation.

---

## Reference

For full business context, see [`docs/MODULE_2_BUSINESS_CONTEXT.md`](MODULE_2_BUSINESS_CONTEXT.md).
