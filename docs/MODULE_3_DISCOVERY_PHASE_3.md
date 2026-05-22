# Module 3 Discovery — Phase 3 Findings: Reconcile / Customer Wallet

> **Status**: Complete (fix run 2026-05-22; OQ1 closed 2026-05-22 by Khaled Odoo UI review)
> **Discovery date**: 2026-05-22
> **Scripts**:
>   - `scripts/discover_reconcile_phase3.py` — initial broad scan
>   - `scripts/discover_reconcile_phase3_fix.py` — targeted fix run on the correct model
> **Outputs**:
>   - `scripts/discover_reconcile_phase3_2026-05-22.txt`
>   - `scripts/discover_reconcile_phase3_fix_2026-05-22.txt`
> **Cost**: $0.00 (no OpenAI calls, read-only RPCs only)
> **Covers**: Identification of the Odoo model backing the Reconcile / Customer Wallet
> concept described in `MODULE_2_BUSINESS_CONTEXT.md §15`

---

## 1. Discovery Target

§15 of `MODULE_2_BUSINESS_CONTEXT.md` defines **Reconcile** as a per-customer balance of
funds paid to La Verde that are not yet allocated to a specific `rs.installment`. Two
scenarios: (1) initial reservation before a unit is chosen, (2) ownership transfer where
funds from the old owner become the new owner's wallet balance. Both scenarios end with
the balance being applied to the Down Payment of a new plan.

§16 stated that the Odoo model name, field names, and state machine were unknown and
marked this as **Phase 3 Discovery Required**.

---

## 2. Model Identified: `rs.account.payment.reconcile`

### Evidence Summary

| Item | Value |
|---|---|
| Model technical name | `rs.account.payment.reconcile` |
| Odoo label | "Real Estate Accounting Payment Reconcile" |
| Module family | `pl_realestate_accounting` |
| Record count (2026-05-22) | **205** |
| State distribution | **all 205 records: `state = 'post'`** |
| Customer link | `partner_id → res.partner` (many2one, label: "Partner") |
| Balance fields | `amount` (monetary), `reconciled_amount` (monetary), `residual_amount` (monetary) |
| Currency | `currency_id = [74, 'EGP']` — homogeneous with `rs.installment` ✓ |
| Type field | `type = 'advance_payment'` for all 205 live records |
| Flow direction indicator | Sign of `amount`: positive = receipt, negative = refund. `payment_type` is **unreliable** — all 205 records (including 7 refunds) show `inbound`. |

### The Three-Field Balance Structure

| Field | Type | Business meaning |
|---|---|---|
| `amount` | monetary | Total cash received into the wallet |
| `reconciled_amount` | monetary | Portion already applied to installment(s) |
| `residual_amount` | monetary | **Current unallocated wallet balance.** Negative for refund records (`amount < 0`). |

Confirmed from fix run samples: for the 3 oldest records, `reconciled_amount = 0.0` and
`residual_amount = amount` exactly — these are unallocated wallets with no application yet.

### Data Entry Pattern Observed in Samples

All 3 samples share a notable characteristic:
- `date` (payment date): 2018-12-05, 2018-12-09, 2020-12-16 — original payment dates
- `create_date`: all 2026-05-17 — entered into the system on the same day
- `reconcile_request_id`: all link to `[2, 'RR/2026/05/00002']`

This indicates a **bulk historical migration**: 205 old wallet balances were entered into
`rs.account.payment.reconcile` on 2026-05-17, backdated to original payment dates. These
are not new payments — they represent customers who had pre-existing balances that were
being migrated into this model.

---

## 3. Supporting Model Ecosystem

```
rs.account.payment.reconcile.request   (1 record, state = 'new')
   └── reconcile_payment_ids (one2many)
          └── rs.account.payment.reconcile   (205 records, all 'post') ← PRIMARY WALLET MODEL
                    └── reconcile_line (one2many)
                           └── rs.account.payment.reconcile.line   (0 records)
```

| Model | Records | Role |
|---|---|---|
| `rs.account.payment.reconcile.request` | 1 | Approval/request document. All 205 reconcile payments link to the single request `RR/2026/05/00002` (id=2, state=`new`). The request is still open — its payments are posted but the request itself is pending. |
| `rs.account.payment.reconcile` | **205** | **Primary wallet model.** One record per customer wallet entry. |
| `rs.account.payment.reconcile.line` | 0 | Sub-lines (currently unused). |

**All 205 wallet records belong to a single reconcile request (`id=2`).** This is the bulk
migration request created 2026-05-17. Its state=`new` suggests it has not been formally
closed/processed yet. The 205 individual payment records are already `post`.

