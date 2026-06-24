"""
Projects Inventory — Inventory Data Quality service (read-only, ADMIN-only page).

An ongoing data-entry review tool (analogous to the CRM "Missing Contacts"): it
surfaces data-completeness defects across ALL three projects so they can be tracked
and fixed in Odoo. It NEVER writes — only reads (the shared units cache + one contracts
read + small parent-record reads). See docs/INVENTORY_DATA_QUALITY_DISCOVERY.md.

Three checks (numbers re-derived live by scripts/verify_inventory_data_quality_live.py
and asserted identity-equal; nothing is hardcoded here):

  A — Sold unit WITHOUT a contract  (defect_type "no_contract")
      sold = state ∈ SOLD_STATES; flagged = NO rs.contract with state != 'cancel'
      references it via unit_id. detail carries the unit's list `amount`.

  B — Broken hierarchy chain  (defect_type "phase_project" / "zone_phase" / "building_zone")
      Each unit denormalises its full chain (project_id, phase_id, zone_id, building_id).
      Reading each PARENT record's OWN upward m2o as the source of truth
      (rs.structure.phase.project_id, rs.structure.zone.phase_id,
      rs.structure.building.zone_id) we verify, per unit:
        phase_id→project == project_id ; zone_id→phase == phase_id ;
        building_id→zone == zone_id.
      A unit with ≥1 broken link is flagged ONCE; the first break in canonical chain
      order (phase→project, zone→phase, building→zone) names its defect_type. (Authoritative
      logic ported from the now-deleted scripts/audit_inventory_hierarchy.py.)

  C — Sold unit with NO list price  (defect_type "no_list_price")
      sold unit whose `amount` is 0 / falsy. A standing guard — 0 today.

  D — Implausible list price/m²  (separate `check_d` object, NOT in checks/total_issues)
      PRICED units (sold AND unsold) whose list price/m² (amount ÷ total_area) is
      implausibly high vs what comparable units actually realize — a list-price DATA
      ERROR to correct in Odoo. Scoped to New Capital + Cassette (La Puerta excluded);
      baselines come from SOLD units' realized price/m². Three deduped tiers (peer →
      type → impossible) — see domain.py and _check_d. The dominant target is the
      HS-Studio "65,000/m²" regime (studios realize ~20,000/m²).

Scope: Checks A/B/C cover all 3 projects; Check D is New Capital + Cassette only. La
Puerta's 129 unpriced AVAILABLE units are EXPECTED (early-stage), NEVER a defect — Check
C only looks at SOLD units, and Check D excludes La Puerta entirely.

READ-ONLY: _assert_read_only() runs at entry; only search_read is ever issued. Every
unmapped unit state is raised on (never silently dropped), mirroring Slice 1/2.
"""

import time
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from loguru import logger

from backend.core.exceptions import OdooQueryError, ReadOnlyViolationError
from backend.modules.projects_inventory.domain import (
    CONTRACT_CANCEL_STATE,
    CONTRACT_MODEL,
    CONTRACT_PAYMENT_TERM_FIELD,
    CONTRACT_PRICE_FIELD,
    CONTRACT_STATE_FIELD,
    CONTRACT_UNIT_FIELD,
    DQ_LIST_IMPOSSIBLE_K,
    DQ_LIST_TYPE_K,
    DQ_LIST_TYPE_SPREAD_MAX,
    OUTLIER_LIST_TRUST_K,
    OUTLIER_MIN_GROUP_SIZE,
    PAYMENT_TERM_DATE_FIELD,
    PAYMENT_TERM_MODEL,
    SOLD_STATES,
    UNIT_AMOUNT_FIELD,
    UNIT_AREA_FIELD,
    UNIT_METER_PRICE_FIELD,
    UNIT_TYPE_FIELD,
    VALUE_SCOPE_PROJECT_IDS,
    VINTAGE_BUCKET_YEARS,
)
from backend.modules.projects_inventory.services import cache as _cache
from backend.modules.projects_inventory.services.inventory_service import (
    _assert_read_only,
    _classify_states,
    _get_units_cached,
    _m2o,
)
from backend.shared.odoo.client import OdooClient

