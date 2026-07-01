"""
Collections KPI service — business logic for all 6 MVP KPIs.

Data source: rs.installment (42,970 records as of 2026-05-14) via the
shared read-only OdooClient. All methods are async; no method ever calls
create, write, or unlink.

Session 1 scope: get_late_uncollected() (KPI 2 — Late Uncollected).
KPIs 1, 3, 4, 5, 6 are implemented in future sessions.
"""

import calendar
import time
from collections import defaultdict
from datetime import date, datetime, time as dt_time, timezone
from zoneinfo import ZoneInfo
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
_CACHE_KEY_PREFIX_KPI3 = "kpi:pending_check_exposure"
_CACHE_KEY_PREFIX_KPI6 = "kpi:collection_trend_6m"
_CACHE_KEY_PREFIX_KPI4  = "kpi:collection_rate"
_CACHE_KEY_PREFIX_KPI5B = "kpi:collection_rate_by_project"
# v2 literal (Decision 19.1) — distinct from the retired v1 "kpi:expected_forecast"
# so a stale v1 cache entry can never serve the v2 payload.
_CACHE_KEY_PREFIX_KPI7_V2 = "kpi:dues_collections_v2"

# KPI 6 uses the payment installment header model (Decision 5.6).
# The HEADER.date field is user-entered (cash receipt date); HEADER.amount
# is proven identity-equal to SUM(LINE.amount) — D0 Part 2 Finding B.
_PAYMENT_HEADER_MODEL = "rs.account.payment.installment"
_CACHE_TTL_KPI6 = 3600  # 1 hour — trend data is stable within a session

# Arabic month labels (Decision 5.5 — hardcoded, no babel dependency).
_ARABIC_MONTHS: dict[int, str] = {
    1: "يناير",   2: "فبراير",  3: "مارس",    4: "أبريل",
    5: "مايو",    6: "يونيو",   7: "يوليو",   8: "أغسطس",
    9: "سبتمبر", 10: "أكتوبر", 11: "نوفمبر", 12: "ديسمبر",
}

# La Verde operates in Egypt (Africa/Cairo — UTC+2 Nov-Apr, UTC+3 May-Oct per
# tzdata 2025.2; Egypt re-introduced DST in 2023). ZoneInfo handles transitions
# automatically. Decision 5.9.
_LA_VERDE_TZ = ZoneInfo("Africa/Cairo")
_UTC_TZ = ZoneInfo("UTC")

# Phase 2 confirmed project IDs and clean display names (MODULE_2_DISCOVERY_PHASE_2.md §6).
# Odoo returns "Project#New Capital" etc.; we expose clean names to API consumers.
# Stage 2 (Decision 25.2): KPI 5 and KPI 5b now resolve names dynamically via
# get_project_name_map(), so this dict — and the UnknownProjectError import — are
# NO LONGER USED by those KPIs. Kept defined for Stage 4, which removes them.
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


# ── Dynamic project-name resolver (Stage 1 — dormant, not yet wired in) ──────
# Read-only resolver that sources project display names LIVE from the project
# master model rs.structure.project, using the clean `code` field (verbatim
# English names — e.g. "New Capital" — with NO "Project#" prefix that the
# many2one `name` field carries). This is the future replacement for the
# hardcoded _PROJECT_NAMES dict above: in a later stage the project-aware KPIs
# and drill-downs will resolve names through this map instead of the literal.
# It is DEFINED AND TESTED HERE BUT NOT YET WIRED INTO ANY KPI/DRILL-DOWN —
# Stage 1 only adds it; Stage 2 will adopt it. Placed in kpi_service.py because
# drilldown_service.py already imports from this module (one-directional), so a
# single definition here is importable by both services with no circular import.
_PROJECT_MASTER_MODEL = "rs.structure.project"
_CACHE_KEY_PREFIX_PROJECT_MASTER = "project_master"
# The master list changes rarely (3 projects today), so cache it for 1 hour —
# far longer than the 60s KPI default. The date-scoped cache key still caps any
# entry at the Cairo-day boundary (≤24h backstop).
_CACHE_TTL_PROJECT_MASTER = 3600  # 1 hour
# Future-proof guard: only currently-active projects (excludes any archived
# later). Confirmed correct by the 2026-06-30 read-only probe (3 active, 0
# archived).
_PROJECT_MASTER_DOMAIN: list = [("active", "=", True)]
_PROJECT_MASTER_FIELDS = ["id", "code"]


