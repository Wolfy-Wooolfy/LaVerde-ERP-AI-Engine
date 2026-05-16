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

from backend.core.exceptions import OdooQueryError, ReadOnlyViolationError, UnknownProjectError
from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient
from backend.modules.collections.services import cache as _cache

# Methods that must never appear in ALLOWED_METHODS.
_FORBIDDEN_WRITE_METHODS = frozenset({"create", "write", "unlink"})

_MODEL = "rs.installment"
_CACHE_KEY_PREFIX = "kpi:late_uncollected"
_CACHE_KEY_PREFIX_KPI1 = "kpi:total_portfolio_value"
_CACHE_KEY_PREFIX_KPI5 = "kpi:late_uncollected_by_project"

# Phase 2 confirmed project IDs and clean display names (MODULE_2_DISCOVERY_PHASE_2.md §6).
# Odoo returns "Project#New Capital" etc.; we expose clean names to API consumers.
# If read_group returns an ID not in this dict, UnknownProjectError is raised — a signal
# that a new Odoo project has appeared and needs a code update here.
_PROJECT_NAMES: dict[int, str] = {
    1: "New Capital",
    2: "Cassette",
    3: "La puerta",
}


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
    # Using _cache.today_str() so the domain date and cache key date are
    # always identical — tests patch a single function to control both.
    today = _cache.today_str()

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


async def get_total_portfolio_value(client: Optional[OdooClient] = None) -> dict:
    """Return KPI 1 — Total Portfolio Value.

    Queries rs.installment filtered to state='post' (posted installments only)
    and returns SUM(amount) via read_group.

    Domain: [('state', '=', 'post')] — excludes draft (19 records) and
    cancelled (508 records) installments that exist in rs.installment but
    are excluded by Odoo's "All Installments" view. See Decision 2.4.

    Return shape::

        {
            "value":           float,  # EGP total amount across posted installments
            "currency":        "EGP",
            "record_count":    int,    # posted installment count (~42,443)
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
    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_KPI1)

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
            args=[domain, ["amount"], []],
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
    value = float(row.get("amount") or 0.0)
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


async def get_late_uncollected_by_project(client: Optional[OdooClient] = None) -> dict:
    """Return KPI 5 (sub-metric 1) — Late Uncollected per project.

    Queries rs.installment with the same three-clause Candidate C domain as KPI 2,
    grouped by project_id. Returns SUM(due_amount) and record count for each of the
    3 active projects, always in order: New Capital (1), Cassette (2), La puerta (3).

    If read_group returns fewer than 3 projects (e.g., one project has zero late
    records), the missing project is zero-padded in the response (Decision 3.4).

    Return shape::

        {
            "projects": [
                {
                    "project_id":       int,
                    "project_name":     str,
                    "late_uncollected": float,  # EGP, SUM(due_amount)
                    "record_count":     int,
                },
                # ... ordered by project_id ascending: 1, 2, 3
            ],
            "total_late_uncollected": float,  # EGP, sum across all 3 projects
            "total_record_count":     int,
            "currency":               "EGP",
            "as_of":                  str,     # ISO 8601 UTC datetime
            "cache_status":           str,     # "fresh" | "cached"
            "rpc_duration_ms":        int,     # 0 if cached
            "domain":                 list,    # exact three-clause domain
        }

    Raises:
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated with a write method.
        OdooQueryError: if the Odoo RPC fails for any reason.
        UnknownProjectError: if read_group returns a project_id not in _PROJECT_NAMES,
            signalling a new Odoo project that requires a code update.
    """
    _assert_read_only()

    today = _cache.today_str()
    domain = _build_late_domain(today)
    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_KPI5)

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
            args=[domain, ["due_amount"], ["project_id"]],
            kwargs={"lazy": False},
        )
    except Exception as exc:
        raise OdooQueryError(
            f"read_group on {_MODEL} (groupby project_id) failed: {exc}"
        ) from exc
    finally:
        if client is None:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        f"Odoo read_group on {_MODEL} (groupby project_id) in {rpc_ms}ms"
        f" | cache_key={cache_key}"
    )

    # Build per-project lookup from Odoo response.
    # read_group returns project_id as [id, display_name] for many2one fields.
    per_project: dict[int, dict] = {}
    for row in rows:
        proj_raw = row.get("project_id")
        if isinstance(proj_raw, (list, tuple)) and len(proj_raw) == 2:
            proj_id = int(proj_raw[0])
        else:
            proj_id = int(proj_raw) if proj_raw else 0

        if proj_id not in _PROJECT_NAMES:
            raise UnknownProjectError(
                f"read_group returned unexpected project_id={proj_id}. "
                "A new Odoo project has appeared. Add it to _PROJECT_NAMES and re-deploy."
            )

        per_project[proj_id] = {
            "project_id": proj_id,
            "project_name": _PROJECT_NAMES[proj_id],
            "late_uncollected": float(row.get("due_amount") or 0.0),
            "record_count": int(row.get("__count") or 0),
        }

    # Build ordered list with zero-padding for any missing project (Decision 3.4).
    projects = []
    for pid in sorted(_PROJECT_NAMES):
        if pid in per_project:
            projects.append(per_project[pid])
        else:
            logger.info(
                f"Project {pid} ({_PROJECT_NAMES[pid]}) absent from read_group — zero-padding"
            )
            projects.append({
                "project_id": pid,
                "project_name": _PROJECT_NAMES[pid],
                "late_uncollected": 0.0,
                "record_count": 0,
            })

    total_late = sum(p["late_uncollected"] for p in projects)
    total_count = sum(p["record_count"] for p in projects)

    result: dict = {
        "projects": projects,
        "total_late_uncollected": total_late,
        "total_record_count": total_count,
        "currency": "EGP",
        "as_of": datetime.now(timezone.utc).isoformat(),
        "cache_status": "fresh",
        "rpc_duration_ms": rpc_ms,
        "domain": domain,
    }

    _cache.set(cache_key, result)
    return result
