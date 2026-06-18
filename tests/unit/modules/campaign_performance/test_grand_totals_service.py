"""
Unit tests for the window-INDEPENDENT grand-totals aggregator
(get_campaign_grand_totals).

OdooClient is fully mocked via a dispatch keyed on (model, method, domain): the two
read_group-by-stage calls differ ONLY by domain — the all-time call carries the empty
domain [], the migration call carries the legacy-days OR-domain. legacy_days + now_cairo
are INJECTED so the migration slice and reference date are deterministic (no live clock,
no legacy-detection RPC).

Covers:
  - incl funnel = ALL leads grouped by stage, classified via the shared classify_stage
  - migration funnel = legacy-days-only read_group, classified the same way
  - excl[group] = incl[group] − migration[group]; excl_total = incl_total − migration_total
  - the excl >= 0 guard (migration cannot exceed all-time for a group) raises RuntimeError
  - won classification is is_won-driven (dynamic), not name-driven
  - the migration RPC is skipped (no exclusion) when no legacy days are given
  - read-only abort, OdooQueryError, cache hit, override bypasses cache

Live verification: scripts/verify_campaign_grand_totals_live.py.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from backend.core.exceptions import OdooQueryError, ReadOnlyViolationError
from backend.modules.campaign_performance.domain import (
    GROUP_INTERESTED,
    GROUP_NEW,
    GROUP_NO_RESULT,
    GROUP_ORDER,
    GROUP_WON,
)
from backend.modules.campaign_performance.services import cache as _cache
from backend.modules.campaign_performance.services.campaign_service import (
    get_campaign_grand_totals,
)

_CAIRO = ZoneInfo("Africa/Cairo")
_NOW = datetime(2026, 6, 17, 12, 0, 0, tzinfo=_CAIRO)
_LEGACY = {"2025-11-15", "2025-11-16", "2025-11-26"}


@pytest.fixture(autouse=True)
def fresh_cache():
    _cache.clear()
    yield
    _cache.clear()


# ── Dataset ───────────────────────────────────────────────────────────────────
# crm.stage: is_won is the ONLY signal for اشترى (Reservation). New -> جديد,
# Follow up/Interested -> مهتم, Lost -> بلا نتيجة.
_STAGES = [
    {"id": 1, "name": "New", "is_won": False},
    {"id": 2, "name": "Follow up", "is_won": False},
    {"id": 3, "name": "Reservation", "is_won": True},
    {"id": 4, "name": "Lost", "is_won": False},
]

# ALL leads by stage (incl. migration): جديد 100, مهتم 30, اشترى 20, بلا نتيجة 50 = 200
_ALL_BY_STAGE = [
    {"stage_id": [1, "New"], "__count": 100},
    {"stage_id": [2, "Follow up"], "__count": 30},
    {"stage_id": [3, "Reservation"], "__count": 20},
    {"stage_id": [4, "Lost"], "__count": 50},
]

# Legacy migration leads by stage: جديد 40, بلا نتيجة 30 = 70
_MIGRATION_BY_STAGE = [
    {"stage_id": [1, "New"], "__count": 40},
    {"stage_id": [4, "Lost"], "__count": 30},
]


def _make_client(stages=_STAGES, all_by_stage=_ALL_BY_STAGE, migration=_MIGRATION_BY_STAGE):
    def _dispatch(model, method, args=None, kwargs=None):
        args = args or []
        kwargs = kwargs or {}
        if model == "crm.stage" and method == "search_read":
            return stages
        if model == "crm.lead" and method == "search_read":
            # Live legacy-detection scan (only when legacy_days is NOT injected). The
            # tiny set never reaches LEGACY_DAY_MIN, so no legacy day is detected.
            tiny = [{"create_date": "2026-06-10 06:00:00"}]
            return tiny if kwargs.get("offset", 0) == 0 else []
        if model == "crm.lead" and method == "read_group":
            domain = args[0] if args else []
            # all-time call carries [] ; the migration call carries the OR-domain.
            return all_by_stage if not domain else migration
        raise AssertionError(f"unexpected RPC: {model}.{method} args={args}")

    client = MagicMock()
    client.execute_kw = AsyncMock(side_effect=_dispatch)
    client.close = AsyncMock()
    return client


async def _grand(**kw):
    return await get_campaign_grand_totals(
        client=_make_client(),
        legacy_days=set(_LEGACY),
        now_cairo=_NOW,
        **kw,
    )


def _g(line, group):
    return next(o["count"] for o in line["groups"] if o["group"] == group)


# ── incl funnel ────────────────────────────────────────────────────────────────


async def test_incl_total_and_groups():
    r = await _grand()
    assert r["incl"]["total"] == 200
    assert _g(r["incl"], GROUP_NEW) == 100
    assert _g(r["incl"], GROUP_INTERESTED) == 30
    assert _g(r["incl"], GROUP_WON) == 20            # is_won=True (Reservation)
    assert _g(r["incl"], GROUP_NO_RESULT) == 50
    # 4 groups in fixed order, summing to total
    assert [o["group"] for o in r["incl"]["groups"]] == list(GROUP_ORDER)
    assert sum(o["count"] for o in r["incl"]["groups"]) == r["incl"]["total"]


# ── migration + excl subtraction ───────────────────────────────────────────────


async def test_migration_total_and_excl_is_incl_minus_migration():
    r = await _grand()
    assert r["migration_total"] == 70
    assert r["excl"]["total"] == 200 - 70 == 130
    # per-group: excl == incl − migration
    assert _g(r["excl"], GROUP_NEW) == 100 - 40 == 60
    assert _g(r["excl"], GROUP_INTERESTED) == 30 - 0 == 30
    assert _g(r["excl"], GROUP_WON) == 20 - 0 == 20
    assert _g(r["excl"], GROUP_NO_RESULT) == 50 - 30 == 20
    assert [o["group"] for o in r["excl"]["groups"]] == list(GROUP_ORDER)
    assert sum(o["count"] for o in r["excl"]["groups"]) == r["excl"]["total"]


async def test_legacy_days_reported_sorted():
    r = await _grand()
    assert r["legacy_days"] == ["2025-11-15", "2025-11-16", "2025-11-26"]


async def test_reference_date_from_injected_now():
    r = await _grand()
    assert r["reference_date"] == "2026-06-17"


# ── excl >= 0 guard ─────────────────────────────────────────────────────────────


async def test_excl_negative_guard_raises():
    """Migration count for a group exceeding the all-time count is impossible in live
    data; if it ever happens the funnel is inconsistent → RuntimeError, never a
    negative bar."""
    bad_migration = [
        {"stage_id": [1, "New"], "__count": 150},     # 150 > all-time جديد 100
    ]
    client = _make_client(migration=bad_migration)
    with pytest.raises(RuntimeError):
        await get_campaign_grand_totals(
            client=client, legacy_days=set(_LEGACY), now_cairo=_NOW
        )


# ── no legacy days → migration RPC skipped, excl == incl ────────────────────────


async def test_no_legacy_days_means_no_exclusion():
    """With an empty legacy set the migration read_group is skipped entirely and the
    excl funnel equals the incl funnel."""
    client = _make_client()
    r = await get_campaign_grand_totals(
        client=client, legacy_days=set(), now_cairo=_NOW
    )
    assert r["migration_total"] == 0
    assert r["excl"]["total"] == r["incl"]["total"] == 200
    for g in GROUP_ORDER:
        assert _g(r["excl"], g) == _g(r["incl"], g)
    # Only the stage read + the all-time read_group ran (no migration RPC).
    read_group_calls = [
        c for c in client.execute_kw.await_args_list
        if c.args[0] == "crm.lead" and c.args[1] == "read_group"
    ]
    assert len(read_group_calls) == 1


# ── Cache / read-only / error handling ──────────────────────────────────────────


async def test_default_config_caches():
    client = _make_client()
    first = await get_campaign_grand_totals(client=client)
    second = await get_campaign_grand_totals(client=client)
    assert first["cache_status"] == "fresh"
    assert second["cache_status"] == "cached"
    assert second["rpc_duration_ms"] == 0


async def test_override_config_bypasses_cache():
    first = await _grand()
    second = await _grand()
    assert first["cache_status"] == "fresh"
    assert second["cache_status"] == "fresh"


async def test_read_only_violation_aborts_before_rpc():
    with patch(
        "backend.modules.campaign_performance.services.campaign_service.ALLOWED_METHODS",
        frozenset({"search_read", "read_group", "write"}),
    ):
        with pytest.raises(ReadOnlyViolationError):
            await _grand()


async def test_odoo_failure_raises_odoo_query_error():
    client = MagicMock()
    client.execute_kw = AsyncMock(side_effect=Exception("connection refused"))
    client.close = AsyncMock()
    with pytest.raises(OdooQueryError):
        await get_campaign_grand_totals(
            client=client, legacy_days=set(_LEGACY), now_cairo=_NOW
        )