async def get_project_name_map(client: Optional[OdooClient] = None) -> dict[int, str]:
    """Return a live {project_id: display_name} map from rs.structure.project.

    Stage 1 of the dynamic-project-resolution refactor (DORMANT — not yet wired
    into any KPI/drill-down). Sources clean English project names from the
    master model's `code` field rather than the hardcoded _PROJECT_NAMES dict.

    Behaviour:
        * Cache-first: returns the cached map without touching Odoo on a hit.
        * On a miss, issues ONE read-only ``search_read`` on
          ``rs.structure.project`` with domain ``[("active", "=", True)]``,
          fields ``["id", "code"]`` and ``order="id asc"``, then caches the
          result for 1 hour (_CACHE_TTL_PROJECT_MASTER).
        * Strictly read-only — ``search_read`` only; never create/write/unlink.

    Ordering:
        The returned dict is ALWAYS id-ascending. ``order="id asc"`` is sent to
        Odoo, and the rows are additionally sorted client-side before the dict
        is built, so the iteration order is deterministic even if Odoo ignores
        the ``order`` hint.

    Edge cases (handled defensively — never crashes):
        * A row whose ``code`` is falsy (``False``/``None``/``""``) maps to the
          safe placeholder ``f"Project {id}"`` — never a blank string.
        * An empty result set returns an empty dict ``{}`` (does not raise).

    Args:
        client: Optional injectable read-only OdooClient (mirrors the KPI
            convention). When omitted, a client is created and closed here;
            when injected, it is left open for the caller to manage.

    Returns:
        dict[int, str]: ``{project_id: display_name}`` in id-ascending order.

    Raises:
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated with a
            write method (checked before any RPC).
        OdooQueryError: if the Odoo RPC fails for any reason.
    """
    _assert_read_only()

    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_PROJECT_MASTER)

    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"Cache hit: {cache_key}")
        return cached

    logger.info(f"Cache miss: {cache_key} — querying Odoo")

    _client = client if client is not None else OdooClient()

    try:
        rows = await _client.execute_kw(
            _PROJECT_MASTER_MODEL,
            "search_read",
            args=[_PROJECT_MASTER_DOMAIN, _PROJECT_MASTER_FIELDS],
            kwargs={"order": "id asc"},
        )
    except Exception as exc:
        raise OdooQueryError(
            f"search_read on {_PROJECT_MASTER_MODEL} failed: {exc}"
        ) from exc
    finally:
        if client is None:
            await _client.close()

    name_map: dict[int, str] = {}
    for row in sorted(rows or [], key=lambda r: int(r["id"])):
        pid = int(row["id"])
        code = row.get("code")
        name_map[pid] = code if code else f"Project {pid}"

    _cache.set(cache_key, name_map, ttl=_CACHE_TTL_PROJECT_MASTER)
    return name_map


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
    whose due date is before today, and returns SUM(amount) − SUM(actual_paid_amount)
    plus the cheques subset annotation via Alternative B (Decision 10.2).

    Formula: PATH A (Decision 11.13, Session 12) — reverses Decision 10.1 PATH C.
    Pre-implementation discovery confirmed safe on live Odoo (commit 14600f3,
    2026-05-20): H2 identity exact to 0.000000 EGP on the Late subset.

    A single read_group call aggregates all monetary fields simultaneously
    (amount, paid_amount, x_studio_actual_paid_amount, total_due_amount) — 1 RPC
    total, C3 constraint preserved.

    Value formula (PATH A, Decision 11.13):
        value = SUM(amount) − SUM(x_studio_actual_paid_amount)

    Identity cross-check: value ≈ SUM(total_due_amount). Tiered thresholds:
        delta < 1 EGP: no flag; 1–999 EGP: INFO log; ≥ 1000 EGP: WARNING + flag.

    Cheques formula (Alternative B, Decision 10.2 / Decision 9.1 analog):
        cheques_in_pipeline = max(SUM(paid_amount) - SUM(x_studio_actual_paid_amount), 0)
    cheques_record_count is null (Alternative B limitation — per-installment
    count unavailable via read_group net formula).

    data_quality_warning priority: "negative_cheques" > "kpi2_identity_mismatch".

    Return shape::

        {
            "value":                     float,  # EGP SUM(amount) − SUM(actual_paid_amount)
            "currency":                  "EGP",
            "record_count":              int,    # matched installment count
            "cheques_in_pipeline":       float,  # EGP uncashed cheques (Alt B)
            "cheques_record_count":      None,   # Alt B limitation
            "drill_down_domain":         list,   # Candidate C domain (= domain)
            "cheques_drill_down_domain": None,   # Alt B limitation
            "as_of":                     str,    # ISO 8601 UTC datetime of the query
            "cache_status":              str,    # "fresh" or "cached"
            "rpc_duration_ms":           int,    # 0 if served from cache
            "domain":                    list,   # legacy field — same value as drill_down_domain
            "data_quality_warning":      str | None,  # "negative_cheques", "kpi2_identity_mismatch", or null
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
            args=[domain, ["amount", "paid_amount", "x_studio_actual_paid_amount", "total_due_amount"], []],
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
    amount_sum   = float(row.get("amount") or 0.0)
    count        = int(row.get("__count") or 0)
    paid_amount  = float(row.get("paid_amount") or 0.0)
    actual_paid  = float(row.get("x_studio_actual_paid_amount") or 0.0)
    total_due    = float(row.get("total_due_amount") or 0.0)
    value        = amount_sum - actual_paid

    cheques_raw         = paid_amount - actual_paid
    cheques_in_pipeline = max(cheques_raw, 0.0)

    # "negative_cheques" takes priority over "kpi2_identity_mismatch" (Risk 3, §6).
    data_quality_warning: Optional[str] = None
    if cheques_raw < 0:
        logger.warning(
            "KPI 2: negative cheques_raw — paid_amount < x_studio_actual_paid_amount. "
            "Data quality anomaly in Odoo Studio fields."
        )
        data_quality_warning = "negative_cheques"
    else:
        identity_delta = abs(value - total_due)
        if identity_delta < 1.0:
            pass
        elif identity_delta < 1000.0:
            logger.info(
                "KPI 2: PATH A identity micro-drift — value=%.2f, total_due=%.2f, "
                "delta=%.2f EGP (acceptable, < 1,000 EGP threshold).",
                value, total_due, identity_delta,
            )
        else:
            logger.warning(
                "KPI 2: PATH A identity mismatch — value=%.2f, total_due=%.2f, "
                "delta=%.2f EGP (>= 1,000 EGP threshold).",
                value, total_due, identity_delta,
            )
            data_quality_warning = "kpi2_identity_mismatch"

    result: dict = {
        "value":                     value,
        "currency":                  "EGP",
        "record_count":              count,
        "cheques_in_pipeline":       cheques_in_pipeline,
        "cheques_record_count":      None,
        "drill_down_domain":         list(domain),
        "cheques_drill_down_domain": None,
        "as_of":                     datetime.now(timezone.utc).isoformat(),
        "cache_status":              "fresh",
        "rpc_duration_ms":           rpc_ms,
        "domain":                    list(domain),
        "data_quality_warning":      data_quality_warning,
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


async def get_pending_check_exposure(client: Optional[OdooClient] = None) -> dict:
    """Return KPI 3 — Pending Check Exposure.

    Queries rs.installment filtered to state='post' and aggregates both
    SUM(paid_amount) and SUM(x_studio_actual_paid_amount) in a single
    read_group call. The derived value (paid - actual) represents checks
    received from customers not yet cashed by the bank.

    Domain: [('state', '=', 'post')] — excludes draft and cancelled
    installments. Cancelled records carry historical paid_amount from
    pre-cancellation cheque submissions; those pending amounts are not
    part of the active collection pipeline. See Decision 4.1.

    Return shape::

        {
            "value":                float,       # EGP, paid_amount_sum - actual_paid_sum
            "currency":             "EGP",
            "record_count":         int,         # posted installments matched (~42,443)
            "as_of":                str,         # ISO 8601 UTC datetime
            "cache_status":         str,         # "fresh" | "cached"
            "rpc_duration_ms":      int,         # 0 if served from cache
            "domain":               list,        # [('state', '=', 'post')]
            "paid_amount_sum":      float,       # SUM(paid_amount)
            "actual_paid_sum":      float,       # SUM(x_studio_actual_paid_amount)
            "derivation_note":      str,         # "value = paid_amount_sum - actual_paid_sum"
            "data_quality_warning": str | None,  # "value_is_negative" or None (Decision 4.4)
        }

    Raises:
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated with a write method.
        OdooQueryError: if the Odoo RPC fails for any reason (network, auth, RPC error).
    """
    _assert_read_only()

    domain: list = [("state", "=", "post")]
    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_KPI3)

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
            args=[domain, ["paid_amount", "x_studio_actual_paid_amount"], []],
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
    paid_amount_sum = float(row.get("paid_amount") or 0.0)
    actual_paid_sum = float(row.get("x_studio_actual_paid_amount") or 0.0)
    count = int(row.get("__count") or 0)
    value = paid_amount_sum - actual_paid_sum

    data_quality_warning: Optional[str] = None
    if value < 0:
        # Decision 4.4 (Option A): return as-is, log warning, set flag.
        # A negative derived value indicates x_studio_actual_paid_amount exceeds
        # paid_amount — a Studio field computation anomaly (Phase 2 §8 Finding 8b).
        logger.warning(
            "KPI 3 derived value is negative: paid_amount_sum=%s, "
            "actual_paid_sum=%s, value=%s. This indicates a data "
            "quality anomaly in Odoo Studio fields — see Decision 1.4 "
            "and Phase 2 §8 Finding 8b.",
            paid_amount_sum, actual_paid_sum, value,
        )
        data_quality_warning = "value_is_negative"

    result: dict = {
        "value": value,
        "currency": "EGP",
        "record_count": count,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "cache_status": "fresh",
        "rpc_duration_ms": rpc_ms,
        "domain": domain,
        "paid_amount_sum": paid_amount_sum,
        "actual_paid_sum": actual_paid_sum,
        "derivation_note": "value = paid_amount_sum - actual_paid_sum",
        "data_quality_warning": data_quality_warning,
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

    Stage 2 (Decision 25.2): project names are resolved live via
    get_project_name_map(); output covers every active project (dynamic
    zero-padding), and a falsy/unmapped project_id is handled defensively
    (skip / f"Project {id}" fallback) rather than raising UnknownProjectError.
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
        # Stage 2: resolve live project names first, reusing THIS KPI's client
        # (so no second Odoo connection is opened; the finally below still closes
        # a self-created _client). Placed before the read_group so the read_group
        # stays the last execute_kw call.
        name_map = await get_project_name_map(client=_client)
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
    # Stage 2: names come from the live resolver (name_map). Rows with a falsy
    # project_id are SKIPPED defensively (crash-safe — replaces the former
    # UnknownProjectError raise). A 2026-07-01 read-only probe found ZERO such
    # rows in this domain and ZERO portfolio-wide, so this branch is dormant in
    # practice; see Decision 25.2 (supersedes the stale Decision 14.13 figure).
    per_project: dict[int, dict] = {}
    for row in rows:
        proj_raw = row.get("project_id")
        if not proj_raw:
            continue
        if isinstance(proj_raw, (list, tuple)) and len(proj_raw) == 2:
            proj_id = int(proj_raw[0])
        else:
            proj_id = int(proj_raw)

        per_project[proj_id] = {
            "project_id": proj_id,
            "project_name": name_map.get(proj_id, f"Project {proj_id}"),
            "late_uncollected": float(row.get("due_amount") or 0.0),
            "record_count": int(row.get("__count") or 0),
        }

    # Dynamic zero-padding (Decision 3.4, generalised in Stage 2): show EVERY
    # active project from the resolver map, plus any payload id not in the map,
    # ordered id-ascending — for any project count, not a fixed 3.
    projects = []
    for pid in sorted(set(name_map) | set(per_project)):
        if pid in per_project:
            projects.append(per_project[pid])
        else:
            pname = name_map.get(pid, f"Project {pid}")
            logger.info(
                f"Project {pid} ({pname}) absent from read_group — zero-padding"
            )
            projects.append({
                "project_id": pid,
                "project_name": pname,
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


def _tz_period_bounds(period_start: date, period_end: date) -> tuple[str, str]:
    """Convert local-time period boundaries to UTC datetime strings for Odoo domain.

    Egypt observes DST (UTC+2 Nov-Apr, UTC+3 May-Oct per tzdata 2025.2).
    A record displayed as "01/12/2025 00:00:00" Egypt time (winter, UTC+2) is
    stored as "2025-11-30 22:00:00" UTC — a naive >= '2025-12-01' filter would
    exclude it. ZoneInfo handles DST transitions automatically. Decision 5.9.
    """
    start_local = datetime.combine(period_start, dt_time.min, tzinfo=_LA_VERDE_TZ)
    end_local   = datetime.combine(period_end, dt_time(23, 59, 59), tzinfo=_LA_VERDE_TZ)
    start_utc = start_local.astimezone(_UTC_TZ)
    end_utc   = end_local.astimezone(_UTC_TZ)
    return (
        start_utc.strftime("%Y-%m-%d %H:%M:%S"),
        end_utc.strftime("%Y-%m-%d %H:%M:%S"),
    )


async def get_collection_trend_6m(client: Optional[OdooClient] = None) -> dict:
    """Return KPI 6 — 6-Month Collection Trend.

    Queries rs.account.payment.installment (HEADER model) for posted payment
    records whose user-entered date falls within the trailing 6 calendar months,
    grouped by date:month. Returns exactly 6 entries oldest-first, zero-padding
    months with no data (Decision 3.4 zero-padding extended to KPI 6).

    Architecture note (Decision 5.6): the LINE model (rs.account.payment.
    installment.line) cannot be used because Odoo's ORM does not support
    :month granularity groupby on related fields (payment_id.date:month).
    HEADER.amount is identity-equal to SUM(LINE.amount) — D0 Part 2 Finding B.

    Empty months in the current data period are expected (Decision 5.7):
    operations staff are entering historical payments retroactively. Zero bars
    are truthful, not bugs.

    Return shape::

        {
            "months": [
                {
                    "month":        str,    # YYYY-MM, oldest first
                    "label_en":     str,    # e.g. "Dec 2025"
                    "label_ar":     str,    # e.g. "ديسمبر"  (Decision 5.5)
                    "amount":       float,  # EGP collected this month
                    "record_count": int,    # payment header count
                },
                # ... exactly 6 entries
            ],
            "total_6m":           float,   # EGP, sum across all 6 months
            "total_record_count": int,
            "average_monthly":    float,   # total_6m / 6 (includes zero months)
            "period_start":       str,     # YYYY-MM-DD (first day of oldest month)
            "period_end":         str,     # YYYY-MM-DD (today)
            "currency":           "EGP",
            "as_of":              str,     # ISO 8601 UTC datetime
            "cache_status":       str,     # "fresh" | "cached"
            "cache_ttl_seconds":  int,     # 3600
            "rpc_duration_ms":    int,     # 0 if cached
            "domain":             list,    # exact domain used
        }

    Raises:
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated with a write method.
        OdooQueryError: if the Odoo RPC fails for any reason (network, auth, RPC error).
    """
    _assert_read_only()

    today = date.fromisoformat(_cache.today_str())

    # Trailing 6 calendar months (stdlib only — Decision 5.4, no python-dateutil).
    # Example: today = 2026-05-17 → start_month = 0 → wraps → period_start = 2025-12-01
    start_month = today.month - 5
    start_year = today.year
    if start_month <= 0:
        start_month += 12
        start_year -= 1
    period_start = date(start_year, start_month, 1)
    period_end = today

    start_utc_str, end_utc_str = _tz_period_bounds(period_start, period_end)
    domain: list = [
        ("state", "=", "post"),
        ("date", ">=", start_utc_str),   # Decision 5.9: UTC boundary for Egypt UTC+2
        ("date", "<=", end_utc_str),
    ]

    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_KPI6)

    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"Cache hit: {cache_key}")
        return {**cached, "cache_status": "cached", "rpc_duration_ms": 0}

    logger.info(f"Cache miss: {cache_key} — querying Odoo")

    _client = client if client is not None else OdooClient()

    # Note: Odoo's read_group groups by raw stored UTC value, which places
    # Egypt-local-midnight records in the previous UTC month. We use search_read
    # + Python regrouping in Egypt local time to produce identity-equal results
    # with the Odoo UI. See Decision 5.10.
    t0 = time.monotonic()
    try:
        records = await _client.execute_kw(
            _PAYMENT_HEADER_MODEL,
            "search_read",
            args=[domain, ["date", "amount"]],
            kwargs={},
        )
    except Exception as exc:
        raise OdooQueryError(
            f"search_read on {_PAYMENT_HEADER_MODEL} failed: {exc}"
        ) from exc
    finally:
        if client is None:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        f"Odoo search_read on {_PAYMENT_HEADER_MODEL} returned {len(records)} records "
        f"in {rpc_ms}ms | cache_key={cache_key}"
    )

    if rpc_ms > 5000:
        logger.warning(
            "KPI 6 search_read on %s took %dms — exceeds 5s performance threshold (Decision 5.4)",
            _PAYMENT_HEADER_MODEL, rpc_ms,
        )

    # Group records by Egypt local month. Odoo returns date as "YYYY-MM-DD HH:MM:SS" UTC.
    by_month: defaultdict = defaultdict(lambda: {"amount": 0.0, "count": 0})
    for rec in records:
        raw_date = rec.get("date")
        if not raw_date:
            logger.warning("KPI 6: record missing date — skipping: id=%s", rec.get("id"))
            continue
        try:
            utc_dt = datetime.strptime(str(raw_date), "%Y-%m-%d %H:%M:%S").replace(tzinfo=_UTC_TZ)
        except ValueError:
            logger.warning("KPI 6: unparseable date %r — skipping", raw_date)
            continue
        local_dt = utc_dt.astimezone(_LA_VERDE_TZ)
        ym = local_dt.strftime("%Y-%m")
        by_month[ym]["amount"] += float(rec.get("amount") or 0.0)
        by_month[ym]["count"]  += 1

    # Build ordered 6-entry list, zero-padding months absent from Odoo response.
    month_entries: list[dict] = []
    y, m = period_start.year, period_start.month
    while (y, m) <= (period_end.year, period_end.month):
        ym   = f"{y:04d}-{m:02d}"
        data = by_month.get(ym, {"amount": 0.0, "count": 0})
        month_entries.append({
            "month":        ym,
            "label_en":     f"{calendar.month_abbr[m]} {y}",
            "label_ar":     _ARABIC_MONTHS[m],
            "amount":       data["amount"],
            "record_count": data["count"],
        })
        m += 1
        if m > 12:
            m = 1
            y += 1

    total_6m        = sum(e["amount"]       for e in month_entries)
    total_count     = sum(e["record_count"] for e in month_entries)
    average_monthly = total_6m / 6  # always 6 entries

    result: dict = {
        "months":             month_entries,
        "total_6m":           total_6m,
        "total_record_count": total_count,
        "average_monthly":    average_monthly,
        "period_start":       period_start.isoformat(),
        "period_end":         period_end.isoformat(),
        "currency":           "EGP",
        "as_of":              datetime.now(timezone.utc).isoformat(),
        "cache_status":       "fresh",
        "cache_ttl_seconds":  _CACHE_TTL_KPI6,
        "rpc_duration_ms":    rpc_ms,
        "domain":             domain,
    }

    _cache.set(cache_key, result, ttl=_CACHE_TTL_KPI6)
    return result


async def get_collection_rate_mtd_ytd(client: Optional[OdooClient] = None) -> dict:
    """Return KPI 4 — Collection Rate MTD & YTD.

    Four sequential read_group RPCs (2 per period):
      Q1 — MTD numerator  : rs.account.payment.installment, state='post', UTC datetime bounds
      Q2 — MTD denominator: rs.installment, state='post', ISO date bounds
      Q3 — YTD numerator  : rs.account.payment.installment, state='post', UTC datetime bounds
      Q4 — YTD denominator: rs.installment, state='post', ISO date bounds

    Formula (Decision 6.1):
      rate_percent = SUM(HEADER.amount) / SUM(rs.installment.amount) * 100

    Denominator uses rs.installment.amount (contractual face value), NOT due_amount
    (remaining balance). Using due_amount would make the ratio self-referential and
    time-unstable: the numerator's own success would shrink the denominator, making the
    rate artificially high as more payments are received.

    Zero denominator → rate_percent: None (Decision 6.3). Frontend renders "—".
    YTD period: calendar year Jan 1 to today (Decision 6.2, pending Finance confirmation).

    Return shape::

        {
            "mtd": {
                "numerator_egp":    float,        # SUM(HEADER.amount) in MTD period
                "denominator_egp":  float,        # SUM(rs.installment.amount) in MTD period
                "rate_percent":     float | None, # None if denominator == 0 (Decision 6.3)
                "period_start":     str,          # YYYY-MM-DD (first day of month)
                "period_end":       str,          # YYYY-MM-DD (today)
                "record_count_num": int,          # HEADER records in period
                "record_count_den": int,          # rs.installment records in period
            },
            "ytd": {
                "numerator_egp":    float,
                "denominator_egp":  float,
                "rate_percent":     float | None,
                "period_start":     str,          # YYYY-01-01 (Jan 1, calendar year)
                "period_end":       str,          # YYYY-MM-DD (today)
                "record_count_num": int,
                "record_count_den": int,
            },
            "ytd_period_assumption": str,   # "calendar_year" (Decision 6.2)
            "currency":             "EGP",
            "as_of":                str,    # ISO 8601 UTC datetime
            "cache_status":         str,    # "fresh" | "cached"
            "rpc_duration_ms":      int,    # total across 4 RPCs, 0 if cached
        }

    Raises:
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated with a write method.
        OdooQueryError: if any of the 4 Odoo RPCs fails.
    """
    _assert_read_only()

    today = date.fromisoformat(_cache.today_str())

    # MTD: first day of current month → today
    mtd_start = date(today.year, today.month, 1)
    mtd_end = today

    # YTD: Jan 1 of current calendar year → today (Decision 6.2)
    ytd_start = date(today.year, 1, 1)
    ytd_end = today

    # Numerator: HEADER.date is datetime in UTC → convert Egypt-local bounds to UTC (Decision 5.9)
    mtd_start_utc, mtd_end_utc = _tz_period_bounds(mtd_start, mtd_end)
    ytd_start_utc, ytd_end_utc = _tz_period_bounds(ytd_start, ytd_end)

    # Denominator: rs.installment.date is a plain date field — ISO strings only, no conversion
    mtd_start_iso = mtd_start.isoformat()
    mtd_end_iso   = mtd_end.isoformat()
    ytd_start_iso = ytd_start.isoformat()
    ytd_end_iso   = ytd_end.isoformat()

    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_KPI4)

    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"Cache hit: {cache_key}")
        return {**cached, "cache_status": "cached", "rpc_duration_ms": 0}

    logger.info(f"Cache miss: {cache_key} — querying Odoo (4 RPCs)")

    _client = client if client is not None else OdooClient()

    t0 = time.monotonic()
    try:
        # Q1 — MTD numerator (HEADER, UTC datetime bounds)
        mtd_num_domain: list = [
            ("state", "=", "post"),
            ("date", ">=", mtd_start_utc),
            ("date", "<=", mtd_end_utc),
        ]
        mtd_num_rows = await _client.execute_kw(
            _PAYMENT_HEADER_MODEL,
            "read_group",
            args=[mtd_num_domain, ["amount"], []],
            kwargs={"lazy": False},
        )

        # Q2 — MTD denominator (rs.installment, plain date bounds)
        mtd_den_domain: list = [
            ("state", "=", "post"),
            ("date", ">=", mtd_start_iso),
            ("date", "<=", mtd_end_iso),
        ]
        mtd_den_rows = await _client.execute_kw(
            _MODEL,
            "read_group",
            args=[mtd_den_domain, ["amount"], []],
            kwargs={"lazy": False},
        )

        # Q3 — YTD numerator (HEADER, UTC datetime bounds)
        ytd_num_domain: list = [
            ("state", "=", "post"),
            ("date", ">=", ytd_start_utc),
            ("date", "<=", ytd_end_utc),
        ]
        ytd_num_rows = await _client.execute_kw(
            _PAYMENT_HEADER_MODEL,
            "read_group",
            args=[ytd_num_domain, ["amount"], []],
            kwargs={"lazy": False},
        )

        # Q4 — YTD denominator (rs.installment, plain date bounds)
        ytd_den_domain: list = [
            ("state", "=", "post"),
            ("date", ">=", ytd_start_iso),
            ("date", "<=", ytd_end_iso),
        ]
        ytd_den_rows = await _client.execute_kw(
            _MODEL,
            "read_group",
            args=[ytd_den_domain, ["amount"], []],
            kwargs={"lazy": False},
        )

    except Exception as exc:
        raise OdooQueryError(
            f"KPI 4 read_group failed: {exc}"
        ) from exc
    finally:
        if client is None:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        f"KPI 4: 4 read_group RPCs completed in {rpc_ms}ms | cache_key={cache_key}"
    )
    if rpc_ms > 5000:
        logger.warning(
            "KPI 4: total RPC time %dms exceeds 5s performance threshold", rpc_ms
        )

    def _extract(rows: list) -> tuple[float, int]:
        row = rows[0] if rows else {}
        return float(row.get("amount") or 0.0), int(row.get("__count") or 0)

    def _rate(num: float, den: float) -> Optional[float]:
        if den == 0.0:
            return None  # Decision 6.3: zero denominator → None, frontend renders "—"
        return num / den * 100

    mtd_num, mtd_num_count = _extract(mtd_num_rows)
    mtd_den, mtd_den_count = _extract(mtd_den_rows)
    ytd_num, ytd_num_count = _extract(ytd_num_rows)
    ytd_den, ytd_den_count = _extract(ytd_den_rows)

    result: dict = {
        "mtd": {
            "numerator_egp":    mtd_num,
            "denominator_egp":  mtd_den,
            "rate_percent":     _rate(mtd_num, mtd_den),
            "period_start":     mtd_start.isoformat(),
            "period_end":       mtd_end.isoformat(),
            "record_count_num": mtd_num_count,
            "record_count_den": mtd_den_count,
        },
        "ytd": {
            "numerator_egp":    ytd_num,
            "denominator_egp":  ytd_den,
            "rate_percent":     _rate(ytd_num, ytd_den),
            "period_start":     ytd_start.isoformat(),
            "period_end":       ytd_end.isoformat(),
            "record_count_num": ytd_num_count,
            "record_count_den": ytd_den_count,
        },
        "ytd_period_assumption": "calendar_year",
        "currency":              "EGP",
        "as_of":                 datetime.now(timezone.utc).isoformat(),
        "cache_status":          "fresh",
        "rpc_duration_ms":       rpc_ms,
    }

    _cache.set(cache_key, result)
    return result


