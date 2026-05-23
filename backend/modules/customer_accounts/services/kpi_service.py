"""
Customer Accounts KPI service — business logic for Module 3 KPIs.

Data source: rs.installment (partner-level aggregation) and
rs.account.payment.reconcile (wallet balance) via the shared read-only OdooClient.
All methods are async. No method ever calls create, write, or unlink.

M3-S2 scope: get_total_customer_receivables() (KPI A).
KPIs B and C are implemented in M3-S3 and M3-S4.
"""

import time
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from backend.core.exceptions import OdooQueryError, ReadOnlyViolationError
from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient
from backend.modules.customer_accounts.services import cache as _cache

_FORBIDDEN_WRITE_METHODS = frozenset({"create", "write", "unlink"})

_MODEL = "rs.installment"
_CACHE_KEY_PREFIX_KPIA = "kpi:total_customer_receivables"


def _assert_read_only() -> None:
    """Defense-in-depth: abort if any write method has leaked into ALLOWED_METHODS."""
    violations = ALLOWED_METHODS & _FORBIDDEN_WRITE_METHODS
    if violations:
        raise ReadOnlyViolationError(
            f"ALLOWED_METHODS contains forbidden write method(s): {sorted(violations)}. "
            "The Odoo client is no longer strictly read-only. Halting before any RPC."
        )


async def get_total_customer_receivables(client: Optional[OdooClient] = None) -> dict:
    """Return KPI A — Total Customer Receivables.

    Queries rs.installment filtered to state='post' and aggregates SUM(due_amount)
    grouped by partner_id. Returns the portfolio-wide total and the distinct
    customer count in a single read_group call.

    Domain: [('state', '=', 'post')] — confirmed in M3-S1 discovery (commit 00f3abf).
    Null-partner check passed: no posted installments have partner_id=False, so the
    grouped sum equals the flat sum exactly (MODULE_3_DISCOVERY_M3S1.md §2).

    Derivation:
        value          = SUM(due_amount) across all partner groups
        customer_count = len(read_group rows)   — one row per distinct partner_id
        record_count   = SUM(__count per row)   — total installment count across groups
                         confirmed from M3-S1 B1 output: __count present on rs.installment
                         groupby partner_id (values: 76, 1, 4, 5, 20 … per top-20 partners)

    Return shape::

        {
            "value":           float,  # EGP SUM(due_amount) across all posted customers
            "customer_count":  int,    # distinct partner_id groups
            "record_count":    int,    # total posted installments (sum of __count per group)
            "currency":        "EGP",
            "as_of":           str,    # ISO 8601 UTC datetime of the query
            "cache_status":    str,    # "fresh" or "cached"
            "rpc_duration_ms": int,    # 0 if served from cache
            "domain":          list,   # [('state', '=', 'post')]
        }

    Raises:
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated with a write method.
        OdooQueryError: if the Odoo RPC fails for any reason (network, auth, RPC error).
    """
    _assert_read_only()

    domain: list = [("state", "=", "post")]
    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_KPIA)

    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"Cache hit: {cache_key}")
        return {**cached, "cache_status": "cached", "rpc_duration_ms": 0}

    logger.info(f"Cache miss: {cache_key} — querying Odoo")

    _client = client if client is not None else OdooClient()

    t0 = time.monotonic()
    try:
        rows = await _client.execute_kw(
            _MODEL,
            "read_group",
            args=[domain, ["due_amount"], ["partner_id"]],
            kwargs={"lazy": False},
        )
    except Exception as exc:
        raise OdooQueryError(
            f"read_group on {_MODEL} (groupby partner_id) failed: {exc}"
        ) from exc
    finally:
        if client is None:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        f"Odoo read_group on {_MODEL} (groupby partner_id) in {rpc_ms}ms"
        f" | cache_key={cache_key}"
    )

    value          = sum(float(r.get("due_amount") or 0.0) for r in rows)
    customer_count = len(rows)
    record_count   = sum(int(r.get("__count") or 0) for r in rows)

    result: dict = {
        "value":           value,
        "customer_count":  customer_count,
        "record_count":    record_count,
        "currency":        "EGP",
        "as_of":           datetime.now(timezone.utc).isoformat(),
        "cache_status":    "fresh",
        "rpc_duration_ms": rpc_ms,
        "domain":          domain,
    }

    _cache.set(cache_key, result)
    return result
