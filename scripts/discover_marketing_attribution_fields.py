"""
scripts/discover_marketing_attribution_fields.py — Marketing Attribution
FIELD-RESOLUTION discovery (READ-ONLY).

Purpose (numbers only — NO build, NO attribution logic, NO product decision):
  (1) Bind the UI labels "Media Buyer" and the Media-Buying "Campaign Name" to
      their exact technical fields, with fill rates over the FULL lead population.
  (2) Produce the complete ordered crm.stage list with per-stage lead counts,
      including lost/inactive (archived) leads.

Triangulation anchor (known UI values on the real lead "Mostafa Marzooq",
email mostafa_marzooq@hotmail.com):
      Media Buyer                  = (EMPTY)
      Media Buyer Manager          = (EMPTY)
      Adset Name                   = "New Leads ad set"
      Campaign Name (Media Buying) = "YM-GCC ABO LAVERDE"
      Campaign (Marketing)         = "Outsource-Y"
  => the candidate field whose value on this lead == "YM-GCC ABO LAVERDE" IS the
     Media-Buying Campaign Name field; the field == "Outsource-Y" is the standard
     Marketing Campaign (utm.campaign).

Method discipline:
  - READ-ONLY: only fields_get / search_count / read_group / search_read.
    ALLOWED_METHODS untouched. No create/write/unlink.
  - Every count passes context={'active_test': False} so archived/lost leads are
    included; the active vs inactive split uses explicit ('active','=',T/F).
  - No FastAPI. No OpenAI. AI cost = $0.00.
  - Each figure prints the exact domain + context used, so it is reproducible.

Usage (from project root; uvicorn NOT required — talks to Odoo directly):
    python scripts/discover_marketing_attribution_fields.py
"""

import asyncio
import io
import sys
from datetime import datetime, timezone
from pathlib import Path

# sys.path.insert so the script runs without PYTHONPATH set (settled convention).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient  # noqa: E402

# Force UTF-8 stdout (Windows consoles default to cp1252).
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_MODEL = "crm.lead"
_STAGE_MODEL = "crm.stage"
_SEP = "=" * 100
_SEP2 = "-" * 100

# Substrings used to flag candidate fields in the fields_get scan (Step 1).
_SCAN_SUBSTRINGS = ("media", "buyer", "campaign", "adset", "utm")

# active_test=False context — the population for every count is ALL leads
# (active + archived). Passed inside kwargs on every call below.
_CTX_ALL = {"active_test": False}

# Known UI anchors on the Mostafa Marzooq lead (for triangulation/verification).
_SPOT_NAME = "Mostafa Marzooq"
_SPOT_EMAIL = "mostafa_marzooq@hotmail.com"
_EXPECT_MEDIA_BUYING_CAMPAIGN = "YM-GCC ABO LAVERDE"
_EXPECT_MARKETING_CAMPAIGN = "Outsource-Y"


# ── small helpers ─────────────────────────────────────────────────────────────

def _m2o_name(v):
    """Render an Odoo many2one value [id, name] (or False) as a display string."""
    if isinstance(v, (list, tuple)) and len(v) == 2:
        return str(v[1])
    return ""


def _val_str(v):
    """Display string for any field value; '(empty)' for Odoo falsey/empty."""
    if v is False or v is None or v == "":
        return "(empty)"
    if isinstance(v, (list, tuple)) and len(v) == 2:
        return f"{v[1]!r} (id={v[0]})"
    return repr(v)


def _or_domain(preds):
    """Build an OR domain (Polish notation) from a list of leaf predicates."""
    preds = [p for p in preds if p]
    if not preds:
        return None
    return ["|"] * (len(preds) - 1) + list(preds)


async def _count(client, domain):
    """search_count over the FULL population (active_test=False)."""
    return await client.execute_kw(
        _MODEL, "search_count", args=[domain], kwargs={"context": _CTX_ALL}
    )


# ── Step 1 — field scan ───────────────────────────────────────────────────────

