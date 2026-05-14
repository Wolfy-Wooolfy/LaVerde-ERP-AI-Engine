# Module 2 — Naming Decision: "Collections" not "Accounting"

> **Decision date:** 2026-05-14  
> **Decision owner:** Khaled (Sales Manager, La Verde Real Estate)  
> **Status:** Approved — folder rename pending in a separate commit

---

## Background

During Phase 6 (multi-module architecture), a placeholder folder was created as `backend/modules/accounting/` based on the initial assumption that Module 2 would cover general accounting functionality.

After a detailed business context walkthrough with Khaled, the actual scope is significantly more specific:

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
| Placeholder folder | `backend/modules/accounting/` | `backend/modules/collections/` |
| Display name (AR) | — | التحصيلات |

---

## Folder Rename

The rename from `backend/modules/accounting/` to `backend/modules/collections/` will be performed in a **separate atomic commit** after this documentation is approved and committed. Both placeholder folders currently exist; the `accounting/` placeholder will be removed and `collections/` will become the canonical location.

This document does not perform the rename. The rename commit will reference this document as the source of the decision.

---

## Reference

For full business context, see [`docs/MODULE_2_BUSINESS_CONTEXT.md`](MODULE_2_BUSINESS_CONTEXT.md).
