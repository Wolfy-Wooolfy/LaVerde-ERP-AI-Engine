# Inventory Data-Quality — DISCOVERY (read-only)

**Status:** discovery only — numbers captured, **nothing built**. Awaiting Khaled's
product decision on which checks to surface and how to present them.

**Goal:** quantify candidate data-completeness checks for a future, persistent
"Inventory Data Quality" view (analogous to the CRM "Missing Contacts"), portfolio-wide
across all three projects, so we can decide scope + presentation before building.

**Method discipline:** READ-ONLY. Only `search_read` / `search_count` / `read_group` /
`fields_get` were issued; `ALLOWED_METHODS` untouched; no writes to Odoo; no app code
changed; AI cost = $0.00. Every key count is **independently re-derived** (triple-agreement,
below) and agrees.

**Provenance**
- Repo `LaVerde-ERP-AI-Engine`, branch `main`, HEAD == origin/main == `1ebbe5a` (no ahead/behind).
- Live run: **2026-06-19** (Africa/Cairo), against the production Odoo via the shared
  read-only `OdooClient`.
- Reproduce:
  - Check B (hierarchy): `python scripts/audit_inventory_hierarchy.py`
  - Checks A + C + context + triple-agreement: `python scripts/probe_inventory_data_quality.py`
- Both scripts are intentionally left **untracked** — they ship with the build later.

**Portfolio (sanity vs Slice 1 — exact match):** 1,873 units —
New Capital (id 1) 1,401 · Cassette (id 2) 334 · La Puerta (id 3) 138.
Domain constants: `SOLD_STATES = {contracted, delivered}`, `AVAILABLE_STATES = {available}`.

---

## Headline

| Check | Definition | NC | Cassette | La Puerta | **Portfolio** |
|---|---|---:|---:|---:|---:|
| **A** — Sold unit without a contract | sold (`state ∈ SOLD_STATES`) with no `rs.contract` (`state != cancel`) on `unit_id` | 3 | 2 | 0 | **5** |
| **B** — Broken hierarchy chain | unit's denormalised project/phase/zone/building disagrees with the parent record | 8 | 0 | 0 | **8** |
| **C** — Sold unit with no list price | sold unit where `amount` is 0 / falsy | 0 | 0 | 0 | **0** |

Context (not a flag): **130 unpriced units** portfolio-wide — **129** are La Puerta
*available* units (early-stage, expected) + **1** NC *reserved* unit; Cassette has none.

---

## Check A — Sold units WITHOUT a contract

**Definition.** A unit is *sold* when `state ∈ {contracted, delivered}`. It is flagged if
**no** `rs.contract` with `state != 'cancel'` references it via `unit_id`. Contracts for all
1,405 sold units were fetched portfolio-wide in chunks of 200.

**Counts**

| | NC | Cassette | La Puerta | Portfolio |
|---|---:|---:|---:|---:|
| sold units | 1,166 | 234 | 5 | 1,405 |
| sold WITH a contract | 1,163 | 232 | 5 | 1,400 |
| **sold WITHOUT a contract** | **3** | **2** | **0** | **5** |

**Flagged units (all 5)** — code · project · state · list `amount` (EGP) · area (m²):

| unit id | code | project | state | amount | area |
|---:|---|---|---|---:|---:|
| 3637 | `AF135-7-404` | New Capital | contracted | 1,620,000.00 | 135.00 |
| 3936 | `AF135-17-404` | New Capital | contracted | 1,350,000.00 | 135.00 |
| 4235 | `BF170-6-302` | New Capital | contracted | 1,700,000.00 | 170.00 |
| 4800 | `A407-4` | Cassette | contracted | 35,151,045.00 | 425.00 |
| 4920 | `B346-133` | Cassette | contracted | 34,875,000.00 | 340.00 |

**Confirms the known 5** (ids 3637, 3936, 4235, 4800, 4920) exactly — and resolves the
open question: **La Puerta's 5 sold units all carry a contract** (0 flagged). These 5 are
the same units already split out as `no_contract_count` by Slice 2's value page; here they
are confirmed to be the *entire* portfolio population, not just NC+Cassette.

