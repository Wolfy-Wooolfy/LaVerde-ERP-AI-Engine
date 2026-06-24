"""
Unit tests for the Marketing Attribution per-MEDIA-BUYER TIMELINE service (Slice 3).

OdooClient is fully mocked via a dispatch keyed on (model, method, domain/groupby)
— order-independent, and the crm.lead search_read is domain-aware (it honours the
`campaign_id in [...]` clause) so we can prove a buyer's funnel draws ONLY from its
attributing campaigns. No live Odoo. legacy_days= and now_cairo= are injected for
determinism (and so the default-config cache is never poisoned by a test gate).

Mirrors test_timeline_service.py (the campaign timeline) and adds the buyer-specific
behaviour:
  - Cairo-local month bucketing (a 2025-12-31 22:30 UTC lead lands in 2026-01)
  - per-month reconciliation (4 groups sum to lead_count, GROUP_ORDER, pct≈100)
  - window total == header.total_leads_in_window
  - migration excluded (seeded legacy-day leads absent everywhere; echoed in
    legacy_days_excluded)
  - trend (exactly trend_months points oldest→newest, 0-filled, independent of the
    smaller funnel window)
  - maturation (recent high-جديد→too_early; old high-جديد→neglected; worked-down→
    normal; empty month→normal)
  - empty window month → all-zero outcomes, reconciles, normal
  - buyer identity: header buyer_id/name + attributing_campaign_count/ids
  - a buyer aggregating across MULTIPLE attributing campaigns
  - leads from non-attributing campaigns EXCLUDED from the funnel
  - BuyerNotFoundError: bad id, an unconfirmed-dominant buyer, a DENYLISTED buyer,
    an unknown buyer — none get a timeline (the >=90% confirmed gate)
  - a confirmed campaign drifting < 90% surfaces an integrity alert on a healthy
    buyer's page
  - read-only abort, OdooQueryError, cache hit, override bypasses cache
  - custom range (identity vs preset, overrides window_months, single month, trend
    ends at end_month, excludes migration) + validation raised BEFORE any RPC

Live verification: scripts/verify_marketing_attribution_buyer_timeline_live.py.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from backend.core.exceptions import OdooQueryError, ReadOnlyViolationError
from backend.modules.campaign_performance.domain import MAX_CUSTOM_SPAN_MONTHS
from backend.modules.campaign_performance.services.timeline_service import (
    InvalidTimelineRangeError,
)
from backend.modules.marketing_attribution.domain import (
    CAMPAIGN_FIELD,
    GROUP_INTERESTED,
    GROUP_NEW,
    GROUP_NO_RESULT,
    GROUP_ORDER,
    GROUP_WON,
)
from backend.modules.marketing_attribution.services import cache as _cache
from backend.modules.marketing_attribution.services.buyer_timeline_service import (
    BuyerNotFoundError,
    get_buyer_timeline,
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

# Campaign table. FB-AY/FB-AY-2 → buyer 101; FB-AM → buyer 102; DOM is dominant but
# unconfirmed; BV - Daima is a denylisted channel owner.
_CAMPAIGNS = [
    {"id": 1, "name": "FB-AY"},
    {"id": 2, "name": "DOM"},
    {"id": 3, "name": "BV - Daima"},
    {"id": 4, "name": "FB-AM"},
    {"id": 5, "name": "FB-AY-2"},
]

# ALL-TIME both-set (campaign→buyer MAP): FB-AY 100% Ahmed, FB-AY-2 100% Ahmed,
# FB-AM 100% Abdallah, DOM 75% Z (unconfirmed), BV - Daima 100% Mahmoud (denylisted).
_BOTH_SET = [
    {"campaign_id": [1, "FB-AY"], "media_buyer_id": [101, "Ahmed Aymen"], "__count": 100},
    {"campaign_id": [5, "FB-AY-2"], "media_buyer_id": [101, "Ahmed Aymen"], "__count": 80},
    {"campaign_id": [4, "FB-AM"], "media_buyer_id": [102, "Abdallah Maher"], "__count": 100},
    {"campaign_id": [2, "DOM"], "media_buyer_id": [201, "Buyer Z"], "__count": 60},
    {"campaign_id": [2, "DOM"], "media_buyer_id": [202, "Other Y"], "__count": 20},
    {"campaign_id": [3, "BV - Daima"], "media_buyer_id": [301, "Mahmoud Mohsen"], "__count": 50},
]


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


def _lead(cairo_dt: datetime, stage_id, campaign_id=1, campaign_name="FB-AY") -> dict:
    """A search_read lead row: Cairo create time + stage id (or False) + campaign."""
    return {
        "create_date": _utc(cairo_dt),
        "campaign_id": [campaign_id, campaign_name],
        "stage_id": _stage_m2o(stage_id),
    }


def _lead_raw(utc_str: str, stage_id, campaign_id=1, campaign_name="FB-AY") -> dict:
    """A lead row from a literal UTC create_date string (for boundary tests)."""
    return {
        "create_date": utc_str,
        "campaign_id": [campaign_id, campaign_name],
        "stage_id": _stage_m2o(stage_id),
    }


def _cid(row: dict):
    v = row.get("campaign_id")
    return int(v[0]) if isinstance(v, (list, tuple)) and len(v) == 2 else None


def _make_client(campaigns, stages, windowed_leads, both_set, population=None):
    """Dispatch mock keyed on (model, method, domain/groupby) — order-independent.

    crm.lead search_read is domain-aware: an empty domain is the legacy population
    scan; a domain with a `campaign_id in [...]` clause returns only matching leads
    (so the service's own scoping is exercised, not assumed)."""

    def _dispatch(model, method, args=None, kwargs=None):
        if model == "utm.campaign" and method == "search_read":
            return campaigns
        if model == "crm.stage" and method == "search_read":
            return stages
        if model == "crm.lead" and method == "read_group":
            return both_set
        if model == "crm.lead" and method == "search_read":
            dom = args[0]
            if not dom:                              # legacy population scan (domain [])
                return population if population is not None else []
            in_ids = None
            for clause in dom:
                if (isinstance(clause, (list, tuple)) and len(clause) == 3
                        and clause[0] == CAMPAIGN_FIELD and clause[1] == "in"):
                    in_ids = set(clause[2])
            rows = windowed_leads
            if in_ids is not None:
                rows = [r for r in rows if _cid(r) in in_ids]
            return rows
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


# A single-buyer timeline with injected legacy_days + now, gate overridden per call.
async def _bt(windowed_leads, both_set=None, *, buyer_id=101, campaigns=None,
              confirmed=frozenset({"FB-AY"}), denylist=frozenset(),
              legacy_days=frozenset(), window_months=3,
              start_month=None, end_month=None, now=_NOW, population=None):
    client = _make_client(
        campaigns if campaigns is not None else _CAMPAIGNS,
        _STAGES,
        windowed_leads,
        both_set if both_set is not None else _BOTH_SET,
        population,
    )
    return await get_buyer_timeline(
        client=client,
        buyer_id=buyer_id,
        window_months=window_months,
        start_month=start_month,
        end_month=end_month,
        confirmed_campaigns=confirmed,
        denylist_campaigns=denylist,
        legacy_days=set(legacy_days),
        now_cairo=now,
    )


# ── Cairo bucketing ─────────────────────────────────────────────────────────


async def test_utc_lead_lands_in_next_cairo_month():
    """2025-12-31 22:30 UTC is 2026-01-01 00:30 Cairo (UTC+2) → the 2026-01 month."""
    result = await _bt(
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
    result = await _bt(leads, window_months=3)
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
    result = await _bt(leads, window_months=3)
    assert sum(p["lead_count"] for p in result["periods"]) == 10
    assert result["header"]["total_leads_in_window"] == 10


# ── Migration excluded ──────────────────────────────────────────────────────


async def test_legacy_migration_days_excluded_everywhere():
    leads = (
        [_lead(datetime(2026, 6, 10, 12), 1)] * 10   # on a legacy day → dropped
        + [_lead(datetime(2026, 6, 15, 12), 1)] * 3  # normal → kept
    )
    result = await _bt(leads, legacy_days={"2026-06-10"}, window_months=3)
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
    result = await _bt(leads, window_months=3)
    assert result["trend_months"] == 6
    assert [t["month"] for t in result["trend"]] == [
        "2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06",
    ]
    assert _trend_point(result, "2026-02") == 2       # present in trend
    assert _trend_point(result, "2026-01") == 0       # 0-filled
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
    result = await _bt(leads, window_months=6)
    assert _period(result, "2026-06")["maturation_state"] == "too_early"
    assert _period(result, "2026-05")["maturation_state"] == "too_early"
    assert _period(result, "2026-04")["maturation_state"] == "neglected"
    assert _period(result, "2026-01")["maturation_state"] == "neglected"
    assert _period(result, "2026-03")["maturation_state"] == "normal"
    assert _period(result, "2026-02")["maturation_state"] == "normal"
    assert _period(result, "2026-02")["lead_count"] == 0


# ── Empty window month ──────────────────────────────────────────────────────


async def test_empty_window_all_zero_reconciles_normal():
    result = await _bt([], window_months=3)
    assert result["header"]["total_leads_in_window"] == 0
    for p in result["periods"]:
        assert p["lead_count"] == 0
        assert [o["group"] for o in p["outcomes"]] == list(GROUP_ORDER)
        assert sum(o["count"] for o in p["outcomes"]) == 0
        assert all(o["pct"] == 0.0 for o in p["outcomes"])
        assert p["maturation_state"] == "normal"


# ── Buyer identity ──────────────────────────────────────────────────────────


async def test_header_buyer_identity_single_campaign():
    result = await _bt([_lead(datetime(2026, 6, 10, 12), 1)] * 3, window_months=3)
    h = result["header"]
    assert h["buyer_id"] == 101
    assert h["buyer_name"] == "Ahmed Aymen"
    assert h["attributing_campaign_count"] == 1
    assert h["attributing_campaign_ids"] == [1]


async def test_buyer_aggregates_across_multiple_attributing_campaigns():
    """When two confirmed campaigns share a dominant buyer, the funnel pools both."""
    leads = (
        [_lead(datetime(2026, 6, 10, 12), 1, campaign_id=1, campaign_name="FB-AY")] * 4
        + [_lead(datetime(2026, 6, 11, 12), 3, campaign_id=5, campaign_name="FB-AY-2")] * 6
    )
    result = await _bt(leads, confirmed=frozenset({"FB-AY", "FB-AY-2"}), window_months=3)
    h = result["header"]
    assert h["attributing_campaign_count"] == 2
    assert h["attributing_campaign_ids"] == [1, 5]
    june = _period(result, "2026-06")
    assert june["lead_count"] == 10                     # 4 (camp 1) + 6 (camp 5)
    assert _pcount(june, GROUP_NEW) == 4
    assert _pcount(june, GROUP_WON) == 6


async def test_leads_from_non_attributing_campaigns_excluded():
    """Only confirmed FB-AY (id 1) attributes to buyer 101; the FB-AY-2 (id 5) leads
    are NOT in this buyer's attributing set this run, so they never reach the funnel."""
    leads = (
        [_lead(datetime(2026, 6, 10, 12), 1, campaign_id=1, campaign_name="FB-AY")] * 3
        + [_lead(datetime(2026, 6, 11, 12), 1, campaign_id=5, campaign_name="FB-AY-2")] * 7
    )
    result = await _bt(leads, confirmed=frozenset({"FB-AY"}), window_months=3)
    assert result["header"]["attributing_campaign_ids"] == [1]
    assert _period(result, "2026-06")["lead_count"] == 3     # not 10


# ── BuyerNotFound (the >=90% confirmed gate) ─────────────────────────────────


async def test_bad_buyer_id_raises_not_found():
    for bad in (0, -5, None):
        with pytest.raises(BuyerNotFoundError):
            await _bt([], buyer_id=bad)


async def test_unconfirmed_dominant_buyer_has_no_timeline():
    """Buyer 201 is DOM's dominant buyer but DOM is not confirmed → 404."""
    with pytest.raises(BuyerNotFoundError):
        await _bt([], buyer_id=201, confirmed=frozenset({"FB-AY"}))


async def test_denylisted_buyer_never_appears():
    """Even if BV - Daima were 'confirmed', the denylist suppresses it → buyer 301
    (Mahmoud Mohsen, a channel owner) gets no timeline."""
    with pytest.raises(BuyerNotFoundError):
        await _bt(
            [], buyer_id=301,
            confirmed=frozenset({"BV - Daima"}),
            denylist=frozenset({"BV - Daima"}),
        )


async def test_unknown_buyer_id_raises_not_found():
    with pytest.raises(BuyerNotFoundError):
        await _bt([], buyer_id=999999)


async def test_confirmed_drift_below_gate_removes_buyer_timeline():
    """FB-AY's dominant buyer drifts to 70% < 90% → no attributing campaign → 404."""
    drifted = [
        {"campaign_id": [1, "FB-AY"], "media_buyer_id": [101, "Ahmed Aymen"], "__count": 70},
        {"campaign_id": [1, "FB-AY"], "media_buyer_id": [109, "Noise"], "__count": 30},
    ]
    with pytest.raises(BuyerNotFoundError):
        await _bt([], buyer_id=101, both_set=drifted, confirmed=frozenset({"FB-AY"}))


async def test_other_campaign_drift_surfaces_integrity_alert_on_healthy_buyer():
    """Buyer 101 (FB-AY 100%) still attributes; a SEPARATE confirmed campaign (FB-AM)
    drifting < 90% surfaces its integrity alert on the page (global gate, like the
    dashboard)."""
    both = [
        {"campaign_id": [1, "FB-AY"], "media_buyer_id": [101, "Ahmed Aymen"], "__count": 100},
        {"campaign_id": [4, "FB-AM"], "media_buyer_id": [102, "Abdallah Maher"], "__count": 70},
        {"campaign_id": [4, "FB-AM"], "media_buyer_id": [108, "Noise"], "__count": 30},
    ]
    result = await _bt(
        [_lead(datetime(2026, 6, 10, 12), 1)] * 2,
        both_set=both, buyer_id=101,
        confirmed=frozenset({"FB-AY", "FB-AM"}),
    )
    assert result["header"]["buyer_id"] == 101
    assert len(result["integrity_alerts"]) == 1
    assert "FB-AM" in result["integrity_alerts"][0]


# ── Read-only / errors ───────────────────────────────────────────────────────


async def test_read_only_violation_aborts_before_rpc():
    client = _make_client(_CAMPAIGNS, _STAGES, [], _BOTH_SET)
    with patch(
        "backend.modules.marketing_attribution.services.buyer_timeline_service.ALLOWED_METHODS",
        frozenset({"search_read", "read_group", "write"}),
    ):
        with pytest.raises(ReadOnlyViolationError):
            await get_buyer_timeline(client=client, buyer_id=101)


async def test_odoo_failure_raises_odoo_query_error():
    client = MagicMock()
    client.execute_kw = AsyncMock(side_effect=Exception("connection refused"))
    client.close = AsyncMock()
    with pytest.raises(OdooQueryError):
        await get_buyer_timeline(client=client, buyer_id=101)


# ── Cache ───────────────────────────────────────────────────────────────────


async def test_cache_hit_on_second_default_call():
    # Default config (no gate/legacy/now overrides) → result is cached. FB-AY is in
    # the real default confirmed set; a small population (< LEGACY_DAY_MIN/day) means
    # no legacy day is detected.
    population = [_lead(datetime(2026, 6, 10, 12), 1) for _ in range(5)]
    both = [{"campaign_id": [1, "FB-AY"], "media_buyer_id": [101, "Ahmed Aymen"], "__count": 100}]
    client = _make_client(
        [{"id": 1, "name": "FB-AY"}], _STAGES,
        windowed_leads=population, both_set=both, population=population,
    )
    first = await get_buyer_timeline(client=client, buyer_id=101)
    assert first["cache_status"] == "fresh"
    second = await get_buyer_timeline(client=client, buyer_id=101)
    assert second["cache_status"] == "cached"
    assert second["rpc_duration_ms"] == 0


async def test_override_config_bypasses_cache():
    # An injected legacy_days set marks the call as overridden → never cached.
    first = await _bt([], legacy_days=set())
    assert first["cache_status"] == "fresh"
    second = await _bt([], legacy_days=set())
    assert second["cache_status"] == "fresh"


# ── Custom range (explicit start_month..end_month) ──────────────────────────

_NON_IDENTITY_KEYS = {"as_of", "rpc_duration_ms", "cache_status", "is_custom_range"}


async def test_custom_range_byte_identical_to_equivalent_preset():
    """A custom range equal to a preset's window reproduces it EXACTLY (modulo the
    runtime/flag fields). The trend ends at end_month == current month, so even the
    fixed 6-bar trend matches."""
    leads = (
        [_lead(datetime(2026, 2, 10, 12), 1)] * 2      # trend-only context month
        + [_lead(datetime(2026, 4, 10, 12), 1)] * 3
        + [_lead(datetime(2026, 5, 10, 12), 2)] * 4
        + [_lead(datetime(2026, 6, 10, 12), 3)] * 5
    )
    preset = await _bt(leads, window_months=3)                 # [2026-04,05,06]
    custom = await _bt(leads, start_month="2026-04", end_month="2026-06")

    assert preset["is_custom_range"] is False
    assert custom["is_custom_range"] is True
    assert custom["window_months"] == 3                        # derived count
    stripped_preset = {k: v for k, v in preset.items() if k not in _NON_IDENTITY_KEYS}
    stripped_custom = {k: v for k, v in custom.items() if k not in _NON_IDENTITY_KEYS}
    assert stripped_custom == stripped_preset                  # byte-identical


async def test_custom_range_overrides_window_months_and_derives_count():
    leads = [_lead(datetime(2026, 3, 10, 12), 1)] * 2
    result = await _bt(
        leads, window_months=3, start_month="2026-01", end_month="2026-06",
    )
    assert result["is_custom_range"] is True
    assert [p["month"] for p in result["periods"]] == [
        "2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06",
    ]
    assert result["window_months"] == 6
    assert result["window_start_month"] == "2026-01"
    assert result["window_end_month"] == "2026-06"


async def test_custom_single_month():
    leads = (
        [_lead(datetime(2026, 3, 5, 12), 1)] * 4       # جديد
        + [_lead(datetime(2026, 3, 6, 12), 3)] * 1     # اشترى
    )
    result = await _bt(leads, start_month="2026-03", end_month="2026-03")
    assert result["is_custom_range"] is True
    assert [p["month"] for p in result["periods"]] == ["2026-03"]
    assert result["window_months"] == 1
    march = _period(result, "2026-03")
    assert march["lead_count"] == 5
    assert sum(o["count"] for o in march["outcomes"]) == 5
    assert [t["month"] for t in result["trend"]] == [
        "2025-10", "2025-11", "2025-12", "2026-01", "2026-02", "2026-03",
    ]
    assert _trend_point(result, "2026-03") == 5


async def test_custom_trend_ends_at_end_month_not_current():
    leads = [_lead(datetime(2025, 12, 10, 12), 1)] * 3
    result = await _bt(leads, start_month="2025-12", end_month="2025-12")
    assert result["trend"][-1]["month"] == "2025-12"     # ends at end_month
    assert all(t["month"] <= "2025-12" for t in result["trend"])


async def test_custom_range_excludes_migration_inside_window():
    leads = (
        [_lead(datetime(2025, 11, 15, 12), 1)] * 10    # legacy day → dropped
        + [_lead(datetime(2025, 11, 20, 12), 1)] * 4   # normal Nov lead → kept
        + [_lead(datetime(2025, 12, 10, 12), 1)] * 6
    )
    result = await _bt(
        leads, legacy_days={"2025-11-15"},
        start_month="2025-11", end_month="2025-12",
    )
    assert _period(result, "2025-11")["lead_count"] == 4    # not 14
    assert _period(result, "2025-12")["lead_count"] == 6
    assert result["header"]["total_leads_in_window"] == 10
    assert result["legacy_days_excluded"] == ["2025-11-15"]


# ── Custom range validation (raised BEFORE any RPC) ─────────────────────────


async def test_custom_partial_range_raises():
    with pytest.raises(InvalidTimelineRangeError):
        await _bt([], start_month="2026-01", end_month=None)
    with pytest.raises(InvalidTimelineRangeError):
        await _bt([], start_month=None, end_month="2026-01")


async def test_custom_malformed_range_raises():
    for bad in [("2026-13", "2026-01"), ("2026-01", "nope"), ("2026/01", "2026-03")]:
        with pytest.raises(InvalidTimelineRangeError):
            await _bt([], start_month=bad[0], end_month=bad[1])


async def test_custom_start_after_end_raises():
    with pytest.raises(InvalidTimelineRangeError):
        await _bt([], start_month="2026-06", end_month="2026-01")


def _add_months(month_str: str, n: int) -> str:
    """'YYYY-MM' + n months → 'YYYY-MM' (test-local, no service import)."""
    y, m = (int(x) for x in month_str.split("-"))
    total = y * 12 + (m - 1) + n
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


async def test_custom_span_at_cap_ok_one_over_raises():
    start = "2024-01"
    end_ok = _add_months(start, MAX_CUSTOM_SPAN_MONTHS - 1)   # inclusive span == MAX
    end_bad = _add_months(start, MAX_CUSTOM_SPAN_MONTHS)      # span == MAX + 1
    res = await _bt(
        [], start_month=start, end_month=end_ok,
        now=datetime(2026, 6, 16, 12, 0, tzinfo=_CAIRO),
    )
    assert res["window_months"] == MAX_CUSTOM_SPAN_MONTHS
    with pytest.raises(InvalidTimelineRangeError):
        await _bt([], start_month=start, end_month=end_bad)
