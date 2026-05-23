"""
Customer Accounts module — read-only Board-level view of customer receivables.

Surfaces per-customer aggregates from rs.installment and rs.account.payment.reconcile
via the shared read-only OdooClient. This module never writes to Odoo.

Design documents:
  docs/MODULE_3_PLAN.md
  docs/MODULE_3_DISCOVERY_PHASE_3.md
  docs/MODULE_3_DISCOVERY_M3S1.md
"""