async def get_collection_rate_by_project(client: Optional[OdooClient] = None) -> dict:
    """Return KPI 5b — Collection Rate per Project (MTD & YTD).

    Branch A architecture (Decision 7.1): project_id is a direct field on
    rs.account.payment.installment. Four read_group RPCs grouped by project_id:
      Q1 — MTD numerator  : HEADER, state='post', UTC datetime bounds, groupby project_id
      Q2 — MTD denominator: rs.installment, state='post', ISO date bounds, groupby project_id
      Q3 — YTD numerator  : HEADER, state='post', UTC datetime bounds, groupby project_id
      Q4 — YTD denominator: rs.installment, state='post', ISO date bounds, groupby project_id

    Always returns all 3 projects, zero-padding missing ones (Decision 3.4 extended).
    Zero denominator per project → rate_percent: None (Decision 6.3).
    YTD: calendar year Jan 1 → today (Decision 6.2).

    Return shape::

        {
            "mtd": {
                "projects": [
                    {
                        "project_id":        int,
                        "project_name":      str,
                        "numerator_egp":     float,
                        "denominator_egp":   float,
                        "rate_percent":      float | None,
                        "record_count_num":  int,
                        "record_count_den":  int,
                    },
                    # 3 entries, ordered by project_id ascending: 1, 2, 3
                ],
                "total_numerator_egp":   float,
                "total_denominator_egp": float,
                "total_rate_percent":    float | None,
                "period_start":          str,   # YYYY-MM-DD
                "period_end":            str,   # YYYY-MM-DD
            },
            "ytd": { ... same shape ... },
            "ytd_period_assumption": "calendar_year",
            "currency":              "EGP",
            "as_of":                 str,    # ISO 8601 UTC datetime
            "cache_status":          str,    # "fresh" | "cached"
            "rpc_duration_ms":       int,    # total across 4 RPCs, 0 if cached
        }

    Raises:
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated.
        OdooQueryError: if any of the 4 Odoo RPCs fails.

    Stage 2 (Decision 25.2): project names are resolved live via
    get_project_name_map(); output covers every active project (dynamic
    zero-padding), and a falsy/unmapped project_id is handled defensively
    rather than raising UnknownProjectError.
    """
    _assert_read_only()

    today = date.fromisoformat(_cache.today_str())

    mtd_start = date(today.year, today.month, 1)
    mtd_end   = today
    ytd_start = date(today.year, 1, 1)
    ytd_end   = today

    mtd_start_utc, mtd_end_utc = _tz_period_bounds(mtd_start, mtd_end)
    ytd_start_utc, ytd_end_utc = _tz_period_bounds(ytd_start, ytd_end)
    mtd_start_iso = mtd_start.isoformat()
    mtd_end_iso   = mtd_end.isoformat()
    ytd_start_iso = ytd_start.isoformat()
    ytd_end_iso   = ytd_end.isoformat()

    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_KPI5B)

    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"Cache hit: {cache_key}")
        return {**cached, "cache_status": "cached", "rpc_duration_ms": 0}

    logger.info(f"Cache miss: {cache_key} — querying Odoo (4 RPCs)")

    _client = client if client is not None else OdooClient()

    t0 = time.monotonic()
    try:
        # Stage 2: resolve live project names first, reusing THIS KPI's client
        # (no second Odoo connection; the finally below still closes a
        # self-created _client). Placed before the read_groups so a read_group
        # stays the last execute_kw call.
        name_map = await get_project_name_map(client=_client)

        # Q1 — MTD numerator per project (HEADER, UTC datetime bounds)
        q1_domain: list = [
            ("state", "=", "post"),
            ("date",  ">=", mtd_start_utc),
            ("date",  "<=", mtd_end_utc),
        ]
        q1_rows = await _client.execute_kw(
            _PAYMENT_HEADER_MODEL, "read_group",
            args=[q1_domain, ["amount"], ["project_id"]],
            kwargs={"lazy": False},
        )

        # Q2 — MTD denominator per project (rs.installment, ISO date bounds)
        q2_domain: list = [
            ("state", "=", "post"),
            ("date",  ">=", mtd_start_iso),
            ("date",  "<=", mtd_end_iso),
        ]
        q2_rows = await _client.execute_kw(
            _MODEL, "read_group",
            args=[q2_domain, ["amount"], ["project_id"]],
            kwargs={"lazy": False},
        )

        # Q3 — YTD numerator per project (HEADER, UTC datetime bounds)
        q3_domain: list = [
            ("state", "=", "post"),
            ("date",  ">=", ytd_start_utc),
            ("date",  "<=", ytd_end_utc),
        ]
        q3_rows = await _client.execute_kw(
            _PAYMENT_HEADER_MODEL, "read_group",
            args=[q3_domain, ["amount"], ["project_id"]],
            kwargs={"lazy": False},
        )

        # Q4 — YTD denominator per project (rs.installment, ISO date bounds)
        q4_domain: list = [
            ("state", "=", "post"),
            ("date",  ">=", ytd_start_iso),
            ("date",  "<=", ytd_end_iso),
        ]
        q4_rows = await _client.execute_kw(
            _MODEL, "read_group",
            args=[q4_domain, ["amount"], ["project_id"]],
            kwargs={"lazy": False},
        )

    except Exception as exc:
        raise OdooQueryError(
            f"KPI 5b read_group failed: {exc}"
        ) from exc
    finally:
        if client is None:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        f"KPI 5b: 4 read_group RPCs completed in {rpc_ms}ms | cache_key={cache_key}"
    )
    if rpc_ms > 5000:
        logger.warning(
            "KPI 5b: total RPC time %dms exceeds 5s performance threshold", rpc_ms
        )

    def _parse_rows(rows: list) -> dict[int, tuple[float, int]]:
        """Parse groupby-project_id rows → {project_id: (amount, count)}.

        Skips null project_id rows (project_id=False) defensively. Stage 2
        removed the former UnknownProjectError raise; names are resolved later
        in _build_period from name_map with an f"Project {id}" fallback.
        """
        out: dict[int, tuple[float, int]] = {}
        for row in rows:
            proj_raw = row.get("project_id")
            if not proj_raw or proj_raw is False:
                continue
            if isinstance(proj_raw, (list, tuple)) and len(proj_raw) == 2:
                proj_id = int(proj_raw[0])
            else:
                proj_id = int(proj_raw)
            out[proj_id] = (
                float(row.get("amount") or 0.0),
                int(row.get("__count") or 0),
            )
        return out

    def _build_period(
        num_map: dict[int, tuple[float, int]],
        den_map: dict[int, tuple[float, int]],
        period_start: date,
        period_end: date,
    ) -> dict:
        # Dynamic zero-padding (Stage 2): every active project from name_map,
        # plus any payload id not in the map, id-ascending — for any count.
        projects = []
        for pid in sorted(set(name_map) | set(num_map) | set(den_map)):
            pname = name_map.get(pid, f"Project {pid}")
            num_amt, num_ct = num_map.get(pid, (0.0, 0))
            den_amt, den_ct = den_map.get(pid, (0.0, 0))
            if pid not in num_map:
                logger.info(
                    "KPI 5b: project %d (%s) absent from numerator read_group — zero-padded",
                    pid, pname,
                )
            if pid not in den_map:
                logger.info(
                    "KPI 5b: project %d (%s) absent from denominator read_group — zero-padded",
                    pid, pname,
                )
            rate_val: Optional[float] = (
                None if den_amt == 0.0 else num_amt / den_amt * 100
            )
            projects.append({
                "project_id":       pid,
                "project_name":     pname,
                "numerator_egp":    num_amt,
                "denominator_egp":  den_amt,
                "rate_percent":     rate_val,
                "record_count_num": num_ct,
                "record_count_den": den_ct,
            })

        total_num = sum(p["numerator_egp"]   for p in projects)
        total_den = sum(p["denominator_egp"] for p in projects)

        return {
            "projects":              projects,
            "total_numerator_egp":   total_num,
            "total_denominator_egp": total_den,
            "total_rate_percent":    None if total_den == 0.0 else total_num / total_den * 100,
            "period_start":          period_start.isoformat(),
            "period_end":            period_end.isoformat(),
        }

    mtd_num_map = _parse_rows(q1_rows)
    mtd_den_map = _parse_rows(q2_rows)
    ytd_num_map = _parse_rows(q3_rows)
    ytd_den_map = _parse_rows(q4_rows)

    result: dict = {
        "mtd":                   _build_period(mtd_num_map, mtd_den_map, mtd_start, mtd_end),
        "ytd":                   _build_period(ytd_num_map, ytd_den_map, ytd_start, ytd_end),
        "ytd_period_assumption": "calendar_year",
        "currency":              "EGP",
        "as_of":                 datetime.now(timezone.utc).isoformat(),
        "cache_status":          "fresh",
        "rpc_duration_ms":       rpc_ms,
    }

    _cache.set(cache_key, result)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# KPI 7 — Dues & Collections — Current Periods (v2, Decision 19.1)
