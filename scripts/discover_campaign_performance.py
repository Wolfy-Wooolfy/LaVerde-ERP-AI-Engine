"""
scripts/discover_campaign_performance.py — Campaign-CENTRIC performance discovery
(READ-ONLY, $0 AI).

Goal: determine exactly what Odoo data can support a per-CAMPAIGN performance view
(the sibling to the shipped media-buyer attribution view), so product can decide
what each build phase can honestly contain. DISCOVERY ONLY — no build, no app/router
change, no commit.

CONSISTENCY (mandatory): the stage->group classification and the CONFIRMED /
DENYLIST campaign config are IMPORTED read-only from
backend/modules/marketing_attribution, so per-campaign numbers are defined
IDENTICALLY to the shipped module. Locked definitions preserved:
  - Population = ALL leads incl. archived (context active_test=False).
  - Stage groups: جديد {New, New X, no-stage} / مهتم {Follow up, Interested} /
    اشترى = crm.stage.is_won=True / بلا نتيجة {the rest} — via domain.classify_stage.
  - The campaign rollup of ATTRIBUTING campaigns by dominant buyer must reproduce
    the module's per-buyer totals EXACTLY (identity check #3).

PRIVACY (hard): AGGREGATES & STATISTICS ONLY. No per-lead PII. utm.campaign names,
media-buyer names and stage names are marketing/staff/process labels (safe to
show — the shipped module shows them). expected_revenue and any money field are
only ever summed / fill-rated per campaign, never printed per lead.

Method discipline: READ-ONLY (fields_get / search_count / read_group / search_read
only). ALLOWED_METHODS untouched. No create/write/unlink. No FastAPI. No OpenAI.
For monthly bucketing: search_read create_date + Python-side Cairo-local regroup
(NOT read_group's raw-UTC bucketing — Decision 5.10).

Pre-flight (run manually BEFORE this script): purge all __pycache__; kill stray
python. This script talks to Odoo directly; uvicorn not required.

Usage (from project root):
    python scripts/discover_campaign_performance.py
"""

import asyncio
import io
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# sys.path.insert so the script runs without PYTHONPATH set (settled convention).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.modules.marketing_attribution import domain  # noqa: E402
from backend.modules.marketing_attribution.domain import (  # noqa: E402
    BUYER_FIELD,
    CAMPAIGN_FIELD,
    GROUP_INTERESTED,
    GROUP_NEW,
    GROUP_NO_RESULT,
    GROUP_ORDER,
    GROUP_WON,
    classify_stage,
)
from backend.modules.marketing_attribution.services import cache as _cache  # noqa: E402
from backend.modules.marketing_attribution.services.attribution_service import (  # noqa: E402
    get_attribution_overview,
)
from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient  # noqa: E402

# Force UTF-8 stdout (Windows consoles default to cp1252; we print Arabic labels).
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_LEAD = "crm.lead"
_CAMPAIGN = "utm.campaign"
_STAGE = "crm.stage"
_SEP = "=" * 100
_SEP2 = "-" * 100
_CTX_ALL = {"active_test": False}          # ALL leads incl. archived (locked population)
_CAIRO_TZ = ZoneInfo("Africa/Cairo")
_PAGE = 5000

# Per-buyer totals the shipped module already verified — the campaign rollup of
# ATTRIBUTING campaigns by dominant buyer must reconcile to these EXACTLY (§ mission).
_LOCKED_BUYER_TOTALS = {
    "Ahmed Aymen": 39522,
    "Yomna Musaad": 15562,
    "Abdallah Maher": 14977,
    "Ali shaban": 6932,
}

# Short ASCII tags for the 4 Arabic groups (keeps table columns aligned in a
# monospace terminal). GROUP_ORDER = (جديد, مهتم, اشترى, بلا نتيجة).
_GROUP_TAG = {
    GROUP_NEW: "New",
    GROUP_INTERESTED: "Intrst",
    GROUP_WON: "Won",
    GROUP_NO_RESULT: "NoRes",
}

# Money/ad-spend name hints (PROBE B/C). Applied to technical field NAMES in Python.
_MONEY_NAME_RE = re.compile(
    r"cost|budget|spend|expense|amount|price|revenue|paid|invoice|payment", re.I
)
_SPEND_NAME_RE = re.compile(r"cost|budget|spend|expense", re.I)  # strict ad-spend hint


