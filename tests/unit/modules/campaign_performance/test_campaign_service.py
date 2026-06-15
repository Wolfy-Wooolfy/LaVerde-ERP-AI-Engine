"""
Unit tests for the Campaign Performance service.

OdooClient is fully mocked via a dispatch function keyed on (model, method,
groupby) — order-independent. No live Odoo connection is made. The gate
(confirmed/denylist) is overridden per test so assertions never depend on the
production config.

Covers:
  - per-campaign funnel + counts + % + sort-by-lead-volume desc + reconciliation
  - the amended media-buyer DISPLAY rule (§7.1): confirmed / dominant / mixed /
    no_buyer / excluded_channel, including the >=90% gate, the 50% floor, and the
    minimum both-set sample guard
  - confirmed-campaign drift -> integrity alert + downgrade
  - junk "None" campaign + no-campaign bucket surfaced as data-quality flags
  - long-tail aggregation at the volume threshold
  - reconciliation raise, read-only abort, OdooQueryError, cache hit

Live verification: scripts/verify_campaign_performance_live.py.
"""

from unittest.mock import AsyncMock, MagicMock, patch

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
    get_campaign_performance_overview,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def fresh_cache():
    _cache.clear()
    yield
    _cache.clear()


def _make_client(campaigns, all_by_campaign, by_campaign_stage, stages, both_set):
    """Dispatch mock keyed on (model, method, groupby) — order-independent."""

    def _dispatch(model, method, args=None, kwargs=None):
        if model == "utm.campaign" and method == "search_read":
            return campaigns
        if model == "crm.stage" and method == "search_read":
            return stages
        if model == "crm.lead" and method == "read_group":
            groupby = args[2]
            if groupby == ["campaign_id"]:
                return all_by_campaign
            if groupby == ["campaign_id", "stage_id"]:
                return by_campaign_stage
            if groupby == ["campaign_id", "media_buyer_id"]:
                return both_set
        raise AssertionError(f"unexpected RPC: {model}.{method} args={args}")

    client = MagicMock()
    client.execute_kw = AsyncMock(side_effect=_dispatch)
    client.close = AsyncMock()
    return client


def _row(result: dict, campaign_id: int) -> dict:
    return next(c for c in result["campaigns"] if c["campaign_id"] == campaign_id)


def _outcome(row: dict, group: str) -> int:
    return next(o["count"] for o in row["outcomes"] if o["group"] == group)


# ── Happy-path dataset ─────────────────────────────────────────────────────────
# gate override below: confirmed={"FB-AY"}, denylist={"BV - Daima"}
#
# id name         leads  both-set (buyer:count)          -> expected status
# 1  FB-AY        130    Ahmed 100 (100%)                -> confirmed
# 2  DOM          100    Z 60, Other 20 (75%)            -> dominant
# 3  MIX           80    A 10, B 9, C 8 (37%)            -> mixed
# 4  NOBUY         60    (none)                          -> no_buyer
# 5  BV - Daima    70    Mahmoud 70 (100%)               -> excluded_channel
# 6  None          55    (none)                          -> DATA-QUALITY (junk), not a row
# 7  TAIL          20    (none)                          -> long tail (< 50)
# (no campaign)    15                                    -> DATA-QUALITY (no_campaign)
#                       total population = 530

_CAMPAIGNS = [
    {"id": 1, "name": "FB-AY"},
    {"id": 2, "name": "DOM"},
    {"id": 3, "name": "MIX"},
    {"id": 4, "name": "NOBUY"},
    {"id": 5, "name": "BV - Daima"},
    {"id": 6, "name": "None"},
    {"id": 7, "name": "TAIL"},
]

_ALL_BY_CAMPAIGN = [
    {"campaign_id": [1, "FB-AY"], "__count": 130},
    {"campaign_id": [2, "DOM"], "__count": 100},
    {"campaign_id": [3, "MIX"], "__count": 80},
    {"campaign_id": [4, "NOBUY"], "__count": 60},
    {"campaign_id": [5, "BV - Daima"], "__count": 70},
    {"campaign_id": [6, "None"], "__count": 55},
    {"campaign_id": [7, "TAIL"], "__count": 20},
    {"campaign_id": False, "__count": 15},
]

_STAGES = [
    {"id": 1, "name": "New", "is_won": False},
    {"id": 2, "name": "Follow up", "is_won": False},
    {"id": 3, "name": "Reservation", "is_won": True},
    {"id": 4, "name": "Lost", "is_won": False},
    {"id": 5, "name": "New X", "is_won": False},
    {"id": 6, "name": "Interested", "is_won": False},
]

