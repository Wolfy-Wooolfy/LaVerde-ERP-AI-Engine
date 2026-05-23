"""
Customer Accounts drill-down service — M3-S6.

Returns a full account statement for one customer by partner_id:
  header      — partner_id, customer_name
  exposure    — total_due, late_due, future_due, paid_cash, total_original, counts
  behavior    — payment_ratio_pct, wallet_balance
  installments — paginated unpaid installments (late + future), each labelled by timing

Design decisions:
  No caching — all calls hit Odoo live (same as Collections drilldown Decision 14.7).
  assert _client.is_read_only at function start (Rule R10, Decision 14.10).
  6 concurrent RPCs via asyncio.gather after authenticate().
  Assertion: late_due + future_due == SUM(due_amount all posted) — التصحيح المفاهيمي.
  payment_ratio_pct = SUM(x_studio_actual_paid_amount) / SUM(amount) × 100
    — cash only; NOT paid_amount which includes pending cheques. DR1 confirmed M3-S6 discovery.
  today from _cache.today_str() — same source as KPI B for consistency (see D-2).
  cursor pagination (keyset) for installments — same pattern as Collections drilldown.
"""

import asyncio
import base64
import json
import time
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from backend.core.exceptions import OdooQueryError
from backend.modules.collections.installment_type_names import get_type_name_ar
from backend.modules.customer_accounts.services import cache as _cache
from backend.shared.odoo.client import OdooClient

_MODEL          = "rs.installment"
_RECONCILE_MODEL = "rs.account.payment.reconcile"

_DRILL_FIELDS: list[str] = [
    "id", "date", "amount", "due_amount",
    "payment_state", "installment_type_id",
]

_DEFAULT_PAGE_SIZE = 50
_MAX_PAGE_SIZE     = 200

_VALID_SORT_FIELDS = frozenset({"date", "amount", "due_amount"})
_VALID_SORT_DIRS   = frozenset({"asc", "desc"})


# ── Cursor helpers (keyset / cursor-based pagination) ─────────────────────────

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