_CACHE_KEY_PREFIX = "projects_inventory:dq:overview"
_CONTRACTS_CACHE_KEY_PREFIX = "projects_inventory:dq:contracts"
_PARENTS_CACHE_KEY_PREFIX = "projects_inventory:dq:parents"   # per parent model
# Check D owns its OWN contracts/terms reads (it needs sales_price + payment_term, which
# Check A's contracts read does not carry), scoped to the NC + Cassette sold population.
_D_CONTRACTS_CACHE_KEY_PREFIX = "projects_inventory:dq:d:contracts"
_D_TERMS_CACHE_KEY_PREFIX = "projects_inventory:dq:d:terms"
_CAIRO_TZ = ZoneInfo("Africa/Cairo")
_CONTRACT_CHUNK = 200   # search_read sold-unit contracts in id chunks (matches Slice 2)

# Stable per-check keys (drive the template's localized section names + CSV file names).
CHECK_NO_CONTRACT = "no_contract"
CHECK_BROKEN_HIERARCHY = "broken_hierarchy"
CHECK_NO_LIST_PRICE = "no_list_price"
CHECK_IMPLAUSIBLE_LIST = "implausible_list_price"   # Check D

# Check D shown-signal vocabulary (precedence Tier 1 → Tier 2a → Tier 2b).
SIGNAL_PEER = "peer"          # Tier 1 — peer-group median realized price/m²
SIGNAL_TYPE = "type"          # Tier 2a — unit-type median realized price/m²
SIGNAL_IMPOSSIBLE = "impossible"  # Tier 2b — unit-type max realized price/m²

# Check D reads sales_price + payment_term off the contract (its own read).
_D_CONTRACT_FIELDS = [
    CONTRACT_UNIT_FIELD, CONTRACT_PRICE_FIELD, CONTRACT_STATE_FIELD,
    CONTRACT_PAYMENT_TERM_FIELD,
]

# Authoritative chain links, in CANONICAL order (the first break a unit has names its
# defect). Each tuple: (defect_type, unit child m2o, child kind word, parent MODEL,
# the parent record's OWN upward m2o, parent kind word, the unit field the parent's
# upward m2o must equal). Parent fields confirmed via fields_get 2026-06-19.
_CHAIN_LINKS: tuple[tuple[str, str, str, str, str, str, str], ...] = (
    ("phase_project", "phase_id", "phase", "rs.structure.phase", "project_id", "project", "project_id"),
    ("zone_phase", "zone_id", "zone", "rs.structure.zone", "phase_id", "phase", "phase_id"),
    ("building_zone", "building_id", "building", "rs.structure.building", "zone_id", "zone", "zone_id"),
)

# The valid defect_type vocabulary the schema/template rely on.
DEFECT_TYPES: frozenset[str] = frozenset(
    {"no_contract", "no_list_price", *(link[0] for link in _CHAIN_LINKS)}
)


def _num(value) -> float:
    """Coerce an Odoo monetary/float (or False for an empty value) to float."""
    return float(value) if isinstance(value, (int, float)) else 0.0


def _project_name(unit: dict) -> str:
    """The unit's project display name (from its denormalised project_id m2o)."""
    return _m2o(unit.get("project_id"))[1] or "—"


def _item(unit: dict, defect_type: str, detail: str) -> dict:
    """One structured flagged-unit row (matches schemas.DataQualityItem)."""
    if defect_type not in DEFECT_TYPES:
        raise RuntimeError(
            f"Refusing to emit a data-quality item with an unknown defect_type "
            f"{defect_type!r} (allowed: {sorted(DEFECT_TYPES)})."
        )
    return {
        "unit_id": unit["id"],
        "code": unit.get("code") or "",
        "project_name": _project_name(unit),
        "defect_type": defect_type,
        "detail": detail,
    }


def _sort_items(items: list[dict]) -> list[dict]:
    """Each check's items are sorted by project then code (stable, language-neutral)."""
    return sorted(items, key=lambda it: (it["project_name"], it["code"]))


