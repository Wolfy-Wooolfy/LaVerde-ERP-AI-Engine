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

**UPDATE:** the deferred `prompts.py` sub-item above is now CLOSED — see item 11.
The other three "deliberately NOT removed" entries (data_fetcher.py,
marketing_attribution/domain.py, tests/mock_odoo/fixtures.py) stand unchanged.

---

## 11. Dead stage names in the intent-parser prompt — RESOLVED (New X + Contact)

Closes the one remaining New X item deferred from item 10, and fixes a second
dead stage name of the same class found during that work.

Live read-only discovery 2026-08-04 measured 17 crm.stage rows. Neither
"New X" nor "Contact" is among them. The 17: New, Lost, No Answer,
Wrong Number, Follow up, Interested, Contact in the Future, Re-Distribution,
Unqualified, Unavailable Request, Cancel Reservation, Bought Out,
Cancel Contract, Draft Reservation, Initial Reservation, Reservation,
Down Payment Confirm & Contracted. crm.stage has no `active` field, so an
absent stage is deleted, not archived.

### (a) New X — CLOSED
`backend/modules/crm/ai/chat/prompts.py` no longer presents "New X" to the LLM.
Four references removed: the vocabulary list (line ~235), the dedicated
few-shot example (lines ~294-295), and the STAGE NAME MAPPING entry
`- New X → New X  (two-word stage name — include the X)` (line ~313). The
few-shot slot was REPLACED rather than deleted, because it was carrying a
multi-word-stage-name demonstration; see (b).

### (b) Contact → Contact in the Future — the second defect, worse than New X
The STAGE NAME MAPPING block is headed "Real stages in this Odoo instance —
ONLY use these stage names", and it listed `- Contact / اتصال → Contact`.
"Contact" is not a stage. The real stage is "Contact in the Future".

Worse than New X for two reasons:

1. **It teaches truncation.** "Contact" is a PREFIX of a real stage, not an
   unrelated dead string. It trains the model to emit the first word of a
   multi-word stage name — the single failure mode the downstream matcher
   cannot absorb.
2. **The two stage intents disagreed about whether it exists.**
   `count_by_stage` resolves through `CrmService.count_leads_by_stage`, which
   is EXACT case-insensitive match (`crm/service.py`, `s["name"].strip().lower()
   == target`) → "Contact" finds nothing → `stage_not_found`. But
   `list_overdue_by_stage` filters by SUBSTRING (`data_fetcher.py`,
   `stage_filter in r.stage_name.lower()`) → "contact" DOES match
   "Contact in the Future" → a real list comes back. Same user word, one
   intent says the stage does not exist and the other answers it. New X at
   least failed consistently.

Fixed by pointing the mapping at the real stage and by re-pointing the freed
few-shot slot at it: the example now demonstrates a FOUR-token stage name
(`"كم lead في مرحلة Contact in the Future؟"` → `{"stage":"Contact in the
Future"}`), upgrading the multi-word lesson the New X example used to carry
from 2 tokens to 4, on the stage most exposed to first-word truncation.

The bare Arabic alias `اتصال` was DROPPED, not re-pointed. Line ~237 of the
same prompt already assigns `اتصال` to phone-attempt-in-chatter detection, so
the file was giving one Arabic word two contradictory jobs. Replacement alias
is `التواصل في المستقبل`. A bare `تواصل` was rejected too: FALLBACK_FOLLOWUPS
and SUGGESTED_QUESTIONS contain "اقترح عليّ 3 عملاء أتواصل معاهم النهارده",
and those strings are re-parsed through `parse_intent` by
`response_builder.py`, so a bare `تواصل` stage alias would compete with the
`اقترح`/`النهارده` → recommendation rule on the system's own canned
follow-ups. `متابعة مستقبلية` was rejected because `متابعة` already maps to
Follow up.

