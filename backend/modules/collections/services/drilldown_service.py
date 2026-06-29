"""
Drill-down service — Stage 5 (Module 2 Collections Dashboard).

Decision 14.7: No caching — all calls hit Odoo live.
Decision 14.10: assert client.is_read_only at top of every function (Rule R10).
Decision 14.2: Cursor-based (keyset) pagination for installment rows.
Decision 14.12: Portfolio uses read_group by ['partner_id', 'project_id'].
Decision 14.8: late_amount = amount − x_studio_actual_paid_amount (PATH A per-record).
"""

import asyncio
import base64
import calendar
import json
import time
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from typing import Optional

from loguru import logger

from backend.core.exceptions import OdooQueryError
from backend.shared.odoo.client import OdooClient
from backend.modules.collections.services import cache as _cache
from backend.modules.collections.services.kpi_service import _compute_period_bounds
from backend.modules.collections.installment_type_names import get_type_name_ar, get_type_name_en

_MODEL = "rs.installment"
_LA_VERDE_TZ = ZoneInfo("Africa/Cairo")

_PROJECT_NAMES_AR: dict[int, str] = {
    1: "نيو كابيتال",
    2: "كاسيت",
    3: "لا بويرتا",
}
_PROJECT_NAMES_EN: dict[int, str] = {
    1: "New Capital",
    2: "Cassette",
    3: "La puerta",
}

# Decision 14.13: sentinel labels for installments with project_id=False in Odoo.
_NO_PROJECT_NAME_AR = "بدون مشروع"
_NO_PROJECT_NAME_EN = "No Project Assigned"

# All fields required to populate InstallmentRow (includes late_amount source fields).
# installment_type_id added Stage 7 — rides the existing search_read, zero extra RPCs.
_DRILL_FIELDS: list[str] = [
    "id", "date", "amount", "due_amount",
    "paid_amount", "x_studio_actual_paid_amount",
    "payment_state", "partner_id", "project_id",
    "installment_type_id",
]

_DEFAULT_PAGE_SIZE = 50
_MAX_PAGE_SIZE = 200

_VALID_SORT_FIELDS = frozenset({"date", "amount", "due_amount"})
_VALID_SORT_DIRS   = frozenset({"asc", "desc"})
_VALID_PROJECT_IDS = frozenset({1, 2, 3})

# ── KPI 7 v2 segment-aware forecast drill-down (Session 21 / N5) ─────────────
# Per-installment rows behind ONE (bucket, segment) of the "Dues & Collections —
# Current Periods" cards (Decision 19.1). The three row-level domains below are
# PROVEN live against Odoo in scripts/discover_n5_segment_drilldown.py — do not
# re-derive. Buckets reuse kpi_service._compute_period_bounds (Cairo calendar).
_FORECAST_BUCKETS  = frozenset({"this_month", "this_quarter", "this_half", "this_year"})
_FORECAST_SEGMENTS = frozenset({"cleared", "pending", "remaining"})

# Odoo field whose read_group SUM equals the card's segment figure — server-side
# segments only ('pending' has no native domain; see get_forecast_segment_drilldown).
_SEGMENT_SUM_FIELD: dict[str, str] = {
    "cleared":   "x_studio_actual_paid_amount",
    "remaining": "due_amount",
}

# _DRILL_FIELDS + unit_id (rs.structure.unit, renders like "Unit#AF208-20-601").
_FORECAST_DRILL_FIELDS: list[str] = _DRILL_FIELDS + ["unit_id"]


def _forecast_segment_metric(rec: dict, segment: str) -> float:
    """Per-row segment value — the number the list sums and shows for the row.

    cleared   → x_studio_actual_paid_amount  (cash + cleared cheques)
    pending   → paid_amount − x_studio_actual_paid_amount  (postdated pipeline)
    remaining → due_amount
    The sum of this metric over the full segment set reconciles to the card's
    segment figure (identity rule, consistent with N2/N4).
    """
    actual = float(rec.get("x_studio_actual_paid_amount") or 0.0)
    if segment == "cleared":
        return actual
    if segment == "remaining":
        return float(rec.get("due_amount") or 0.0)
    return float(rec.get("paid_amount") or 0.0) - actual  # pending