---

## Check B — Broken hierarchy chains

**Definition.** Every unit denormalises its full chain (`project_id`, `phase_id`, `zone_id`,
`building_id`). Reading each **parent record's own** upward link as the source of truth
(`rs.structure.phase.project_id`, `rs.structure.zone.phase_id`,
`rs.structure.building.zone_id`), we verify for every unit:
`phase→project == project_id`, `zone→phase == phase_id`, `building→zone == zone_id`.

This **authoritative** per-unit check (Part 1 of `audit_inventory_hierarchy.py`) and the
independent **spanning** inference (Part 2 — a node whose units carry >1 distinct parent)
**agree exactly**: 8 units, 3 offending nodes.

**Counts** — 8 units flagged, **all in New Capital** (Cassette 0, La Puerta 0).

| broken link | flagged units | offending node(s) |
|---|---:|---|
| `zone→phase` | 1 | zone 26 `Zone#1` (authoritative phase 4, but unit says phase 2) |
| `building→zone` | 7 | building 614 `Building#6` (×6), building 829 `Building#215` (×1) |
| `phase→project` | 0 | — |

**Flagged units (all 8)** — code · project · broken-link description:

| code | project | broken link |
|---|---|---|
| `AF155-3-702` | New Capital | zone 26 `Zone#1` belongs to phase 4 `Phase#1` but unit's `phase_id` is 2 |
| `BF175-6-201` | New Capital | building 614 `Building#6` belongs to zone 20 `Zone#2` but unit's `zone_id` is 24 |
| `BF175-6-301` | New Capital | building 614 `Building#6` belongs to zone 20 `Zone#2` but unit's `zone_id` is 24 |
| `BF175-6-401` | New Capital | building 614 `Building#6` belongs to zone 20 `Zone#2` but unit's `zone_id` is 24 |
| `BF175-6-501` | New Capital | building 614 `Building#6` belongs to zone 20 `Zone#2` but unit's `zone_id` is 24 |
| `BF175-6-601` | New Capital | building 614 `Building#6` belongs to zone 20 `Zone#2` but unit's `zone_id` is 24 |
| `BF175-6-701` | New Capital | building 614 `Building#6` belongs to zone 20 `Zone#2` but unit's `zone_id` is 24 |
| `CS75-1-4B` | New Capital | building 829 `Building#215` belongs to zone 26 `Zone#1` but unit's `zone_id` is 25 |

**Spanning corroboration (Part 2):** zone 26 spans phases (206 in phase 4, **1** in phase 2);
building 614 spans zones (28 in zone 20, **6** in zone 24); building 829 spans zones
(1 in zone 25, **1** in zone 26). Σ misplaced = 1 + 6 + 1 = **8** — identical to the
authoritative count.

These are the inconsistencies behind the 1-unit drill discrepancy noted in Slice 1b: a unit
whose denormalised `zone_id`/`phase_id` points outside its parent's own chain is counted in
one place by the drill (single-field scoping) and another by the full path.

---

## Check C — Sold units with no list price

**Definition.** Sold unit (`state ∈ {contracted, delivered}`) whose `amount` is 0 / falsy.

**Result: 0 portfolio-wide.** Every sold unit in all three projects carries a list price
(NC 0, Cassette 0, La Puerta 0). La Puerta's 5 sold units are among its 9 priced units —
consistent with the pricing discovery (only 9/138 La Puerta units carry `amount`).

---

## Context counts (informative — NOT a data-quality flag)

Totals & unpriced (`amount` 0/falsy) by project × bucket. Totals match Slice 1 exactly.

| project | bucket | total | unpriced |
|---|---|---:|---:|
| New Capital | available | 201 | 0 |
| New Capital | reserved | 34 | **1** |
| New Capital | contracted | 1,166 | 0 |
| **New Capital** | **ALL** | **1,401** | **1** |
| Cassette | available | 86 | 0 |
| Cassette | reserved | 14 | 0 |
| Cassette | contracted | 234 | 0 |
| **Cassette** | **ALL** | **334** | **0** |
| La Puerta | available | 132 | **129** |
| La Puerta | reserved | 1 | 0 |
| La Puerta | contracted | 5 | 0 |
| **La Puerta** | **ALL** | **138** | **129** |
| **PORTFOLIO** | **ALL** | **1,873** | **130** |