_BY_CAMPAIGN_STAGE = [
    # FB-AY: جديد 60, مهتم 30, اشترى 20, بلا نتيجة 20
    {"campaign_id": [1, "FB-AY"], "stage_id": [1, "New"], "__count": 60},
    {"campaign_id": [1, "FB-AY"], "stage_id": [2, "Follow up"], "__count": 30},
    {"campaign_id": [1, "FB-AY"], "stage_id": [3, "Reservation"], "__count": 20},
    {"campaign_id": [1, "FB-AY"], "stage_id": [4, "Lost"], "__count": 20},
    # DOM: جديد 50, مهتم 30, بلا نتيجة 20
    {"campaign_id": [2, "DOM"], "stage_id": [1, "New"], "__count": 50},
    {"campaign_id": [2, "DOM"], "stage_id": [6, "Interested"], "__count": 30},
    {"campaign_id": [2, "DOM"], "stage_id": [4, "Lost"], "__count": 20},
    # MIX: جديد 40, اشترى 10, بلا نتيجة 30
    {"campaign_id": [3, "MIX"], "stage_id": [1, "New"], "__count": 40},
    {"campaign_id": [3, "MIX"], "stage_id": [3, "Reservation"], "__count": 10},
    {"campaign_id": [3, "MIX"], "stage_id": [4, "Lost"], "__count": 30},
    # NOBUY: جديد 60
    {"campaign_id": [4, "NOBUY"], "stage_id": [1, "New"], "__count": 60},
    # BV - Daima: جديد 40, بلا نتيجة 30
    {"campaign_id": [5, "BV - Daima"], "stage_id": [1, "New"], "__count": 40},
    {"campaign_id": [5, "BV - Daima"], "stage_id": [4, "Lost"], "__count": 30},
    # None (junk): جديد 30, بلا نتيجة 25
    {"campaign_id": [6, "None"], "stage_id": [1, "New"], "__count": 30},
    {"campaign_id": [6, "None"], "stage_id": [4, "Lost"], "__count": 25},
    # TAIL: جديد 20
    {"campaign_id": [7, "TAIL"], "stage_id": [1, "New"], "__count": 20},
    # no campaign: New 10 + no-stage 5 -> جديد 15
    {"campaign_id": False, "stage_id": [1, "New"], "__count": 10},
    {"campaign_id": False, "stage_id": False, "__count": 5},
]

_BOTH_SET = [
    {"campaign_id": [1, "FB-AY"], "media_buyer_id": [101, "Ahmed Aymen"], "__count": 100},
    {"campaign_id": [2, "DOM"], "media_buyer_id": [201, "Buyer Z"], "__count": 60},
    {"campaign_id": [2, "DOM"], "media_buyer_id": [202, "Other Y"], "__count": 20},
    {"campaign_id": [3, "MIX"], "media_buyer_id": [301, "A"], "__count": 10},
    {"campaign_id": [3, "MIX"], "media_buyer_id": [302, "B"], "__count": 9},
    {"campaign_id": [3, "MIX"], "media_buyer_id": [303, "C"], "__count": 8},
    {"campaign_id": [5, "BV - Daima"], "media_buyer_id": [401, "Mahmoud Mohsen"], "__count": 70},
]


def _hp_client():
    return _make_client(_CAMPAIGNS, _ALL_BY_CAMPAIGN, _BY_CAMPAIGN_STAGE, _STAGES, _BOTH_SET)


async def _hp_overview(**kw):
    return await get_campaign_performance_overview(
        client=_hp_client(),
        confirmed_campaigns=frozenset({"FB-AY"}),
        denylist_campaigns=frozenset({"BV - Daima"}),
        **kw,
    )


# ── Happy path ──────────────────────────────────────────────────────────────


async def test_top_level_totals_and_no_warnings():
    result = await _hp_overview()
    assert result["total_leads_population"] == 530
    assert result["total_campaigns_with_leads"] == 7      # ids 1-7 incl. junk; excl. no-campaign
    assert result["min_lead_threshold"] == 50
    assert result["is_won_stage_names"] == ["Reservation"]
    assert result["config_warnings"] == []
    assert result["integrity_alerts"] == []


async def test_listed_rows_sorted_by_lead_volume_desc():
    result = await _hp_overview()
    # junk "None" excluded; TAIL (20) below threshold; the rest listed desc.
    assert [c["campaign_id"] for c in result["campaigns"]] == [1, 2, 3, 5, 4]
    assert result["listed_campaign_count"] == 5