def _forecast_segment_clause(segment: str) -> Optional[tuple]:
    """Server-side Odoo domain clause for cleared/remaining; None for pending.

    cleared   : actual_paid > 0   (no negative actual_paid rows exist in the data)
    remaining : due_amount != 0   (NOT > 0 — the one −147 overpayment row, id 93146,
                is included in the card's SUM(due_amount); '> 0' would under-count it
                by 147 EGP in this_half / this_year)
    pending   : None — the field-to-field comparison paid_amount > actual is rejected
                by the ORM, so pending is resolved with a client-side filter over a
                server-side paid_amount>0 superset (see the service docstring).
    """
    if segment == "cleared":
        return ("x_studio_actual_paid_amount", ">", 0)
    if segment == "remaining":
        return ("due_amount", "!=", 0)
    return None


def _serialize_forecast_segment_row(rec: dict, segment: str) -> dict:
    """Serialize one rs.installment row for a forecast SEGMENT drill-down.

    Reuses _serialize_row (so the table component renders identically to the other
    drill-downs) and adds the partner id, the unit reference, and the per-row
    SEGMENT METRIC.
    """
    row = _serialize_row(rec)
    partner = rec.get("partner_id")
    unit    = rec.get("unit_id")
    row["partner_id"] = int(partner[0]) if isinstance(partner, (list, tuple)) and partner else 0
    row["unit_id"]    = int(unit[0]) if isinstance(unit, (list, tuple)) and unit else 0
    row["unit_name"]  = (unit[1] if isinstance(unit, (list, tuple)) and unit else "")
    row["segment"]        = segment
    row["segment_metric"] = _forecast_segment_metric(rec, segment)
    return row


def _sort_rows_py(rows: list[dict], sort_by: str, sort_dir: str) -> list[dict]:
    """Stable Python-side sort for the pending segment (client-filtered set).

    Mirrors the Odoo order `{sort_by} {sort_dir}, id {sort_dir}` so a Python page
    matches a server page. date sorts lexicographically (ISO strings); amount /
    due_amount sort numerically; id is the deterministic tiebreaker.
    """
    reverse = sort_dir == "desc"
    if sort_by == "date":
        def _key(r: dict) -> tuple:
            return (str(r.get("date") or ""), int(r.get("id") or 0))
    else:
        def _key(r: dict) -> tuple:
            return (float(r.get(sort_by) or 0.0), int(r.get("id") or 0))
    return sorted(rows, key=_key, reverse=reverse)


# ── Cursor helpers ────────────────────────────────────────────────────────────

def _encode_cursor(payload: dict) -> str:
    return base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).decode()


def _decode_cursor(cursor: str) -> dict:
    try:
        return json.loads(base64.urlsafe_b64decode(cursor.encode()))
    except Exception:
        return {}


def _build_keyset_clause(sort_by: str, sort_dir: str, sv: object, rid: int) -> list:
    """Prefix-notation Odoo domain clause that excludes all records up to and including cursor.

    For DESC: ('|', (field,'<',sv), '&', (field,'=',sv), ('id','<',rid))
    For ASC:  ('|', (field,'>',sv), '&', (field,'=',sv), ('id','>',rid))
    """
    op = "<" if sort_dir == "desc" else ">"
    return ["|", (sort_by, op, sv), "&", (sort_by, "=", sv), ("id", op, rid)]


def _odoo_order(sort_by: str, sort_dir: str) -> str:
    return f"{sort_by} {sort_dir}, id {sort_dir}"


def _clamp_page_size(n: int) -> int:
    return max(1, min(n, _MAX_PAGE_SIZE))


def _normalize_sort(sort_by: str, sort_dir: str) -> tuple[str, str]:
    if sort_by not in _VALID_SORT_FIELDS:
        sort_by = "date"
    if sort_dir not in _VALID_SORT_DIRS:
        sort_dir = "asc"
    return sort_by, sort_dir


# ── Row serialization ─────────────────────────────────────────────────────────

