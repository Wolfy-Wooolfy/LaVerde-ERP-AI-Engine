"""
Unit tests for the Campaign Performance per-campaign TIMELINE service (Level 2).

OdooClient is fully mocked via a dispatch keyed on (model, method, domain/groupby)
— order-independent. No live Odoo. legacy_days= and now_cairo= are injected for
determinism (and so the default-config cache is never poisoned by a test gate).

Covers:
  - Cairo-local month bucketing (a 2025-12-31 22:30 UTC lead lands in 2026-01)
  - per-month reconciliation (4 groups sum to lead_count, GROUP_ORDER, pct≈100)
  - window total == header.total_leads_in_window
  - migration excluded (seeded legacy-day leads absent from every period/trend/
    header; legacy_days_excluded echoes them)
  - trend (exactly trend_months points oldest→newest, 0-filled, independent of the
    smaller funnel window)
  - maturation (recent high-جديد→too_early; old high-جديد→neglected; worked-down→
    normal; empty month→normal)
  - header buyer parity vs derive_buyer_status for all 5 states + confirmed-drift →
    downgrade + one integrity_alert
  - empty window month → all-zero outcomes, reconciles, normal
  - read-only abort, OdooQueryError, 404 (unknown campaign), cache hit, override
    bypasses cache

Live verification: scripts/verify_campaign_performance_timeline_live.py.
"""

from collections import Counter
from datetime import datetime, timezone
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
from backend.modules.campaign_performance.services.buyer import derive_buyer_status
from backend.modules.campaign_performance.services.timeline_service import (
    CampaignNotFoundError,
    get_campaign_timeline,
)

_CAIRO = ZoneInfo("Africa/Cairo")
_NOW = datetime(2026, 6, 16, 12, 0, 0, tzinfo=_CAIRO)   # fixed "now" for the windows

# Stage table → classify_stage groups: 1=New(جديد) 2=Follow up(مهتم)
# 3=Reservation(اشترى, is_won) 4=Lost(بلا نتيجة). stage_id False → جديد.
_STAGES = [
    {"id": 1, "name": "New", "is_won": False},
    {"id": 2, "name": "Follow up", "is_won": False},
    {"id": 3, "name": "Reservation", "is_won": True},
    {"id": 4, "name": "Lost", "is_won": False},
]
_STAGE_NAME = {s["id"]: s["name"] for s in _STAGES}


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def fresh_cache():
    _cache.clear()
    yield
    _cache.clear()


# ── Builders ──────────────────────────────────────────────────────────────────


def _stage_m2o(stage_id):
    return False if stage_id is False else [stage_id, _STAGE_NAME[stage_id]]


def _utc(cairo_dt: datetime) -> str:
    """A naive Cairo-local datetime → the UTC-naive string Odoo would store."""
    return cairo_dt.replace(tzinfo=_CAIRO).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _lead(cairo_dt: datetime, stage_id) -> dict:
    """A search_read lead row from a Cairo-local create time + a stage id (or False)."""
    return {"create_date": _utc(cairo_dt), "stage_id": _stage_m2o(stage_id)}


def _lead_raw(utc_str: str, stage_id) -> dict:
    """A lead row from a literal UTC create_date string (for boundary tests)."""
    return {"create_date": utc_str, "stage_id": _stage_m2o(stage_id)}


def _buyer_row(bid: int, name: str, count: int) -> dict:
    return {"media_buyer_id": [bid, name], "__count": count}


def _make_client(campaigns, stages, windowed_leads, both_set, population=None):
    """Dispatch mock keyed on (model, method, domain/groupby) — order-independent."""

    def _dispatch(model, method, args=None, kwargs=None):
        if model == "utm.campaign" and method == "search_read":
            return campaigns
        if model == "crm.stage" and method == "search_read":
            return stages
        if model == "crm.lead" and method == "search_read":
            dom = args[0]
            if not dom:                              # legacy population scan (domain [])
                return population if population is not None else []
            return windowed_leads                    # the windowed campaign-filtered fetch
        if model == "crm.lead" and method == "read_group":
            if args[2] == ["media_buyer_id"]:
                return both_set
        raise AssertionError(f"unexpected RPC: {model}.{method} args={args}")

    client = MagicMock()
    client.execute_kw = AsyncMock(side_effect=_dispatch)
    client.close = AsyncMock()
    return client


