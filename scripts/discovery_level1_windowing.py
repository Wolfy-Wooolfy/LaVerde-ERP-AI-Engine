"""
scripts/discovery_level1_windowing.py — READ-ONLY discovery for Level-1 LIST
windowing (all campaigns / all buyers by time period). $0 AI, no OpenAI, no FastAPI.

WHY
The Level-1 campaign list (and the media-buyer list) show ALL-TIME totals per
entity. There is no way to see ALL campaigns/buyers for a chosen Cairo period
(e.g. the current month) without drilling into each one's timeline. This script
ONLY scopes the feature with real numbers — it changes nothing.

Produces:
  1. List scale            — total campaigns, with-leads, real, listed@threshold,
                             long tail, junk/no-campaign, gated (confirmed/denylist).
  2. Active-this-period    — # campaigns with >=1 POST-MIGRATION Cairo lead in
                             (a) current month, (b) last 3 months, (c) all-time —
                             the drop-off.
  3. Windowing feasibility — for "all campaigns, current Cairo month, migration
                             excluded": total lead volume + whether per-(campaign,
                             stage-group) counts come from ONE search_read + Python
                             regroup. Reports RPC count/latency vs per-campaign ×N.
  4. Migration effect      — top campaigns: all-time (incl migration, as the list
                             shows now) vs current-month windowed (excl migration).
  5. Buyer list            — 1-3 repeated briefly for media buyers.

Method discipline: READ-ONLY (search_read / read_group / search_count only).
ALLOWED_METHODS untouched. AI cost = $0.00. Reuses the SAME population basis as
Level 1 / the timeline (active_test=False, Cairo regroup, LEGACY_DAY_MIN exclusion).

Pre-flight: kill all python; purge __pycache__; (uvicorn, if used, WITHOUT --reload).

Usage (from project root):
    python scripts/discovery_level1_windowing.py
"""

import asyncio
import io
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loguru import logger  # noqa: E402

# keep stdout clean — silence the client's INFO/DEBUG RPC chatter
logger.remove()
logger.add(sys.stderr, level="WARNING")

from backend.modules.campaign_performance import domain  # noqa: E402
from backend.modules.campaign_performance.domain import (  # noqa: E402
    BUYER_FIELD,
    CAMPAIGN_FIELD,
    GROUP_ORDER,
    JUNK_CAMPAIGN_NAMES,
    classify_stage,
)
from backend.modules.campaign_performance.services import cache as _cp_cache  # noqa: E402
from backend.modules.campaign_performance.services.campaign_service import (  # noqa: E402
    get_campaign_performance_overview,
)
from backend.modules.marketing_attribution.services import cache as _ma_cache  # noqa: E402
from backend.modules.marketing_attribution.services.attribution_service import (  # noqa: E402
    get_attribution_overview,
)
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
_PAGE = 10000
_SAMPLE_N = 5          # campaigns sampled for the per-campaign latency extrapolation


def _to_cairo(dt_str) -> datetime:
    return (
        datetime.strptime(str(dt_str), "%Y-%m-%d %H:%M:%S")
        .replace(tzinfo=timezone.utc)
        .astimezone(_CAIRO)
    )


def _shift_months(dt: datetime, months: int) -> datetime:
    total = (dt.year * 12 + (dt.month - 1)) + months
    year, month = divmod(total, 12)
    return dt.replace(year=year, month=month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)


def _month_str(dt: datetime) -> str:
    return dt.strftime("%Y-%m")