def _serialize_row(rec: dict) -> dict:
    partner  = rec.get("partner_id")
    project  = rec.get("project_id")
    inst_typ = rec.get("installment_type_id")
    pid      = int(project[0]) if isinstance(project, (list, tuple)) and project else 0
    type_id  = int(inst_typ[0]) if isinstance(inst_typ, (list, tuple)) and inst_typ else (
                   int(inst_typ) if inst_typ else 0
               )
    amount   = float(rec.get("amount") or 0.0)
    actual   = float(rec.get("x_studio_actual_paid_amount") or 0.0)
    paid     = float(rec.get("paid_amount") or 0.0)
    return {
        "record_id":                int(rec["id"]),
        "customer_name":            (partner[1] if isinstance(partner, (list, tuple)) and partner else ""),
        "project_id":               pid,
        "project_name_ar":          _PROJECT_NAMES_AR.get(pid, ""),
        "project_name_en":          _PROJECT_NAMES_EN.get(pid, ""),
        "installment_type_id":      type_id,
        "installment_type_name_ar": get_type_name_ar(type_id),
        "installment_type_name_en": get_type_name_en(type_id),
        "date":                     str(rec.get("date") or ""),
        "amount":                   amount,
        "due_amount":               float(rec.get("due_amount") or 0.0),
        "paid_amount":              paid,
        "actual_paid_amount":       actual,
        "pending_cheque":           max(paid - actual, 0.0),
        "payment_state":            str(rec.get("payment_state") or ""),
        "late_amount":              amount - actual,  # Decision 14.8: PATH A per-record
    }


def _next_cursor_from_items(
    items: list[dict], sort_by: str, sort_dir: str
) -> str | None:
    if not items:
        return None
    last = items[-1]
    return _encode_cursor({
        "sv": last[sort_by],
        "id": last["record_id"],
        "sb": sort_by,
        "sd": sort_dir,
    })


def _build_meta(
    request_id: str,
    rpc_ms: int,
    page_size: int,
    total_count: int,
    cursor: str | None,
    next_cur: str | None,
    has_next: bool,
    filters: dict,
    sort: dict,
    data_quality: dict | None = None,  # Decision 14.13: portfolio unassigned-project info
) -> dict:
    meta: dict = {
        "request_id":      request_id,
        "as_of":           datetime.now(timezone.utc).isoformat(),
        "rpc_duration_ms": rpc_ms,
        "page_size":       page_size,
        "total_count":     total_count,
        "cursor_current":  cursor,
        "cursor_next":     next_cur,
        "has_next":        has_next,
        "filters_applied": filters,
        "sort_applied":    sort,
    }
    if data_quality is not None:
        meta["data_quality"] = data_quality
    return meta


# ── Service functions ─────────────────────────────────────────────────────────

async def get_late_drilldown(
    request_id: str,
    cursor: Optional[str] = None,
    page_size: int = _DEFAULT_PAGE_SIZE,
    sort_by: str = "due_amount",
    sort_dir: str = "desc",
    payment_state: Optional[str] = None,
    has_pending_cheque: Optional[bool] = None,
    client: Optional[OdooClient] = None,
) -> dict:
    """Paginated drill-down for KPI 2 — Late Uncollected installments.

    Domain: Candidate C (state=post, payment_state IN [unpaid,partial], date < today_cairo).
    Optional narrowing: payment_state='unpaid'|'partial', has_pending_cheque=True.
    2 concurrent RPCs: search_count (full domain) + search_read (page domain with cursor).
    """
    _own_client = client is None
    _client = client or OdooClient()
    assert _client.is_read_only  # Rule R10 (Decision 14.10)
    _log = logger.bind(request_id=request_id)

    page_size = _clamp_page_size(page_size)
    sort_by, sort_dir = _normalize_sort(sort_by, sort_dir)

    today = _cache.today_str()
    ps_clause = (
        ("payment_state", "=", payment_state)
        if payment_state in ("unpaid", "partial")
        else ("payment_state", "in", ["unpaid", "partial"])
    )
    base_domain: list = [("state", "=", "post"), ps_clause, ("date", "<", today)]
    if has_pending_cheque is True:
        base_domain.append(("check_pending_amount", ">", 0))
    elif has_pending_cheque is False:
        base_domain.append(("check_pending_amount", "=", 0))

    page_domain = list(base_domain)
    if cursor:
        cur = _decode_cursor(cursor)
        if cur:
            page_domain += _build_keyset_clause(cur["sb"], cur["sd"], cur["sv"], cur["id"])

    order = _odoo_order(sort_by, sort_dir)
    t0 = time.monotonic()
    try:
        await _client.authenticate()  # pre-auth before concurrent RPCs
        total_count, rows = await asyncio.gather(
            _client.execute_kw(_MODEL, "search_count", args=[base_domain]),
            _client.execute_kw(
                _MODEL, "search_read",
                args=[page_domain, _DRILL_FIELDS],
                kwargs={"limit": page_size + 1, "order": order},
            ),
        )
    except Exception as exc:
        raise OdooQueryError(f"Late drill-down failed: {exc}") from exc
    finally:
        if _own_client:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)
    _log.info(f"Late drill-down: {int(total_count or 0)} total, page {page_size} in {rpc_ms}ms")

    has_next = len(rows) > page_size
    rows = rows[:page_size]
    items = [_serialize_row(r) for r in rows]
    next_cur = _next_cursor_from_items(items, sort_by, sort_dir) if has_next else None

    return {
        "version": "1.0",
        "data": {"items": items},
        "meta": _build_meta(
            request_id, rpc_ms, page_size, int(total_count or 0),
            cursor, next_cur, has_next,
            {
                "today": today,
                "payment_state": payment_state,
                "has_pending_cheque": has_pending_cheque,
            },
            {"sort_by": sort_by, "sort_dir": sort_dir},
        ),
    }


