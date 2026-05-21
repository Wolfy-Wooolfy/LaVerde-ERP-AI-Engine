# Module 2 Stage 6 — Browser Verification Checklist

**Purpose:** Manual browser verification that all Stage 6 drill-down
UI deliverables work correctly before tagging
`checkpoint-E-stage6-drilldown-frontend-complete`.

**Pre-conditions:**
1. Backend running (uvicorn, no `--reload`). Decision 6.4 ritual:
   kill processes → purge `__pycache__` → restart.
2. Open `http://localhost:8000/collections/dashboard` in Chrome/Edge
   (latest stable). DevTools Console open — no JS errors on load.
3. Unit tests pass: `node tests/frontend/test_drilldown.js` → 46/46.

---

## V0 — Live identity re-run (verify_drilldowns_live.py)

Run **after** the Decision 6.4 clean-restart ritual (kill → purge `__pycache__`
→ restart uvicorn without `--reload`). This confirms that the frontend changes
introduced no backend regressions and that every drill-down endpoint still
sums to its current parent KPI.

```
python scripts/verify_drilldowns_live.py
```

Expected result: **8/8 GREEN**. Data drift since Stage 5 is normal — absolute
numbers will have moved as La Verde staff enter data daily. A GREEN result
means the identity holds (drill-down sum ≈ parent KPI), not that the number
matches the Stage 5 baseline. Only a broken identity (e.g., drill-down sum
differs materially from the live parent KPI) is a failure.

| # | Check | Expected | PASS/FAIL |
|---|-------|----------|-----------|
| 0.1 | `python scripts/verify_drilldowns_live.py` | 8/8 GREEN | |

---

## V1 — Panel opens for all 11 trigger targets

| # | Trigger | Expected panel title | PASS/FAIL |
|---|---------|----------------------|-----------|
| 1.1 | Click **Portfolio Scale** card (KPI 1) | "Portfolio — Customer Breakdown" | |
| 1.2 | Click **Late Uncollected** card (KPI 2) | "Late Uncollected — Detail" | |
| 1.3 | Click **cheques annotation** below KPI 2 | "Late — Received Cheques" | |
| 1.4 | Click **This Month** forecast card | "Expected Collections — This Month" | |
| 1.5 | Click **This Quarter** forecast card | "Expected Collections — This Quarter" | |
| 1.6 | Click **This Half** forecast card | "Expected Collections — This Half" | |
| 1.7 | Click **This Year** forecast card | "Expected Collections — This Year" | |
| 1.8 | Click **New Capital** project card | "{project name} — Late Detail" | |
| 1.9 | Click **Cassette** project card | "{project name} — Late Detail" | |
| 1.10 | Click **La puerta** project card | "{project name} — Late Detail" | |
| 1.11 | Click a bar/point in the **6-Month Trend** chart | "Trend — YYYY-MM" | |

**V1 criteria:** Panel slides in from the right (LTR). Rows appear after
brief loading skeleton. No JS errors in console.

---

## V2 — Panel close

| # | Action | Expected | PASS/FAIL |
|---|--------|----------|-----------|
| 2.1 | Press `Escape` while panel is open | Panel slides out; focus returns to trigger | |
| 2.2 | Click the **×** close button | Panel slides out; focus returns to trigger | |
| 2.3 | Click the **backdrop** (grey overlay) | Panel slides out | |
| 2.4 | After close, URL hash is cleared | No `#dd=` in address bar | |

---

## V3 — Filter bar visibility

| # | Panel | Filter bar visible? | PASS/FAIL |
|---|-------|---------------------|-----------|
| 3.1 | KPI 1 (Portfolio) | **No** filter bar | |
| 3.2 | KPI 2 (Late) | Filter bar with Status chips + Sort | |
| 3.3 | KPI 2 cheques | Filter bar; "Cheques only" chip **active** | |
| 3.4 | Any forecast bucket | Filter bar visible | |
| 3.5 | Any project card | Filter bar visible | |
| 3.6 | Trend month | Filter bar visible | |