async def _scan_fields(client):
    fields = await client.execute_kw(
        _MODEL, "fields_get", args=[],
        kwargs={"attributes": ["string", "type", "relation"]},
    )

    print(_SEP)
    print("  STEP 1 — FIELD SCAN  (fields_get on crm.lead)")
    print(f"  match rule: technical name OR string label contains any of "
          f"{list(_SCAN_SUBSTRINGS)}")
    print(_SEP)
    print(f"  crm.lead exposes {len(fields)} fields total.")
    print()
    print(f"  {'technical name':<36} | {'ttype':<10} | {'comodel':<16} | string label")
    print(f"  {'-'*36}-+-{'-'*10}-+-{'-'*16}-+-{'-'*28}")

    matched = []
    for name in sorted(fields):
        meta = fields[name]
        label = (meta.get("string") or "")
        hay = (name + " " + label).lower()
        if any(s in hay for s in _SCAN_SUBSTRINGS):
            matched.append(name)
            print(f"  {name:<36} | {meta.get('type',''):<10} | "
                  f"{(meta.get('relation') or ''):<16} | {label!r}")
    print()
    print(f"  -> {len(matched)} fields matched the scan substrings (raw rows above).")
    print()
    return fields


def _classify_candidates(fields):
    """Split scanned fields into buyer / manager / campaign candidate groups."""
    def _hay(n):
        return (n + " " + (fields[n].get("string") or "")).lower()

    buyer_all = sorted(n for n in fields if "buyer" in _hay(n))
    mb_primary = [n for n in buyer_all if "manager" not in _hay(n)]
    mb_manager = [n for n in buyer_all if n not in mb_primary]
    campaign = sorted(n for n in fields if "campaign" in _hay(n))
    return {
        "buyer_all": buyer_all,
        "mb_primary": mb_primary,
        "mb_manager": mb_manager,
        "campaign": campaign,
    }


# ── Step 2 — explicit existence of the two named media-buyer fields ───────────

def _explicit_existence(fields):
    print(_SEP)
    print("  STEP 2 — EXPLICIT EXISTENCE: media_buyer_id vs direct_media_buyer_id")
    print(_SEP)
    for name in ("media_buyer_id", "direct_media_buyer_id"):
        meta = fields.get(name)
        if meta:
            print(f"  {name:<26} EXISTS  type={meta.get('type','')!s:<10} "
                  f"comodel={meta.get('relation') or '-'!s:<12} label={meta.get('string')!r}")
        else:
            print(f"  {name:<26} !! DOES NOT EXIST on crm.lead")
    print()


# ── fill-rate helper (Step 4) ─────────────────────────────────────────────────

async def _fill_rate(client, field, total, is_m2o):
    """set_count / total_count / % over active_test=False; top-10 for m2o."""
    set_domain = [(field, "!=", False)]
    set_count = await _count(client, set_domain)
    pct = 100 * set_count / max(total, 1)
    top = []
    if is_m2o:
        rg = await client.execute_kw(
            _MODEL, "read_group",
            args=[set_domain, [field], [field]],
            kwargs={"context": _CTX_ALL, "lazy": False},
        )
        rg.sort(key=lambda r: -(r.get("__count") or 0))
        for r in rg[:10]:
            top.append((_m2o_name(r.get(field)) or "(no name)", r.get("__count") or 0))
    return set_count, pct, top


# ── Verification — Mostafa spot-check + UI-binding exemplars (Addition 1) ──────