def _cairo_month_start_to_utc(dt: datetime) -> str:
    """Cairo first-of-month 00:00 -> UTC-naive 'YYYY-MM-DD HH:MM:SS' (an exact lower bound)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _m2o(value):
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return int(value[0]), str(value[1])
    return None, None


async def _fetch_all(client, model, dom, fields):
    """Paged search_read; returns (rows, rpc_count, rpc_wall_ms)."""
    rows, offset, rpc_count = [], 0, 0
    t0 = time.monotonic()
    while True:
        page = await client.execute_kw(
            model, "search_read", args=[dom],
            kwargs={"fields": fields, "order": "id", "limit": _PAGE,
                    "offset": offset, "context": _CTX_ALL},
        )
        rpc_count += 1
        rows.extend(page)
        if len(page) < _PAGE:
            break
        offset += _PAGE
    return rows, rpc_count, int((time.monotonic() - t0) * 1000)


async def main():
    now_cairo = datetime.now(_CAIRO)
    current_month_start = now_cairo.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    current_month = _month_str(current_month_start)
    three_mo_start = _shift_months(current_month_start, -2)   # current + 2 prior = 3 months
    last3_months = {_month_str(_shift_months(current_month_start, -i)) for i in range(3)}

    print(_SEP)
    print("  LEVEL-1 LIST WINDOWING — READ-ONLY DISCOVERY ($0 AI)")
    print(f"  ALLOWED_METHODS : {sorted(ALLOWED_METHODS)}")
    print(f"  Population/ctx  : ALL leads incl. archived — context={_CTX_ALL}")
    print(f"  Today (Cairo)   : {now_cairo.date().isoformat()}  current_month={current_month}")
    print(f"  Last-3 window   : {sorted(last3_months)}")
    print(f"  Tunables        : threshold={domain.DEFAULT_MIN_LEAD_THRESHOLD}  "
          f"LEGACY_DAY_MIN={domain.LEGACY_DAY_MIN:,}")
    print(_SEP)
    print()

    _cp_cache.clear()
    _ma_cache.clear()

    async with OdooClient() as client:
        # ── stage info (for classify_stage) ────────────────────────────────────
        stages = await client.execute_kw(
            _STAGE, "search_read", args=[[]],
            kwargs={"fields": ["id", "name", "is_won"]},
        )
        stage_info = {int(s["id"]): {"name": str(s.get("name") or ""), "is_won": bool(s.get("is_won"))}
                      for s in stages}

        # ── campaign id<->name ──────────────────────────────────────────────────
        campaigns = await client.execute_kw(
            _CAMPAIGN, "search_read", args=[[]],
            kwargs={"fields": ["id", "name"], "context": _CTX_ALL},
        )
        id_to_name = {int(c["id"]): str(c.get("name") or "") for c in campaigns}
        junk_ids = {cid for cid, nm in id_to_name.items() if nm in JUNK_CAMPAIGN_NAMES}

        # ── the shipped Level-1 + attribution views (the "as the list shows now") ─
        overview = await get_campaign_performance_overview(client=client)            # threshold=50
        overview_all = await get_campaign_performance_overview(client=client, min_lead_threshold=1)
        attribution = await get_attribution_overview(client=client)

        # ── ONE full-population fetch powering req 2/4/5 + legacy detection ─────
        # (create_date + campaign_id + stage_id + media_buyer_id). Timed + reported
        # as the cost of the all-time, Cairo-correct, legacy-excluded computation.
        pop, pop_rpcs, pop_ms = await _fetch_all(
            client, _LEAD, [], ["create_date", CAMPAIGN_FIELD, "stage_id", BUYER_FIELD]
        )

        # legacy migration days (Cairo days with >= LEGACY_DAY_MIN leads) — dynamic
        by_day: Counter = Counter()
        for r in pop:
            cd = r.get("create_date")
            if cd:
                by_day[_to_cairo(cd).strftime("%Y-%m-%d")] += 1
        legacy_days = {d for d, c in by_day.items() if c >= domain.LEGACY_DAY_MIN}
        legacy_months = sorted({d[:7] for d in legacy_days})

        # ── regroup the whole population by Cairo month, dropping legacy days ───
        # campaign_months[cid] = set of Cairo months it has >=1 (non-legacy) lead in
        # buyer_months[bid]    = same for media_buyer_id
        campaign_months: dict = defaultdict(set)
        buyer_months: dict = defaultdict(set)
        cm_current_count: Counter = Counter()      # per-campaign current-month volume (excl legacy)
        legacy_lead_total = 0
        for r in pop:
            cd = r.get("create_date")
            if not cd:
                continue
            cairo = _to_cairo(cd)
            day = cairo.strftime("%Y-%m-%d")
            if day in legacy_days:
                legacy_lead_total += 1
                continue
            month = _month_str(cairo)
            cid, _ = _m2o(r.get(CAMPAIGN_FIELD))
            bid, _ = _m2o(r.get(BUYER_FIELD))
            if cid is not None:
                campaign_months[cid].add(month)
                if month == current_month:
                    cm_current_count[cid] += 1
            if bid is not None:
                buyer_months[bid].add(month)

        # ════════════════════════════════════════════════════════════════════════
        # 1. LIST SCALE
        # ════════════════════════════════════════════════════════════════════════
        print(_SEP)
        print("  (1) LIST SCALE — what the Level-1 list contains today")
        print(_SEP)
        total_campaign_records = len(id_to_name)
        with_leads = overview["total_campaigns_with_leads"]
        listed = overview["listed_campaign_count"]
        tail = overview["long_tail"]["campaign_count"] if overview["long_tail"] else 0
        junk_bucket = overview["data_quality"]["junk_none"]
        nocamp = overview["data_quality"]["no_campaign"]
        real_with_leads = len(overview_all["campaigns"])      # threshold=1 => every real campaign is a row
        denylist_rows = sum(1 for c in overview_all["campaigns"] if c["attribution_status"] == "excluded_channel")
        confirmed_rows = sum(1 for c in overview_all["campaigns"] if c["attribution_status"] == "confirmed")
        dominant_rows = sum(1 for c in overview_all["campaigns"] if c["attribution_status"] == "dominant")
        print(f"  total utm.campaign records ................ {total_campaign_records:>7,}")
        print(f"  campaigns with >=1 lead (all-time) ........ {with_leads:>7,}")
        print(f"  REAL campaigns (junk 'None' excluded) ..... {real_with_leads:>7,}   <- list universe")
        print(f"    listed individually (>= {domain.DEFAULT_MIN_LEAD_THRESHOLD} leads) ....... {listed:>7,}")
        print(f"    rolled into long tail (< {domain.DEFAULT_MIN_LEAD_THRESHOLD} leads) ...... {tail:>7,}")
        print(f"  gating (display only; still list rows):")
        print(f"    confirmed buyer shown ................... {confirmed_rows:>7,}")
        print(f"    dominant buyer shown ................... {dominant_rows:>7,}")
        print(f"    denylist (excluded_channel) ............ {denylist_rows:>7,}")
        print(f"  data-quality buckets (NEVER list rows):")
        print(f"    junk 'None' campaign ................... "
              f"{(junk_bucket['lead_count'] if junk_bucket else 0):>7,} leads")
        print(f"    no-campaign (campaign_id=False) ........ "
              f"{(nocamp['lead_count'] if nocamp else 0):>7,} leads")
        print(f"  total population .......................... {overview['total_leads_population']:>7,} leads")
        print(f"  legacy migration: days={sorted(legacy_days)} months={legacy_months} "
              f"leads_excluded={legacy_lead_total:,}")
        print()

        # ════════════════════════════════════════════════════════════════════════
        # 2. ACTIVE-THIS-PERIOD REALITY (the core question)
        # ════════════════════════════════════════════════════════════════════════
        print(_SEP)
        print("  (2) ACTIVE-THIS-PERIOD — # REAL campaigns with >=1 post-migration Cairo lead")
        print(_SEP)
        real_ids = [cid for cid in campaign_months if cid not in junk_ids]
        n_alltime = len(real_ids)
        n_last3 = sum(1 for cid in real_ids if campaign_months[cid] & last3_months)
        n_current = sum(1 for cid in real_ids if current_month in campaign_months[cid])
        # also the LISTED subset (>=50 all-time) — the rows actually shown by default
        listed_ids = {c["campaign_id"] for c in overview["campaigns"]}
        l_alltime = sum(1 for cid in listed_ids if cid in campaign_months)
        l_last3 = sum(1 for cid in listed_ids if campaign_months.get(cid, set()) & last3_months)
        l_current = sum(1 for cid in listed_ids if current_month in campaign_months.get(cid, set()))
        print(f"  ALL real campaigns (post-migration, Cairo):")
        print(f"    (c) all-time ............ {n_alltime:>5,}")
        print(f"    (b) last 3 months ....... {n_last3:>5,}   ({100.0*n_last3/n_alltime:.1f}% of all-time)")
        print(f"    (a) current month ....... {n_current:>5,}   ({100.0*n_current/n_alltime:.1f}% of all-time)")
        print(f"  LISTED rows only (>= {domain.DEFAULT_MIN_LEAD_THRESHOLD} all-time, n={len(listed_ids)}):")
        print(f"    (c) all-time ............ {l_alltime:>5,}")
        print(f"    (b) last 3 months ....... {l_last3:>5,}")
        print(f"    (a) current month ....... {l_current:>5,}")
        print()

        # ════════════════════════════════════════════════════════════════════════
        # 3. WINDOWING FEASIBILITY — single windowed query for the WHOLE list
        # ════════════════════════════════════════════════════════════════════════
        print(_SEP)
        print("  (3) WINDOWING FEASIBILITY — one windowed query for ALL campaigns, current Cairo month")
        print(_SEP)
        cm_lo = _cairo_month_start_to_utc(current_month_start)
        l3_lo = _cairo_month_start_to_utc(three_mo_start)

        # (3a) RECOMMENDED: ONE search_read of the window + Python regroup → per
        #      (campaign, stage-group). Cairo-correct + legacy-excludable.
        win_rows, win_rpcs, win_ms = await _fetch_all(
            client, _LEAD, [("create_date", ">=", cm_lo)], ["create_date", CAMPAIGN_FIELD, "stage_id"]
        )
        t0 = time.monotonic()
        win_funnel: dict = defaultdict(lambda: {g: 0 for g in GROUP_ORDER})
        win_campaigns = set()
        win_total = 0
        for r in win_rows:
            cd = r.get("create_date")
            if not cd:
                continue
            cairo = _to_cairo(cd)
            if cairo.strftime("%Y-%m-%d") in legacy_days:
                continue
            if _month_str(cairo) != current_month:        # exact-bound guard
                continue
            cid, _ = _m2o(r.get(CAMPAIGN_FIELD))
            sid, _ = _m2o(r.get("stage_id"))
            win_funnel[cid][classify_stage(sid, stage_info)] += 1
            win_campaigns.add(cid)
            win_total += 1
        regroup_ms = int((time.monotonic() - t0) * 1000)

        # (3b) read_group alternative (server-side per-(campaign,stage), 1 RPC) — fastest
        #      but raw-UTC bucketed + cannot drop legacy days per Cairo-day.
        t0 = time.monotonic()
        rg_rows = await client.execute_kw(
            _LEAD, "read_group",
            args=[[("create_date", ">=", cm_lo)], [CAMPAIGN_FIELD, "stage_id"], [CAMPAIGN_FIELD, "stage_id"]],
            kwargs={"context": _CTX_ALL, "lazy": False},
        )
        rg_ms = int((time.monotonic() - t0) * 1000)

        # (3c) per-campaign cost: sample N big listed campaigns, 1 windowed read_group each
        sample_ids = [c["campaign_id"] for c in overview["campaigns"][:_SAMPLE_N]]
        per_cam_ms = []
        for cid in sample_ids:
            t0 = time.monotonic()
            await client.execute_kw(
                _LEAD, "read_group",
                args=[[(CAMPAIGN_FIELD, "=", cid), ("create_date", ">=", cm_lo)],
                      ["stage_id"], ["stage_id"]],
                kwargs={"context": _CTX_ALL, "lazy": False},
            )
            per_cam_ms.append((time.monotonic() - t0) * 1000)
        avg_per_cam = sum(per_cam_ms) / len(per_cam_ms) if per_cam_ms else 0.0
        n_listed = len(listed_ids)

        print(f"  current-month window lower bound (UTC) : {cm_lo}")
        print(f"  (3a) SINGLE search_read + Python regroup  [RECOMMENDED — Cairo+legacy safe]")
        print(f"        rows fetched ......... {len(win_rows):>7,}")
        print(f"        RPCs (pages) ......... {win_rpcs:>7}")
        print(f"        RPC wall time ........ {win_ms:>7,} ms")
        print(f"        Python regroup ....... {regroup_ms:>7,} ms")
        print(f"        TOTAL ................ {win_ms + regroup_ms:>7,} ms")
        print(f"        window lead volume ... {win_total:>7,}  (legacy excluded; current month)")
        print(f"        campaigns present .... {len(win_campaigns - {None}):>7,}")
        print(f"  (3b) SINGLE read_group (server-side, 1 RPC)  [faster, NOT Cairo/legacy safe]")
        print(f"        (campaign,stage) rows  {len(rg_rows):>7,}")
        print(f"        RPC wall time ........ {rg_ms:>7,} ms")
        print(f"  (3c) PER-CAMPAIGN (×N) cost  [the naive alternative]")
        print(f"        sampled {len(sample_ids)} campaigns, avg {avg_per_cam:,.0f} ms/campaign (1 read_group each)")
        print(f"        extrapolated to listed N={n_listed}: ~{avg_per_cam * n_listed / 1000:,.1f} s "
              f"({n_listed} RPCs, sequential)")
        print(f"        vs single windowed query above: {win_ms + regroup_ms:,} ms ({win_rpcs} RPCs)")
        print()

        # also size the 3-month window (a likely preset) with the same single fetch
        l3_rows, l3_rpcs, l3_ms = await _fetch_all(
            client, _LEAD, [("create_date", ">=", l3_lo)], ["create_date", CAMPAIGN_FIELD, "stage_id"]
        )
        l3_total = 0
        l3_campaigns = set()
        for r in l3_rows:
            cd = r.get("create_date")
            if not cd:
                continue
            cairo = _to_cairo(cd)
            if cairo.strftime("%Y-%m-%d") in legacy_days:
                continue
            if _month_str(cairo) not in last3_months:
                continue
            cid, _ = _m2o(r.get(CAMPAIGN_FIELD))
            l3_total += 1
            l3_campaigns.add(cid)
        print(f"  last-3-month window (preset candidate): rows={len(l3_rows):,} RPCs={l3_rpcs} "
              f"wall={l3_ms:,}ms volume={l3_total:,} campaigns={len(l3_campaigns - {None}):,}")
        print()

        # ════════════════════════════════════════════════════════════════════════
        # 4. MIGRATION EFFECT AT LIST LEVEL
        # ════════════════════════════════════════════════════════════════════════
        print(_SEP)
        print("  (4) MIGRATION EFFECT — top campaigns: all-time (incl migration) vs current-month windowed")
        print(_SEP)
        print(f"  {'campaign':<34} {'all-time(list)':>14} {'cur-month(win)':>14} {'cur/all':>8}")
        for c in overview["campaigns"][:4]:
            cid = c["campaign_id"]
            allt = c["lead_count"]
            cur = cm_current_count.get(cid, 0)
            ratio = f"{100.0*cur/allt:.1f}%" if allt else "—"
            print(f"  {c['campaign_name'][:33]:<34} {allt:>14,} {cur:>14,} {ratio:>8}")
        print(f"  (all-time = the number the list shows TODAY, incl. the Nov-2025 migration;")
        print(f"   cur-month = the same campaign scoped to {current_month}, migration excluded.)")
        print()

        # ════════════════════════════════════════════════════════════════════════
        # 5. BUYER LIST — repeat 1-3 briefly
        # ════════════════════════════════════════════════════════════════════════
        print(_SEP)
        print("  (5) BUYER LIST — media buyers by period")
        print(_SEP)
        n_buyers_alltime = len(buyer_months)
        n_buyers_last3 = sum(1 for bid in buyer_months if buyer_months[bid] & last3_months)
        n_buyers_current = sum(1 for bid in buyer_months if current_month in buyer_months[bid])
        print(f"  distinct media_buyer_id with >=1 post-migration lead:")
        print(f"    (c) all-time ............ {n_buyers_alltime:>5,}")
        print(f"    (b) last 3 months ....... {n_buyers_last3:>5,}")
        print(f"    (a) current month ....... {n_buyers_current:>5,}")
        print(f"  attribution view 'buyers' (derived dominant of confirmed campaigns): "
              f"{len(attribution['buyers'])}")
        # buyer windowing volume (current month) from the SAME single window fetch idea
        buyer_win_rows, buyer_win_rpcs, buyer_win_ms = await _fetch_all(
            client, _LEAD, [("create_date", ">=", cm_lo), (BUYER_FIELD, "!=", False)],
            ["create_date", BUYER_FIELD],
        )
        buyer_cur_vol = Counter()
        for r in buyer_win_rows:
            cd = r.get("create_date")
            if not cd:
                continue
            cairo = _to_cairo(cd)
            if cairo.strftime("%Y-%m-%d") in legacy_days or _month_str(cairo) != current_month:
                continue
            bid, _ = _m2o(r.get(BUYER_FIELD))
            buyer_cur_vol[bid] += 1
        print(f"  single windowed buyer query (current month): rows={len(buyer_win_rows):,} "
              f"RPCs={buyer_win_rpcs} wall={buyer_win_ms:,}ms")
        print(f"  per-buyer current-month volume (top 8):")
        for bid, vol in buyer_cur_vol.most_common(8):
            print(f"        buyer id={bid:<6} {vol:>6,} leads")
        print()

        # ── full-population fetch cost (context for the all-time path) ──────────
        print(_SEP)
        print("  RPC COST SUMMARY")
        print(_SEP)
        print(f"  full-population fetch (create_date+campaign+stage+buyer): "
              f"rows={len(pop):,} RPCs={pop_rpcs} wall={pop_ms:,}ms")
        print(f"  single current-month window fetch ....................... "
              f"rows={len(win_rows):,} RPCs={win_rpcs} wall={win_ms:,}ms")
        print(f"  single last-3-month window fetch ........................ "
              f"rows={len(l3_rows):,} RPCs={l3_rpcs} wall={l3_ms:,}ms")
        print()

    print(_SEP)
    print("  DISCOVERY COMPLETE — numbers above. READ-ONLY, $0. No writes, no commits.")
    print(_SEP)
    return 0


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