def _period(result: dict, month: str) -> dict:
    return next(p for p in result["periods"] if p["month"] == month)


def _pcount(period: dict, group: str) -> int:
    return next(o["count"] for o in period["outcomes"] if o["group"] == group)


def _trend_point(result: dict, month: str) -> int:
    return next(t["lead_count"] for t in result["trend"] if t["month"] == month)


# A single-campaign timeline with injected legacy_days + now, no gate overrides.
async def _timeline(windowed_leads, both_set=None, *, campaign_id=10,
                    campaign_name="TESTCAMP", confirmed=frozenset(),
                    denylist=frozenset(), legacy_days=frozenset(),
                    window_months=3, now=_NOW):
    client = _make_client(
        [{"id": campaign_id, "name": campaign_name}],
        _STAGES,
        windowed_leads,
        both_set or [],
    )
    return await get_campaign_timeline(
        client=client,
        campaign_id=campaign_id,
        window_months=window_months,
        confirmed_campaigns=confirmed,
        denylist_campaigns=denylist,
        legacy_days=set(legacy_days),
        now_cairo=now,
    )


# ── Cairo bucketing ─────────────────────────────────────────────────────────


async def test_utc_lead_lands_in_next_cairo_month():
    """2025-12-31 22:30 UTC is 2026-01-01 00:30 Cairo (UTC+2) → the 2026-01 month."""
    result = await _timeline(
        [_lead_raw("2025-12-31 22:30:00", 1)],   # New → جديد
        now=datetime(2026, 1, 15, 12, 0, tzinfo=_CAIRO),
        window_months=3,                          # periods: 2025-11, 2025-12, 2026-01
    )
    assert _period(result, "2026-01")["lead_count"] == 1
    assert _pcount(_period(result, "2026-01"), GROUP_NEW) == 1
    assert _period(result, "2025-12")["lead_count"] == 0


# ── Per-month reconciliation ────────────────────────────────────────────────


async def test_per_month_reconciliation_groups_order_and_pct():
    leads = (
        [_lead(datetime(2026, 6, 5, 12), 1)] * 4    # جديد
        + [_lead(datetime(2026, 6, 6, 12), 2)] * 3  # مهتم
        + [_lead(datetime(2026, 6, 7, 12), 3)] * 2  # اشترى
        + [_lead(datetime(2026, 6, 8, 12), 4)] * 1  # بلا نتيجة
    )
    result = await _timeline(leads, window_months=3)
    june = _period(result, "2026-06")
    assert june["lead_count"] == 10
    assert [o["group"] for o in june["outcomes"]] == list(GROUP_ORDER)
    assert sum(o["count"] for o in june["outcomes"]) == june["lead_count"]
    assert sum(o["pct"] for o in june["outcomes"]) == pytest.approx(100.0, abs=0.05)
    assert _pcount(june, GROUP_NEW) == 4
    assert _pcount(june, GROUP_INTERESTED) == 3
    assert _pcount(june, GROUP_WON) == 2
    assert _pcount(june, GROUP_NO_RESULT) == 1


async def test_window_total_equals_header_total():
    leads = (
        [_lead(datetime(2026, 4, 10, 12), 1)] * 2
        + [_lead(datetime(2026, 5, 10, 12), 1)] * 5
        + [_lead(datetime(2026, 6, 10, 12), 2)] * 3
    )
    result = await _timeline(leads, window_months=3)
    assert sum(p["lead_count"] for p in result["periods"]) == 10
    assert result["header"]["total_leads_in_window"] == 10


# ── Migration excluded ──────────────────────────────────────────────────────


async def test_legacy_migration_days_excluded_everywhere():
    """Leads on a seeded legacy Cairo day appear in NO period/trend/header, and the
    detected days are echoed in legacy_days_excluded."""
    leads = (
        [_lead(datetime(2026, 6, 10, 12), 1)] * 10   # on a legacy day → dropped
        + [_lead(datetime(2026, 6, 15, 12), 1)] * 3  # normal → kept
    )
    result = await _timeline(leads, legacy_days={"2026-06-10"}, window_months=3)
    assert _period(result, "2026-06")["lead_count"] == 3        # not 13
    assert _trend_point(result, "2026-06") == 3
    assert result["header"]["total_leads_in_window"] == 3
    assert result["legacy_days_excluded"] == ["2026-06-10"]