async def get_forecast_segment_drilldown(
    request_id: str,
    bucket: str,
    segment: str,
    cursor: Optional[str] = None,
    page_size: int = _DEFAULT_PAGE_SIZE,
    sort_by: str = "date",
    sort_dir: str = "desc",
    installment_type_id: Optional[int] = None,
    client: Optional[OdooClient] = None,
) -> dict:
    """Paginated per-installment drill-down for ONE KPI 7 v2 (bucket, segment).

    bucket  ∈ {this_month, this_quarter, this_half, this_year}
    segment ∈ {cleared, pending, remaining}

    Full-period base domain — lifted verbatim from kpi_service._fetch_bucket
    (Decision 19.1): state=post, date ∈ [period_start, period_end]. Bucket bounds
    come from the REUSED _compute_period_bounds (Cairo calendar; rs.installment.date
    is a plain date field, no UTC conversion). NO payment_state filter.

    Per-segment row set (domains proven live — discover_n5_segment_drilldown.py):
      cleared   : base + [(actual_paid, '>', 0)]  — server-side, Odoo offset/limit.
                  read_group SUM(actual_paid) == card collected_cleared_egp.
      remaining : base + [(due_amount, '!=', 0)]  — server-side, Odoo offset/limit.
                  read_group SUM(due_amount)  == card remaining_egp. '!= 0' (NOT '> 0')
                  keeps the one −147 overpayment row (id 93146) the card's SUM includes.
      pending   : NO native Odoo domain — the field-to-field comparison
                  paid_amount > actual is rejected by the ORM. Fetch the SERVER-SIDE
                  SUPERSET base + [(paid_amount, '>', 0)], compute paid−actual per row,
                  KEEP rows where it is > 0, then total and paginate the FILTERED list
                  IN PYTHON. The this_year pending superset is modest (~1.4k rows), so a
                  single fetch + in-memory slice is correct and cheap.

    Pagination is offset-based (cursor carries {"offset": n}, the same scheme as the
    portfolio drill-down) — uniform across all three segments because the pending page
    cannot be a pure Odoo offset/limit on the client-filtered set.

    data.segment_total_egp is the segment metric summed over the FULL set (not just the
    page) so the UI can prove list-total == the card's segment figure.

    installment_type_id (optional): when set, ONE read-only equality tuple
    ("installment_type_id", "=", id) is appended to base_domain BEFORE the segment
    split, so all three segments + search_count + read_group recompute against the
    type-filtered set (segment_total_egp then reflects the filtered total, not the
    card figure — the UI flags this with a "(filtered)" marker). omit for all types.

    READ-ONLY: search_read / read_group / search_count only.
    """
    if bucket not in _FORECAST_BUCKETS:
        raise ValueError(
            f"Unknown forecast bucket: {bucket!r}. Valid: {sorted(_FORECAST_BUCKETS)}"
        )
    if segment not in _FORECAST_SEGMENTS:
        raise ValueError(
            f"Unknown forecast segment: {segment!r}. Valid: {sorted(_FORECAST_SEGMENTS)}"
        )

    _own_client = client is None
    _client = client or OdooClient()
    assert _client.is_read_only  # Rule R10 (Decision 14.10)
    _log = logger.bind(request_id=request_id)

    page_size = _clamp_page_size(page_size)
    sort_by, sort_dir = _normalize_sort(sort_by, sort_dir)

    today_cairo = datetime.now(_LA_VERDE_TZ).date()
    start, end = _compute_period_bounds(today_cairo)[bucket]
    base_domain: list = [
        ("state", "=", "post"),
        ("date", ">=", start.isoformat()),
        ("date", "<=", end.isoformat()),
    ]
    # Single-select installment-type filter (read-only). Appended BEFORE the segment
    # split so it flows uniformly into cleared/remaining (server-side clause), the
    # pending paid>0 superset, search_count and read_group — counts and
    # segment_total_egp recompute against the type-filtered set automatically.
    if installment_type_id is not None:
        base_domain.append(("installment_type_id", "=", installment_type_id))

    offset = 0
    if cursor:
        cur = _decode_cursor(cursor)
        offset = max(0, int(cur.get("offset", 0) or 0))

    order = _odoo_order(sort_by, sort_dir)
    t0 = time.monotonic()
    try:
        if segment == "pending":
            # No native domain — fetch the paid>0 superset and filter/paginate in Python.
            superset = base_domain + [("paid_amount", ">", 0)]
            rows = await _client.execute_kw(
                _MODEL, "search_read",
                args=[superset, _FORECAST_DRILL_FIELDS],
                kwargs={"order": "id asc"},
            )
            filtered = [
                r for r in rows
                if (float(r.get("paid_amount") or 0.0)
                    - float(r.get("x_studio_actual_paid_amount") or 0.0)) > 0
            ]
            filtered = _sort_rows_py(filtered, sort_by, sort_dir)
            total_count  = len(filtered)
            total_metric = sum(_forecast_segment_metric(r, "pending") for r in filtered)
            page_rows = filtered[offset: offset + page_size]
        else:
            domain    = base_domain + [_forecast_segment_clause(segment)]
            sum_field = _SEGMENT_SUM_FIELD[segment]
            await _client.authenticate()  # pre-auth before concurrent RPCs
            total_count, agg_rows, page_rows = await asyncio.gather(
                _client.execute_kw(_MODEL, "search_count", args=[domain]),
                _client.execute_kw(
                    _MODEL, "read_group",
                    args=[domain, [sum_field], []],
                    kwargs={"lazy": False},
                ),
                _client.execute_kw(
                    _MODEL, "search_read",
                    args=[domain, _FORECAST_DRILL_FIELDS],
                    kwargs={"limit": page_size, "offset": offset, "order": order},
                ),
            )
            total_count  = int(total_count or 0)
            agg_row      = agg_rows[0] if agg_rows else {}
            total_metric = float(agg_row.get(sum_field) or 0.0)
    except Exception as exc:
        raise OdooQueryError(
            f"Forecast segment drill-down ({bucket}/{segment}) failed: {exc}"
        ) from exc
    finally:
        if _own_client:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)
    _log.info(
        f"Forecast segment drill-down ({bucket}/{segment}): {total_count} total, "
        f"metric {total_metric:,.2f} EGP in {rpc_ms}ms"
    )

    items    = [_serialize_forecast_segment_row(r, segment) for r in page_rows]
    has_next = (offset + page_size) < total_count
    next_cur = _encode_cursor({"offset": offset + page_size}) if has_next else None

    return {
        "version": "1.0",
        "data": {
            "bucket":            bucket,
            "segment":           segment,
            "period_start":      start.isoformat(),
            "period_end":        end.isoformat(),
            "segment_total_egp": total_metric,
            "items":             items,
        },
        "meta": _build_meta(
            request_id, rpc_ms, page_size, total_count,
            cursor, next_cur, has_next,
            {
                "bucket":              bucket,
                "segment":             segment,
                "period_start":        start.isoformat(),
                "period_end":          end.isoformat(),
                "installment_type_id": installment_type_id,
            },
            {"sort_by": sort_by, "sort_dir": sort_dir},
        ),
    }


