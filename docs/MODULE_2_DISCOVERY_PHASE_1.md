# Module 2 — Discovery Phase 1 Findings

> **Status**: Complete  
> **Discovery date**: 2026-05-14  
> **Script runtime**: 14 seconds  
> **Estimated RPC calls**: ~80  
> **Cost**: $0 (no OpenAI calls)  
> **Source script**: `scripts/discover_collections.py`  
> **Script output** (transient, gitignored): `scripts/discover_collections_output.txt`  
> **Covers**: Collections Mgmt, RS Accounting data sources  
> **Phase 2 scope**: Standard Accounting, deep project/unit inventory,
> payment model internals, installment type lookup  

---

## 1. Custom Module Naming Convention

La Verde's real estate customizations follow a two-level naming pattern:

| Layer | Pattern | Example |
|-------|---------|---------|
| Python package (Odoo addon) | `pl_realestate_*` | `pl_realestate_installment` |
| Odoo model name | `rs.*` | `rs.installment` |

The `pl_` prefix indicates the vendor is likely **Plementus**, an Odoo
implementation partner. The `rs` prefix in model names stands for
**Real Estate** and applies to all custom models — structure, installment,
contract, accounting, and so on.

### Custom Modules Found (all state=installed)

| Module Name | App? | Description |
|-------------|------|-------------|
| `pl_realestate_base` | YES | Real Estate Base |
| `pl_realestate_installment` | YES | Real Estate Installments |
| `pl_realestate_reservation` | YES | Real Estate Reservation |
| `pl_realestate_contract` | YES | Real Estate Contract |
| `pl_realestate_contract_amendment` | YES | Real Estate Contract Amendment |
| `pl_realestate_payment_term` | YES | Real Estate Payment Term |
| `pl_realestate_accounting` | YES | Real Estate Accounting (= RS Accounting) |
| `pl_realestate_accounting_checks` | YES | Real Estate Accounting Checks |
| `pl_realestate_accounting_updates` | YES | Real Estate Accounting Updates |
| `pl_realestate_crm` | YES | Real Estate CRM |
| `pl_realestate_discount` | YES | Real Estate Discount |
| `pl_realestate_penalty` | YES | Real Estate Penalty |
| `pl_realestate_special_payment_plan_approval` | YES | Special Payment Plan Approval |
| `pl_realestate_termination` | YES | Real Estate Termination |
| `pl_realestate_commission_base` | YES | Real Estate Commission Base |
| `pl_realestate_unit_pricing` | YES | Real Estate Unit Pricing |
| `pl_installment_payment_report` | no | Installment Payment Report |

The existence of `pl_realestate_crm` confirms the CRM↔Collections
linkage is intentional, not incidental.

---

## 2. Model Inventory

Section 2 of the discovery script fetched all models from `ir.model`
and filtered by pattern. **102 matching models** were found. The
operationally relevant subset:

### Core Collections Models

| Model | Label | Records |
|-------|-------|---------|
| `rs.installment` | Real Estate Installments | 42,970 |
| `rs.reservation` | Real Estate Reservation | 1,479 |
| `rs.contract` | RealEstate Contract | 1,409 |
| `rs.contract.amendment` | Real Estate Contract Amendment | not counted |
| `rs.termination` | RealEstate Termination | not counted |

### Payment Structure Models

| Model | Label | Records |
|-------|-------|---------|
| `rs.payment.term` | Real Estate Payment Term | 1,497 |
| `rs.payment.plan` | Real Estate Payment Plan | 121 |
| `rs.payment.plan.line` | Real Estate Payment Plan Line | not counted |
| `rs.payment.type` | Real Estate Payment Type | not counted |
| `rs.payment.term.simulation` | Real Estate Payment Term Simulation | not counted |

### RS Accounting Models

