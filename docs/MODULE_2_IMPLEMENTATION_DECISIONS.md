# Module 2 — Implementation Decisions Log

> **Status:** Living document — append-only.
> **Convention:** Each implementation session appends a new section.
> Previous sessions are not edited (decisions may be marked
> "superseded by Session N" but the original entry stays).
> **Purpose:** Preserve the rationale behind implementation choices
> so future sessions do not re-litigate settled questions.

---

## Session 1 — 2026-05-16 — Scaffold + KPI 2 Backend

### Decision 1.1 — Caching: in-memory dict (not Redis)

- **Choice:** Python dict held in module-level state, 60-second TTL,
  keyed by `<function_name>:<YYYY-MM-DD>` so it auto-invalidates
  at midnight UTC.
- **Rationale:** Single-user MVP — Khaled validates internally
  before Board launch. Redis brings deployment, monitoring, and
  failure-mode complexity that is not yet justified.
- **Migration trigger:** Move to Redis when the Board begins
  concurrent dashboard access. Estimated effort: ~1 hour (drop-in
  replacement of the cache backend behind a thin interface).
- **Implementation hint:** Wrap the cache in a small class or
  module with `get` / `set` / `invalidate` methods so the future
  Redis migration changes one file, not every KPI service.

### Decision 1.2 — Verification: on-demand with append-only log

- **Choice:** `scripts/verify_kpi2_live.py` runs only when Khaled
  invokes it manually. Each run appends one tab-separated row to
  `logs/kpi2_verification.log`. No cron, no scheduled task, no
  notifications.
- **Rationale:** Historical data entry is ongoing for ~1 month
  (see Decision 1.3). A cron-driven verification would generate
  noise from legitimate daily data corrections that look like
  drift but are not. Khaled controlling when to measure keeps
  the log signal high.
- **Future change:** When La Verde's historical data entry
  completes, evaluate moving to a daily cron with a dashboard
  alert when delta exceeds a threshold to be defined.

### Decision 1.3 — Board launch timing: deferred until data entry completes