async def _spot_check(client, groups):
    print(_SEP)
    print("  VERIFICATION A — SPOT-CHECK lead 'Mostafa Marzooq' (proves we read UI data)")
    print(_SEP)

    fetch_fields = (["id", "name", "adset_name"]
                    + groups["campaign"] + groups["buyer_all"])
    fetch_fields = list(dict.fromkeys(fetch_fields))  # de-dupe, keep order

    rows = await client.execute_kw(
        _MODEL, "search_read",
        args=[[("name", "=", _SPOT_NAME)]],
        kwargs={"fields": fetch_fields + ["email_from"], "context": _CTX_ALL,
                "limit": 10, "order": "id"},
    )
    spot = None
    if len(rows) == 1:
        spot = rows[0]
    elif len(rows) > 1:
        for r in rows:
            if str(r.get("email_from") or "").strip().lower() == _SPOT_EMAIL:
                spot = r
                break
        spot = spot or rows[0]

    if not spot:
        print(f"  !! No lead named {_SPOT_NAME!r} found — cannot verify. FLAG.")
        print()
        return None

    print(f"  matched lead id={spot['id']}  (disambiguated by email if needed)")
    print(f"  name (as given by Khaled in the mission) = {_SPOT_NAME!r}")
    print()
    print("  candidate CAMPAIGN field values on this lead:")
    for f in groups["campaign"]:
        print(f"     {f:<28} = {_val_str(spot.get(f))}")
    print(f"     {'adset_name':<28} = {_val_str(spot.get('adset_name'))}")
    print()
    print("  candidate MEDIA-BUYER / MANAGER field values on this lead:")
    for f in groups["buyer_all"]:
        print(f"     {f:<28} = {_val_str(spot.get(f))}")
    print()

    # Assertions against the known UI anchors.
    mb_all_empty = all(not spot.get(f) for f in groups["mb_primary"])
    cn_field = None
    for f in groups["campaign"]:
        v = spot.get(f)
        if isinstance(v, str) and v.strip() == _EXPECT_MEDIA_BUYING_CAMPAIGN:
            cn_field = f
    print(f"  ASSERT Media Buyer empty on this lead        : "
          f"{'PASS' if mb_all_empty else 'FAIL'}")
    print(f"  ASSERT a field == {_EXPECT_MEDIA_BUYING_CAMPAIGN!r}     : "
          f"{'PASS (field=' + cn_field + ')' if cn_field else 'FAIL'}")
    print()
    return spot


async def _ui_binding_exemplars(client, groups):
    print(_SEP)
    print("  VERIFICATION B — UI-BINDING EXEMPLARS (Addition 1: decisive browser check)")
    print(_SEP)

    two = [f for f in ("media_buyer_id", "direct_media_buyer_id") if f in groups["buyer_all"]]
    if not two:
        print("  !! Neither media_buyer_id nor direct_media_buyer_id exists — skip.")
        print()
        return []

    pool_domain = _or_domain([(f, "!=", False) for f in two])
    fetch_fields = ["id", "name"] + groups["buyer_all"]
    fetch_fields = list(dict.fromkeys(fetch_fields))
    pool = await client.execute_kw(
        _MODEL, "search_read",
        args=[pool_domain],
        kwargs={"fields": fetch_fields, "context": _CTX_ALL, "limit": 400, "order": "id"},
    )
    print(f"  pool domain (>=1 of the two set) = {pool_domain}  context={_CTX_ALL}")
    print(f"  pool fetched = {len(pool):,} rows (capped at 400 for ranking)")

    def _id_of(r, f):
        v = r.get(f)
        return v[0] if isinstance(v, (list, tuple)) and len(v) == 2 else None

    decisive, both_same, single = [], [], []
    for r in pool:
        a = _id_of(r, "media_buyer_id") if "media_buyer_id" in two else None
        b = _id_of(r, "direct_media_buyer_id") if "direct_media_buyer_id" in two else None
        if a and b and a != b:
            decisive.append(r)
        elif a and b:
            both_same.append(r)
        else:
            single.append(r)

    chosen = (decisive + both_same + single)[:3]
    print(f"  ranking: {len(decisive)} decisive (two fields differ), "
          f"{len(both_same)} both-set-equal, {len(single)} single-set in pool")
    print()
    for r in chosen:
        print(f"  exemplar lead id={r['id']}  name={r.get('name')!r}")
        for f in groups["buyer_all"]:
            print(f"       {f:<28} = {_val_str(r.get(f))}")
        print()
    return chosen


# ── Step 3 — resolve campaign fields from the spot-check ──────────────────────

