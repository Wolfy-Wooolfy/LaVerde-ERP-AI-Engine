# Collections Module

**Detailed business context:** see [`docs/MODULE_2_BUSINESS_CONTEXT.md`](../../../docs/MODULE_2_BUSINESS_CONTEXT.md)

**Status:** Pre-Discovery. Models and fields below are approximate and will be verified against live Odoo in the Discovery phase.

---

## Planned Scope

This module will deliver a read-only AI intelligence layer over Odoo's accounts receivable and collections data. It will surface overdue invoices, identify high-risk debtors, track payment promise follow-ups, and highlight customers with deteriorating payment behavior — enabling the collections team to prioritize their outreach effectively.

All data access is strictly read-only. No invoices, payments, or records are ever created or modified through this engine.

## Intended Odoo Data Sources

- `account.move` — invoices, credit notes, payment status
- `account.move.line` — individual line items and due dates
- `res.partner` — customer payment terms and credit limits
- `account.payment` — payment history
- `mail.message` — collections communication history per partner

## Sample AI Queries

1. "Which customers owe more than 30 days past due?"
2. "Show me the top 10 debtors by total outstanding balance"
3. "Which accounts have had no payment activity in 60 days?"
4. "What is our total overdue receivables amount by aging bucket?"
5. "Which customers have a history of late payments but are currently current?"

---

## La Verde-Specific Notes

La Verde uses three Odoo apps that this module reads from:
- Accounting (Standard Odoo)
- RS Accounting (Custom — checks, payments, penalties, discounts)
- Collections Mgmt (Custom — installment list views by status)

The full workflow, installment types, and column definitions are documented in the business context file above.