Of 130 unpriced units: **129** are La Puerta *available* (early-stage, expected — see caveat)
and **1** is a New Capital *reserved* unit. The latter is a genuine near-miss: NC is otherwise
100% priced, so a single unpriced *reserved* NC unit stands out (it is **not** caught by
Check C because it is not yet *sold*).

---

## Triple-agreement (independent re-derivation)

Every key count was independently recomputed via `search_count` / `read_group` and matches
the in-Python figure:

| check | python | independent | |
|---|---:|---:|:--|
| total units (portfolio) | 1,873 | 1,873 | OK |
| sold units (portfolio) | 1,405 | 1,405 | OK |
| sold units, no list price (Check C) | 0 | 0 | OK |
| sold units WITH a contract | 1,400 | 1,400 | OK |
| sold units WITHOUT a contract (Check A) | 5 | 5 | OK |
| total units — New Capital (id 1) | 1,401 | 1,401 | OK |
| total units — Cassette (id 2) | 334 | 334 | OK |
| total units — La Puerta (id 3) | 138 | 138 | OK |

---

## Data interpretation & caveats

- **La Puerta is EARLY-STAGE** (≈3.6% sold; only 9/138 units priced). Its 129 unpriced
  *available* units are **expected**, not data errors — La Puerta is excluded from every
  value figure (Slice 2). A data-quality view must **not** flag unpriced La Puerta inventory
  as a defect; doing so would drown the board in false positives.
- **Check A (5 units)** is small, concrete, and actionable — each flagged unit is a *sold*
  unit (real money committed) with **no contract record**, i.e. a genuine reconciliation gap
  between the structure model and `rs.contract`. High signal-to-noise. Already surfaced
  numerically by Slice 2; a dedicated checklist would make it *trackable*.
- **Check B (8 units, all NC)** is small and precise; the authoritative + spanning methods
  cross-validate it. It explains the known 1-unit drill discrepancy. Each item has an exact,
  human-readable fix instruction ("set unit's `zone_id` to 20"), so it is well-suited to a
  trackable checklist — but it is a *structural* fix in Odoo, a different audience/owner than
  the sales-side Check A.
- **Check C (0 units)** is currently clean. Worth keeping as a *standing* check (cheap, and it
  guards against future regressions) but there is nothing to surface today.
- The **1 unpriced NC reserved unit** is the one "context" finding that behaves like a defect
  (NC is otherwise fully priced). It is a candidate for a *fourth* check — "non-sold unit
  with no list price, in a fully-priced project" — but only if scoped to exclude early-stage
  projects, or it collapses into the La Puerta noise.

## Recommendation (for Khaled's decision — nothing built)

1. **Surface Check A and Check B** in the first cut — both are small, exact, and trackable,
   and each flagged record carries a concrete fix. They target different owners (A = sales /
   contract reconciliation; B = structural / Odoo hierarchy), so consider two sections.
2. **Keep Check C as a silent standing guard** (show "0 issues ✓") rather than a populated
   list — it's clean today but cheap to keep honest.
3. **Scope every check to exclude expected-empty early-stage data** (La Puerta pricing). If a
   "unit missing list price" check is wanted, restrict it to fully-priced projects (NC,
   Cassette) and to non-sold buckets — that surfaces exactly the 1 NC reserved unit without
   the 129 La Puerta false positives.
4. **Presentation:** mirror the CRM "Missing Contacts" pattern — a per-check count badge, an
   enumerated, exportable list (code + project + the specific defect text), and persistence so
   fixes can be tracked over time. Each row should carry its own one-line "what's wrong"
   string (already produced by the probes).

**STOP — discovery only. No view, service, endpoint, or test was built. Awaiting product
decision on scope + presentation.**