---

## V4 — Filter bar functionality

| # | Action | Expected | PASS/FAIL |
|---|--------|----------|-----------|
| 4.1 | Click "Not Paid" chip on KPI 2 panel | Row list refreshes; only not_paid rows | |
| 4.2 | Click "All" chip | All rows reload | |
| 4.3 | Click "Cheques only" toggle | List shows only rows with pending_cheque > 0 | |
| 4.4 | Toggle "Cheques only" off | List returns to previous filter | |
| 4.5 | Click "Date ↑" sort button | Rows sorted ascending by date | |
| 4.6 | Click "Amount ↓" | Rows sorted descending by amount | |

---

## V5 — Pagination (Load more)

| # | Action | Expected | PASS/FAIL |
|---|--------|----------|-----------|
| 5.1 | Open KPI 1 (Portfolio) — many customers exist | "Load more (25 / N)" button appears if > 25 | |
| 5.2 | Click "Load more" | Next 25 customers appended | |
| 5.3 | Open KPI 2 (Late) — 2,027 records | Multiple pages of 25 rows | |
| 5.4 | Scroll to bottom — no more pages | Load-more button hidden | |

---

## V6 — URL hash deep-link

| # | Action | Expected | PASS/FAIL |
|---|--------|----------|-----------|
| 6.1 | Open KPI 2 panel | Address bar shows `#dd=kpi2` | |
| 6.2 | Apply "Not Paid" filter | Hash updates to `#dd=kpi2&st=unpaid` | |
| 6.3 | Copy URL, open in new tab | Panel opens with Not Paid filter active | |
| 6.4 | Open cheques annotation | Hash shows `#dd=kpi2-cheques&pc=1` | |
| 6.5 | Close panel | Hash is cleared from URL | |

---

## V7 — Keyboard navigation

| # | Action | Expected | PASS/FAIL |
|---|--------|----------|-----------|
| 7.1 | Tab to a KPI card, press **Enter** | Panel opens | |
| 7.2 | Tab to cheques annotation, press **Space** | Cheques panel opens | |
| 7.3 | While panel open: **Tab** cycles within panel | Focus does NOT escape to background | |
| 7.4 | **Shift+Tab** from close button | Focus wraps to last focusable element | |
| 7.5 | Try clicking a KPI card while panel open | Background is inert — click has no effect | |

---

## V8 — Portfolio flat rendering (Decision 15.8)

| # | Check | Expected | PASS/FAIL |
|---|-------|----------|-----------|
| 8.1 | Open Portfolio panel (KPI 1) | Customer rows with project breakdown sub-rows visible | |
| 8.2 | Sub-rows always visible | No expand/collapse button; all breakdowns shown immediately | |
| 8.3 | "بدون مشروع" / "No Project Assigned" row | Shown for customers with `project_id = null` | |
| 8.4 | Data-quality note | If `meta.data_quality` is set, note appears at top of list | |

---

## V9 — RTL mode (Arabic)

| # | Action | Expected | PASS/FAIL |
|---|--------|----------|-----------|
| 9.1 | Switch language to AR | Panel slides from **left** edge (RTL) | |
| 9.2 | Panel title in Arabic | All `dd_title_*` strings show Arabic | |
| 9.3 | Filter chips show Arabic labels | "الكل", "غير مدفوع", etc. | |
| 9.4 | Close panel | Focus returned; hash cleared | |

---

## V10 — Dark canvas / light mode

| # | Check | Expected | PASS/FAIL |
|---|-------|----------|-----------|
| 10.1 | Dark mode (default) | Panel background `bg-neutral-900`; text readable | |
| 10.2 | Light mode | Panel background `bg-white`; borders visible | |
| 10.3 | Payment badges in dark mode | Danger/warning/success colours readable | |

---

## Pass criteria

All items in V1–V10 must be PASS. Console must show no unhandled JS
errors during any test step.

After verification, Khaled creates the tag:
```
git tag checkpoint-E-stage6-drilldown-frontend-complete
git push origin --tags
```
