"""
Unit tests for the window-INDEPENDENT all-time attribution-coverage footer
(get_attribution_grand_coverage) — the buyer-page parallel of the campaign
grand-totals footer.

OdooClient is fully mocked. The aggregator REUSES get_attribution_overview for the
incl side, so the single mock client must serve BOTH the overview's RPCs (campaigns /
both-set / all-by-campaign / stages / attributed-by-(campaign,stage)) AND the two
extra migration aggregations (migration-attributed read_group-by-stage + the total
migration search_count). The read_group calls are dispatched on their groupby so the
overview's calls and the footer's migration call never collide. legacy_days + now_cairo
are INJECTED (deterministic migration slice + reference date, no live clock / no
legacy-detection RPC); the gate is overridden per test (so the production cache is
never poisoned and the overview runs un-cached).

Covers:
  - incl side ties to the overview: attributed total / population / coverage, and the
    incl funnel = Σ the overview's per-buyer outcomes (no regression / no drift)
  - migration-attributed funnel + total from the (attributing campaigns AND legacy
    days) read_group, classified through the shared classify_stage
  - excl[group] = incl[group] − migration-attributed[group]; excl_total = incl_total −
    migration_attributed_total; population_excl = population − migration_total
  - the two coverages (incl / excl)
  - the excl >= 0 guard (migration-attributed cannot exceed all-time attributed for a
    group) raises RuntimeError
  - no legacy days → both migration RPCs skipped, excl == incl
  - read-only abort, OdooQueryError, cache hit, override bypasses cache

Live verification: scripts/verify_marketing_attribution_grand_coverage_live.py.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from backend.core.exceptions import OdooQueryError, ReadOnlyViolationError
from backend.modules.marketing_attribution.domain import (
    BUYER_FIELD,
    CAMPAIGN_FIELD,
    GROUP_INTERESTED,
    GROUP_NEW,
    GROUP_NO_RESULT,
    GROUP_ORDER,
    GROUP_WON,
)
from backend.modules.marketing_attribution.services import cache as _cache
from backend.modules.marketing_attribution.services.attribution_service import (
    get_attribution_grand_coverage,
)

_CAIRO = ZoneInfo("Africa/Cairo")
_NOW = datetime(2026, 6, 17, 12, 0, 0, tzinfo=_CAIRO)
_LEGACY = {"2025-11-15", "2025-11-16"}

# Gate overrides — Confirmed {FB-AY, FB-AM}, Denylist {BV - Daima}. Both confirmed
# campaigns hold 100% concentration → both attribute. DOM is dominant-but-unconfirmed
# and BV - Daima is denylisted → neither attributes (neither feeds the funnel).
_CONFIRMED = frozenset({"FB-AY", "FB-AM"})
_DENYLIST = frozenset({"BV - Daima"})


@pytest.fixture(autouse=True)
def fresh_cache():
    _cache.clear()
    yield
    _cache.clear()


# ── Dataset ───────────────────────────────────────────────────────────────────
_CAMPAIGNS = [
    {"id": 1, "name": "FB-AY"},
    {"id": 2, "name": "DOM"},
    {"id": 3, "name": "BV - Daima"},
    {"id": 4, "name": "FB-AM"},
]

# crm.stage: is_won is the ONLY signal for اشترى (Reservation). New -> جديد,
# Follow up/Interested -> مهتم, Lost -> بلا نتيجة.
_STAGES = [
    {"id": 1, "name": "New", "is_won": False},
    {"id": 2, "name": "Follow up", "is_won": False},
    {"id": 3, "name": "Reservation", "is_won": True},
    {"id": 4, "name": "Lost", "is_won": False},
    {"id": 6, "name": "Interested", "is_won": False},
]

# ALL-TIME both-set (the campaign→buyer MAP + the >=90% gate): FB-AY 100% Ahmed,
# FB-AM 100% Abdallah, DOM 75% Z (unconfirmed), BV - Daima 100% Mahmoud (denylisted).
_BOTH_SET = [
    {"campaign_id": [1, "FB-AY"], "media_buyer_id": [101, "Ahmed Aymen"], "__count": 100},
    {"campaign_id": [4, "FB-AM"], "media_buyer_id": [102, "Abdallah Maher"], "__count": 100},
    {"campaign_id": [2, "DOM"], "media_buyer_id": [201, "Buyer Z"], "__count": 60},
    {"campaign_id": [2, "DOM"], "media_buyer_id": [202, "Other Y"], "__count": 20},
    {"campaign_id": [3, "BV - Daima"], "media_buyer_id": [301, "Mahmoud Mohsen"], "__count": 50},
]

# ALL leads grouped by campaign (population): FB-AY 200, FB-AM 100, DOM 50,
# BV - Daima 40, no-campaign 60 → population 450.
_ALL_BY_CAMPAIGN = [
    {"campaign_id": [1, "FB-AY"], "__count": 200},
    {"campaign_id": [4, "FB-AM"], "__count": 100},
    {"campaign_id": [2, "DOM"], "__count": 50},
    {"campaign_id": [3, "BV - Daima"], "__count": 40},
    {"campaign_id": False, "__count": 60},
]

# Attributed leads by (campaign, stage) — overview RPC 5, restricted to the gated
# {FB-AY, FB-AM}. Incl-attributed total = 200 + 100 = 300.
#   جديد 120+40=160 · مهتم 30 · اشترى 30 · بلا نتيجة 50+30=80   (Σ 300)
_ATTRIB = [
    {"campaign_id": [1, "FB-AY"], "stage_id": [1, "New"], "__count": 120},
    {"campaign_id": [1, "FB-AY"], "stage_id": [3, "Reservation"], "__count": 30},
    {"campaign_id": [1, "FB-AY"], "stage_id": [4, "Lost"], "__count": 50},
    {"campaign_id": [4, "FB-AM"], "stage_id": [1, "New"], "__count": 40},
    {"campaign_id": [4, "FB-AM"], "stage_id": [6, "Interested"], "__count": 30},
    {"campaign_id": [4, "FB-AM"], "stage_id": [4, "Lost"], "__count": 30},
]

# Migration-ATTRIBUTED leads by stage (attributing campaigns AND legacy days):
#   جديد 50 · بلا نتيجة 40   → migration-attributed total 90
_MIGRATION_ATTR = [
    {"stage_id": [1, "New"], "__count": 50},
    {"stage_id": [4, "Lost"], "__count": 40},
]

# Total migration population (ALL leads on the legacy days — search_count).
_MIGRATION_TOTAL = 200


def _make_client(
    campaigns=_CAMPAIGNS,
    stages=_STAGES,
    both_set=_BOTH_SET,
    all_by_campaign=_ALL_BY_CAMPAIGN,
    attrib=_ATTRIB,
    migration_attr=_MIGRATION_ATTR,
    migration_total=_MIGRATION_TOTAL,
):
    def _dispatch(model, method, args=None, kwargs=None):
        args = args or []
        kwargs = kwargs or {}
        if model == "utm.campaign" and method == "search_read":
            return campaigns
        if model == "crm.stage" and method == "search_read":
            return stages
        if model == "crm.lead" and method == "search_read":
            # Live legacy-detection scan (only when legacy_days is NOT injected). The
            # tiny set never reaches LEGACY_DAY_MIN, so no legacy day is detected.
            tiny = [{"create_date": "2026-06-10 06:00:00"}]
            return tiny if kwargs.get("offset", 0) == 0 else []
        if model == "crm.lead" and method == "search_count":
            return migration_total                     # total migration population
        if model == "crm.lead" and method == "read_group":
            groupby = args[2] if len(args) > 2 else []
            if groupby == [CAMPAIGN_FIELD, BUYER_FIELD]:
                return both_set                        # overview: campaign→buyer map
            if groupby == [CAMPAIGN_FIELD]:
                return all_by_campaign                 # overview: population
            if groupby == [CAMPAIGN_FIELD, "stage_id"]:
                return attrib                          # overview: attributed funnel
            if groupby == ["stage_id"]:
                return migration_attr                  # footer: migration-attributed
        raise AssertionError(f"unexpected RPC: {model}.{method} args={args}")

    client = MagicMock()
    client.execute_kw = AsyncMock(side_effect=_dispatch)
    client.close = AsyncMock()
    return client


async def _grand(client=None, **kw):
    return await get_attribution_grand_coverage(
        client=client if client is not None else _make_client(),
        confirmed_campaigns=_CONFIRMED,
        denylist_campaigns=_DENYLIST,
        legacy_days=set(_LEGACY),
        now_cairo=_NOW,
        **kw,
    )


def _g(line, group):
    return next(o["count"] for o in line["groups"] if o["group"] == group)


# ── incl side ties to the overview (no regression) ──────────────────────────────


async def test_incl_total_population_and_coverage():
    r = await _grand()
    assert r["incl"]["attributed_total"] == 300
    assert r["incl"]["population"] == 450
    assert r["incl"]["coverage_pct"] == round(100.0 * 300 / 450, 2)   # 66.67


async def test_incl_funnel_is_sum_of_overview_buyer_outcomes():
    r = await _grand()
    assert _g(r["incl"], GROUP_NEW) == 160
    assert _g(r["incl"], GROUP_INTERESTED) == 30
    assert _g(r["incl"], GROUP_WON) == 30             # is_won=True (Reservation)
    assert _g(r["incl"], GROUP_NO_RESULT) == 80
    assert [o["group"] for o in r["incl"]["groups"]] == list(GROUP_ORDER)
    assert sum(o["count"] for o in r["incl"]["groups"]) == r["incl"]["attributed_total"]


# ── migration-attributed + excl subtraction ─────────────────────────────────────


async def test_migration_attributed_total_and_migration_total():
    r = await _grand()
    assert r["migration_attributed_total"] == 90
    assert r["migration_total"] == 200


async def test_excl_is_incl_minus_migration_attributed_per_group():
    r = await _grand()
    assert r["excl"]["attributed_total"] == 300 - 90 == 210
    assert _g(r["excl"], GROUP_NEW) == 160 - 50 == 110
    assert _g(r["excl"], GROUP_INTERESTED) == 30 - 0 == 30
    assert _g(r["excl"], GROUP_WON) == 30 - 0 == 30
    assert _g(r["excl"], GROUP_NO_RESULT) == 80 - 40 == 40
    assert [o["group"] for o in r["excl"]["groups"]] == list(GROUP_ORDER)
    assert sum(o["count"] for o in r["excl"]["groups"]) == r["excl"]["attributed_total"]


async def test_population_excl_and_coverages():
    r = await _grand()
    # population_excl = population − migration_total
    assert r["excl"]["population"] == 450 - 200 == 250
    # coverage_excl = excl_attributed_total / population_excl  (much higher than incl —
    # the migration drags the incl % down)
    assert r["excl"]["coverage_pct"] == round(100.0 * 210 / 250, 2)   # 84.0
    assert r["excl"]["coverage_pct"] > r["incl"]["coverage_pct"]


async def test_legacy_days_reported_sorted_and_reference_date():
    r = await _grand()
    assert r["legacy_days"] == ["2025-11-15", "2025-11-16"]
    assert r["reference_date"] == "2026-06-17"


# ── excl >= 0 guard ─────────────────────────────────────────────────────────────


async def test_excl_negative_guard_raises():
    """Migration-attributed for a group exceeding the all-time attributed count is
    impossible in live data; if it ever happens the funnel is inconsistent →
    RuntimeError, never a negative bar."""
    bad = [{"stage_id": [1, "New"], "__count": 200}]   # 200 > all-time جديد 160
    with pytest.raises(RuntimeError):
        await _grand(client=_make_client(migration_attr=bad))


# ── no legacy days → both migration RPCs skipped, excl == incl ───────────────────


async def test_no_legacy_days_means_no_exclusion():
    """With an empty legacy set the migration read_group AND the migration search_count
    are skipped entirely, and the excl coverage equals the incl coverage."""
    client = _make_client()
    r = await get_attribution_grand_coverage(
        client=client,
        confirmed_campaigns=_CONFIRMED,
        denylist_campaigns=_DENYLIST,
        legacy_days=set(),
        now_cairo=_NOW,
    )
    assert r["migration_total"] == 0
    assert r["migration_attributed_total"] == 0
    assert r["excl"]["attributed_total"] == r["incl"]["attributed_total"] == 300
    assert r["excl"]["population"] == r["incl"]["population"] == 450
    assert r["excl"]["coverage_pct"] == r["incl"]["coverage_pct"]
    for g in GROUP_ORDER:
        assert _g(r["excl"], g) == _g(r["incl"], g)
    # No migration RPCs ran: no search_count, and only the overview's read_groups
    # (groupby never == ["stage_id"]). execute_kw is called as
    # execute_kw(model, method, args=[...], kwargs={...}) → model/method are
    # positional (c.args), the read_group groupby is c.kwargs["args"][2].
    lead_calls = [c for c in client.execute_kw.await_args_list if c.args[0] == "crm.lead"]
    assert not any(c.args[1] == "search_count" for c in lead_calls)
    assert not any(
        c.args[1] == "read_group" and c.kwargs["args"][2] == ["stage_id"]
        for c in lead_calls
    )


# ── Cache / read-only / error handling ──────────────────────────────────────────


async def test_default_config_caches():
    client = _make_client()
    first = await get_attribution_grand_coverage(client=client)
    second = await get_attribution_grand_coverage(client=client)
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
        "backend.modules.marketing_attribution.services.attribution_service.ALLOWED_METHODS",
        frozenset({"search_read", "read_group", "search_count", "write"}),
    ):
        with pytest.raises(ReadOnlyViolationError):
            await _grand()


async def test_odoo_failure_raises_odoo_query_error():
    client = MagicMock()
    client.execute_kw = AsyncMock(side_effect=Exception("connection refused"))
    client.close = AsyncMock()
    with pytest.raises(OdooQueryError):
        await _grand(client=client)