async def get_portfolio_drilldown(
    request_id: str,
    cursor: Optional[str] = None,
    page_size: int = _DEFAULT_PAGE_SIZE,
    project_id: Optional[int] = None,
    client: Optional[OdooClient] = None,
) -> dict:
    """Paginated drill-down for KPI 1 — Total Portfolio.

    Uses read_group by ['partner_id', 'project_id'] to aggregate all posted
    installments into customer-level rows (Decision 14.12). Python-side sort
    + offset-encoded cursor (offset-based because read_group result is re-derived
    fresh each call; keyset on aggregated rows is not stable).

    1 RPC: read_group (full dataset).
    """
    _own_client = client is None
    _client = client or OdooClient()
    assert _client.is_read_only  # Rule R10
    _log = logger.bind(request_id=request_id)

    page_size = _clamp_page_size(page_size)

    offset = 0
    if cursor:
        cur = _decode_cursor(cursor)
        offset = int(cur.get("offset", 0))

    base_domain: list = [("state", "=", "post")]
    if project_id is not None:
        base_domain.append(("project_id", "=", project_id))

    t0 = time.monotonic()
    try:
        rg_rows = await _client.execute_kw(
            _MODEL,
            "read_group",
            args=[
                base_domain,
                ["amount", "due_amount", "paid_amount", "x_studio_actual_paid_amount"],
                ["partner_id", "project_id"],
            ],
            kwargs={"lazy": False},
        )
    except Exception as exc:
        raise OdooQueryError(f"Portfolio drill-down failed: {exc}") from exc
    finally:
        if _own_client:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)
    _log.info(f"Portfolio drill-down: {len(rg_rows)} read_group rows in {rpc_ms}ms")

    # Python-side aggregation: collapse (customer, project) rows into customer rows.
    # Decision 14.13: project_id=False rows are surfaced, never silently dropped.
    customer_map: dict[int, dict] = {}
    unassigned_inst_count  = 0    # __count  of rows where project_id=False
    unassigned_inst_amount = 0.0  # SUM(amount) of those rows

    for row in rg_rows:
        partner_raw = row.get("partner_id")
        project_raw = row.get("project_id")

        # partner_id=False: no customer link — cannot attribute to any named customer.
        # Q1 diagnostic (2026-05-21) confirmed this branch is dead in live data (0 rows).
        # Count for data-quality transparency; do NOT silently drop.
        if not partner_raw:
            unassigned_inst_count  += int(row.get("__count") or 0)
            unassigned_inst_amount += float(row.get("amount") or 0.0)
            continue

        cust_id   = int(partner_raw[0]) if isinstance(partner_raw, (list, tuple)) else int(partner_raw)
        cust_name = partner_raw[1]       if isinstance(partner_raw, (list, tuple)) else ""

        # project_id=False: real customer, installment with no project assignment in Odoo.
        # Decision 14.13: include in customer totals under 'بدون مشروع' sentinel label.
        if not project_raw:
            pid         = None
            pid_name_ar = _NO_PROJECT_NAME_AR
            pid_name_en = _NO_PROJECT_NAME_EN
            unassigned_inst_count  += int(row.get("__count") or 0)
            unassigned_inst_amount += float(row.get("amount") or 0.0)
        else:
            pid         = int(project_raw[0]) if isinstance(project_raw, (list, tuple)) else int(project_raw)
            pid_name_ar = _PROJECT_NAMES_AR.get(pid, "")
            pid_name_en = _PROJECT_NAMES_EN.get(pid, f"Project {pid}")

        amount     = float(row.get("amount") or 0.0)
        due_amount = float(row.get("due_amount") or 0.0)
        paid       = float(row.get("paid_amount") or 0.0)
        actual     = float(row.get("x_studio_actual_paid_amount") or 0.0)
        count      = int(row.get("__count") or 0)

        if cust_id not in customer_map:
            customer_map[cust_id] = {
                "customer_id":       cust_id,
                "customer_name":     cust_name,
                "total_amount":      0.0,
                "total_paid":        0.0,
                "total_due":         0.0,
                "total_actual_paid": 0.0,
                "record_count":      0,
                "project_breakdown": [],
            }

        c = customer_map[cust_id]
        c["total_amount"]      += amount
        c["total_paid"]        += paid
        c["total_due"]         += due_amount
        c["total_actual_paid"] += actual
        c["record_count"]      += count
        c["project_breakdown"].append({
            "project_id":      pid,          # None for project_id=False rows (Decision 14.13)
            "project_name_ar": pid_name_ar,
            "project_name_en": pid_name_en,
            "amount":          amount,
            "due_amount":      due_amount,
            "record_count":    count,
        })

    all_customers = sorted(
        customer_map.values(),
        key=lambda c: (-c["total_amount"], c["customer_id"]),
    )

    total_count    = len(all_customers)
    page_customers = all_customers[offset: offset + page_size]
    has_next       = (offset + page_size) < total_count
    next_cur       = _encode_cursor({"offset": offset + page_size}) if has_next else None

    data_quality: dict | None = None
    if unassigned_inst_count > 0:
        data_quality = {
            "unassigned_project_installments": unassigned_inst_count,
            "unassigned_project_amount":       round(unassigned_inst_amount, 2),
            "note_ar": (
                f"يوجد {unassigned_inst_count} قسط بقيمة "
                f"{unassigned_inst_amount:,.2f} ج.م غير مرتبطين بمشروع "
                f"في Odoo — تظهر تحت 'بدون مشروع'"
            ),
            "note_en": (
                f"{unassigned_inst_count} installments "
                f"(EGP {unassigned_inst_amount:,.2f}) have no project "
                f"assigned in Odoo — shown under 'No Project Assigned'"
            ),
        }

    return {
        "version": "1.0",
        "data": {"customers": list(page_customers)},
        "meta": _build_meta(
            request_id, rpc_ms, page_size, total_count,
            cursor, next_cur, has_next,
            {"project_id": project_id},
            {"sort_by": "total_amount", "sort_dir": "desc"},
            data_quality=data_quality,
        ),
    }


