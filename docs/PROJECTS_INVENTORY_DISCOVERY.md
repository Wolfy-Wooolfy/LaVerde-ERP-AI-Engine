# Projects / Inventory Module — Discovery & Feasibility

**Status:** Read-only discovery. No code, no commit. Decision document for a GO / NO-GO call.
**Date:** 2026-06-18 &nbsp;|&nbsp; **Odoo:** laverde.odoo.com (Odoo 17), JSON-RPC, READ-ONLY &nbsp;|&nbsp; **AI cost:** $0.00
**HEAD at discovery:** 6b7b89d (clean tree, == origin/main)
**Scope:** the **supply / inventory** side (units, areas, value, availability) — complementary to the shipped **Collections** (receivables) and **Customer Accounts** (balances), which are the **money** side.

---

## 0. Executive summary

La Verde's Odoo carries a real, populated real-estate inventory of **1,873 units** across **3 projects**, with a confirmed hierarchy and rich per-unit area / pricing / status data. The data is **good enough to build a board-facing Inventory module today — for two of the three projects** — provided numbers are framed correctly.

| Field | Readiness verdict (one line) |
|-------|------------------------------|
| **STATUS** (`state`) | ✅ **Board-trustworthy now** — 100% populated, cross-validates against actual contracts. |
| **AREA** (`total_area`) | ✅ **Trustworthy for New Capital + Cassette (100%)**; ❌ La Puerta nearly empty (9/138). |
| **PRICE** (`amount`) | ✅ **Trustworthy for New Capital + Cassette (100%)**; ❌ La Puerta nearly empty (9/138). Caveat: list price was bulk-reloaded May 2026 — it is *current list value*, not historical contracted revenue. |

**Recommendation: GO**, phased — build Inventory & Availability first (fully supported), add Value & Area for New Capital + Cassette, **carve La Puerta out** as "data pending," and defer Absorption to a later slice. See §8.

---

## 1. Hierarchy map (confirmed from live schema)

The custom RE module prefix is `pl_realestate_*`; the model prefix is `rs.*` (84 `rs.*` models total). The structural hierarchy uses the `rs.structure.*` family. The **level order was previously unknown — it is now resolved from the live parent-link fields:**

```
rs.structure.project    3        (no structural parent — TOP)
   └─ rs.structure.phase    5     parent: project_id
        └─ rs.structure.zone   11   parent: project_id, phase_id
             └─ rs.structure.building  277   parent: project_id, phase_id, zone_id
                  └─ rs.structure.unit   1,873  parent: project_id, phase_id, zone_id, building_id
```

**Phase sits ABOVE Zone** (this was the open question): `rs.structure.zone` carries a `phase_id` link, while `rs.structure.phase` carries only `project_id`. Confirmed by both the link direction and the counts (5 phases → 11 zones).

Every unit denormalises the full chain — `project_id`, `phase_id`, `zone_id`, `building_id` are **100% populated on all 1,873 units** — so any level can be aggregated directly off the unit table without walking the tree. (`rs.structure.base` / `rs.structure.mixin` are shared bases, not levels.)

### Structural vs. operational `rs.*` models

| Role | Models |
|------|--------|
| **Structural hierarchy** | `rs.structure.project`, `rs.structure.phase`, `rs.structure.zone`, `rs.structure.building`, `rs.structure.unit` |
| Structural lookups | `rs.structure.type`, `rs.structure.unit.type`, `rs.structure.unit.view`, `rs.structure.unit.finishing.type`, `rs.structure.project.type`, `rs.structure.boq` (BOQ/cost) |
| **Operational / money** (used elsewhere) | `rs.installment` (42,940), `rs.contract` (1,437), `rs.reservation` (1,478), `rs.payment.plan`, `rs.account.payment.*`, `rs.discount`, `rs.penalty`, `rs.termination`, `rs.followup*` |

The three projects (label = `Project#…`, not PII):