# ── Trend ───────────────────────────────────────────────────────────────────


async def test_trend_has_exactly_trend_months_points_independent_of_window():
    leads = (
        [_lead(datetime(2026, 2, 10, 12), 1)] * 2     # in trend, OUTSIDE the 3-mo window
        + [_lead(datetime(2026, 5, 10, 12), 1)] * 4
        + [_lead(datetime(2026, 6, 10, 12), 1)] * 3
    )
    result = await _timeline(leads, window_months=3)
    assert result["trend_months"] == 6
    assert [t["month"] for t in result["trend"]] == [
        "2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06",
    ]
    assert _trend_point(result, "2026-02") == 2       # present in trend
    assert _trend_point(result, "2026-01") == 0       # 0-filled
    # …but 2026-02 is NOT one of the 3 funnel periods.
    assert [p["month"] for p in result["periods"]] == ["2026-04", "2026-05", "2026-06"]
    assert result["window_start_month"] == "2026-04"
    assert result["window_end_month"] == "2026-06"


# ── Maturation ──────────────────────────────────────────────────────────────


async def test_maturation_states():
    leads = (
        [_lead(datetime(2026, 6, 10, 12), 1)] * 10    # age 0, جديد 100% → too_early
        + [_lead(datetime(2026, 5, 10, 12), 1)] * 10  # age 1, جديد 100% → too_early
        + [_lead(datetime(2026, 4, 10, 12), 1)] * 10  # age 2, جديد 100% → neglected
        + [_lead(datetime(2026, 1, 10, 12), 1)] * 10  # age 5, جديد 100% → neglected
        + [_lead(datetime(2026, 3, 10, 12), 1)] * 2   # age 3, worked-down (جديد 20%) …
        + [_lead(datetime(2026, 3, 11, 12), 3)] * 8   #   … 8 اشترى → normal
        # 2026-02 left empty → normal
    )
    result = await _timeline(leads, window_months=6)
    assert _period(result, "2026-06")["maturation_state"] == "too_early"
    assert _period(result, "2026-05")["maturation_state"] == "too_early"
    assert _period(result, "2026-04")["maturation_state"] == "neglected"
    assert _period(result, "2026-01")["maturation_state"] == "neglected"
    assert _period(result, "2026-03")["maturation_state"] == "normal"
    assert _period(result, "2026-02")["maturation_state"] == "normal"
    assert _period(result, "2026-02")["lead_count"] == 0


# ── Empty window month ──────────────────────────────────────────────────────


async def test_empty_window_all_zero_reconciles_normal():
    result = await _timeline([], window_months=3)
    assert result["header"]["total_leads_in_window"] == 0
    for p in result["periods"]:
        assert p["lead_count"] == 0
        assert [o["group"] for o in p["outcomes"]] == list(GROUP_ORDER)
        assert sum(o["count"] for o in p["outcomes"]) == 0
        assert all(o["pct"] == 0.0 for o in p["outcomes"])
        assert p["maturation_state"] == "normal"


# ── Header buyer parity vs derive_buyer_status (5 states + drift) ────────────


async def test_header_buyer_confirmed_matches_helper():
    both = [_buyer_row(101, "Ahmed Aymen", 100)]
    result = await _timeline(
        [], both_set=both, campaign_name="FB-AY",
        confirmed=frozenset({"FB-AY"}),
    )
    expected = derive_buyer_status(
        10, "FB-AY", Counter({101: 100}), {101: "Ahmed Aymen"}, 100,
        is_confirmed=True, is_denylisted=False,
    )
    h = result["header"]
    assert (h["attribution_status"], h["media_buyer_id"], h["media_buyer_name"],
            h["concentration"], h["both_set_count"]) == expected[:5]
    assert h["attribution_status"] == "confirmed"
    assert result["integrity_alerts"] == []


async def test_header_buyer_dominant_matches_helper():
    both = [_buyer_row(201, "Z", 60), _buyer_row(202, "Y", 20)]   # 60/80 = 75%
    result = await _timeline([], both_set=both, campaign_name="DOM")
    expected = derive_buyer_status(
        10, "DOM", Counter({201: 60, 202: 20}), {201: "Z", 202: "Y"}, 80,
        is_confirmed=False, is_denylisted=False,
    )
    h = result["header"]
    assert (h["attribution_status"], h["media_buyer_id"], h["concentration"],
            h["both_set_count"]) == (expected[0], expected[1], expected[3], expected[4])
    assert h["attribution_status"] == "dominant"


