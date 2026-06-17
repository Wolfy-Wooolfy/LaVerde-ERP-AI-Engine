"""
scripts/verify_campaign_performance_windowed_live.py — Campaign Performance
(Level 1, WINDOWED list) LIVE verification (READ-ONLY, $0 AI).

Proves the windowed campaign list scopes correctly and reconciles against the
already-verified per-campaign timeline:

  §W1 — WINDOWED == TIMELINE (current month): for window="current", each listed
        campaign's windowed funnel EQUALS that campaign's timeline current-month
        period (window_months=1) — lead_count + all 4 group counts, 1:1 — and the
        windowed total equals Σ of those timeline periods. (The timeline is itself
        independently cross-checked against direct search_count in
        verify_campaign_performance_timeline_live.py §T5/§T8.)
  §W2 — GLOBAL windowed identity: listed + junk 'None' + no-campaign == windowed
        population; and that population == an INDEPENDENT Odoo count of the current
        Cairo month (UTC month bounds) minus any legacy-day leads.
  §W3 — ALL-TIME regression: the shipped un-windowed overview still reconciles
        (listed + long_tail + junk + no_campaign == population) AND its population
        equals an independent full-population search_count (migration INCLUDED) —
        i.e. "All-time" is byte-for-byte the shipped behaviour, no regression.
  §W4 — CUSTOM range crossing Nov-2025 EXCLUDES the migration: for 2025-10..2026-01
        (crosses the migration AND a DST boundary), each Cairo month reconciles
        against a direct search_count minus its legacy-day leads, the detected
        legacy days are reported as excluded, and legacy_n > 0 is actually removed.

Method discipline: READ-ONLY (search_read / read_group / search_count only).
ALLOWED_METHODS untouched. No FastAPI. No OpenAI. AI cost = $0.00.

Pre-flight (run manually BEFORE this script): kill all python; purge all
__pycache__; (uvicorn, if used, WITHOUT --reload). Talks to Odoo directly.

Usage (from project root):
    python scripts/verify_campaign_performance_windowed_live.py
"""

import asyncio
import io
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.modules.campaign_performance import domain  # noqa: E402
from backend.modules.campaign_performance.domain import (  # noqa: E402
    CAMPAIGN_FIELD,
    GROUP_ORDER,
)
from backend.modules.campaign_performance.services import cache as _cp_cache  # noqa: E402
from backend.modules.campaign_performance.services.campaign_service import (  # noqa: E402
    get_campaign_performance_overview,
    get_campaign_performance_windowed,
)
from backend.modules.campaign_performance.services.timeline_service import (  # noqa: E402
    get_campaign_timeline,
    get_legacy_migration_days,
)
from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient  # noqa: E402

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_LEAD = "crm.lead"
_SEP = "=" * 100
_SEP2 = "-" * 100
_CTX_ALL = {"active_test": False}
_CAIRO = ZoneInfo("Africa/Cairo")
_PAGE = 5000


def _ok(flag: bool) -> str:
    return "PASS" if flag else "**FAIL**"


def _to_cairo(dt_str) -> datetime:
    return (
        datetime.strptime(str(dt_str), "%Y-%m-%d %H:%M:%S")
        .replace(tzinfo=timezone.utc)
        .astimezone(_CAIRO)
    )


def _cairo_to_utc_str(cairo_dt: datetime) -> str:
    return cairo_dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _month_bounds_utc(month_str: str) -> tuple[str, str]:
    y, m = (int(x) for x in month_str.split("-"))
    lo = datetime(y, m, 1, tzinfo=_CAIRO)
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    hi = datetime(ny, nm, 1, tzinfo=_CAIRO)
    return _cairo_to_utc_str(lo), _cairo_to_utc_str(hi)


def _day_bounds_utc(day_str: str) -> tuple[str, str]:
    d = datetime.strptime(day_str, "%Y-%m-%d").date()
    nxt = d + timedelta(days=1)
    lo = datetime(d.year, d.month, d.day, tzinfo=_CAIRO)
    hi = datetime(nxt.year, nxt.month, nxt.day, tzinfo=_CAIRO)
    return _cairo_to_utc_str(lo), _cairo_to_utc_str(hi)


def _group_counts(outcomes: list[dict]) -> dict[str, int]:
    return {o["group"]: o["count"] for o in outcomes}


async def _count(client, dom):
    return await client.execute_kw(
        _LEAD, "search_count", args=[dom], kwargs={"context": _CTX_ALL}
    )


async def _legacy_in_month(client, detected, month, campaign_id=None):
    """Σ legacy-day leads that fall in `month` (optionally for one campaign)."""
    n = 0
    for d in detected:
        if d[:7] != month:
            continue
        d_lo, d_hi = _day_bounds_utc(d)
        dom = [("create_date", ">=", d_lo), ("create_date", "<", d_hi)]
        if campaign_id is not None:
            dom = [(CAMPAIGN_FIELD, "=", campaign_id)] + dom
        n += await _count(client, dom)
    return n