| id | Project | Units |
|----|---------|------:|
| 1 | New Capital | 1,401 |
| 2 | Cassette | 334 |
| 3 | La puerta | 138 |

---

## 2. Area / Price / Status field map (from `fields_get` on `rs.structure.unit`, 175 fields)

### Status — `state` (the canonical availability field)

`state` is a **stored selection** with the sales lifecycle:

| value | label | live count |
|-------|-------|-----------:|
| `available` | Available | 419 |
| `initial` | Initial Reserve | 3 |
| `reserved` | Reserved | 46 |
| `contracted` | Contracted | 1,405 |
| `delivered` | Delivered | 0 |

- **100% of units have `state` set.** "Sold" = `contracted` (handover not yet started: `delivered` = 0).
- ⚠️ **`availability_status`** (lock/unlock/hidden) is a *separate* field and is **100% `unlock`** across all units — it is a UI lock flag, **not** a sales status. Do **not** use it. The real status is `state`.

### Area fields (all stored floats, m²)

| field | label | populated | notes |
|-------|-------|----------:|-------|
| `total_area` | Total Unit Area | **93.1%** | **the headline sellable area** — use this |
| `area` | Area | 93.1% | base built-up |
| `extra_area`, `outdoor_area`, `operation_area` | extra / outdoor / operation | partial | add-on areas (garden/roof/terrace) |
| `net_area` | Net Area | **7.4%** | ❌ mostly empty — **do not rely on `net_area`** |

### Price fields (stored monetary, EGP)

| field | label | populated | notes |
|-------|-------|----------:|-------|
| `amount` | Total Unit Price | **93.1%** | **headline list price** — use this |
| `sale_price` | Sale Price | 93.1% | ≈ `amount` in aggregate |
| `unit_amount` | Unit Price | 93.1% | ≈ `amount` |
| `official_price` | Official Price | 93.1% | registration/official value |
| `meter_price` | Meter Price | **99.9%** | **price per m²** (derived) |
| `avg_amount` | Average Price per Meter | — | computed |
| `amount_after_discount` | Price After Discount | — | post-discount |

Total price relates to area via `meter_price` (price/m²); `amount` is stored, not computed at read time. Cost-side fields also exist (`cost_entry_id → account.move`, BOQ lines, execution-cost accounts) but are out of scope for a board inventory view.

### Sample units (structural + area + price + status only — no PII)

| id | project / phase / zone / bldg | type | total_area | meter_price | amount (EGP) | state |
|----|-------------------------------|------|-----------:|------------:|-------------:|-------|
| 3440 | New Capital / Ph2 / Z1 / B1 | AG-Garden Apt | 220 | 21,290 | 3,300,000 | contracted |
| 3441 | New Capital / Ph2 / Z1 / B1 | AG-Garden Apt | 230 | 21,212 | 3,500,000 | contracted |
| 3442 | New Capital / Ph2 / Z1 / B1 | AVG-Garden Duplex | 315 | 20,691 | 5,586,500 | contracted |
| 3443 | New Capital / Ph2 / Z1 / B1 | AVG-Garden Duplex | 325 | 22,664 | 6,346,000 | contracted |
| 3444 | New Capital / Ph2 / Z1 / B1 | AF-Apartment | 190 | 14,500 | 2,755,000 | contracted |

---

## 3. DATA READINESS — the decision driver

### 3.1 Field completeness (of all 1,873 units)

| Field | Filled | % |
|-------|-------:|--:|
| `state` (status) | 1,873 | **100.0%** |
| structural links (project/phase/zone/building) | 1,873 | **100.0%** |
| `total_area` > 0 | 1,744 | 93.1% |
| `amount` (list price) > 0 | 1,743 | 93.1% |
| `sale_price` > 0 | 1,744 | 93.1% |
| `meter_price` > 0 | 1,872 | 99.9% |
| `net_area` > 0 | 138 | 7.4% ❌ |

The ~6.9% gap on area/price is **not random — it is almost entirely one project (La Puerta).**