# Session 19 (N3) — full-period three-segment buckets.
# v1 (forward-looking [today, period_end] unpaid/partial — Decisions 9.1-9.4,
# 11.9, 14.6, 16.2-16.7) is superseded; see MODULE_2_REFACTOR_SPEC.md §4 banner.
# ══════════════════════════════════════════════════════════════════════════════

# Canonical bucket order — must not change without updating _BUCKET_NAMES everywhere.
_BUCKET_NAMES: tuple[str, ...] = (
    "this_month", "this_quarter", "this_half", "this_year"
)


def _compute_period_bounds(today: date) -> dict[str, tuple[date, date]]:
    """Return (period_start, period_end) for each of the 4 KPI 7 calendar buckets.

    Ported from scripts/discover_kpi7_v2_full_period.py (N3 discovery,
    commit bc0d2cd — validated live). Decision 19.1.

    this_month   : 1st of current month   → last day of current month
    this_quarter : 1st of current quarter → last day of current quarter
    this_half    : Jan 1 → Jun 30 (H1) or Jul 1 → Dec 31 (H2)
    this_year    : Jan 1 → Dec 31

    All arithmetic is pure calendar math on plain date objects. ZoneInfo is
    NOT used here — it is used upstream when computing today_cairo; the caller
    is responsible for passing a Cairo-local date (Decision 9.2). ISO strings
    go straight into domains with no UTC conversion — rs.installment.date is
    a plain 'date' field (D0.3).
    """
    _, last_day = calendar.monthrange(today.year, today.month)
    month = (date(today.year, today.month, 1),
             date(today.year, today.month, last_day))

    quarter_idx   = (today.month - 1) // 3        # 0=Q1, 1=Q2, 2=Q3, 3=Q4
    q_start_month = quarter_idx * 3 + 1           # 1, 4, 7, 10
    q_end_month   = (quarter_idx + 1) * 3         # 3, 6, 9, 12
    _, q_last_day = calendar.monthrange(today.year, q_end_month)
    quarter = (date(today.year, q_start_month, 1),
               date(today.year, q_end_month, q_last_day))

    if today.month <= 6:
        half = (date(today.year, 1, 1), date(today.year, 6, 30))
    else:
        half = (date(today.year, 7, 1), date(today.year, 12, 31))

    year = (date(today.year, 1, 1), date(today.year, 12, 31))

    return {
        "this_month":   month,
        "this_quarter": quarter,
        "this_half":    half,
        "this_year":    year,
    }


