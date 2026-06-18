"""
scripts/discovery_media_buyer_windowing.py — READ-ONLY discovery ($0 AI) for
MEDIA-BUYER (marketing_attribution) LIST windowing feasibility.

WHY
The media-buyer page (route /marketing-attribution/dashboard) lists buyers with a
4-group funnel each, ALL-TIME only — no time windows. We want to add the SAME
window controls the campaign page now has (current / last3 / all + custom range,
migration excluded). Before building, settle ONE question with real numbers:
HOW is buyer attribution keyed, and what would a windowed buyer view show for
recent periods?

THE PAGE'S ACTUAL MECHANISM (read from attribution_service.py):
  Attribution is MAP-BASED on campaign_id, NOT the raw media_buyer_id field.
    1. Derive a campaign->buyer MAP from ALL-TIME BOTH-SET leads (leads with both
       campaign_id AND media_buyer_id): dominant buyer = the media_buyer_id with
       the most both-set leads in that campaign; concentration = dominant/both.
    2. GATE: attributing_ids = confirmed campaigns that qualify (conc >= 90%) and
       are not denylisted.
    3. Attribute ALL leads of each attributing campaign to its DERIVED dominant
       buyer — REGARDLESS of whether the individual lead has media_buyer_id set
       (amendment A6). media_buyer_id is the MAP BASIS only, never the lead key.
  => A lead is attributed to a buyer via its CAMPAIGN. campaign_id is always
     filled on recent leads, so a windowed buyer view shows real recent data.

This probe reproduces that mechanism but SCOPED to a Cairo window (legacy Nov-2025
migration excluded), exactly like get_campaign_performance_windowed: the
campaign->buyer MAP stays ALL-TIME both-set (so a buyer's identity never shifts
with the window), only the attributed leads are windowed.

Reports A-E:
  A. KEYING (decisive) — current month: windowed leads attributable via the CAMPAIGN
     MAP (per buyer) vs leads with a non-empty media_buyer_id FIELD (per buyer); gap.
  B. WINDOWED BUYER VIEW — current month + last 3 months: per buyer total + 4-group
     funnel, tied to the campaign reference (Ahmed Aymen ~ FB-AY, etc.).
  C. UNMAPPED RECENT ACTIVITY — current-month volume OUTSIDE the buyer map
     (pending / denylisted / unconfirmed like FB-OK), would show as unattributed.
  D. FEASIBILITY — one windowed query (like the campaign windowing): RPC count + ms.
  E. ALL-TIME REGRESSION SANITY — probe's all-time buyer totals/funnels must match
     the shipped page (get_attribution_overview) before trusting windowed numbers.

Method discipline: READ-ONLY (search_read / read_group only). ALLOWED_METHODS
untouched. AI cost = $0.00. Same population basis as the page (active_test=False,
Cairo regroup, LEGACY_DAY_MIN exclusion). Leaves NOTHING on the live page; writes
NOTHING to Odoo.

Pre-flight: kill stray python; purge __pycache__; (uvicorn, if used, WITHOUT --reload).

Usage (from project root):
    python scripts/discovery_media_buyer_windowing.py
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

from backend.modules.campaign_performance import domain as cp_domain  # noqa: E402
from backend.modules.campaign_performance.services.timeline_service import (  # noqa: E402
    get_legacy_migration_days,
)
from backend.modules.marketing_attribution import domain as ma_domain  # noqa: E402
from backend.modules.marketing_attribution.domain import (  # noqa: E402
    BUYER_FIELD,
    CAMPAIGN_FIELD,
    GROUP_ORDER,
    classify_stage,
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


def _utc(dt: datetime) -> str:
    """Cairo-aware datetime -> UTC-naive 'YYYY-MM-DD HH:MM:SS' (an exact fetch bound)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _m2o(value):
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return int(value[0]), str(value[1])
    return None, None


def _qualifies(dom_cnt: int, both: int) -> bool:
    """concentration >= 0.90, integer-exact (mirror attribution_service._qualifies)."""
    if both <= 0:
        return False
    return dom_cnt * 100 >= both * 90