async def test_per_campaign_funnel_counts_and_pct_and_reconcile():
    result = await _hp_overview()
    fb = _row(result, 1)
    assert fb["lead_count"] == 130
    assert _outcome(fb, GROUP_NEW) == 60
    assert _outcome(fb, GROUP_INTERESTED) == 30
    assert _outcome(fb, GROUP_WON) == 20
    assert _outcome(fb, GROUP_NO_RESULT) == 20
    won = next(o for o in fb["outcomes"] if o["group"] == GROUP_WON)
    assert won["pct"] == pytest.approx(15.38, abs=0.01)   # 20/130

    # Every row: 4 groups in fixed order, sum to lead_count, pct sums ~100.
    for c in result["campaigns"]:
        assert [o["group"] for o in c["outcomes"]] == list(GROUP_ORDER)
        assert sum(o["count"] for o in c["outcomes"]) == c["lead_count"]
        assert sum(o["pct"] for o in c["outcomes"]) == pytest.approx(100.0, abs=0.05)


async def test_global_reconciliation_listed_tail_junk_nocampaign():
    result = await _hp_overview()
    listed = sum(c["lead_count"] for c in result["campaigns"])
    tail = result["long_tail"]["lead_count"]
    junk = result["data_quality"]["junk_none"]["lead_count"]
    none_camp = result["data_quality"]["no_campaign"]["lead_count"]
    assert listed + tail + junk + none_camp == result["total_leads_population"] == 530


# ── Media-buyer display rule (§7.1, amended) ──────────────────────────────────


async def test_confirmed_campaign_shows_buyer():
    fb = _row(await _hp_overview(), 1)
    assert fb["attribution_status"] == "confirmed"
    assert fb["media_buyer_id"] == 101
    assert fb["media_buyer_name"] == "Ahmed Aymen"
    assert fb["concentration"] == 100.0
    assert fb["both_set_count"] == 100


async def test_dominant_non_confirmed_shows_buyer():
    dom = _row(await _hp_overview(), 2)
    assert dom["attribution_status"] == "dominant"
    assert dom["media_buyer_id"] == 201
    assert dom["concentration"] == pytest.approx(75.0, abs=0.01)   # 60/80
    assert dom["both_set_count"] == 80


async def test_mixed_campaign_hides_buyer():
    mix = _row(await _hp_overview(), 3)
    assert mix["attribution_status"] == "mixed"
    assert mix["media_buyer_id"] is None
    assert mix["media_buyer_name"] is None
    assert mix["concentration"] is None
    assert mix["both_set_count"] == 27       # 10+9+8


async def test_no_buyer_campaign():
    nb = _row(await _hp_overview(), 4)
    assert nb["attribution_status"] == "no_buyer"
    assert nb["media_buyer_id"] is None
    assert nb["both_set_count"] == 0


async def test_denylist_campaign_is_excluded_channel():
    deny = _row(await _hp_overview(), 5)
    assert deny["attribution_status"] == "excluded_channel"
    assert deny["media_buyer_id"] is None      # channel owner suppressed despite 100%
    assert deny["concentration"] is None
    assert deny["both_set_count"] == 70


# ── Long tail + data-quality flags ────────────────────────────────────────────


async def test_long_tail_aggregates_below_threshold():
    result = await _hp_overview()
    lt = result["long_tail"]
    assert lt["campaign_count"] == 1            # only TAIL (20 < 50)
    assert lt["lead_count"] == 20
    assert sum(o["count"] for o in lt["outcomes"]) == 20


async def test_threshold_param_moves_campaigns_into_tail():
    # Raise threshold to 90: only FB-AY(130) and DOM(100) stay listed; the rest tail.
    result = await _hp_overview(min_lead_threshold=90)
    assert [c["campaign_id"] for c in result["campaigns"]] == [1, 2]
    assert result["min_lead_threshold"] == 90
    # tail = MIX(80)+NOBUY(60)+BV(70)+TAIL(20) = 230 over 4 campaigns
    assert result["long_tail"]["campaign_count"] == 4
    assert result["long_tail"]["lead_count"] == 230


async def test_junk_none_is_data_quality_not_a_row():
    result = await _hp_overview()
    assert all(c["campaign_name"] != "None" for c in result["campaigns"])
    junk = result["data_quality"]["junk_none"]
    assert junk["label"] == "None"
    assert junk["campaign_ids"] == [6]
    assert junk["lead_count"] == 55
    assert _outcome_agg(junk, GROUP_NEW) == 30
    assert _outcome_agg(junk, GROUP_NO_RESULT) == 25


async def test_no_campaign_bucket_is_data_quality():
    nc = (await _hp_overview())["data_quality"]["no_campaign"]
    assert nc["label"] == "(no campaign)"
    assert nc["campaign_ids"] == []
    assert nc["lead_count"] == 15
    assert _outcome_agg(nc, GROUP_NEW) == 15


