"""
Unit tests for the Marketing Attribution service.

OdooClient is fully mocked via a dispatch function keyed on (model, method,
groupby) — robust against the conditional 5th RPC (skipped when nothing
attributes). No live Odoo connection is made.

Covers D8 + plan amendments:
  D8  — concentration boundary at exactly 0.90; denylist exclusion at 100%;
        confirmed-only attribution; pending-flag detection; stage->group
        mapping incl. no-stage -> جديد; group counts reconcile to total.
  A1  — confirmed campaign < 90% excluded from attribution AND raises an
        integrity alert.
  A3  — a configured name matching two campaign ids includes both.
  A6  — a confirmed campaign's buyerless leads (media_buyer_id False) still
        count toward the buyer's total (the inferred attribution).

Live verification: scripts/verify_marketing_attribution_live.py.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.core.exceptions import OdooQueryError, ReadOnlyViolationError
from backend.modules.marketing_attribution.domain import (
    GROUP_INTERESTED,
    GROUP_NEW,
    GROUP_NO_RESULT,
    GROUP_ORDER,
    GROUP_WON,
    classify_stage,
)
from backend.modules.marketing_attribution.services import cache as _cache
from backend.modules.marketing_attribution.services.attribution_service import (
    get_attribution_overview,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def fresh_cache():
    _cache.clear()
    yield
    _cache.clear()


def _make_client(campaigns, both_set, all_by_campaign, stages, attrib):
    """Dispatch mock keyed on (model, method, groupby) — order-independent."""

    def _dispatch(model, method, args=None, kwargs=None):
        if model == "utm.campaign" and method == "search_read":
            return campaigns
        if model == "crm.stage" and method == "search_read":
            return stages
        if model == "crm.lead" and method == "read_group":
            groupby = args[2]
            if groupby == ["campaign_id", "media_buyer_id"]:
                return both_set
            if groupby == ["campaign_id"]:
                return all_by_campaign
            if groupby == ["campaign_id", "stage_id"]:
                return attrib
        raise AssertionError(f"unexpected RPC: {model}.{method} args={args}")

    client = MagicMock()
    client.execute_kw = AsyncMock(side_effect=_dispatch)
    client.close = AsyncMock()
    return client


def _outcome(buyer_row: dict, group: str) -> int:
    return next(o["count"] for o in buyer_row["outcomes"] if o["group"] == group)


def _buyer(result: dict, buyer_id: int) -> dict:
    return next(b for b in result["buyers"] if b["buyer_id"] == buyer_id)


# ── Happy-path dataset (default production config) ─────────────────────────────
# Confirmed (domain): FB-AY, FB-AM, Outsource-Y, FB-LA
# Denylist (domain):  BV - Daima, Website - Daima
#
# id  name            both-set (buyer:count)            all-leads   gate
# 1   FB-AY           Ahmed 100                         130         confirmed -> attributes
# 2   FB-AM           Abdallah 100                      110         confirmed -> attributes
# 3   Outsource-Y     Yomna 100                         120         confirmed -> attributes
# 4   FB-LA           Ali 100                           105         confirmed -> attributes
# 5   NEW-CAMP        Hagar 50, Other 5 (90.9%)         60          pending (>=90%, unconfirmed)
# 6   BV - Daima        Mahmoud 80 (100%)                 90          denylist -> excluded
# 7   Mixed           Ahmed 10, Abdallah 9 (52.6%)      25          noise -> not attributed
# 8   Website - Daima   Mahmoud 40 (100%)                 45          denylist -> excluded
# (no campaign)                                          15
#                                            total population = 700

_HP_CAMPAIGNS = [
    {"id": 1, "name": "FB-AY"},
    {"id": 2, "name": "FB-AM"},
    {"id": 3, "name": "Outsource-Y"},
    {"id": 4, "name": "FB-LA"},
    {"id": 5, "name": "NEW-CAMP"},
    {"id": 6, "name": "BV - Daima"},
    {"id": 7, "name": "Mixed"},
    {"id": 8, "name": "Website - Daima"},
]

_HP_BOTH_SET = [
    {"campaign_id": [1, "FB-AY"], "media_buyer_id": [101, "Ahmed Aymen"], "__count": 100},
    {"campaign_id": [2, "FB-AM"], "media_buyer_id": [102, "Abdallah Maher"], "__count": 100},
    {"campaign_id": [3, "Outsource-Y"], "media_buyer_id": [103, "Yomna Musaad"], "__count": 100},
    {"campaign_id": [4, "FB-LA"], "media_buyer_id": [104, "Ali shaban"], "__count": 100},
    {"campaign_id": [5, "NEW-CAMP"], "media_buyer_id": [105, "Hagar X"], "__count": 50},
    {"campaign_id": [5, "NEW-CAMP"], "media_buyer_id": [106, "Other Y"], "__count": 5},
    {"campaign_id": [6, "BV - Daima"], "media_buyer_id": [107, "Mahmoud Mohsen"], "__count": 80},
    {"campaign_id": [7, "Mixed"], "media_buyer_id": [101, "Ahmed Aymen"], "__count": 10},
    {"campaign_id": [7, "Mixed"], "media_buyer_id": [102, "Abdallah Maher"], "__count": 9},
    {"campaign_id": [8, "Website - Daima"], "media_buyer_id": [107, "Mahmoud Mohsen"], "__count": 40},
]

_HP_ALL_BY_CAMPAIGN = [
    {"campaign_id": [1, "FB-AY"], "__count": 130},
    {"campaign_id": [2, "FB-AM"], "__count": 110},
    {"campaign_id": [3, "Outsource-Y"], "__count": 120},
    {"campaign_id": [4, "FB-LA"], "__count": 105},
    {"campaign_id": [5, "NEW-CAMP"], "__count": 60},
    {"campaign_id": [6, "BV - Daima"], "__count": 90},
    {"campaign_id": [7, "Mixed"], "__count": 25},
    {"campaign_id": [8, "Website - Daima"], "__count": 45},
    {"campaign_id": False, "__count": 15},
]

_HP_STAGES = [
    {"id": 1, "name": "New", "is_won": False},
    {"id": 2, "name": "Follow up", "is_won": False},
    {"id": 3, "name": "Reservation", "is_won": True},
    {"id": 4, "name": "Lost", "is_won": False},
    {"id": 5, "name": "New X", "is_won": False},
    {"id": 6, "name": "Interested", "is_won": False},
]

_HP_ATTRIB = [
    {"campaign_id": [1, "FB-AY"], "stage_id": [1, "New"], "__count": 60},
    {"campaign_id": [1, "FB-AY"], "stage_id": [3, "Reservation"], "__count": 20},
    {"campaign_id": [1, "FB-AY"], "stage_id": [2, "Follow up"], "__count": 30},
    {"campaign_id": [1, "FB-AY"], "stage_id": [4, "Lost"], "__count": 20},
    {"campaign_id": [2, "FB-AM"], "stage_id": [1, "New"], "__count": 50},
    {"campaign_id": [2, "FB-AM"], "stage_id": [6, "Interested"], "__count": 40},
    {"campaign_id": [2, "FB-AM"], "stage_id": [4, "Lost"], "__count": 20},
    {"campaign_id": [3, "Outsource-Y"], "stage_id": [5, "New X"], "__count": 70},
    {"campaign_id": [3, "Outsource-Y"], "stage_id": [3, "Reservation"], "__count": 30},
    {"campaign_id": [3, "Outsource-Y"], "stage_id": False, "__count": 20},
    {"campaign_id": [4, "FB-LA"], "stage_id": [2, "Follow up"], "__count": 55},
    {"campaign_id": [4, "FB-LA"], "stage_id": [4, "Lost"], "__count": 50},
]


def _hp_client():
    return _make_client(
        _HP_CAMPAIGNS, _HP_BOTH_SET, _HP_ALL_BY_CAMPAIGN, _HP_STAGES, _HP_ATTRIB
    )


# ── Happy path ────────────────────────────────────────────────────────────────


async def test_happy_path_overview():
    result = await get_attribution_overview(client=_hp_client())

    # Top-level totals
    assert result["total_leads_population"] == 700
    assert result["total_attributed"] == 465
    assert result["attribution_pct"] == pytest.approx(66.43, abs=0.01)
    assert result["is_won_stage_names"] == ["Reservation"]
    assert result["config_warnings"] == []
    assert result["integrity_alerts"] == []

    # Buyers — sorted by total desc
    assert [b["buyer_id"] for b in result["buyers"]] == [101, 103, 102, 104]

    ahmed = _buyer(result, 101)
    assert ahmed["total_attributed"] == 130
    assert ahmed["campaign_ids"] == [1]
    assert _outcome(ahmed, GROUP_NEW) == 60
    assert _outcome(ahmed, GROUP_INTERESTED) == 30
    assert _outcome(ahmed, GROUP_WON) == 20
    assert _outcome(ahmed, GROUP_NO_RESULT) == 20

    # Yomna — New X + no-stage both fold into جديد
    yomna = _buyer(result, 103)
    assert yomna["total_attributed"] == 120
    assert _outcome(yomna, GROUP_NEW) == 90       # 70 (New X) + 20 (no stage)
    assert _outcome(yomna, GROUP_WON) == 30

    # Reconciliation: every buyer's 4 groups sum to the total, fixed order
    for b in result["buyers"]:
        assert [o["group"] for o in b["outcomes"]] == list(GROUP_ORDER)
        assert sum(o["count"] for o in b["outcomes"]) == b["total_attributed"]
        # pct sums to ~100 for non-empty buyers
        assert sum(o["pct"] for o in b["outcomes"]) == pytest.approx(100.0, abs=0.05)

    # Confirmed campaigns surfaced (4), sorted by lead_count desc
    assert [c["campaign_id"] for c in result["confirmed_campaigns"]] == [1, 3, 2, 4]
    fb_ay = next(c for c in result["confirmed_campaigns"] if c["campaign_id"] == 1)
    assert fb_ay["dominant_buyer_name"] == "Ahmed Aymen"
    assert fb_ay["concentration"] == 100.0
    assert fb_ay["lead_count"] == 130

    # Pending — NEW-CAMP only
    assert len(result["pending_campaigns"]) == 1
    pend = result["pending_campaigns"][0]
    assert pend["campaign_id"] == 5
    assert pend["dominant_buyer_id"] == 105
    assert pend["concentration"] == pytest.approx(90.91, abs=0.01)
    assert pend["both_set_count"] == 55
    assert pend["lead_count"] == 60


async def test_cache_hit_on_second_call():
    client = _hp_client()
    first = await get_attribution_overview(client=client)
    assert first["cache_status"] == "fresh"

    second = await get_attribution_overview(client=client)
    assert second["cache_status"] == "cached"
    assert second["rpc_duration_ms"] == 0
    assert second["total_attributed"] == first["total_attributed"]


# ── D8 / A-amendment focused tests ────────────────────────────────────────────


async def test_concentration_exactly_90_qualifies():
    """9/10 = exactly 0.90 -> the confirmed campaign attributes (boundary)."""
    campaigns = [{"id": 1, "name": "BND"}]
    both_set = [
        {"campaign_id": [1, "BND"], "media_buyer_id": [201, "Buyer X"], "__count": 9},
        {"campaign_id": [1, "BND"], "media_buyer_id": [202, "Buyer Y"], "__count": 1},
    ]
    all_by = [{"campaign_id": [1, "BND"], "__count": 10}]
    stages = [{"id": 1, "name": "New", "is_won": False}]
    attrib = [{"campaign_id": [1, "BND"], "stage_id": [1, "New"], "__count": 10}]

    result = await get_attribution_overview(
        client=_make_client(campaigns, both_set, all_by, stages, attrib),
        confirmed_campaigns=frozenset({"BND"}),
        denylist_campaigns=frozenset(),
    )

    assert result["integrity_alerts"] == []
    assert [c["campaign_id"] for c in result["confirmed_campaigns"]] == [1]
    assert result["confirmed_campaigns"][0]["concentration"] == 90.0
    assert _buyer(result, 201)["total_attributed"] == 10


async def test_concentration_below_90_excluded_and_alerts():
    """A confirmed campaign at 88% is NOT attributed AND raises an integrity alert (A1)."""
    campaigns = [{"id": 1, "name": "BND"}]
    both_set = [
        {"campaign_id": [1, "BND"], "media_buyer_id": [201, "Buyer X"], "__count": 88},
        {"campaign_id": [1, "BND"], "media_buyer_id": [202, "Buyer Y"], "__count": 12},
    ]
    all_by = [{"campaign_id": [1, "BND"], "__count": 100}]
    stages = [{"id": 1, "name": "New", "is_won": False}]
    # RPC 5 must NOT be requested (nothing attributes) — attrib left empty.
    result = await get_attribution_overview(
        client=_make_client(campaigns, both_set, all_by, stages, []),
        confirmed_campaigns=frozenset({"BND"}),
        denylist_campaigns=frozenset(),
    )

    assert result["buyers"] == []
    assert result["total_attributed"] == 0
    assert result["confirmed_campaigns"] == []
    assert len(result["integrity_alerts"]) == 1
    assert "88.0%" in result["integrity_alerts"][0]
    assert "< 90%" in result["integrity_alerts"][0]


async def test_denylist_excludes_at_100pct():
    """A denylisted campaign at 100% concentration is neither attributed nor pending."""
    campaigns = [{"id": 1, "name": "DENY"}]
    both_set = [
        {"campaign_id": [1, "DENY"], "media_buyer_id": [301, "Channel Owner"], "__count": 100},
    ]
    all_by = [{"campaign_id": [1, "DENY"], "__count": 100}]
    stages = [{"id": 1, "name": "New", "is_won": False}]

    result = await get_attribution_overview(
        client=_make_client(campaigns, both_set, all_by, stages, []),
        confirmed_campaigns=frozenset(),
        denylist_campaigns=frozenset({"DENY"}),
    )

    assert result["buyers"] == []
    assert result["confirmed_campaigns"] == []
    assert result["pending_campaigns"] == []      # denylisted -> not surfaced as pending
    assert result["integrity_alerts"] == []        # not confirmed -> no drift alert


async def test_confirmed_only_attribution():
    """A campaign NOT in the confirmed set is not attributed even at 100% with a buyer set."""
    campaigns = [{"id": 1, "name": "FB-AY"}, {"id": 2, "name": "OTHER"}]
    both_set = [
        {"campaign_id": [1, "FB-AY"], "media_buyer_id": [101, "Ahmed Aymen"], "__count": 100},
        {"campaign_id": [2, "OTHER"], "media_buyer_id": [301, "Z"], "__count": 100},
    ]
    all_by = [
        {"campaign_id": [1, "FB-AY"], "__count": 100},
        {"campaign_id": [2, "OTHER"], "__count": 100},
    ]
    stages = [{"id": 1, "name": "New", "is_won": False}]
    attrib = [{"campaign_id": [1, "FB-AY"], "stage_id": [1, "New"], "__count": 100}]

    result = await get_attribution_overview(
        client=_make_client(campaigns, both_set, all_by, stages, attrib),
        confirmed_campaigns=frozenset({"FB-AY"}),
        denylist_campaigns=frozenset(),
    )

    # OTHER's buyer (301) is never attributed, even though it has a buyer at 100%.
    assert result["total_attributed"] == 100
    assert [b["buyer_id"] for b in result["buyers"]] == [101]
    # OTHER instead surfaces as a pending campaign (qualifies, unconfirmed).
    assert [p["campaign_id"] for p in result["pending_campaigns"]] == [2]


async def test_pending_flag_detection():
    """A qualifying, non-denied, non-confirmed campaign appears in pending (§3.5)."""
    campaigns = [{"id": 1, "name": "PEND"}]
    both_set = [
        {"campaign_id": [1, "PEND"], "media_buyer_id": [401, "Maybe Buyer"], "__count": 95},
        {"campaign_id": [1, "PEND"], "media_buyer_id": [402, "Noise"], "__count": 5},
    ]
    all_by = [{"campaign_id": [1, "PEND"], "__count": 200}]
    stages = [{"id": 1, "name": "New", "is_won": False}]

    result = await get_attribution_overview(
        client=_make_client(campaigns, both_set, all_by, stages, []),
        confirmed_campaigns=frozenset(),
        denylist_campaigns=frozenset(),
    )

    assert result["buyers"] == []
    assert len(result["pending_campaigns"]) == 1
    p = result["pending_campaigns"][0]
    assert p["campaign_id"] == 1
    assert p["dominant_buyer_id"] == 401
    assert p["concentration"] == 95.0
    assert p["both_set_count"] == 100
    assert p["lead_count"] == 200


async def test_inferred_attribution_counts_buyerless_leads():
    """A6 — leads with media_buyer_id False in a confirmed campaign still count.

    both-set is only 10, but the campaign has 100 leads total; all 100 attribute
    to the dominant buyer (the inferred ~18% — the core of the module).
    """
    campaigns = [{"id": 1, "name": "FB-AY"}]
    both_set = [
        {"campaign_id": [1, "FB-AY"], "media_buyer_id": [101, "Ahmed Aymen"], "__count": 10},
    ]
    all_by = [{"campaign_id": [1, "FB-AY"], "__count": 100}]
    stages = [
        {"id": 1, "name": "New", "is_won": False},
        {"id": 4, "name": "Lost", "is_won": False},
    ]
    attrib = [
        {"campaign_id": [1, "FB-AY"], "stage_id": [1, "New"], "__count": 70},
        {"campaign_id": [1, "FB-AY"], "stage_id": [4, "Lost"], "__count": 30},
    ]

    result = await get_attribution_overview(
        client=_make_client(campaigns, both_set, all_by, stages, attrib),
        confirmed_campaigns=frozenset({"FB-AY"}),
        denylist_campaigns=frozenset(),
    )

    ahmed = _buyer(result, 101)
    assert ahmed["total_attributed"] == 100        # not 10 — buyerless leads included
    assert _outcome(ahmed, GROUP_NEW) == 70
    assert _outcome(ahmed, GROUP_NO_RESULT) == 30


async def test_duplicate_campaign_name_includes_both_ids():
    """A3 — a configured name matching two utm.campaign ids includes both, with a warning."""
    campaigns = [{"id": 1, "name": "FB-AY"}, {"id": 2, "name": "FB-AY"}]
    both_set = [
        {"campaign_id": [1, "FB-AY"], "media_buyer_id": [101, "Ahmed Aymen"], "__count": 50},
        {"campaign_id": [2, "FB-AY"], "media_buyer_id": [101, "Ahmed Aymen"], "__count": 50},
    ]
    all_by = [
        {"campaign_id": [1, "FB-AY"], "__count": 60},
        {"campaign_id": [2, "FB-AY"], "__count": 40},
    ]
    stages = [{"id": 1, "name": "New", "is_won": False}]
    attrib = [
        {"campaign_id": [1, "FB-AY"], "stage_id": [1, "New"], "__count": 60},
        {"campaign_id": [2, "FB-AY"], "stage_id": [1, "New"], "__count": 40},
    ]

    result = await get_attribution_overview(
        client=_make_client(campaigns, both_set, all_by, stages, attrib),
        confirmed_campaigns=frozenset({"FB-AY"}),
        denylist_campaigns=frozenset(),
    )

    assert {c["campaign_id"] for c in result["confirmed_campaigns"]} == {1, 2}
    assert _buyer(result, 101)["total_attributed"] == 100
    assert _buyer(result, 101)["campaign_ids"] == [1, 2]
    assert any("matched 2" in w for w in result["config_warnings"])


async def test_unresolved_config_name_warns_not_crash():
    """A configured name with no matching campaign warns and is ignored (no crash)."""
    campaigns = [{"id": 1, "name": "FB-AY"}]
    both_set = [
        {"campaign_id": [1, "FB-AY"], "media_buyer_id": [101, "Ahmed Aymen"], "__count": 100},
    ]
    all_by = [{"campaign_id": [1, "FB-AY"], "__count": 100}]
    stages = [{"id": 1, "name": "New", "is_won": False}]
    attrib = [{"campaign_id": [1, "FB-AY"], "stage_id": [1, "New"], "__count": 100}]

    result = await get_attribution_overview(
        client=_make_client(campaigns, both_set, all_by, stages, attrib),
        confirmed_campaigns=frozenset({"FB-AY", "GHOST"}),
        denylist_campaigns=frozenset(),
    )

    assert _buyer(result, 101)["total_attributed"] == 100
    assert any("GHOST" in w and "did not resolve" in w for w in result["config_warnings"])


# ── Error handling / read-only ────────────────────────────────────────────────


async def test_read_only_violation_aborts_before_rpc():
    with patch(
        "backend.modules.marketing_attribution.services.attribution_service.ALLOWED_METHODS",
        frozenset({"search_read", "read_group", "write"}),
    ):
        with pytest.raises(ReadOnlyViolationError):
            await get_attribution_overview(client=_hp_client())


async def test_odoo_failure_raises_odoo_query_error():
    client = MagicMock()
    client.execute_kw = AsyncMock(side_effect=Exception("connection refused"))
    client.close = AsyncMock()
    with pytest.raises(OdooQueryError):
        await get_attribution_overview(client=client)


# ── classify_stage — pure mapping (§3.7) ──────────────────────────────────────


def test_no_stage_maps_to_jadid():
    assert classify_stage(False, {}) == GROUP_NEW
    assert classify_stage(None, {}) == GROUP_NEW
    assert classify_stage(0, {}) == GROUP_NEW


def test_is_won_true_maps_to_ashtara_regardless_of_name():
    info = {7: {"name": "Down Payment Confirm & Contracted", "is_won": True}}
    assert classify_stage(7, info) == GROUP_WON
    # Even a weirdly-named won stage still maps to اشترى by is_won, not by name.
    info2 = {8: {"name": "Something Else", "is_won": True}}
    assert classify_stage(8, info2) == GROUP_WON


def test_new_and_new_x_map_to_jadid():
    info = {1: {"name": "New", "is_won": False}, 2: {"name": "New X", "is_won": False}}
    assert classify_stage(1, info) == GROUP_NEW
    assert classify_stage(2, info) == GROUP_NEW


def test_follow_up_and_interested_map_to_muhtam():
    info = {
        3: {"name": "Follow up", "is_won": False},
        4: {"name": "Interested", "is_won": False},
    }
    assert classify_stage(3, info) == GROUP_INTERESTED
    assert classify_stage(4, info) == GROUP_INTERESTED


def test_other_and_unknown_stages_map_to_no_result():
    info = {5: {"name": "Lost", "is_won": False}}
    assert classify_stage(5, info) == GROUP_NO_RESULT       # known but uncategorized
    assert classify_stage(999, info) == GROUP_NO_RESULT     # unknown stage id
