"""
Customer Accounts KPI service — business logic for Module 3 KPIs.

Data source: rs.installment (partner-level aggregation) and
rs.account.payment.reconcile (wallet balance) via the shared read-only OdooClient.
All methods are async. No method ever calls create, write, or unlink.

M3-S2 scope: get_total_customer_receivables() (KPI A).
M3-S3 scope: get_top_overdue_customers()       (KPI B).
M3-S4 scope: get_unallocated_wallet_balance()  (KPI C).
             get_refunds_summary()             (Refunds alert section).
"""

import time
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from backend.core.exceptions import OdooQueryError, ReadOnlyViolationError
from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient
from backend.modules.customer_accounts.services import cache as _cache

_FORBIDDEN_WRITE_METHODS = frozenset({"create", "write", "unlink"})

_MODEL          = "rs.installment"
_RECONCILE_MODEL = "rs.account.payment.reconcile"

_CACHE_KEY_PREFIX_KPIA    = "kpi:total_customer_receivables"
_CACHE_KEY_PREFIX_KPIB    = "kpi:top_overdue_customers"
_CACHE_KEY_PREFIX_KPIC    = "kpi:unallocated_wallet_balance"
_CACHE_KEY_PREFIX_REFUNDS = "kpi:refunds_summary"

# Top-N used for concentration ratio (KPI B). Named constant so schema and
# service stay in sync if the Board ever requests a different N.
_CONCENTRATION_N = 10


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


async def get_top_overdue_customers(client: Optional[OdooClient] = None) -> dict:
    """Return KPI B — Top Overdue Customers.

    Queries rs.installment with the Late domain (Candidate C, three-clause —
    confirmed in M3-S1 discovery, commit 00f3abf) grouped by partner_id.
    Returns the portfolio-wide overdue total, distinct overdue customer count,
    top-20 customer list sorted by due_amount descending, and a top-N
    concentration ratio.

    Domain: Late (Candidate C — MODULE_3_DISCOVERY_M3S1.md §3, R1a PASS):
        state='post'  +  payment_state in [unpaid,partial]  +  date < today

    today source: _cache.today_str() = Cairo-local date (Africa/Cairo, Decision 5.9).
    Uses the same source as Collections KPI 2 (get_late_uncollected) for
    consistency — both KPIs query the identical Late installment set.

    All rows are fetched before sorting; the total and customer_count reflect
    ALL overdue customers, not only the top-20. The top-N concentration ratio
    uses _CONCENTRATION_N (currently 10) as its denominator's complement.

    Derivation:
        total_overdue          = SUM(due_amount) across ALL overdue partner groups
        overdue_customer_count = len(read_group rows)
        record_count           = SUM(__count per row)   — total overdue installments
        top_customers          = top-20 rows sorted by due_amount descending
        top_n_concentration    = { n, amount=SUM(top-N due_amount), pct=amount/total*100 }

    Customer names (partner_id[1]) are included in top_customers for Board display.
    They must not appear in any logger call — only counts and amounts are logged.

    Return shape::

        {
            "total_overdue":          float,  # EGP SUM(due_amount) all overdue customers
            "overdue_customer_count": int,    # distinct overdue partner_id groups
            "record_count":           int,    # total overdue installments (sum __count)
            "top_n_concentration": {
                "n":      int,                # _CONCENTRATION_N (10)
                "amount": float,              # EGP SUM(due_amount) for top-N
                "pct":    float,              # amount / total_overdue * 100
            },
            "top_customers": [               # up to 20 rows, sorted due_amount desc
                {
                    "rank":               int,
                    "customer_id":        int,
                    "customer_name":      str,
                    "due_amount":         float,
                    "installment_count":  int,
                },
                ...
            ],
            "currency":              "EGP",
            "as_of":                 str,    # ISO 8601 UTC datetime of the query
            "cache_status":          str,    # "fresh" or "cached"
            "rpc_duration_ms":       int,    # 0 if served from cache
            "domain":                list,
        }

    Raises:
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated with a write method.
        OdooQueryError: if the Odoo RPC fails for any reason (network, auth, RPC error).
    """
    _assert_read_only()

    today: str = _cache.today_str()
    domain: list = [
        ("state",         "=",  "post"),
        ("payment_state", "in", ["unpaid", "partial"]),
        ("date",          "<",  today),
    ]
    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_KPIB)

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
            f"read_group on {_MODEL} (Late domain, groupby partner_id) failed: {exc}"
        ) from exc
    finally:
        if client is None:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        f"Odoo read_group on {_MODEL} (Late domain, groupby partner_id)"
        f" returned {len(rows)} groups in {rpc_ms}ms | cache_key={cache_key}"
    )

    # Totals from ALL rows — must precede slicing to keep concentration ratio correct.
    total_overdue   = sum(float(r.get("due_amount") or 0.0) for r in rows)
    customer_count  = len(rows)
    record_count    = sum(int(r.get("__count") or 0) for r in rows)

    sorted_rows = sorted(
        rows,
        key=lambda r: float(r.get("due_amount") or 0.0),
        reverse=True,
    )

    top_n_amount = sum(
        float(r.get("due_amount") or 0.0) for r in sorted_rows[:_CONCENTRATION_N]
    )
    top_n_pct = round(top_n_amount / total_overdue * 100, 2) if total_overdue > 0 else 0.0

    top_customers = []
    for i, r in enumerate(sorted_rows[:20]):
        partner_raw = r.get("partner_id")
        if isinstance(partner_raw, (list, tuple)) and partner_raw:
            cust_id   = int(partner_raw[0])
            cust_name = str(partner_raw[1])
        else:
            cust_id   = int(partner_raw) if partner_raw else 0
            cust_name = ""
        top_customers.append({
            "rank":              i + 1,
            "customer_id":       cust_id,
            "customer_name":     cust_name,
            "due_amount":        float(r.get("due_amount") or 0.0),
            "installment_count": int(r.get("__count") or 0),
        })

    result: dict = {
        "total_overdue":          total_overdue,
        "overdue_customer_count": customer_count,
        "record_count":           record_count,
        "top_n_concentration": {
            "n":      _CONCENTRATION_N,
            "amount": top_n_amount,
            "pct":    top_n_pct,
        },
        "top_customers":          top_customers,
        "currency":               "EGP",
        "as_of":                  datetime.now(timezone.utc).isoformat(),
        "cache_status":           "fresh",
        "rpc_duration_ms":        rpc_ms,
        "domain":                 domain,
    }

    _cache.set(cache_key, result)
    return result