| Model | Label | Records |
|-------|-------|---------|
| `rs.account.check` | Real Estate Accounting Checks | 5,179 |
| `rs.account.check.lot` | Real Estate Accounting Checks Lot | 77 |
| `rs.account.check.action` | Real Estate Accounting Checks Action | 1,654 |
| `rs.account.check.action.line` | Real Estate Accounting Checks Action Line | 2,223 |
| `rs.account.payment` | Real Estate Accounting Payment | not counted |
| `rs.account.payment.installment` | Real Estate Accounting Payment Installment | not counted |
| `rs.account.payment.installment.line` | Real Estate Accounting Payment Installment Line | not counted |
| `rs.account.payment.cash` | Real Estate Accounting Payment Cash | not counted |
| `rs.account.payment.reconcile` | Real Estate Accounting Payment Reconcile | not counted |
| `rs.entries.engine` | Entries for Real Estate Accounting | not counted |

### Project Structure Models

| Model | Label | Records |
|-------|-------|---------|
| `rs.structure.project` | Real Estate Project | not counted* |
| `rs.structure.phase` | Real Estate Phase | 5 |
| `rs.structure.zone` | Real Estate Zone | 11 |
| `rs.structure.building` | Real Estate Building | 277 |
| `rs.structure.unit` | Real Estate Unit | not counted* |

*Section 10 picked wrong sub-models for Project and Unit. See Section 9
of this document.

### Penalty / Discount / Installment Type Models

| Model | Label | Records |
|-------|-------|---------|
| `rs.penalty` | Real Estate Penalty | 170 |
| `rs.penalty.type` | Real Estate Penalty Type | 2 |
| `rs.penalty.line` | rs.penalty.line | 0 |
| `rs.discount` | Real Estate Discount | 0 |
| `rs.discount.type` | Real Estate Discount Type | 1 |
| `rs.installment.type` | Real Estate Installment Type | not counted |
| `rs.installment.type.category` | Real Estate Installment Type Category | not counted |
| `rs.installment.history` | Installment History | not counted |
| `rs.followup` | Real Estate Follow Up | not counted |

---

## 3. Installment Model — rs.installment

**Total records: 42,970** — pagination required (threshold: 5,000).

### Status Fields (confirmed)

| Business Label | Field Name | Type | Observed Values |
|----------------|------------|------|-----------------|
| Status (Accounting) | `state` | selection | `draft` (19), `post` (42,443), `cancel` (508) |
| Payment Status | `payment_state` | selection | `unpaid` (12,994), `partial` (418), `paid` (29,558) |

UI display name for `post` is "Posted". UI display name for `partial`
is "Partially Paid". Both fields are set automatically by the system.

### Amount Fields (the critical 5 + 3 Studio/check fields)

| Business Label | Field Name | Type | Note |
|----------------|------------|------|------|
| Amount | `amount` | monetary | Native field |
| Paid Amount | `paid_amount` | monetary | Native field |
| Actual Paid Amount | `x_studio_actual_paid_amount` | monetary | Odoo Studio field |
| Due Amount | `due_amount` | monetary | Native field |
| Total Due Amount | `total_due_amount` | monetary | Native field |
| Check Approved Amount | `check_approved_amount` | monetary | Native field |
| Check Pending Amount | `check_pending_amount` | monetary | Native field |
| Bank Collected Amount | `x_studio_bank_collected_amount` | monetary | Odoo Studio field |
| Executive Outstanding Amount | `x_studio_executive_outstanding_amount` | monetary | Odoo Studio field |

The `x_studio_*` prefix means these fields were added via Odoo Studio
(low-code field editor), not via the `pl_realestate_*` modules. They
are stored in the same database table and behave identically to native
fields for query purposes, but upgrades may be more sensitive to them.

### Relational Links