### Verification status — READ THIS BEFORE TRUSTING THE GREEN SUITE
No test imports, reads, or asserts anything about `INTENT_PARSING_SYSTEM_PROMPT`.
The four tests that import this module take `ALLOWED_INTENTS`,
`CONVERSATIONAL_INTENTS`, `SUGGESTED_QUESTIONS`, `_TERMINOLOGY_RULES` only;
`test_intent_parser.py` drives `parse_intent` with a mock client and asserts on
canned JSON it supplies itself, so the system prompt reaches a mock and is
discarded unexamined. The suite signature is identical whether this edit is
right or wrong — it proves only the absence of collateral breakage. Correctness
rests on Khaled's live chat run.

Operational note for any future edit to this prompt: it is a module-level
f-string built at import time, and `IntentCache` is keyed on
`sha256(locale:question)` with the prompt NOT in the key. A full server restart
is required — it both reloads the constant and clears the cache (IntentCache is
in-memory only and, unlike AICache, does not persist to logs/ai_cache.json).

### STILL OPEN — coverage gap, deliberately not fixed here
The STAGE NAME MAPPING table names 8 of the 17 live stages. **9 live stages are
absent from it entirely:** Wrong Number, Unavailable Request, Cancel
Reservation, Bought Out, Cancel Contract, Draft Reservation, Initial
Reservation, Down Payment Confirm & Contracted, and — as an Arabic mapping —
any alias beyond the one added above.

This is a DIFFERENT and lower-severity class than items (a) and (b): a missing
entry is a coverage gap, whereas New X and Contact were false claims about
live Odoo. Questions about the 9 still work when the user types the exact
English name (it passes through `_normalise_stage` unchanged to an exact
match); what is missing is Arabic-alias coverage and the model's awareness
that they exist. Expanding the table is a scope increase and needs its own
live-chat verification run, so it is left open rather than folded in blind.

---

## 12. Five of seven CRM KPI cards never refreshed — RESOLVED (aliases)
Pressing Refresh on the CRM dashboard updated only 2 of the 7 KPI cards. The
DOM carries 7 `data-kpi-value` names; GET /api/v1/dashboard/kpis returned 10
keys; the intersection was only `total_leads` and `followups_today`. Since
app.js:146 matches payload keys against `[data-kpi-value="<key>"]`, the cards
Critical Overdue, Overdue Follow-ups, Missing Contact Info, Missing
Salesperson and Data Quality Issues could never be updated by a refresh. The
success toast still fired, because 097b48d gates it on "at least one selector
matched" and two did.

NOT a regression. The two vocabularies diverged in 9286a7b — the single commit
that created both dashboard.html and dashboard_api.py — and never agreed
afterwards. Those five cards had never once refreshed in the product's
history. Neither c5350b2 (New X removal, item 10) nor 097b48d (toast gating)
caused it; 097b48d concealed it by making a 2-of-7 match look like success.
The bug is invisible on load because the server-rendered first paint reads the
correct model fields every time — it shows only if you press Refresh and
compare. The full suite was green throughout: nothing in it compared the two
vocabularies.

### Why aliases (option f) and not a rename
`sparkline_metric` in _kpi_card.html is ONE macro argument feeding FOUR
attributes — data-sparkline-metric (:41), data-kpi-value (:76), data-kpi-trend
(:80), data-sparkline (:88). The short names it carries have FOUR consumers,
not the two originally assumed:
1. `_METRIC_MAP` in dashboard_api.py — the /sparkline dispatch table.
2. charts.js:246 — the raw attribute becomes the `?metric=` query value.
3. charts.js:250 — `[data-kpi-trend="<metric>"]` places the trend badge.
4. charts.js:273 — a hardcoded literal array
   `['critical','overdue','missing_contact','data_quality']` choosing the
   sparkline stroke colour. THE TRAP: no linter, compiler or rename tool
   would flag it. Missing it turns four red sparklines indigo, silently.

