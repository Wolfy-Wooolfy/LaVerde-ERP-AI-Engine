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

## Session 3 — 2026-05-16 — KPI 5 Backend (Late Uncollected per Project)

### Decision 3.1 — KPI 5 scope narrowed to Late Uncollected per project only

- **Choice:** Session 3 implements only the Late Uncollected
  sub-metric of KPI 5 (per-project breakdown of the KPI 2 number).
  The Collection Rate sub-metric is deferred to a later session
  alongside KPI 4 which shares the period-based machinery.
- **Rationale:** Consistent with the one-metric-per-session pattern
  proven in Sessions 1 and 2. Avoids compound complexity that
  could repeat Lesson B (Decision 2.4) at greater scale.

### Decision 3.2 — Pre-implementation discovery is now mandatory

- **Choice:** Every new KPI session must include a Deliverable 0
  discovery script that verifies per-record and per-grouping
  semantics against Odoo BEFORE writing service code.
- **Rationale:** Lesson B from Session 2 (`domain=[]` vs
  `state='post'`) cost ~1 hour of investigation that would have
  been avoided by a 10-minute discovery script. The
  `scripts/investigate_kpi1_delta.py` pattern (read-only,
  evidence-based, audit-trail-preserved) is the template.
- **Applies to:** All future KPI sessions (3, 4, 6) and any
  re-implementation of KPI 1/2 if specs change.

### Decision 3.3 — Project order is fixed: 1, 2, 3

- **Choice:** The `projects` array in KPI 5 responses is always
  ordered by `project_id` ascending: New Capital (1), Cassette (2),
  La puerta (3). This order is enforced by the service, not by
  Odoo's response.
- **Rationale:** Consistent display order is a UI requirement that
  the backend should guarantee, not delegate.

### Decision 3.4 — Zero-padding for missing projects

- **Choice:** If `read_group` returns fewer than 3 projects (e.g.,
  one project has zero late records), the service pads the result
  with explicit zero entries. The API consumer always sees exactly
  3 projects.
- **Rationale:** Strategic Q3 (MVP Design §8) requires always
  showing all 3 projects. Backend enforcement prevents frontend
  edge-case bugs.

### Decision 3.5 — Cache key constants refactor explicitly deferred

- **Choice:** Continue the per-KPI constant pattern
  (`_CACHE_KEY_PREFIX`, `_CACHE_KEY_PREFIX_KPI1`,
  `_CACHE_KEY_PREFIX_KPI5`). Do NOT refactor to a dict or
  per-function locals in this session.
- **Rationale:** Scope discipline. Mixing a refactor commit with
  new-feature commits would (a) entangle review boundaries,
  (b) risk regressions on the verified KPI 1 and KPI 2 services,
  and (c) inflate session wall time.
- **Future trigger:** A dedicated refactor session, scheduled
  AFTER KPI 3, KPI 4, and KPI 6 backends are complete. At that
  point we will see the full pattern across 5-6 KPIs and can
  make a more informed refactor choice.
- **Supersedes:** Decision 2.3's forward-looking statement that
  "Session 3 will refactor." Decision 2.3 itself remains as
  historical record but its forward-looking statement is overridden
  by this decision.

### Decision 3.6 — Drill-down fields deferred to frontend session

- **Note:** Odoo's per-project Late view exposes Amount, Paid
  Amount, Actual Paid Amount in addition to Due Amount. The KPI 5
  drill-down design (MVP Design §3.2 KPI 5 "Drill-Down Target")
  references these.
- **Choice:** Session 3 returns only `late_uncollected`
  (= SUM(due_amount)) and `record_count` per project. The
  additional fields are deferred until the frontend session
  builds the drill-down panel.
- **Rationale:** Backend should not return data the frontend has
  not been designed for. When the drill-down session begins, the
  service will be extended (additive — no breaking change).
- **Future extension:** When extended, the per-project entry
  shape will become:
  ```python
  {
      "project_id": int,
      "project_name": str,
      "late_uncollected": float,
      "record_count": int,
      # Future additions:
      "amount": float,
      "paid_amount": float,
      "actual_paid_amount": float,
  }
  ```
  Existing API consumers will see the new fields appear; nothing
  breaks.

### Verification Result — Session 3 KPI 5 Close

**Date:** 2026-05-16
**Method:** `scripts/verify_kpi5_live.py` against live Odoo via
the running backend, cross-checked manually against Odoo
Collections Mgmt → Late Installments tab (Group By Project) at
the D0 discovery step.

