"""
Campaign Performance module — read-only, campaign-CENTRIC performance view.

Level 1 (this slice): per-campaign funnel — every campaign with leads, showing
its 4 stage-group breakdown (جديد / مهتم / اشترى / بلا نتيجة) as BOTH count and %,
sorted by lead volume desc, with the dominant media buyer shown per campaign
(see services/campaign_service.py for the display rule), the junk campaign
literally named "None" surfaced as a DATA-QUALITY flag (not a list row), and a
long-tail aggregate below a volume threshold.

Sibling to the shipped buyer-CENTRIC backend/modules/marketing_attribution. The
stage->group classification and the CONFIRMED / DENYLIST campaign config are
IMPORTED from that module (never re-declared), so every per-campaign stage-group
number is defined IDENTICALLY to the shipped module and reconciles 1:1.

Read-only invariant: ALLOWED_METHODS in backend/shared/odoo/client.py must never
contain create, write, unlink, or any state-modifying RPC. Every query function
in this module enforces this with a defense-in-depth assertion at entry time
(see services/campaign_service.py::_assert_read_only). This module NEVER writes
to Odoo — now or ever.

Design / discovery documents:
  docs/CAMPAIGN_PERFORMANCE_DISCOVERY.md
  scripts/discover_campaign_performance.py
"""