def _resolve_campaign(spot, groups):
    print(_SEP)
    print("  STEP 3 — CAMPAIGN FIELD RESOLUTION (Media-Buying 'Campaign Name' vs std 'Campaign')")
    print(_SEP)

    cn_field = None      # Media-Buying Campaign Name (== 'YM-GCC ABO LAVERDE')
    mkt_field = None     # standard Marketing Campaign (== 'Outsource-Y')
    if spot:
        for f in groups["campaign"]:
            v = spot.get(f)
            sval = v[1] if isinstance(v, (list, tuple)) and len(v) == 2 else v
            if isinstance(sval, str):
                if sval.strip() == _EXPECT_MEDIA_BUYING_CAMPAIGN:
                    cn_field = f
                if sval.strip() == _EXPECT_MARKETING_CAMPAIGN:
                    mkt_field = f

    print(f"  By triangulation against the Mostafa Marzooq anchor values:")
    print(f"     Media-Buying 'Campaign Name' (== {_EXPECT_MEDIA_BUYING_CAMPAIGN!r}) "
          f"-> {cn_field or '<<UNRESOLVED — FLAG>>'}")
    print(f"     standard 'Campaign'          (== {_EXPECT_MARKETING_CAMPAIGN!r})        "
          f"-> {mkt_field or '<<UNRESOLVED — FLAG>>'}")
    if cn_field is None:
        # Fallback: the conventional char field name, flagged as inferred.
        cn_field = "campaign_name" if "campaign_name" in groups["campaign"] else None
        print(f"     (spot-check did not bind it; falling back to {cn_field!r} by name — FLAG)")
    print()
    return cn_field, mkt_field


# ── Step 4 — fill-rate table for all candidate fields ─────────────────────────

async def _fill_table(client, fields, groups, total, cn_field):
    print(_SEP)
    print("  STEP 4 — FILL RATES over FULL population")
    print(f"  total_count = search_count([])  context={_CTX_ALL}  =>  {total:,}")
    print(f"  set_count   = search_count([(field,'!=',False)])  context={_CTX_ALL}")
    print(_SEP)

    candidates = list(dict.fromkeys(groups["buyer_all"] + groups["campaign"]))
    print(f"  {'field':<32} | {'ttype':<10} | {'set_count':>10} | {'total':>9} | {'%':>6}")
    print(f"  {'-'*32}-+-{'-'*10}-+-{'-'*10}-+-{'-'*9}-+-{'-'*6}")
    m2o_tops = {}
    for f in candidates:
        meta = fields.get(f, {})
        is_m2o = meta.get("type") == "many2one"
        set_count, pct, top = await _fill_rate(client, f, total, is_m2o)
        if is_m2o:
            m2o_tops[f] = top
        tag = ""
        if f == cn_field:
            tag = "  <- resolved Media-Buying Campaign Name"
        print(f"  {f:<32} | {meta.get('type',''):<10} | {set_count:>10,} | "
              f"{total:>9,} | {pct:>5.1f}%{tag}")
    print()

    # Top-10 values for the many2one candidates (internal staff / campaign labels).
    for f, top in m2o_tops.items():
        print(f"  TOP 10 values — {f}  ({len(top)} shown):")
        if not top:
            print("     (field empty across the entire population)")
        for val, n in top:
            print(f"     {n:>9,}  {val}")
        print()
    return candidates


# ── Addition 2 — combined attribution coverage (the design-driving number) ────

async def _combined_coverage(client, total, cn_field):
    print(_SEP)
    print("  ADDITION 2 — COMBINED ATTRIBUTION COVERAGE (UNION = true attributable set)")
    print(f"  population = ALL leads  context={_CTX_ALL}  total={total:,}")
    print(_SEP)

    parts = {
        "media_buyer_id set": [("media_buyer_id", "!=", False)],
        "direct_media_buyer_id set": [("direct_media_buyer_id", "!=", False)],
        f"Media-Buying Campaign Name set ({cn_field})":
            [(cn_field, "!=", False)] if cn_field else None,
    }
    results = {}
    for label, dom in parts.items():
        if dom is None:
            print(f"     {label:<48} = <unresolved field — skip>")
            continue
        n = await _count(client, dom)
        results[label] = n
        print(f"     {label:<48} = {n:>9,}  ({100*n/max(total,1):>5.1f}%)  domain={dom}")
    print()

    union_preds = [("media_buyer_id", "!=", False),
                   ("direct_media_buyer_id", "!=", False)]
    if cn_field:
        union_preds.append((cn_field, "!=", False))
    union_domain = _or_domain(union_preds)
    union_n = await _count(client, union_domain)
    print(f"  >>> UNION (media_buyer_id OR direct_media_buyer_id OR Campaign Name) = "
          f"{union_n:,}  ({100*union_n/max(total,1):.1f}% of {total:,})")
    print(f"      domain={union_domain}  context={_CTX_ALL}")
    print(f"      => this UNION is the true attributable-lead coverage.")
    print()
    return union_n


