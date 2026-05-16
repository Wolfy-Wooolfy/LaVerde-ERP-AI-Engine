"""
Collections module — read-only intelligence layer for the Collections domain.

Surfaces KPI data sourced from Odoo's rs.installment model and related models
via the shared read-only OdooClient. This module never writes to Odoo.

Read-only invariant: ALLOWED_METHODS in backend/shared/odoo/client.py must
never contain create, write, unlink, or any state-modifying RPC. Every query
function in this module enforces this with a defense-in-depth assertion at
entry time.

Design documents:
  docs/MODULE_2_BUSINESS_CONTEXT.md
  docs/MODULE_2_DISCOVERY_PHASE_1.md
  docs/MODULE_2_MVP_DESIGN.md
  docs/MODULE_2_DISCOVERY_PHASE_2.md
  docs/MODULE_2_IMPLEMENTATION_DECISIONS.md
"""