Because data-kpi-value (:76) and data-kpi-trend (:80) are interpolated from
the SAME variable but read under OPPOSITE vocabularies, no edit to
dashboard.html alone can satisfy both.

Rejected options, recorded so they are not re-litigated:
- (a) rename the DOM values to the payload keys — needs three lockstep edits,
  one of which is the charts.js:273 trap above.
- (b) rename the 5 payload keys to the short names — external consumers of
  /api/v1/dashboard/kpis could NOT be enumerated by static inspection (only
  app.js:137 in-repo). Removing keys on an unverifiable assumption was not
  acceptable.
- (c)/(e) a mapping layer inside crmRefresh() — crmRefresh is bound to the
  shared base.html topbar button and runs on EVERY page, on a timer, and on
  Ctrl/Cmd+Shift+R. Too broad a blast radius for a CRM-only fix.
- (d) split the macro argument (a new `kpi_key` param) — the only option that
  unwelds the shared argument, but a call site omitting `kpi_key` renders
  data-kpi-value="" and no-ops without complaint: one silent failure mode
  traded for another of the same class.
- (f) CHOSEN. /kpis gained `critical`, `overdue`, `missing_contact`,
  `missing_salesperson`, `data_quality` alongside the existing long keys.
  Renames nothing, removes nothing, so all four short-name consumers are
  untouched BY CONSTRUCTION rather than by care. _METRIC_MAP, charts.js,
  _kpi_card.html, dashboard.html and app.js were not modified at all.

### The guard — tests/unit/core/test_kpi_vocabulary_consistency.py
No option was safe without it; it ships in the same commit. It extracts every
`sparkline_metric` literal from dashboard.html (extracted, never hardcoded, so
an 8th card cannot slip through) and asserts each is present both in the /kpis
payload key set and in `_METRIC_MAP.keys()`; that each alias reports the same
number as its long key; that the kpi_card() call count matches the literal
count; and that the 10 original payload keys still exist. It uses a mini
FastAPI app with a mocked CrmService — no server, no Odoo, no browser, no
playwright. It was run BEFORE the aliases were added and failed, naming the
exact five cards. It would have failed on 9286a7b.

What it CANNOT see — stated here and in the test file itself:
- charts.js:273's literal array. tests/frontend/*.js are run by hand with
  `node` and are never collected by pytest, so no Python test can reach it.
- whether the rendered attribute lands on the right DOM element, or whether
  crmRefresh() animates it. That needs a browser; the e2e suite is skipped
  (playwright deliberately not installed) and only counts cards anyway.

### STILL OPEN — deferred, each its own decision
- **A2 — three payload keys have no card.** `planned_followups`,
  `no_activity_leads` and `missing_stage_count` are computed and sent on every
  refresh but no KPI card exists for them; `_METRIC_MAP` likewise carries
  `planned` and `no_activity` with no card. Adding cards is a product
  decision, not a bug fix.
- **A5 — `data-sparkline-metric` (_kpi_card.html:41) is dead.** Emitted on all
  7 cards, read by nothing (verified by both hyphenated and camelCase
  `dataset` greps). Harmless; removing it is a separate cleanup.
- **Sparklines and trend badges are not refreshed at all.**
  `loadAllSparklines()` is called only from the DOMContentLoaded handler
  (dashboard.html:590); crmRefresh() never calls it. After a refresh the
  numbers update but each sparkline and its trend badge stay at page-load
  values. Deferred deliberately: fixing it costs one extra request per card
  per refresh, which cuts against item 9's Odoo-load reduction.

---

## Notes for a fresh session
- READ-ONLY on Odoo is absolute; ALLOWED_METHODS never gains a write method.
- Always confirm working dir + local HEAD == origin/main at session start.
- Verify item status against live repo state before building — this file may
  lag reality.
- Arabic terminology: "موظف مبيعات"/"موظفي مبيعات", never "مندوب".
