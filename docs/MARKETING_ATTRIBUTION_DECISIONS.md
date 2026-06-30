# Marketing Attribution — Implementation Decisions (append-only)

Read-only intelligence layer over Odoo 17. This module NEVER writes to Odoo.
Each entry records a decision made while building the backend + the single
RBAC-gated JSON endpoint. Append only — do not rewrite history.

---

## 2026-06-14 — Initial build (backend + one endpoint)

### D1 — File layout (mirrors collections / hr)
```
backend/modules/marketing_attribution/
  __init__.py                         read-only invariant docstring
  domain.py                           config gates + stage->group mapping (pure)
  schemas.py                          Pydantic v2 response models
  services/__init__.py
  services/cache.py                   per-module dict cache (60s, Cairo-date keys)
  services/attribution_service.py     all Odoo reads + map + attribution + outcomes
backend/api/v1/endpoints/marketing_attribution.py   single GET endpoint
tests/unit/modules/marketing_attribution/           service + route tests
scripts/verify_marketing_attribution_live.py        identity-equal live verification
```
Unit tests live under `tests/unit/modules/<mod>/` — the location the bare gate
actually collects (`testpaths = ["tests"]` in pyproject.toml). HR/collections
also keep their gate-run tests there; an in-module `tests/` dir would not be
collected by the gate, so none was created.

### D2 — Gate keying = campaign NAME, resolved to id SET at runtime (§6b, A3)
`CONFIRMED_BUYER_CAMPAIGNS` and `DENYLIST_CAMPAIGNS` are frozensets of
utm.campaign **names** (human-stable, shown in the Odoo UI and every discovery
doc; ids would shift on a re-import). At runtime each name is resolved against
the live `utm.campaign` table to the **set** of matching ids (names are not
guaranteed unique — A3):
  - name → 0 records  → appended to `config_warnings`, ignored.
  - name → >1 records → all ids included (union), appended to `config_warnings`.

### D3 — Attribution basis field = `media_buyer_id` (§3.2)
Per the discovery (DISCOVERY_DATA §4a: 34.7% coverage, 6 clean values) and the
mission field facts, the both-set map uses `media_buyer_id`, NOT
`direct_media_buyer_id` (26.2%) and NOT the `campaign_name` convention (9.6%).

### D4 — Concentration gate uses integer math (§3.4)
`qualifies(C)` = `dominant_count * 100 >= both_set_count * 90`. Integer
comparison avoids float-boundary ambiguity at exactly 90% (9/10 qualifies
exactly). The float `concentration` (0–100) is still reported for display.

### D5 — Attribution gate computed BEFORE the attribution RPC (amendment A1)
`attributing_ids = { C in confirmed_ids : qualifies(C) AND C not in denylist_ids }`
is computed from the both-set read_group (RPC 2) **before** the per-stage
attribution read_group (RPC 5), and is RPC 5's campaign filter. A confirmed
campaign that fails `qualifies()` (concentration < 90%), has zero both-set
leads, or resolves into the denylist is **not attributed** and produces a loud
entry in `integrity_alerts` (logged at ERROR) — surfacing locked-decision drift
rather than silently adjusting.

### D6 — Attribution is to the DERIVED dominant buyer (§3.3)
For every attributing campaign, ALL its leads (incl. archived, incl. leads with
`media_buyer_id` empty — the inferred share) are attributed to that campaign's
**derived** dominant buyer. `domain.DOCUMENTED_DOMINANT_BUYER` (FB-AY→Ahmed
Aymen, etc.) is REFERENCE ONLY — it is never used by the attribution logic; it
exists so the live-verification script can assert derived == documented (A5).

### D7 — Population includes archived leads (amendment A2)
Every RPC uses `context={'active_test': False}`. Board outcome analysis must
include Lost/closed leads (§3.6); the discovery figures (~34.7% recorded,
~52.6% attributable) were measured on this same archived-included 146,814
population (DISCOVERY_DATA §4a/§5b), so the module's `attribution_pct`
reconciles against them directly. The module attributes CONFIRMED campaigns
only, so `attribution_pct <= ~52.6%` is expected; the shortfall is accounted for
by `pending_campaigns` + non-confirmed qualifying channels.

### D8 — Stage→group: اشترى is dynamic via is_won; جديد/مهتم by exact name (§3.7)
`classify_stage` reads `crm.stage.is_won` for the اشترى group (never hardcoded
stage names), maps `{New, New X}`→جديد and `{Follow up, Interested}`→مهتم by
exact name, sends a null stage (`stage_id` False) to جديد, and everything else
to بلا نتيجة. The mapping is total, so per-buyer group counts always reconcile
to the total. The reconciliation is enforced with an explicit `raise
RuntimeError` (not `assert`) so it survives `python -O` (amendment A7).

### D9 — Outcome group % is each group's share of that buyer's total
`pct = round(100 * count / total_attributed, 2)`, or 0.0 when the buyer has no
attributed leads. Groups are emitted in fixed order: جديد, مهتم, اشترى, بلا نتيجة.

### D10 — RBAC: new module key `marketing_attribution`
Registered in `_VALID_MODULES` (settings.py) so the admin user-management API
accepts it. The endpoint router is gated with
`require_module_api("marketing_attribution")` — the exact dependency the HR
drill-downs use. Returns 401 unauthenticated, 403 without the module grant.