### 3.2 Per-project completeness (the critical split)

| Project | Units | `total_area`>0 | `amount`>0 | `state` set | Verdict |
|---------|------:|---------------:|-----------:|------------:|---------|
| **New Capital** | 1,401 | 1,401 (100%) | 1,400 (99.9%) | 1,401 (100%) | ✅ Fully populated |
| **Cassette** | 334 | 334 (100%) | 334 (100%) | 334 (100%) | ✅ Fully populated |
| **La puerta** | 138 | 9 (6.5%) | 9 (6.5%) | 138 (100%) | ❌ Commercial data absent (status only) |

**New Capital + Cassette = 1,735 units (92.6% of all units, ~99% of all value) are 100% complete on area, price, and status.** La Puerta is a shell: all 138 units have a status (132 available, 5 contracted, 1 reserved) but only 9 carry area/price. This matches the known pattern (La Puerta also had **zero Collections**) — La Puerta is the transitional/not-yet-loaded project.

### 3.3 Migration / stability signals

| Signal | Evidence | Reading |
|--------|----------|---------|
| `create_date` | **1,871 of 1,873 created July 2025** (min 2025-07-08), 2 stragglers Apr/May 2026 | Single bulk import — not organic entry |
| `write_date` | **1,844 bulk-rewritten May 2026**, 29 in June 2026 | A second bulk pass (the area/price load / reprice) |

**Interpretation:** units were bulk-imported (Jul 2025) and then **bulk-repriced/edited (May 2026)**. Status is live and organic (it tracks real contracts — see §4). Area is structural and stable. **Price (`amount`) reflects *current list pricing as of the May 2026 reload*** — it is sound for "value of remaining inventory at today's prices," but it is **not** the historical contracted sale value for already-sold units (see the caveat in §5 and §6).

---

## 4. Linkage map — supply ↔ money (no PII)

The inventory side joins cleanly to the money side via a **direct `unit_id`** (no multi-hop needed):

```
rs.structure.unit (1,873)
   ├─ rs.reservation.unit_id   → 1,455 distinct units reserved   (1,478 reservations)
   ├─ rs.contract.unit_id      → 1,435 distinct units contracted (1,437 contracts)
   └─ rs.installment.unit_id   → present on all 42,940 installments  (+ project_id)
```

- Each of `rs.installment`, `rs.reservation`, `rs.contract` carries **both `unit_id` and `project_id`** → a sold unit's payment plan, reservation, and contract are all reachable in one hop.
- **Status cross-validates:** units with `state = contracted` (1,405) ≈ distinct units on contracts (1,435) ≈ total contracts (1,437). The `state` field is trustworthy.
- **Accounting bridge (Path C):** `account.move.project_id → rs.structure.project` exists, and `rs.structure.project.analytic_account_id → account.analytic.account`. So RE project ↔ accounting/analytic is wired for later P&L-by-project work.
- **Absorption dates** are available via the join, not on the unit: `rs.contract` has `date`, `reservation_date`, `delivery_date`; `rs.reservation` has `date`, `delivery_date`. The unit itself has no sale-date field.

This means "units sold" (inventory) can be cross-referenced with Collections / Customer Accounts (money) whenever needed.

---

## 5. Aggregate figures (current list pricing — indicative)

**By status (all 3 projects):**

| state | units | list value `amount` (EGP) | area (m²) |
|-------|------:|--------------------------:|----------:|
| available | 419 | 4,691,166,395 | 68,062 |
| reserved | 46 | 382,904,112 | 8,294 |
| initial | 3 | 14,447,500 | 525 |
| contracted (sold) | 1,405 | 6,369,680,761 | 287,150 |
| **TOTAL** | **1,873** | **≈ 11,458,198,768 (11.46bn)** | **364,031** |

**By project:**