def _outcome_agg(bucket: dict, group: str) -> int:
    return next(o["count"] for o in bucket["outcomes"] if o["group"] == group)


# ── Confirmed-campaign drift -> integrity alert ───────────────────────────────


async def test_confirmed_below_90_alerts_and_downgrades_to_dominant():
    """A confirmed campaign that no longer holds >=90% raises an integrity alert
    and is shown as 'dominant' (still >= floor), not 'confirmed'."""
    campaigns = [{"id": 1, "name": "FB-AY"}]
    all_by = [{"campaign_id": [1, "FB-AY"], "__count": 100}]
    by_stage = [{"campaign_id": [1, "FB-AY"], "stage_id": [1, "New"], "__count": 100}]
    stages = [{"id": 1, "name": "New", "is_won": False}]
    both = [
        {"campaign_id": [1, "FB-AY"], "media_buyer_id": [101, "Ahmed Aymen"], "__count": 70},
        {"campaign_id": [1, "FB-AY"], "media_buyer_id": [102, "Noise"], "__count": 30},
    ]
    result = await get_campaign_performance_overview(
        client=_make_client(campaigns, all_by, by_stage, stages, both),
        confirmed_campaigns=frozenset({"FB-AY"}),
        denylist_campaigns=frozenset(),
    )
    row = _row(result, 1)
    assert row["attribution_status"] == "dominant"     # 70/100 = 70% < 90% gate
    assert row["media_buyer_id"] == 101
    assert len(result["integrity_alerts"]) == 1
    assert "70.0%" in result["integrity_alerts"][0]
    assert "FB-AY" in result["integrity_alerts"][0]


async def test_min_both_set_guard_yields_mixed_even_at_100pct():
    """A single buyer at 100% but with fewer than the minimum both-set leads is
    NOT labelled — it is 'mixed' (too small a sample to attribute)."""
    campaigns = [{"id": 1, "name": "TINY"}]
    all_by = [{"campaign_id": [1, "TINY"], "__count": 80}]
    by_stage = [{"campaign_id": [1, "TINY"], "stage_id": [1, "New"], "__count": 80}]
    stages = [{"id": 1, "name": "New", "is_won": False}]
    both = [
        {"campaign_id": [1, "TINY"], "media_buyer_id": [501, "Solo"], "__count": 3},
    ]
    result = await get_campaign_performance_overview(
        client=_make_client(campaigns, all_by, by_stage, stages, both),
        confirmed_campaigns=frozenset(),
        denylist_campaigns=frozenset(),
    )
    row = _row(result, 1)
    assert row["attribution_status"] == "mixed"   # 3 both-set < min sample of 10
    assert row["media_buyer_id"] is None
    assert row["both_set_count"] == 3


# ── Cache / read-only / error handling ────────────────────────────────────────


async def test_cache_hit_on_second_call_default_config():
    client = _hp_client()
    first = await get_campaign_performance_overview(client=client)
    assert first["cache_status"] == "fresh"
    second = await get_campaign_performance_overview(client=client)
    assert second["cache_status"] == "cached"
    assert second["rpc_duration_ms"] == 0


async def test_override_config_bypasses_cache():
    # An overridden gate must never be served from (or populate) the prod cache.
    first = await _hp_overview()
    assert first["cache_status"] == "fresh"
    second = await _hp_overview()
    assert second["cache_status"] == "fresh"


async def test_reconciliation_raises_on_inconsistent_funnel():
    """A campaign whose stage rows don't sum to its lead_count raises RuntimeError."""
    campaigns = [{"id": 1, "name": "BAD"}]
    all_by = [{"campaign_id": [1, "BAD"], "__count": 100}]
    by_stage = [{"campaign_id": [1, "BAD"], "stage_id": [1, "New"], "__count": 60}]  # 60 != 100
    stages = [{"id": 1, "name": "New", "is_won": False}]
    with pytest.raises(RuntimeError):
        await get_campaign_performance_overview(
            client=_make_client(campaigns, all_by, by_stage, stages, []),
            confirmed_campaigns=frozenset(),
            denylist_campaigns=frozenset(),
        )


async def test_read_only_violation_aborts_before_rpc():
    with patch(
        "backend.modules.campaign_performance.services.campaign_service.ALLOWED_METHODS",
        frozenset({"search_read", "read_group", "write"}),
    ):
        with pytest.raises(ReadOnlyViolationError):
            await get_campaign_performance_overview(client=_hp_client())


async def test_odoo_failure_raises_odoo_query_error():
    client = MagicMock()
    client.execute_kw = AsyncMock(side_effect=Exception("connection refused"))
    client.close = AsyncMock()
    with pytest.raises(OdooQueryError):
        await get_campaign_performance_overview(client=client)
