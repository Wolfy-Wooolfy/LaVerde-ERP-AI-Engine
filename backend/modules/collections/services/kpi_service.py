"""
Collections KPI service — business logic for all 6 MVP KPIs.

Data source: rs.installment (42,970 records as of 2026-05-14) via the
shared read-only OdooClient. All methods are async; no method ever calls
create, write, or unlink.

Session 1 scope: get_late_uncollected() (KPI 2 — Late Uncollected).
KPIs 1, 3, 4, 5, 6 are implemented in future sessions.
"""

import time
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from backend.core.exceptions import OdooQueryError, ReadOnlyViolationError
from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient
from backend.modules.collections.services import cache as _cache

# Methods that must never appear in ALLOWED_METHODS.
_FORBIDDEN_WRITE_METHODS = frozenset({"create", "write", "unlink"})

_MODEL = "rs.installment"
_CACHE_KEY_PREFIX = "kpi:late_uncollected"


def _assert_read_only() -> None:
    """Defense-in-depth: abort if any write method has leaked into ALLOWED_METHODS."""
    violations = ALLOWED_METHODS & _FORBIDDEN_WRITE_METHODS
    if violations:
        raise ReadOnlyViolationError(
            f"ALLOWED_METHODS contains forbidden write method(s): {sorted(violations)}. "
            "The Odoo client is no longer strictly read-only. Halting before any RPC."
        )


def _build_late_domain(today: str) -> list:
    # Immutable — Candidate C, three-clause form validated in
    # MODULE_2_DISCOVERY_PHASE_2.md §3. Do not modify without Khaled's approval.
    return [
        ("state", "=", "post"),
        ("payment_state", "in", ["unpaid", "partial"]),
        ("date", "<", today),
    ]


async def get_late_uncollected(client: Optional[OdooClient] = None) -> dict:
    """Return KPI 2 — Late Uncollected.

    Queries rs.installment for posted, partially-paid-or-unpaid installments
    whose due date is before today, and returns SUM(due_amount) via read_group.

    Return shape::

        {
            "value":           float,  # EGP total due_amount
            "currency":        "EGP",
            "record_count":    int,    # matched installment count
            "as_of":           str,    # ISO 8601 UTC datetime of the query
            "cache_status":    str,    # "fresh" or "cached"
            "rpc_duration_ms": int,    # 0 if served from cache
            "domain":          list,   # exact domain used (paste into Odoo for debugging)
        }

    Raises:
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated with a write method.
        OdooQueryError: if the Odoo RPC fails for any reason (network, auth, RPC error).
    """
    _assert_read_only()

    # Decision 1.7: prefer Odoo server date. ALLOWED_METHODS exposes no
    # server-date method, so falling back to UTC date per Khaled's
    # authorization (MODULE_2_IMPLEMENTATION_DECISIONS.md §1.7).
    today = datetime.now(timezone.utc).date().isoformat()

    domain = _build_late_domain(today)
    cache_key = _cache.make_key(_CACHE_KEY_PREFIX)

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
            args=[domain, ["due_amount"], []],
            kwargs={"lazy": False},
        )
    except Exception as exc:
        raise OdooQueryError(
            f"read_group on {_MODEL} failed: {exc}"
        ) from exc
    finally:
        if client is None:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)
    logger.info(f"Odoo read_group on {_MODEL} in {rpc_ms}ms | cache_key={cache_key}")

    row = rows[0] if rows else {}
    value = float(row.get("due_amount") or 0.0)
    count = int(row.get("__count") or 0)

    result: dict = {
        "value": value,
        "currency": "EGP",
        "record_count": count,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "cache_status": "fresh",
        "rpc_duration_ms": rpc_ms,
        "domain": domain,
    }

    _cache.set(cache_key, result)
    return result