async def get_unallocated_wallet_balance(client: Optional[OdooClient] = None) -> dict:
    """Return KPI C — Unallocated Wallet Balance.

    Queries rs.account.payment.reconcile filtered to state='post' AND
    residual_amount>0, grouped by partner_id.  Returns the portfolio-wide
    unallocated total and the count of distinct customers holding a balance.

    Domain: [('state','=','post'), ('residual_amount','>',0)]
      — confirmed in M3-S1 discovery (MODULE_3_DISCOVERY_M3S1.md §5).
      — residual_amount>0 is intentional: excludes the 7 refund records
        (amount<0 → residual_amount<0).  Including them would understate the
        wallet balance (MODULE_3_PLAN.md §3 KPI C, §4).

    Baseline (M3-S1, 2026-05-23, moving): 17,214,301.92 EGP / 27 customers.
    The value changes as wallet balances are applied to installments.

    Return shape::

        {
            "value":           float,  # EGP SUM(residual_amount) — positive
            "customer_count":  int,    # distinct partner_id groups
            "record_count":    int,    # total reconcile records with residual > 0
            "currency":        "EGP",
            "as_of":           str,    # ISO 8601 UTC datetime of the query
            "cache_status":    str,    # "fresh" or "cached"
            "rpc_duration_ms": int,    # 0 if served from cache
            "domain":          list,
        }

    Raises:
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated.
        OdooQueryError: if the Odoo RPC fails.
    """
    _assert_read_only()

    domain: list = [
        ("state",           "=", "post"),
        ("residual_amount", ">", 0),
    ]
    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_KPIC)

    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"Cache hit: {cache_key}")
        return {**cached, "cache_status": "cached", "rpc_duration_ms": 0}

    logger.info(f"Cache miss: {cache_key} — querying Odoo")

    _client = client if client is not None else OdooClient()

    t0 = time.monotonic()
    try:
        rows = await _client.execute_kw(
            _RECONCILE_MODEL,
            "read_group",
            args=[domain, ["residual_amount"], ["partner_id"]],
            kwargs={"lazy": False},
        )
    except Exception as exc:
        raise OdooQueryError(
            f"read_group on {_RECONCILE_MODEL} (KPI C, groupby partner_id) failed: {exc}"
        ) from exc
    finally:
        if client is None:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        f"Odoo read_group on {_RECONCILE_MODEL} (KPI C, groupby partner_id)"
        f" returned {len(rows)} groups in {rpc_ms}ms | cache_key={cache_key}"
    )

    value          = sum(float(r.get("residual_amount") or 0.0) for r in rows)
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


