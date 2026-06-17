"""
Unit tests for the Campaign Performance WINDOWED list service
(get_campaign_performance_windowed).

OdooClient is fully mocked. now_cairo + legacy_days are INJECTED so the window and
the migration exclusion are deterministic (no live clock, no legacy RPC). The gate
is overridden per test.

Covers:
  - dated preset ("current" / "last3"): window resolution, Cairo month bucketing,
    legacy-day exclusion, out-of-window drop, per-campaign funnel + reconciliation
  - lists EVERY active campaign (>=1 windowed lead) sorted by windowed volume; no
    long-tail, no threshold; zero-activity campaigns hidden
  - the media-buyer cell stays the ALL-TIME both-set status (confirmed/dominant)
  - windowed junk "None" + no-campaign data-quality buckets + global identity
  - custom range (start_month..end_month) overrides the preset (is_custom_range)
  - invalid window / invalid custom range raise InvalidTimelineRangeError (no RPC)
  - read-only abort, OdooQueryError, override bypasses cache

Live verification: scripts/verify_campaign_performance_windowed_live.py.
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
    get_campaign_performance_windowed,
)
from backend.modules.campaign_performance.services.timeline_service import (
    InvalidTimelineRangeError,
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

_CAMPAIGNS = [
    {"id": 1, "name": "FB-AY"},
    {"id": 2, "name": "DOM"},
    {"id": 3, "name": "None"},      # junk
]

_STAGES = [
    {"id": 1, "name": "New", "is_won": False},
    {"id": 2, "name": "Follow up", "is_won": False},
    {"id": 3, "name": "Reservation", "is_won": True},
    {"id": 4, "name": "Lost", "is_won": False},
]

_LEADS = [
    # FB-AY — 3 in-window (جديد, اشترى, بلا نتيجة) + 1 legacy-day (dropped)
    {"create_date": "2026-06-10 06:00:00", "campaign_id": [1, "FB-AY"], "stage_id": [1, "New"]},
    {"create_date": "2026-06-11 06:00:00", "campaign_id": [1, "FB-AY"], "stage_id": [3, "Reservation"]},
    {"create_date": "2026-06-12 06:00:00", "campaign_id": [1, "FB-AY"], "stage_id": [4, "Lost"]},
    {"create_date": "2026-06-05 06:00:00", "campaign_id": [1, "FB-AY"], "stage_id": [1, "New"]},   # LEGACY → drop
    # DOM — 1 in-window (مهتم) + 1 out-of-window May (dropped from "current")
    {"create_date": "2026-06-09 06:00:00", "campaign_id": [2, "DOM"], "stage_id": [2, "Follow up"]},
    {"create_date": "2026-05-20 06:00:00", "campaign_id": [2, "DOM"], "stage_id": [1, "New"]},     # 2026-05 → out
    # junk "None" — 1 in-window
    {"create_date": "2026-06-08 06:00:00", "campaign_id": [3, "None"], "stage_id": [1, "New"]},
    # no campaign — 1 in-window
    {"create_date": "2026-06-07 06:00:00", "campaign_id": False, "stage_id": [1, "New"]},
]

# ALL-TIME both-set (buyer cell): FB-AY 100% Ahmed (confirmed), DOM 75% Z (dominant)
_BOTH_SET = [
    {"campaign_id": [1, "FB-AY"], "media_buyer_id": [101, "Ahmed Aymen"], "__count": 100},
    {"campaign_id": [2, "DOM"], "media_buyer_id": [201, "Buyer Z"], "__count": 60},
    {"campaign_id": [2, "DOM"], "media_buyer_id": [202, "Other Y"], "__count": 20},
]


def _make_client(campaigns=_CAMPAIGNS, stages=_STAGES, leads=_LEADS, both_set=_BOTH_SET):
    def _dispatch(model, method, args=None, kwargs=None):
        kwargs = kwargs or {}
        if model == "utm.campaign" and method == "search_read":
            return campaigns
        if model == "crm.stage" and method == "search_read":
            return stages
        if model == "crm.lead" and method == "search_read":
            # paged windowed fetch — return everything on the first page, then stop
            return leads if kwargs.get("offset", 0) == 0 else []
        if model == "crm.lead" and method == "read_group":
            return both_set
        raise AssertionError(f"unexpected RPC: {model}.{method} args={args}")

    client = MagicMock()
    client.execute_kw = AsyncMock(side_effect=_dispatch)
    client.close = AsyncMock()
    return client


async def _windowed(**kw):
    return await get_campaign_performance_windowed(
        client=_make_client(),
        confirmed_campaigns=frozenset({"FB-AY"}),
        denylist_campaigns=frozenset(),
        legacy_days=set(_LEGACY),
        now_cairo=_NOW,
        **kw,
    )


def _row(result, cid):
    return next(c for c in result["campaigns"] if c["campaign_id"] == cid)


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


# ── Bucketing, legacy + out-of-window exclusion, reconciliation ───────────────


async def test_current_window_counts_and_excludes_legacy_and_out_of_window():
    r = await _windowed(window="current")
    # active real campaigns: FB-AY(3), DOM(1) — sorted by windowed volume desc
    assert [c["campaign_id"] for c in r["campaigns"]] == [1, 2]
    assert r["active_campaign_count"] == 2

    fb = _row(r, 1)
    assert fb["lead_count"] == 3                 # legacy 06-05 lead dropped
    assert _g(fb, GROUP_NEW) == 1
    assert _g(fb, GROUP_WON) == 1
    assert _g(fb, GROUP_NO_RESULT) == 1
    assert _g(fb, GROUP_INTERESTED) == 0

    dom = _row(r, 2)
    assert dom["lead_count"] == 1                # the 2026-05 lead is out of the window
    assert _g(dom, GROUP_INTERESTED) == 1

    # every row: 4 groups in fixed order, summing to lead_count
    for c in r["campaigns"]:
        assert [o["group"] for o in c["outcomes"]] == list(GROUP_ORDER)
        assert sum(o["count"] for o in c["outcomes"]) == c["lead_count"]


async def test_last3_window_pulls_in_the_may_lead():
    r = await _windowed(window="last3")
    dom = _row(r, 2)
    assert dom["lead_count"] == 2                # June مهتم + May جديد now both in-window
    assert _g(dom, GROUP_NEW) == 1
    assert _g(dom, GROUP_INTERESTED) == 1


async def test_windowed_data_quality_buckets_and_global_identity():
    r = await _windowed(window="current")
    junk = r["data_quality"]["junk_none"]
    nc = r["data_quality"]["no_campaign"]
    assert junk["label"] == "None" and junk["lead_count"] == 1
    assert nc["label"] == "(no campaign)" and nc["lead_count"] == 1
    # listed (3+1) + junk (1) + no_campaign (1) == windowed population (6)
    listed = sum(c["lead_count"] for c in r["campaigns"])
    assert listed + junk["lead_count"] + nc["lead_count"] == r["total_leads_population"] == 6


async def test_junk_none_never_a_list_row():
    r = await _windowed(window="current")
    assert all(c["campaign_name"] != "None" for c in r["campaigns"])


# ── Buyer cell uses the ALL-TIME both-set status ──────────────────────────────


async def test_buyer_cell_is_all_time_status():
    r = await _windowed(window="current")
    fb = _row(r, 1)
    assert fb["attribution_status"] == "confirmed"
    assert fb["media_buyer_id"] == 101
    assert fb["concentration"] == 100.0
    assert fb["both_set_count"] == 100           # ALL-TIME, not the 3 windowed leads

    dom = _row(r, 2)
    assert dom["attribution_status"] == "dominant"
    assert dom["concentration"] == pytest.approx(75.0, abs=0.01)
    assert dom["both_set_count"] == 80


# ── Custom range overrides the preset ─────────────────────────────────────────


async def test_custom_range_overrides_preset():
    r = await _windowed(window="current", start_month="2026-04", end_month="2026-06")
    assert r["is_custom_range"] is True
    assert r["window"] == "custom"
    assert r["window_start_month"] == "2026-04"
    assert r["window_end_month"] == "2026-06"
    assert r["window_months"] == 3
    # the May DOM lead is in-window under the custom span
    assert _row(r, 2)["lead_count"] == 2


# ── Validation (before any RPC) ───────────────────────────────────────────────


async def test_invalid_window_raises():
    with pytest.raises(InvalidTimelineRangeError):
        await _windowed(window="bogus")


async def test_invalid_custom_range_raises():
    with pytest.raises(InvalidTimelineRangeError):
        await _windowed(start_month="2026-06", end_month="2026-01")   # start > end


async def test_partial_custom_range_raises():
    with pytest.raises(InvalidTimelineRangeError):
        await _windowed(start_month="2026-01")                        # both-or-neither


# ── Cache / read-only / error handling ────────────────────────────────────────


async def test_override_config_bypasses_cache():
    # now_cairo/legacy/gate overrides => default_config False => never cached.
    first = await _windowed(window="current")
    second = await _windowed(window="current")
    assert first["cache_status"] == "fresh"
    assert second["cache_status"] == "fresh"


async def test_default_config_caches():
    # No gate/legacy/now overrides => default_config True => the result is cached.
    # (Date-independent: only the cache_status transition is asserted. The mock's
    # tiny lead set never reaches LEGACY_DAY_MIN, so legacy detection is a no-op.)
    client = _make_client()
    first = await get_campaign_performance_windowed(client=client, window="current")
    second = await get_campaign_performance_windowed(client=client, window="current")
    assert first["cache_status"] == "fresh"
    assert second["cache_status"] == "cached"
    assert second["rpc_duration_ms"] == 0


async def test_read_only_violation_aborts_before_rpc():
    with patch(
        "backend.modules.campaign_performance.services.campaign_service.ALLOWED_METHODS",
        frozenset({"search_read", "read_group", "write"}),
    ):
        with pytest.raises(ReadOnlyViolationError):
            await _windowed(window="current")


async def test_odoo_failure_raises_odoo_query_error():
    client = MagicMock()
    client.execute_kw = AsyncMock(side_effect=Exception("connection refused"))
    client.close = AsyncMock()
    with pytest.raises(OdooQueryError):
        await get_campaign_performance_windowed(
            client=client, window="current",
            legacy_days=set(_LEGACY), now_cairo=_NOW,
            confirmed_campaigns=frozenset(), denylist_campaigns=frozenset(),
        )
