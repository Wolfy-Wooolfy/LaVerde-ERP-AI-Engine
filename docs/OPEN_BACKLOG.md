# OPEN BACKLOG — LaVerde-ERP-AI-Engine (living list, updated 2026-07-09)

This file is the single source of truth for OPEN work items. A new chat
session has no memory of past conversations — start here. Verify each item
against live disk/repo state before acting (memory and this file can drift).

## Git state at last update
- origin/main == cfc99c6. Recent tags: sidebar-reorg-complete,
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

## 4. Flaky test: test_cached_summary_is_instant — DONE (6867bdb)
Was: a performance test asserting a cached response is under ~10ms; it
false-failed 3 times under back-to-back suite runs / memory pressure (the
timing assertion was tighter than an OS scheduler quantum), then passed
cleanly on rerun. Fixed in 6867bdb by replacing the wall-clock timing
assertion with a deterministic cache-hit call-count check.

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

## 8. stage_resolver uptime-<1h latent bug — RESOLVED (deleted as dead code)
StageResolver was dead code from the moment it was introduced in 7945b81
(Phase 1): nothing in the application ever imported it. Proven by a
1627-test coverage run (the full suite minus the resolver's own test file)
that builds the entire FastAPI app and reported
backend/modules/crm/stage_resolver.py at 0% — missing from line 6, its
very first import, so the module object was never even created. Its only
importer in the whole repository was tests/unit/modules/crm/test_stage_resolver.py.

The claimed production symptom ("CRM stage names render as 'Stage 28'-style
numeric fallbacks") was therefore unreachable. Production CRM stage names
come from Odoo many2one tuples in modules/crm/service.py
(stage_name=stage[1] if stage else "No Stage"), whose only fallback is the
literal "No Stage"; the sole f"Stage {id}" producer anywhere in backend/
was inside the dead module itself.

The underlying defect was nonetheless genuine, not a test artefact:
_loaded_at = 0.0 compared against a boot-relative time.monotonic() made a
freshly built resolver evaluate as "not stale" during the first hour of
machine uptime, so it served its empty cache. That is the real cause of the
intermittent test_stage_resolver.py failures within 1h of boot (see the
79e9b15 commit message) — the tests were reporting a true bug in code no
caller could reach.

Resolved by deleting the module and its test file in this commit; no other
source file needed a single change. Nothing to schedule.

## 9. Manual refresh on SSR pages — dashboard-refresh Phase 3 (MEDIUM-HIGH)
Predecessor phases are both DONE:
- Phase 1 DONE (79e9b15): automatic KPI refresh slowed from 60s to 1h to
  reduce Odoo load.
- Phase 2 DONE (9ec37cd): request-scoped cache-bypass — manual ?refresh=1
  GET skips the in-memory cache so on-demand refresh returns fresh Odoo
  data; write-back preserved; covers the existing manual refresh buttons
  on all 4 client-fetch dashboards (CRM, Collections, Customer Accounts,
  Balance Sheet).
The gap: the global topbar Refresh button in base.html is hard-wired to
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

## 10. "New X Leads" data-quality KPI — RESOLVED (removed as dead code)
The dashboard shipped a Data Quality mini-card counting leads in Odoo CRM
stage id 44 ("New X"). That stage no longer exists, so the KPI returned a
permanent, silent zero while rendering to board members as a clean bill of
health. Removed end to end in this commit.

Why the stage is gone (product fact, confirmed by Khaled): New X was a
TEMPORARY stage created to work around a problem that has since been solved.
Once the problem was fixed the stage was deliberately deleted, and the concept
is NOT being replaced by any other stage. So there is nothing to re-point the
KPI at — the measurement itself is obsolete, not merely mis-configured.

Live read-only evidence, measured 2026-08-04:
- 17 stages exist live. Ids 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35,
  37, 38, 41, 42, 46. Id 44 is absent.
- `crm.stage` has NO `active` field (`fields_get(['active'])` returned `{}`),
  so a stage cannot be archived on this instance. search_read returned the
  same 17 records under the default context and under
  `{'active_test': False}`, and the archived-only set was empty. The absence
  of id 44 is therefore a DELETION, not an archival — it is not coming back
  on its own.
- `new_x_count` measured live = 0. Historical values of the identical query:
  2,923 (2026-05-12) and 2,227 (2026-06-14). The series 2,923 -> 2,227 -> 0
  is the stage being emptied and then dropped.
- The other two stage settings were re-verified live and are correct and
  untouched: all of CRM_CRITICAL_STAGE_IDS (28,34,35,37,41) and all of
  CRM_CLOSED_EXCLUDED_STAGE_IDS (26,30,31,32,38,42,46) exist.

Because the count was 0, removing it from `total_data_quality_issues` changes
the "Data Quality Issues" KPI, its /api/v1/dashboard/kpis value, and its
sparkline series by exactly zero — no visible step, nothing to explain to the
board. The key was also already inert in the client refresh path: app.js only
updates elements matching `[data-kpi-value="<metric>"]`, and the Data Quality
mini-cards carry no such attribute, so `new_x_count` never drove a pixel.

Same failure class as item 8 (StageResolver, 85824aa): code measuring
something that no longer exists. Difference worth remembering — StageResolver
was unreachable, so it was invisible; this one ran successfully on every
dashboard load and published a truthful-looking zero. A KPI that cannot fail
loudly needs its subject re-verified, not just its code reviewed.

Removed: the CRM_DATA_QUALITY_STAGE_IDS setting and its `data_quality_stage_ids`
property, `get_data_quality_stage_ids()` in crm/domain.py (no remaining caller),
the 4th gather leg in `data_quality_summary()`, `DataQuality.new_x_count`, the
`new_x_count` key in the /kpis payload and in the AI chat `data_quality_full`
feed, the dashboard card, and the "New X Leads" label in en.json/ar.json. The
`data_quality_tooltip` string was CORRECTED rather than deleted — it still
describes the surviving three checks.

Deliberately NOT removed, and still open:
- `backend/modules/crm/ai/chat/prompts.py` (lines ~235, ~294-295, ~313) still
  presents "New X" to the LLM as a current pipeline stage, including a
  dedicated few-shot example. This is the same defect class and SHOULD be
  fixed, but editing the intent-parser prompt changes AI behaviour and needs a
  live chat verification run to confirm, so it is deferred to its own commit
  rather than changed blind. THIS IS THE ONE REMAINING NEW X ITEM.
- `data_fetcher.py` STAGE_AR_TO_EN keeps `"new x": "New X"`. Its failure mode
  is honest — `count_leads_by_stage("New X")` finds no crm.stage row and
  returns `stage_not_found`, not a silent zero — and it is load-bearing for
  the exact-match guard that stops "New" matching "New X".
- `marketing_attribution/domain.py` keeps `NEW_STAGE_NAMES = {"New", "New X"}`.
  Different module, name-based grouping, unreachable but harmless.
- `tests/mock_odoo/fixtures.py` keeps stage 44. A test double, not a claim
  about live Odoo.
- Historical docs (PHASE_5_BUG_HUNT.md, MARKETING_ATTRIBUTION_DISCOVERY_DATA.md,
  ISSUES_FOUND.md, PHASE_3/5_REPORT.md, MODULE_2_IMPLEMENTATION_DECISIONS.md
  and the other dated snapshots) are left intact — they are accurate as of
  their own dates.

Khaled must delete one line from his real .env by hand; see the session
report. Not urgent: Settings uses `extra="ignore"`, so the leftover line is
silently ignored and the app does not break.

---

## Notes for a fresh session
- READ-ONLY on Odoo is absolute; ALLOWED_METHODS never gains a write method.
- Always confirm working dir + local HEAD == origin/main at session start.
- Verify item status against live repo state before building — this file may
  lag reality.
- Arabic terminology: "موظف مبيعات"/"موظفي مبيعات", never "مندوب".