async def get_project_drilldown(
    request_id: str,
    project_id: int,
    cursor: Optional[str] = None,
    page_size: int = _DEFAULT_PAGE_SIZE,
    sort_by: str = "due_amount",
    sort_dir: str = "desc",
    payment_state: Optional[str] = None,
    has_pending_cheque: Optional[bool] = None,
    client: Optional[OdooClient] = None,
) -> dict:
    """Paginated drill-down for one project — KPI 5 late installments by project.

    Domain: Candidate C + project_id = X.
    Optional narrowing: payment_state='unpaid'|'partial', has_pending_cheque=True.
    total_late_uncollected = SUM(due_amount) — identity-equal with KPI 5 (Decision 14.2).
    3 concurrent RPCs: search_count, read_group (SUM due_amount), search_read (page).
    """
    if project_id not in _VALID_PROJECT_IDS:
        raise ValueError(
            f"Invalid project_id: {project_id}. Must be one of {sorted(_VALID_PROJECT_IDS)}."
        )

    _own_client = client is None
    _client = client or OdooClient()
    assert _client.is_read_only  # Rule R10
    _log = logger.bind(request_id=request_id)

    page_size = _clamp_page_size(page_size)
    sort_by, sort_dir = _normalize_sort(sort_by, sort_dir)

    today = _cache.today_str()
    ps_clause = (
        ("payment_state", "=", payment_state)
        if payment_state in ("unpaid", "partial")
        else ("payment_state", "in", ["unpaid", "partial"])
    )
    base_domain: list = [
        ("state", "=", "post"),
        ps_clause,
        ("date", "<", today),
        ("project_id", "=", project_id),
    ]
    if has_pending_cheque is True:
        base_domain.append(("check_pending_amount", ">", 0))
    elif has_pending_cheque is False:
        base_domain.append(("check_pending_amount", "=", 0))

    page_domain = list(base_domain)
    if cursor:
        cur = _decode_cursor(cursor)
        if cur:
            page_domain += _build_keyset_clause(cur["sb"], cur["sd"], cur["sv"], cur["id"])

    order = _odoo_order(sort_by, sort_dir)
    t0 = time.monotonic()
    try:
        await _client.authenticate()
        total_count, agg_rows, rows = await asyncio.gather(
            _client.execute_kw(_MODEL, "search_count", args=[base_domain]),
            _client.execute_kw(
                _MODEL, "read_group",
                args=[base_domain, ["due_amount"], []],
                kwargs={"lazy": False},
            ),
            _client.execute_kw(
                _MODEL, "search_read",
                args=[page_domain, _DRILL_FIELDS],
                kwargs={"limit": page_size + 1, "order": order},
            ),
        )
    except Exception as exc:
        raise OdooQueryError(
            f"Project drill-down (project_id={project_id}) failed: {exc}"
        ) from exc
    finally:
        if _own_client:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)
    _log.info(
        f"Project drill-down (project_id={project_id}): "
        f"{int(total_count or 0)} total in {rpc_ms}ms"
    )

    agg_row    = agg_rows[0] if agg_rows else {}
    total_late = float(agg_row.get("due_amount") or 0.0)

    has_next = len(rows) > page_size
    rows = rows[:page_size]
    items = [_serialize_row(r) for r in rows]
    next_cur = _next_cursor_from_items(items, sort_by, sort_dir) if has_next else None

    return {
        "version": "1.0",
        "data": {
            "project_id":             project_id,
            "project_name_ar":        _PROJECT_NAMES_AR.get(project_id, ""),
            "project_name_en":        _PROJECT_NAMES_EN.get(project_id, ""),
            "total_late_uncollected": total_late,
            "total_record_count":     int(total_count or 0),
            "items":                  items,
        },
        "meta": _build_meta(
            request_id, rpc_ms, page_size, int(total_count or 0),
            cursor, next_cur, has_next,
            {
                "project_id": project_id,
                "today": today,
                "payment_state": payment_state,
                "has_pending_cheque": has_pending_cheque,
            },
            {"sort_by": sort_by, "sort_dir": sort_dir},
        ),
    }


