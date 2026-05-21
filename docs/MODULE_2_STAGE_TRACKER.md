# Module 2 — Stage Tracker

**Last updated:** 2026-05-21 (Stage 5 closed)

This is the single source of truth for "where are we?" in
Module 2. Update at the close of every stage.

## Stage table

| Stage | Status | Tag | Session | Commits | Key Output |
|---|---|---|---|---|---|
| 1 — KPI 7 backend | ✅ Closed | `checkpoint-C-stage1-kpi7-backend-complete` | Session 9 | 4 | Expected Collections forecast endpoint |
| 2 — KPI 2 cheques extension | ✅ Closed | `checkpoint-C-stage2-kpi2-extended` | Session 10 (10.1-10.9) | 5 | KPI 2 backend extended (PATH C) |
| 3 — Frontend Restructure | ✅ Closed | `checkpoint-D-stage3-frontend-restructure-complete` | Session 11 (11.1-11.18) | 5 + 1 fix + 1 doc | 4-section layout, state refactor |
| 2.5 — KPI 2 redefinition | ✅ Closed | `checkpoint-C-stage2-5-kpi2-redefined` | Session 12 | 6 | KPI 2 formula PATH A; +1.93M EGP cheques annotation |
| 4 — Premium Visual Identity | ✅ Closed | `checkpoint-D-stage4-premium-visual-identity-complete` | Session 13 | 10 | Dark canvas, heartbeat, premium cards, cheques pill, D2.9 fix |
| 5 — Drill-down Backend | ✅ Closed | `checkpoint-E-stage5-drilldown-backend-complete` | Session 14 | 8 | 5 drill-down endpoints, D6 8/8 PASS, KPI 7 cheques_record_count |
| 6 — Drill-down Frontend | ⏳ Planned | — | (Session 15) | TBD | Drill-down UI integration |

## Current Numbers Baseline (2026-05-21, D6 live verification)

Values are live Odoo readings as of the D6 gate run. They will drift as
La Verde staff enter data daily.

| KPI | Value | Notes |
|---|---|---|
| KPI 1 Portfolio | 6,121,816,265.23 EGP / 42,413 records | D6 V3 confirmed |
| KPI 2 Late | 332,036,464.40 EGP / 2,027 records | D6 V1 confirmed; PATH A (Decision 12.1) |
| KPI 2 Cheques in Pipeline | — | not re-read in D6; prior baseline 790,500 EGP (Decision 14.6a) |
| KPI 3 Pending Check Exposure | backend live, frontend removed | per refactor §6 |
| KPI 4 Collection Rate | unavailable (data-state) | Decision 11.16 |
| KPI 5 New Capital | 171,695,538.40 EGP | D6 V4 confirmed |
| KPI 5 Cassette | 154,822,426.00 EGP | D6 V4 confirmed |
| KPI 5 La puerta | 3,589,500.00 EGP | D6 V4 confirmed |
| KPI 5 Sum | 330,107,464.40 EGP | = KPI 2 ✓ |
| KPI 7 This Year | — | not re-read in D6 |
| KPI 7 Cheques this_year | 790,500 EGP / count = 2 | D6 V8 confirmed; Decision 14.6a baseline |

## Rollback Tags

| Tag | Phase | Date |
|---|---|---|
| `checkpoint-A-D1-complete` | Module 2 Phase 5 D1 close | (historical) |
| `checkpoint-B-D2-complete` | Module 2 Phase 5 D2 close | (historical) |
| `checkpoint-C-stage1-kpi7-backend-complete` | Stage 1 close | 2026-05-17 |
| `checkpoint-C-stage2-kpi2-extended` | Stage 2 close | 2026-05-19 |
| `checkpoint-D-stage3-frontend-restructure-complete` | Stage 3 close | 2026-05-19 |
| `checkpoint-C-stage2-5-kpi2-redefined` | Stage 2.5 close | 2026-05-20 |
| `checkpoint-D-stage4-premium-visual-identity-complete` | Stage 4 close | 2026-05-20 |
| `checkpoint-E-stage5-drilldown-backend-complete` | Stage 5 close | 2026-05-21 |

## Maintenance instructions

UPDATE THIS DOCUMENT at the close of every stage:
1. Change the Stage's status from 🔄 to ✅
2. Fill in the actual tag, session range, commit count, and key output
3. Add the new tag to the Rollback Tags table
4. Update Current Numbers Baseline if any KPI value changed
5. Update "Last updated" date at the top