| Field | Type | Relation | Label |
|-------|------|----------|-------|
| `partner_id` | many2one | `res.partner` | Customer |
| `reservation_id` | many2one | `rs.reservation` | Reservation |
| `contract_id` | many2one | `rs.contract` | Contract |
| `amendment_id` | many2one | `rs.contract.amendment` | Amendment |
| `payment_term_id` | many2one | `rs.payment.term` | Payment Term |
| `payment_plan_id` | many2one | `rs.payment.plan` | Payment Plan |
| `termination_id` | many2one | `rs.termination` | Termination |
| `termination_payment_id` | many2one | `rs.termination.payment` | Termination Payment |
| `installment_type_id` | many2one | `rs.installment.type` | Installment Type |
| `payment_type_id` | many2one | `rs.payment.type` | Payment Period |
| `project_id` | many2one | `rs.structure.project` | Project |
| `phase_id` | many2one | `rs.structure.phase` | Phase |
| `zone_id` | many2one | `rs.structure.zone` | Zone |
| `building_id` | many2one | `rs.structure.building` | Building |
| `unit_id` | many2one | `rs.structure.unit` | Unit |
| `check_ids` | many2many | `rs.account.check` | Checks |
| `payment_line` | one2many | `rs.account.payment.installment.line` | Payment Lines |
| `penality_line` | one2many | `rs.penalty` | Penalty Lines |
| `discount_line` | one2many | `rs.discount` | Discount Lines |
| `followup_ids` | one2many | `rs.followup` | Follow Ups |
| `installment_history_ids` | one2many | `rs.installment.history` | Installment History |

**Field name typos:** `penality_line` (not `penalty_line`) and
`pending_penality_amount` (not `pending_penalty_amount`). These are
vendor typos baked into the schema. Any code querying these fields
must use the misspelled names exactly.

### Installment Type

`installment_type_id` is a **many2one to `rs.installment.type`**, not
a selection field. The 8 business type categories (Down Payment, Regular,
Maintenance, etc.) are records in `rs.installment.type`, not enum
values. Their IDs, names, and sequence have not been fetched.
Phase 2 required.

### Sample Records (sanitized)

The script returned the first 25 fields alphabetically, which are all
`activity_*` and `message_*` infrastructure fields. The structural
context from the samples:

| Field | Sample Values Observed |
|-------|----------------------|
| `project_id` | Project#New Capital (id=1), Project#Cassette (id=2) |
| `phase_id` | Phase#2 (id=2) |
| `zone_id` | Zone#2 (id=20), Zone#3 (id=21) |
| `building_id` | Building#10, Building#14, Building#18 |
| `unit_id` | Unit#AF190-10-101, Unit#AF208-18-401, Unit#AD270-14-203-303 |
| `unit_type_id` | AF-Apartment (id=27), AD-Duplex (id=37) |
| `unit_finishing_type_id` | Semi Finished (id=4) |

No customer names or PII appeared in any sample record.

---

## 4. Reservation Model — rs.reservation

**Total records: 1,479.**

### State Distribution

| State (technical) | Count | Business Meaning |
|-------------------|-------|-----------------|
| `draft` | 2 | Created, not yet confirmed |
| `confirm` | 41 | Confirmed — at least 1 EGP received |
| `contract` | 1,405 | Converted to Contract |
| `cancel` | 31 | Cancelled |

The 1,405 reservations in `contract` state correspond closely to the
1,409 contracts in `rs.contract` — consistent with the workflow.

### CRM Link (confirmed)

`opportunity_id` (many2one → `crm.lead`) is present directly on
`rs.reservation`. This is the primary bridge between the sales and
collections data. The `should_sync_opportunity` boolean confirms that
the CRM record is kept in sync with the reservation — a designed
bidirectional relationship, not a one-time reference.

### Key Relational Fields

| Field | Type | Relation | Label |
|-------|------|----------|-------|
| `opportunity_id` | many2one | `crm.lead` | Opportunity |
| `partner_id` | many2one | `res.partner` | Customer |
| `unit_id` | many2one | `rs.structure.unit` | Unit |
| `project_id` | many2one | `rs.structure.project` | Project |
| `building_id` | many2one | `rs.structure.building` | Building |
| `payment_term_id` | many2one | `rs.payment.term` | Payment Term |
| `contract_id` | many2one | `rs.contract` | Contract |
| `all_installment_ids` | one2many | `rs.installment` | All Installments |
| `installment_ids` | one2many | `rs.installment` | Installments |
| `maintenance_installment_ids` | one2many | `rs.installment` | Maintenance Installments |
| `facilities_installment_ids` | one2many | `rs.installment` | Facilities Installments |
| `penalty_installment_ids` | one2many | `rs.installment` | Penalty Installments |
| `modification_installment_ids` | one2many | `rs.installment` | Modification Installments |
| `service_installment_ids` | one2many | `rs.installment` | Service Installments |
| `other_service_installment_ids` | one2many | `rs.installment` | Other Service Installments |

