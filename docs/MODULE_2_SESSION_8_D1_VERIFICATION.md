# D1 — Foundation & Routes — Verification Checklist

**Module:** 2 — Collections  
**Session:** 8  
**Deliverable:** D1 (Foundation & Routes)  
**Author:** Claude Sonnet 4.6  
**Date:** 2026-05-18

---

## Pre-flight

These steps must be completed **before** opening the browser. Skipping
them risks stale `.pyc` bytecode causing 404s (per Decision 6.4).

### 1. Kill Python processes

```powershell
Get-Process -Name python -ErrorAction SilentlyContinue | Stop-Process -Force
```

### 2. Purge `__pycache__` directories

```powershell
Get-ChildItem -Path . -Filter __pycache__ -Recurse -Directory |
  Remove-Item -Recurse -Force
```

### 3. Rebuild CSS (optional — only if you modified templates)

```powershell
cd frontend
npm run build:css
cd ..
```

Expected output: `Done in ~5s`. The `caniuse-lite is outdated` line is
a known non-removable advisory (documented in the browserslist commit).

### 4. Start uvicorn **without** `--reload`

```powershell
C:\Python310\python.exe -m uvicorn backend.main:app
```

Wait for: `Application startup complete.` in the console output.

---

## Smoke Tests

### Test 1: Route returns 200

**URL:** `http://localhost:8000/collections/dashboard`

**Steps:**
1. Open the URL in Chrome.
2. Open DevTools → Network tab → filter by "Doc".

**Expected:**
- The page returns HTTP 200.
- Response content-type is `text/html`.
- No redirect loop (not 301 → 301).

**Pass criteria:** Status 200, page renders (even if content is skeleton
placeholders).

---

### Test 2: Sidebar navigation

**Steps:**
1. Open `http://localhost:8000/dashboard` (CRM page).
2. Locate the "Modules" section in the left sidebar.
3. Click the "Collections" link (should have a ✓ badge, not "Soon").

**Expected:**
- Collections link is clickable (`<a>` element, not `<div>`).
- Clicking navigates to `/collections/dashboard`.
- On the Collections page, the Collections sidebar link is highlighted
  (active state — primary colour background).
- On the CRM page, the Collections link is NOT highlighted.

**Pass criteria:** Navigation works; active state is page-specific.

---

### Test 3: All 7 KPI API endpoints return 200

**Steps:**
1. Open `http://localhost:8000/collections/dashboard`.
2. Open DevTools → Network tab → filter by "XHR/Fetch".
3. Wait for the page to finish loading (~2–5 seconds).

**Expected:** Seven fetch requests, each returning HTTP 200:

| # | Endpoint |
|---|---|
| 1 | `/api/v1/collections/kpi/late-uncollected` |
| 2 | `/api/v1/collections/kpi/total-portfolio-value` |
| 3 | `/api/v1/collections/kpi/late-uncollected-by-project` |
| 4 | `/api/v1/collections/kpi/pending-check-exposure` |
| 5 | `/api/v1/collections/kpi/collection-trend-6m` |
| 6 | `/api/v1/collections/kpi/collection-rate` |
| 7 | `/api/v1/collections/kpi/collection-rate-by-project` |

**Pass criteria:** All 7 show status 200, no 404 or 503.

---

### Test 4: Console is clean — ONE log only

**Steps:**
1. Open `http://localhost:8000/collections/dashboard`.
2. Open DevTools → Console tab.
3. Clear the console, then hard-refresh the page (Ctrl+Shift+R).

**Expected:**
- Exactly **one** log line:  
  `[Collections] Fetched 7 KPIs in <X>ms`  
  where X is a number (typically 200–2000ms on a local Odoo connection).
- **No** JavaScript errors.
- **No** additional `console.log` lines from collections.js or
  formatters.js.

**Pass criteria:** One log, zero errors, zero other logs from Collections
code.

---

### Test 5: Skeleton layout renders correctly

**Steps:**
1. Open `http://localhost:8000/collections/dashboard` in English/light mode.
2. Inspect the page visually.

**Expected layout (top to bottom):**
1. Header bar: "Collections Dashboard" title + "as of …" + Last updated
   timestamp + Refresh button.
2. Warning banner: "Data entry in progress" (amber, full-width).
3. **Row 1 — Hero:** One wide skeleton card (taller than the trio cards).
4. **Row 2 — Trio:** Three equal-width skeleton cards side by side.
5. **Row 3 — Projects:** Section label "Top 3 Projects Performance" +
   three equal-width skeleton project cards.
6. **Row 4 — Trend:** Full-width chart skeleton panel.

**Pass criteria:** All 6 sections visible, no broken layout, no raw
`—` or placeholder text visible in the skeleton state.

---

### Test 6: Arabic RTL dark mode

**Steps:**
1. From the Collections dashboard, click the language button in the
   topbar → select **العربية**.
2. Click the theme button → select **Dark**.

**Expected:**
- Page title becomes "لوحة التحصيلات".
- Page direction flips to RTL (Arabic text aligns right).
- Warning banner text: "البيانات تحت الإدخال".
- Dark mode background: neutral-950.
- The sidebar Collections link text: "التحصيلات".

**Pass criteria:** Title, banner, sidebar all show Arabic text; RTL
layout is intact; dark mode is applied.

---

### Test 7: Refresh button works

**Steps:**
1. Open `http://localhost:8000/collections/dashboard`.
2. Wait for initial load (see Test 4 for the console log).
3. Click the **Refresh** button in the Collections page header.

**Expected:**
- A second `[Collections] Fetched 7 KPIs in Xms` log appears in the
  console.
- The "Last updated" timestamp updates to the current time.
- The live-dot turns green (success-500) and pulses.
- No page navigation occurs.

**Pass criteria:** Refresh triggers a second full KPI fetch and updates
the timestamp without a page reload.

---

## Screenshot Instructions

Take two screenshots and save them to `docs/screenshots/D1/`:

1. **`D1_en_light.png`** — English light mode (default). URL:
   `http://localhost:8000/collections/dashboard`. Capture the full
   viewport including sidebar.

2. **`D1_ar_dark.png`** — Arabic dark mode. Switch language → AR,
   theme → Dark, then refresh. Capture full viewport.

These screenshots are required for Checkpoint A sign-off.

---

## Known Non-Issues

| Item | Status |
|---|---|
| `caniuse-lite is outdated` in `npm run build:css` stderr | Non-removable advisory. Build succeeds. See browserslist commit. |
| KPI cards show `—` values | Expected. D1 is skeleton only. D2 renders live values. |
| Collection Rate shows `—` or `0.00%` | Expected. No 2026 payment headers in Odoo yet (data-entry phase). |
| 6-Month Trend shows only December 2025 | Expected per Decision 5.7. |
