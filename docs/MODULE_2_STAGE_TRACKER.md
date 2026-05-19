# Module 2 — Stage Tracker

**Last updated:** 2026-05-19

This is the single source of truth for "where are we?" in
Module 2. Update at the close of every stage.

## Stage table

| Stage | Status | Tag | Session | Commits | Key Output |
|---|---|---|---|---|---|
| 1 — KPI 7 backend | ✅ Closed | `checkpoint-C-stage1-kpi7-backend-complete` | Session 9 | 4 | Expected Collections forecast endpoint |
| 2 — KPI 2 cheques extension | ✅ Closed | `checkpoint-C-stage2-kpi2-extended` | Session 10 (10.1-10.9) | 5 | KPI 2 backend extended (PATH C) |
| 3 — Frontend Restructure | 🔄 In closure | (pending V7-V16 sign-off) | Session 11 (11.1-11.17) | 5 + 1 fix | 4-section layout, state refactor |
| 2.5 — KPI 2 redefinition | ⏳ Planned | — | (Session 12) | TBD ~5 | KPI 2 formula → actual_paid_amount |
| 4 — Premium Visual Polish | ⏳ Planned | — | (Session 13) | TBD | "From the future" tier UX |
| 5 — Drill-down Backend | ⏳ Planned | — | (Session 14) | TBD | Drill-down endpoints + KPI 7 cheques count |
| 6 — Drill-down Frontend | ⏳ Planned | — | (Session 15) | TBD | Drill-down UI integration |

## Current Numbers Baseline (2026-05-19, smoke test)

| KPI | Value | Notes |
|---|---|---|
| KPI 1 Portfolio | 6.12B EGP / 42,443 records | confirmed |
| KPI 2 Late (current) | 326.4M EGP / 2,004 records | confirmed, Decision 10.1 formula |
| KPI 2 Late (Stage 2.5 target) | ~328.3M EGP / 2,004 records | tentative, Decision 11.13 formula, pending discovery |
| KPI 2 Cheques in Pipeline | 1.929M EGP | will become subset of KPI 2 per Decision 11.13 |
| KPI 3 Pending Check Exposure | backend live, frontend removed | per refactor §6 |
| KPI 4 Collection Rate | unavailable (data-state) | Decision 11.16 |
| KPI 5 New Capital | 168.7M EGP | confirmed |
| KPI 5 Cassette | 154.1M EGP | confirmed |
| KPI 5 La puerta | 3.6M EGP | confirmed |
| KPI 5 Sum | 326.4M EGP | = KPI 2 ✓ |
| KPI 7 This Month | 17.9M EGP / 112 installments | period_end 2026-05-31 |
| KPI 7 This Quarter | 50.7M EGP / 334 installments | period_end 2026-06-30 |
| KPI 7 This Half | 50.7M EGP / 334 installments | Q2=H1 collapse |
| KPI 7 This Year | 333.1M EGP / 1,913 installments | period_end 2026-12-31 |
| KPI 7 Cheques 2026 | 643K EGP | per KPI 7 Phase 0 discovery |
| KPI 7 Cheques 2027+ | 2.54M EGP | out of forecast scope |

## Rollback Tags

| Tag | Phase | Date |
|---|---|---|
| `checkpoint-A-D1-complete` | Module 2 Phase 5 D1 close | (historical) |
| `checkpoint-B-D2-complete` | Module 2 Phase 5 D2 close | (historical) |
| `checkpoint-C-stage1-kpi7-backend-complete` | Stage 1 close | 2026-05-17 |
| `checkpoint-C-stage2-kpi2-extended` | Stage 2 close | 2026-05-19 |
| `checkpoint-D-stage3-frontend-restructure-complete` | Stage 3 close | 2026-05-19 (pending V7-V16 sign-off) |

## Maintenance instructions

UPDATE THIS DOCUMENT at the close of every stage:
1. Change the Stage's status from 🔄 to ✅
2. Fill in the actual tag, session range, commit count, and key output
3. Add the new tag to the Rollback Tags table
4. Update Current Numbers Baseline if any KPI value changed
5. Update "Last updated" date at the top