async def get_trend_drilldown(
    request_id: str,
    month: str,
    cursor: Optional[str] = None,
    page_size: int = _DEFAULT_PAGE_SIZE,
    sort_by: str = "due_amount",
    sort_dir: str = "desc",
    payment_state: Optional[str] = None,
    has_pending_cheque: Optional[bool] = None,
    client: Optional[OdooClient] = None,
) -> dict:
    """Paginated drill-down for one trend month — KPI 6 axis.

    Decision 14.5: uses rs.installment (due-date axis) for model consistency
    across all 5 drill-downs. month must be YYYY-MM within the trailing 6 months;
    empty month returns 200 OK with empty items (ambiguity #7 resolved).

    Domain: state=post, date in [YYYY-MM-01, YYYY-MM-last]. No payment_state filter —
    shows all installments due in the month (paid, partial, unpaid).
    2 concurrent RPCs: search_count + search_read.
    """
    try:
        month_date = date.fromisoformat(f"{month}-01")
    except ValueError:
        raise ValueError(f"Invalid month format: {month!r}. Expected YYYY-MM.")

    # Decision 14.11: only the trailing 6 calendar months are valid.
    today_cairo = datetime.now(_LA_VERDE_TZ).date()
    months_behind = (
        (today_cairo.year - month_date.year) * 12
        + (today_cairo.month - month_date.month)
    )
    if months_behind < 0:
        raise ValueError(
            f"Month {month!r} is in the future — only the trailing 6 months are valid."
        )
    if months_behind > 5:
        raise ValueError(
            f"Month {month!r} is out of range — only the trailing 6 months are valid."
        )

    _own_client = client is None
    _client = client or OdooClient()
    assert _client.is_read_only  # Rule R10
    _log = logger.bind(request_id=request_id)

    page_size = _clamp_page_size(page_size)
    sort_by, sort_dir = _normalize_sort(sort_by, sort_dir)

    _, last_day    = calendar.monthrange(month_date.year, month_date.month)
    period_start   = month_date.isoformat()
    period_end     = date(month_date.year, month_date.month, last_day).isoformat()

    base_domain: list = [
        ("state", "=", "post"),
        ("date", ">=", period_start),
        ("date", "<=", period_end),
    ]
    if payment_state in ("unpaid", "partial"):
        base_domain.append(("payment_state", "=", payment_state))
    if has_pending_cheque is True:
        base_domain.append(("check_pending_amount", ">", 0))
    elif has_pending_cheque is False:
        base_domain.append(("check_pending_amount", "=", 0))

    page_domain = list(base_domain)
    if cursor:
        cur = _decode_cursor(cursor)
        if cur:
            page_domain += _build_keyset_clause(cur["sb"], cur["sd"], cur["sv"], cur["id"])

    order = _odoo_order(sort_by, sort_dir)
    t0 = time.monotonic()
    try:
        await _client.authenticate()
        total_count, rows = await asyncio.gather(
            _client.execute_kw(_MODEL, "search_count", args=[base_domain]),
            _client.execute_kw(
                _MODEL, "search_read",
                args=[page_domain, _DRILL_FIELDS],
                kwargs={"limit": page_size + 1, "order": order},
            ),
        )
    except Exception as exc:
        raise OdooQueryError(f"Trend drill-down ({month}) failed: {exc}") from exc
    finally:
        if _own_client:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)
    _log.info(f"Trend drill-down ({month}): {int(total_count or 0)} total in {rpc_ms}ms")

    has_next = len(rows) > page_size
    rows = rows[:page_size]
    items = [_serialize_row(r) for r in rows]
    next_cur = _next_cursor_from_items(items, sort_by, sort_dir) if has_next else None

    return {
        "version": "1.0",
        "data": {"month": month, "items": items},
        "meta": _build_meta(
            request_id, rpc_ms, page_size, int(total_count or 0),
            cursor, next_cur, has_next,
            {
                "month": month,
                "period_start": period_start,
                "period_end": period_end,
                "payment_state": payment_state,
                "has_pending_cheque": has_pending_cheque,
            },
            {"sort_by": sort_by, "sort_dir": sort_dir},
        ),
    }