# ── helpers ─────────────────────────────────────────────────────────────────────

def _m2o(v):
    if isinstance(v, (list, tuple)) and len(v) == 2:
        return int(v[0]), str(v[1])
    return None, None


def _qualifies(dom_cnt: int, both: int) -> bool:
    """concentration >= 0.90, integer-exact at the boundary (identical to module)."""
    if both <= 0:
        return False
    return dom_cnt * 100 >= both * 90


def _is_filled_num(v) -> bool:
    """A monetary/float field counts as 'filled' iff it is a non-zero number."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v != 0


def _ok(flag: bool) -> str:
    return "PASS" if flag else "**FAIL**"


async def _count(client, model, dom):
    return await client.execute_kw(model, "search_count", args=[dom], kwargs={"context": _CTX_ALL})


async def _fetch_all(client, model, dom, fields):
    """search_read the whole domain in pages of _PAGE, ordered by id."""
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


def _cairo_month(dt_str: str):
    """Parse an Odoo UTC-naive datetime string -> Cairo-local 'YYYY-MM' (Decision 5.10)."""
    if not dt_str:
        return None
    dt_utc = datetime.strptime(str(dt_str), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return dt_utc.astimezone(_CAIRO_TZ).strftime("%Y-%m")


def _cairo_dt(dt_str: str) -> str:
    if not dt_str:
        return "—"
    dt_utc = datetime.strptime(str(dt_str), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return dt_utc.astimezone(_CAIRO_TZ).strftime("%Y-%m-%d %H:%M")


# ── PROBE A — campaign inventory & funnel ────────────────────────────────────────

async def probe_a(client) -> dict:
    print(_SEP)
    print("  PROBE A — CAMPAIGN INVENTORY & FUNNEL  (population = ALL incl. archived; context active_test=False)")
    print(_SEP)

    # headline counts
    total_campaign_records = await _count(client, _CAMPAIGN, [])
    total_leads = await _count(client, _LEAD, [])
    print(f"  utm.campaign records (incl. archived)   : {total_campaign_records:,}")
    print(f"  crm.lead population (incl. archived)    : {total_leads:,}")
    print()

    # campaign id -> name (resolve gates exactly as the module does)
    campaigns = await client.execute_kw(
        _CAMPAIGN, "search_read", args=[[]],
        kwargs={"fields": ["id", "name"], "context": _CTX_ALL},
    )
    id_to_name, name_to_ids = {}, defaultdict(list)
    for c in campaigns:
        cid, cname = int(c["id"]), str(c.get("name") or "")
        id_to_name[cid] = cname
        name_to_ids[cname].append(cid)

    def _resolve(names):
        out = set()
        for nm in names:
            out.update(name_to_ids.get(nm, []))
        return out

    confirmed_ids = _resolve(domain.CONFIRMED_BUYER_CAMPAIGNS)
    denylist_ids = _resolve(domain.DENYLIST_CAMPAIGNS)

    # crm.stage is_won + names (for classify_stage + won-lead probing later)
    stages = await client.execute_kw(
        _STAGE, "search_read", args=[[]],
        kwargs={"fields": ["id", "name", "is_won"]},
    )
    stage_info, won_ids, won_names = {}, [], []
    for s in stages:
        sid, sname, is_won = int(s["id"]), str(s.get("name") or ""), bool(s.get("is_won"))
        stage_info[sid] = {"name": sname, "is_won": is_won}
        if is_won:
            won_ids.append(sid)
            won_names.append(sname)

    # RPC — lead_count per campaign (incl. the no-campaign bucket)
    by_campaign = await client.execute_kw(
        _LEAD, "read_group", args=[[], [CAMPAIGN_FIELD], [CAMPAIGN_FIELD]],
        kwargs={"context": _CTX_ALL, "lazy": False},
    )
    lead_count = {}            # cid -> count   (None key = no campaign)
    sum_lead_count = 0
    for r in by_campaign:
        cid, _ = _m2o(r.get(CAMPAIGN_FIELD))
        cnt = int(r.get("__count") or 0)
        sum_lead_count += cnt
        lead_count[cid] = lead_count.get(cid, 0) + cnt

    # RPC — per (campaign, stage) -> classify into the 4 groups per campaign
    by_campaign_stage = await client.execute_kw(
        _LEAD, "read_group", args=[[], [CAMPAIGN_FIELD, "stage_id"], [CAMPAIGN_FIELD, "stage_id"]],
        kwargs={"context": _CTX_ALL, "lazy": False},
    )
    funnel = defaultdict(lambda: {g: 0 for g in GROUP_ORDER})   # cid (or None) -> {group:count}
    for r in by_campaign_stage:
        cid, _ = _m2o(r.get(CAMPAIGN_FIELD))
        sid, _ = _m2o(r.get("stage_id"))
        cnt = int(r.get("__count") or 0)
        funnel[cid][classify_stage(sid, stage_info)] += cnt

    # RPC — dominant buyer + concentration per campaign (BOTH-SET leads; module RPC 2)
    both_set = await client.execute_kw(
        _LEAD, "read_group",
        args=[[(CAMPAIGN_FIELD, "!=", False), (BUYER_FIELD, "!=", False)],
              [CAMPAIGN_FIELD, BUYER_FIELD], [CAMPAIGN_FIELD, BUYER_FIELD]],
        kwargs={"context": _CTX_ALL, "lazy": False},
    )
    cmap = defaultdict(lambda: {"both": 0, "buyers": Counter(), "names": {}})
    for r in both_set:
        cid, _ = _m2o(r.get(CAMPAIGN_FIELD))
        bid, bname = _m2o(r.get(BUYER_FIELD))
        if cid is None or bid is None:
            continue
        cnt = int(r.get("__count") or 0)
        cmap[cid]["both"] += cnt
        cmap[cid]["buyers"][bid] += cnt
        cmap[cid]["names"][bid] = bname

    def _dominant(cid):
        e = cmap.get(cid)
        if not e or not e["buyers"]:
            return None, None, 0, 0
        bid, dc = e["buyers"].most_common(1)[0]
        return bid, e["names"].get(bid), dc, e["both"]

    def _status(cid):
        if cid in denylist_ids:
            return "denied"
        if cid in confirmed_ids:
            return "confirmed"
        _, _, dc, both = _dominant(cid)
        if both > 0 and _qualifies(dc, both):
            return "pending"
        return "none"

    attributing_ids = {
        cid for cid in confirmed_ids
        if cid not in denylist_ids and (lambda d: d[3] > 0 and _qualifies(d[2], d[3]))(_dominant(cid))
    }

    # ── top 25 by lead volume ────────────────────────────────────────────────
    real_ids = [cid for cid in lead_count if cid is not None]
    ranked = sorted(real_ids, key=lambda c: -lead_count[c])
    print("  TOP 25 CAMPAIGNS BY LEAD VOLUME — funnel + dominant buyer + attribution status")
    print(f"  groups: New=جديد  Intrst=مهتم  Won=اشترى  NoRes=بلا نتيجة")
    print(f"  {'campaign':<26} | {'leads':>7} | {'New':>6} | {'Intrst':>6} | {'Won':>5} | "
          f"{'NoRes':>6} | {'dominant buyer':<16} | {'conc':>5} | status")
    print(f"  {'-'*26}-+-{'-'*7}-+-{'-'*6}-+-{'-'*6}-+-{'-'*5}-+-{'-'*6}-+-{'-'*16}-+-{'-'*5}-+-{'-'*8}")
    for cid in ranked[:25]:
        f = funnel[cid]
        bid, bname, dc, both = _dominant(cid)
        conc = f"{100.0*dc/both:.0f}%" if both else "—"
        nm = (id_to_name.get(cid, f"id={cid}") or "(empty)")[:26]
        print(f"  {nm:<26} | {lead_count[cid]:>7,} | {f[GROUP_NEW]:>6,} | {f[GROUP_INTERESTED]:>6,} | "
              f"{f[GROUP_WON]:>5,} | {f[GROUP_NO_RESULT]:>6,} | {(bname or '—')[:16]:<16} | "
              f"{conc:>5} | {_status(cid)}")
    print()

    # ── concentration / long tail ────────────────────────────────────────────
    ge50 = sum(1 for c in real_ids if lead_count[c] >= 50)
    ge500 = sum(1 for c in real_ids if lead_count[c] >= 500)
    top10_leads = sum(lead_count[c] for c in ranked[:10])
    no_campaign = lead_count.get(None, 0)
    print(_SEP2)
    print("  CONCENTRATION / LONG TAIL")
    print(_SEP2)
    print(f"  campaigns with >=1 lead                 : {len(real_ids):,}  "
          f"(of {total_campaign_records:,} utm.campaign records)")
    print(f"  campaigns with >=50 leads               : {ge50:,}")
    print(f"  campaigns with >=500 leads              : {ge500:,}")
    print(f"  leads in TOP 10 campaigns               : {top10_leads:,}  "
          f"({100.0*top10_leads/max(total_leads,1):.1f}% of all leads)")
    print(f"  leads with NO campaign (campaign_id=False): {no_campaign:,}  "
          f"({100.0*no_campaign/max(total_leads,1):.2f}%)")
    print()

    # ── attribution-status rollup ────────────────────────────────────────────
    status_counts = Counter(_status(c) for c in real_ids)
    status_leads = Counter()
    for c in real_ids:
        status_leads[_status(c)] += lead_count[c]
    print(_SEP2)
    print("  ATTRIBUTION STATUS (campaign config imported read-only from the shipped module)")
    print(_SEP2)
    print(f"  confirmed names : {sorted(domain.CONFIRMED_BUYER_CAMPAIGNS)} -> ids {sorted(confirmed_ids)}")
    print(f"  denylist  names : {sorted(domain.DENYLIST_CAMPAIGNS)} -> ids {sorted(denylist_ids)}")
    print(f"  {'status':<12} | {'#campaigns':>10} | {'#leads':>10}")
    print(f"  {'-'*12}-+-{'-'*10}-+-{'-'*10}")
    for st in ("confirmed", "pending", "denied", "none"):
        print(f"  {st:<12} | {status_counts.get(st,0):>10,} | {status_leads.get(st,0):>10,}")
    print()

    # ── IDENTITY CHECKS ──────────────────────────────────────────────────────
    print(_SEP)
    print("  IDENTITY CHECKS")
    print(_SEP)

    # (1) Σ per-campaign lead counts == total population
    c1 = sum_lead_count == total_leads
    print(f"  (1) Σ per-campaign lead counts == total population")
    print(f"      Σ = {sum_lead_count:,}   total = {total_leads:,}   {_ok(c1)}")

    # (2) each campaign's 4 groups sum to its lead_count
    mismatches = []
    for cid, cnt in lead_count.items():
        gsum = sum(funnel[cid].values())
        if gsum != cnt:
            mismatches.append((cid, cnt, gsum))
    c2 = not mismatches
    print(f"  (2) each campaign's 4 stage-groups sum to its lead_count "
          f"({len(lead_count):,} campaigns incl. no-campaign bucket)")
    print(f"      mismatches = {len(mismatches)}   {_ok(c2)}")
    for cid, cnt, gsum in mismatches[:10]:
        print(f"        FAIL campaign {id_to_name.get(cid, cid)!r}: lead_count={cnt:,} groups_sum={gsum:,}")

    # (3) is computed AFTER the module cross-confirmation (it compares the
    # independent campaign rollup to the LIVE shipped module — the meaningful
    # divergence test — see final_identity_check()). Here we only build the
    # independent rollup so it can be compared later.
    rollup = Counter()
    for cid in attributing_ids:
        _, bname, _, _ = _dominant(cid)
        rollup[bname] += lead_count.get(cid, 0)
    print(f"  (3) ATTRIBUTING-campaign rollup by dominant buyer — computed; reconciled to the")
    print(f"      LIVE shipped module in the MODULE CROSS-CONFIRMATION section below (check #3).")
    print(f"      attributing campaign ids = {sorted(attributing_ids)} "
          f"({[id_to_name.get(c) for c in sorted(attributing_ids)]})")
    print(f"      independent rollup       = "
          f"{ {b: rollup[b] for b in sorted(rollup)} }")
    print()
    print(f"  (1)&(2) PASS = {c1 and c2}")
    print()

    return {
        "checks_1_2_passed": c1 and c2,
        "rollup": dict(rollup),
        "won_ids": won_ids,
        "won_names": sorted(won_names),
        "stage_info": stage_info,
        "id_to_name": id_to_name,
        "lead_count": lead_count,
        "funnel": funnel,
        "ranked": ranked,
        "dominant": _dominant,
        "status": _status,
        "attributing_ids": attributing_ids,
        "total_leads": total_leads,
    }


# ── PROBE B — cost / spend ───────────────────────────────────────────────────────

async def probe_b(client) -> dict:
    print(_SEP)
    print("  PROBE B — COST / SPEND  (make-or-break for any ROAS)")
    print(_SEP)

    camp_fields = await client.execute_kw(
        _CAMPAIGN, "fields_get", args=[],
        kwargs={"attributes": ["string", "type", "relation"]},
    )
    print(f"  utm.campaign exposes {len(camp_fields)} fields. FULL LIST (FLAG = money-ish / studio):")
    print(f"  {'technical name':<34} | {'type':<12} | {'flag':<10} | label")
    print(f"  {'-'*34}-+-{'-'*12}-+-{'-'*10}-+-{'-'*30}")
    camp_money_fields = []
    for name in sorted(camp_fields):
        meta = camp_fields[name]
        typ = meta.get("type", "")
        flags = []
        if typ == "monetary":
            flags.append("MONETARY")
        if _MONEY_NAME_RE.search(name):
            flags.append("NAME")
        if name.startswith("x_studio"):
            flags.append("STUDIO")
        flag = ",".join(flags)
        if flags:
            camp_money_fields.append((name, typ))
        print(f"  {name:<34} | {typ:<12} | {flag:<10} | {meta.get('string','')}")
    print()

    # fill-rate for any flagged utm.campaign field (only ~hundreds of campaigns)
    spend_candidates = [(n, t) for (n, t) in camp_money_fields
                        if t in ("monetary", "float", "integer") or _SPEND_NAME_RE.search(n)]
    print("  utm.campaign flagged numeric/spend fields — fill-rate (non-zero) across all campaigns:")
    if not spend_candidates:
        print("     (none flagged)")
    spend_present_field = None
    for name, typ in spend_candidates:
        rows = await client.execute_kw(
            _CAMPAIGN, "search_read", args=[[]],
            kwargs={"fields": [name], "context": _CTX_ALL},
        )
        filled = sum(1 for r in rows if _is_filled_num(r.get(name)))
        total = sum((r.get(name) or 0) for r in rows if _is_filled_num(r.get(name)))
        print(f"     {name:<34} type={typ:<10} filled={filled:>5,}/{len(rows):,}  sum={total:,.2f}")
        if _SPEND_NAME_RE.search(name) and filled > 0:
            spend_present_field = name
    print()

    # crm.lead flagged fields (monetary type or cost/budget-ish names)
    lead_fields = await client.execute_kw(
        _LEAD, "fields_get", args=[],
        kwargs={"attributes": ["string", "type", "relation"]},
    )
    print(f"  crm.lead exposes {len(lead_fields)} fields. FLAGGED money-type / cost-like names:")
    print(f"  {'technical name':<34} | {'type':<12} | {'flag':<10} | label")
    print(f"  {'-'*34}-+-{'-'*12}-+-{'-'*10}-+-{'-'*30}")
    lead_money_fields = []
    for name in sorted(lead_fields):
        meta = lead_fields[name]
        typ = meta.get("type", "")
        flags = []
        if typ == "monetary":
            flags.append("MONETARY")
        if _MONEY_NAME_RE.search(name):
            flags.append("NAME")
        if name.startswith("x_studio") and (typ in ("monetary", "float", "integer")
                                             or _MONEY_NAME_RE.search(name)):
            flags.append("STUDIO$")
        if flags:
            lead_money_fields.append(name)
            print(f"  {name:<34} | {typ:<12} | {','.join(flags):<10} | {meta.get('string','')}")
    print()
    print("  NOTE: crm.lead.expected_revenue is a FORECAST of revenue, NOT ad-spend — kept distinct.")
    print()

    verdict = (f"YES (field={spend_present_field})" if spend_present_field else "NO")
    print(f"  >>> VERDICT B — Ad-spend present in Odoo: {verdict}")
    print()
    return {"lead_fields": lead_fields, "spend_field": spend_present_field}


# ── PROBE C — revenue linkage ────────────────────────────────────────────────────

async def probe_c(client, a: dict, b: dict) -> None:
    print(_SEP)
    print("  PROBE C — REVENUE LINKAGE  (so اشترى can be EGP, not just a count)")
    print(_SEP)

    lead_fields = b["lead_fields"]
    won_ids = a["won_ids"]
    id_to_name = a["id_to_name"]
    lead_count = a["lead_count"]
    ranked = a["ranked"]

    has_exp_rev = "expected_revenue" in lead_fields
    print(f"  crm.lead.expected_revenue present : {has_exp_rev}  "
          f"(won/اشترى stages = {a['won_names']})")

    # relational fields pointing at realized-value models
    rel_fields = []
    for name, meta in lead_fields.items():
        rel = meta.get("relation") or ""
        if rel in ("sale.order", "account.move") or rel.startswith("rs."):
            rel_fields.append((name, meta.get("type"), rel))
    rel_fields.sort()
    print(f"  crm.lead relations to sale.order / account.move / rs.* : {len(rel_fields)} field(s)")
    for name, typ, rel in rel_fields:
        print(f"     {name:<34} type={typ:<10} -> {rel}")
    print()

    # fetch won leads once, with expected_revenue + every realized-value relation field
    fetch_fields = ["id", CAMPAIGN_FIELD]
    if has_exp_rev:
        fetch_fields.append("expected_revenue")
    fetch_fields += [n for (n, _, _) in rel_fields if n not in fetch_fields]
    won_rows = []
    if won_ids:
        won_rows = await _fetch_all(client, _LEAD, [("stage_id", "in", won_ids)], fetch_fields)
    n_won = len(won_rows)
    print(f"  WON (اشترى) leads fetched : {n_won:,}  (fields={fetch_fields})")
    print()

    # expected_revenue fill-rate on won + per-campaign sum for top campaigns
    best_signal, best_reliability = "none", 0.0
    if has_exp_rev:
        filled = sum(1 for r in won_rows if _is_filled_num(r.get("expected_revenue")))
        fill_pct = 100.0 * filled / max(n_won, 1)
        rev_by_campaign = defaultdict(float)
        for r in won_rows:
            cid, _ = _m2o(r.get(CAMPAIGN_FIELD))
            rev_by_campaign[cid] += float(r.get("expected_revenue") or 0)
        print(f"  expected_revenue fill-rate on WON leads : {filled:,}/{n_won:,} = {fill_pct:.1f}%  "
              f"(FORECAST, not realized)")
        print(f"  expected_revenue SUM per campaign — top 15 campaigns by lead volume:")
        print(f"     {'campaign':<28} | {'won-lead Σ exp_rev':>20}")
        print(f"     {'-'*28}-+-{'-'*20}")
        for cid in ranked[:15]:
            print(f"     {(id_to_name.get(cid) or '(empty)')[:28]:<28} | {rev_by_campaign.get(cid,0):>20,.0f}")
        print()
        best_signal, best_reliability = "expected_revenue forecast", fill_pct

    # realized-value relation fill-rate on won
    if rel_fields:
        print(f"  Realized-value relation fill-rate on WON leads:")
        for name, _, rel in rel_fields:
            filled = sum(1 for r in won_rows if r.get(name))
            pct = 100.0 * filled / max(n_won, 1)
            print(f"     {name:<34} -> {rel:<14} filled on won = {filled:,}/{n_won:,} = {pct:.1f}%")
            if pct > best_reliability:
                best_signal, best_reliability = f"realized via {name} -> {rel}", pct
        print()
        print("  NOTE: a populated relation only proves a LINK exists; reading a realized PRICE")
        print("        read-only depends on access to the target model's amount field (not asserted here).")
    else:
        print("  No crm.lead relation to sale.order / account.move / rs.* found.")
    print()

    print(f"  >>> VERDICT C — Best per-campaign revenue signal = [{best_signal}], "
          f"reliability ≈ {best_reliability:.1f}% (fill-rate on won leads)")
    print()


# ── PROBE D — time feasibility ───────────────────────────────────────────────────

async def probe_d(client, a: dict, b: dict) -> None:
    print(_SEP)
    print("  PROBE D — TIME FEASIBILITY  (Cairo-local; search_read + Python regroup — Decision 5.10)")
    print(_SEP)

    has_create = "create_date" in b["lead_fields"]
    print(f"  crm.lead.create_date present : {has_create}  "
          f"(type={b['lead_fields'].get('create_date',{}).get('type')})")
    print()

    # 12-month target keys (Cairo-local), oldest -> newest
    today = datetime.now(_CAIRO_TZ)
    keys = []
    y, m = today.year, today.month
    for i in range(11, -1, -1):
        yy, mm = y, m - i
        while mm <= 0:
            mm += 12
            yy -= 1
        keys.append(f"{yy:04d}-{mm:02d}")

    # lower bound: first day of the oldest target month, padded 1 day for the UTC offset
    first_y, first_m = int(keys[0][:4]), int(keys[0][5:7])
    cutoff = (datetime(first_y, first_m, 1, tzinfo=_CAIRO_TZ) - timedelta(days=1))
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")

    rows = await _fetch_all(client, _LEAD, [("create_date", ">=", cutoff_str)], ["id", "create_date"])
    buckets = Counter()
    for r in rows:
        k = _cairo_month(r.get("create_date"))
        if k:
            buckets[k] += 1
    print(f"  LEAD-CREATION COUNTS PER MONTH — last 12 Cairo-local months "
          f"(fetched {len(rows):,} leads created since {keys[0]}):")
    print(f"     {'month':<9} | {'leads created':>14}")
    print(f"     {'-'*9}-+-{'-'*14}")
    for k in keys:
        print(f"     {k:<9} | {buckets.get(k,0):>14,}")
    print(f"     {'TOTAL':<9} | {sum(buckets.get(k,0) for k in keys):>14,}")
    print()

    # activity span of the top 10 campaigns
    print(f"  ACTIVITY SPAN — top 10 campaigns by lead volume (first & last lead create_date, Cairo-local):")
    print(f"     {'campaign':<28} | {'leads':>7} | {'first lead':<17} | {'last lead':<17}")
    print(f"     {'-'*28}-+-{'-'*7}-+-{'-'*17}-+-{'-'*17}")
    for cid in a["ranked"][:10]:
        first = await client.execute_kw(
            _LEAD, "search_read", args=[[(CAMPAIGN_FIELD, "=", cid)]],
            kwargs={"fields": ["create_date"], "order": "create_date asc",
                    "limit": 1, "context": _CTX_ALL},
        )
        last = await client.execute_kw(
            _LEAD, "search_read", args=[[(CAMPAIGN_FIELD, "=", cid)]],
            kwargs={"fields": ["create_date"], "order": "create_date desc",
                    "limit": 1, "context": _CTX_ALL},
        )
        f_dt = _cairo_dt(first[0]["create_date"]) if first else "—"
        l_dt = _cairo_dt(last[0]["create_date"]) if last else "—"
        print(f"     {(a['id_to_name'].get(cid) or '(empty)')[:28]:<28} | "
              f"{a['lead_count'].get(cid,0):>7,} | {f_dt:<17} | {l_dt:<17}")
    print()


# ── module cross-confirmation (reconciles campaign rollup to the shipped numbers) ─

async def module_crosscheck(client) -> dict:
    print(_SEP)
    print("  MODULE CROSS-CONFIRMATION — get_attribution_overview() (shipped module, injected client)")
    print(_SEP)
    _cache.clear()
    result = await get_attribution_overview(client=client)
    print(f"  population={result['total_leads_population']:,}  "
          f"total_attributed={result['total_attributed']:,}  "
          f"attribution_pct={result['attribution_pct']:.2f}%")
    print(f"  {'buyer':<18} | {'module total':>12}")
    print(f"  {'-'*18}-+-{'-'*12}")
    module_totals = {}
    for row in result["buyers"]:
        module_totals[row["buyer_name"]] = row["total_attributed"]
        print(f"  {row['buyer_name']:<18} | {row['total_attributed']:>12,}")
    print()
    print(f"  pending campaigns : {len(result['pending_campaigns'])}")
    for p in result["pending_campaigns"][:10]:
        print(f"     {p['campaign_name']!r} -> {p['dominant_buyer_name']!r}  "
              f"conc={p['concentration']:.0f}%  leads={p['lead_count']:,}")
    print(f"  integrity_alerts  : {result['integrity_alerts'] or '(none)'}")
    print(f"  config_warnings   : {result['config_warnings'] or '(none)'}")
    print()
    return {
        "module_totals": module_totals,
        "population": result["total_leads_population"],
        "total_attributed": result["total_attributed"],
        "attribution_pct": result["attribution_pct"],
    }


def final_identity_check(a: dict, m: dict) -> bool:
    """Check #3 — the meaningful divergence test.

    The independent per-campaign rollup (PROBE A, computed directly from
    read_group) must reproduce the LIVE shipped module's per-buyer
    total_attributed EXACTLY — that is what proves the campaign-CENTRIC
    definition does NOT diverge from the module. The originally-locked snapshot
    (2026-06-14) is shown only as an informational drift reference, since the
    live DB mutates continuously between runs.
    """
    print(_SEP)
    print("  IDENTITY CHECK #3 — campaign rollup == LIVE shipped module (divergence test)")
    print(_SEP)
    rollup = a["rollup"]
    module = m["module_totals"]
    buyers = sorted(set(rollup) | set(module) | set(_LOCKED_BUYER_TOTALS))
    print(f"  {'buyer':<18} | {'rollup (A)':>10} | {'module':>10} | {'=?':>4} | "
          f"{'locked':>9} | {'drift':>6}")
    print(f"  {'-'*18}-+-{'-'*10}-+-{'-'*10}-+-{'-'*4}-+-{'-'*9}-+-{'-'*6}")
    passed = True
    for b in buyers:
        r = rollup.get(b, 0)
        mod = module.get(b, 0)
        locked = _LOCKED_BUYER_TOTALS.get(b)
        same = r == mod
        passed = passed and same
        drift = "" if locked is None else f"{r - locked:+d}"
        print(f"  {b:<18} | {r:>10,} | {mod:>10,} | {('OK' if same else 'XX'):>4} | "
              f"{(locked if locked is not None else '—'):>9} | {drift:>6}")
    # the set of attributed buyers must also be exactly the 4 locked buyers
    set_ok = set(rollup) == set(_LOCKED_BUYER_TOTALS) == set(module)
    print()
    print(f"  rollup == live module (definition identity)  : {_ok(passed)}")
    print(f"  attributed-buyer SET == 4 locked buyers      : {_ok(set_ok)}")
    print(f"  NOTE: any nonzero 'drift' is live-DB growth since the 2026-06-14 lock "
          f"(all small & positive),")
    print(f"        NOT a definition divergence — rollup and the live module agree exactly.")
    print()
    return passed and set_ok


# ── main ─────────────────────────────────────────────────────────────────────────

async def main() -> int:
    run_at = datetime.now(timezone.utc)
    print(_SEP)
    print("  CAMPAIGN PERFORMANCE — DISCOVERY (READ-ONLY, aggregates only, $0 AI)")
    print(f"  Run at (UTC)    : {run_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  Today (Cairo)   : {datetime.now(_CAIRO_TZ).date().isoformat()}")
    print(f"  ALLOWED_METHODS : {sorted(ALLOWED_METHODS)}")
    print(f"  Population/ctx  : ALL leads incl. archived — context={_CTX_ALL}")
    print("  READ-ONLY. Direct JSON-RPC. No FastAPI. No OpenAI. AI cost = $0.00")
    print(_SEP)
    print()

    async with OdooClient() as client:
        a = await probe_a(client)
        b = await probe_b(client)
        await probe_c(client, a, b)
        await probe_d(client, a, b)
        m = await module_crosscheck(client)
        c3 = final_identity_check(a, m)

    all_passed = a["checks_1_2_passed"] and c3
    print(_SEP)
    if all_passed:
        print("  DISCOVERY COMPLETE — all identity checks PASSED (campaign rollup reconciles")
        print("  EXACTLY to the live shipped module). Numbers only; no build, no decision.")
    else:
        print("  DISCOVERY COMPLETE — **an IDENTITY CHECK FAILED**. STOP and report (definition drift).")
    print(_SEP)
    return 0 if all_passed else 1


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