The multiple `*_installment_ids` one2many fields are filtered views
of `rs.installment` segmented by installment type — not separate tables.

### Notable Fields

- `should_sync_opportunity` (boolean) — confirms bidirectional CRM sync
- `vat` / `vat_file` — national ID stored on reservation (not on
  `res.partner`); relevance to reporting TBD
- `multi_owner` / `partner_ids` — reservations can have multiple owners
- `collection_percentage` (float) — system-computed collection progress metric

---

## 5. Contract Model — rs.contract

**Total records: 1,409.**

### State Distribution

| State (technical) | Count | Business Meaning |
|-------------------|-------|-----------------|
| `draft` | 3 | Being drafted |
| `finance` | 1 | In Finance Review stage |
| `confirm` | 1,402 | Confirmed (all review stages passed) |
| `cancel` | 3 | Cancelled |

Only 4 of the 5 approval-cycle states appear in live data. The `legal`
and `engineering` intermediate states have no records currently — all
historical contracts have completed those stages. The single contract
in `finance` state is the only active in-progress approval at time
of discovery.

Filtering on `state = 'confirm'` captures 99.6% of active contracts.

### Key Relational Fields

| Field | Type | Relation | Note |
|-------|------|----------|------|
| `reservation_id` | many2one | `rs.reservation` | Bridge to CRM via reservation |
| `partner_id` | many2one | `res.partner` | Customer |
| `unit_id` | many2one | `rs.structure.unit` | Unit |
| `payment_term_id` | many2one | `rs.payment.term` | Payment Term |
| `all_installment_ids` | one2many | `rs.installment` | All Installments |
| `installment_ids` | one2many | `rs.installment` | Regular Installments |
| `penalty_installment_ids` | one2many | `rs.installment` | Penalty Installments |
| `ownership_percentage_ids` | one2many | `rs.ownership.percentage` | Multi-owner percentages |

No direct `opportunity_id` or `user_id` on `rs.contract`. CRM
attribution requires going through `reservation_id`.

### Additional Notable Fields

- `is_legal_blocked` (boolean) — contract can be blocked by Legal Affairs
- `legal_affairs_count` (integer) — related legal affairs records count
- `delivery_date`, `deliver_date`, `contract_delivery_date` — three
  delivery date fields exist; semantics and distinctions require
  verification

---

## 6. Payment Term & Payment Plan Models

### rs.payment.term — 1,497 records

State distribution: `draft` (7), `confirm` (1,446), `cancel` (44).

One payment term is created per reservation. The 1,497 records include
both active and cancelled terms (the 44 cancelled correspond to
cancelled reservations). Key links: `state`, and confirmed relationship
to installment schedules.

### rs.payment.plan — 121 records

Payment plan templates (Standard Plans) defined at the project level.

State distribution:

| State (technical) | Count | Business Meaning |
|-------------------|-------|-----------------|
| `draft` | 112 | Standard plans (not in approval flow) |
| `s_sales_manager_approved` | 7 | Special plan — Sales Manager approved |
| `approve` | 1 | Approved — `[REQUIRES VERIFICATION]` |
| `cancel` | 1 | Cancelled |

The UI-described approval chain (`Draft → Waiting → Direct Manager
Approved → Sales Manager Approved`) shows only `draft` and
`s_sales_manager_approved` in live data. The intermediate states
may have different technical values, or no plans are currently at
those stages.

The `s_sales_manager_approved` naming convention (lowercase with
underscores, `s_` prefix) is the vendor's technical state value —
not a display label.

### Related Models

| Model | Label |
|-------|-------|
| `rs.payment.plan.line` | Payment Plan Line (installment schedule rows) |
| `rs.payment.plan.rounding` | Rounding rules |
| `rs.payment.term.facilities` | Facilities within a Payment Term |
| `rs.payment.term.simulation` | Simulation wizard |
| `rs.payment.type` | Payment period type (monthly, quarterly, etc.) |

