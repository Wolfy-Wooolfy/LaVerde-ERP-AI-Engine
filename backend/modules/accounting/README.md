# Accounting Module

**Status:** 🚧 Coming Soon

## Planned Scope

This module will provide a read-only AI intelligence layer over Odoo's accounting and financial data. It will surface budget variances, cash flow anomalies, unreconciled entries, and period-over-period trends — giving finance teams an AI-assisted view of the company's financial health without exposing write access to accounting records.

All data access is strictly read-only. No journal entries, invoices, or financial records are ever created or modified through this engine.

## Intended Odoo Data Sources

- `account.move` — journal entries, invoices, bills
- `account.account` — chart of accounts
- `account.budget.line` — budget allocations and actuals
- `account.bank.statement` — bank reconciliation state
- `account.analytic.line` — cost center allocations

## Sample AI Queries

1. "Which cost centers are over budget this month?"
2. "Show me unreconciled bank statement lines older than 7 days"
3. "What is our net cash position compared to last month?"
4. "Which expense accounts have had unusual spikes this quarter?"
5. "What are our top 5 expense categories year-to-date?"