### D11 — Endpoint
`GET /api/v1/marketing-attribution/overview`, Pydantic v2 response
(`MarketingAttributionOverview`), `Cache-Control: private, max-age=60`,
`X-Cache-Status` header. 503 on `OdooQueryError`, 500 on unexpected error
(mirrors the HR endpoint contract).

### D12 — Cache & testability
Per-module dict cache (60s TTL, Cairo-date-scoped keys), mirroring
collections/hr. The service accepts optional `confirmed_campaigns` /
`denylist_campaigns` overrides for deterministic unit tests; when either is
provided the cache is bypassed so a test config can never poison the production
cache key. Default (both None) uses the domain constants and caches.

### Environment note (not a product decision)
The test environment was missing several **declared** dependencies
(`itsdangerous`, `bcrypt`, `respx`, `pytest-benchmark` — all listed in
requirements.txt / requirements-dev.txt). They were installed so the suite
could run; no dependency versions were changed.

---

## 2026-06-14 — Denylist strings corrected to exact live spelling

The first live verification run surfaced a whitespace mismatch: the originally
locked denylist strings `"BV-Daima"` / `"Website-Daima"` did not resolve to any
`utm.campaign` record (they were reported in `config_warnings`), and the two
intended channel campaigns instead appeared in `pending_campaigns`. The actual
live `utm.campaign` names carry spaces around the hyphen:
`"BV - Daima"` (id 1802) and `"Website - Daima"` (id 1803). Both resolve to
dominant buyer **"Mahmoud Mohsen" at 100%** — a channel owner, confirming the
§3.4 intent. `DENYLIST_CAMPAIGNS` was corrected to the exact live names. No other
config changed; `CONFIRMED_BUYER_CAMPAIGNS` untouched.

---

## 2026-06-30 — Window-following totals line on both attribution dashboards

### D13 — Windowed totals line added beneath the pinned all-time block (frontend-only)

Both attribution dashboards (buyer + campaign) pin an ALL-TIME baseline block at the
bottom (`grand_coverage` / `grand_totals`) that, by design (the f8f27bf footer), ignores
the window switcher. Users reading the bottom of the page expected those totals to follow
the selected period. Fix: a new `{% if win.is_windowed %}`-gated line is rendered directly
ABOVE each pinned block, showing the SELECTED period's figures, so the two read as a pair
(this period vs. all-time). The pinned all-time block is retained, unconditional, and
byte-identical.

**Rationale.** The figures already exist in the windowed top payload each route passes to
its template — no backend touch. The two pages are asymmetric and each renders its OWN
natural windowed total: the buyer page renders `attr.coverage_pct` (with the
`total_attributed` / `total_leads_population` split, reusing the existing
`mktattr_window_coverage_*` keys already used by the §2 coverage strip); the campaign page
renders `campperf.total_leads_population` (a lead COUNT — the campaign page has no coverage
metric, so none was invented). Gated on `win.is_windowed` because on the all-time view the
windowed fields are not present in the payload.

**Scope.** Frontend-only: two templates (`marketing_attribution/dashboard.html`,
`campaign_performance/dashboard.html`) plus ONE new i18n key
`campperf_window_total_leads_label` (en + ar). No backend service, route signature, schema,
or cache-key change; the pinned `grand_coverage` / `grand_totals` services and blocks are
untouched.

---

## 2026-06-30 — Windowed 4-group outcome breakdown beneath the period total

### D14 — Windowed new/interested/bought/no-result breakdown (route-side re-aggregation)

The window-following bottom line (D13) showed only a total. Users want, for the selected
period, that total PLUS a 4-group outcome split (new جديد / interested مهتم / bought اشترى /
no-result بلا نتيجة), each group showing a percentage AND the raw count it represents, with
the four counts adding up to the big total.

**Rationale.** No aggregate 4-group total exists in the windowed payload — only per-entity
`outcomes`. It is computed by **route-side re-aggregation** of data already fetched: a small
helper `_aggregate_outcome_groups()` in `dashboard.py` sums each group's COUNT across the
per-entity outcomes and recomputes the percentage over the windowed total. No new service,
no Odoo, no schema, no cache. The big total = `total_leads_population` on BOTH pages and the
breakdown is over POPULATION so the counts reconcile to it: the buyer page sums
`attr.buyers[].outcomes` PLUS `attr.unattributed.outcomes`; the campaign page sums
`campperf.campaigns[].outcomes` PLUS the `data_quality.junk_none` / `data_quality.no_campaign`
buckets (None-guarded). The buyer big number was switched from `coverage_pct` to
`total_leads_population` so the breakdown reconciles (coverage_pct retained as a secondary
stat). The helper logs a warning (does not raise) if the summed counts don't reconcile, so a
future per-entity data-shape drift surfaces without breaking the page. Reuses the existing
`mktattr_group_*` i18n keys (no new strings) and the per-card legend idiom.

**Scope.** `dashboard.py` (route-side aggregation only) + the two templates
(`marketing_attribution/dashboard.html`, `campaign_performance/dashboard.html`). No service /
Odoo / schema / cache change; no new i18n; the pinned `grand_coverage` / `grand_totals` blocks
are untouched. Gated on `win.is_windowed` — `window_groups` is computed and passed only in the
windowed branch.