---

## 7. Check Models — RS Accounting

### rs.account.check — 5,179 records (pagination required)

The primary check model. Each record represents one physical check
received from a customer.

**Check lifecycle states:**

| State (technical) | Count | Business Meaning |
|-------------------|-------|-----------------|
| `draft` | 226 | Received, not yet processed |
| `holding` | 828 | Held at La Verde |
| `approved` | 780 | Approved for bank deposit |
| `deposited` | 2,747 | Sent to bank / deposited |
| `customer_return` | 551 | Returned to customer |
| `returned` | 47 | Bounced or returned by bank |

The `rejected` state appears in `rs.account.check.action.line` but
not at the check level — it may be a line-level outcome rather than
a terminal check state. The full lifecycle including rejection paths
requires Phase 2 investigation.

Key linkage: `check_ids` on `rs.installment` is a many2many to
`rs.account.check`, allowing direct lookup of checks against an
installment.

### rs.account.check.lot — 77 records

Batch containers for checks. States: `draft` (14), `validate` (63).
The 63 validated lots represent closed batches; 14 are open/pending.
Grouping mechanism (how many checks per lot, date range) not
investigated in Phase 1.

### rs.account.check.action — 1,654 records

Processing events applied to checks. States: `draft` (10),
`scheduled` (3), `post` (1,632), `cancel` (9).

### rs.account.check.action.line — 2,223 records

Sub-lines within check actions. Two state fields observed:

`current_check_state`:

| Value | Count |
|-------|-------|
| `deposited` | 1,129 |
| `holding` | 610 |
| `rejected` | 251 |
| `returned` | 233 |

`check_state` (historical state at time of action):

| Value | Count |
|-------|-------|
| `approved` | 793 |
| `customer_return` | 560 |
| `deposited` | 252 |
| `rejected` | 307 |
| `returned` | 308 |

The `rejected` state is present here but absent from the main
`rs.account.check` state field — this gap requires Phase 2
investigation.

### Models with Zero Records (check family)

`rs.account.check.action.subaction`, `rs.account.check.location`,
`rs.account.check.position`, `rs.account.payment.check`,
`rs.account.payment.check.reconcile` — all exist in `ir.model` but
have 0 live records. These may be feature stubs or deprecated paths.

---

## 8. Payment Models

### Standard account.payment — 0 records

**Critical finding.** The standard Odoo payment model is installed
but contains no records. La Verde does not use the standard Odoo
payment path. All payments flow exclusively through the custom
`rs.account.payment.*` family.

Implications:
- RS Accounting is the **canonical** payment system — not Standard Odoo
- The role of `account.move` in the payment-to-posting flow
  (Business Context Section 14) requires re-examination. If no
  `account.payment` exists, journal entry creation is likely driven
  by `rs.entries.engine` rather than the standard payment workflow
- There is definitively no double-recording between RS Accounting
  and Standard Accounting

### RS Accounting Custom Payment Models (not deep-dived)

| Model | Label |
|-------|-------|
| `rs.account.payment` | Real Estate Accounting Payment |
| `rs.account.payment.installment` | Real Estate Accounting Payment Installment |
| `rs.account.payment.installment.line` | Real Estate Accounting Payment Installment Line |
| `rs.account.payment.cash` | Real Estate Accounting Payment Cash |
| `rs.account.payment.cash.line` | Real Estate Accounting Payment Cash Line |
| `rs.account.payment.reconcile` | Real Estate Accounting Payment Reconcile |
| `rs.account.payment.reconcile.line` | Real Estate Accounting Payment Reconcile |
| `rs.account.payment.reconcile.request` | Real Estate Accounting Payment Reconcile Request |

**Known linkage from other models' fields:**
- `rs.installment.payment_line` → one2many to `rs.account.payment.installment.line`
- `rs.discount.installment_payment_id` → many2one to `rs.account.payment.installment`
- `rs.penalty.line.installment_payment_id` → many2one to `rs.account.payment.installment`

This confirms `rs.account.payment.installment` is the join table
between the payment event and the installment. Its field inventory
is the highest-priority Phase 2 task.