def _funnel_str(groups: dict) -> str:
    """Compact 4-group funnel string (avoids RTL fixed-width alignment headaches)."""
    return "  ".join(f"{g}={groups.get(g, 0):,}" for g in GROUP_ORDER)


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
    next_month_start = _shift_months(current_month_start, 1)
    three_mo_start = _shift_months(current_month_start, -2)      # current + 2 prior = 3 months
    current_set = {current_month}
    last3_set = {_month_str(_shift_months(current_month_start, -i)) for i in range(3)}

    print(_SEP)
    print("  MEDIA-BUYER (marketing_attribution) LIST WINDOWING — READ-ONLY DISCOVERY ($0 AI)")
    print(f"  ALLOWED_METHODS : {sorted(ALLOWED_METHODS)}")
    print(f"  Population/ctx  : ALL leads incl. archived — context={_CTX_ALL}")
    print(f"  Today (Cairo)   : {now_cairo.date().isoformat()}  current_month={current_month}")
    print(f"  Last-3 window   : {sorted(last3_set)}")
    print(f"  Confirmed cfg   : {sorted(ma_domain.CONFIRMED_BUYER_CAMPAIGNS)}")
    print(f"  Denylist cfg    : {sorted(ma_domain.DENYLIST_CAMPAIGNS)}")
    print(f"  LEGACY_DAY_MIN  : {cp_domain.LEGACY_DAY_MIN:,}")
    print(_SEP)
    print()

    _ma_cache.clear()

    async with OdooClient() as client:
        # ── stage info (for classify_stage) ──────────────────────────────────────
        stages = await client.execute_kw(
            _STAGE, "search_read", args=[[]],
            kwargs={"fields": ["id", "name", "is_won"]},
        )
        stage_info = {int(s["id"]): {"name": str(s.get("name") or ""), "is_won": bool(s.get("is_won"))}
                      for s in stages}

        # ── campaign id<->name + gate resolution (independent of the module) ─────
        campaigns = await client.execute_kw(
            _CAMPAIGN, "search_read", args=[[]],
            kwargs={"fields": ["id", "name"], "context": _CTX_ALL},
        )
        id_to_name = {int(c["id"]): str(c.get("name") or "") for c in campaigns}
        name_to_ids: dict = defaultdict(list)
        for cid, nm in id_to_name.items():
            name_to_ids[nm].append(cid)

        def _resolve(names):
            out = set()
            for nm in names:
                out.update(name_to_ids.get(nm, []))
            return out

        confirmed_ids = _resolve(ma_domain.CONFIRMED_BUYER_CAMPAIGNS)
        denylist_ids = _resolve(ma_domain.DENYLIST_CAMPAIGNS)

        # ── ALL-TIME both-set map: dominant buyer + concentration per campaign ───
        both_set_rows = await client.execute_kw(
            _LEAD, "read_group",
            args=[[(CAMPAIGN_FIELD, "!=", False), (BUYER_FIELD, "!=", False)],
                  [CAMPAIGN_FIELD, BUYER_FIELD], [CAMPAIGN_FIELD, BUYER_FIELD]],
            kwargs={"context": _CTX_ALL, "lazy": False},
        )
        campaign_map: dict = {}
        for r in both_set_rows:
            cid, _ = _m2o(r.get(CAMPAIGN_FIELD))
            if cid is None:
                continue
            bid, bname = _m2o(r.get(BUYER_FIELD))
            if bid is None:
                continue
            cnt = int(r.get("__count") or 0)
            e = campaign_map.setdefault(cid, {"both": 0, "buyers": Counter(), "names": {}})
            e["both"] += cnt
            e["buyers"][bid] += cnt
            e["names"][bid] = bname

        def _dominant(cid):
            e = campaign_map.get(cid)
            if not e or not e["buyers"]:
                return None, None, 0, 0
            bid, dom = e["buyers"].most_common(1)[0]
            return bid, e["names"].get(bid), dom, e["both"]

        # ── GATE: attributing_ids (confirmed AND qualifies AND not denied) ───────
        attributing_ids: set = set()
        buyer_of_campaign: dict = {}        # cid -> (bid, bname)
        for cid in sorted(confirmed_ids):
            if cid in denylist_ids:
                continue
            bid, bname, dom, both = _dominant(cid)
            if both == 0 or not _qualifies(dom, both):
                continue
            attributing_ids.add(cid)
            buyer_of_campaign[cid] = (bid, bname)

        attributing_buyer_ids = {bid for bid, _ in buyer_of_campaign.values()}

        print(_SEP)
        print("  (0) KEYING MECHANISM + GATE (reproduced independently from live data)")
        print(_SEP)
        print(f"  confirmed campaign ids ... {sorted(confirmed_ids)}")
        print(f"  denylist campaign ids .... {sorted(denylist_ids)}")
        print(f"  ATTRIBUTING campaign ids . {sorted(attributing_ids)}  (confirmed AND conc>=90% AND not denied)")
        print(f"  derived campaign->buyer map (attributing only):")
        for cid in sorted(attributing_ids):
            bid, bname, dom, both = _dominant(cid)
            conc = 100.0 * dom / both if both else 0.0
            print(f"     {id_to_name.get(cid, cid)!r:<16} (id={cid}) -> {bname!r} "
                  f"(buyer id={bid})  conc={conc:.1f}%  both_set={both:,}")
        print(f"  MECHANISM: a lead is attributed to a buyer VIA its campaign_id (always filled);")
        print(f"  the raw media_buyer_id FIELD is only the MAP BASIS (both-set), never the lead key.")
        print()

        # ── legacy migration days (the SAME dynamic detection the windowing uses) ─
        legacy_days = await get_legacy_migration_days(client)
        legacy_months = sorted({d[:7] for d in legacy_days})
        print(f"  legacy migration days (>= {cp_domain.LEGACY_DAY_MIN:,}/Cairo-day): "
              f"{sorted(legacy_days)}  months={legacy_months}")
        print()

        # ── windowed fetches: ONE paged search_read per window (the feature query) ─
        cm_lo, cm_hi = _utc(current_month_start), _utc(next_month_start)
        l3_lo, l3_hi = _utc(three_mo_start), _utc(next_month_start)
        win_fields = ["create_date", CAMPAIGN_FIELD, "stage_id", BUYER_FIELD]

        cm_rows, cm_rpcs, cm_ms = await _fetch_all(
            client, _LEAD, [("create_date", ">=", cm_lo), ("create_date", "<", cm_hi)], win_fields)
        l3_rows, l3_rpcs, l3_ms = await _fetch_all(
            client, _LEAD, [("create_date", ">=", l3_lo), ("create_date", "<", l3_hi)], win_fields)

        # regroup (legacy-excluded, Cairo-bucketed, classified). Done here so
        # classify_stage gets the real stage_info (the helper above is a stub-free path).
        def _regroup(rows, month_set):
            total = with_campaign = with_buyer_field = 0
            camp_count: Counter = Counter()
            camp_stage: dict = defaultdict(lambda: {g: 0 for g in GROUP_ORDER})
            raw_buyer: Counter = Counter()
            raw_buyer_name: dict = {}
            for r in rows:
                cd = r.get("create_date")
                if not cd:
                    continue
                cairo = _to_cairo(cd)
                if cairo.strftime("%Y-%m-%d") in legacy_days:
                    continue
                if _month_str(cairo) not in month_set:
                    continue
                total += 1
                cid, _ = _m2o(r.get(CAMPAIGN_FIELD))
                sid, _ = _m2o(r.get("stage_id"))
                grp = classify_stage(sid, stage_info)
                if cid is not None:
                    with_campaign += 1
                    camp_count[cid] += 1
                    camp_stage[cid][grp] += 1
                bid, bname = _m2o(r.get(BUYER_FIELD))
                if bid is not None:
                    with_buyer_field += 1
                    raw_buyer[bid] += 1
                    raw_buyer_name[bid] = bname
            return {
                "total": total, "with_campaign": with_campaign, "with_buyer_field": with_buyer_field,
                "camp_count": camp_count, "camp_stage": camp_stage,
                "raw_buyer": raw_buyer, "raw_buyer_name": raw_buyer_name,
            }

        cm = _regroup(cm_rows, current_set)
        l3 = _regroup(l3_rows, last3_set)

        def _buyer_view(w):
            """Build the windowed buyer view from a regrouped window: attribute each
            attributing campaign's windowed funnel to its dominant buyer."""
            buyers: dict = defaultdict(lambda: {"name": None, "total": 0,
                                                "groups": {g: 0 for g in GROUP_ORDER},
                                                "campaigns": set()})
            for cid in attributing_ids:
                if cid not in w["camp_stage"]:
                    continue
                bid, bname = buyer_of_campaign[cid]
                b = buyers[bid]
                b["name"] = bname
                b["campaigns"].add(cid)
                for g in GROUP_ORDER:
                    c = w["camp_stage"][cid][g]
                    b["groups"][g] += c
                    b["total"] += c
            return buyers

        # ════════════════════════════════════════════════════════════════════════
        # A. KEYING (decisive) — CURRENT MONTH: campaign map vs raw media_buyer_id
        # ════════════════════════════════════════════════════════════════════════
        print(_SEP)
        print("  (A) KEYING — CURRENT MONTH (Cairo, migration excluded): map vs raw field")
        print(_SEP)
        cm_buyers = _buyer_view(cm)
        attributed_via_map = sum(b["total"] for b in cm_buyers.values())
        print(f"  total windowed leads (current month) ............ {cm['total']:>7,}")
        print(f"    with campaign_id set (the MAP KEY) ............ {cm['with_campaign']:>7,}  "
              f"({100.0*cm['with_campaign']/cm['total'] if cm['total'] else 0:.1f}%)")
        print(f"    with media_buyer_id FIELD set (raw) ........... {cm['with_buyer_field']:>7,}  "
              f"({100.0*cm['with_buyer_field']/cm['total'] if cm['total'] else 0:.1f}%)")
        print()
        print(f"  (A.1) ATTRIBUTABLE VIA THE CAMPAIGN MAP (what the windowed buyer page would show):")
        print(f"        total attributed ......... {attributed_via_map:>7,}  "
              f"({100.0*attributed_via_map/cm['total'] if cm['total'] else 0:.1f}% of windowed leads)")
        for bid, b in sorted(cm_buyers.items(), key=lambda kv: -kv[1]["total"]):
            camps = sorted(id_to_name.get(c, c) for c in b["campaigns"])
            print(f"        {b['name']!r:<18} (id={bid}) {b['total']:>6,}   via {camps}")
        print()
        print(f"  (A.2) HAVE A NON-EMPTY media_buyer_id FIELD DIRECTLY (raw, not the page mechanism):")
        print(f"        total with field ......... {cm['with_buyer_field']:>7,}")
        if not cm["raw_buyer"]:
            print(f"        (no current-month lead carries a media_buyer_id value)")
        for bid, vol in cm["raw_buyer"].most_common(12):
            print(f"        {cm['raw_buyer_name'].get(bid, '?')!r:<18} (id={bid}) {vol:>6,}")
        print()
        gap = attributed_via_map - cm["with_buyer_field"]
        print(f"  (A.3) GAP (map-attributed − raw-field-set) ...... {gap:>+7,}")
        print(f"        => the campaign map attributes {attributed_via_map:,} leads this month; only "
              f"{cm['with_buyer_field']:,} carry the raw field.")
        print(f"        A media_buyer_id-BASED window would show ~{cm['with_buyer_field']:,}; the "
              f"map-based window shows {attributed_via_map:,}.")
        print()

        # ════════════════════════════════════════════════════════════════════════
        # B. WINDOWED BUYER VIEW — current month + last 3 months (page mechanism)
        # ════════════════════════════════════════════════════════════════════════
        # current-month per-campaign reference (ties buyers to FB-AY/Outsource-Y/...)
        def _print_buyer_view(label, w, buyers):
            print(_SEP2)
            print(f"  {label}: windowed leads={w['total']:,}  buyers with >=1 attributed lead="
                  f"{sum(1 for b in buyers.values() if b['total'] > 0)}")
            print(_SEP2)
            for bid, b in sorted(buyers.items(), key=lambda kv: -kv[1]["total"]):
                if b["total"] == 0:
                    continue
                camps = sorted(id_to_name.get(c, c) for c in b["campaigns"])
                recon = "OK" if sum(b["groups"].values()) == b["total"] else "**RECON FAIL**"
                print(f"    {b['name']!r:<18} (id={bid})  total={b['total']:>6,}  [{recon}]  via {camps}")
                print(f"        funnel: {_funnel_str(b['groups'])}")
            # tie to the per-campaign reference
            print(f"    per-campaign windowed volume (for the 1:1 tie):")
            for cid in sorted(attributing_ids, key=lambda c: -w["camp_count"].get(c, 0)):
                print(f"        {id_to_name.get(cid, cid)!r:<16} (id={cid}) "
                      f"{w['camp_count'].get(cid, 0):>6,}")

        print(_SEP)
        print("  (B) WINDOWED BUYER VIEW — via the page's campaign-map mechanism")
        print(_SEP)
        _print_buyer_view(f"CURRENT MONTH ({current_month})", cm, cm_buyers)
        print()
        l3_buyers = _buyer_view(l3)
        _print_buyer_view(f"LAST 3 MONTHS ({sorted(last3_set)})", l3, l3_buyers)
        print()

        # ════════════════════════════════════════════════════════════════════════
        # C. UNMAPPED RECENT ACTIVITY — current-month volume OUTSIDE the buyer map
        # ════════════════════════════════════════════════════════════════════════
        print(_SEP)
        print("  (C) UNMAPPED RECENT ACTIVITY — current-month volume OUTSIDE the buyer map")
        print(_SEP)
        unmapped_total = 0
        unmapped_rows = []
        for cid, vol in cm["camp_count"].items():
            if cid in attributing_ids:
                continue
            unmapped_total += vol
            bid, bname, dom, both = _dominant(cid)
            conc = 100.0 * dom / both if both else 0.0
            if cid in denylist_ids:
                why = "DENYLISTED"
            elif cid in confirmed_ids:
                why = "confirmed-but-failed-gate"
            elif both > 0 and _qualifies(dom, both):
                why = "PENDING (qualifies, not confirmed)"
            else:
                why = "unmapped (no/low both-set buyer)"
            unmapped_rows.append((vol, cid, id_to_name.get(cid, cid), why, bname, conc, both))
        no_campaign_vol = cm["total"] - cm["with_campaign"]
        unmapped_total += no_campaign_vol
        print(f"  current-month windowed leads ............... {cm['total']:>7,}")
        print(f"    attributed (in the buyer map) ............ {attributed_via_map:>7,}  "
              f"({100.0*attributed_via_map/cm['total'] if cm['total'] else 0:.1f}%)")
        print(f"    OUTSIDE the buyer map (unattributed) ..... {unmapped_total:>7,}  "
              f"({100.0*unmapped_total/cm['total'] if cm['total'] else 0:.1f}%)")
        print(f"      of which: no campaign_id at all ........ {no_campaign_vol:>7,}")
        print(f"  top unmapped campaigns this month (would be OFF the buyer list):")
        for vol, cid, nm, why, bname, conc, both in sorted(unmapped_rows, reverse=True)[:12]:
            dom_note = f"dom={bname!r} conc={conc:.0f}% both={both:,}" if both else "no both-set buyer"
            print(f"     {nm!r:<18} (id={cid}) {vol:>6,}  [{why}]  {dom_note}")
        print()

        # ════════════════════════════════════════════════════════════════════════
        # D. FEASIBILITY — one windowed query (like the campaign windowing)
        # ════════════════════════════════════════════════════════════════════════
        print(_SEP)
        print("  (D) FEASIBILITY — windowed buyer view = ONE windowed search_read + Python re-aggregate")
        print(_SEP)
        print(f"  The buyer view re-aggregates the SAME single windowed lead fetch the campaign")
        print(f"  windowing already does (group by campaign->buyer instead of by campaign). Plus the")
        print(f"  ALL-TIME both-set map (1 read_group, shared) + legacy days (cached, shared).")
        print(f"  CURRENT MONTH window fetch : rows={len(cm_rows):>7,}  RPCs(pages)={cm_rpcs}  wall={cm_ms:,} ms")
        print(f"  LAST 3 MONTHS window fetch : rows={len(l3_rows):>7,}  RPCs(pages)={l3_rpcs}  wall={l3_ms:,} ms")
        print(f"  => single windowed query is ample; same shape/cost as get_campaign_performance_windowed.")
        print()

        # ════════════════════════════════════════════════════════════════════════
        # E. ALL-TIME REGRESSION SANITY — probe vs the shipped page
        # ════════════════════════════════════════════════════════════════════════
        print(_SEP)
        print("  (E) ALL-TIME REGRESSION SANITY — probe's all-time attribution vs the shipped page")
        print(_SEP)
        # probe's independent all-time attribution: read_group(campaign,stage) over
        # attributing_ids, legacy INCLUDED (the page does NOT exclude migration), per
        # dominant buyer + 4-group funnel.
        attrib_rows = await client.execute_kw(
            _LEAD, "read_group",
            args=[[(CAMPAIGN_FIELD, "in", sorted(attributing_ids))],
                  [CAMPAIGN_FIELD, "stage_id"], [CAMPAIGN_FIELD, "stage_id"]],
            kwargs={"context": _CTX_ALL, "lazy": False},
        )
        probe_buyers: dict = defaultdict(lambda: {"name": None, "total": 0,
                                                  "groups": {g: 0 for g in GROUP_ORDER}})
        for r in attrib_rows:
            cid, _ = _m2o(r.get(CAMPAIGN_FIELD))
            if cid is None or cid not in attributing_ids:
                continue
            bid, bname = buyer_of_campaign[cid]
            sid, _ = _m2o(r.get("stage_id"))
            cnt = int(r.get("__count") or 0)
            grp = classify_stage(sid, stage_info)
            pb = probe_buyers[bid]
            pb["name"] = bname
            pb["total"] += cnt
            pb["groups"][grp] += cnt

        # the shipped module (default production config, same injected client)
        module = await get_attribution_overview(client=client)
        module_buyers = {b["buyer_id"]: b for b in module["buyers"]}

        print(f"  {'buyer':<20} {'PROBE total':>12} {'PAGE total':>12}  {'totals':>8}  funnel match")
        print(f"  {'-'*20} {'-'*12} {'-'*12}  {'-'*8}  {'-'*12}")
        fails = 0
        all_bids = sorted(set(probe_buyers) | set(module_buyers),
                          key=lambda b: -(module_buyers.get(b, {}).get("total_attributed", 0)))
        for bid in all_bids:
            pb = probe_buyers.get(bid)
            mb = module_buyers.get(bid)
            pname = pb["name"] if pb else (mb["buyer_name"] if mb else "?")
            ptot = pb["total"] if pb else 0
            mtot = mb["total_attributed"] if mb else 0
            tot_ok = ptot == mtot
            # funnel match
            f_ok = True
            if pb and mb:
                mgroups = {o["group"]: o["count"] for o in mb["outcomes"]}
                f_ok = all(pb["groups"][g] == mgroups.get(g, 0) for g in GROUP_ORDER)
            else:
                f_ok = False
            if not (tot_ok and f_ok):
                fails += 1
            print(f"  {str(pname)[:20]:<20} {ptot:>12,} {mtot:>12,}  "
                  f"{'PASS' if tot_ok else '**FAIL**':>8}  {'PASS' if f_ok else '**FAIL**'}")
        print()
        print(f"  page attribution_pct={module['attribution_pct']:.2f}%  "
              f"total_attributed={module['total_attributed']:,}  "
              f"population={module['total_leads_population']:,}")
        print(f"  REGRESSION RESULT: {'ALL PASS — probe reproduces the page' if fails == 0 else f'{fails} MISMATCH(ES) — DO NOT trust windowed numbers'}")
        print()

        # ── RPC cost summary ─────────────────────────────────────────────────────
        print(_SEP)
        print("  RPC COST SUMMARY (read-only)")
        print(_SEP)
        print(f"  current-month window fetch ..... rows={len(cm_rows):,} RPCs={cm_rpcs} wall={cm_ms:,}ms")
        print(f"  last-3-month window fetch ...... rows={len(l3_rows):,} RPCs={l3_rpcs} wall={l3_ms:,}ms")
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
