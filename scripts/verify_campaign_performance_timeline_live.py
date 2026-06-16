"""
scripts/verify_campaign_performance_timeline_live.py — Campaign Performance
(Level 2, per-campaign TIMELINE) LIVE verification (READ-ONLY, $0 AI).

Runs on the 4 CONFIRMED campaigns. Proves the period-level (month) timeline:

  §T1 — per-month funnel sums: every period's 4 stage-groups sum to its lead_count
        (0 mismatches across all campaigns × periods).
  §T2 — window total: Σ period lead_count == header.total_leads_in_window.
  §T3 — buyer 1:1 with Level 1: the timeline header (status, buyer id/name,
        concentration, both_set_count) EQUALS that campaign's Level-1 overview row
        EXACTLY (the all-time both-set slice is identical to the shipped view).
  §T4 — migration excluded (months=12 so Nov-2025 is in range): the campaign's
        legacy-day lead count N is > 0, and Σ period volume ==
        search_count(create_date >= window_start_month) − N (legacy leads appear in
        no period/trend).
  §T5 — INDEPENDENT Odoo cross-check: for one non-legacy window month, each group
        count == a direct search_count over (campaign + that Cairo month's UTC
        bounds + an independent group→stage_id domain).
  §T6 — legacy-day detection: the detected legacy days each hold >= LEGACY_DAY_MIN
        leads and NO other Cairo day does (independent full-population re-scan).

ALSO PRINTS, per confirmed campaign, its per-month POST-MIGRATION lead volume for
the last ~12 Cairo months — so the real ongoing flow is visible (it may be modest;
expected, not a bug — discovery §D.1).

Method discipline: READ-ONLY (search_read / read_group / search_count only).
ALLOWED_METHODS untouched. No FastAPI. No OpenAI. AI cost = $0.00.

Pre-flight (run manually BEFORE this script): kill all python; purge all
__pycache__; (uvicorn, if used, WITHOUT --reload). Talks to Odoo directly.

Usage (from project root):
    python scripts/verify_campaign_performance_timeline_live.py
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
    GROUP_INTERESTED,
    GROUP_NEW,
    GROUP_NO_RESULT,
    GROUP_ORDER,
    GROUP_WON,
)
from backend.modules.campaign_performance.services import cache as _cp_cache  # noqa: E402
from backend.modules.campaign_performance.services.campaign_service import (  # noqa: E402
    get_campaign_performance_overview,
)
from backend.modules.campaign_performance.services.timeline_service import (  # noqa: E402
    get_campaign_timeline,
    get_legacy_migration_days,
)
from backend.modules.marketing_attribution import domain as ma_domain  # noqa: E402
from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient  # noqa: E402

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_LEAD = "crm.lead"
_CAMPAIGN = "utm.campaign"
_STAGE = "crm.stage"
_SEP = "=" * 100
_SEP2 = "-" * 100
_CTX_ALL = {"active_test": False}
_CAIRO = ZoneInfo("Africa/Cairo")
_PAGE = 5000
_VERIFY_MONTHS = 12         # months=12 so the Nov-2025 migration is in range (§T4)


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
    """UTC-naive [lo, hi) strings bounding a Cairo-local "YYYY-MM" month."""
    y, m = (int(x) for x in month_str.split("-"))
    lo = datetime(y, m, 1, tzinfo=_CAIRO)
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    hi = datetime(ny, nm, 1, tzinfo=_CAIRO)
    return _cairo_to_utc_str(lo), _cairo_to_utc_str(hi)


def _day_bounds_utc(day_str: str) -> tuple[str, str]:
    """UTC-naive [lo, hi) strings bounding a Cairo-local "YYYY-MM-DD" day."""
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


async def _fetch_all(client, model, dom, fields):
    rows, offset = [], 0
    while True:
        page = await client.execute_kw(
            model, "search_read", args=[dom],
            kwargs={"fields": fields, "order": "id", "limit": _PAGE,
                    "offset": offset, "context": _CTX_ALL},
        )
        rows.extend(page)
        if len(page) < _PAGE:
            break
        offset += _PAGE
    return rows


async def main():
    print(_SEP)
    print("  CAMPAIGN PERFORMANCE (LEVEL 2 — TIMELINE) LIVE VERIFICATION (READ-ONLY, $0)")
    print(f"  ALLOWED_METHODS : {sorted(ALLOWED_METHODS)}")
    print(f"  Population/ctx  : ALL leads incl. archived — context={_CTX_ALL}")
    print(f"  Confirmed cfg   : {sorted(domain.CONFIRMED_BUYER_CAMPAIGNS)}")
    print(f"  Today (Cairo)   : {datetime.now(_CAIRO).date().isoformat()}")
    print(f"  Tunables        : LEGACY_DAY_MIN={domain.LEGACY_DAY_MIN:,}  "
          f"window(verify)={_VERIFY_MONTHS}  trend={domain.DEFAULT_TREND_MONTHS}  "
          f"mat_high={domain.MATURATION_NEW_PCT_HIGH}%")
    print(_SEP)
    print()

    fail_count = 0
    _cp_cache.clear()

    async with OdooClient() as client:
        # ── independent full-population by-day histogram (for §T6) ─────────────
        pop = await _fetch_all(client, _LEAD, [], ["create_date"])
        by_day: Counter = Counter()
        for r in pop:
            cd = r.get("create_date")
            if cd:
                by_day[_to_cairo(cd).strftime("%Y-%m-%d")] += 1
        my_legacy = {d for d, c in by_day.items() if c >= domain.LEGACY_DAY_MIN}
        detected = await get_legacy_migration_days(client)
        legacy_months = {d[:7] for d in detected}

        # ── Level-1 overview (threshold=1 so confirmed rows are present) ───────
        overview = await get_campaign_performance_overview(client=client, min_lead_threshold=1)
        ov_by_id = {c["campaign_id"]: c for c in overview["campaigns"]}

        # ── resolve confirmed campaign names → ids ─────────────────────────────
        campaigns = await client.execute_kw(
            _CAMPAIGN, "search_read", args=[[]],
            kwargs={"fields": ["id", "name"], "context": _CTX_ALL},
        )
        name_to_ids: dict[str, list[int]] = {}
        id_to_name: dict[int, str] = {}
        for c in campaigns:
            cid, cname = int(c["id"]), str(c.get("name") or "")
            id_to_name[cid] = cname
            name_to_ids.setdefault(cname, []).append(cid)
        confirmed_ids = sorted(
            cid for nm in domain.CONFIRMED_BUYER_CAMPAIGNS for cid in name_to_ids.get(nm, [])
        )

        # ── independent stage-group → stage_id buckets (for §T5) ───────────────
        stages = await client.execute_kw(
            _STAGE, "search_read", args=[[]],
            kwargs={"fields": ["id", "name", "is_won"]},
        )
        won_ids, new_ids, intr_ids, nores_ids = [], [], [], []
        for s in stages:
            sid, sname, is_won = int(s["id"]), str(s.get("name") or ""), bool(s.get("is_won"))
            if is_won:
                won_ids.append(sid)
            elif sname in ma_domain.NEW_STAGE_NAMES:
                new_ids.append(sid)
            elif sname in ma_domain.INTERESTED_STAGE_NAMES:
                intr_ids.append(sid)
            else:
                nores_ids.append(sid)
        group_stage_dom = {
            GROUP_NEW: ["|", ("stage_id", "in", new_ids), ("stage_id", "=", False)],
            GROUP_INTERESTED: [("stage_id", "in", intr_ids)],
            GROUP_WON: [("stage_id", "in", won_ids)],
            GROUP_NO_RESULT: [("stage_id", "in", nores_ids)],
        }

        # ── per-campaign timeline checks (§T1–§T5) ─────────────────────────────
        for cid in confirmed_ids:
            cname = id_to_name.get(cid, f"id={cid}")
            print(_SEP)
            print(f"  CAMPAIGN {cname!r} (id={cid})  —  timeline window={_VERIFY_MONTHS} months")
            print(_SEP)
            tl = await get_campaign_timeline(
                client=client, campaign_id=cid, window_months=_VERIFY_MONTHS
            )
            header = tl["header"]

            # §T1 — every period's 4 groups sum to its lead_count
            t1_mismatch = 0
            for p in tl["periods"]:
                if sum(o["count"] for o in p["outcomes"]) != p["lead_count"]:
                    t1_mismatch += 1
                    print(f"     **FAIL** §T1 month {p['month']}: Σgroups != lead_count")
            t1_ok = t1_mismatch == 0
            fail_count += 0 if t1_ok else 1
            print(f"  §T1 per-month funnel sums : periods={len(tl['periods'])} "
                  f"mismatches={t1_mismatch}  {_ok(t1_ok)}")

            # §T2 — Σ period lead_count == header.total_leads_in_window
            window_sum = sum(p["lead_count"] for p in tl["periods"])
            t2_ok = window_sum == header["total_leads_in_window"]
            fail_count += 0 if t2_ok else 1
            print(f"  §T2 window total          : Σperiods={window_sum:,}  "
                  f"header={header['total_leads_in_window']:,}  {_ok(t2_ok)}")

            # §T3 — header buyer 1:1 with the Level-1 overview row
            row = ov_by_id.get(cid)
            if row is None:
                print(f"  §T3 buyer 1:1 vs Level 1  : **FAIL** no Level-1 row for id={cid}")
                fail_count += 1
            else:
                t3_ok = (
                    header["attribution_status"] == row["attribution_status"]
                    and header["media_buyer_id"] == row["media_buyer_id"]
                    and header["media_buyer_name"] == row["media_buyer_name"]
                    and header["concentration"] == row["concentration"]
                    and header["both_set_count"] == row["both_set_count"]
                )
                fail_count += 0 if t3_ok else 1
                print(f"  §T3 buyer 1:1 vs Level 1  : status={header['attribution_status']} "
                      f"buyer={header['media_buyer_name']!r} conc={header['concentration']} "
                      f"both_set={header['both_set_count']:,}  {_ok(t3_ok)}")

            # §T4 — migration excluded over the 12-month window
            lo_utc, _ = _month_bounds_utc(tl["window_start_month"])
            total_since_start = await _count(client, [(CAMPAIGN_FIELD, "=", cid), ("create_date", ">=", lo_utc)])
            legacy_n = 0
            for d in detected:
                d_lo, d_hi = _day_bounds_utc(d)
                legacy_n += await _count(
                    client,
                    [(CAMPAIGN_FIELD, "=", cid), ("create_date", ">=", d_lo), ("create_date", "<", d_hi)],
                )
            t4_ok = (legacy_n > 0) and (window_sum == total_since_start - legacy_n)
            fail_count += 0 if t4_ok else 1
            print(f"  §T4 migration excluded    : legacy N={legacy_n:,}  "
                  f"since_start={total_since_start:,}  Σperiods={window_sum:,}  "
                  f"(since_start−N={total_since_start - legacy_n:,})  {_ok(t4_ok)}")

            # §T5 — independent cross-check on one non-legacy window month
            candidates = [p for p in tl["periods"] if p["month"] not in legacy_months]
            pick = max(candidates, key=lambda p: p["lead_count"], default=None)
            if pick is None or pick["lead_count"] == 0:
                print("  §T5 cross-check           : (no non-legacy month with leads — skipped)")
            else:
                m_lo, m_hi = _month_bounds_utc(pick["month"])
                cp_g = _group_counts(pick["outcomes"])
                t5_ok = True
                print(f"  §T5 cross-check month     : {pick['month']} (lead_count={pick['lead_count']:,})")
                for g in GROUP_ORDER:
                    dom = ([(CAMPAIGN_FIELD, "=", cid),
                            ("create_date", ">=", m_lo), ("create_date", "<", m_hi)]
                           + group_stage_dom[g])
                    odoo_g = await _count(client, dom)
                    g_ok = odoo_g == cp_g.get(g, 0)
                    t5_ok = t5_ok and g_ok
                    print(f"        {g:<22} CP={cp_g.get(g, 0):>7,}  ODOO={odoo_g:>7,}  {_ok(g_ok)}")
                fail_count += 0 if t5_ok else 1

            # ALSO PRINT — per-month post-migration volume (last ~12 months)
            print(f"  per-month post-migration volume (last {len(tl['periods'])} Cairo months):")
            for p in tl["periods"]:
                print(f"        {p['month']}  leads={p['lead_count']:>6,}  "
                      f"maturation={p['maturation_state']}")
            if tl["integrity_alerts"]:
                for a in tl["integrity_alerts"]:
                    print(f"     INTEGRITY ALERT: {a}")
                    fail_count += 1
            print()

        # ── §T6 — legacy-day detection ─────────────────────────────────────────
        print(_SEP)
        print("  §T6 — LEGACY-DAY DETECTION (>= LEGACY_DAY_MIN leads/Cairo-day)")
        print(_SEP)
        print(f"  detected legacy days : {sorted(detected)}")
        print(f"  TOP 8 Cairo days by lead volume:")
        for day, cnt in sorted(by_day.items(), key=lambda kv: -kv[1])[:8]:
            flag = "  <- legacy" if cnt >= domain.LEGACY_DAY_MIN else ""
            print(f"        {day}  {cnt:>8,}{flag}")
        each_big = all(by_day[d] >= domain.LEGACY_DAY_MIN for d in detected)
        no_other = (detected == my_legacy)
        t6_ok = each_big and no_other and bool(detected)
        fail_count += 0 if t6_ok else 1
        print(f"  every detected day >= {domain.LEGACY_DAY_MIN:,}            : {_ok(each_big)}")
        print(f"  no OTHER Cairo day >= {domain.LEGACY_DAY_MIN:,}            : {_ok(no_other)}")
        print(f"  detection matches service get_legacy_migration_days : {_ok(detected == my_legacy)}")
        print()

    print(_SEP)
    if fail_count == 0:
        print("  TIMELINE VERIFICATION COMPLETE — ALL CHECKS (§T1–§T6) PASSED.")
    else:
        print(f"  TIMELINE VERIFICATION COMPLETE — {fail_count} CHECK(S) FAILED/FLAGGED. STOP and report.")
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