- **Choice:** Build the full KPI 2 stack (backend + verification
  + eventually frontend) but do NOT announce or expose to the
  Board of Directors until La Verde's historical data entry
  effort completes (estimated ~1 month from 2026-05-16, per
  Khaled's confirmation).
- **Rationale:** While historical entries are being corrected
  daily, the Late Uncollected figure can shift by 2-3M EGP
  overnight purely from data corrections, with no underlying
  business event. Presenting this to the Chairman would generate
  unanswerable questions ("why did this drop 2M overnight if
  nobody paid?"). Khaled uses the internal MVP for polish during
  this period.
- **Launch criteria for Board:**
  1. Khaled confirms La Verde's historical data entry effort
     is complete.
  2. Two consecutive verification runs show backend value
     matching Odoo Collections Mgmt UI within ±1 EGP.
  3. Frontend KPI 2 card built and reviewed by Khaled (not in
     this session).

### Decision 1.4 — Late domain reconciliation: deferred indefinitely

- **Choice:** Accept that the ~2.9M EGP delta between Late domain
  Candidate C output (2026-05-15) and the snapshot baseline
  (2026-05-14) is operational, not a domain bug. No reconciliation
  script will be built.
- **Rationale:** Khaled confirmed La Verde is actively entering
  and correcting historical data daily. Delta is expected to
  fluctuate during the data entry period and converge after it
  completes. The Late domain (Candidate C, see
  `docs/MODULE_2_DISCOVERY_PHASE_2.md §3`) is correct as defined.
- **Revisit trigger:** If, after Khaled confirms data entry is
  complete, the delta does not converge toward near-zero (within
  one normal business day of payment posting), that indicates a
  structural domain issue and Phase 3 investigation is required.

### Decision 1.5 — Reconcile dependency for future KPIs (note, not a decision)

- **Note:** Reconcile balances are not `rs.installment` records
  and are not in scope for any of the 6 MVP KPIs. However, any
  future KPI or AI intent answering "total cash received from
  customer X" must include the customer's reconcile balance.
- **Action:** Phase 3 discovery must identify the reconcile Odoo
  model and balance field. See the new entry in
  `docs/MODULE_2_BUSINESS_CONTEXT.md` "Open Questions —
  Discovery Status".

### Decision 1.6 — Module scaffold approach: pattern reuse, not file copy

- **Choice:** The Collections module mirrors the CRM module's
  folder structure and architectural patterns (service layer,
  routing, caching abstraction, test layout), but does not copy
  CRM code files. Collections is its own module with its own
  services and its own (initially empty) data fetcher.
- **Rationale:** Code copying would duplicate maintenance burden
  and entangle the modules. Pattern reuse keeps modules
  independent while preserving the proven Phase 5 architecture.

### Decision 1.7 — Today's date source: Odoo server date

- **Choice:** Use the Odoo server's current date for the
  `('date', '<', today)` clause of the Late domain, not the
  Python process's `date.today()`.
- **Rationale:** The backend may run in a different timezone or
  on a host with clock drift relative to Odoo. The Late domain
  is evaluated by Odoo against `rs.installment.date` (a `date`
  field, no timezone), and the snapshot baseline was taken using
  Odoo's notion of "today". Sourcing today from Odoo keeps
  comparisons consistent.
- **Implementation note:** If fetching Odoo server date requires
  an extra RPC, cache it for the duration of a single request.
  If `ALLOWED_METHODS` does not permit a server-date method,
  fall back to `date.today()` in UTC and document the fallback
  in code comments. The decision to use the fallback rather than
  add a method to `ALLOWED_METHODS` belongs to Khaled — escalate
  before silently falling back.

### Verification Result — Session 1 Close

**Date:** 2026-05-16
**Method:** `scripts/verify_kpi2_live.py` against live Odoo via
the running backend, cross-checked manually against Odoo
Collections Mgmt → Late Installments tab.

| Metric | Backend | Odoo UI | Delta |
|---|---|---|---|
| Due Amount (EGP) | 318,626,200.40 | 318,626,200.40 | **0.00** |
| Record count | 1,981 | 1,981 | **0** |

**Conclusion:** The three-clause Candidate C Late domain
(`state=post` AND `payment_state IN [unpaid, partial]` AND
`date < today`) reproduces Odoo's native Late Installments view
identity-equal at the EGP level on 2026-05-16. KPI 2 backend
is production-ready from a numeric-correctness standpoint.

**Caveats preserved from Decision 1.3:**
- Board launch remains deferred until La Verde's historical
  data entry effort completes.
- Ongoing daily verification is the only acceptable proof of
  continued correctness — a 2026-05-16 match does not guarantee
  a 2026-06-16 match if domain semantics change in Odoo.

---

## Session 2 — 2026-05-16 — KPI 1 Backend + verify_kpi2 fixes

### Decision 2.1 — KPI 1 domain: `state='post'` (not empty)

- **Choice:** Total Portfolio Value uses domain
  `[('state', '=', 'post')]` and aggregates `SUM(amount)` across
  all posted `rs.installment` records (~42,443 records).
- **Rationale:** The KPI is defined in
  `docs/MODULE_2_MVP_DESIGN.md §3.2 KPI 1` as the portfolio
  total matching the Odoo "All Installments" view. The original
  design specified an empty domain; this was corrected during
  live verification (see Decision 2.4).
- **Baseline (2026-05-14):** 6,123,549,625.23 EGP at ~42,443
  posted records. (The original design cited 42,970 — the total
  including draft and cancelled — which was incorrect notation
  for the baseline figure. The 6.12B number itself was always
  correct.)

### Decision 2.2 — verify_kpi2_live.py display bug fixes

- **Choice:** Fixed two display bugs surfaced at the end of
  Session 1:
  1. Range-check messages had inverted comparison operators in
     their f-strings (now corrected: `>=` and `<=`).
  2. The `domain[2][2]` date value was not asserted (now asserted
     to be a valid ISO date within ±1 day of UTC today).
- **Rationale:** The assertions themselves were already correct
  in Session 1; only the log message strings were misleading.
  Adding the missing date-value assertion closes a small but real
  coverage gap.
- **Method:** Fix applied before any KPI 1 code so
  `verify_kpi1_live.py` could be modelled on a clean template.
- **Post-fix verification (2026-05-16):** 24 assertions, all
  PASS. No production impact on KPI 2 itself (backend value
  318,626,200.40 EGP, 1,981 records — identical to Session 1
  verification result).

### Decision 2.3 — KPI 1 cache key independence

- **Choice:** Cache keys are prefixed per-KPI
  (`kpi:late_uncollected:...` vs `kpi:total_portfolio_value:...`)
  so each KPI's cache lifecycle is independent.
- **Rationale:** Prevents any future cross-KPI cache pollution and
  allows each KPI to be invalidated on its own if needed.
- **Scaling note (future sessions):** The per-KPI module-level
  constant pattern (`_CACHE_KEY_PREFIX`, `_CACHE_KEY_PREFIX_KPI1`)
  will not scale cleanly past 3-4 KPIs. Session 3 will refactor
  to a dict or per-function local constants when KPI 3 is added.

### Decision 2.4 — KPI 1 domain correction: `state='post'`, not empty

- **Choice:** KPI 1 domain is `[('state', '=', 'post')]`, not
  the empty list `[]` originally specified in MVP Design §3.2
  KPI 1.
- **Discovery:** During Session 2 verification, the empty-domain
  query returned 6,266,498,967.23 EGP (42,970 records), but the
  Odoo "All Installments" UI showed 6,123,549,625.23 EGP.
  Investigation script (`scripts/investigate_kpi1_delta.py`)
  proved the Odoo view applies a `state='post'` filter at the
  view layer, excluding 19 draft records (8,699,849.00 EGP) and
  508 cancelled records (134,249,493.00 EGP) — total 527 records
  / 142,949,342.00 EGP delta, accounted for exactly.
- **Rationale:** The Board sees the Odoo UI; our backend must
  match it identity-equal. Draft and cancelled installments are
  not part of the "portfolio" in any business sense — they are
  in-progress or voided records.
- **Side note:** The MVP Design baseline of 6,123,549,625.23 EGP
  was always the post-only total — it matched the snapshot Khaled
  took from the Odoo UI on 2026-05-14. The "no domain filter /
  42,970 records" notation in the design doc was incorrect from
  the start; the baseline number itself was correct.
- **Cross-module consistency:** KPI 2's domain already starts
  with `('state', '=', 'post')`. KPI 1's domain alignment makes
  both KPIs share the same `state='post'` prefix, which is the
  right business semantic ("posted installments are the real
  portfolio").
- **Action item — Phase 3 discovery:** Verify the same
  `state='post'` exclusion applies (or doesn't) to KPIs 3, 4,
  5, 6 before each is implemented. Do NOT assume.
- **Investigation script:** Committed at
  `scripts/investigate_kpi1_delta.py` for audit trail.

### Decision 2.5 — Investigation scripts kept in `scripts/`

- **Choice:** One-off investigation scripts (like
  `investigate_kpi1_delta.py`) are committed to `scripts/`
  rather than deleted after use.
- **Rationale:** Audit trail. When a future reviewer asks "how
  did you determine KPI 1 needs `state='post'`?", the script +
  its output in the decisions doc tell the full story. Disk
  cost is negligible; clarity benefit is large.

### Decision 2.6 — Auto-push incident and prevention

- **Incident:** During Session 2, the Claude Code IDE auto-pushed
  commits to `origin/main` before Khaled's explicit "push"
  instruction. Affected commits: D0 (verify_kpi2 fixes), D1
  (KPI 1 service with the original `domain=[]`), D2 (endpoint),
  D3 (verify script), and the investigation script. The KPI 1
  domain fix, unit tests, and Decision 2.4 documentation were
  withheld until explicit approval, but the buggy initial
  KPI 1 implementation was on `origin/main` for approximately
  one hour before the fix landed.
- **External impact:** None. The Collections module has no
  frontend yet (Pillar 1 not built), the Board has no access
  (Decision 1.3), and no production deployment pulls from
  `origin/main` automatically. The bug existed only in the git
  history during a development window.
- **Audit trail:** The git history preserves the full sequence:
  initial commit with `domain=[]`, investigation script with
  evidence, fix commit with `state='post'`. A future reviewer
  can trace the discovery and correction in commit order.
- **Mitigation for future sessions:** Disable auto-push in the
  Claude Code IDE settings before starting any subsequent
  session. The "Push to origin/main only after Khaled's explicit
  push instruction" rule in session prompts is operationally
  meaningless if the IDE pushes anyway.
- **Action item for Khaled:** Locate and disable the Claude
  Code IDE auto-push setting before Session 3 begins.

This decision documents the incident; it does not require any
code change.

### Verification Result — Session 2 KPI 1 Close

**Date:** 2026-05-16
**Method:** `scripts/verify_kpi1_live.py` against live Odoo via
the running backend, cross-checked manually against Odoo
Collections Mgmt → All Installments → Amount measure.

| Metric | Backend | Odoo UI | Delta |
|---|---|---|---|
| Amount (EGP) | 6,123,549,625.23 | 6,123,549,625.23 | **0.00** |
| Record count | 42,443 | 42,443 (view total) | **0** |

**Conclusion:** The corrected single-clause domain
`[('state', '=', 'post')]` reproduces Odoo's All Installments
view identity-equal at the EGP level on 2026-05-16. KPI 1
backend is production-ready from a numeric-correctness standpoint.

**Caveats:**
- Same Board launch deferral as KPI 2 (Decision 1.3).
- Ongoing daily verification is the only acceptable proof of
  continued correctness.
- Subsequent KPIs (3, 4, 5, 6) must each verify their own
  domain semantics against the corresponding Odoo view —
  do not assume `state='post'` applies universally.

---
