# Phase 5 Bug Hunt — Root Cause Analysis

**Date:** 2026-05-12  
**Session:** Live data verification against https://laverde.odoo.com  
**Tool:** `scripts/diag_stages.py` (read-only, zero OpenAI calls)

---

## Diagnostic Output (verbatim)

```
========================================================================
CRM Stage Diagnostic — READ-ONLY — ZERO OpenAI calls
========================================================================

[1] Authenticating...
    OK uid=1947

[2] Fetching all crm.stage records (search_read)...
    Found 18 stage records

     ID   SEQ   FOLD  TEAM                            NAME
  --------------------------------------------------------------------
     24     0     no  (global/shared)                 New
     44     1     no  (global/shared)                 New X
     46     1    YES  (global/shared)                 Lost
     25     2     no  (global/shared)                 No Answer
     26     3     no  (global/shared)                 Wrong Number
     27     4     no  (global/shared)                 Follow up
     28     5     no  (global/shared)                 Interested
     29     6     no  (global/shared)                 Contact in the Future
     33     7     no  (global/shared)                 Re-Distribution
     30     8     no  Managers team                   Unqualified
     31    10     no  Managers team                   Unavailable Request
     38    11     no  (global/shared)                 Cancel Reservation
     32    12     no  Managers team                   Bought Out
     42    13     no  (global/shared)                 Cancel Contract
     34    14     no  Managers team                   Draft Reservation
     35    15     no  Managers team                   Initial Reservation
     37    16     no  Managers team                   Reservation
     41    19     no  Managers team                   Down Payment Confirm & Contracted

[3] Lead counts per stage (read_group, read-only)...

     ID    TOTAL   OVERDUE    DIFF  NAME
  --------------------------------------------------------------------
     24       97         1      96  New  <- NEW
     44     2923         0    2923  New X  <- NEW
     46     5234        15    5219  Lost
     25     1683        32    1651  No Answer
     26       46         0      46  Wrong Number
     27     4790        78    4712  Follow up
     28      610        24     586  Interested
     29     8950         3    8947  Contact in the Future
     33     2546       243    2303  Re-Distribution
     30     3023         0    3023  Unqualified
     31        1         0       1  Unavailable Request
     38       36         0      36  Cancel Reservation
     32        1         0       1  Bought Out
     42       21         0      21  Cancel Contract
     35       40         0      40  Initial Reservation
     37       45         1      44  Reservation
     41      835         0     835  Down Payment Confirm & Contracted
  --------------------------------------------------------------------
    ALL    30881       397

[4] Stages matching 'new' in name:
    stage_id=24  name='New'  team='global'  total=97  overdue=1
    stage_id=44  name='New X'  team='global'  total=2923  overdue=0

    AI reports (overdue only): 1
    Odoo shows you (all leads): 3020
    Gap (hidden leads):         3019
```

---

## Bug 1 — Semantic Mismatch: `count_by_stage` Answers the Wrong Question

### What the bug is (plain language)

When the user asks "ما هي تفاصيل leads في مرحلة New؟" ("how many leads are in New stage?"), the AI responds with the count of leads that have an **overdue follow-up activity** in that stage — not the total number of leads in the stage. Stage "New" (ID=24) has 97 resolved opportunities, but only 1 has an overdue activity flag. The AI says 1. The user sees 97 in Odoo. The AI is not lying; it is answering a different question than the one asked.

### Why it happens (code references)

The intent handler `_handle_count_by_stage` in
`backend/modules/ai/chat/data_fetcher.py:114` calls
`crm.overdue_by_stage()` at line 118:

```python
async def _handle_count_by_stage(crm: CrmService, filters: dict, _p: Any) -> dict:
    ...
    rows = await crm.overdue_by_stage()   # ← wrong method
    if stage_filter:
        matching = [r for r in rows if stage_filter in r.stage_name.lower()]
        count = sum(r.overdue_count for r in matching)
```

`overdue_by_stage()` in `backend/modules/crm/service.py:151` builds:

```python
domain = BASE_DOMAIN + [
    ["activity_state", "=", "overdue"],   # ← only overdue activities
    ["stage_id", "not in", get_closed_excluded_stage_ids()],
]
```

There is no `CrmService` method that counts **all leads by stage**. The only
stage-aggregation method the service exposes is overdue-gated. The intent handler
inherited this method from the dashboard's overdue-tracking logic and was never
given the correct query for the question being asked.

### The proposed fix

A. **New `CrmService` method** — `leads_by_stage(overdue_only: bool = False)`:
   Returns `list[OverdueByStage]` (reuse schema) grouped by `stage_id`.
   When `overdue_only=False` the domain is just `BASE_DOMAIN` (no activity_state filter).
   When `overdue_only=True` it matches the existing `overdue_by_stage()` behaviour.

