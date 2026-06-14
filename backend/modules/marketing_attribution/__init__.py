"""
Marketing Attribution module — read-only intelligence layer attributing each
CRM lead to the Media Buyer who generated it, CAMPAIGN-DRIVEN.

Attributes leads to media buyers via the structured UTM campaign (utm.campaign),
then reports, per media buyer, total attributed leads plus a 4-group outcome
breakdown (counts + %). This module NEVER writes to Odoo — now or ever.

Read-only invariant: ALLOWED_METHODS in backend/shared/odoo/client.py must
never contain create, write, unlink, or any state-modifying RPC. Every query
function in this module enforces this with a defense-in-depth assertion at
entry time (see services/attribution_service.py::_assert_read_only).

Design / discovery documents:
  docs/MARKETING_ATTRIBUTION_DISCOVERY.md
  docs/MARKETING_ATTRIBUTION_DISCOVERY_DATA.md
  docs/MARKETING_ATTRIBUTION_DECISIONS.md
"""
