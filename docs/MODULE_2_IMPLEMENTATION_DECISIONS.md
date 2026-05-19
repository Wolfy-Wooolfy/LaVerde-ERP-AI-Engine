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

## Session 6 — 2026-05-17 — KPI 4 Backend (Collection Rate MTD & YTD)

**Scope:** D0 (discovery), D1 (service), D2 (endpoint), D3 (verification script),
D4 (unit tests), D5 (this decisions entry).

---

### Decision 6.1 — KPI 4 formula: HEADER amount ÷ rs.installment amount

**Status:** Approved (Checkpoint 0)

**Formula:**
```
Collection Rate = SUM(rs.account.payment.installment.amount WHERE date IN period AND state='post')
                ÷ SUM(rs.installment.amount              WHERE date IN period AND state='post')
                × 100
```

**Numerator model:** `rs.account.payment.installment` (HEADER), `amount` field,
`date` field (UTC `datetime`, user-entered cash receipt date — Decision 5.6 Finding A).

**Denominator model:** `rs.installment`, `amount` field (contractual face value),
`date` field (plain `date`, no timezone).

**Why `amount`, not `due_amount`, for the denominator:**
`rs.installment.amount` is the contractual face value — fixed at contract signing and
independent of payment history. `rs.installment.due_amount` is the remaining balance
(amount − paid_amount), which changes as payments are received. Using `due_amount`
would create a self-referential ratio: as the numerator's own success increases
`paid_amount`, it shrinks `due_amount` (the denominator), making the rate appear
artificially high. The formula becomes time-unstable — the same payment events
produce different rates depending on when you query. `amount` avoids this by
remaining constant for the life of the contract.

**Why two different models:**
The two events have different temporal semantics. Cash receipts are recorded on the
HEADER model with `date` as a UTC `datetime` (so timezone-aware UTC bounds are required
per Decision 5.9). Installment due dates are on `rs.installment` with `date` as a
plain `date` field (ISO string comparison is correct and sufficient).

**State filter:** `state='post'` applied to both sides — consistent with all other
KPIs in this module.

---

### Decision 6.2 — YTD period: calendar year (Jan 1 to today)

**Status:** Approved (Checkpoint 0, pending Finance team confirmation)

**Choice:** YTD = Jan 1 of the current calendar year to today (inclusive).
The `ytd_period_assumption: "calendar_year"` field in the API response makes this
explicit so consumers know the definition.

