"""
Projects Inventory — Pricing Outliers service (Slice 2.5, read-only).

Board-facing intelligence for New Capital + Cassette ONLY (La Puerta excluded — its
`amount` pricing is incomplete). Surfaces SOLD units priced/sold anomalously, using two
VINTAGE-CONTROLLED signals. Realized price = rs.contract.sales_price (the CONTRACTED
deal value, == installments_total); list price = rs.structure.unit.amount. See
docs/PROJECTS_INVENTORY_PRICING_DISCOVERY.md §5.

In-scope population (LOCKED): rs.structure.unit with project_id ∈ VALUE_SCOPE_PROJECT_IDS,
state ∈ SOLD_STATES, total_area > 0, having ≥1 non-cancel rs.contract, AND a resolvable
sale date. The handful of sold-no-contract units are naturally excluded (no realized
value, no sale date). Per unit:
  realized_total = Σ sales_price over the unit's NON-cancel contracts.
  realized_pm2   = realized_total / total_area   (rounded to cents — the deterministic
                   basis every comparison and the live verify share, so flags never flip
                   on float noise).
  list_total     = unit.amount.
  discount_pct   = (amount − realized_total) / amount * 100   (only when amount > 0).
  SALE DATE / vintage: unit → non-cancel rs.contract.payment_term_id →
                   rs.payment.term.contract_date. vintage_year = its year;
                   vintage_bucket = (year // VINTAGE_BUCKET_YEARS) * VINTAGE_BUCKET_YEARS;
                   label e.g. "2022–2023". (The contract's own reservation_date/create_date
                   are migration stamps — NEVER used.) If a unit's non-cancel contracts
                   carry more than one dated term, the EARLIEST is the sale date (they
                   agree live; min is the deterministic tie-break).

SECTION A — peer realized price/m² outliers (vintage-controlled):
  Peer key = (zone_id, unit_type_id, vintage_bucket). Groups with ≥ MIN_GROUP_SIZE
  in-scope units are eligible; smaller groups are NOT evaluated (their units are counted
  as "insufficient peers" for a footnote). Within each eligible group compute median, Q1,
  Q3, IQR of realized_pm2 (inclusive linear quantiles). FLAG a unit iff BOTH:
    (i)  realized_pm2 < Q1 − IQR_MULT·IQR  OR  realized_pm2 > Q3 + IQR_MULT·IQR (Tukey), AND
    (ii) |realized_pm2 − median| / median * 100 ≥ MIN_DEV_PCT.
  direction = "below" under the lower fence, "above" over the upper fence.

SECTION B — discount outliers vs the unit's OWN list (amount > 0):
  FLAG "deep" if discount_pct ≥ DEEP_DISCOUNT_PCT; FLAG "premium" (sold above own list)
  if discount_pct ≤ PREMIUM_PCT.

CONFIRMED: a unit flagged in BOTH sections is high-confidence — marked in both and counted.

READ-ONLY: _assert_read_only() runs at entry; only search_read is issued. No method ever
writes. Every count is independently recomputed live by
scripts/verify_pricing_outliers_live.py and asserted identity-equal; nothing is hardcoded.
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
    OUTLIER_DEEP_DISCOUNT_PCT,
    OUTLIER_IQR_MULT,
    OUTLIER_MIN_DEV_PCT,
    OUTLIER_MIN_GROUP_SIZE,
    OUTLIER_PREMIUM_PCT,
    PAYMENT_TERM_DATE_FIELD,
    PAYMENT_TERM_MODEL,
    SOLD_STATES,
    UNIT_AMOUNT_FIELD,
    UNIT_AREA_FIELD,
    UNIT_TYPE_FIELD,
    VALUE_EXCLUDED_PROJECT_IDS,
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

_CACHE_KEY_PREFIX = "projects_inventory:outliers:overview"
_CONTRACTS_CACHE_KEY_PREFIX = "projects_inventory:outliers:contracts"
_TERMS_CACHE_KEY_PREFIX = "projects_inventory:outliers:terms"
_CAIRO_TZ = ZoneInfo("Africa/Cairo")
_CONTRACT_CHUNK = 200   # search_read sold-unit contracts in id chunks (matches Slice 2)

# This service reads payment_term_id off the contract too (Slice 2's contracts cache does
# not carry it), so it owns a separate contracts read keyed under its own prefix.
_CONTRACT_FIELDS = [
    CONTRACT_UNIT_FIELD, CONTRACT_PRICE_FIELD, CONTRACT_STATE_FIELD,
    CONTRACT_PAYMENT_TERM_FIELD,
]

# Stable section/direction/kind vocabularies the schema + template rely on.
DIRECTION_BELOW = "below"
DIRECTION_ABOVE = "above"
KIND_DEEP = "deep"
KIND_PREMIUM = "premium"


def _num(value) -> float:
    """Coerce an Odoo monetary/float (or False for an empty value) to float."""
    return float(value) if isinstance(value, (int, float)) else 0.0


def _c2(value: float) -> float:
    """Round to cents — the deterministic basis the service and the live verify share, so
    a Tukey/threshold test never flips on float noise."""
    return round(value, 2)


def _vintage_bucket(year: int) -> int:
    """2-year bucket floor: 2022 & 2023 → 2022."""
    return (year // VINTAGE_BUCKET_YEARS) * VINTAGE_BUCKET_YEARS


def _bucket_label(bucket: int) -> str:
    """Render a bucket as an inclusive range label, e.g. 2022 → '2022–2023' (en dash)."""
    return f"{bucket}–{bucket + VINTAGE_BUCKET_YEARS - 1}"


def _quantile(sorted_vals: list[float], q: float) -> float:
    """Inclusive linear quantile (numpy 'linear' / statistics 'inclusive'): position
    (n-1)*q into the sorted values, linearly interpolated. Deterministic — the live verify
    reimplements the identical formula so the fences match to the bit."""
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    pos = (n - 1) * q
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


async def _fetch_contracts(client: OdooClient, unit_ids: list[int]) -> list[dict]:
    """search_read rs.contract (unit_id, sales_price, state, payment_term_id) for the given
    sold unit ids, in id chunks. Read-only — mirrors value_service's chunked fetch."""
    rows: list[dict] = []
    for i in range(0, len(unit_ids), _CONTRACT_CHUNK):
        chunk = unit_ids[i:i + _CONTRACT_CHUNK]
        part = await client.execute_kw(
            CONTRACT_MODEL,
            "search_read",
            args=[[(CONTRACT_UNIT_FIELD, "in", chunk)]],
            kwargs={"fields": _CONTRACT_FIELDS},
        )
        rows.extend(part)
    return rows