### rs.entries.engine

Identified in Section 2. Label: "Entries for Real Estate Accounting".
Has an accompanying `rs.entries.engine.mixin`. This model likely
generates `account.move` (journal entry) records from RS Accounting
payment events — the bridge between the custom payment system and
Standard Accounting. Not investigated in Phase 1.

### rs.account.installment.payment.wzd

A transient wizard model. Label: "Real Estate installment Payment
Wizard". This is the UI entry point for registering payments against
installments in the RS Accounting interface.

---

## 9. Penalty and Discount Models

### rs.penalty — 170 records

Penalty records exist as a **separate model** parallel to penalty
installment lines. A single penalty event produces both:
- A record in `rs.penalty` (the penalty document with its own tracking)
- An installment line of type "Penalties" in `rs.installment` (the
  financial obligation visible in Collections Mgmt)

Key fields:

| Field | Type | Relation | Label |
|-------|------|----------|-------|
| `amount` | monetary | — | Penalty Amount |
| `due_amount` | monetary | — | Due Amount |
| `paid_amount` | monetary | — | Paid Amount |
| `state` | selection | — | Status |
| `type` | selection | — | Type |
| `penalty_type_id` | many2one | `rs.penalty.type` | Penalty Type |
| `installment_id` | many2one | `rs.installment` | Installment |
| `contract_id` | many2one | `rs.contract` | Contract |
| `reservation_id` | many2one | `rs.reservation` | Reservation |
| `check_id` | many2one | `rs.account.check` | Check |
| `penalty_line` | one2many | `rs.penalty.line` | Penalty Lines |

The `check_id` link allows penalties to be directly associated with
a specific bounced or returned check — a common trigger in real
estate collections.

### rs.penalty.line — 0 records

Sub-line model; currently empty. Field structure reveals the
amendment and payment linkage:

| Field | Relation | Note |
|-------|----------|------|
| `penalty_id` | `rs.penalty` | Parent penalty document |
| `amendment_id` | `rs.contract.amendment` | Confirms: penalties created via amendment |
| `installment_payment_id` | `rs.account.payment.installment` | Payment that triggered/settled the penalty |
| `check_action_id` | `rs.account.check.action` | Check processing event |

### rs.discount — 0 records

Model exists with full field structure but no live records.

Key fields:

| Field | Type | Label |
|-------|------|-------|
| `amount` | monetary | Discount Amount |
| `applied_amount` | monetary | Applied Amount |
| `residual_amount` | monetary | Residual Amount |
| `percentage` | float | Discount Percentage |
| `installment_id` | many2one → `rs.installment` | Installment |
| `installment_payment_id` | many2one → `rs.account.payment.installment` | Installment Payment |
| `discount_type_id` | many2one → `rs.discount.type` | Discount Type |

### rs.structure.discount — 1 record

Distinct from `rs.discount`. Stores **structural discounts** defined
at the project/building level (e.g., a blanket discount configured
on a Phase). Fields: `discount` (float percentage), `discount_amount`
(monetary), `discount_type` (selection), plus full hierarchy links.

### rs.penalty.type — 2 records | rs.discount.type — 1 record

Type lookup tables with `name` (char) and `type` (selection) fields.
Actual type values not retrieved in Phase 1.

---

## 10. Project Structure Hierarchy

### Script Model-Picking Errors

| Level | Script picked | Correct model | Error reason |
|-------|--------------|---------------|-------------|
| Project | `rs.structure.project.type` (1 record) | `rs.structure.project` | type model matched "project" pattern first |
| Unit | `rs.unit.search` (wizard, RPC error) | `rs.structure.unit` | wizard matched "unit" pattern; no `name` field |

Both correct models are confirmed to exist in `ir.model` (Section 2
output) and confirmed by relational field evidence: every operational
model carries `project_id → rs.structure.project` and
`unit_id → rs.structure.unit`.

### Correctly Identified Levels

| Level | Model | Records | Parent links |
|-------|-------|---------|-------------|
| Phase | `rs.structure.phase` | 5 | `project_id` |
| Zone | `rs.structure.zone` | 11 | `project_id`, `phase_id` |
| Building | `rs.structure.building` | 277 | `project_id`, `phase_id`, `zone_id` |