**Alternative considered:** Fiscal year start (La Verde's fiscal year). Deferred
because the fiscal year boundary was not confirmed by Finance at the time of
implementation. If Finance specifies a different fiscal year start, update the
`ytd_start` computation in `get_collection_rate_mtd_ytd()` and bump to Decision 6.2b.

**Future action:** Finance team to confirm whether collection rate should be reported
on a calendar-year or fiscal-year basis. No code change needed if calendar year is
confirmed.

---

### Decision 6.3 — Zero denominator → rate_percent: None

**Status:** Approved (Checkpoint 0)

**Choice:** When `SUM(rs.installment.amount)` for a period is zero (no installments
due), `rate_percent` is returned as `None` (JSON `null`). The frontend renders "—".

**Why not 0%:** A 0% rate implies installments were due and none were paid. None implies
the question "what fraction was collected?" is undefined for that period — no
installments were scheduled. These are different business situations and must be
distinguished clearly.

**Why not raise an exception:** Zero denominator is not an Odoo error. It is a valid
business state (e.g., on Jan 1 before any installments are due in the new year).
Raising would cause a 503 that misleads the caller into thinking the service is down.

**Unit test:** `test_kpi4_zero_denominator_returns_none_rate` (K4-02) and
`test_kpi4_both_denominators_zero_both_rates_none` (K4-07) cover this behavior.

---

### KPI 4 — Implementation summary

| Item | Value |
|---|---|
| Endpoint | `GET /api/v1/collections/kpi/collection-rate` |
| Numerator model | `rs.account.payment.installment` (HEADER) |
| Denominator model | `rs.installment` |
| Numerator amount field | `amount` (= SUM of LINE amounts, Decision 5.6 Finding B) |
| Denominator amount field | `amount` (contractual face value, NOT `due_amount` — Decision 6.1) |
| Numerator date filter | UTC datetime bounds via `_tz_period_bounds()` (Decision 5.9) |
| Denominator date filter | ISO date string bounds (plain `date` field, no timezone) |
| State filter | `state = 'post'` on both sides (Decision 5.1 extended) |
| MTD period | First day of current month → today |
| YTD period | Jan 1 (calendar year) → today (Decision 6.2) |
| Zero denominator | `rate_percent: None` (Decision 6.3) |
| Cache TTL | 60s (default — Decision 5.2) |
| Cache-Control | `private, max-age=60` |
| RPCs per call | 4 sequential `read_group` calls (Q1–Q4) |
| Performance warning | Log WARNING if total `rpc_duration_ms > 5000` |
| Verification | `scripts/verify_kpi4_live.py` — Checkpoint 2: identity-equal match on 4 manual Odoo checks |
| Discovery | `scripts/discover_kpi4_architecture.py` — Checkpoint 1: identity-equal match confirmed 2026-05-17 |
| Unit tests | K4-01 through K4-10 + 1 extra (mid-sequence failure) = 11 tests, all passing |

**Operational note (Decision 5.7 analog):**
As of 2026-05-17, both MTD and YTD rates compute as 0.00% because
`rs.account.payment.installment` has no posted records in 2026 — payments are being
back-entered retroactively. When the operations team completes back-entry,
rates will populate automatically without any code change.

---

### Verification Result — Session 6 KPI 4 Close

**Date:** 2026-05-17
**Checkpoint 1 (D0 discovery — manual Odoo cross-check):**

| Check | Backend | Odoo UI | Delta |
|---|---|---|---|
| MTD Numerator | 0.00 EGP / 0 records | 0.00 EGP / 0 records | **0.00 / 0** |
| MTD Denominator | 43,653,133.00 EGP / 263 records | 43,653,133.00 EGP / 263 records | **0.00 / 0** |
| YTD Numerator | 0.00 EGP / 0 records | 0.00 EGP / 0 records | **0.00 / 0** |
| YTD Denominator | 302,882,977.00 EGP / 1,861 records | 302,882,977.00 EGP / 1,861 records | **0.00 / 0** |

**Conclusion:** The two-model architecture (HEADER numerator + rs.installment
denominator), with UTC-aware datetime bounds on the numerator and plain ISO date
bounds on the denominator, reproduces Odoo's native data identity-equal at the
EGP level on 2026-05-17. KPI 4 backend is architecturally validated.

**Checkpoint 2 (D3 verify script — live endpoint):**
_Pending. Run `python scripts/verify_kpi4_live.py` against the running backend
and paste output for sign-off._

**Caveats:**
- Both rates are 0.00% as of 2026-05-17 (zero numerators — data entry phase).
  This is correct business behavior, not a bug (Decision 5.7 analog).
- Board launch deferred per Decision 1.3 until historical data entry is complete.
- Denominator values will grow daily as new installments are posted. The D0
  baselines (43,653,133 MTD / 302,882,977 YTD) are snapshots, not targets.
- KPI 5b (Collection Rate per project) is deferred to Session 7 (out of scope
  for Session 6).

---

### Decision 6.4 — Uvicorn --reload stale bytecode caveat

**Identified:** Session 6 Checkpoint 2, 2026-05-17

During Session 6 Checkpoint 2, the KPI 4 endpoint returned HTTP 404
despite being correctly defined in `collections.py` and committed. Root cause:
uvicorn's `--reload` mode cached the old compiled `.pyc` of `collections.py`
before the new endpoint's changes were applied, and the file-watcher failed to
trigger a recompile. The Python 3 `__pycache__` directory retained the stale
bytecode. The router inclusion in `router.py` and the endpoint definition itself
were both correct — the only fault was stale bytecode on the running process.

**Resolution:** Full process termination + `__pycache__` purge + clean uvicorn
restart (without `--reload`) resolved the issue immediately. No code change
was required.

**Standard going forward:** When running `verify_kpi*_live.py` scripts, restart
the server cleanly before verification:

1. Stop all python processes:
   `Get-Process -Name python -ErrorAction SilentlyContinue | Stop-Process -Force`
2. Purge all `__pycache__` directories:
   `Get-ChildItem -Path . -Filter __pycache__ -Recurse -Directory | Remove-Item -Recurse -Force`
3. Start uvicorn **without** `--reload`:
   `C:\Python310\python.exe -m uvicorn backend.main:app`
4. Re-run the verify script.

The `--reload` flag is acceptable during active development, but must be disabled
(with a clean restart) before any identity-equal verification gate.

---

## Session 7 — 2026-05-17 — KPI 5b: Collection Rate per Project

### Decision 7.1 — Branch A: project_id is a direct field on HEADER

**Open question resolved by D0 (`scripts/discover_kpi5b_architecture.py`):**
`rs.account.payment.installment` exposes a `project_id` many2one field directly.
The indirect join via `rs.account.payment.installment.line → rs.installment.project_id`
(Branch B) is not needed.

- **Chosen architecture (Branch A):** `get_collection_rate_by_project()` uses 4
  `read_group` RPCs on the same two models as KPI 4, adding `groupby=["project_id"]`
  to each query. This keeps the RPC budget identical to KPI 4 (4 RPCs, same models).
- **Branch B (discarded):** Would have required paginated pre-fetch of installment IDs
  per project + LINE model queries — more RPCs and more complexity. No reason to use
  it given Branch A is available.
- **Architectural assumption:** La Verde payment HEADERs are single-project. D0
  cross-check confirmed SUM(per-project) == KPI 4 global with zero delta. If a future
  HEADER spans multiple projects (multi-project bulk payment), KPI 5b per-project sums
  will diverge from KPI 4 by the multi-project amount. This will surface in the
  cross-KPI consistency check (Decision 7.3) rather than silently corrupting data.

### Decision 7.2 — Internal consistency: totals computed from parts only

`get_collection_rate_by_project()` computes `total_numerator_egp` and
`total_denominator_egp` as `sum(p["numerator_egp"] for p in projects)` and
`sum(p["denominator_egp"] for p in projects)` respectively. The total is never
fetched via a separate 5th RPC — it is derived from the same 4 read_group rows.

- **Rationale:** An extra RPC to compute the global total would cost latency and
  add a failure mode for no correctness benefit. The per-project rows are the
  authoritative data; the total is a derived convenience field.
- **Consequence:** `total_*` fields are always internally consistent with the
  per-project list by construction (no assertion needed). The cross-KPI check
  against KPI 4 standalone is the correctness gate (Decision 7.3).
- **Missing projects** (zero-padding): if Odoo returns fewer than 3 projects for a
  period (e.g., La Puerta has no due installments in MTD), the missing project is
  zero-padded and logged at INFO level. This is consistent with KPI 5 (Decision 3.4).

### Decision 7.3 — Cross-KPI consistency check lives in the verify script

`scripts/verify_kpi5b_live.py` Step 6 asserts:
```
abs(KPI5b.total_numerator_egp - KPI4.numerator_egp) < 0.01 EGP
abs(KPI5b.total_denominator_egp - KPI4.denominator_egp) < 0.01 EGP
```
for both MTD and YTD periods. This check is performed by calling
`GET /api/v1/collections/kpi/collection-rate` as a second HTTP request in the
verify script.

- **Rationale:** The cross-KPI check requires calling a second endpoint. In a
  production service this would be wasteful (extra RPC + HTTP round-trip every 60s).
  The verify script runs manually before sign-off, making the extra call cost
  acceptable there.
- **D0 confirmed baseline (2026-05-17):** Zero delta on both periods (no null-project
  installments in any period). This baseline is preserved in the verify script as an
  assertion gate.

### Decision 7.4 — Multi-project payment hypothesis resolved

D0 sanity check (3 RPCs: 10 HEADERs → LINEs → installment project lookup)
confirmed that all sampled HEADER records link to installments from a single project.
No multi-project bulk payment was detected.

- **Consequence for D1:** No special handling needed in the service. Branch A
  `groupby=["project_id"]` on HEADER produces unambiguous per-project amounts.
- **Future guard:** If a HEADER ever links installments across multiple projects,
  Odoo's `read_group` will place that HEADER's `amount` in whichever `project_id`
  is stored on the HEADER record — the LINE-level project distribution is not
  consulted. This is acceptable for the MVP, where multi-project payments do not
  exist. Document as a known limitation; revisit if La Verde introduces cross-project
  payment batches.
- **No service-level RPC guard added** (per Decision 7.2 rationale): the verify script
  cross-KPI check is the runtime guard. A service-level guard would require a 5th RPC
  on every cache miss, which is not justified for an edge case that has never been
  observed.

### KPI 5b Implementation Summary

| Aspect | Detail |
|---|---|
| Endpoint | `GET /api/v1/collections/kpi/collection-rate-by-project` |
| Service function | `get_collection_rate_by_project()` in `kpi_service.py` |
| Architecture | Branch A — direct `project_id` on HEADER (Decision 7.1) |
| RPCs per call | 4 sequential `read_group` (same budget as KPI 4) |
| Zero denominator | `rate_percent: None` per project; `total_rate_percent: None` if all zero |
| Zero-padding | Always returns 3 projects (Decision 3.4 analog) |
| Cache key | `kpi:collection_rate_by_project`, TTL 60s |
| Cross-KPI check | KPI 5b totals == KPI 4 standalone, delta < 0.01 EGP |
| Unit tests | K5B-01 through K5B-10 + 1 extra = 13 tests, all passing |
| Discovery | `scripts/discover_kpi5b_architecture.py` — Checkpoint 1 confirmed 2026-05-17 |
| Verification | `scripts/verify_kpi5b_live.py` — Checkpoint 2: pending live run |

---

### Verification Result — Session 7 KPI 5b Close

**Date:** 2026-05-17
**Checkpoint 1 (D0 discovery — manual Odoo cross-check):**

| Project | Backend | Odoo UI | Delta |
|---|---|---|---|
| New Capital (id=1) | 162,112,391.00 EGP / 1,458 records | 162,112,391.00 / 1,458 | 0.00 / 0 |
| Cassette (id=2) | 138,966,586.00 EGP / 391 records | 138,966,586.00 / 391 | 0.00 / 0 |
| La puerta (id=3) | 1,804,000.00 EGP / 12 records | 1,804,000.00 / 12 | 0.00 / 0 |
| **TOTAL** | **302,882,977.00 EGP / 1,861 records** | **302,882,977.00 / 1,861** | **0.00 / 0** |

Branch A confirmed. Null-project records in MTD/YTD periods = 0. Cross-check delta = 0.

**Checkpoint 2 (D3 verify script — live endpoint):**
_Pending. Run `python scripts/verify_kpi5b_live.py` against the running backend
(after Decision 6.4 clean restart) and paste output for final sign-off._

**Caveats:**
- Both MTD and YTD numerators are 0.00 EGP as of 2026-05-17 (zero payment headers
  in 2026 — data entry phase, same as KPI 4).
- Denominator values will grow daily as new installments are posted.
- Board launch deferred per Decision 1.3 until historical data entry is complete.

---

## Session 9 — 2026-05-19 — KPI 7 Backend (Expected Collections Forecast)

**Scope:** D0 (Phase 0 + Phase 0.5 discovery, completed in prior sessions),
D1 (service function + schemas), D2 (endpoint), D3 (verification script),
D4 (unit tests), D5 (this decisions entry).

---

### Decision 9.1 — Cheques formula: Alternative B (read_group net, null count)

**Status:** Approved (Phase 0 discovery finding U1)

**Approach:**
```python
cheques_in_pipeline = max(SUM(paid_amount) - SUM(x_studio_actual_paid_amount), 0.0)
```

via a single `read_group` RPC per bucket (fields `["paid_amount", "x_studio_actual_paid_amount"]`).

**Why Alternative B, not Alternative A (Python-side filter + search_read):**
Alternative A would allow exact `cheques_record_count` (per-installment count of
records where `paid_amount > x_studio_actual_paid_amount`), but requires fetching
full record sets and Python-side filtering. This does not scale for the
`this_year` bucket (1,934+ records). Alternative B uses one extra `read_group`
RPC per bucket, matching KPI 3's portfolio-wide formula (Decision 4.5).

**Consequence:** `cheques_record_count` is returned as `null` in all KPI 7
bucket responses. `cheques_drill_down_domain` is also `null` (field-to-field
Odoo domain comparison is broken for Float fields — raises `ValueError`
as confirmed in Phase 0 discovery D0.2/U1).

**PATH C (Phase 0.5):** The backend keeps `cheques_in_pipeline` in the response
(Alternative B formula, value ≈ 0 for near-term buckets). The Stage 4 frontend
suppresses the amber annotation when `cheques_in_pipeline == 0` (per spec §4.4).
Only `this_year` bucket may show non-zero cheques (643,000 EGP observed in Phase 0).

---

### Decision 9.2 — Domain date format: plain ISO strings, no UTC conversion

**Status:** Approved (Phase 0 discovery finding D0.3)

`rs.installment.date` is a plain `date` field (type confirmed via `fields_get`).
All domain clauses use plain `YYYY-MM-DD` strings:
```python
("date", ">=", "2026-05-19")   # today_cairo ISO
("date", "<=", "2026-05-31")   # bucket_end ISO
```

**Why:** UTC conversion (Decision 5.9 pattern) applies only to `datetime` fields.
Applying UTC offsets to a `date` field would shift the boundary by 0 or 1 day
depending on DST, producing silently wrong results. `ZoneInfo("Africa/Cairo")`
is used exclusively to compute "today" — not to convert domain values.

**Contrast with KPI 6:** KPI 6 (Decision 5.9) uses UTC-shifted datetime strings
because `rs.account.payment.installment.date` is a `datetime` field (UTC-stored).

---

### Decision 9.3 — Cache key uses Cairo-local date, not UTC date

**Status:** Approved

Cache key format: `kpi:expected_forecast:YYYY-MM-DD` where the date is
`datetime.now(ZoneInfo("Africa/Cairo")).date().isoformat()`.

**Why Cairo, not UTC:** The cache invalidates at Cairo midnight (the natural
day boundary for La Verde operations). Using UTC midnight would produce a stale
cache during the window 22:00–24:00 UTC (Egypt winter, UTC+2) or 21:00–24:00
UTC (Egypt summer, UTC+3). This is the same rationale as for other daily-keyed
KPIs — consistency with the Cairo business day.

**Contrast with KPI 2 / KPI 1:** Those KPIs use `_cache.make_key()` which
internally calls `today_str()` (UTC date). KPI 7 constructs its cache key
manually to use the Cairo date. A future refactor may standardize this
(Decision 3.5 — deferred).

---

### Decision 9.4 — RPC budget: 8 per uncached call; TTL 60 seconds

**Status:** Approved

8 RPCs = 2 `read_group` calls per bucket × 4 buckets:
- RPC 1: fields `["amount", "due_amount"]` → bucket total + record count
- RPC 2: fields `["paid_amount", "x_studio_actual_paid_amount"]` → cheques net

TTL is 60 seconds (consistent with KPI 2, KPI 1, KPI 3, KPI 4, KPI 5b).
KPI 6 uses 3600s (hourly trend data); KPI 7 uses 60s because forward-looking
installment data can change intra-session (new postings, partial payments).

---

### Decision 9.5 — KPI 7 endpoint uses response_model= (PATH Y)

**Status:** Approved (Commit 2 implementation decision)

`GET /api/v1/collections/kpi/expected-forecast` is decorated with:
```python
response_model=ExpectedCollectionsForecastResponse
```

This makes KPI 7 the first Collections endpoint to use `response_model=`,
joining the wider project convention (5 other modules, 8 endpoints already use it —
confirmed via grep in Commit 2 pre-approval review).

**Implementation pattern (PATH P1):** The endpoint returns `dict` on the success
path (so FastAPI's `response_model=` validates and serializes the response via
Pydantic) and `JSONResponse` on error paths (preserving the
`{"error": {"code": ..., "message": ...}}` shape expected by `api.js`).
Response headers (`Cache-Control`, `X-Cache-Status`) are injected via the
`response: Response` parameter rather than via `JSONResponse(..., headers=...)`.

**Why dict return for success:** Returning `JSONResponse` directly bypasses
`response_model=` validation at runtime, making the decorator documentation-only.
Returning `dict` causes FastAPI to run the full Pydantic validation + serialization
pipeline, which is the intent of Decision 9.5.

---

### Decision 9.6 — Tech debt: 6 existing collections endpoints lack response_model=

**Status:** Noted as tech debt; deferred to a future cleanup session

The 6 existing collections endpoints (`/kpi/late-uncollected`, `/kpi/total-portfolio-value`,
`/kpi/pending-check-exposure`, `/kpi/collection-trend-6m`, `/kpi/collection-rate`,
`/kpi/collection-rate-by-project`) all return `JSONResponse(content=data)` with no
`response_model=` decorator. As a result, their response shapes are only implicitly
documented (via unit tests and the service function's return dict).

KPI 7 sets the correct pattern. A future cleanup session should:
1. Define Pydantic models for each of the 6 existing KPI responses in `schemas.py`
2. Refactor each endpoint to use PATH P1 (dict return on success, JSONResponse on errors)
3. Add `test_kpiN_response_model_validates_success_shape` tests for each

This is explicitly deferred — no scope expansion in the current session (Constraint C9).

---

### Decision 9.8 — Dual-return endpoint pattern for KPI 7

**Status:** Implemented in Commit 2 (`7a5be0e`)

**Choice:** The KPI 7 endpoint returns `dict` on the success path (validated by
`response_model=ExpectedCollectionsForecastResponse`) and `JSONResponse` on error
paths (preserving the `{"error": {"code": ..., "message": ...}}` shape that the
existing 6 endpoints use and that the frontend `api.js` reads via
`data?.error?.message`).

**Rationale:** `response_model=` validation only runs when the endpoint returns
`dict` or a Pydantic model — it is skipped when the endpoint returns a `Response`
object directly (FastAPI behavior). To get both runtime schema enforcement AND
frontend-compatible error responses, the dual-return pattern is required.

The pattern:
```python
async def expected_collections_forecast(
    request: Request,
    response: Response,         # injected for header injection on success path
) -> dict | JSONResponse:
    try:
        data = await get_expected_collections_forecast()
    except OdooQueryError:
        return JSONResponse(status_code=503, content={"error": {...}})
    except Exception:
        return JSONResponse(status_code=500, content={"error": {...}})
    response.headers["Cache-Control"] = "private, max-age=60"
    response.headers["X-Cache-Status"] = str(data.get("cache_status", "fresh"))
    return data   # ← response_model= validates + serializes this path
```

**Alternative considered:** `HTTPException`-based error handling with FastAPI's
default `{"detail": ...}` shape. Rejected because `api.js` reads
`data?.error?.message` — switching to `.detail` would require a frontend change
out of scope for Stage 1.

**Verification:** `test_kpi7_response_model_validates_success_shape` in
`test_routes.py` confirms `response_model=` is active on the success path
(`ExpectedCollectionsForecastResponse(**r.json())` must not raise).
`test_kpi7_odoo_unavailable_returns_503` confirms the error path preserves the
existing `{"error": {"code": ...}}` shape.

**Source:** PATH P1 decision reached during Commit 2 pre-approval review
(2026-05-19). Grep of `api.js` confirmed `data?.error?.message` pattern; grep
of existing test_routes.py confirmed `body["error"]["code"] == "odoo_unavailable"`
in all 5 existing 503 tests.

---

### Decision 9.7 — Parameter naming: `odoo_client` vs `client` inconsistency

**Status:** Noted; deferred to future standardization

`get_expected_collections_forecast(odoo_client: Optional[OdooClient] = None)` uses
`odoo_client` while all 6 existing KPI service functions use `client`. The naming
is cosmetic and does not affect behavior. A future refactor session should standardize
the parameter name across all 7 KPI service functions (recommended: `client` for
brevity, matching the majority pattern).

---

### KPI 7 Implementation Summary

| Aspect | Detail |
|---|---|
| Endpoint | `GET /api/v1/collections/kpi/expected-forecast` |
| Service function | `get_expected_collections_forecast()` in `kpi_service.py` |
| Pydantic schemas | `ForecastBucket`, `ExpectedCollectionsForecastResponse` in `schemas.py` |
| Buckets | 4 nested forward-looking calendar buckets: this_month ⊆ this_quarter ⊆ this_half ⊆ this_year |
| Scoping | `state='post'`, `payment_state IN ['unpaid','partial']` (KD-1 canonical) |
| Domain date format | Plain ISO strings — no UTC conversion (D0.3: date field, not datetime) |
| Cheques formula | Alternative B: `max(SUM(paid_amount) - SUM(x_studio_actual_paid_amount), 0)` |
| `cheques_record_count` | `null` (Alternative B limitation) |
| `cheques_drill_down_domain` | `null` (field-to-field comparison broken for Float fields — D0.2/U1) |
| RPCs per call | 8 `read_group` calls (2 per bucket × 4 buckets) |
| Cache key | `kpi:expected_forecast:{YYYY-MM-DD}` using Cairo-local date |
| Cache TTL | 60 seconds |
| `response_model=` | `ExpectedCollectionsForecastResponse` (PATH Y / PATH P1 — Decision 9.5) |
| Unit tests | 13 tests (K7-1 through K7-13), all passing |
| Endpoint tests | 6 tests (K7-8a through K7-8e), all passing |
| Verification | `scripts/verify_kpi7_live.py` — pending live run |
| Discovery | Phase 0: `scripts/discover_kpi7.py`; Phase 0.5: `scripts/discover_phase_0_5_ui_artifacts.py` |

---

### Verification Result — Session 9 KPI 7 Close

**Date:** 2026-05-19

**Checkpoint 1 (D0 / Phase 0 discovery cross-check — manual Odoo UI, 2026-05-18):**

| Bucket | Records | Amount EGP | Cheques EGP |
|---|---|---|---|
| `this_month` | 133 | 22,719,871.00 | 0.00 |
| `this_quarter` | 355 | 55,527,209.00 | 0.00 |
| `this_half` | 355 | 55,527,209.00 | 0.00 |
| `this_year` | 1,934 | 337,946,411.00 | 643,000.00 |

Q2/H1 nesting collapse confirmed (May 2026: quarter end = half end = 2026-06-30).

**Checkpoint 2 (D3 verify script — live endpoint):**
_Pending. Run `python scripts/verify_kpi7_live.py` against the running backend
(after Decision 6.4 clean restart) and paste output for final Stage 1 sign-off._

---

## Session 10 — 2026-05-19 — KPI 2 Cheques Extension

**Scope:** D1 (service + schema), D2 (endpoint + response_model= adoption),
D3 (unit tests: 4 service + 2 endpoint), D4 (verify script extension),
D5 (this decisions entry).
Checkpoint tag target: `checkpoint-C-stage2-kpi2-extended`

---

### Decision 10.1 — PATH C applied to KPI 2

**Status:** Approved (Khaled, 2026-05-19)

PATH C applied to KPI 2 cheques annotation: backend includes all 4 new cheques
fields; Stage 3 frontend will suppress the amber annotation on the Risk Card.

Rationale: 1,929,000 EGP / 0.49% of the 326M EGP late portfolio = visual noise.
The EGP signal is 5.2× below the PATH C threshold (5M EGP). The 9.02% count
signal is in the gray zone but the EGP signal dominates for Board decision-making.

Cross-reference: `docs/KPI2_CHEQUES_DISCOVERY_FINDINGS.md` — mini-discovery
evidence (Section 4 parity check, PATH MIXED recommendation).

This overrides the original REFACTOR_SPEC v1.0 §7.6 which showed the amber
annotation on the KPI 2 Risk Card. REFACTOR_SPEC will be updated in Stage 3.

---

### Decision 10.2 — Single read_group RPC extended; no additional RPC

**Status:** Approved

The existing `get_late_uncollected()` read_group fields list was extended from:
```python
["due_amount"]
```
to:
```python
["due_amount", "amount", "paid_amount", "x_studio_actual_paid_amount"]
```

3 additional fields added to the SAME call. Total RPCs: 1 (unchanged, per C3
constraint). Proven safe by mini-discovery Section 4 Check A: combined read_group
returns identical sums to standalone calls (delta = 0.00 EGP).

---

### Decision 10.3 — Legacy `domain` field preserved alongside `drill_down_domain`

**Status:** Approved

Both fields carry the same Candidate C value (3-clause domain). The duplication
is intentional for backward compatibility with:
- `verify_kpi2_live.py` Step 6 domain shape assertions (existing consumers)
- Any external consumers of the `/kpi/late-uncollected` response
- Frontend `api.js` code that may reference `data.domain` by name

`drill_down_domain` is the canonical field for Stage 5 drill-down endpoints.
`domain` will be deprecated (but not removed) in a future cleanup session.

The service applies `list(domain)` to both fields (defensive copy — prevents
shared-mutation cache corruption if downstream code ever mutates a returned list).

---

### Decision 10.4 — KPI 2 endpoint adopts `response_model=` and PATH P1 dual-return

**Status:** Implemented (Commit 2, `ad7b5a3`)

`GET /api/v1/collections/kpi/late-uncollected` is now decorated with:
```python
response_model=LateUncollectedResponse
```

KPI 2 is the second Collections endpoint to adopt this convention (KPI 7 was
first, per Decision 9.5). The endpoint returns `dict` on success (so FastAPI
validates via `response_model=`) and `JSONResponse` on error paths (preserving
the `{"error": {"code": ...}}` shape expected by `api.js`).

Stages 3–6 will retrofit the remaining 5 endpoints per Decision 9.6 tech-debt plan.

---

### Decision 10.5 — Future revisit trigger for KPI 2 PATH decision

**Status:** Documented

Re-open the PATH decision for KPI 2 cheques annotation if EITHER condition holds:
- `cheques_in_pipeline` crosses **10M EGP** (currently 1.929M EGP — 5.2× headroom)
- `late_with_checks_count` crosses **250** (12.5% of current 2,006 late universe)

To check: re-run `scripts/discover_kpi2_cheques.py` and compare to these thresholds.
No code change required until a threshold is crossed.

---

### Decision 10.6 — Out-of-scope insight: 2027+ cheques observed

**Status:** Documented (no code change)

Mini-discovery Image 2 (visible during Section 6 Odoo UI inspection) revealed
~2.54M EGP in cheques attached to installments with `date > 2026-12-31` (i.e.,
2027 and beyond). These are outside KPI 7's calendar-year scope and would not
be counted in any current bucket.

Not actionable in the current MVP. Documented as a future enhancement candidate
if the Board requests a multi-year forecast beyond the `this_year` bucket.
No code change required.

---

### Decision 10.7 — KPI 2 cache key unchanged

**Status:** Approved

The KPI 2 cache key continues to use `_cache.make_key(_CACHE_KEY_PREFIX)` which
is date-stamped at UTC midnight. The 4 new fields are stored in the same cached
payload — no separate cache key needed. Cache TTL unchanged at 60 seconds.

This differs from KPI 7's Cairo-local cache key (Decision 9.3) because KPI 2's
`date < today` boundary uses UTC date from `_cache.today_str()`. Changing this
would require also changing the domain date source — deferred to a future
standardization pass (Decision 3.5).

---

### Decision 10.8 — `data_quality_warning` is REQUIRED (not optional) in `LateUncollectedResponse`

**Status:** Approved (Khaled schema correction, 2026-05-19)

`LateUncollectedResponse.data_quality_warning` is typed `str | None` (not
`str | None = None`), matching `ExpectedCollectionsForecastResponse` exactly.
The service function always includes this key with value `None` in the normal
case and `"negative_cheques"` on anomaly.

Rationale: schema consistency across KPI endpoints simplifies Stage 3 frontend
rendering (both KPIs pass through the same rendering code path) and future AI
Chat response parsing (Pillar 2).

---

### KPI 2 Stage 2 Implementation Summary

| Aspect | Detail |
|---|---|
| Endpoint | `GET /api/v1/collections/kpi/late-uncollected` |
| Service function | `get_late_uncollected()` in `kpi_service.py` |
| Pydantic schema | `LateUncollectedResponse` in `schemas.py` |
| New fields | `cheques_in_pipeline`, `cheques_record_count`, `drill_down_domain`, `cheques_drill_down_domain` |
| Required field | `data_quality_warning: str \| None` (always present, Decision 10.8) |
| Legacy field | `domain` preserved (= `drill_down_domain` value, Decision 10.3) |
| RPCs | 1 (unchanged — single read_group extended, Decision 10.2) |
| PATH applied | C — backend includes fields, Stage 3 frontend suppresses annotation (Decision 10.1) |
| Cheques value (2026-05-19) | 1,929,000.00 EGP / 0.49% of late portfolio |
| Commits | 3 atomic commits per C7 |
| Unit tests added | 4 service + 2 endpoint = 6 new; 1 existing extended (12-key set) |
| `response_model=` | `LateUncollectedResponse` (PATH Y / PATH P1 — Decision 10.4) |

---

## Session 11 — 2026-05-17 to 2026-05-19 — Stage 3 Frontend Restructure + Closure Review

**Scope:** Stage 3 frontend restructure (4-section dashboard layout,
KPI 3 DOM removal, state-as-named-object refactor, KPI 7 fetch
addition) — Commits 1-4 + Commit 4.5 smoke test fix. Closure review
on 2026-05-19 added 5 forward-looking decisions (11.13-11.17).

**Format note:** Decisions 11.1-11.12 use a hybrid format
(title + spec reference + code reference + capture note) because
they were taken and applied during Stage 3 implementation but their
detailed rationale was not captured in this log in the moment. The
spec section and code path together encode the decision intent; no
post-hoc rationale prose is invented. Decisions 11.13-11.17 use the
standard full-rationale format because they originate in the
current closure review.

Checkpoint tag target:
`checkpoint-D-stage3-frontend-restructure-complete`

---

### Decision 11.1 — 4 sections with typed h2 headers (REFACTOR_SPEC §7.2 styling)

- **Status:** Implemented in Commit 3 (`9f5c496`)
- **Spec Reference:** `MODULE_2_REFACTOR_SPEC.md` §7.2
- **Code Reference:** `frontend/templates/collections/dashboard.html`
  — 4 `<section>` blocks with section-header h2 elements
- **Capture Note:** Decision taken and applied during the Stage 3
  implementation session (Session 11 in-flight). Detailed rationale
  was not captured in this log in the moment; preserved here as
  title + verifiable references for audit trail continuity. The
  spec and code together encode the full decision intent and
  implementation.

### Decision 11.2 — KPI 3 removal: DOM-only (endpoint preserved per §6.2)

- **Status:** Implemented in Commit 3 (`9f5c496`)
- **Spec Reference:** `MODULE_2_REFACTOR_SPEC.md` §6.2
- **Code Reference:** `frontend/templates/collections/dashboard.html`
  (no `col-kpi3-*` elements present) and
  `backend/api/v1/endpoints/collections.py` (KPI 3 endpoint
  route preserved)
- **Capture Note:** Decision taken and applied during the Stage 3
  implementation session (Session 11 in-flight). Detailed rationale
  was not captured in this log in the moment; preserved here as
  title + verifiable references for audit trail continuity. The
  spec and code together encode the full decision intent and
  implementation.

### Decision 11.3 — data_quality_warning: silent console.warn after state build

- **Status:** Implemented in Commit 4 (`b9a1e84`)
- **Spec Reference:** Procedural — no dedicated spec section. `data_quality_warning` field is defined in the response schemas for KPI 2 (per Decision 10.8) and KPI 7 (per Session 9 schema work). The frontend's choice to log silently via `console.warn` instead of surfacing in UI is a Stage 3 implementation detail.
- **Code Reference:** `frontend/static/js/collections.js` — `fetchAllKPIs()`, the `console.warn` block immediately after `_lastFetchData = state` assignment, guards on `state.late.data_quality_warning` and `state.forecast.data_quality_warning`
- **Capture Note:** Decision taken and applied during the Stage 3
  implementation session (Session 11 in-flight). Detailed rationale
  was not captured in this log in the moment; preserved here as
  title + verifiable references for audit trail continuity. The
  spec and code together encode the full decision intent and
  implementation.

### Decision 11.4 — border-primary-500 for forecast cards (no info palette)

- **Status:** Implemented in Commit 2 (`6a43992`)
- **Spec Reference:** `MODULE_2_REFACTOR_SPEC.md` §7.4 (Color Palette per Section) — spec calls for `border-info-500` for Section 3 Expected Collections
- **Code Reference:** `frontend/templates/components/_forecast_card.html` (uses `border-primary-500`) and `frontend/tailwind.config.js` (no `info` palette key defined; spec's `border-info-500` mapped to the existing primary palette as the closest blue available)
- **Capture Note:** Decision taken and applied during the Stage 3
  implementation session (Session 11 in-flight). Detailed rationale
  was not captured in this log in the moment; preserved here as
  title + verifiable references for audit trail continuity. The
  spec and code together encode the full decision intent and
  implementation.

### Decision 11.5 — kpi-card CSS class as the actual class name

- **Status:** Implemented in Commit 2 (`6a43992`)
- **Spec Reference:** `MODULE_2_REFACTOR_SPEC.md` §7.5 (Card Templates) — spec describes card template structure without naming a CSS class; `kpi-card` is the implementation's actual class name
- **Code Reference:** `frontend/src/css/input.css` line 53 (`.kpi-card` utility class definition; compiled into `frontend/static/css/app.css`) and the 3 new macros (`_portfolio_card.html`, `_risk_card.html`, `_forecast_card.html`) consume the `kpi-card` class
- **Capture Note:** Decision taken and applied during the Stage 3
  implementation session (Session 11 in-flight). Detailed rationale
  was not captured in this log in the moment; preserved here as
  title + verifiable references for audit trail continuity. The
  spec and code together encode the full decision intent and
  implementation.

### Decision 11.6 — State clean break: explicit object literal, not dynamic loop

- **Status:** Implemented in Commit 4 (`b9a1e84`)
- **Spec Reference:** `MODULE_2_REFACTOR_SPEC.md` §6.3 (state refactor
  intent)
- **Code Reference:** `frontend/static/js/collections.js` —
  `fetchAllKPIs()` builds `state` as a named object literal
  (late, portfolio, perProject, trend, rate, rateByProject,
  forecast); zero `state[N]` array indexing in the file
- **Capture Note:** Decision taken and applied during the Stage 3
  implementation session (Session 11 in-flight). Detailed rationale
  was not captured in this log in the moment; preserved here as
  title + verifiable references for audit trail continuity. The
  spec and code together encode the full decision intent and
  implementation.

### Decision 11.7 — CSS rebuild required when new Tailwind classes are introduced

- **Status:** Implemented in Commit 3 (`9f5c496`)
- **Spec Reference:** (procedural — no spec section)
- **Code Reference:** `frontend/static/css/app.css` — rebuilt to
  include Tailwind classes added in Stage 3 (sm:grid-cols-2,
  lg:grid-cols-4, tracking-wider, sm:grid-cols-3)
- **Capture Note:** Decision taken and applied during the Stage 3
  implementation session (Session 11 in-flight). Detailed rationale
  was not captured in this log in the moment; preserved here as
  title + verifiable references for audit trail continuity. The
  spec and code together encode the full decision intent and
  implementation.

### Decision 11.8 — data-drilldown-target attributes on Stage 3 macro cards

- **Status:** Implemented in Commit 2 (`6a43992`)
- **Spec Reference:** `MODULE_2_REFACTOR_SPEC.md` §9 (drill-down
  architecture, Stages 5/6 prep)
- **Code Reference:**
  `frontend/templates/components/_portfolio_card.html`,
  `frontend/templates/components/_risk_card.html`,
  `frontend/templates/components/_forecast_card.html` —
  `data-drilldown-target` attribute on each card's root element
- **Capture Note:** Decision taken and applied during the Stage 3
  implementation session (Session 11 in-flight). Detailed rationale
  was not captured in this log in the moment; preserved here as
  title + verifiable references for audit trail continuity. The
  spec and code together encode the full decision intent and
  implementation.

### Decision 11.9 — Forecast bucket collapse (Q2=H1 in Apr-Jun, Q4=H2 in Oct-Dec) is expected

- **Status:** Implemented in Commit 4.5 (`2fb18ef`)
- **Spec Reference:** (emergent from KPI 7 backend semantics — calendar
  boundary coincidence; see Session 9 KPI 7 implementation summary
  for the Q2/H1 cross-check on 2026-05-19)
- **Code Reference:**
  `backend/modules/collections/services/kpi_service.py` —
  `get_expected_collections_forecast()` calendar boundary
  computation; this_quarter and this_half period_end values
  coincide in Apr-Jun and Oct-Dec by design
- **Capture Note:** Decision taken and applied during the Stage 3
  implementation session (Session 11 in-flight). Detailed rationale
  was not captured in this log in the moment; preserved here as
  title + verifiable references for audit trail continuity. The
  spec and code together encode the full decision intent and
  implementation.

### Decision 11.10 — Section 4 layout: Option C (rate full-width row 1, projects row 2)

- **Status:** Implemented in Commit 3 (`9f5c496`)
- **Spec Reference:** `MODULE_2_REFACTOR_SPEC.md` §7.1 (Section 4
  area + amendment)
- **Code Reference:**
  `frontend/templates/collections/dashboard.html` — Section 4
  structure: rate card outside any grid container, then
  `sm:grid-cols-3` projects, then full-width trend chart
- **Capture Note:** Decision taken and applied during the Stage 3
  implementation session (Session 11 in-flight). Detailed rationale
  was not captured in this log in the moment; preserved here as
  title + verifiable references for audit trail continuity. The
  spec and code together encode the full decision intent and
  implementation.

### Decision 11.11 — KPI 7 forecast cards suppress cheques_in_pipeline annotation (PATH C)

- **Status:** Implemented in Commit 2 (`6a43992`)
- **Spec Reference:** `MODULE_2_REFACTOR_SPEC.md` §10 Decision Log, R.11
- **Code Reference:**
  `frontend/templates/components/_forecast_card.html` (no cheques
  annotation rendered) and `frontend/static/js/collections.js` —
  `renderSection3()` does not consume `cheques_in_pipeline`
- **Capture Note:** Decision taken and applied during the Stage 3
  implementation session (Session 11 in-flight). Detailed rationale
  was not captured in this log in the moment; preserved here as
  title + verifiable references for audit trail continuity. The
  spec and code together encode the full decision intent and
  implementation.

### Decision 11.12 — KPI 2 risk card suppresses cheques_in_pipeline annotation (Decision 10.1 applied to UI)

- **Status:** Implemented in Commit 2 (`6a43992`)
- **Spec Reference:** Decision 10.1 (Session 10, applied at frontend
  layer) — this entry is the frontend application of the prior
  backend-side decision
- **Code Reference:**
  `frontend/templates/components/_risk_card.html` (no cheques
  annotation rendered) and `frontend/static/js/collections.js` —
  `renderSection2()` does not consume `cheques_in_pipeline`
- **Capture Note:** Decision taken and applied during the Stage 3
  implementation session (Session 11 in-flight). Detailed rationale
  was not captured in this log in the moment; preserved here as
  title + verifiable references for audit trail continuity. The
  spec and code together encode the full decision intent and
  implementation.

### Decision 11.13 — KPI 2 redefinition deferred to Stage 2.5

- **Status:** Approved 2026-05-19 (fully reverses Decision 10.1)
- **Trigger:** Stage 3 smoke test review revealed semantic
  ambiguity in the current KPI 2 formula. `paid_amount` includes
  postdated cheques received (1.929M EGP), which is "received but
  not yet cleared" — risk-material regardless of the 0.49%
  proportion that drove the original PATH C decision.
- **Change:** Replace formula
  `Late Uncollected = Amount - paid_amount` (currently 326.4M)
  with `Late Uncollected = Amount - actual_paid_amount` (target
  ~328.3M, exact value pending Pre-Implementation Discovery in
  Stage 2.5). Add annotation "منهم 1.9 مليون شيكات مستلمة لم
  تتحصل بعد" as a mathematically-correct subset of the new total.
- **Scope:** ~4 hours across backend service, ~12 unit test
  updates, `verify_kpi2_live.py` update, Odoo UI view creation
  (human-side), identity-equal re-verification, frontend
  annotation rendering. Full breakdown in
  `docs/STAGE_2_5_PLAN.md`.
- **Reversal of Decision 10.1:** Decision 10.1 (Session 10,
  2026-05-19) applied PATH C to KPI 2 based on EGP signal
  proportion (1.9M / 326.4M = 0.49%). This decision concludes
  proportion-based suppression was the wrong lens — the
  categorical distinction (cleared vs uncleared cheques) is
  risk-material regardless of proportion. Decision 10.1 entry
  stays in the log as historical record; this decision marks
  PATH C → PATH A reversal.
- **Identity-equal consequence:** Stage 2 V5 verified
  326,374,203.40 EGP. Stage 2.5 V5 will verify a new ground
  truth (~328.3M, exact value TBD). This is not a regression —
  it is the corrected ground truth per the redefined formula.
- **Risk gate:** Finding 8b from Module 2 Phase 2 Discovery
  established that `x_studio_actual_paid_amount` does not
  satisfy `Amount = actual_paid_amount + due_amount` on the
  full portfolio. The Stage 2.5 Pre-Implementation Discovery
  script must prove the mismatch is absent on the Late subset
  (state='post', payment unpaid/partial) before any code change.
  If the hypothesis fails, Decision 11.13 must be re-evaluated.

### Decision 11.14 — KPI 7 cheques_record_count deferred to Stage 5

- **Status:** Approved 2026-05-19
- **Observation:** Stage 3 forecast cards display `record_count`
  per bucket (e.g., "112 installments") but the ideal display
  would include a cheques sub-count (e.g., "112 installments —
  50 منهم شيكات"). The KPI 7 backend currently returns
  `cheques_record_count: null` (Alternative B limitation per
  Decision 9.X).
- **Decision:** Defer the backend extension that would compute
  per-bucket `cheques_record_count` to Stage 5 (Drill-Down
  Backend session), paired with the drill-down endpoint work.
  Rationale: the same per-installment query infrastructure
  needed for drill-down detail is what unlocks
  `cheques_record_count`. Bundling avoids duplicated query design.
- **Frontend consequence:** Stage 3 forecast cards display
  `record_count` only. Stage 5 backend + a follow-up frontend
  update will add the sub-count.

### Decision 11.15 — Per-card drill-down confirmed for Stages 5+6

- **Status:** Approved 2026-05-19 (re-confirms REFACTOR_SPEC §9)
- **Confirmation:** All clickable KPI elements (KPI 1 hero, KPI 2
  risk card, the 4 KPI 7 forecast cards, the 3 KPI 5 project
  cards, KPI 6 trend chart months) will get drill-down per
  REFACTOR_SPEC §8.6 (5 backend endpoints) and §9 Stages 5+6.
- **No scope change.** This entry exists to mark the
  confirmation as part of the Session 11 close so future
  sessions do not re-litigate the question.

### Decision 11.16 — KPI 4 visual emptiness is data-state, not regression

- **Status:** Approved 2026-05-19 (documentation only)
- **Observation:** Stage 3 smoke test showed Section 4 Rate card
  rendering empty with the "البيانات تحت الإدخال" fallback. KPI 4
  backend returns `rate_percent=0.0`, `numerator_egp=0.0`,
  `denominator_egp=50,380,117`. The `isRateUnavailable()` third
  clause fires correctly.
- **Root cause:** La Verde Collections team has not entered
  May 2026 paid amounts in Odoo yet. Numerator is legitimately
  zero because no collections have been recorded for the
  current period.
- **Self-correction trigger:** When the Collections team
  completes May 2026 data entry, `numerator_egp > 0` and the
  card will render the rate naturally. No code change required.
- **Verification:** Confirm in Stage 2.5 V5 or a later
  verification run that once `numerator_egp > 0`, the rate
  renders.

### Decision 11.17 — Smoke test lesson: include exact JSON response shape in Stage prompts

- **Status:** Approved 2026-05-19 (process improvement)
- **Lesson source:** Stage 3 Commit 4 introduced a shape
  mismatch — `renderSection3()` read `forecast[key]` but the
  KPI 7 response shape is `forecast.buckets[key]`. The browser
  smoke test caught the bug; unit tests did not because no
  unit test asserted the JS render function against the actual
  backend response shape.
- **Process change:** All future Stage prompts that involve
  frontend rendering of a backend KPI response MUST include
  the exact JSON response shape (keys at every nesting level)
  verbatim in the prompt's Context section. Do not assume
  Claude Code can derive the runtime shape from Pydantic
  schemas — schemas describe types, not nested object names.
- **Template addition:** Future Stage prompts for frontend
  work include a section titled "Backend Response Shape" with
  pasted JSON sample (1 example per KPI consumed) before any
  rendering work begins.
- **Applies to:** All Stage 4, 5, 6 prompts, and any
  subsequent module's frontend work.