async def _fetch_bucket(
    client: OdooClient,
    start_str: str,
    end_str: str,
) -> tuple[float, float, float, float, int]:
    """Fetch one KPI 7 v2 bucket via a single read_group RPC (Decision 19.1).

    Full-period domain — NO payment_state filter:
        state='post', date >= period_start, date <= period_end
    No UTC conversion — rs.installment.date is a plain date field (D0.3).

    Returns (amount, paid, actual_paid, due, count):
        amount      = SUM(amount)                       — period total dues
        paid        = SUM(paid_amount)                  — cash + cleared + postdated received
        actual_paid = SUM(x_studio_actual_paid_amount)  — cash + cleared only
        due         = SUM(due_amount)                   — remaining (amount − paid per record)
    """
    domain = [
        ("state", "=", "post"),
        ("date", ">=", start_str),
        ("date", "<=", end_str),
    ]
    rows = await client.execute_kw(
        _MODEL,
        "read_group",
        args=[domain, ["amount", "paid_amount", "x_studio_actual_paid_amount", "due_amount"], []],
        kwargs={"lazy": False},
    )
    row = rows[0] if rows else {}
    return (
        float(row.get("amount") or 0.0),
        float(row.get("paid_amount") or 0.0),
        float(row.get("x_studio_actual_paid_amount") or 0.0),
        float(row.get("due_amount") or 0.0),
        int(row.get("__count") or 0),
    )