async def _get_contracts_cached(client: OdooClient, unit_ids: list[int]) -> list[dict]:
    """Cached (60s TTL, per Cairo date) read of the scope's sold-unit contracts. The
    sold-unit id set is deterministic from the shared units cache, so a fixed key is safe."""
    cache_key = _cache.make_key(_CONTRACTS_CACHE_KEY_PREFIX)
    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"Outliers contracts cache hit: {cache_key}")
        return cached
    logger.info(f"Outliers contracts cache miss: {cache_key} — querying Odoo")
    rows = await _fetch_contracts(client, unit_ids)
    _cache.set(cache_key, rows)
    return rows


async def _fetch_terms(client: OdooClient, term_ids: list[int]) -> dict[int, str]:
    """search_read rs.payment.term (id, contract_date) → {term_id: 'YYYY-MM-DD'}, in id
    chunks. The contract_date is the TRUE sale date. Read-only."""
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
    """Cached (60s TTL) payment-term → contract_date map. The referenced term id set is
    deterministic from the contracts read, so a fixed key is safe."""
    cache_key = _cache.make_key(_TERMS_CACHE_KEY_PREFIX)
    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"Outliers terms cache hit: {cache_key}")
        return cached
    logger.info(f"Outliers terms cache miss: {cache_key} — querying Odoo")
    tmap = await _fetch_terms(client, term_ids)
    _cache.set(cache_key, tmap)
    return tmap