### Live Projects (inferred from sample data)

Three projects are visible from phase and installment samples:

| id | Project label |
|----|--------------|
| 1 | Project#New Capital |
| 2 | Project#Cassette |
| 3 | Project#La puerta |

### Unit Hierarchy Context

Unit IDs in the 3,700–5,300 range appear across all three projects,
suggesting several thousand units total. Exact count requires Phase 2.

Unit codes in samples follow a pattern:
`[Type prefix][unit#]-[building#]-[floor][unit-on-floor]`
(e.g., `AF190-10-101` = type AF, unit 190, building 10, floor 1, unit 01).

### Additional Structure Models

| Model | Label |
|-------|-------|
| `rs.structure.type` | Real Estate Type (category) |
| `rs.structure.mixin` | Shared mixin for all structure levels |
| `rs.structure.boq` | Bill of Quantities |
| `rs.structure.unit.type` | Unit Type (Apartment, Villa, etc.) |
| `rs.structure.unit.view` | View type (Landscape, Road, etc.) |
| `rs.structure.unit.finishing.type` | Finishing level (Core/Shell, Semi, etc.) |

---

## 11. CRM ↔ Collections Linkage

**Result: Confirmed — 2-hop join. No direct CRM field on installment
or contract.**

### Attribution Path

```
rs.installment
    .reservation_id  ──►  rs.reservation
                              .opportunity_id  ──►  crm.lead
                                                        .user_id  ──►  res.users
                                                                           (salesperson)
```

Alternative path via contract (same endpoint):

```
rs.installment
    .contract_id  ──►  rs.contract
                           .reservation_id  ──►  rs.reservation
                                                     .opportunity_id  ──►  crm.lead
```

Both paths reach `rs.reservation.opportunity_id`. The direct
`reservation_id` on `rs.installment` is the shorter path and preferred.

### Field Evidence Per Model

| Model | CRM fields found | Indirect path fields |
|-------|-----------------|---------------------|
| `rs.installment` | None | `reservation_id`, `contract_id` |
| `rs.reservation` | `opportunity_id` → `crm.lead` | — |
| `rs.contract` | None | `reservation_id` |

`rs.reservation.should_sync_opportunity` (boolean) confirms the
`opportunity_id` link is a maintained bidirectional relationship.

### Practical Query Strategy

Three sequential `search_read` calls suffice — no joins needed:

```
Step 1: rs.installment  → fields ['id', 'amount', 'payment_state',
                                   'date', 'reservation_id']
Step 2: rs.reservation  → fields ['id', 'opportunity_id'],
                           domain [['id', 'in', <reservation_ids>]]
Step 3: crm.lead        → fields ['id', 'user_id', 'team_id'],
                           domain [['id', 'in', <opportunity_ids>]]
```

Each step returns a small result set (reservations: 1,479;
opportunities: at most 1,479). No pagination concerns at the
attribution step.

### rs.followup — Follow-up Tracking System

`rs.installment.followup_ids` (one2many → `rs.followup`) reveals a
structured follow-up tracking system. Three related models exist:
`rs.followup`, `rs.followup.result`, `rs.followup.type`. This allows
Collections Officers to log follow-up calls and outcomes against
individual installments. Not inventoried in Phase 1.

---

## 12. Critical Findings Summary