async def get_expected_collections_forecast(
    odoo_client: Optional[OdooClient] = None,
) -> dict:
    """Return KPI 7 v2 — Dues & Collections — Current Periods (Decision 19.1).

    Four FULL-PERIOD calendar buckets (this_month ⊆ this_quarter ⊆ this_half
    ⊆ this_year), each aggregating ALL posted installments dated inside the
    calendar period [period_start, period_end] — no payment_state filter.
    The v1 forward-looking [today, period_end] unpaid/partial number is
    removed entirely (that story belongs to KPI 2).

    Bucket boundaries are computed in Africa/Cairo timezone for today's
    date; all domain values use plain ISO date strings since
    rs.installment.date is type 'date', not 'datetime' (D0.3, Decision 9.2).

    Three-segment breakdown per bucket (locked Module 2 field semantics):
        period_total_egp      = SUM(amount)
        collected_cleared_egp = SUM(x_studio_actual_paid_amount)
        cheques_pending_egp   = SUM(paid_amount) − SUM(x_studio_actual_paid_amount)
        remaining_egp         = SUM(due_amount)
    Invariant: collected_cleared + cheques_pending + remaining == period_total
    (per-record identity due = amount − paid).

    Guards (Decision 18.2 warn-not-raise pattern — never HTTP 500):
        cheques_pending_egp < 0 in any bucket          → "negative_cheques"
        |period_total − (cleared+pending+remaining)| ≥ 1.0 EGP in any bucket
                                                       → "kpi7_identity_mismatch"
    Priority: "negative_cheques" > "kpi7_identity_mismatch". Values are
    reported unclamped so the invariant stays auditable.

    Cache key uses the Cairo-local date so the cache invalidates at Cairo
    midnight (Decision 9.3). The v2 literal "kpi:dues_collections_v2"
    guarantees a stale v1 "kpi:expected_forecast" entry can never serve.

    4 RPCs per uncached call (one read_group per bucket), 60-second TTL.

    Return shape::

        {
            "buckets": {
                "this_month":   { bucket fields ... },
                "this_quarter": { bucket fields ... },
                "this_half":    { bucket fields ... },
                "this_year":    { bucket fields ... },
            },
            "currency":              "EGP",
            "today_cairo":           "YYYY-MM-DD",   # Cairo-local date string
            "cache_status":          "fresh" | "cached",
            "rpc_duration_ms":       int,             # 0 if cached
            "data_quality_warning":  str | None,
        }

    Each bucket shape::

        {
            "period_start":          str,    # period start ISO (1st of month/quarter/half/year)
            "period_end":            str,    # period end ISO (last day of month/quarter/half/year)
            "record_count":          int,
            "period_total_egp":      float,  # SUM(amount)
            "collected_cleared_egp": float,  # SUM(x_studio_actual_paid_amount)
            "cheques_pending_egp":   float,  # SUM(paid) − SUM(actual_paid), unclamped
            "remaining_egp":         float,  # SUM(due_amount)
        }

    Raises:
        ReadOnlyViolationError: if ALLOWED_METHODS has been contaminated.
        OdooQueryError: if any of the 4 Odoo RPCs fails.
    """
    _assert_read_only()

    # Compute today in Cairo local time (Decision 9.3).
    # ZoneInfo handles DST automatically (UTC+2 Nov-Apr, UTC+3 May-Oct).
    today_cairo = datetime.now(_LA_VERDE_TZ).date()
    today_str   = today_cairo.isoformat()

    bounds = _compute_period_bounds(today_cairo)

    # Cairo-local date in the key so cache invalidates at Cairo midnight (Decision 9.3).
    cache_key = f"{_CACHE_KEY_PREFIX_KPI7_V2}:{today_str}"

    cached = _cache.get(cache_key)
    if cached is not None:
        logger.debug(f"Cache hit: {cache_key}")
        return {**cached, "cache_status": "cached", "rpc_duration_ms": 0}

    logger.info(f"Cache miss: {cache_key} — querying Odoo (4 RPCs)")

    _client = odoo_client if odoo_client is not None else OdooClient()

    t0 = time.monotonic()
    try:
        raw: dict[str, tuple[float, float, float, float, int]] = {}
        for bname in _BUCKET_NAMES:
            start, end = bounds[bname]
            raw[bname] = await _fetch_bucket(
                _client, start.isoformat(), end.isoformat()
            )
    except Exception as exc:
        raise OdooQueryError(f"KPI 7 read_group failed: {exc}") from exc
    finally:
        if odoo_client is None:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)
    logger.info(f"KPI 7 v2: 4 RPCs completed in {rpc_ms}ms | cache_key={cache_key}")

    buckets: dict = {}
    has_negative_cheques  = False
    has_identity_mismatch = False
    for bname in _BUCKET_NAMES:
        amount, paid, actual_paid, due, count = raw[bname]
        start, end = bounds[bname]
        cheques_pending = paid - actual_paid
        identity_delta  = amount - (actual_paid + cheques_pending + due)

        if cheques_pending < 0:
            has_negative_cheques = True
            logger.warning(
                f"KPI 7 v2: negative cheques_pending in {bname} "
                f"({cheques_pending:,.2f} EGP) — paid_amount < x_studio_actual_paid_amount "
                "is a data quality anomaly in Odoo Studio fields. Reported unclamped."
            )
        if abs(identity_delta) >= 1.0:
            has_identity_mismatch = True
            logger.warning(
                f"KPI 7 v2: identity mismatch in {bname} — "
                f"period_total {amount:,.2f} vs cleared+pending+remaining "
                f"{actual_paid + cheques_pending + due:,.2f} "
                f"(delta {identity_delta:,.2f} EGP). "
                "Reported as-is (Decision 18.2 pattern — warn, never 500)."
            )

        buckets[bname] = {
            "period_start":          start.isoformat(),
            "period_end":            end.isoformat(),
            "record_count":          count,
            "period_total_egp":      amount,
            "collected_cleared_egp": actual_paid,
            "cheques_pending_egp":   cheques_pending,
            "remaining_egp":         due,
        }

    # Priority mirrors KPI 2 (Decision 12.3 analog): a structural field anomaly
    # (negative cheques) outranks an identity drift.
    data_quality_warning: Optional[str] = None
    if has_negative_cheques:
        data_quality_warning = "negative_cheques"
    elif has_identity_mismatch:
        data_quality_warning = "kpi7_identity_mismatch"

    result: dict = {
        "buckets":              buckets,
        "currency":             "EGP",
        "today_cairo":          today_str,
        "cache_status":         "fresh",
        "rpc_duration_ms":      rpc_ms,
        "data_quality_warning": data_quality_warning,
    }

    _cache.set(cache_key, result)
    return result