async def _fetch_contracts(client: OdooClient, unit_ids: list[int]) -> list[dict]:
    """search_read rs.contract (unit_id, state) for the given (sold) unit ids, in id
    chunks. Read-only — mirrors value_service's chunked contract fetch."""
    rows: list[dict] = []
    for i in range(0, len(unit_ids), _CONTRACT_CHUNK):
        chunk = unit_ids[i:i + _CONTRACT_CHUNK]
        part = await client.execute_kw(
            CONTRACT_MODEL,
            "search_read",
            args=[[(CONTRACT_UNIT_FIELD, "in", chunk)]],
            kwargs={"fields": [CONTRACT_UNIT_FIELD, CONTRACT_STATE_FIELD]},
        )
        rows.extend(part)
    return rows


async def _get_contracts_cached(client: OdooClient, unit_ids: list[int]) -> list[dict]:
    """Cached (60s TTL, per Cairo date) read of the SOLD-unit contracts. The sold-unit id
    set is deterministic from the shared units cache, so a fixed key is safe."""
    cache_key = _cache.make_key(_CONTRACTS_CACHE_KEY_PREFIX)
    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"DQ contracts cache hit: {cache_key}")
        return cached
    logger.info(f"DQ contracts cache miss: {cache_key} — querying Odoo")
    rows = await _fetch_contracts(client, unit_ids)
    _cache.set(cache_key, rows)
    return rows


async def _fetch_parent_map(client: OdooClient, model: str, parent_field: str) -> dict:
    """search_read every record of `model` → {id: parent_id} from its OWN upward m2o
    `parent_field` (the source of truth). Read-only, one query per parent model."""
    rows = await client.execute_kw(
        model,
        "search_read",
        args=[[]],
        kwargs={"fields": ["id", parent_field], "order": "id"},
    )
    return {int(r["id"]): _m2o(r.get(parent_field))[0] for r in rows}


async def _get_parent_map_cached(client: OdooClient, model: str, parent_field: str) -> dict:
    """Cached (60s TTL) parent map for one chain link, keyed by model name."""
    cache_key = _cache.make_key(f"{_PARENTS_CACHE_KEY_PREFIX}:{model}")
    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"DQ parents cache hit: {cache_key}")
        return cached
    logger.info(f"DQ parents cache miss: {cache_key} — querying Odoo")
    pmap = await _fetch_parent_map(client, model, parent_field)
    _cache.set(cache_key, pmap)
    return pmap


async def _fetch_d_contracts(client: OdooClient, unit_ids: list[int]) -> list[dict]:
    """search_read rs.contract (unit_id, sales_price, state, payment_term_id) for the given
    sold unit ids (NC + Cassette), in id chunks. Read-only — mirrors the Slice 2.5 fetch.
    Check A's contracts read carries only (unit_id, state); Check D needs the realized
    price + the payment term that resolves the sale date, so it owns this separate read."""
    rows: list[dict] = []
    for i in range(0, len(unit_ids), _CONTRACT_CHUNK):
        chunk = unit_ids[i:i + _CONTRACT_CHUNK]
        part = await client.execute_kw(
            CONTRACT_MODEL,
            "search_read",
            args=[[(CONTRACT_UNIT_FIELD, "in", chunk)]],
            kwargs={"fields": _D_CONTRACT_FIELDS},
        )
        rows.extend(part)
    return rows


async def _get_d_contracts_cached(client: OdooClient, unit_ids: list[int]) -> list[dict]:
    """Cached (60s TTL) read of the NC + Cassette sold-unit contracts for Check D. The
    scoped sold-unit id set is deterministic from the shared units cache, so a fixed key
    is safe."""
    cache_key = _cache.make_key(_D_CONTRACTS_CACHE_KEY_PREFIX)
    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"DQ-D contracts cache hit: {cache_key}")
        return cached
    logger.info(f"DQ-D contracts cache miss: {cache_key} — querying Odoo")
    rows = await _fetch_d_contracts(client, unit_ids)
    _cache.set(cache_key, rows)
    return rows


