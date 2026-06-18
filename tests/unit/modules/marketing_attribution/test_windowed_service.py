"""
Unit tests for the Marketing Attribution WINDOWED buyer service
(get_attribution_overview_windowed).

OdooClient is fully mocked. now_cairo + legacy_days are INJECTED so the window and
the migration exclusion are deterministic (no live clock, no legacy RPC). The gate
is overridden per test.

Covers:
  - dated preset ("current" / "last3"): window resolution, Cairo month bucketing,
    legacy-day exclusion, out-of-window drop, per-buyer funnel + reconciliation
  - the campaign→buyer MAP stays ALL-TIME (confirmed + >=90%); only the LEADS feeding
    the funnel are windowed (a buyer's mapping never shifts with the window)
  - lists every buyer with >=1 attributed windowed lead, sorted by windowed volume
  - the UNATTRIBUTED bucket (no-campaign / denylisted / unconfirmed channels) + the
    windowed coverage + the population identity (attributed + unattributed == pop)
  - custom range (start_month..end_month) overrides the preset (is_custom_range)
  - a confirmed campaign below 90% raises an integrity alert AND its leads fall into
    the unattributed bucket (not attributed)
  - invalid window / invalid custom range raise InvalidTimelineRangeError (no RPC)
  - read-only abort, OdooQueryError, override bypasses cache, default config caches

Live verification: scripts/verify_marketing_attribution_windowed_live.py.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from backend.core.exceptions import OdooQueryError, ReadOnlyViolationError
from backend.modules.campaign_performance.services.timeline_service import (
    InvalidTimelineRangeError,
)
from backend.modules.marketing_attribution.domain import (
    GROUP_INTERESTED,
    GROUP_NEW,
    GROUP_NO_RESULT,
    GROUP_ORDER,
    GROUP_WON,
)
from backend.modules.marketing_attribution.services import cache as _cache
from backend.modules.marketing_attribution.services.attribution_service import (
    get_attribution_overview_windowed,
)

_CAIRO = ZoneInfo("Africa/Cairo")
_NOW = datetime(2026, 6, 17, 12, 0, 0, tzinfo=_CAIRO)   # current Cairo month = 2026-06
_LEGACY = {"2026-06-05"}                                # one legacy day inside the window


@pytest.fixture(autouse=True)
def fresh_cache():
    _cache.clear()
    yield
    _cache.clear()


# ── Dataset ───────────────────────────────────────────────────────────────────
# create_date is UTC-naive; Egypt is UTC+3 in June (DST) so 06:00 UTC -> 09:00 Cairo
# (same Cairo day). The 2026-06-05 lead lands on the injected legacy day; the
# 2026-05-20 lead is out of the "current" window; both are dropped from "current".
#
# Confirmed:  FB-AY (-> Ahmed), FB-AM (-> Abdallah)    Denylist: BV - Daima
# DOM is dominant-but-unconfirmed; BV - Daima is denylisted — both attribute NOTHING,
# so their windowed leads land in the UNATTRIBUTED bucket.

_CAMPAIGNS = [
    {"id": 1, "name": "FB-AY"},
    {"id": 2, "name": "DOM"},
    {"id": 3, "name": "BV - Daima"},
    {"id": 4, "name": "FB-AM"},
]

_STAGES = [
    {"id": 1, "name": "New", "is_won": False},
    {"id": 2, "name": "Follow up", "is_won": False},
    {"id": 3, "name": "Reservation", "is_won": True},
    {"id": 4, "name": "Lost", "is_won": False},
    {"id": 6, "name": "Interested", "is_won": False},
]

_LEADS = [
    # FB-AY (attributing → Ahmed): 3 in-window + 1 legacy-day (dropped) + 1 May (out of current)
    {"create_date": "2026-06-10 06:00:00", "campaign_id": [1, "FB-AY"], "stage_id": [1, "New"]},        # جديد
    {"create_date": "2026-06-11 06:00:00", "campaign_id": [1, "FB-AY"], "stage_id": [3, "Reservation"]},# اشترى
    {"create_date": "2026-06-12 06:00:00", "campaign_id": [1, "FB-AY"], "stage_id": [4, "Lost"]},       # بلا نتيجة
    {"create_date": "2026-06-05 06:00:00", "campaign_id": [1, "FB-AY"], "stage_id": [1, "New"]},        # LEGACY → drop
    {"create_date": "2026-05-20 06:00:00", "campaign_id": [1, "FB-AY"], "stage_id": [1, "New"]},        # 2026-05 → out of current
    # FB-AM (attributing → Abdallah): 2 in-window
    {"create_date": "2026-06-09 06:00:00", "campaign_id": [4, "FB-AM"], "stage_id": [1, "New"]},        # جديد
    {"create_date": "2026-06-09 06:00:00", "campaign_id": [4, "FB-AM"], "stage_id": [6, "Interested"]}, # مهتم
    # DOM (dominant but UNCONFIRMED → unattributed): 1 in-window
    {"create_date": "2026-06-08 06:00:00", "campaign_id": [2, "DOM"], "stage_id": [2, "Follow up"]},    # مهتم
    # BV - Daima (DENYLIST → unattributed): 1 in-window
    {"create_date": "2026-06-08 06:00:00", "campaign_id": [3, "BV - Daima"], "stage_id": [1, "New"]},   # جديد
    # no campaign (→ unattributed): 1 in-window
    {"create_date": "2026-06-07 06:00:00", "campaign_id": False, "stage_id": [1, "New"]},               # جديد
]

# ALL-TIME both-set (the campaign→buyer MAP): FB-AY 100% Ahmed, FB-AM 100% Abdallah,
# DOM 75% Z (unconfirmed), BV - Daima 100% Mahmoud (denylisted).
_BOTH_SET = [
    {"campaign_id": [1, "FB-AY"], "media_buyer_id": [101, "Ahmed Aymen"], "__count": 100},
    {"campaign_id": [4, "FB-AM"], "media_buyer_id": [102, "Abdallah Maher"], "__count": 100},
    {"campaign_id": [2, "DOM"], "media_buyer_id": [201, "Buyer Z"], "__count": 60},
    {"campaign_id": [2, "DOM"], "media_buyer_id": [202, "Other Y"], "__count": 20},
    {"campaign_id": [3, "BV - Daima"], "media_buyer_id": [301, "Mahmoud Mohsen"], "__count": 50},
]


def _make_client(campaigns=_CAMPAIGNS, stages=_STAGES, leads=_LEADS, both_set=_BOTH_SET):
    def _dispatch(model, method, args=None, kwargs=None):
        kwargs = kwargs or {}
        if model == "utm.campaign" and method == "search_read":
            return campaigns
        if model == "crm.stage" and method == "search_read":
            return stages
        if model == "crm.lead" and method == "search_read":
            # paged windowed fetch — everything on the first page, then stop
            return leads if kwargs.get("offset", 0) == 0 else []
        if model == "crm.lead" and method == "read_group":
            return both_set
        raise AssertionError(f"unexpected RPC: {model}.{method} args={args}")

    client = MagicMock()
    client.execute_kw = AsyncMock(side_effect=_dispatch)
    client.close = AsyncMock()
    return client


async def _windowed(**kw):
    return await get_attribution_overview_windowed(
        client=_make_client(),
        confirmed_campaigns=frozenset({"FB-AY", "FB-AM"}),
        denylist_campaigns=frozenset({"BV - Daima"}),
        legacy_days=set(_LEGACY),
        now_cairo=_NOW,
        **kw,
    )


def _buyer(result, bid):
    return next(b for b in result["buyers"] if b["buyer_id"] == bid)


def _g(row, group):
    return next(o["count"] for o in row["outcomes"] if o["group"] == group)


# ── Window resolution ─────────────────────────────────────────────────────────


async def test_current_window_resolution():
    r = await _windowed(window="current")
    assert r["window"] == "current"
    assert r["is_custom_range"] is False
    assert r["window_start_month"] == "2026-06"
    assert r["window_end_month"] == "2026-06"
    assert r["window_months"] == 1
    assert r["legacy_days_excluded"] == ["2026-06-05"]


async def test_last3_window_resolution_spans_three_months():
    r = await _windowed(window="last3")
    assert r["window"] == "last3"
    assert r["window_start_month"] == "2026-04"
    assert r["window_end_month"] == "2026-06"
    assert r["window_months"] == 3


# ── Buyers, funnels, sorting (current month) ──────────────────────────────────


async def test_current_window_buyers_funnel_and_sort():
    r = await _windowed(window="current")
    # buyers sorted by windowed volume desc: Ahmed(3) then Abdallah(2)
    assert [b["buyer_id"] for b in r["buyers"]] == [101, 102]

    ahmed = _buyer(r, 101)
    assert ahmed["total_attributed"] == 3            # legacy 06-05 + May 20 both dropped
    assert ahmed["campaign_ids"] == [1]
    assert _g(ahmed, GROUP_NEW) == 1
    assert _g(ahmed, GROUP_WON) == 1
    assert _g(ahmed, GROUP_NO_RESULT) == 1
    assert _g(ahmed, GROUP_INTERESTED) == 0

    abdallah = _buyer(r, 102)
    assert abdallah["total_attributed"] == 2
    assert _g(abdallah, GROUP_NEW) == 1
    assert _g(abdallah, GROUP_INTERESTED) == 1

    # every buyer: 4 groups in fixed order, summing to total_attributed
    for b in r["buyers"]:
        assert [o["group"] for o in b["outcomes"]] == list(GROUP_ORDER)
        assert sum(o["count"] for o in b["outcomes"]) == b["total_attributed"]


# ── Unattributed bucket + coverage + population identity ──────────────────────


async def test_unattributed_bucket_and_coverage_identity():
    r = await _windowed(window="current")
    ua = r["unattributed"]
    # DOM (مهتم) + BV-Daima (جديد) + no-campaign (جديد) = 3
    assert ua["lead_count"] == 3
    assert next(o["count"] for o in ua["outcomes"] if o["group"] == GROUP_NEW) == 2
    assert next(o["count"] for o in ua["outcomes"] if o["group"] == GROUP_INTERESTED) == 1
    assert [o["group"] for o in ua["outcomes"]] == list(GROUP_ORDER)
    assert sum(o["count"] for o in ua["outcomes"]) == ua["lead_count"]

    # attributed 5 (Ahmed 3 + Abdallah 2) + unattributed 3 == windowed population 8
    assert r["total_attributed"] == 5
    assert r["total_leads_population"] == 8
    assert r["total_attributed"] + ua["lead_count"] == r["total_leads_population"]
    assert r["coverage_pct"] == pytest.approx(62.5, abs=0.01)


async def test_denylisted_and_unconfirmed_never_a_buyer_row():
    r = await _windowed(window="current")
    bids = {b["buyer_id"] for b in r["buyers"]}
    assert 201 not in bids and 202 not in bids      # DOM's buyers (unconfirmed)
    assert 301 not in bids                           # BV - Daima's buyer (denylisted)
    # no integrity alert: DOM/BV-Daima are not in the CONFIRMED set, so no drift
    assert r["integrity_alerts"] == []


# ── last3 pulls in the May lead ───────────────────────────────────────────────


async def test_last3_window_pulls_in_the_may_lead():
    r = await _windowed(window="last3")
    ahmed = _buyer(r, 101)
    assert ahmed["total_attributed"] == 4            # 3 June + 1 May now both in-window
    assert _g(ahmed, GROUP_NEW) == 2                 # June New + May New
    # population grows by exactly the May lead; unattributed unchanged (all June)
    assert r["unattributed"]["lead_count"] == 3
    assert r["total_attributed"] == 6
    assert r["total_leads_population"] == 9
    assert r["coverage_pct"] == pytest.approx(66.67, abs=0.01)


# ── Buyer identity comes from the ALL-TIME map (not the windowed slice) ────────


async def test_buyer_identity_is_all_time_map():
    # FB-AY's 100% all-time concentration (both_set=100) is what makes Ahmed appear,
    # even though only 3 of his leads fall in the window.
    r = await _windowed(window="current")
    ahmed = _buyer(r, 101)
    assert ahmed["buyer_name"] == "Ahmed Aymen"
    assert ahmed["total_attributed"] == 3            # windowed, NOT the all-time 100


# ── Custom range overrides the preset ─────────────────────────────────────────


async def test_custom_range_overrides_preset():
    r = await _windowed(window="current", start_month="2026-04", end_month="2026-06")
    assert r["is_custom_range"] is True
    assert r["window"] == "custom"
    assert r["window_start_month"] == "2026-04"
    assert r["window_end_month"] == "2026-06"
    assert r["window_months"] == 3
    # the May FB-AY lead is in-window under the custom span (same as last3)
    assert _buyer(r, 101)["total_attributed"] == 4


# ── Confirmed-below-90 → integrity alert + leads unattributed ─────────────────


async def test_confirmed_below_90_alerts_and_falls_to_unattributed():
    # FB-AY drifts to 80% all-time → NOT attributed: an integrity alert is raised and
    # its windowed leads land in the unattributed bucket (no Ahmed buyer row).
    both_set = [
        {"campaign_id": [1, "FB-AY"], "media_buyer_id": [101, "Ahmed Aymen"], "__count": 80},
        {"campaign_id": [1, "FB-AY"], "media_buyer_id": [109, "Noise"], "__count": 20},
        {"campaign_id": [4, "FB-AM"], "media_buyer_id": [102, "Abdallah Maher"], "__count": 100},
    ]
    r = await get_attribution_overview_windowed(
        client=_make_client(both_set=both_set),
        confirmed_campaigns=frozenset({"FB-AY", "FB-AM"}),
        denylist_campaigns=frozenset({"BV - Daima"}),
        legacy_days=set(_LEGACY),
        now_cairo=_NOW,
        window="current",
    )
    assert [b["buyer_id"] for b in r["buyers"]] == [102]   # only Abdallah attributes
    assert len(r["integrity_alerts"]) == 1
    assert "80.0%" in r["integrity_alerts"][0] and "< 90%" in r["integrity_alerts"][0]
    # FB-AY's 3 windowed leads now count as unattributed (3 DOM/BVD/no-camp + 3 FB-AY)
    assert r["unattributed"]["lead_count"] == 6
    assert r["total_attributed"] == 2
    assert r["total_leads_population"] == 8


# ── Validation (before any RPC) ───────────────────────────────────────────────


async def test_invalid_window_raises():
    with pytest.raises(InvalidTimelineRangeError):
        await _windowed(window="bogus")


async def test_all_window_not_served_here():
    # "all" is NOT a dated preset — the route sends it to the un-windowed overview.
    with pytest.raises(InvalidTimelineRangeError):
        await _windowed(window="all")


async def test_invalid_custom_range_raises():
    with pytest.raises(InvalidTimelineRangeError):
        await _windowed(start_month="2026-06", end_month="2026-01")   # start > end


async def test_partial_custom_range_raises():
    with pytest.raises(InvalidTimelineRangeError):
        await _windowed(start_month="2026-01")                        # both-or-neither


# ── Cache / read-only / error handling ────────────────────────────────────────


async def test_override_config_bypasses_cache():
    first = await _windowed(window="current")
    second = await _windowed(window="current")
    assert first["cache_status"] == "fresh"
    assert second["cache_status"] == "fresh"


async def test_default_config_caches():
    # No gate/legacy/now overrides => default_config True => the result is cached.
    # (The mock's tiny lead set never reaches LEGACY_DAY_MIN, so legacy detection is a
    # no-op; the both-set map has no confirmed-domain campaigns, so buyers is empty —
    # only the cache_status transition is asserted.)
    client = _make_client()
    first = await get_attribution_overview_windowed(client=client, window="current")
    second = await get_attribution_overview_windowed(client=client, window="current")
    assert first["cache_status"] == "fresh"
    assert second["cache_status"] == "cached"
    assert second["rpc_duration_ms"] == 0


async def test_read_only_violation_aborts_before_rpc():
    with patch(
        "backend.modules.marketing_attribution.services.attribution_service.ALLOWED_METHODS",
        frozenset({"search_read", "read_group", "write"}),
    ):
        with pytest.raises(ReadOnlyViolationError):
            await _windowed(window="current")


async def test_odoo_failure_raises_odoo_query_error():
    client = MagicMock()
    client.execute_kw = AsyncMock(side_effect=Exception("connection refused"))
    client.close = AsyncMock()
    with pytest.raises(OdooQueryError):
        await get_attribution_overview_windowed(
            client=client, window="current",
            legacy_days=set(_LEGACY), now_cairo=_NOW,
            confirmed_campaigns=frozenset(), denylist_campaigns=frozenset(),
        )