B. **Update `_handle_count_by_stage`** to call
   `crm.leads_by_stage(overdue_only=False)`. Users asking "كم lead في X؟"
   get total leads, not overdue-only leads.

C. **New intent `count_overdue_by_stage`** handled by a new `_handle_count_overdue_by_stage`
   that calls `crm.leads_by_stage(overdue_only=True)`. Dispatched when the
   user explicitly asks about overdue/متأخر leads.

D. **Keep `overdue_by_stage()`** unchanged — the dashboard and other intent handlers
   that correctly ask for overdue-only data (`list_overdue_by_stage`,
   `team_performance_summary`) must continue to use it.

E. **Update the intent parser prompt** with examples distinguishing the two questions
   and default the ambiguous case to `count_by_stage` (total).

---

## Bug 2 — Substring Match Conflates "New" with "New X"

### What the bug is

The stage filter uses substring matching: `"new" in stage_name.lower()`. Stage "New X"
(ID=44, the data-quality/unclassified bucket, 2,923 leads) matches this filter alongside
the real pipeline stage "New" (ID=24, 97 leads). After Bug 1 is fixed and the AI starts
reporting total leads, a user asking about "New" stage would get 3,020 — a number that is
meaningless and alarming.

### Why it happens

`_normalise_stage("New")` returns `"New"`. The filter `"new" in r.stage_name.lower()`
matches both "New" (exact) and "New X" (substring). The code was written for
overdue-only counts where "New X" happened to have 0 overdue leads, so the bug was
invisible. It will surface immediately after Bug 1 is fixed.

### The proposed fix

Exact-name match instead of substring match for stage filtering:
`stage_filter == r.stage_name.lower()` (equality, not `in`).

Retain substring match as a fallback: if exact match returns no results, fall back
to substring and note the ambiguity in the response data. This handles partial
Arabic transliterations while preventing "New" from matching "New X".

---

## Bug 3 — ARCHITECTURE.md Stage Name Table is Outdated

### What the bug is

`docs/ARCHITECTURE.md` lists stage names against their IDs that do not match live data:

| ID | Documented Name | Actual Name (live) |
|----|----------------|-------------------|
| 26 | Closed Won | Wrong Number |
| 28 | New Lead | Interested |
| 30 | Closed Lost | Unqualified |
| 31 | Closed Duplicate | Unavailable Request |
| 32 | Closed Invalid | Bought Out |
| 34 | Qualified | Draft Reservation |
| 35 | Proposal Sent | Initial Reservation |
| 37 | Negotiation | Reservation |
| 38 | Closed No Answer | Cancel Reservation |
| 41 | Contract Sent | Down Payment Confirm & Contracted |
| 42 | Closed Cancelled | Cancel Contract |
| 46 | Closed Transferred | Lost |

The IDs appear to still serve their intended functional role (terminal stages, critical
stages, data-quality stages). The documentation was written for a different Odoo database
snapshot and was never updated.

### Impact

Low runtime impact — the code uses IDs, not names, for hardcoded lists. Documentation
misleads anyone reading `ARCHITECTURE.md` about the actual pipeline structure.

### Fix

Update the stage name table in `docs/ARCHITECTURE.md` using the live data above.

---

## Hypothesis Refuted

> *Hypothesis: Multiple stages named "New" (one per sales team) cause undercounting.*

**Refuted.** There are no team-scoped duplicate "New" stages. Both "New" (ID=24) and
"New X" (ID=44) are global/shared stages. The gap is entirely explained by the overdue
filter in Bug 1 and the substring match in Bug 2.

---

## Summary of Required Changes

| # | File | Change |
|---|------|--------|
| 1 | `backend/modules/crm/service.py` | Add `leads_by_stage(overdue_only=False)` method |
| 2 | `backend/modules/ai/chat/data_fetcher.py` | Update `_handle_count_by_stage` to call `leads_by_stage(overdue_only=False)` |
| 3 | `backend/modules/ai/chat/data_fetcher.py` | Add `_handle_count_overdue_by_stage` using `overdue_only=True` |
| 4 | `backend/modules/ai/chat/data_fetcher.py` | Register `count_overdue_by_stage` in `_INTENT_HANDLERS` |
| 5 | `backend/modules/ai/chat/data_fetcher.py` | Change stage match from substring to exact-first, substring-fallback |
| 6 | Intent parser prompt | Add examples for total vs overdue count; default ambiguous to total |
| 7 | `scripts/verify_chat.py` | Add Scenario 8: AI count vs Odoo ground truth per stage |
| 8 | `docs/ARCHITECTURE.md` | Fix stage name table |

**DO NOT implement until user approves.**