async def get_refunds_summary(client: Optional[OdooClient] = None) -> dict:
    """Return Refunds alert-section summary.

    Queries rs.account.payment.reconcile filtered to state='post' AND
    amount<0, grouped by partner_id.  Returns the total refund amount
    (a negative number), the record count, and the count of records with
    no associated partner (partner_id = False).

    Flow direction: sign of `amount` is the reliable indicator
    (MODULE_3_DISCOVERY_PHASE_3.md §4.1). payment_type is NOT used — all
    205 live records show payment_type='inbound', including the 7 refunds.

    Domain: [('state','=','post'), ('amount','<',0)]
      — confirmed in M3-S1 discovery (MODULE_3_DISCOVERY_M3S1.md §6).

    Baseline (M3-S1, 2026-05-23): −719,812.00 EGP / 7 records / 0 null-partner.

    Return shape::

        {
            "total_refunds":      float,  # EGP SUM(amount) — negative
            "refund_count":       int,    # total records (sum of __count per group)
            "null_partner_count": int,    # records where partner_id = False
            "currency":           "EGP",
            "as_of":              str,
            "cache_status":       str,    # "fresh" or "cached"
            "rpc_duration_ms":    int,    # 0 if served from cache
            "domain":             list,
        }

    Raises:
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated.
        OdooQueryError: if the Odoo RPC fails.
    """
    _assert_read_only()

    domain: list = [
        ("state",  "=", "post"),
        ("amount", "<", 0),
    ]
    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_REFUNDS)

    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"Cache hit: {cache_key}")
        return {**cached, "cache_status": "cached", "rpc_duration_ms": 0}

    logger.info(f"Cache miss: {cache_key} — querying Odoo")

    _client = client if client is not None else OdooClient()

    t0 = time.monotonic()
    try:
        rows = await _client.execute_kw(
            _RECONCILE_MODEL,
            "read_group",
            args=[domain, ["amount"], ["partner_id"]],
            kwargs={"lazy": False},
        )
    except Exception as exc:
        raise OdooQueryError(
            f"read_group on {_RECONCILE_MODEL} (Refunds, groupby partner_id) failed: {exc}"
        ) from exc
    finally:
        if client is None:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        f"Odoo read_group on {_RECONCILE_MODEL} (Refunds, groupby partner_id)"
        f" returned {len(rows)} groups in {rpc_ms}ms | cache_key={cache_key}"
    )

    total_refunds      = sum(float(r.get("amount") or 0.0) for r in rows)
    refund_count       = sum(int(r.get("__count") or 0) for r in rows)
    null_partner_count = sum(
        int(r.get("__count") or 0)
        for r in rows
        if not r.get("partner_id")
    )

    result: dict = {
        "total_refunds":      total_refunds,
        "refund_count":       refund_count,
        "null_partner_count": null_partner_count,
        "currency":           "EGP",
        "as_of":              datetime.now(timezone.utc).isoformat(),
        "cache_status":       "fresh",
        "rpc_duration_ms":    rpc_ms,
        "domain":             domain,
    }

    _cache.set(cache_key, result)
    return result