async def main():
    now_cairo = datetime.now(_CAIRO)
    current_month = now_cairo.strftime("%Y-%m")

    print(_SEP)
    print("  CAMPAIGN PERFORMANCE (LEVEL 1 — WINDOWED LIST) LIVE VERIFICATION (READ-ONLY, $0)")
    print(f"  ALLOWED_METHODS : {sorted(ALLOWED_METHODS)}")
    print(f"  Population/ctx  : ALL leads incl. archived — context={_CTX_ALL}")
    print(f"  Today (Cairo)   : {now_cairo.date().isoformat()}  current_month={current_month}")
    print(f"  Default window  : {domain.DEFAULT_WINDOW}  presets={list(domain.WINDOW_PRESETS)}")
    print(f"  Tunables        : LEGACY_DAY_MIN={domain.LEGACY_DAY_MIN:,}")
    print(_SEP)
    print()

    fail_count = 0
    _cp_cache.clear()

    async with OdooClient() as client:
        detected = await get_legacy_migration_days(client)
        legacy_months = {d[:7] for d in detected}

        # ══════════════════════════════════════════════════════════════════════
        # §W1 — WINDOWED (current month) == per-campaign TIMELINE current-month
        # ══════════════════════════════════════════════════════════════════════
        print(_SEP)
        print("  §W1 — WINDOWED current-month list == Σ each campaign's timeline current-month period")
        print(_SEP)
        win = await get_campaign_performance_windowed(client=client, window="current")
        print(f"  window={win['window']} [{win['window_start_month']}..{win['window_end_month']}]  "
              f"active campaigns={win['active_campaign_count']}  windowed_leads={win['total_leads_population']:,}")
        w1_ok = True
        sum_windowed, sum_timeline = 0, 0
        for c in win["campaigns"]:
            cid = c["campaign_id"]
            tl = await get_campaign_timeline(client=client, campaign_id=cid, window_months=1)
            # window_months=1 → exactly the current Cairo month as the single period
            period = next((p for p in tl["periods"] if p["month"] == current_month), None)
            tl_total = period["lead_count"] if period else 0
            sum_windowed += c["lead_count"]
            sum_timeline += tl_total
            wc = _group_counts(c["outcomes"])
            tc = _group_counts(period["outcomes"]) if period else {g: 0 for g in GROUP_ORDER}
            row_ok = (c["lead_count"] == tl_total) and all(wc.get(g, 0) == tc.get(g, 0) for g in GROUP_ORDER)
            w1_ok = w1_ok and row_ok
            print(f"     {c['campaign_name'][:30]:<31} WIN={c['lead_count']:>6,}  TL={tl_total:>6,}  {_ok(row_ok)}")
        sum_ok = sum_windowed == sum_timeline
        w1_ok = w1_ok and sum_ok
        fail_count += 0 if w1_ok else 1
        print(f"  Σ windowed={sum_windowed:,}  Σ timeline(current)={sum_timeline:,}  {_ok(sum_ok)}")
        print(f"  §W1 windowed == timeline (per-campaign + total)            {_ok(w1_ok)}")
        print()

        # ══════════════════════════════════════════════════════════════════════
        # §W2 — GLOBAL windowed identity + independent Odoo cross-check
        # ══════════════════════════════════════════════════════════════════════
        print(_SEP)
        print("  §W2 — GLOBAL windowed identity (listed + junk + no-campaign == windowed population)")
        print(_SEP)
        listed = sum(c["lead_count"] for c in win["campaigns"])
        dq = win["data_quality"]
        junk = dq["junk_none"]["lead_count"] if dq["junk_none"] else 0
        ncamp = dq["no_campaign"]["lead_count"] if dq["no_campaign"] else 0
        recon = listed + junk + ncamp
        recon_ok = recon == win["total_leads_population"]
        # independent: current-month UTC bounds count minus legacy-in-month
        m_lo, m_hi = _month_bounds_utc(current_month)
        raw_month = await _count(client, [("create_date", ">=", m_lo), ("create_date", "<", m_hi)])
        legacy_month = await _legacy_in_month(client, detected, current_month)
        indep_ok = win["total_leads_population"] == raw_month - legacy_month
        fail_count += 0 if (recon_ok and indep_ok) else 1
        print(f"  listed={listed:,}  junk_None={junk:,}  no_campaign={ncamp:,}")
        print(f"  Σ={recon:,}  ==  windowed population {win['total_leads_population']:,}        {_ok(recon_ok)}")
        print(f"  independent Odoo: raw_month={raw_month:,} − legacy={legacy_month:,} = "
              f"{raw_month - legacy_month:,}  ==  population              {_ok(indep_ok)}")
        print()

        # ══════════════════════════════════════════════════════════════════════
        # §W3 — ALL-TIME overview unchanged (no regression)
        # ══════════════════════════════════════════════════════════════════════
        print(_SEP)
        print("  §W3 — ALL-TIME overview regression (shipped un-windowed path, migration INCLUDED)")
        print(_SEP)
        ov = await get_campaign_performance_overview(client=client)
        ov_listed = sum(c["lead_count"] for c in ov["campaigns"])
        ov_tail = ov["long_tail"]["lead_count"] if ov["long_tail"] else 0
        ov_junk = ov["data_quality"]["junk_none"]["lead_count"] if ov["data_quality"]["junk_none"] else 0
        ov_nc = ov["data_quality"]["no_campaign"]["lead_count"] if ov["data_quality"]["no_campaign"] else 0
        ov_recon = ov_listed + ov_tail + ov_junk + ov_nc
        ov_recon_ok = ov_recon == ov["total_leads_population"]
        full_pop = await _count(client, [])     # ALL leads incl. archived + migration
        full_ok = ov["total_leads_population"] == full_pop
        fail_count += 0 if (ov_recon_ok and full_ok) else 1
        print(f"  listed={ov_listed:,}  long_tail={ov_tail:,}  junk={ov_junk:,}  no_campaign={ov_nc:,}")
        print(f"  Σ={ov_recon:,}  ==  population {ov['total_leads_population']:,}              {_ok(ov_recon_ok)}")
        print(f"  population {ov['total_leads_population']:,}  ==  independent full count {full_pop:,} "
              f"(migration INCLUDED)  {_ok(full_ok)}")
        print(f"  listed_campaign_count={ov['listed_campaign_count']:,}  "
              f"total_campaigns_with_leads={ov['total_campaigns_with_leads']:,}  threshold={ov['min_lead_threshold']}")
        print()

        # ══════════════════════════════════════════════════════════════════════
        # §W4 — CUSTOM range crossing Nov-2025 excludes the migration
        # ══════════════════════════════════════════════════════════════════════
        print(_SEP)
        print("  §W4 — CUSTOM range 2025-10..2026-01 (crosses Nov-2025 migration + DST) excludes legacy")
        print(_SEP)
        cust = await get_campaign_performance_windowed(
            client=client, start_month="2025-10", end_month="2026-01"
        )
        legacy_in_range = sorted(d for d in detected if "2025-10" <= d[:7] <= "2026-01")
        print(f"  is_custom_range={cust['is_custom_range']}  window=[{cust['window_start_month']}.."
              f"{cust['window_end_month']}]  windowed_leads={cust['total_leads_population']:,}")
        print(f"  legacy days in range (excluded): {legacy_in_range}")
        print(f"  service legacy_days_excluded ⊇ in-range : "
              f"{_ok(set(legacy_in_range).issubset(set(cust['legacy_days_excluded'])))}")

        # reconcile the whole window: Σ over months of (raw_month − legacy_month) == population
        months = []
        cy, cm = 2025, 10
        for _ in range(4):
            months.append(f"{cy:04d}-{cm:02d}")
            cm += 1
            if cm == 13:
                cy, cm = cy + 1, 1
        expected_total, total_legacy = 0, 0
        for mo in months:
            mlo, mhi = _month_bounds_utc(mo)
            raw = await _count(client, [("create_date", ">=", mlo), ("create_date", "<", mhi)])
            leg = await _legacy_in_month(client, detected, mo)
            expected_total += raw - leg
            total_legacy += leg
            flag = "  <-legacy-excl" if leg else ""
            print(f"     {mo}  raw={raw:>7,}  legacy={leg:>7,}  net={raw - leg:>7,}{flag}")
        w4_recon_ok = expected_total == cust["total_leads_population"]
        w4_legacy_removed = total_legacy > 0
        # global identity for the custom window too
        c_listed = sum(c["lead_count"] for c in cust["campaigns"])
        c_junk = cust["data_quality"]["junk_none"]["lead_count"] if cust["data_quality"]["junk_none"] else 0
        c_nc = cust["data_quality"]["no_campaign"]["lead_count"] if cust["data_quality"]["no_campaign"] else 0
        c_ident_ok = (c_listed + c_junk + c_nc) == cust["total_leads_population"]
        w4_ok = w4_recon_ok and w4_legacy_removed and c_ident_ok
        fail_count += 0 if w4_ok else 1
        print(f"  Σ net(raw−legacy)={expected_total:,}  ==  windowed population "
              f"{cust['total_leads_population']:,}   {_ok(w4_recon_ok)}")
        print(f"  legacy actually removed (Σ legacy in range > 0): {total_legacy:,}  {_ok(w4_legacy_removed)}")
        print(f"  custom-window identity (listed+junk+nocamp==pop): {_ok(c_ident_ok)}")
        print(f"  §W4 custom range excludes migration                        {_ok(w4_ok)}")
        if cust["integrity_alerts"]:
            for a in cust["integrity_alerts"]:
                print(f"     INTEGRITY ALERT: {a}")
        print()

    print(_SEP)
    if fail_count == 0:
        print("  WINDOWED VERIFICATION COMPLETE — ALL CHECKS (§W1–§W4) PASSED.")
    else:
        print(f"  WINDOWED VERIFICATION COMPLETE — {fail_count} CHECK(S) FAILED/FLAGGED. STOP and report.")
    print(_SEP)
    return 1 if fail_count else 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except SystemExit:
        raise
    except Exception as exc:
        print(f"\n  FATAL ERROR: {exc}")
        import traceback
        traceback.print_exc()
        raise SystemExit(1)
