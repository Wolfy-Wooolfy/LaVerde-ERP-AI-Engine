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

---