async def _fetch_terms(client: OdooClient, term_ids: list[int]) -> dict[int, str]:
    """search_read rs.payment.term (id, contract_date) → {term_id: 'YYYY-MM-DD'}, in id
    chunks. contract_date is the TRUE sale date (Slice 2.5). Read-only."""
    out: dict[int, str] = {}
    for i in range(0, len(term_ids), _CONTRACT_CHUNK):
        chunk = term_ids[i:i + _CONTRACT_CHUNK]
        part = await client.execute_kw(
            PAYMENT_TERM_MODEL,
            "search_read",
            args=[[("id", "in", chunk)]],
            kwargs={"fields": ["id", PAYMENT_TERM_DATE_FIELD]},
        )
        for r in part:
            d = r.get(PAYMENT_TERM_DATE_FIELD)
            if d:
                out[int(r["id"])] = str(d)[:10]
    return out


async def _get_terms_cached(client: OdooClient, term_ids: list[int]) -> dict[int, str]:
    """Cached (60s TTL) payment-term → contract_date map for Check D. The referenced term
    id set is deterministic from the contracts read, so a fixed key is safe."""
    cache_key = _cache.make_key(_D_TERMS_CACHE_KEY_PREFIX)
    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"DQ-D terms cache hit: {cache_key}")
        return cached
    logger.info(f"DQ-D terms cache miss: {cache_key} — querying Odoo")
    tmap = await _fetch_terms(client, term_ids)
    _cache.set(cache_key, tmap)
    return tmap


def _check_no_contract(sold: list[dict], covered: set[int]) -> list[dict]:
    """Check A — sold units with no non-cancel contract. detail carries the list amount."""
    items: list[dict] = []
    for u in sold:
        if u["id"] in covered:
            continue
        amount = _num(u.get(UNIT_AMOUNT_FIELD))
        items.append(_item(u, "no_contract", f"amount {amount:,.0f}"))
    return _sort_items(items)


def _check_broken_hierarchy(units: list[dict], parent_maps: dict[str, dict]) -> list[dict]:
    """Check B — broken hierarchy chain (AUTHORITATIVE: the parent record is the source of
    truth). Each unit is flagged ONCE; the FIRST break in canonical chain order names its
    defect_type + detail. Ported from scripts/audit_inventory_hierarchy.py (Part 1)."""
    items: list[dict] = []
    for u in units:
        for defect_type, child_field, child_kind, _model, _pfield, parent_kind, unit_field in _CHAIN_LINKS:
            cid, cname = _m2o(u.get(child_field))
            claimed_id = _m2o(u.get(unit_field))[0]
            if cid is None:
                items.append(_item(u, defect_type, f"unit has no {child_field}"))
                break
            pmap = parent_maps[defect_type]
            if cid not in pmap:
                items.append(_item(
                    u, defect_type,
                    f"{child_kind} {cid} {cname!r} → {parent_kind} (missing parent record); "
                    f"unit {unit_field}={claimed_id}",
                ))
                break
            actual_parent = pmap[cid]
            if actual_parent != claimed_id:
                items.append(_item(
                    u, defect_type,
                    f"{child_kind} {cid} {cname!r} → {parent_kind} {actual_parent}; "
                    f"unit {unit_field}={claimed_id}",
                ))
                break   # first break wins — one item per flagged unit
    return _sort_items(items)


def _check_no_list_price(sold: list[dict]) -> list[dict]:
    """Check C — sold units whose list `amount` is 0 / falsy. detail is empty (the unit
    code + project say it all). La Puerta's unpriced AVAILABLE units are NOT sold, so they
    can never appear here."""
    items = [_item(u, "no_list_price", "") for u in sold if not _num(u.get(UNIT_AMOUNT_FIELD))]
    return _sort_items(items)


def _check(key: str, items: list[dict]) -> dict:
    """Materialise one DataQualityCheck dict."""
    return {"key": key, "count": len(items), "items": items}


# ── Check D — implausible list price/m² (NC + Cassette; read-only) ─────────────


def _c2(value: float) -> float:
    """Round to cents — the deterministic basis the service and the live verify share, so
    a tier threshold never flips on float noise (mirrors pricing_outliers_service)."""
    return round(value, 2)