# ── Step 5 — full ordered stage list ──────────────────────────────────────────

async def _list_stages(client):
    print(_SEP)
    print("  STEP 5 — crm.stage LIST (ordered by sequence)")
    print(_SEP)

    smeta = await client.execute_kw(
        _STAGE_MODEL, "fields_get", args=[], kwargs={"attributes": ["type"]}
    )
    has_is_won = "is_won" in smeta
    has_fold = "fold" in smeta
    fields = ["id", "name", "sequence"]
    if has_is_won:
        fields.append("is_won")
    if has_fold:
        fields.append("fold")

    stages = await client.execute_kw(
        _STAGE_MODEL, "search_read", args=[[]],
        kwargs={"fields": fields, "order": "sequence asc, id asc"},
    )
    print(f"  is_won field present: {has_is_won}   fold field present: {has_fold}")
    print(f"  {len(stages)} stage records:")
    print()
    hdr = f"  {'id':>5} | {'seq':>5} | {'name':<40}"
    if has_is_won:
        hdr += f" | {'is_won':<6}"
    if has_fold:
        hdr += f" | {'fold':<5}"
    print(hdr)
    print(f"  {'-'*5}-+-{'-'*5}-+-{'-'*40}" +
          (f"-+-{'-'*6}" if has_is_won else "") +
          (f"-+-{'-'*5}" if has_fold else ""))
    for s in stages:
        line = f"  {s['id']:>5} | {s.get('sequence',0):>5} | {str(s.get('name','')):<40}"
        if has_is_won:
            line += f" | {str(s.get('is_won','')):<6}"
        if has_fold:
            line += f" | {str(s.get('fold','')):<5}"
        print(line)
    print()
    return stages


# ── Step 6 — per-stage active/inactive/total counts ───────────────────────────

