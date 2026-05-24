"""
Customer Accounts — Refunds detail service (M3-S8).

Returns per-record detail for all posted refunds.
Domain: [('state','=','post'), ('amount','<',0)] — same predicate as get_refunds_summary().
Uses search_read (not read_group) to return individual records.
Sort: date desc (most recent first).

No pagination: the refund set is small (7 records as of M3-S1) and the panel
uses overflow-y scroll for safety if the set grows. Pagination can be added
as a targeted enhancement if the count ever reaches 50+.

READ-ONLY: search_read only. ALLOWED_METHODS not modified.
"""

import time
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from backend.core.exceptions import OdooQueryError, ReadOnlyViolationError
from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient
from backend.modules.customer_accounts.services import cache as _cache

_FORBIDDEN_WRITE_METHODS = frozenset({"create", "write", "unlink"})

_RECONCILE_MODEL = "rs.account.payment.reconcile"

_CACHE_KEY_PREFIX_REFUNDS_DETAIL = "kpi:refunds_detail"

_DETAIL_FIELDS: list[str] = ["id", "partner_id", "amount", "date"]


def _assert_read_only() -> None:
    """Defense-in-depth: abort if any write method has leaked into ALLOWED_METHODS."""
    violations = ALLOWED_METHODS & _FORBIDDEN_WRITE_METHODS
    if violations:
        raise ReadOnlyViolationError(
            f"ALLOWED_METHODS contains forbidden write method(s): {sorted(violations)}. "
            "The Odoo client is no longer strictly read-only. Halting before any RPC."
        )


def _serialize_row(rec: dict) -> dict:
    partner_raw = rec.get("partner_id")
    if isinstance(partner_raw, (list, tuple)) and partner_raw:
        customer_id   = int(partner_raw[0])
        customer_name = str(partner_raw[1])
    else:
        customer_id   = 0
        customer_name = "غير معروف"
    return {
        "record_id":     int(rec["id"]),
        "customer_id":   customer_id,
        "customer_name": customer_name,
        "amount":        float(rec.get("amount") or 0.0),
        "date":          str(rec.get("date") or ""),
    }


async def get_refunds_detail(client: Optional[OdooClient] = None) -> dict:
    """Return per-record detail for all posted refunds.

    Domain: [('state','=','post'), ('amount','<',0)]
      — confirmed in M3-S1 discovery (MODULE_3_DISCOVERY_M3S1.md §6).
      — identical predicate to get_refunds_summary(); the detail endpoint
        exposes individual records instead of an aggregate.

    Flow direction: sign of amount is the reliable indicator (Phase 3 §4.1).
    payment_type='inbound' on all 205 records including 7 refunds — not used.

    partner_id semantics: all 7 refund records have partner_id set (null_partner_count=0
    confirmed M3-S1). 4 point to the named catch-all "عميل غير معروف" partner.
    _serialize_row() returns partner_id[1] as-is; the "غير معروف" fallback only
    fires if partner_id is False (which does not occur in live data).

    Return shape::

        {
            "items": [
                {
                    "record_id":     int,
                    "customer_id":   int,    # partner_id[0]; 0 if partner_id is False
                    "customer_name": str,    # partner_id[1]; "غير معروف" if False
                    "amount":        float,  # negative
                    "date":          str,    # YYYY-MM-DD
                },
                ...
            ],
            "total_amount":    float,  # SUM(amount) — negative
            "record_count":    int,
            "currency":        "EGP",
            "as_of":           str,    # ISO 8601 UTC datetime
            "cache_status":    str,    # "fresh" or "cached"
            "rpc_duration_ms": int,    # 0 if served from cache
            "domain":          list,
        }

    Raises:
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated with a write method.
        OdooQueryError: if the Odoo RPC fails for any reason.
    """
    _assert_read_only()

    domain: list = [
        ("state",  "=", "post"),
        ("amount", "<", 0),
    ]
    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_REFUNDS_DETAIL)

    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"Cache hit: {cache_key}")
        return {**cached, "cache_status": "cached", "rpc_duration_ms": 0}

    logger.info(f"Cache miss: {cache_key} — querying Odoo")

    _client = client if client is not None else OdooClient()

    t0 = time.monotonic()
    try:
        records = await _client.execute_kw(
            _RECONCILE_MODEL,
            "search_read",
            args=[domain, _DETAIL_FIELDS],
            kwargs={"order": "date desc"},
        )
    except Exception as exc:
        raise OdooQueryError(
            f"search_read on {_RECONCILE_MODEL} (Refunds detail) failed: {exc}"
        ) from exc
    finally:
        if client is None:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        f"Odoo search_read on {_RECONCILE_MODEL} (Refunds detail)"
        f" returned {len(records)} records in {rpc_ms}ms | cache_key={cache_key}"
    )

    items        = [_serialize_row(r) for r in records]
    total_amount = sum(item["amount"] for item in items)
    record_count = len(items)

    result: dict = {
        "items":           items,
        "total_amount":    total_amount,
        "record_count":    record_count,
        "currency":        "EGP",
        "as_of":           datetime.now(timezone.utc).isoformat(),
        "cache_status":    "fresh",
        "rpc_duration_ms": rpc_ms,
        "domain":          domain,
    }

    _cache.set(cache_key, result)
    return result
