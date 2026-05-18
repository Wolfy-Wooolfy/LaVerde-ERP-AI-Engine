# Module 2 — Session 8 — D2 Smoke Test Checklist

**Deliverable**: D2 — KPI Cards (4 Rows)
**Status**: Ready for verification

---

## Pre-flight

- [ ] Purge `__pycache__` across all backend packages
- [ ] Restart uvicorn **without** `--reload` flag
- [ ] Open browser at `/collections/dashboard` (both `?lang=en` and `?lang=ar`)
- [ ] Open browser DevTools → Console (no JS errors expected on load)

---

## ST-01 — Page loads without JS errors

**Steps**: Load `/collections/dashboard`
**Expected**: No `TypeError`, `ReferenceError`, or uncaught errors in console
**Pass**: Console shows only `[Collections] Fetched 7 KPIs in Xms`

---

## ST-02 — KPI 2 hero card renders

**Steps**: Wait for first fetch to complete
**Expected**:
- `col-kpi2-value` shows a formatted EGP value (e.g. `245.3M EGP`) — no longer `—`
- `col-kpi2-value` has a non-empty `title` attribute (full precision value)
- `col-kpi2-records` shows a count
- `col-kpi2-as-of` shows a formatted date
- The subtitle paragraph fades in (opacity transitions from 0 to 1)
- Card `aria-label` includes the formatted value

---

## ST-03 — Row 2 trio (KPI 1, 3, 4) renders

**Steps**: After fetch
**Expected**:
- `col-kpi1-value` (emerald): formatted EGP, non-empty title, fades in
- `col-kpi1-subtitle`: record count + "installments"
- `col-kpi3-value` (warning amber): formatted EGP, fades in
- `col-kpi3-subtitle`: "in pipeline" string
- `col-kpi4-mtd` and `col-kpi4-ytd`: formatted percentage or `—` if null, both fade in
- KPI 4 subtitle shows `"Data entry in progress"` when both rates are null, otherwise `"MTD / YTD · YYYY-MM-DD"`

---

## ST-04 — Row 3 project cards render

**Steps**: After fetch
**Expected**:
- All 3 project cards show translated project names (not `—`)
- All 3 `col-proj{idx}-late` values show formatted EGP (danger red), title set
- All 3 `col-proj{idx}-rate` values show percentage or `—`
- Cards have `data-drilldown-target="kpi5-proj-{id}"` (not `kpi5-placeholder-{idx}`)
- `aria-label` on each card includes project name + late amount + rate

---

## ST-05 — Row 4 trend chart renders

**Steps**: After fetch, when KPI 6 has at least 1 non-zero month
**Expected**:
- Chart canvas `col-kpi6-chart` is visible
- Line chart shows 6 months on X-axis (abbreviated month names)
- Emerald line with fill visible for collection amounts
- Dashed grey reference line visible at average_monthly level
- Legend shows both dataset labels
- Hover tooltip appears on the emerald line, NOT on the dashed reference line

---

## ST-06 — Empty state for trend chart

**Steps**: If all 6 months have zero collection amounts (data entry scenario)
**Expected**:
- `col-kpi6-chart` canvas hidden (`display: none`)
- `col-kpi6-chart-empty` element has `hidden` class removed (empty state visible)

---

## ST-07 — Data-entry banner logic

**Steps**: Load page; observe banner `col-data-entry-notice`
**Expected** — banner VISIBLE when any of:
  - KPI 4 MTD rate AND YTD rate are both null
  - KPI 6 has fewer than 5 non-zero months
  - URL contains `?show_banner=1`

**Expected** — banner HIDDEN when:
  - KPI 4 has at least one non-null rate AND KPI 6 has 5+ non-zero months

---

## ST-08 — Auto-refresh fires at 60s

**Steps**: Wait 65 seconds (or mock `setInterval` in DevTools)
**Expected**:
- Console shows a second `[Collections] Fetched 7 KPIs in Xms` line ~60s after the first
- "Last updated" time in the header updates

---

## ST-09 — Visibility API pauses/resumes refresh

**Steps**: Switch to another tab, wait 90s, switch back
**Expected**:
- No fetch fires while tab is hidden (console silent)
- On returning to tab: immediate fetch fires, then 60s timer restarts

---

## ST-10 — Manual refresh button

**Steps**: Click the "Refresh" button in the page header
**Expected**:
- `collectionsRefresh()` fires a fetch immediately
- Auto-refresh timer resets (no double-fire within 5s of manual refresh)
- All values update if data changed

---

## ST-11 — Arabic locale

**Steps**: Load `/collections/dashboard?lang=ar`
**Expected**:
- `col-kpi2-value` shows Arabic-Indic digits (e.g. `٢٤٥٫٣ مليون جنيه`)
- `col-kpi1-value`, `col-kpi3-value` likewise
- Project names show Arabic translations from `project_names` map
- Trend chart X-axis labels show Arabic month names (`label_ar`)
- RTL layout preserved (no visual breakage)

---

## ST-12 — Error banner

**Steps**: Kill the backend server; click Refresh
**Expected**:
- `col-error-banner` appears (removes `hidden` class)
- "Try again" button visible and clickable
- Banner disappears on next successful fetch

---

## ST-13 — Focus/keyboard navigation

**Steps**: Tab through the page
**Expected**:
- Hero card (KPI 2) receives focus with visible ring
- All 3 Row 2 cards focusable with ring
- All 3 project cards focusable with ring
- `tabindex="0"` on all interactive card elements

---

## Deviation from prompt spec

| Item | Prompt spec | Actual |
|---|---|---|
| Banner ID | `data-entry-banner` | `col-data-entry-notice` (existing template ID preserved) |

Rationale: the existing `id="col-data-entry-notice"` was already rendered in the template from D1. Changing it would break any CSS or unknown DOM queries targeting it. Documented here per pre-flight agreement.
