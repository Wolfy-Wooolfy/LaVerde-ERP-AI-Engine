# Stage 3 — Verification Guide

**Stage:** Frontend Restructure (4 Sections + KPI 3 Removal + state refactor)
**Date:** 2026-05-19
**Status:** Pending V7-V16 human visual sign-off
**Checkpoint target:** `checkpoint-D-stage3-frontend-restructure-complete`

## Prerequisites
- Backend running on http://localhost:8000 (per Decision 6.4:
  killed Python processes, purged all __pycache__ directories,
  restarted uvicorn WITHOUT --reload)
- Browser at http://localhost:8000/collections/dashboard
- DevTools open (Console + Network + Elements)
- Both EN and AR locales testable
- Light + Dark mode testable

## Automated checks (run BEFORE human sign-off)

### V1 — Unit test suite passes
Command: `pytest tests/unit/modules/collections/ -v`
Expected: all collections unit tests PASS, no new failures.

### V2 — Endpoint smoke (KPI 1, 2, 5a, 6, 4, 5b, 7)
Command: `python scripts/smoke_endpoints.py` (or curl each of
the 7 endpoints if no script exists yet — document command used)
Expected: all 7 return HTTP 200 with valid JSON.

### V3 — collections.js console.log discipline
Command: grep `console\.log` in `frontend/static/js/collections.js`
Expected: exactly 1 hit — the `[Collections] Fetched 7 KPIs in Xms`
line.

### V4 — KPI 3 removed from JS
Command: grep `kpi3` in `frontend/static/js/collections.js`
Expected: 0 hits.

### V5 — KPI 3 removed from template
Command: grep `col-kpi3-container` in
`frontend/templates/collections/dashboard.html`
Expected: 0 hits.

### V6 — state shape is named object
Command: grep `_lastFetchData` in
`frontend/static/js/collections.js`, inspect the assignment
inside `fetchAllKPIs()`.
Expected: `_lastFetchData = state;` where `state` is a named
object with keys late, portfolio, perProject, trend, rate,
rateByProject, forecast. No array indexing assignments.

## Visual checks (human performs after V1-V6 pass)

### V7 — Section 1 (Portfolio Scale) renders correctly
Expected: KPI 1 hero card shows 6.12B EGP / 42,443 records in
emerald accent. Section header "Portfolio Scale" (EN) /
"إجمالي المحفظة" (AR) above the card.

### V8 — Section 3 forecast cards render with smoke-test values
Reference values (smoke test 2026-05-19):

| Card | Amount | Count | Period end |
|---|---|---|---|
| This Month | 17.9M EGP | 112 installments | 31 May 2026 |
| This Quarter | 50.7M EGP | 334 installments | 30 June 2026 |
| This Half | 50.7M EGP | 334 installments | 30 June 2026 |
| This Year | 333.1M EGP | 1,913 installments | 31 December 2026 |

All 4 cards render with values above (or close — values may
shift slightly if backend cache expired or data entry advanced).
Q2=H1 collapse (this_quarter == this_half end date) is expected
behavior per Decision 11.9.

### V9 — Section 4 layout
Rate card (col-kpi4-container) is rendered outside any grid
container, occupying the full available width of Section 4.
The 3 project cards appear in a separate grid below in their
own row (sm:grid-cols-3).

### V10 — KPI 4 rate empty state
Rate card shows fallback "البيانات تحت الإدخال" (AR) / "Data
being entered" (EN). Expected per Decision 11.16 (data-state,
not regression).

### V11 — Project per-late values sum to KPI 2
New Capital 168.7M + Cassette 154.1M + La puerta 3.6M = 326.4M
EGP, matching Section 2 KPI 2 value.

### V12 — KPI 6 trend chart renders
6-month trend chart visible with an average reference line.
Last 3 months show zero (data entry incomplete) — expected.

### V13 — AR locale
Switch to Arabic. Verify:
- All section headers in Arabic
- All KPI labels in Arabic
- Forecast bucket names: "هذا الشهر", "هذا الربع", "النصف",
  "هذا العام"
- Arabic-Indic digits where applicable
- RTL layout correct

### V14 — Dark mode
Switch to dark mode. Verify:
- All cards visible with readable contrast
- emerald / danger / primary accents visible
- Chart colors adjusted for dark background

### V15 — No console errors
Refresh page. DevTools Console: zero errors. One log line:
`[Collections] Fetched 7 KPIs in Xms`.

### V16 — Network panel
Refresh page. DevTools Network: 7 KPI fetches, all 200. No
4xx, no 5xx, no kpi3 endpoint fetch.

## Screenshot capture instructions

### SS-1 — EN light mode full dashboard
Save as `docs/screenshots/Stage_3/SS-1_en_light.png`.

### SS-2 — AR dark mode full dashboard
Save as `docs/screenshots/Stage_3/SS-2_ar_dark.png`.

### SS-3 — DevTools console
Capture DevTools console panel after a page refresh, showing the
single [Collections] log line and zero errors. Save as
`docs/screenshots/Stage_3/SS-3_console.png`.

### SS-4 — Network panel
Capture DevTools Network panel after a page refresh, showing the
7 KPI endpoint fetches all returning 200. Save as
`docs/screenshots/Stage_3/SS-4_network.png`.

### SS-5 — KPI 3 removed (Elements pane proof)
In DevTools Elements pane: press Ctrl+F (or Cmd+F on Mac), search
for col-kpi3-container. Screenshot must show the search bar with
the query and the indicator showing 'No matches' or '0 of 0'.
Save as `docs/screenshots/Stage_3/SS-5_kpi3_removed.png`.

## Sign-off

When V1-V16 all PASS and SS-1 through SS-5 captured:

1. Tag the commit:
   `git tag -a checkpoint-D-stage3-frontend-restructure-complete -m "..."`
2. Update `docs/MODULE_2_STAGE_TRACKER.md` row 3 status to ✅ Closed
3. Open next session for Stage 2.5