| Project | units | list value `amount` (EGP) | area (m²) |
|---------|------:|--------------------------:|----------:|
| New Capital | 1,401 | 6,515,188,968 | 264,650 |
| Cassette | 334 | 4,829,121,800 | 98,818 |
| La puerta | 138 | 113,888,000 *(only 9 priced)* | 564 |

> ⚠️ **Caveat on "sold value":** the `amount` sum for `contracted` units is the **current list value of sold units**, because pricing was bulk-reloaded May 2026 — it is *not* the historically contracted revenue. For realized/contracted money, use the **Collections / Customer Accounts** side (`rs.installment` / contracts), not `unit.amount`. Present `unit.amount` sums as **"list / inventory value,"** never as recognized revenue.

---

## 6. Candidate board KPIs — feasibility

| KPI | Data supports now? | Caveat |
|-----|--------------------|--------|
| **Inventory count by status** (available/reserved/contracted), overall + per project/phase/zone | ✅ **YES — fully** | `state` 100% complete; drill-down via denormalised links. The cleanest, lowest-risk slice. |
| **Remaining (unsold) inventory value & area** at current pricing | ✅ **YES** (New Capital + Cassette) | Uses available+reserved `amount`/`total_area`. Exclude La Puerta. "Current list price" framing. |
| **Total / sold / remaining sellable area; avg price/m²** | ✅ **YES** (New Capital + Cassette) | `total_area` + `meter_price` 100% there. `net_area` unusable. |
| **Total list (gross development) value** | ✅ YES, with caveat | Label as list value, not revenue (see §5 caveat). |
| **"Sold value" = realized revenue** | ⚠️ **NO via inventory** | Must come from the money side (Collections), because `unit.amount` was repriced. |
| **Absorption over time** (units/value/area sold per period) | 🟡 **Later** | Feasible via `rs.contract.date` / `reservation_date` join; not in unit table. Adds one join — defer to a second slice. |
| Anything for **La Puerta** value/area | ❌ NO | Only 9/138 units priced. Status-only until data lands. |

---

## 7. Risks

1. **La Puerta is a shell** (status only, no area/price). Showing its `amount` total (113.9M from 9 units) next to New Capital/Cassette would understate it ~50×. **Mitigation:** exclude La Puerta from value KPIs or badge it "data pending."
2. **Repriced list value ≠ contracted revenue.** Never present `sum(unit.amount)` of sold units as revenue. **Mitigation:** label as "inventory list value at current prices"; route revenue questions to Collections.
3. **Transitional data.** Everything was bulk-loaded/repriced; figures will shift as La Verde finalises pricing. **Mitigation:** an "indicative — under review" banner, same posture as the Accounting discovery.
4. **`net_area` and `availability_status` are traps** (7.4% filled / 100% constant). **Mitigation:** use `total_area` and `state` exclusively.

---

## 8. Recommendation — **GO** (phased)

The inventory data is real, structurally complete, and well-linked. A board-facing Projects/Inventory module is **high-value and feasible now**, provided La Puerta is carved out and list-value framing is correct.

**MVP — smallest valuable first slice (lowest risk):**
> **Inventory & Availability.** Units by `state` (available / reserved / contracted), overall and drilled down by project → phase → zone → building. Backed by 100%-complete status + structural data on all 1,873 units. Zero pricing risk.

**Slice 2 (immediately after):**
> **Value & Area** for **New Capital + Cassette only** — remaining-inventory value, sold vs. available area, average price/m². La Puerta shown as "data pending." All figures badged "current list pricing — indicative."

**Slice 3 (defer):**
> **Absorption over time** via the `rs.contract` / `rs.reservation` date join. One extra hop; build once Slices 1–2 land.

**Before building, confirm with Khaled (3 items):**
1. La Puerta — exclude entirely, or include status-only with a "pricing pending" badge?
2. Is the May 2026 repricing the final list, or still moving? (Drives the "indicative" banner.)
3. For sold/realized value, agree the source is **Collections (money side)**, with this module owning **inventory list value** only — so the two modules never contradict each other.

**No code has been written. This is a go/no-go decision point.**