| Project | Backend | Odoo UI | Delta |
|---|---|---|---|
| New Capital (id=1) | 164,017,258.40 EGP / 1,472 records | 164,017,258.40 / 1,472 | **0.00 / 0** |
| Cassette (id=2) | 151,019,442.00 EGP / 488 records | 151,019,442.00 / 488 | **0.00 / 0** |
| La puerta (id=3) | 3,589,500.00 EGP / 21 records | 3,589,500.00 / 21 | **0.00 / 0** |
| **TOTAL** | **318,626,200.40 / 1,981** | **318,626,200.40 / 1,981** | **0.00 / 0** |

**Cross-check vs KPI 2 standalone:**
- KPI 5 total = 318,626,200.40 EGP
- KPI 2 standalone = 318,626,200.40 EGP
- Delta = **0.00 EGP** (mathematical proof that grouped aggregation
  reproduces the verified KPI 2 value exactly)

**Conclusion:** The `read_group` by `project_id` over the verified
three-clause Candidate C Late domain produces identity-equal
results with Odoo's Collections Mgmt Late Installments view,
grouped by Project, at every project and at the total. KPI 5
backend is production-ready from a numeric-correctness standpoint.

**Bonus drill-down evidence (out of scope, for the future):**
The Odoo per-project Late view also exposes Amount, Paid Amount,
and Actual Paid Amount fields. Per Decision 3.6, these are
deferred to the frontend drill-down session. Notable: La puerta
shows zero Paid Amount and zero Actual Paid Amount across its
21 late records — a data point the Board will likely discuss but
not an implementation concern.

**Caveats:**
- Same Board launch deferral as KPIs 1 and 2 (Decision 1.3).
- Daily delta of 2-3M EGP on KPI 2 (and proportionally on KPI 5's
  total) is expected during the historical data entry period.
- The KPI 5 Collection Rate per-project sub-metric remains
  unimplemented (Decision 3.1) — to be built alongside KPI 4.

---

## Session 4 — 2026-05-16 — KPI 3 Backend (Pending Check Exposure)

### Decision 4.1 — KPI 3 domain: `state='post'`, not empty

- **Choice:** Pending Check Exposure uses domain `[('state', '=', 'post')]`
  and aggregates both `SUM(paid_amount)` and
  `SUM(x_studio_actual_paid_amount)` across all posted `rs.installment`
  records (~42,443 records). The MVP Design originally specified
  "no domain filter" for KPI 3.
- **Discovery (D0):** `scripts/discover_kpi3_domain.py` revealed that 508
  cancelled installments carry `paid_amount = 2,470,884.00 EGP` and
  `x_studio_actual_paid_amount = 0.00 EGP`, yielding a derived exposure
  of 2,470,884.00 EGP. These are postdated cheques submitted before
  contract cancellation whose `paid_amount` was never reversed. Including
  them inflates the KPI by 2.47M EGP relative to Odoo's own calculation.
- **Confirmation:** Odoo's native `check_pending_amount` stored field
  (Decision 4.5) computes `518,235,384.10 EGP` at `state='post'`, which
  is identity-equal to the derived formula at the same domain. The
  cancelled-state records are excluded from `check_pending_amount` by
  Odoo's own logic — confirming `state='post'` is the correct semantic.
- **Cross-module consistency:** All four implemented KPIs (1, 2, 3, 5)
  now use `state='post'` as the base clause. This is the correct business
  semantic: "posted installments are the real portfolio".
- **Implementation:** Single-clause domain `[("state", "=", "post")]`
  passed as the first positional argument to `read_group`.
- **Supersedes:** "Domain: none" in MVP Design §3.2 KPI 3.

### Decision 4.2 — KPI 3 aggregation: two-field read_group in one RPC

- **Choice:** A single `read_group` call with
  `fields=["paid_amount", "x_studio_actual_paid_amount"]` retrieves both
  aggregation sums in one round-trip. The result row contains both field
  keys plus `__count`.
- **Rationale:** Two separate RPC calls would double network overhead for
  no benefit. Odoo's `read_group` API supports multiple aggregation fields
  natively; this is the first KPI in the codebase to exercise that
  capability.