def _vintage_bucket(year: int) -> int:
    """2-year bucket floor (2022 & 2023 → 2022) — the SAME bucketing Slice 2.5 uses."""
    return (year // VINTAGE_BUCKET_YEARS) * VINTAGE_BUCKET_YEARS


def _quantile(sorted_vals: list[float], q: float) -> float:
    """Inclusive linear quantile (numpy 'linear') — identical to pricing_outliers_service
    and the live verify, so baselines match to the bit."""
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    pos = (n - 1) * q
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def _median(vals: list[float]) -> float:
    """Median via the inclusive quantile (raw, un-rounded — the comparison basis)."""
    return _quantile(sorted(vals), 0.5)


def _realized_and_terms(
    contract_rows: list[dict],
) -> tuple[dict[int, float], dict[int, set[int]]]:
    """From the non-cancel contracts, build realized[unit_id] = Σ sales_price and
    term_ids[unit_id] = set of payment_term_ids (for the sale date). A unit appears iff it
    has ≥1 non-cancel contract (mirrors pricing_outliers_service._realized_and_terms)."""
    realized: dict[int, float] = {}
    term_ids: dict[int, set[int]] = {}
    for ct in contract_rows:
        if ct.get(CONTRACT_STATE_FIELD) == CONTRACT_CANCEL_STATE:
            continue
        uid = _m2o(ct.get(CONTRACT_UNIT_FIELD))[0]
        if uid is None:
            continue
        realized[uid] = realized.get(uid, 0.0) + _num(ct.get(CONTRACT_PRICE_FIELD))
        ptid = _m2o(ct.get(CONTRACT_PAYMENT_TERM_FIELD))[0]
        if ptid is not None:
            term_ids.setdefault(uid, set()).add(ptid)
    return realized, term_ids


def _sale_date_for_unit(
    unit_term_ids: set[int], term_dates: dict[int, str]
) -> Optional[str]:
    """The unit's sale date = the EARLIEST contract_date across its non-cancel contracts'
    payment terms (they agree live; min is the deterministic tie-break). None if no term
    resolves to a date."""
    dates = [term_dates[t] for t in unit_term_ids if t in term_dates]
    return min(dates) if dates else None


def _check_d(
    scope_units: list[dict],
    realized: dict[int, float],
    term_ids: dict[int, set[int]],
    term_dates: dict[int, str],
) -> dict:
    """Check D — flag PRICED units whose list price/m² is implausibly high vs comparable
    realized prices (NC + Cassette only). Returns a DataQualityListPriceCheck dict.

    `scope_units` are the units already filtered to VALUE_SCOPE_PROJECT_IDS and
    state-classified. Baselines are built from SOLD units (those with realized value).
    Tier 2a/2b are CURRENT-ERA aware [2026-06-24]: the type baseline is keyed by
    (unit-type, 2-yr vintage bucket), and a unit is scored against its OWN sale-period
    bucket if sold, else the type's LATEST qualifying bucket (no all-history fallback;
    Option A → unevaluable when the chosen bucket has no baseline). Tier 1 (peer) is
    UNCHANGED. The three tiers are deduped with precedence peer → type → impossible.
    """
    # 1) Sold realized population (sold + area>0 + realized) → realized_pm2 + vintage.
    sold_pop: list[dict] = []
    for u in scope_units:
        if u["state"] not in SOLD_STATES:
            continue
        area = _num(u.get(UNIT_AREA_FIELD))
        if area <= 0:
            continue
        uid = u["id"]
        if uid not in realized:
            continue   # sold but no non-cancel contract → no realized price/m²
        sale_date = _sale_date_for_unit(term_ids.get(uid, set()), term_dates)
        bucket = _vintage_bucket(int(sale_date[:4])) if sale_date else None
        sold_pop.append({
            "zone_id": _m2o(u.get("zone_id"))[0],
            "unit_type_id": _m2o(u.get(UNIT_TYPE_FIELD))[0],
            "vintage_bucket": bucket,
            "realized_pm2": _c2(_c2(realized[uid]) / area),
        })

    # 2) Peer baseline — (zone, unit-type, 2-yr vintage) groups with ≥ MIN_GROUP_SIZE sold
    #    members; store the MEDIAN realized price/m² (raw — the Tier-1 comparison basis).
    peer_vals: dict[tuple, list[float]] = {}
    for m in sold_pop:
        if m["vintage_bucket"] is None:
            continue
        key = (m["zone_id"], m["unit_type_id"], m["vintage_bucket"])
        peer_vals.setdefault(key, []).append(m["realized_pm2"])
    peer_median: dict[tuple, float] = {}
    for key, vals in peer_vals.items():
        if len(vals) >= OUTLIER_MIN_GROUP_SIZE:
            med = _median(vals)
            if med > 0:
                peer_median[key] = med

    # 3) Type baseline — CURRENT-ERA aware [2026-06-24]. Per (unit-type, 2-yr vintage bucket)
    #    with ≥ MIN_GROUP_SIZE sold members: median, max and spread (= max / median). This
    #    REPLACES the prior all-history per-type baseline, which benchmarked today's price
    #    lists against a median polluted by cheap 2018-2021 sales (Egyptian price/m² escalated
    #    ~6× across 2018-2025 — discovery commit 611261f). A unit is scored against ONE bucket
    #    (step 4): its OWN sale-period bucket if sold; the type's LATEST qualifying bucket if
    #    unsold (a present-day list deserves a current-era benchmark).
    type_bucket_vals: dict[tuple, list[float]] = {}
    for m in sold_pop:
        if m["vintage_bucket"] is None:
            continue   # sold but unresolvable sale date → cannot place in a vintage bucket
        type_bucket_vals.setdefault(
            (m["unit_type_id"], m["vintage_bucket"]), []).append(m["realized_pm2"])
    type_baseline: dict[tuple, dict] = {}
    for key, vals in type_bucket_vals.items():
        if len(vals) >= OUTLIER_MIN_GROUP_SIZE:
            med = _median(vals)
            mx = max(vals)
            if med > 0:
                type_baseline[key] = {"median": med, "max": mx, "spread": mx / med}
    # The type's LATEST vintage bucket that has a baseline — the current-era benchmark for an
    # UNSOLD unit, whose list price is a present-day asking price with no sale period.
    type_latest_bucket: dict[Optional[int], int] = {}
    for (tid, bucket) in type_baseline:
        if tid not in type_latest_bucket or bucket > type_latest_bucket[tid]:
            type_latest_bucket[tid] = bucket

    # 4) Evaluate PRICED units (amount>0 & area>0), sold + unsold. A unit is flagged if any
    #    tier fires; the shown signal follows precedence peer → type → impossible.
    rows: list[dict] = []
    tier1 = tier2a = tier2b = 0
    evaluated = 0
    unevaluable = 0
    for u in scope_units:
        area = _num(u.get(UNIT_AREA_FIELD))
        amount = _num(u.get(UNIT_AMOUNT_FIELD))
        if amount <= 0 or area <= 0:
            continue
        evaluated += 1
        uid = u["id"]
        is_sold = u["state"] in SOLD_STATES
        zone_id = _m2o(u.get("zone_id"))[0]
        type_id, type_name = _m2o(u.get(UNIT_TYPE_FIELD))
        list_pm2 = _c2(amount / area)

        # Tier-1 anchor: the unit's eligible peer-group median (sold + resolvable vintage).
        # UNCHANGED — Tier 1 is already vintage-aware; own_bucket also drives the Tier-2 pick.
        peer_anchor: Optional[float] = None
        own_bucket: Optional[int] = None
        if is_sold and uid in realized:
            sale_date = _sale_date_for_unit(term_ids.get(uid, set()), term_dates)
            if sale_date is not None:
                own_bucket = _vintage_bucket(int(sale_date[:4]))
                peer_anchor = peer_median.get((zone_id, type_id, own_bucket))  # None if not eligible

        # Tier-2 baseline bucket (current-era): a SOLD unit scores against its OWN sale-period
        # bucket; an UNSOLD (or sold-without-date) unit against the type's LATEST qualifying
        # bucket. No all-history fallback (Option A) — if the chosen bucket has no qualifying
        # baseline, the unit is UNEVALUABLE under Tier 2.
        chosen_bucket = own_bucket if own_bucket is not None else type_latest_bucket.get(type_id)
        tb = type_baseline.get((type_id, chosen_bucket)) if chosen_bucket is not None else None

        # Unevaluable: no eligible peer group AND no unit-type baseline (counted, not flagged).
        if peer_anchor is None and tb is None:
            unevaluable += 1
            continue

        fires_t1 = peer_anchor is not None and list_pm2 > OUTLIER_LIST_TRUST_K * peer_anchor
        fires_t2a = (
            tb is not None
            and tb["spread"] < DQ_LIST_TYPE_SPREAD_MAX
            and list_pm2 > DQ_LIST_TYPE_K * tb["median"]
        )
        fires_t2b = tb is not None and list_pm2 > DQ_LIST_IMPOSSIBLE_K * tb["max"]
        if not (fires_t1 or fires_t2a or fires_t2b):
            continue

        if fires_t1:
            signal, anchor = SIGNAL_PEER, peer_anchor
            tier1 += 1
        elif fires_t2a:
            signal, anchor = SIGNAL_TYPE, tb["median"]
            tier2a += 1
        else:
            signal, anchor = SIGNAL_IMPOSSIBLE, tb["max"]
            tier2b += 1

        rows.append({
            "unit_id": uid,
            "code": u.get("code") or "",
            "project_name": _project_name(u),
            "unit_type_name": (type_name or "—"),
            "state": "sold" if is_sold else "unsold",
            "list_pm2": list_pm2,
            "meter_price": _c2(_num(u.get(UNIT_METER_PRICE_FIELD))),
            "anchor_realized_pm2": _c2(anchor),
            "ratio": _c2(list_pm2 / anchor),
            "list_total": _c2(amount),
            "signal": signal,
        })

    # Sort by ratio desc (largest over-list first), stable tie-break on code.
    rows.sort(key=lambda r: (-r["ratio"], r["code"]))

    if tier1 + tier2a + tier2b != len(rows):
        raise RuntimeError(
            f"Check D reconciliation FAILED: per-tier {tier1}+{tier2a}+{tier2b} "
            f"!= flagged rows {len(rows)}."
        )

    return {
        "key": CHECK_IMPLAUSIBLE_LIST,
        "count": len(rows),
        "items": rows,
        "tier1_count": tier1,
        "tier2a_count": tier2a,
        "tier2b_count": tier2b,
        "evaluated_count": evaluated,
        "unevaluable_count": unevaluable,
        "thresholds": {
            "list_trust_k": OUTLIER_LIST_TRUST_K,
            "type_k": DQ_LIST_TYPE_K,
            "type_spread_max": DQ_LIST_TYPE_SPREAD_MAX,
            "impossible_k": DQ_LIST_IMPOSSIBLE_K,
            "min_group_size": OUTLIER_MIN_GROUP_SIZE,
        },
    }


async def get_data_quality_overview(client: Optional[OdooClient] = None) -> dict:
    """Return the Inventory Data Quality overview — Checks A, B and C across all projects,
    plus Check D (implausible list price/m², New Capital + Cassette only).

    Args:
        client: optional injected OdooClient (tests pass a mock; production opens and
            closes its own).

    Returns a dict matching schemas.DataQualityOverview.

    Raises:
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated.
        OdooQueryError: if the Odoo RPC fails.
        RuntimeError: on an unmapped unit state, an unknown defect_type, or a total
            reconciliation failure (explicit raises so they survive python -O).
    """
    _assert_read_only()

    cairo_today = datetime.now(_CAIRO_TZ).date()
    cache_key = _cache.make_key(_CACHE_KEY_PREFIX)
    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"DQ cache hit: {cache_key}")
        return {**cached, "cache_status": "cached", "rpc_duration_ms": 0}
    logger.info(f"DQ cache miss: {cache_key} — querying Odoo")

    _client = client if client is not None else OdooClient()

    t0 = time.monotonic()
    try:
        units = await _get_units_cached(_client)

        # Every unit's state must map to a known bucket before we classify anything sold.
        _classify_states(units)

        sold = [u for u in units if u["state"] in SOLD_STATES]
        sold_ids = sorted(u["id"] for u in sold)

        # Check A inputs — non-cancel contracts covering each sold unit.
        contract_rows = await _get_contracts_cached(_client, sold_ids)

        # Check B inputs — one parent map per chain link (each parent record's own upward m2o).
        parent_maps: dict[str, dict] = {}
        for defect_type, _cf, _ck, model, parent_field, _pk, _uf in _CHAIN_LINKS:
            parent_maps[defect_type] = await _get_parent_map_cached(_client, model, parent_field)

        # Check D inputs — realized value + sale-date (vintage) for the SOLD units in the
        # NC + Cassette scope (La Puerta excluded), via Check D's own contracts/terms reads.
        d_scope_units = [
            u for u in units if _m2o(u.get("project_id"))[0] in VALUE_SCOPE_PROJECT_IDS
        ]
        d_sold_ids = sorted(u["id"] for u in d_scope_units if u["state"] in SOLD_STATES)
        d_contract_rows = await _get_d_contracts_cached(_client, d_sold_ids)
        d_realized, d_term_ids = _realized_and_terms(d_contract_rows)
        d_referenced_terms = sorted({t for ts in d_term_ids.values() for t in ts})
        d_term_dates = await _get_terms_cached(_client, d_referenced_terms)
    except (ReadOnlyViolationError, RuntimeError):
        raise
    except Exception as exc:
        raise OdooQueryError(f"get_data_quality_overview() RPC failed: {exc}") from exc
    finally:
        if client is None:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)

    # Check A — covered = sold units referenced by ≥1 non-cancel contract.
    covered: set[int] = set()
    for ct in contract_rows:
        if ct.get(CONTRACT_STATE_FIELD) == CONTRACT_CANCEL_STATE:
            continue
        uid = _m2o(ct.get(CONTRACT_UNIT_FIELD))[0]
        if uid is not None:
            covered.add(uid)

    checks = [
        _check(CHECK_NO_CONTRACT, _check_no_contract(sold, covered)),
        _check(CHECK_BROKEN_HIERARCHY, _check_broken_hierarchy(units, parent_maps)),
        _check(CHECK_NO_LIST_PRICE, _check_no_list_price(sold)),
    ]

    total_issues = sum(c["count"] for c in checks)
    # Reconciliation: total == Σ per-check counts == Σ items emitted (explicit raise).
    items_emitted = sum(len(c["items"]) for c in checks)
    if total_issues != items_emitted:
        raise RuntimeError(
            f"Data-quality reconciliation FAILED: total_issues {total_issues} != "
            f"Σ items emitted {items_emitted}."
        )

    # Check D — implausible list price/m² (NC + Cassette only). A SEPARATE object on the
    # response; it is NOT folded into checks/total_issues (those stay A/B/C completeness
    # defects). _check_d does its own per-tier reconciliation.
    check_d = _check_d(d_scope_units, d_realized, d_term_ids, d_term_dates)

    logger.info(
        f"Inventory data quality: {total_issues} issue(s) across {len(units):,} units | "
        f"A(no_contract)={checks[0]['count']} B(broken_hierarchy)={checks[1]['count']} "
        f"C(no_list_price)={checks[2]['count']} | D(implausible_list)={check_d['count']} "
        f"(peer {check_d['tier1_count']}/type {check_d['tier2a_count']}/impossible "
        f"{check_d['tier2b_count']}, {check_d['unevaluable_count']} unevaluable of "
        f"{check_d['evaluated_count']} priced) | RPC {rpc_ms}ms | cache_key={cache_key}"
    )

    result: dict = {
        "checks": checks,
        "total_issues": total_issues,
        "check_d": check_d,
        "reference_date": cairo_today.isoformat(),
        "as_of": datetime.now(timezone.utc).isoformat(),
        "cache_status": "fresh",
        "rpc_duration_ms": rpc_ms,
    }

    _cache.set(cache_key, result)
    return result