def _realized_and_terms(
    contract_rows: list[dict],
) -> tuple[dict[int, float], dict[int, set[int]]]:
    """From the non-cancel contracts, build:
      realized[unit_id]  = Σ sales_price over that unit's NON-cancel contracts.
      term_ids[unit_id]  = the set of payment_term_ids on those contracts (for the sale date).
    A unit appears iff it has ≥1 non-cancel contract (the realized-price / coverage signal)."""
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
    resolves to a date — such a unit is dropped from the population."""
    dates = [term_dates[t] for t in unit_term_ids if t in term_dates]
    return min(dates) if dates else None


def _build_population(
    scope_units: list[dict],
    realized: dict[int, float],
    term_ids: dict[int, set[int]],
    term_dates: dict[int, str],
) -> list[dict]:
    """Materialise the in-scope population: sold + area>0 + ≥1 non-cancel contract +
    resolvable sale date. Each entry carries the derived per-unit fields."""
    population: list[dict] = []
    for u in scope_units:
        if u["state"] not in SOLD_STATES:
            continue
        area = _num(u.get(UNIT_AREA_FIELD))
        if area <= 0:
            continue
        uid = u["id"]
        if uid not in realized:
            continue   # no non-cancel contract → no realized value
        sale_date = _sale_date_for_unit(term_ids.get(uid, set()), term_dates)
        if sale_date is None:
            continue   # unresolvable sale date → out of scope
        realized_total = _c2(realized[uid])
        amount = _num(u.get(UNIT_AMOUNT_FIELD))
        zone_id, zone_name = _m2o(u.get("zone_id"))
        type_id, type_name = _m2o(u.get(UNIT_TYPE_FIELD))
        proj_id, proj_name = _m2o(u.get("project_id"))
        vintage_year = int(sale_date[:4])
        bucket = _vintage_bucket(vintage_year)
        discount_pct = (
            _c2((amount - realized_total) / amount * 100.0) if amount > 0 else None
        )
        population.append({
            "unit_id": uid,
            "code": u.get("code") or "",
            "project_id": proj_id if proj_id is not None else 0,
            "project_name": (proj_name or "—").strip() or "—",
            "zone_id": zone_id,
            "zone_name": (zone_name or "—").strip() or "—",
            "unit_type_id": type_id,
            "unit_type_name": (type_name or "—").strip() or "—",
            "sale_date": sale_date,
            "vintage_year": vintage_year,
            "vintage_bucket": bucket,
            "vintage_bucket_label": _bucket_label(bucket),
            "realized_total": realized_total,
            "list_total": _c2(amount),
            "realized_pm2": _c2(realized_total / area),
            "discount_pct": discount_pct,
        })
    return population


def _section_a(population: list[dict]) -> tuple[list[dict], dict[int, dict], int, int]:
    """Section A — peer realized price/m² outliers. Returns
    (flagged_rows, flag_by_unit, insufficient_peers_count, eligible_group_count).
    flag_by_unit maps unit_id → {"direction", "deviation_pct"} for the confirmed-join."""
    groups: dict[tuple, list[dict]] = {}
    for u in population:
        key = (u["zone_id"], u["unit_type_id"], u["vintage_bucket"])
        groups.setdefault(key, []).append(u)

    rows: list[dict] = []
    flag_by_unit: dict[int, dict] = {}
    insufficient = 0
    eligible = 0
    for members in groups.values():
        if len(members) < OUTLIER_MIN_GROUP_SIZE:
            insufficient += len(members)
            continue
        eligible += 1
        vals = sorted(m["realized_pm2"] for m in members)
        median = _quantile(vals, 0.5)
        q1 = _quantile(vals, 0.25)
        q3 = _quantile(vals, 0.75)
        iqr = q3 - q1
        lower = q1 - OUTLIER_IQR_MULT * iqr
        upper = q3 + OUTLIER_IQR_MULT * iqr
        median_out = _c2(median)
        for m in members:
            pm2 = m["realized_pm2"]
            below = pm2 < lower
            above = pm2 > upper
            if not (below or above):
                continue
            if median <= 0:
                continue   # degenerate group — relative deviation undefined
            dev_pct = (pm2 - median) / median * 100.0
            if abs(dev_pct) < OUTLIER_MIN_DEV_PCT:
                continue
            direction = DIRECTION_BELOW if below else DIRECTION_ABOVE
            deviation_pct = _c2(dev_pct)
            rows.append({
                "unit_id": m["unit_id"],
                "code": m["code"],
                "project_id": m["project_id"],
                "project_name": m["project_name"],
                "zone_name": m["zone_name"],
                "unit_type_name": m["unit_type_name"],
                "vintage_bucket_label": m["vintage_bucket_label"],
                "sale_date": m["sale_date"],
                "realized_pm2": m["realized_pm2"],
                "group_median_pm2": median_out,
                "deviation_pct": deviation_pct,
                "direction": direction,
                "is_confirmed": False,
            })
            flag_by_unit[m["unit_id"]] = {"direction": direction, "deviation_pct": deviation_pct}
    # Sort by magnitude — largest absolute deviation first (stable tie-break on code).
    rows.sort(key=lambda r: (-abs(r["deviation_pct"]), r["code"]))
    return rows, flag_by_unit, insufficient, eligible


def _section_b(population: list[dict]) -> tuple[list[dict], dict[int, dict]]:
    """Section B — discount outliers vs own list. Returns (flagged_rows, flag_by_unit).
    flag_by_unit maps unit_id → {"kind", "discount_pct"} for the confirmed-join."""
    rows: list[dict] = []
    flag_by_unit: dict[int, dict] = {}
    for u in population:
        disc = u["discount_pct"]
        if disc is None:
            continue   # amount <= 0 — no own-list reference
        if disc >= OUTLIER_DEEP_DISCOUNT_PCT:
            kind = KIND_DEEP
        elif disc <= OUTLIER_PREMIUM_PCT:
            kind = KIND_PREMIUM
        else:
            continue
        rows.append({
            "unit_id": u["unit_id"],
            "code": u["code"],
            "project_id": u["project_id"],
            "project_name": u["project_name"],
            "unit_type_name": u["unit_type_name"],
            "sale_date": u["sale_date"],
            "list_total": u["list_total"],
            "realized_total": u["realized_total"],
            "discount_pct": disc,
            "kind": kind,
            "is_confirmed": False,
        })
        flag_by_unit[u["unit_id"]] = {"kind": kind, "discount_pct": disc}
    # Deep first (discount desc — deepest first), then premium (discount asc — most
    # negative / biggest premium first). One stable ordering by magnitude.
    rows.sort(key=lambda r: (0 if r["kind"] == KIND_DEEP else 1,
                             -r["discount_pct"] if r["kind"] == KIND_DEEP else r["discount_pct"],
                             r["code"]))
    return rows, flag_by_unit


def _project_counts(
    population: list[dict], a_units: set[int], b_units: set[int], confirmed: set[int]
) -> list[dict]:
    """Per-project Section-A / Section-B / confirmed counts, over the scoped projects (a
    project with population but zero flags still appears, with zeros)."""
    names: dict[int, str] = {}
    for u in population:
        names.setdefault(u["project_id"], u["project_name"])
    a_by: dict[int, int] = {}
    b_by: dict[int, int] = {}
    c_by: dict[int, int] = {}
    pid_by_unit = {u["unit_id"]: u["project_id"] for u in population}
    for uid in a_units:
        a_by[pid_by_unit[uid]] = a_by.get(pid_by_unit[uid], 0) + 1
    for uid in b_units:
        b_by[pid_by_unit[uid]] = b_by.get(pid_by_unit[uid], 0) + 1
    for uid in confirmed:
        c_by[pid_by_unit[uid]] = c_by.get(pid_by_unit[uid], 0) + 1
    projects = [
        {
            "project_id": pid,
            "project_name": names[pid],
            "section_a_count": a_by.get(pid, 0),
            "section_b_count": b_by.get(pid, 0),
            "confirmed_count": c_by.get(pid, 0),
        }
        for pid in names
    ]
    # Stable, board-friendly order: most flags first, then name.
    projects.sort(key=lambda p: (-(p["section_a_count"] + p["section_b_count"]), p["project_name"]))
    return projects


def _reconcile(result: dict) -> None:
    """Every count is internally consistent and Σ per-project == combined (explicit raises
    — survive python -O), mirroring Slice 1/2."""
    a = result["section_a"]
    b = result["section_b"]
    if result["section_a_count"] != len(a):
        raise RuntimeError(
            f"Outliers reconciliation FAILED: section_a_count {result['section_a_count']} "
            f"!= len(section_a) {len(a)}."
        )
    if result["section_b_count"] != len(b):
        raise RuntimeError(
            f"Outliers reconciliation FAILED: section_b_count {result['section_b_count']} "
            f"!= len(section_b) {len(b)}."
        )
    a_below = sum(1 for r in a if r["direction"] == DIRECTION_BELOW)
    a_above = sum(1 for r in a if r["direction"] == DIRECTION_ABOVE)
    if (a_below, a_above) != (result["section_a_below_count"], result["section_a_above_count"]):
        raise RuntimeError(
            "Outliers reconciliation FAILED: section A below/above "
            f"({a_below},{a_above}) != "
            f"({result['section_a_below_count']},{result['section_a_above_count']})."
        )
    if a_below + a_above != result["section_a_count"]:
        raise RuntimeError(
            "Outliers reconciliation FAILED: section A below+above "
            f"{a_below + a_above} != section_a_count {result['section_a_count']}."
        )
    b_deep = sum(1 for r in b if r["kind"] == KIND_DEEP)
    b_prem = sum(1 for r in b if r["kind"] == KIND_PREMIUM)
    if (b_deep, b_prem) != (result["section_b_deep_count"], result["section_b_premium_count"]):
        raise RuntimeError(
            "Outliers reconciliation FAILED: section B deep/premium "
            f"({b_deep},{b_prem}) != "
            f"({result['section_b_deep_count']},{result['section_b_premium_count']})."
        )
    if b_deep + b_prem != result["section_b_count"]:
        raise RuntimeError(
            "Outliers reconciliation FAILED: section B deep+premium "
            f"{b_deep + b_prem} != section_b_count {result['section_b_count']}."
        )
    # Confirmed == units flagged in BOTH sections, marked is_confirmed in BOTH lists.
    a_conf = {r["unit_id"] for r in a if r["is_confirmed"]}
    b_conf = {r["unit_id"] for r in b if r["is_confirmed"]}
    if a_conf != b_conf:
        raise RuntimeError(
            f"Outliers reconciliation FAILED: confirmed set disagrees between sections "
            f"(A {sorted(a_conf)} != B {sorted(b_conf)})."
        )
    if len(a_conf) != result["confirmed_count"]:
        raise RuntimeError(
            f"Outliers reconciliation FAILED: confirmed_count {result['confirmed_count']} "
            f"!= |is_confirmed units| {len(a_conf)}."
        )
    # Σ per-project == combined for each of the three counts.
    for key in ("section_a_count", "section_b_count", "confirmed_count"):
        per_sum = sum(p[key] for p in result["projects"])
        if per_sum != result[key]:
            raise RuntimeError(
                f"Outliers reconciliation FAILED: Σ per-project {key} {per_sum} "
                f"!= combined {result[key]}."
            )
    # Population accounting: every evaluated-or-skipped unit is one of the two.
    if result["population_count"] < result["insufficient_peers_count"]:
        raise RuntimeError(
            f"Outliers reconciliation FAILED: insufficient_peers_count "
            f"{result['insufficient_peers_count']} > population_count "
            f"{result['population_count']}."
        )


async def get_pricing_outliers_overview(client: Optional[OdooClient] = None) -> dict:
    """Return the Pricing Outliers overview (Sections A + B, confirmed, per-project counts)
    for New Capital + Cassette. La Puerta is excluded entirely.

    Args:
        client: optional injected OdooClient (tests pass a mock; production opens and
            closes its own).

    Returns a dict matching schemas.PricingOutliersOverview.

    Raises:
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated.
        OdooQueryError: if the Odoo RPC fails.
        RuntimeError: on an unmapped unit state, a leaked excluded project, or any
            reconciliation failure (explicit raises so they survive python -O).
    """
    _assert_read_only()

    cairo_today = datetime.now(_CAIRO_TZ).date()
    cache_key = _cache.make_key(_CACHE_KEY_PREFIX)
    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"Outliers cache hit: {cache_key}")
        return {**cached, "cache_status": "cached", "rpc_duration_ms": 0}
    logger.info(f"Outliers cache miss: {cache_key} — querying Odoo")

    _client = client if client is not None else OdooClient()

    t0 = time.monotonic()
    try:
        all_units = await _get_units_cached(_client)

        # Scope to NC + Cassette; La Puerta (and anything else) is dropped here.
        scope_units = [
            u for u in all_units if _m2o(u.get("project_id"))[0] in VALUE_SCOPE_PROJECT_IDS
        ]
        leaked = [u["id"] for u in scope_units
                  if _m2o(u.get("project_id"))[0] in VALUE_EXCLUDED_PROJECT_IDS]
        if leaked:
            raise RuntimeError(
                f"Excluded project leaked into the outliers scope (unit ids {leaked[:10]}). "
                "Refusing to surface a pricing figure that includes La Puerta."
            )

        # Every scoped unit's state must map to a known bucket (never silently dropped).
        _classify_states(scope_units)

        sold_ids = sorted(u["id"] for u in scope_units if u["state"] in SOLD_STATES)
        contract_rows = await _get_contracts_cached(_client, sold_ids)
        realized, term_ids = _realized_and_terms(contract_rows)
        referenced_terms = sorted({t for ts in term_ids.values() for t in ts})
        term_dates = await _get_terms_cached(_client, referenced_terms)
    except (ReadOnlyViolationError, RuntimeError):
        raise
    except Exception as exc:
        raise OdooQueryError(f"get_pricing_outliers_overview() RPC failed: {exc}") from exc
    finally:
        if client is None:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)

    population = _build_population(scope_units, realized, term_ids, term_dates)

    section_a, a_flags, insufficient, eligible = _section_a(population)
    section_b, b_flags = _section_b(population)

    a_units = set(a_flags)
    b_units = set(b_flags)
    confirmed = a_units & b_units
    for r in section_a:
        r["is_confirmed"] = r["unit_id"] in confirmed
    for r in section_b:
        r["is_confirmed"] = r["unit_id"] in confirmed

    a_below = sum(1 for r in section_a if r["direction"] == DIRECTION_BELOW)
    a_above = sum(1 for r in section_a if r["direction"] == DIRECTION_ABOVE)
    b_deep = sum(1 for r in section_b if r["kind"] == KIND_DEEP)
    b_prem = sum(1 for r in section_b if r["kind"] == KIND_PREMIUM)

    projects = _project_counts(population, a_units, b_units, confirmed)

    result: dict = {
        "section_a": section_a,
        "section_b": section_b,
        "section_a_count": len(section_a),
        "section_a_below_count": a_below,
        "section_a_above_count": a_above,
        "section_b_count": len(section_b),
        "section_b_deep_count": b_deep,
        "section_b_premium_count": b_prem,
        "confirmed_count": len(confirmed),
        "insufficient_peers_count": insufficient,
        "eligible_group_count": eligible,
        "population_count": len(population),
        "projects": projects,
        "project_count": len(projects),
        "thresholds": {
            "min_group_size": OUTLIER_MIN_GROUP_SIZE,
            "iqr_mult": OUTLIER_IQR_MULT,
            "min_dev_pct": OUTLIER_MIN_DEV_PCT,
            "deep_discount_pct": OUTLIER_DEEP_DISCOUNT_PCT,
            "premium_pct": OUTLIER_PREMIUM_PCT,
            "vintage_bucket_years": VINTAGE_BUCKET_YEARS,
        },
        "reference_date": cairo_today.isoformat(),
        "as_of": datetime.now(timezone.utc).isoformat(),
        "cache_status": "fresh",
        "rpc_duration_ms": rpc_ms,
    }
    _reconcile(result)

    logger.info(
        f"Pricing outliers: population {len(population):,} (NC+Cassette) | "
        f"A={len(section_a)} (below {a_below}/above {a_above}, {eligible} eligible groups, "
        f"{insufficient} insufficient-peer units) | B={len(section_b)} "
        f"(deep {b_deep}/premium {b_prem}) | confirmed {len(confirmed)} | RPC {rpc_ms}ms"
    )

    _cache.set(cache_key, result)
    return result
