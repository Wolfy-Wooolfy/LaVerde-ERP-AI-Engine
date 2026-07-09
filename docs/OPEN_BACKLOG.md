# OPEN BACKLOG — LaVerde-ERP-AI-Engine (living list, updated 2026-07-09)

This file is the single source of truth for OPEN work items. A new chat
session has no memory of past conversations — start here. Verify each item
against live disk/repo state before acting (memory and this file can drift).

## Git state at last update
- origin/main == 79e9b15. Recent tags: sidebar-reorg-complete,
  dq-hub-complete, module4-phase2-complete, module4-phase1-complete,
  hardening-1-session-secret.
- Test gate baseline: 1547 passed / 4 pre-existing environmental skips
  (3 in tests/integration/test_rbac.py, 1 in tests/integration/
  test_settings_api.py, firing when Odoo is unreachable) / 29 deselected.

---

## 1. Pre-launch deployment hardening (3 of 4 items remain)
Item 1 (SESSION_SECRET fail-loud + laverde_session cookie rename) is DONE
(tag hardening-1-session-secret). Remaining, all deferred because launch
itself is deferred until Finance finishes historical data entry:
- Change the default admin password.
- Disable /docs + /openapi.json + /redoc in production.
- Lock CORS_ORIGINS.
A strong SESSION_SECRET is already confirmed in .env (do NOT rotate it).
These are deployment-config only; each is a small config change. Do a
read-only discovery first (locations were mapped in the SESSION_SECRET
discovery: main.py app setup, config.py, CORS middleware).

## 2. Contract-Date-based sales feature (PARKED — needs product definition)
Already documented separately in docs/BACKLOG_CONTRACT_DATE_FEATURE.md.
Blocked on Khaled defining the OUTPUT (report? KPI? date-range filter?
sales-over-time chart?). Source date = rs.payment.term.contract_date
(the true sale date). READ-ONLY; discovery-first when built.

## 3. Merge the two inventory pages into one hub (OPTIONAL, not urgent)
/projects-inventory/data-quality (inventory data hygiene, admin-only) and
/projects-inventory/pricing-outliers (board realized-price analytics) are
DISTINCT concerns but share duplicated quantile/vintage helpers
(_quantile/_median/_vintage_bucket/_c2 duplicated in both service files).
Option B from the org-audit: merge into one /projects-inventory/quality
tabbed hub (mirror the CRM DQ hub) and de-duplicate the math. ~4-6 commits;
touches the board-facing pricing page and mixes RBAC gates (admin-only
completeness + all-user pricing → needs per-tab gating). Deferred; do only
if Khaled wants inventory consolidation. NOTE: Pricing Outliers is board
intelligence, NOT data hygiene — if merged, keep that distinction clear.

## 4. Flaky test: test_cached_summary_is_instant (SMALL, low-risk)
A performance test asserting a cached response is under ~10ms. It has
false-failed 3 times under back-to-back suite runs / memory pressure, then
passed cleanly on rerun. Not a regression — a timing flake. Fix: raise the
threshold or mark it non-flaky / de-time it. Small, isolated, zero product
risk. Do a quick read of the test first to pick the cleanest fix.

## 5. Collections AI Chat (DEFERRED — explicitly last)
The one genuinely missing Collections capability: an AI chat over the
Collections module. Reuse the CRM chat architecture at
backend/modules/crm/ai/chat/ (intent_parser, data_fetcher, prompts,
response_builder, session_manager, schemas). Model gpt-4o-mini, hard cap
$10/month. Estimate cost before any verification run. Deferred until other
priorities clear.

## 6. Collections unit-test coverage gap (pre-board, if board launch nears)
Collections KPIs 3/4/5/5b/6/7 and all drill-downs lack unit tests (only
KPI 2 has ~14). Live verify_* scripts exist separately, but unit coverage
is thin. Not blocking day-to-day; revisit before any board launch.

## 7. Minor deferred UI item
Redirect / (root) to the login page — currently returns 404. Frontend-only,
~2 min. Bundle with any future frontend touch.

## 8. stage_resolver uptime-<1h latent bug (MEDIUM — real but narrow)
backend/modules/crm/stage_resolver.py computes cache staleness as
time.monotonic() - _loaded_at > 3600, and a never-loaded resolver is
initialized to _loaded_at = 0.0. On Windows time.monotonic() counts from
system boot, so during the FIRST HOUR after a machine reboot a freshly
created resolver evaluates as "not stale" and serves its EMPTY cache
without ever fetching stage names from Odoo. Production impact: if the
FastAPI app is (re)started within 1h of machine boot, CRM stage names
render as "Stage 28"-style numeric fallbacks instead of real names until
uptime passes 1h. Same root cause as the known test flake: the
test_stage_resolver.py tests fail when the full suite runs within 1h of
boot and pass otherwise (diagnosed by timestamp correlation; see the
79e9b15 commit message). One-line fix when scheduled: initialize
_loaded_at: float | None = None and treat None as an explicit
never-loaded/needs-load state, instead of relying on the 0.0 sentinel
against a boot-relative clock. Medium priority — real but narrow; only
affects app starts within 1h of a reboot.

## 9. Manual refresh on SSR pages — dashboard-refresh Phase 3 (MEDIUM-HIGH)
Context: request-scoped cache-bypass is live (manual ?refresh=1 GET skips
the in-memory cache — 9ec37cd) and automatic refresh is slowed to 1h
(79e9b15). The 4 client-fetch dashboards (CRM, Collections, Customer
Accounts, Balance Sheet) already have working manual refresh buttons. The
gap: the global topbar Refresh button in base.html is hard-wired to
crmRefresh() (CRM data only). On the 10 SSR pages (HR; Projects-Inventory
Dashboard, Value & Area, Pricing Outliers, Data Quality; Marketing
Attribution Dashboard + Timeline; Campaign Performance Dashboard +
Timeline; CRM Data Quality hub) that button fetches CRM data, updates
nothing on-screen, AND still shows a false "Data refreshed" toast — a
real UX bug, not just a missing feature. Agreed Phase 3 approach: on SSR
pages the manual refresh triggers a full page reload carrying ?refresh=1
(the cache-bypass middleware already honors it, so the reload renders
fresh Odoo data) instead of calling the CRM-only crmRefresh(). Exclude
pages with no live Odoo data (Settings, Login, 403, no_modules).
Medium-high priority — includes fixing the false-success toast /
wrong-data bug on 10 pages.

---

## Notes for a fresh session
- READ-ONLY on Odoo is absolute; ALLOWED_METHODS never gains a write method.
- Always confirm working dir + local HEAD == origin/main at session start.
- Verify item status against live repo state before building — this file may
  lag reality.
- Arabic terminology: "موظف مبيعات"/"موظفي مبيعات", never "مندوب".