async def test_header_buyer_mixed_matches_helper():
    both = [_buyer_row(301, "A", 10), _buyer_row(302, "B", 9), _buyer_row(303, "C", 8)]
    result = await _timeline([], both_set=both, campaign_name="MIX")
    h = result["header"]
    assert h["attribution_status"] == "mixed"
    assert h["media_buyer_id"] is None
    assert h["concentration"] is None
    assert h["both_set_count"] == 27


async def test_header_buyer_no_buyer_matches_helper():
    result = await _timeline([], both_set=[], campaign_name="NOBUY")
    h = result["header"]
    assert h["attribution_status"] == "no_buyer"
    assert h["media_buyer_id"] is None
    assert h["both_set_count"] == 0


async def test_header_buyer_excluded_channel_matches_helper():
    both = [_buyer_row(401, "Mahmoud Mohsen", 70)]
    result = await _timeline(
        [], both_set=both, campaign_name="BV - Daima",
        denylist=frozenset({"BV - Daima"}),
    )
    h = result["header"]
    assert h["attribution_status"] == "excluded_channel"
    assert h["media_buyer_id"] is None        # channel owner suppressed despite 100%
    assert h["concentration"] is None
    assert h["both_set_count"] == 70


async def test_header_confirmed_drift_downgrades_with_one_alert():
    both = [_buyer_row(101, "Ahmed Aymen", 70), _buyer_row(102, "Noise", 30)]  # 70%
    result = await _timeline(
        [], both_set=both, campaign_name="FB-AY",
        confirmed=frozenset({"FB-AY"}),
    )
    h = result["header"]
    assert h["attribution_status"] == "dominant"      # 70% < 90% gate
    assert h["media_buyer_id"] == 101
    assert len(result["integrity_alerts"]) == 1
    assert "70.0%" in result["integrity_alerts"][0]
    assert "FB-AY" in result["integrity_alerts"][0]


# ── Read-only / errors / 404 ────────────────────────────────────────────────


async def test_read_only_violation_aborts_before_rpc():
    client = _make_client([{"id": 10, "name": "TESTCAMP"}], _STAGES, [], [])
    with patch(
        "backend.modules.campaign_performance.services.timeline_service.ALLOWED_METHODS",
        frozenset({"search_read", "read_group", "write"}),
    ):
        with pytest.raises(ReadOnlyViolationError):
            await get_campaign_timeline(client=client, campaign_id=10)


async def test_odoo_failure_raises_odoo_query_error():
    client = MagicMock()
    client.execute_kw = AsyncMock(side_effect=Exception("connection refused"))
    client.close = AsyncMock()
    with pytest.raises(OdooQueryError):
        await get_campaign_timeline(client=client, campaign_id=10)


async def test_unknown_campaign_id_raises_not_found():
    client = _make_client([{"id": 10, "name": "TESTCAMP"}], _STAGES, [], [])
    with pytest.raises(CampaignNotFoundError):
        await get_campaign_timeline(
            client=client, campaign_id=999, legacy_days=set(), now_cairo=_NOW,
        )


# ── Cache ───────────────────────────────────────────────────────────────────


async def test_cache_hit_on_second_default_call():
    # Default config (no gate/legacy/now overrides) → result is cached. A small
    # population (< LEGACY_DAY_MIN/day) means no legacy day is detected.
    population = [_lead(datetime(2026, 6, 10, 12), 1) for _ in range(5)]
    client = _make_client(
        [{"id": 777, "name": "TESTCAMP"}], _STAGES,
        windowed_leads=population, both_set=[], population=population,
    )
    first = await get_campaign_timeline(client=client, campaign_id=777)
    assert first["cache_status"] == "fresh"
    second = await get_campaign_timeline(client=client, campaign_id=777)
    assert second["cache_status"] == "cached"
    assert second["rpc_duration_ms"] == 0


async def test_override_config_bypasses_cache():
    # An injected legacy_days set marks the call as overridden → never cached.
    first = await _timeline([], legacy_days=set())
    assert first["cache_status"] == "fresh"
    second = await _timeline([], legacy_days=set())
    assert second["cache_status"] == "fresh"
