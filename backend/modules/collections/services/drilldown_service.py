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
from backend.modules.collections.services.kpi_service import _compute_bucket_ends
from backend.modules.collections.installment_type_names import get_type_name_ar

_MODEL = "rs.installment"
_LA_VERDE_TZ = ZoneInfo("Africa/Cairo")

_BUCKET_URL_TO_INTERNAL: dict[str, str] = {
    "month":   "this_month",
    "quarter": "this_quarter",
    "half":    "this_half",
    "year":    "this_year",
}

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


async def get_forecast_drilldown(
    request_id: str,
    bucket_url_key: str,
    cursor: Optional[str] = None,
    page_size: int = _DEFAULT_PAGE_SIZE,
    sort_by: str = "due_amount",
    sort_dir: str = "desc",
    payment_state: Optional[str] = None,
    has_pending_cheque: Optional[bool] = None,
    client: Optional[OdooClient] = None,
) -> dict:
    """Paginated drill-down for one KPI 7 forecast bucket.

    bucket_url_key ∈ {'month', 'quarter', 'half', 'year'} maps to internal bucket name.
    Domain: state=post, payment_state IN [unpaid,partial], date in [today_cairo, bucket_end].
    Optional narrowing: payment_state='unpaid'|'partial', has_pending_cheque=True.
    2 concurrent RPCs: search_count + search_read.
    """
    internal_bucket = _BUCKET_URL_TO_INTERNAL.get(bucket_url_key)
    if internal_bucket is None:
        raise ValueError(f"Unknown bucket key: {bucket_url_key!r}. Valid: {list(_BUCKET_URL_TO_INTERNAL)}")

    _own_client = client is None
    _client = client or OdooClient()
    assert _client.is_read_only  # Rule R10
    _log = logger.bind(request_id=request_id)

    page_size = _clamp_page_size(page_size)
    sort_by, sort_dir = _normalize_sort(sort_by, sort_dir)

    today_cairo = datetime.now(_LA_VERDE_TZ).date()
    today_str   = today_cairo.isoformat()
    bucket_ends = _compute_bucket_ends(today_cairo)
    bucket_end_str = bucket_ends[internal_bucket].isoformat()

    ps_clause = (
        ("payment_state", "=", payment_state)
        if payment_state in ("unpaid", "partial")
        else ("payment_state", "in", ["unpaid", "partial"])
    )
    base_domain: list = [
        ("state", "=", "post"),
        ps_clause,
        ("date", ">=", today_str),
        ("date", "<=", bucket_end_str),
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
        total_count, rows = await asyncio.gather(
            _client.execute_kw(_MODEL, "search_count", args=[base_domain]),
            _client.execute_kw(
                _MODEL, "search_read",
                args=[page_domain, _DRILL_FIELDS],
                kwargs={"limit": page_size + 1, "order": order},
            ),
        )
    except Exception as exc:
        raise OdooQueryError(f"Forecast drill-down ({bucket_url_key}) failed: {exc}") from exc
    finally:
        if _own_client:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)
    _log.info(
        f"Forecast drill-down ({internal_bucket}): {int(total_count or 0)} total in {rpc_ms}ms"
    )

    has_next = len(rows) > page_size
    rows = rows[:page_size]
    items = [_serialize_row(r) for r in rows]
    next_cur = _next_cursor_from_items(items, sort_by, sort_dir) if has_next else None

    return {
        "version": "1.0",
        "data": {
            "bucket":         internal_bucket,
            "bucket_url_key": bucket_url_key,
            "items":          items,
        },
        "meta": _build_meta(
            request_id, rpc_ms, page_size, int(total_count or 0),
            cursor, next_cur, has_next,
            {
                "bucket": internal_bucket,
                "today": today_str,
                "period_end": bucket_end_str,
                "payment_state": payment_state,
                "has_pending_cheque": has_pending_cheque,
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
