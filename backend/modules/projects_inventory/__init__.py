"""
Projects Inventory module — read-only real-estate INVENTORY & AVAILABILITY layer.

Slice 1 (this module): board-level unit counts by sales STATUS — overall and per
project — over rs.structure.unit. Counts ONLY (no pricing, no area, no value;
those are a later slice). This is the supply / inventory side, complementary to
the shipped Collections (receivables) and Customer Accounts (balances) money side.

Status buckets (LOCKED — discovery docs/PROJECTS_INVENTORY_DISCOVERY.md §2):
  available    = state in {available}
  reserved     = state in {reserved, initial}
  contracted   = state in {contracted, delivered}   (the "sold" bucket)
sold% = contracted ÷ total units (overall and per project).

Read-only invariant: ALLOWED_METHODS in backend/shared/odoo/client.py must never
contain create, write, unlink, or any state-modifying RPC. Every query function in
this module enforces this with a defense-in-depth assertion at entry time (see
services/inventory_service.py::_assert_read_only).

Design / discovery document:
  docs/PROJECTS_INVENTORY_DISCOVERY.md
"""