async def _stage_counts(client, stages, total):
    print(_SEP)
    print("  STEP 6 — LEAD COUNT PER STAGE (active / inactive / total)")
    print(f"  total uses context={_CTX_ALL}; active=('active','=',True); "
          f"inactive=('active','=',False) (both with active_test=False)")
    print(_SEP)

    async def _grp(domain):
        rg = await client.execute_kw(
            _MODEL, "read_group",
            args=[domain, ["stage_id"], ["stage_id"]],
            kwargs={"context": _CTX_ALL, "lazy": False},
        )
        out = {}
        for r in rg:
            st = r.get("stage_id")
            sid = st[0] if isinstance(st, (list, tuple)) and len(st) == 2 else None
            out[sid] = r.get("__count") or 0
        return out

    total_by = await _grp([])
    active_by = await _grp([("active", "=", True)])
    inactive_by = await _grp([("active", "=", False)])

    grand_active = await _count(client, [("active", "=", True)])
    grand_inactive = await _count(client, [("active", "=", False)])

    # Ordered rows: documented stages by sequence, then a "(no stage)" bucket.
    ordered = [(s["id"], s.get("name", "")) for s in stages]
    seen = {sid for sid, _ in ordered}
    for sid in total_by:
        if sid not in seen and sid is not None:
            ordered.append((sid, f"<stage id {sid} (not in stage list)>"))
    if None in total_by:
        ordered.append((None, "(no stage)"))

    print(f"  {'stage':<40} | {'active':>9} | {'inactive':>9} | {'total':>9} | {'% grand':>8}")
    print(f"  {'-'*40}-+-{'-'*9}-+-{'-'*9}-+-{'-'*9}-+-{'-'*8}")
    sum_total = 0
    for sid, name in ordered:
        a = active_by.get(sid, 0)
        i = inactive_by.get(sid, 0)
        t = total_by.get(sid, 0)
        if t == 0 and a == 0 and i == 0:
            continue
        sum_total += t
        print(f"  {str(name):<40} | {a:>9,} | {i:>9,} | {t:>9,} | "
              f"{100*t/max(total,1):>7.1f}%")
    print(f"  {'-'*40}-+-{'-'*9}-+-{'-'*9}-+-{'-'*9}-+-{'-'*8}")
    print(f"  {'GRAND TOTAL':<40} | {grand_active:>9,} | {grand_inactive:>9,} | "
          f"{sum_total:>9,} |")
    print()
    print(f"  grand active   (search_count active=True,  active_test=False) = {grand_active:,}")
    print(f"  grand inactive (search_count active=False, active_test=False) = {grand_inactive:,}")
    print(f"  grand total    (search_count [],           active_test=False) = {total:,}")
    print()

    ok_sum = (sum_total == total)
    ok_split = (grand_active + grand_inactive == total)
    print(f"  ASSERT  Σ(per-stage total) == grand total          : "
          f"{'PASS' if ok_sum else 'FAIL'}  ({sum_total:,} vs {total:,})")
    print(f"  ASSERT  grand active + grand inactive == grand tot : "
          f"{'PASS' if ok_split else 'FAIL'}  "
          f"({grand_active:,}+{grand_inactive:,}={grand_active+grand_inactive:,} vs {total:,})")
    print()
    return ordered, total_by, active_by, inactive_by, grand_active, grand_inactive


# ── Step 7 — DECISIONS-NEEDED block ───────────────────────────────────────────

def _decisions_block(fields, groups, cn_field, mkt_field, total, union_n,
                     stages, total_by, active_by, inactive_by,
                     grand_active, grand_inactive, exemplars, fill_lookup):
    print(_SEP)
    print("  ATTRIBUTION DISCOVERY — DECISIONS NEEDED  (paste-ready for chat)")
    print(_SEP)
    print()

    mbid = fill_lookup.get("media_buyer_id", (0, 0.0))
    dmbid = fill_lookup.get("direct_media_buyer_id", (0, 0.0))
    print("  MEDIA-BUYER FIELD (two fields share the label 'Media Buyer'):")
    print(f"     media_buyer_id         : fill {mbid[0]:,} ({mbid[1]:.1f}%)")
    print(f"     direct_media_buyer_id  : fill {dmbid[0]:,} ({dmbid[1]:.1f}%)")
    higher = "media_buyer_id" if mbid[0] >= dmbid[0] else "direct_media_buyer_id"
    print(f"     -> RECOMMENDED primary key (higher coverage): {higher}")
    print(f"        (fill rate alone cannot prove which one the UI 'Media Buyer'")
    print(f"         widget renders — confirm via the UI-binding check below.)")
    print()

    cn_fill = fill_lookup.get(cn_field, (0, 0.0)) if cn_field else (0, 0.0)
    print("  CAMPAIGN FIELDS:")
    print(f"     Media-Buying 'Campaign Name' -> {cn_field or '<UNRESOLVED>'}"
          f"   fill {cn_fill[0]:,} ({cn_fill[1]:.1f}%)")
    print(f"     standard 'Campaign'          -> {mkt_field or '<UNRESOLVED>'}"
          f"   (utm.campaign; the fallback attribution dimension)")
    print()

    print(f"  COMBINED ATTRIBUTABLE COVERAGE (UNION of both MB fields OR Campaign Name):")
    print(f"     {union_n:,} of {total:,} leads  ({100*union_n/max(total,1):.1f}%)  "
          f"<- true attributable-lead coverage")
    print()

    if exemplars:
        e = exemplars[0]
        print("  UI-binding check (manual/browser): open lead "
              f"<{e['id']} — {e.get('name')!r}> in Odoo; whichever field's value the")
        print("     'Media Buyer' widget shows is the bound primary field; map "
              "'Media Buyer Manager' the same way.")
    else:
        print("  UI-binding check: no exemplar with a populated media-buyer field "
              "was found — FLAG.")
    print()

    print(f"  FULL STAGE LIST WITH COUNTS (active / inactive / total; "
          f"context active_test=False):")
    ordered = [(s["id"], s.get("name", "")) for s in stages]
    seen = {sid for sid, _ in ordered}
    for sid in total_by:
        if sid not in seen and sid is not None:
            ordered.append((sid, f"<stage id {sid}>"))
    if None in total_by:
        ordered.append((None, "(no stage)"))
    print(f"     {'stage':<40} | {'active':>9} | {'inactive':>9} | {'total':>9}")
    print(f"     {'-'*40}-+-{'-'*9}-+-{'-'*9}-+-{'-'*9}")
    for sid, name in ordered:
        t = total_by.get(sid, 0)
        if t == 0:
            continue
        print(f"     {str(name):<40} | {active_by.get(sid,0):>9,} | "
              f"{inactive_by.get(sid,0):>9,} | {t:>9,}")
    print(f"     {'-'*40}-+-{'-'*9}-+-{'-'*9}-+-{'-'*9}")
    print(f"     {'GRAND TOTAL':<40} | {grand_active:>9,} | {grand_inactive:>9,} | "
          f"{total:>9,}")
    print()
    print("  (Discovery only — no module built, no attribution logic written, no "
          "product decision made.)")
    print()