- **kwargs:** `lazy=False` (same pattern as KPI 5's project grouping)
  is passed to prevent lazy evaluation — the grouped result is consumed
  as a flat list with one row (no grouping clause in this call).
- **Edge case:** If `rows` is empty (no posted installments exist),
  both sums default to `0.0` via `row.get("paid_amount") or 0.0`.

### Decision 4.3 — derivation_note: fixed string in every response

- **Choice:** Every KPI 3 response includes
  `"derivation_note": "value = paid_amount_sum - actual_paid_sum"` as an
  explicit field. This string is a constant — it does not vary by
  request.
- **Rationale:** KPI 3's value is not a native Odoo field; it is derived.
  Future consumers (frontend, AI chat, audit log) must know the formula
  without reading this document. Embedding it in the payload makes the
  derivation self-documenting and machine-readable.
- **Decision scope:** The exact string is locked at D3 verification.
  Any change to the formula requires a new decision superseding this one.

### Decision 4.4 — Negative derived value: Option A (return as-is + warn)

- **Choice:** If `SUM(paid_amount) − SUM(x_studio_actual_paid_amount)` is
  negative, the service:
  1. Returns `value` as-is (the negative float).
  2. Logs a `logger.warning(...)` using `%s` format with `paid_amount_sum`,
     `actual_paid_sum`, and `value` as arguments.
  3. Adds `"data_quality_warning": "value_is_negative"` to the response
     payload.
  4. Sets `"data_quality_warning": null` when value is non-negative.
- **Rationale:** A negative exposure is logically impossible (checks
  received cannot exceed "cleared checks + uncashed checks") and
  would indicate a data quality anomaly in Odoo Studio fields — not a
  calculation error in our backend. Hiding the anomaly or clamping to
  zero would mask a real Odoo data problem. Returning it as-is lets the
  frontend (and Khaled) observe the anomaly and investigate in Odoo.
- **Why not raise an exception:** This is not an Odoo connectivity failure
  — the query succeeded. A 503 would confuse the caller into thinking
  the service is down. The `data_quality_warning` field is the correct
  channel for data-level anomalies.
- **Why not Option B (return zero):** Clamping silently removes the
  signal that something is wrong in Odoo. The warning field achieves the
  same "safe display" goal without information loss.
- **Unit test:** `test_kpi3_negative_derived_value_option_a` covers all
  three behaviors: `value == approx(-100.0)`, `logger.warning` called
  once, `data_quality_warning == "value_is_negative"`.

### Decision 4.5 — Phase 2 Dependency #7 resolved: derived formula = check_pending_amount

- **Resolution:** `MODULE_2_MVP_DESIGN.md §7 Dependency #7` asked whether
  `paid_amount − x_studio_actual_paid_amount` equals Odoo's native
  `check_pending_amount` field on `rs.installment`. D0 discovery resolved
  this: at `state='post'` domain, `check_pending_amount` aggregate =
  518,235,384.10 EGP, which is identity-equal (delta = 0.00 EGP) to the
  derived formula.
- **Choice:** Continue using the derived formula (not `check_pending_amount`)
  as the canonical source for KPI 3. Both give identical results, but the
  derived formula makes the two component sums (`paid_amount_sum`,
  `actual_paid_sum`) visible in the response payload, enabling the
  frontend drill-down panel and the AI chat to display them without an
  additional query.
- **Phase 2 Dependency #7 status:** Closed. The formulas agree. The
  drill-down filter (`paid_amount − x_studio_actual_paid_amount > 0`)
  noted in MVP Design §3.4 remains the correct approach; simplifying to
  `check_pending_amount > 0` is equivalent but provides less detail.

### Verification Result — Session 4 KPI 3 Close

**Date:** 2026-05-16
**Method:** `scripts/verify_kpi3_live.py` against live Odoo via
the running backend, cross-checked against Odoo's `check_pending_amount`
aggregate in D0 discovery script (`scripts/discover_kpi3_domain.py`).

| Metric | Backend | Odoo (D0 check_pending_amount) | Delta |
|---|---|---|---|
| Pending Check Exposure (EGP) | 518,235,384.10 | 518,235,384.10 | **0.00** |
| paid_amount_sum (EGP) | 3,488,834,648.95 | 3,488,834,648.95 | **0.00** |
| actual_paid_sum (EGP) | 2,970,599,264.85 | 2,970,599,264.85 | **0.00** |
| Record count | 42,443 | 42,443 | **0** |

**Assertions:** 16 assertions — all PASS. Includes: all 11 response
keys, value in [400M, 700M] range, domain = `[['state','=','post']]`,
`paid_amount_sum > actual_paid_sum`, derivation math
(|paid−actual−value| < 0.01 EGP), `derivation_note` exact string,
`data_quality_warning` is None, response headers
(`Cache-Control: private, max-age=60`, `X-Cache-Status: fresh`),
and cache hit on second request
(`cache_status == 'cached'`, `rpc_duration_ms == 0`).

**Cross-module confirmation:**
This is the fourth consecutive identity-equal verification in Module 2
(KPI 1 on 2026-05-16, KPI 2 on 2026-05-16, KPI 5 on 2026-05-16,
KPI 3 on 2026-05-16). All four KPIs match Odoo at the cent level.

**Conclusion:** The single-clause `state='post'` domain with two-field
`read_group` aggregation reproduces Odoo's `check_pending_amount` sum
identity-equal. KPI 3 backend is production-ready from a
numeric-correctness standpoint.

**Caveats:**
- Same Board launch deferral as KPIs 1, 2, and 5 (Decision 1.3).
- Daily drift is expected as treasury processes checks in RS Accounting.
  The pending exposure should decrease as checks clear and
  `x_studio_actual_paid_amount` is updated.
- The D0 verification date was 2026-05-16. Subsequent verification runs
  (`scripts/verify_kpi3_live.py`) will show daily drift; the [400M, 700M]
  sanity bounds allow ±100M of realistic drift from the D0 baseline.

---

## Session 5 — KPI 6: 6-Month Collection Trend

**Session date:** 2026-05-17  
**Scope:** Pre-D1 cache refactor, D1 (service), D2 (endpoint), D3 (verification script), D4 (unit tests)

---

### Decision 5.1 — State filter required for payment headers

**Status:** Approved  
**Context:** D0 Part 1 side-by-side comparison (Section 5) found +83,000 EGP delta between
unfiltered and `state='post'` filtered results on `rs.account.payment.installment`.  
**Decision:** Apply `("state", "=", "post")` to the KPI 6 domain. The delta is material
(83K EGP from non-post records in December 2025 alone) and state filtering is consistent
with every other KPI in this module.

---

### Decision 5.2 — Cache TTL Option A: per-key parameter

**Status:** Approved (pre-session)  
**Context:** KPI 6 requires a 3600s (hourly) TTL, while KPIs 1, 2, 3, 5 use 60s. A single
module-level `_TTL_SECONDS = 60` global cannot serve both.  
**Decision:** Option A — extend `cache.set(key, value, ttl: int = 60)` with a `ttl` parameter
defaulting to `_TTL_SECONDS`. The internal store becomes a 3-tuple `(value, stored_at, ttl)`.
All existing callers are unaffected; KPI 6 calls `_cache.set(cache_key, result, ttl=3600)`.  
**Rejected alternatives:**  
- Option B (separate module): unnecessary complexity for a single extra parameter.  
- Option C (Redis): premature; Redis is a future migration path (Decision 1.1).

---

### Decision 5.3 — Always return exactly 6 month entries (zero-padding)

**Status:** Approved (pre-session, extension of Decision 3.4)  
**Context:** Odoo's `read_group` only returns groups with matching records. Months with no
posted payment headers are absent from the response.  
**Decision:** Zero-pad absent months to always return exactly 6 entries oldest-first. This
is the same pattern as KPI 5's project zero-padding (Decision 3.4), extended to KPI 6.
The frontend must render zero bars without special treatment.

---

### Decision 5.4 — Performance warning threshold: 5000ms

**Status:** Approved (pre-session)  
**Context:** KPI 6 queries `rs.account.payment.installment` (~4,437 line records, 431 header
records in the 6-month window). Expected RPC time is well under 5s.  
**Decision:** Log a `WARNING` if `rpc_duration_ms > 5000`. No hard timeout is applied.
The 3600s cache TTL means a slow first fetch is amortized across 1 hour of requests.

---

### Decision 5.5 — Arabic month labels: hardcoded dict

**Status:** Approved (pre-session)  
**Context:** The frontend label system requires Arabic month names. Alternatives considered:
(a) `babel` library, (b) `python-dateutil`, (c) hardcoded dict.  
**Decision:** Hardcoded `_ARABIC_MONTHS: dict[int, str]` in `kpi_service.py`.  
**Rationale:** Neither `babel` nor `python-dateutil` is in `requirements.txt`; adding a
dependency for 12 string literals is disproportionate. The mapping is stable (month names
do not change).  
**Mapping:**

```python
_ARABIC_MONTHS = {
    1: "يناير",   2: "فبراير",  3: "مارس",    4: "أبريل",
    5: "مايو",    6: "يونيو",   7: "يوليو",   8: "أغسطس",
    9: "سبتمبر", 10: "أكتوبر", 11: "نوفمبر", 12: "ديسمبر",
}
```

---

### Decision 5.6 — Architecture: HEADER model + user-entered date

**Status:** Approved (session — replaces Phase 2 §6.4 LINE model approach)  
**Context:** D0 Part 1 discovery confirmed that Odoo's ORM does NOT support `:month`
granularity `groupby` on related fields (`payment_id.date:month` raises `ValueError` in
`_read_group_get_annotated_groupby`). The Phase 2 §6.4 approach of querying the LINE model
grouped by `payment_id.date:month` is therefore blocked at the ORM layer.

D0 Part 2 discovery confirmed (Findings A and B):
- **Finding A:** `HEADER.date` is a user-entered field (distinct from both `create_date`
  and `write_date` in 10/10 sampled records). It represents the cash receipt date.
- **Finding B:** `HEADER.amount` == `SUM(LINE.amount)` in 10/10 sampled records (identity-equal,
  delta = ±0.00 for all). The HEADER model carries the correct value.
- **Finding D:** `rs.installment.write_date` is UNUSABLE as a trend axis — a bulk data migration
  in April 2026 wrote 26,110 records (≈ the entire database) on a single day, making
  `write_date:month` groupby return 2.97B EGP in April 2026 alone.

**Decision:**  
- **Model:** `rs.account.payment.installment` (HEADER, not LINE)  
- **Date axis:** `HEADER.date` (user-entered cash receipt date)  
- **Amount field:** `HEADER.amount` (proven = SUM(LINE.amount))  
- **Groupby:** `["date:month"]` (direct field — no ORM limitation)  
- **State filter:** `("state", "=", "post")` (Decision 5.1)  
- **Odoo groupby key format:** `"date:month": "December 2025"` (English full month name + year).
  Parsed via `_MONTH_NAME_TO_NUM` reverse-lookup of `calendar.month_name`.

---

### Decision 5.7 — Empty months during data entry period are expected

**Status:** Approved  
**Context:** D0 Part 1 found only December 2025 has data in the 6-month window
(2025-12-01 → 2026-05-17). January–May 2026 return zero records.  
**Root cause:** The operations team is entering historical payment data retroactively.
All 10 most-recent header records (Section A, D0 Part 2) were created in April–May 2026
but carry `HEADER.date` values in 2025, confirming the data is being back-entered.  
**Decision:** Zero months are truthful data, not bugs. The service zero-pads them (Decision 5.3).
The verification script (`verify_kpi6_live.py`) explicitly notes this with a `[WARN]` label —
not a `[FAIL]` — and the manual cross-check instructions state the same.  
**Implication for frontend:** Zero bars must be rendered normally. A "no data" placeholder
would mislead users into thinking the KPI is broken.

---

### Decision 5.8 — Board launch criteria for KPI 6

**Status:** Approved  
**Context:** Decision 1.3 defers all KPI Board exposure until data entry is complete.
KPI 6 has an additional data-density requirement.  
**Decision:** KPI 6 must NOT be shown to the Board until BOTH conditions hold:
1. La Verde confirms historical data entry is complete (Decision 1.3 baseline).
2. At least 5 of the trailing 6 calendar months have non-zero payment records.

Condition 1 alone is insufficient: if data entry completes but only December 2025 has
records, the trend chart shows a single bar — misleading.  
Condition 2 alone is insufficient: if months have data but entry is still in progress,
the chart numbers are incomplete.

---

### KPI 6 — Implementation summary

| Item | Value |
|---|---|
| Endpoint | `GET /api/v1/collections/kpi/collection-trend-6m` |
| Model | `rs.account.payment.installment` |
| Amount field | `amount` (= SUM of LINE amounts, Decision 5.6 Finding B) |
| Date axis | `date` (user-entered cash receipt date, Decision 5.6 Finding A) |
| Groupby | `date:month` |
| State filter | `state = 'post'` (Decision 5.1) |
| Cache TTL | 3600s (Decision 5.2) |
| Cache-Control | `private, max-age=3600` |
| Response months | Always 6, zero-padded, oldest-first (Decision 5.3) |
| Arabic labels | Hardcoded dict (Decision 5.5) |
| Verification | `scripts/verify_kpi6_live.py` — Checkpoint 2: manual cross-check Dec 2025 = 47,481,212 EGP / 430 records (state='post' + timezone-aware) |

**Caveats:**
- Board launch deferred per Decisions 1.3 and 5.8.
- The December 2025 baseline is 47,481,212.00 EGP / 430 records (state='post', Decision 5.1;
  timezone-aware UTC boundaries, Decision 5.9). Earlier D0 figures (431 records /
  47,465,098 EGP unfiltered; 429 records / 47,382,098 EGP state='post' naive) are superseded.
- Jan-May 2026 show zero until back-entry of 2026 payment records is complete.

---

### Decision 5.10 — Python-side regrouping for KPI 6 month buckets

- **Choice:** KPI 6's service uses `search_read` to fetch raw records within
  the 6-month window, then groups by Egypt local month in Python. It does NOT
  use Odoo's `read_group` with `date:month` groupby.
- **Why:** Odoo's `date:month` groupby key is computed from the raw stored UTC
  value. Records stored at Egypt-local-midnight (e.g., a record displayed as
  `01/12/2025 00:00:00` in the Odoo UI, stored as `2025-11-30 22:00:00 UTC`)
  are grouped by Odoo into the previous UTC month. This produces results that
  disagree with the Odoo UI by one record per month boundary — 99,114 EGP for
  the December 2025 boundary (record id=3869, confirmed by
  `scripts/inspect_kpi6_dec1_records.py`).
- **Trade-off accepted:** Slightly more data transfer per call (~430 rows for
  the current 6-month window, well within FastAPI + JSON limits). The 1-hour
  cache TTL means at most 24 RPCs per day per process. Performance is
  negligible compared to the identity-equal correctness requirement.
- **Discovery:** Checkpoint 2 manual cross-check against Odoo UI. The
  diagnostic script `scripts/inspect_kpi6_dec1_records.py` (kept for the
  audit trail) confirmed `search_count` returns 430 records under the
  UTC-shifted domain but `read_group` returns only 429 (1 record bucketed to
  the previous UTC month).
- **Future KPIs:** Any KPI requiring period-bucketing on a `datetime` field
  must follow the same pattern (`search_read` + Python-side local-time
  grouping). KPIs 1, 2, 3, 5 are unaffected because they do not bucket by
  period.
- **Supersedes Decision 5.9 partially:** The timezone-aware domain boundaries
  (Decision 5.9) remain correct and necessary — they bring record id=3869 INTO
  the search result set. Decision 5.10 adds the Python-side regrouping that
  places it into the correct Egypt-local month bucket.

---

### Decision 5.9 — Timezone-aware datetime filters for KPI 6

**Status:** Approved  
**Identified:** Checkpoint 2 manual cross-check, 2026-05-17  
**Root cause:** `rs.account.payment.installment.date` is a `datetime` field stored in UTC by
Odoo. The naive domain boundary `("date", ">=", "2025-12-01")` is interpreted by Odoo's ORM
as UTC midnight, which excludes any record whose Egypt-local timestamp on December 1 is stored
earlier than `2025-12-01 00:00:00 UTC`. The first ascending record had `date: 01/12/2025
00:00:00` in the Egypt-local Odoo UI — stored as `2025-11-30 22:00:00 UTC` — and was silently
excluded by the naive filter. Delta: 1 record / 99,114 EGP.

**Egypt timezone:**  
Egypt observes Africa/Cairo, which per tzdata 2025.2 is:
- **UTC+2 (EET):** approximately November through April
- **UTC+3 (EEST):** approximately May through October (DST re-introduced ~2023)

`ZoneInfo("Africa/Cairo")` handles DST transitions automatically with no hardcoded offset.

**Fix applied:** Added `_tz_period_bounds(period_start, period_end)` helper to
`backend/modules/collections/services/kpi_service.py`. The helper converts period start
(local midnight) and period end (local 23:59:59) to UTC datetime strings using
`ZoneInfo("Africa/Cairo")` before constructing the Odoo domain. `zoneinfo` is Python 3.9+
stdlib; `tzdata` package (already in `requirements.txt`) provides the IANA timezone database
on Windows.

**Impact audit:** Only KPI 6 is affected. KPIs 1, 2, 3, and 5 use `rs.installment`
(`date` field is a plain `date` type, not `datetime`) with relative comparisons
(e.g., `< today`). No timezone conversion is needed for date-type fields.

**Future standard:** Any new KPI or endpoint that filters on a `datetime` field in Odoo
**must** convert boundaries to UTC using `_tz_period_bounds()` or an equivalent pattern.
Do not use `.isoformat()` or naive date strings for `datetime` domain clauses.

**Baseline update:** December 2025 correct baseline after fix: **47,481,212.00 EGP / 430 records**.

---