| # | Finding | Impact |
|---|---------|--------|
| 1 | **`account.payment` = 0.** RS Accounting is the sole canonical payment system. | Eliminates standard Odoo as a payment data source. All payment queries go to `rs.account.payment.*`. Business Context Section 14 flow requires re-examination. |
| 2 | **Contract approval cycle is 5 stages** (Draft → Legal → Finance → Engineering → Confirmed), not a simple conversion step. | Business Context Section 5 corrected. `state = 'confirm'` captures 99.6% of active contracts. Technical values for `legal` and `engineering` stages unconfirmed. |
| 3 | **`x_studio_actual_paid_amount` is an Odoo Studio field**, not a native model field. Two additional Studio fields exist for bank/executive check collection tracking. | The 5-column financial model is partially Studio-added. Studio fields are queryable identically to native fields but carry upgrade sensitivity. |
| 4 | **CRM attribution requires a 2-hop join** through `rs.reservation.opportunity_id`. No direct CRM link on `rs.installment` or `rs.contract`. | Salesperson dashboards require 3 sequential `search_read` calls. The `pl_realestate_crm` module makes this linkage intentional. |
| 5 | **Installment type is Many2One** (`installment_type_id` → `rs.installment.type`), not a selection field. | `read_group` by installment type requires grouping by the relational field. Actual type records (names, IDs, sequence) must be fetched in Phase 2. |

---

## 13. Gaps Requiring Phase 2 Discovery

| # | Gap | Correct approach |
|---|-----|-----------------|
| 1 | `rs.structure.project` and `rs.structure.unit` field inventory and record counts | `search_count` + `fields_get` on each; expect ~3 projects and several thousand units |
| 2 | `rs.account.payment` and `rs.account.payment.installment` full inventory | `fields_get`, `search_count`, `read_group` by `state`; this is the primary payment data source |
| 3 | `rs.installment.type` actual records (the 8 installment type names, IDs, sequence) | `search_read` with no domain, all fields; small lookup table |
| 4 | Special Payment Plan intermediate approval states — `waiting` and `direct_manager_approved` technical values not confirmed | `fields_get` on `rs.payment.plan` inspecting the `state` field's selection values attribute |
| 5 | Field-by-field reconciliation of the 5 standard + 2 Studio + 2 check amount columns | Pull a single installment with known payment history and verify all monetary fields sum correctly |

---

## 14. Record Counts and Pagination Strategy

Pagination threshold: **5,000 records**. Models above this threshold
require offset-based pagination in production fetchers.

| Model | Records | Paginate? | Note |
|-------|---------|-----------|------|
| `rs.installment` | 42,970 | **YES** | Primary data source |
| `rs.account.check` | 5,179 | **YES** | Just above threshold |
| `rs.account.check.action.line` | 2,223 | no | |
| `rs.account.check.action` | 1,654 | no | |
| `rs.payment.term` | 1,497 | no | |
| `rs.reservation` | 1,479 | no | |
| `rs.contract` | 1,409 | no | |
| `rs.payment.plan` | 121 | no | |
| `rs.account.check.lot` | 77 | no | |
| `rs.penalty` | 170 | no | |
| `rs.structure.building` | 277 | no | |
| `rs.structure.zone` | 11 | no | |
| `rs.structure.phase` | 5 | no | |
| `rs.penalty.type` | 2 | no | |
| `rs.discount.type` | 1 | no | |
| `rs.structure.discount` | 1 | no | |
| `rs.discount` | 0 | no | Model exists, unused |
| `rs.penalty.line` | 0 | no | Model exists, unused |
| `account.payment` | 0 | no | Standard Odoo — not used by La Verde |
| `rs.structure.project` | **not counted** | unknown | Wrong model picked in Phase 1 |
| `rs.structure.unit` | **not counted** | unknown | Wrong model picked in Phase 1 |
| `rs.account.payment` | **not counted** | unknown | Not deep-dived in Phase 1 |
| `rs.account.payment.installment` | **not counted** | unknown | Not deep-dived in Phase 1 |

---

## 15. Sanitization Note

All sample records in this document are sanitized. The discovery
script applied `sanitize()` to all fields with names matching
`name`, `partner_name`, `customer_name`, `display_name`, `phone`,
`mobile`, `email`, `vat`, `id_number`, `street`, `street2`, `city`.

In practice, the sample records returned by the discovery run did not
contain customer PII — the first-25-fields-alphabetically sampling
returned primarily `activity_*` and `message_*` infrastructure fields.
Project names (Project#New Capital, Project#Cassette, Project#La
puerta), building references (Building#14), and unit codes
(Unit#AF190-10-101) are internal operational identifiers, not
customer data, and are retained.

No real customer names, phone numbers, national IDs, or partner names
appear anywhere in this document.