### Related — `rs.account.payment.check.reconcile` (48 records)

**Separate model.** Handles check-level reconciliation events (the payment instrument),
not the customer wallet balance. Not relevant to §15.

---

## 4. Field Inventory — `rs.account.payment.reconcile`

**67 fields total.** Key fields confirmed from fix run:

### Balance Fields (confirmed MONETARY type)

| Field | Type | Label | Role |
|---|---|---|---|
| `amount` | **monetary** | Amount | Total cash received into wallet |
| `reconciled_amount` | **monetary** | Reconciled Amount | Already applied to installment(s) |
| `residual_amount` | **monetary** | Residual Amount | Current unallocated balance |

All three are `monetary` type — not `float`. They are paired with `currency_id`.

### Currency Fields

| Field | Type | Relation | Confirmed value |
|---|---|---|---|
| `currency_id` | many2one | `res.currency` | `[74, 'EGP']` |
| `company_currency_id` | many2one | `res.currency` | `[74, 'EGP']` |
| `currency_rate` | float | — | `1.0` |

### Type / Direction Fields

| Field | Type | Schema values | Live distribution |
|---|---|---|---|
| `type` | selection | `advance_payment`, `outstanding_payment`, `termination_payment` | **205 = `advance_payment`**, 0 others |
| `payment_type` | selection | `inbound` (Receive Money), `outbound` (Send Money) | **205 = `inbound`** — including 7 refund records. **Not a reliable flow-direction indicator.** Use sign of `amount` instead. |

### State Field

| Field | Observed values (205 records) |
|---|---|
| `state` | `post` (205/205) |

### Customer and Context Linkage

| Field | Type | Relation | Label | Observed in samples |
|---|---|---|---|---|
| `partner_id` | many2one | `res.partner` | Partner | Present (REDACTED) |
| `reservation_id` | many2one | `rs.reservation` | Reservation | Present (REDACTED) |
| `contract_id` | many2one | `rs.contract` | Contract | `False` in all 3 samples |
| `unit_id` | many2one | `rs.structure.unit` | Unit | `False` in all 3 samples |
| `termination_id` | many2one | `rs.termination` | Termination | `False` in all 3 samples |
| `reconcile_request_id` | many2one | `rs.account.payment.reconcile.request` | Reconcile Request | `[2, 'RR/2026/05/00002']` |
| `reconcile_line` | one2many | `rs.account.payment.reconcile.line` | Reconcile Lines | `[]` (empty) |