# ── main ──────────────────────────────────────────────────────────────────────

async def main():
    run_at = datetime.now(timezone.utc)
    print(_SEP)
    print("  MARKETING ATTRIBUTION — FIELD-RESOLUTION DISCOVERY (READ-ONLY, numbers only)")
    print(f"  Run at (UTC)    : {run_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  Model           : {_MODEL}  /  {_STAGE_MODEL}")
    print(f"  ALLOWED_METHODS : {sorted(ALLOWED_METHODS)}")
    print(f"  Population/ctx  : ALL leads incl. archived — context={_CTX_ALL}")
    print("  READ-ONLY. Direct JSON-RPC. No FastAPI. No OpenAI. AI cost = $0.00")
    print(_SEP)
    print()

    async with OdooClient() as client:
        fields = await _scan_fields(client)
        groups = _classify_candidates(fields)
        _explicit_existence(fields)

        total = await _count(client, [])

        spot = await _spot_check(client, groups)
        exemplars = await _ui_binding_exemplars(client, groups)
        cn_field, mkt_field = _resolve_campaign(spot, groups)

        await _fill_table(client, fields, groups, total, cn_field)

        # Recompute the few fill rates we cite in the decisions block (cheap).
        fill_lookup = {}
        for f in ("media_buyer_id", "direct_media_buyer_id"):
            if f in fields:
                sc, pct, _ = await _fill_rate(client, f, total, is_m2o=True)
                fill_lookup[f] = (sc, pct)
        if cn_field and cn_field in fields:
            is_m2o = fields[cn_field].get("type") == "many2one"
            sc, pct, _ = await _fill_rate(client, cn_field, total, is_m2o)
            fill_lookup[cn_field] = (sc, pct)

        union_n = await _combined_coverage(client, total, cn_field)

        stages = await _list_stages(client)
        (ordered, total_by, active_by, inactive_by,
         grand_active, grand_inactive) = await _stage_counts(client, stages, total)

        _decisions_block(
            fields, groups, cn_field, mkt_field, total, union_n,
            stages, total_by, active_by, inactive_by,
            grand_active, grand_inactive, exemplars, fill_lookup,
        )

    print(_SEP)
    print("  DISCOVERY COMPLETE — numbers only. No build, no attribution logic, no decision.")
    print(_SEP)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except SystemExit:
        raise
    except Exception as exc:
        print(f"\n  FATAL ERROR: {exc}")
        import traceback
        traceback.print_exc()
        raise SystemExit(1)
