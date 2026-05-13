# Contracts Module

**Status:** 🚧 Coming Soon

## Planned Scope

This module will provide a read-only AI intelligence layer over Odoo's sales and purchase contracts. It will flag contracts approaching expiry, surface unsigned agreements, highlight recurring renewal opportunities, and identify contracts with unusual value deviations — allowing commercial teams to act proactively.

All data access is strictly read-only. No contracts, amendments, or signatures are ever created or modified through this engine.

## Intended Odoo Data Sources

- `sale.order` — sales contracts and orders
- `purchase.order` — purchase agreements
- `account.analytic.account` — project-linked contract budgets
- `mail.message` — communication history per contract
- `res.partner` — customer and vendor details

## Sample AI Queries

1. "Which contracts are expiring in the next 30 days?"
2. "Show me unsigned contracts with a value over 100,000 EGP"
3. "Which vendors have contracts up for renewal this quarter?"
4. "What is the total contract value pending approval?"
5. "Which customer accounts have had no contract activity in 6 months?"