`contract_id = False` and `unit_id = False` in all samples is consistent with §15
Scenario A (initial reservation without a unit — the customer hasn't chosen a unit yet).

---

## 5. State Machine

**`rs.account.payment.reconcile.state`:**

| State | Count | Meaning |
|---|---|---|
| `post` | 205 | Posted / active |

All 205 records are `post`. No `draft` or `cancel` in live data. Domain for all balance
queries: `[('state', '=', 'post')]` — consistent with `rs.installment` scoping.

**`rs.account.payment.reconcile.request.state`:**

| State | Count | Meaning |
|---|---|---|
| `new` | 1 | Request created, not yet processed |

The single request (id=2, 'RR/2026/05/00002') is in `new` state, created 2026-05-17
during the bulk migration.

---

## 6. Currency Analysis — Homogeneity with `rs.installment`

**CONFIRMED HOMOGENEOUS — EGP.**

Fix run sample records from `rs.account.payment.reconcile` show:
- `currency_id = [74, 'EGP']` on all 3 records
- `company_currency_id = [74, 'EGP']` on all 3 records
- `currency_rate = 1.0` (no multi-currency)

`rs.installment` amount fields are also monetary EGP (confirmed in Phase 1/2).

The planned sum `SUM(rs.installment.x_studio_actual_paid_amount) + SUM(rs.account.payment.reconcile.residual_amount)` is currency-homogeneous. No conversion required.

---

## 7. Type Field Semantics — Mapping to §15 Scenarios

The `type` field has three schema values, mapped to §15 as follows:

| `type` value | Display | §15 scenario | Live records |
|---|---|---|---|
| `advance_payment` | Advance Payment | **Scenario A** — customer pays before choosing a unit | **205** |
| `termination_payment` | Termination Payment | **Scenario B** — ownership transfer (funds moved to new owner) | **0** |
| `outstanding_payment` | Outstanding Payment | Unknown (possibly a third scenario or historical migration type) | **0** |

**Key finding:** All 205 live records are Scenario A (`advance_payment`). Scenario B
(`termination_payment`) has never been recorded through this model in live data — ownership
transfers may have been handled differently historically, or have not occurred since this
model was set up.

`payment_type = 'inbound'` for all 205 — but this field is **not a reliable flow-direction
indicator**. 7 of the 205 records are refunds (`amount < 0`, Destination Account =
"عملاء – استرداد"), yet their `payment_type` is still `inbound`. The sign of `amount` is
the actual indicator of flow direction (see §4.1 below).

### §4.1 Two Flow Types — Amount Sign Is the Actual Indicator

Confirmed by Khaled's Odoo UI review (2026-05-22):

| Flow type | `amount` sign | `residual_amount` | Destination Account | Count (live) |
|---|---|---|---|---|
| Receipt (customer pays La Verde) | positive | positive | Normal receivable account | 198 |
| Refund (La Verde returns funds to customer) | **negative** | **negative** | "عملاء – استرداد" (customer refund account) | **7** |

**`payment_type` is `inbound` for both rows** — it does not distinguish them.
4 of the 7 refund records have `partner = "عميل غير معروف"` (unknown customer),
consistent with §15 Scenario A (pre-unit reservation). Refund dates are spread
across 2025 — these are not a migration artifact.

**Design implication for Module 3:** Any KPI that sums `residual_amount` portfolio-wide
must decide explicitly how to handle the 7 negative records:
- Include them (net position — reduces total if a customer has both receipts and refunds)
- Exclude them (only positive balances — "funds still held")
- Separate them (report receipts and refunds independently)

This is a **Module 3 design decision**, not a discovery finding. It cannot be resolved
without understanding the business question being answered.

---

## 8. Linkage Map

### Customer → Wallet Balance Query

```
rs.account.payment.reconcile
    .partner_id          ──►  res.partner  (customer)
    .state = 'post'
    .residual_amount           ← current unallocated balance
```

### Down Payment Application — Reverse Linkage

The reconcile record does **not** store a pointer to the installment it was applied to.
The link is on the payment side:

```
rs.account.payment.installment   (the installment payment event)
    .reconcile_payment_ids  ──►  rs.account.payment.reconcile  (many2many)
    .reconcile_outstanding_amount  (boolean: "use wallet to cover outstanding")
    .payment_reconcile_request_id  ──►  rs.account.payment.reconcile.request
```

When staff registers an installment payment and ticks `reconcile_outstanding_amount`,
the `reconciled_amount` on the wallet record increases and `residual_amount` decreases.
The wallet record itself does not know which installment — that information lives on
`rs.account.payment.installment`.

### Reservation / Contract Linkage

`reservation_id` and `contract_id` exist on the wallet record. In the 3 samples,
`reservation_id` is set and `contract_id` is `False` — consistent with the pre-unit
reservation scenario.

---

## 9. Open Questions

### OQ-NEW-1 — 7 Records with Negative `residual_amount` — **CLOSED**

**Closed 2026-05-22 by Khaled Odoo UI review.**

| Sign | Count (state = 'post') |
|---|---|
| `residual_amount > 0` | 198 |
| `residual_amount = 0` | 0 |
| `residual_amount < 0` | 7 |

**Finding:** The 7 negative records are **legitimate refunds** — not migration errors.

Evidence from the inspected record:
- `amount = -60,000` (negative from the original entry — the sign originates on `amount`, not from `reconciled_amount` exceeding `amount`)
- `reconciled_amount = 0`
- `residual_amount = -60,000`
- Destination Account = "عملاء – استرداد" (customer refund account)
- 4 of 7 have `partner = "عميل غير معروف"` — consistent with pre-unit reservation (§15 Scenario A)
- Dates spread across 2025 — not a bulk migration artifact

**Conclusion:** The negative `residual_amount` is correct accounting for a refund outflow.
The §15 sum is arithmetically valid as-is — the sign on `amount` carries the refund
semantics automatically. How to treat refunds in any given KPI is a Module 3 design
decision (see §4.1). No data quality issue.

### OQ-NEW-2 — `type = 'outstanding_payment'` and `termination_payment` Both Have 0 Records

**Finding:** Both exist in the schema but no live records use them.

`termination_payment` is the expected type for §15 Scenario B (ownership transfer). Its
absence means one of:
1. Scenario B has never been recorded through this model (handled via a different path)
2. The ownership transfer feature using this model has not been activated yet
3. Terminated/transferred units were handled manually outside this model historically

**Impact:** For now, a query filtering `type = 'advance_payment'` would return all
205 current records. Once ownership transfers are recorded via this model, those records
will appear with `type = 'termination_payment'`. The §15 sum should include BOTH types —
`domain = [('state', '=', 'post')]` (no type filter) is safer than filtering by type.

`outstanding_payment` semantics are unknown. Before filtering by type in any Module 3
KPI, Khaled should confirm what this type represents.

### OQ2 (Carried) — `reconcile_line` — When Is It Populated?

All 205 records have `reconcile_line = []`. The sub-line model has 0 records.

**Priority:** Low — does not block the balance query. Relevant only if Module 3 needs
to reconstruct per-installment application history from the wallet side.

### OQ4 (Carried) — `rs.account.payment.reconcile.request` — Workflow Role

Single request `RR/2026/05/00002` (state=`new`) groups all 205 wallet records. Whether
the request model is a mandatory step or an optional approval layer is unclear.

**Priority:** Low — does not affect the current balance query.

---

## 10. §15 Sum — Status

Per `MODULE_2_BUSINESS_CONTEXT.md §15`, any future KPI for "total cash received from
customer X" must compute:

```
total_cash_from_X =
    SUM(rs.installment.x_studio_actual_paid_amount
        WHERE partner_id = X AND state = 'post')
  + SUM(rs.account.payment.reconcile.residual_amount
        WHERE partner_id = X AND state = 'post')
```

**Currency:** HOMOGENEOUS — both sides EGP, monetary type, `currency_id = [74, 'EGP']`. ✓

**State filter:** `state = 'post'` on both sides. ✓

**Type filter:** Do NOT filter by `type` — include all values so ownership transfers
(`termination_payment`) are automatically included when they appear. ✓

**Refund handling (design decision for Module 3):** 7 records have `amount < 0` (refunds).
Including them in the sum produces a net position per customer. Excluding them produces
"funds still held" only. The formula above is structurally correct; the choice of domain
filter on `amount` is a Module 3 design decision — see §4.1.

**No blocking items remain.** OQ-NEW-1 closed.

---

## 11. Script Lesson — Section 6 Primary Candidate Selection

The original `discover_reconcile_phase3.py` selected the "primary candidate" for Section 6
by highest record count. This returned `rs.account.check` (5,253 records) instead of
`rs.account.payment.reconcile` (205 records). The sample records and aggregate sign check
in Section 6 of the original run described check records, not wallet records.

**Lesson for future discovery scripts:** When the target model is known by name or by
business function, the primary candidate should be selected by name match or explicit
designation — not by record count. High record counts indicate operational volume, not
conceptual relevance. A dedicated `DISCOVERY_TARGET` constant at the top of the script
(like `_TYPE_MODEL` in `discover_installment_types.py`) prevents this error.

The fix script `discover_reconcile_phase3_fix.py` addresses this by targeting
`rs.account.payment.reconcile` explicitly from line 1.

---

## 12. Summary — What Is Now Known

| Item | Status |
|---|---|
| Primary wallet model | **CONFIRMED** — `rs.account.payment.reconcile` |
| Record count | **205** (all `state = 'post'`) |
| Customer linkage field | **CONFIRMED** — `partner_id → res.partner` |
| Balance field (current unallocated) | **CONFIRMED** — `residual_amount` (monetary) |
| Total received field | **CONFIRMED** — `amount` (monetary) |
| Applied amount field | **CONFIRMED** — `reconciled_amount` (monetary) |
| Field type for all 3 balance fields | **CONFIRMED** — `monetary` (not float) |
| Currency | **CONFIRMED** — EGP (`currency_id = [74, 'EGP']`, homogeneous with `rs.installment`) |
| State machine | **CONFIRMED** — `state = 'post'` for all active records |
| Flow direction indicator | **CONFIRMED** — sign of `amount` (positive = receipt, negative = refund). `payment_type` is unreliable for this. |
| Current scenario type | **CONFIRMED** — all 205 = `type = 'advance_payment'` (Scenario A) |
| Scenario B (`termination_payment`) | **CONFIRMED ABSENT** — 0 live records |
| Down Payment application linkage | **CONFIRMED** — reverse direction via `rs.account.payment.installment.reconcile_payment_ids` |
| Sign of `residual_amount` | **CLOSED** — OQ-NEW-1: 7 negative records are legitimate refunds (Khaled UI review 2026-05-22) |
| Refund treatment in §15 sum | **OPEN (design decision)** — Module 3 must choose: net position vs. receipts-only filter |
| `outstanding_payment` type semantics | **OPEN** — OQ-NEW-2 |
| `reconcile_line` usage | **OPEN (low priority)** — OQ2 |
| Request model workflow | **OPEN (low priority)** — OQ4 |

---

*Discovery complete 2026-05-22. OQ1 closed same day by Khaled Odoo UI review.*
*No Module 3 design or feature code written.*