def _serialize_installment_row(rec: dict, today: str) -> dict:
    """Serialize one rs.installment record for the drill-down panel table.

    timing is computed Python-side: 'late' if date < today, 'future' otherwise.
    This avoids an extra RPC and is definitionally consistent with the domains used
    in the 6-RPC gather.
    """
    inst_typ = rec.get("installment_type_id")
    type_id  = (
        int(inst_typ[0]) if isinstance(inst_typ, (list, tuple)) and inst_typ
        else (int(inst_typ) if inst_typ else 0)
    )
    rec_date = str(rec.get("date") or "")
    timing   = "late" if rec_date < today else "future"
    return {
        "record_id":                int(rec["id"]),
        "date":                     rec_date,
        "installment_type_id":      type_id,
        "installment_type_name_ar": get_type_name_ar(type_id),
        "payment_state":            str(rec.get("payment_state") or ""),
        "timing":                   timing,
        "amount":                   float(rec.get("amount") or 0.0),
        "due_amount":               float(rec.get("due_amount") or 0.0),
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


# ── Service ───────────────────────────────────────────────────────────────────

async def get_customer_drilldown(
    partner_id: int,
    request_id: str,
    cursor: Optional[str] = None,
    page_size: int = _DEFAULT_PAGE_SIZE,
    sort_by: str = "date",
    sort_dir: str = "asc",
    client: Optional[OdooClient] = None,
) -> dict:
    """Full account statement for one customer.

    6 concurrent RPCs:
      1. read_group(base_all, aggregates, ["partner_id"]) → name + totals
      2. read_group(late_domain, ["due_amount"], [])      → late aggregate
      3. read_group(future_domain, ["due_amount"], [])    → future aggregate
      4. read_group(wallet_domain, ["residual_amount"],[])→ wallet balance
      5. search_count(unpaid_domain)                      → pagination total
      6. search_read(page_domain, _DRILL_FIELDS, ...)     → installment page

    Assertion (التصحيح المفاهيمي):
      late_due + future_due == SUM(due_amount for all posted installments)
      This holds because paid installments have due_amount=0, so the sum over
      all posted equals the sum over unpaid/partial only.

    Raises:
      AssertionError:  integrity check failed (delta >= 1.0 EGP).
      OdooQueryError:  any RPC failure.
    """
    _own_client = client is None
    _client = client or OdooClient()
    assert _client.is_read_only  # Rule R10

    page_size = _clamp_page_size(page_size)
    sort_by, sort_dir = _normalize_sort(sort_by, sort_dir)
    today = _cache.today_str()

    # ── Domains ───────────────────────────────────────────────────────────────
    base_all = [("state", "=", "post"), ("partner_id", "=", partner_id)]
    unpaid_domain = base_all + [("payment_state", "in", ["unpaid", "partial"])]
    late_domain   = unpaid_domain + [("date", "<",  today)]
    future_domain = unpaid_domain + [("date", ">=", today)]
    wallet_domain = [
        ("state",           "=", "post"),
        ("partner_id",      "=", partner_id),
        ("residual_amount", ">", 0),
    ]

    # ── Cursor clause for installment page ────────────────────────────────────
    page_domain = list(unpaid_domain)
    if cursor:
        cur = _decode_cursor(cursor)
        if cur:
            page_domain += _build_keyset_clause(cur["sb"], cur["sd"], cur["sv"], cur["id"])
    order = _odoo_order(sort_by, sort_dir)

    # ── 6 concurrent RPCs ─────────────────────────────────────────────────────
    t0 = time.monotonic()
    try:
        await _client.authenticate()
        (
            all_rg_rows,
            late_rg_rows,
            future_rg_rows,
            wallet_rg_rows,
            unpaid_count,
            inst_rows,
        ) = await asyncio.gather(
            _client.execute_kw(
                _MODEL, "read_group",
                args=[base_all,
                      ["amount", "due_amount", "paid_amount",
                       "x_studio_actual_paid_amount"],
                      ["partner_id"]],
                kwargs={"lazy": False},
            ),
            _client.execute_kw(
                _MODEL, "read_group",
                args=[late_domain, ["due_amount"], []],
                kwargs={"lazy": False},
            ),
            _client.execute_kw(
                _MODEL, "read_group",
                args=[future_domain, ["due_amount"], []],
                kwargs={"lazy": False},
            ),
            _client.execute_kw(
                _RECONCILE_MODEL, "read_group",
                args=[wallet_domain, ["residual_amount"], []],
                kwargs={"lazy": False},
            ),
            _client.execute_kw(_MODEL, "search_count", args=[unpaid_domain]),
            _client.execute_kw(
                _MODEL, "search_read",
                args=[page_domain, _DRILL_FIELDS],
                kwargs={"limit": page_size + 1, "order": order},
            ),
        )
    except Exception as exc:
        raise OdooQueryError(
            f"Customer drill-down (partner_id={partner_id}) failed: {exc}"
        ) from exc
    finally:
        if _own_client:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        f"Customer drill-down partner_id={partner_id}: "
        f"all_rows={len(all_rg_rows)}, unpaid={unpaid_count}, "
        f"page={min(len(inst_rows), page_size)} in {rpc_ms}ms"
    )

    # ── Extract aggregates ────────────────────────────────────────────────────
    all_row = all_rg_rows[0] if all_rg_rows else {}

    partner_raw   = all_row.get("partner_id")
    customer_name = (
        str(partner_raw[1])
        if isinstance(partner_raw, (list, tuple)) and len(partner_raw) > 1
        else ""
    )

    total_amount = float(all_row.get("amount") or 0.0)
    all_due      = float(all_row.get("due_amount") or 0.0)
    total_actual = float(all_row.get("x_studio_actual_paid_amount") or 0.0)
    total_inst   = int(all_row.get("__count") or 0)

    late_due   = float((late_rg_rows[0]   if late_rg_rows   else {}).get("due_amount") or 0.0)
    future_due = float((future_rg_rows[0] if future_rg_rows else {}).get("due_amount") or 0.0)

    wallet_row      = wallet_rg_rows[0] if wallet_rg_rows else {}
    wallet_balance  = float(wallet_row.get("residual_amount") or 0.0)
    wallet_records  = int(wallet_row.get("__count") or 0)

    # ── Assertion: التصحيح المفاهيمي ──────────────────────────────────────────
    # late + future must equal all posted due_amount.
    # Paid installments have due_amount=0 by definition, so the equality holds
    # when data is consistent. A delta >= 1.0 EGP indicates a data anomaly.
    total_due = late_due + future_due
    _delta = abs(total_due - all_due)
    if _delta >= 1.0:
        logger.error(
            f"Integrity assertion FAILED (partner_id={partner_id}): "
            f"late({late_due:.2f}) + future({future_due:.2f}) = {total_due:.2f} "
            f"but all_posted_due = {all_due:.2f}  delta={_delta:.4f}"
        )
        raise AssertionError(
            f"Drill-down integrity: late+future={total_due:.2f} "
            f"!= all_posted_due={all_due:.2f}  delta={_delta:.4f} EGP "
            f"for partner_id={partner_id}"
        )

    # ── Payment ratio (DR1 confirmed M3-S6 discovery) ─────────────────────────
    # Numerator: x_studio_actual_paid_amount = confirmed cash received only.
    # NOT paid_amount (includes pending cheques → inflates ratio).
    # Denominator: SUM(amount) all posted installments = full contractual value.
    payment_ratio_pct = (
        round(total_actual / total_amount * 100, 2) if total_amount > 0 else 0.0
    )

    # ── Paginate installment list ─────────────────────────────────────────────
    has_next = len(inst_rows) > page_size
    inst_rows = inst_rows[:page_size]
    items    = [_serialize_installment_row(r, today) for r in inst_rows]
    next_cur = _next_cursor_from_items(items, sort_by, sort_dir) if has_next else None

    return {
        "version": "1.0",
        "data": {
            "header": {
                "partner_id":    partner_id,
                "customer_name": customer_name,
            },
            "exposure": {
                "total_due_egp":            total_due,
                "late_due_egp":             late_due,
                "future_due_egp":           future_due,
                "paid_cash_egp":            total_actual,
                "total_original_egp":       total_amount,
                "total_installments":       total_inst,
                "unpaid_installment_count": int(unpaid_count or 0),
            },
            "behavior": {
                "payment_ratio_pct":  payment_ratio_pct,
                "wallet_balance_egp": wallet_balance,
                "wallet_record_count": wallet_records,
            },
            "installments": {
                "items":          items,
                "total_count":    int(unpaid_count or 0),
                "cursor_current": cursor,
                "cursor_next":    next_cur,
                "has_next":       has_next,
            },
        },
        "meta": {
            "request_id":      request_id,
            "as_of":           datetime.now(timezone.utc).isoformat(),
            "rpc_duration_ms": rpc_ms,
            "today":           today,
            "page_size":       page_size,
            "sort_by":         sort_by,
            "sort_dir":        sort_dir,
        },
    }
