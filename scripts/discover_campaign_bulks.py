"""
scripts/discover_campaign_bulks.py — Campaign BULK-identification discovery
(READ-ONLY, $0 AI).

Goal: verify whether lead "bulks" (batch uploads delivered per campaign event) are
RELIABLY IDENTIFIABLE in Odoo, so product can validate the per-bulk-timeline design
BEFORE we build it. DISCOVERY ONLY — no build, no app/router change, no commit.

Business reality (confirmed by Khaled): the CRM was migrated from an old system —
~129K leads were bulk-imported on 2025-11-15 (the "legacy" bucket). Going forward,
social campaigns deliver leads in BATCHES ("bulks") of varying size uploaded to Odoo
per event. We want a per-campaign TIMELINE of these bulks. THIS discovery only
answers: can we identify bulks, how, and are there enough?

CONSISTENCY (mandatory): the stage->group classification and the CONFIRMED / DENYLIST
campaign config are IMPORTED read-only from backend.modules.marketing_attribution,
and the OdooClient + paging/Cairo-regroup patterns are reused from
scripts/discover_campaign_performance.py, so every number is defined IDENTICALLY to
the shipped module and the sibling discovery.

Locked definitions preserved:
  - Population = ALL leads incl. archived (context active_test=False).
  - Cairo-local dates via ZoneInfo("Africa/Cairo"); search_read + Python regroup,
    NOT read_group raw-UTC bucketing (Decision 5.10).

Method discipline: READ-ONLY (fields_get / search_count / search_read only).
ALLOWED_METHODS untouched. No create/write/unlink. No FastAPI. No OpenAI.

Pre-flight (run manually BEFORE this script): purge all __pycache__; kill stray
python. This script talks to Odoo directly; uvicorn not required.

Usage (from project root):
    python scripts/discover_campaign_bulks.py
"""

import asyncio
import io
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# sys.path.insert so the script runs without PYTHONPATH set (settled convention).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.modules.marketing_attribution import domain  # noqa: E402
from backend.modules.marketing_attribution.domain import CAMPAIGN_FIELD  # noqa: E402
from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient  # noqa: E402

# Force UTF-8 stdout (Windows consoles default to cp1252; we print Arabic labels).
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_LEAD = "crm.lead"
_CAMPAIGN = "utm.campaign"
_SEP = "=" * 100
_SEP2 = "-" * 100
_CTX_ALL = {"active_test": False}          # ALL leads incl. archived (locked population)
_CAIRO_TZ = ZoneInfo("Africa/Cairo")
_PAGE = 5000

# ── Tunable thresholds (printed so the verdict is reproducible from the numbers) ──
_LEGACY_DAY_MIN = 10_000   # a Cairo DAY holding >= this many leads is migration-scale
_CLEAR_MIN = 10            # a timestamp shared by >= this many leads = a "clear" bulk
_ENOUGH_BULKS = 3          # need >= this many meaningful bulks for a trend (Probe 4)
_MEANINGFUL_SIZE = 30      # a bulk is "meaningful" at >= this many leads (Probe 4)
_CLEAN_PCT = 80.0          # >= this % of leads in clear clusters -> CLEAN
_PARTIAL_PCT = 40.0        # >= this % -> PARTIAL, else SCATTERED
_CLUSTER_LIST_CAP = 40     # max clear clusters printed per campaign

# Candidate "batch-id" field hint (Probe 3) — applied to technical NAME and label.
_BATCHID_RE = re.compile(
    r"import|batch|upload|external|legacy|migrat|origin|x_studio|x_import|x_batch|"
    r"file|sequence|\bseq\b|\bref\b|reference|source_id|medium_id|referred",
    re.I,
)
# Stage-timing field hint (Probe 5) — feasibility for "conversion at a fixed age".
_TIMING_RE = re.compile(
    r"date_open|date_closed|date_close|date_last_stage_update|date_conversion|"
    r"date_won|date_deadline|day_open|day_close|won|lost|conversion",
    re.I,
)
# Field types light enough to pull on the whole 147K population in the bulk fetch.
_SIMPLE_TYPES = frozenset(
    {"char", "integer", "float", "boolean", "selection", "many2one", "date", "datetime"}
)


# ── helpers (reused/adapted from discover_campaign_performance.py) ────────────────

def _m2o(v):
    if isinstance(v, (list, tuple)) and len(v) == 2:
        return int(v[0]), str(v[1])
    return None, None


def _to_cairo(dt_str: str):
    """Odoo UTC-naive datetime string -> Cairo-local aware datetime (Decision 5.10)."""
    return (
        datetime.strptime(str(dt_str), "%Y-%m-%d %H:%M:%S")
        .replace(tzinfo=timezone.utc)
        .astimezone(_CAIRO_TZ)
    )


def _has(v) -> bool:
    """A field counts as 'filled' iff it is not a falsey/empty value."""
    return not (v is False or v is None or v == "")


def _verdict_word(pct: float) -> str:
    if pct >= _CLEAN_PCT:
        return "CLEAN"
    if pct >= _PARTIAL_PCT:
        return "PARTIAL"
    return "SCATTERED"


async def _count(client, dom):
    return await client.execute_kw(
        _LEAD, "search_count", args=[dom], kwargs={"context": _CTX_ALL}
    )


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


# ── setup: fields_get, campaign resolution, target selection, bulk fetch ─────────

async def setup(client) -> dict:
    print(_SEP)
    print("  SETUP — fields_get, campaign resolution, target selection, bulk fetch")
    print(_SEP)

    lead_fields = await client.execute_kw(
        _LEAD, "fields_get", args=[],
        kwargs={"attributes": ["string", "type", "relation"]},
    )
    print(f"  crm.lead exposes {len(lead_fields)} fields. "
          f"create_date present: {'create_date' in lead_fields}")

    # candidate batch-id fields (Probe 3) — name OR label matches the hint regex
    batchid_candidates = []
    for name in sorted(lead_fields):
        meta = lead_fields[name]
        label = str(meta.get("string") or "")
        if _BATCHID_RE.search(name) or _BATCHID_RE.search(label):
            batchid_candidates.append((name, meta.get("type", ""), label))
    # which of those are light enough to pull on the whole population
    bulk_extra = [n for (n, t, _) in batchid_candidates if t in _SIMPLE_TYPES][:6]

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
    confirmed_ids = {cid for nm in domain.CONFIRMED_BUYER_CAMPAIGNS
                     for cid in name_to_ids.get(nm, [])}

    # bulk fetch: every lead, just the columns we need for all probes
    fetch_fields = ["id", "create_date", CAMPAIGN_FIELD, "create_uid"] + bulk_extra
    print(f"  bulk-fetch fields : {fetch_fields}")
    rows = await _fetch_all(client, _LEAD, [], fetch_fields)
    print(f"  crm.lead population fetched (incl. archived): {len(rows):,}")

    # pre-compute Cairo datetime + per-campaign lead counts once
    lead_count = Counter()
    for r in rows:
        r["_cairo"] = _to_cairo(r["create_date"]) if r.get("create_date") else None
        cid, _ = _m2o(r.get(CAMPAIGN_FIELD))
        lead_count[cid] += 1

    # targets = 4 confirmed campaigns ∪ top-5 campaigns by lead volume (dedup, vol order)
    ranked_real = sorted((cid for cid in lead_count if cid is not None),
                         key=lambda c: -lead_count[c])
    target_set = set(confirmed_ids) | set(ranked_real[:5])
    targets = [cid for cid in ranked_real if cid in target_set]

    print(f"  confirmed campaigns: "
          f"{sorted(domain.CONFIRMED_BUYER_CAMPAIGNS)} -> ids {sorted(confirmed_ids)}")
    print(f"  TARGETS (4 confirmed ∪ top-5 by volume), volume-ordered:")
    for cid in targets:
        tags = []
        if cid in confirmed_ids:
            tags.append("CONFIRMED")
        if (id_to_name.get(cid) or "").strip().lower() == "none":
            tags.append("JUNK-LABEL")
        tag = f"  [{','.join(tags)}]" if tags else ""
        print(f"     id={cid:<5} {(id_to_name.get(cid) or '(empty)'):<28} "
              f"leads={lead_count[cid]:>7,}{tag}")
    print()

    return {
        "lead_fields": lead_fields,
        "batchid_candidates": batchid_candidates,
        "bulk_extra": bulk_extra,
        "id_to_name": id_to_name,
        "confirmed_ids": confirmed_ids,
        "rows": rows,
        "lead_count": lead_count,
        "targets": targets,
    }


# ── PROBE 1 — Legacy migration cluster ───────────────────────────────────────────

def probe_1(ctx: dict) -> set:
    print(_SEP)
    print("  PROBE 1 — LEGACY MIGRATION CLUSTER  (confirm exact timestamp(s) + size)")
    print(_SEP)
    rows = ctx["rows"]

    by_day = Counter(r["_cairo"].strftime("%Y-%m-%d") for r in rows if r["_cairo"])
    print(f"  TOP 8 Cairo-local days by lead-creation volume (migration shows as a spike):")
    print(f"     {'day':<12} | {'leads created':>14}")
    print(f"     {'-'*12}-+-{'-'*14}")
    for day, cnt in sorted(by_day.items(), key=lambda kv: -kv[1])[:8]:
        flag = "  <- migration-scale" if cnt >= _LEGACY_DAY_MIN else ""
        print(f"     {day:<12} | {cnt:>14,}{flag}")
    print()

    legacy_days = {d for d, c in by_day.items() if c >= _LEGACY_DAY_MIN}
    legacy_rows = [r for r in rows if r["_cairo"]
                   and r["_cairo"].strftime("%Y-%m-%d") in legacy_days]
    print(f"  LEGACY definition: Cairo day(s) with >= {_LEGACY_DAY_MIN:,} leads "
          f"= {sorted(legacy_days)}")
    print(f"  legacy leads (excluded from Probe 2+): {len(legacy_rows):,} "
          f"({100.0*len(legacy_rows)/max(len(rows),1):.1f}% of population)")
    print()

    # exact-timestamp shape of the migration burst (is it one second or a spread?)
    exact = Counter(r["create_date"] for r in legacy_rows)
    span_lo = min(r["_cairo"] for r in legacy_rows)
    span_hi = max(r["_cairo"] for r in legacy_rows)
    print(f"  migration burst — distinct exact create_date timestamps: {len(exact):,}")
    print(f"  migration burst — Cairo span: {span_lo:%Y-%m-%d %H:%M:%S} "
          f"-> {span_hi:%Y-%m-%d %H:%M:%S}")
    print(f"  TOP 12 exact timestamps inside the migration burst (Cairo-local):")
    print(f"     {'exact timestamp (Cairo)':<24} | {'leads':>8}")
    print(f"     {'-'*24}-+-{'-'*8}")
    for ts, cnt in sorted(exact.items(), key=lambda kv: -kv[1])[:12]:
        print(f"     {_to_cairo(ts):%Y-%m-%d %H:%M:%S}      | {cnt:>8,}")
    # create_uid corroboration — a migration is typically one importing user
    uid_share = Counter()
    for r in legacy_rows:
        uid, uname = _m2o(r.get("create_uid"))
        uid_share[uname or "(none)"] += 1
    top_uid, top_uid_cnt = uid_share.most_common(1)[0]
    print()
    print(f"  create_uid of legacy leads — top author: {top_uid!r} "
          f"holds {top_uid_cnt:,}/{len(legacy_rows):,} "
          f"({100.0*top_uid_cnt/max(len(legacy_rows),1):.1f}%) "
          f"[{len(uid_share)} distinct authors]")
    print()
    print(f"  >>> PROBE 1 — legacy migration cluster = {len(legacy_rows):,} leads on "
          f"{sorted(legacy_days)}; excluded cleanly below.")
    print()
    return legacy_days


# ── PROBE 2 — Post-migration bulk clustering ─────────────────────────────────────

def _cluster_stats(crows, keyfn):
    """Group rows by keyfn -> (clear_clusters[key->cnt], clean_pct, n_singletons)."""
    grp = Counter(keyfn(r) for r in crows)
    clear = {k: c for k, c in grp.items() if c >= _CLEAR_MIN}
    in_clear = sum(clear.values())
    pct = 100.0 * in_clear / max(len(crows), 1)
    singles = sum(1 for c in grp.values() if c == 1)
    return grp, clear, pct, singles


def probe_2(ctx: dict, legacy_days: set) -> dict:
    print(_SEP)
    print("  PROBE 2 — POST-MIGRATION BULK CLUSTERING  (the core question)")
    print(_SEP)
    print(f"  'clear' cluster = an exact create_date timestamp shared by >= "
          f"{_CLEAR_MIN} leads.")
    print(f"  Three lenses compared: EXACT second (primary) · same MINUTE · same DAY "
          f"(fallback).")
    print()

    rows, id_to_name = ctx["rows"], ctx["id_to_name"]
    by_campaign = defaultdict(list)
    for r in rows:
        if r["_cairo"] and r["_cairo"].strftime("%Y-%m-%d") in legacy_days:
            continue                                   # exclude legacy migration
        cid, _ = _m2o(r.get(CAMPAIGN_FIELD))
        by_campaign[cid].append(r)

    k_exact = lambda r: r["create_date"]
    k_min = lambda r: r["_cairo"].strftime("%Y-%m-%d %H:%M")
    k_day = lambda r: r["_cairo"].strftime("%Y-%m-%d")

    # headline cleanliness table across all targets
    print(f"  {'campaign':<28} | {'post-mig':>8} | {'exact%':>7} | {'min%':>6} | "
          f"{'day%':>6} | {'clear#':>6} | {'singles':>7}")
    print(f"  {'-'*28}-+-{'-'*8}-+-{'-'*7}-+-{'-'*6}-+-{'-'*6}-+-{'-'*6}-+-{'-'*7}")
    per_campaign = {}
    agg_total = agg_clear = 0
    for cid in ctx["targets"]:
        crows = by_campaign.get(cid, [])
        if not crows:
            continue
        _, clear_e, pct_e, singles = _cluster_stats(crows, k_exact)
        _, _, pct_m, _ = _cluster_stats(crows, k_min)
        _, _, pct_d, _ = _cluster_stats(crows, k_day)
        per_campaign[cid] = {"rows": crows, "clear_exact": clear_e,
                             "pct_exact": pct_e, "singles": singles}
        agg_total += len(crows)
        agg_clear += sum(clear_e.values())
        print(f"  {(id_to_name.get(cid) or '(empty)')[:28]:<28} | {len(crows):>8,} | "
              f"{pct_e:>6.1f}% | {pct_m:>5.1f}% | {pct_d:>5.1f}% | "
              f"{len(clear_e):>6,} | {singles:>7,}")
    agg_pct = 100.0 * agg_clear / max(agg_total, 1)
    print(f"  {'-'*28}-+-{'-'*8}-+-{'-'*7}-+-{'-'*6}-+-{'-'*6}-+-{'-'*6}-+-{'-'*7}")
    print(f"  {'ALL TARGETS (aggregate)':<28} | {agg_total:>8,} | {agg_pct:>6.1f}% | "
          f"{'':>6} | {'':>6} | {'':>6} | {'':>7}")
    print()

    # per-campaign candidate-bulk listing (the clear exact-timestamp clusters)
    for cid in ctx["targets"]:
        info = per_campaign.get(cid)
        if not info:
            continue
        clear = info["clear_exact"]
        print(_SEP2)
        print(f"  CANDIDATE BULKS — {id_to_name.get(cid) or '(empty)'}  "
              f"(post-mig leads={len(info['rows']):,}, clear clusters={len(clear)}, "
              f"exact-cluster cleanliness={info['pct_exact']:.1f}%)")
        if not clear:
            print(f"     (no exact timestamp is shared by >= {_CLEAR_MIN} leads — "
                  f"this campaign's post-mig leads are SCATTERED)")
            continue
        ordered = sorted(clear.items(), key=lambda kv: _to_cairo(kv[0]))
        print(f"     {'#':>3}  {'bulk timestamp (Cairo)':<22}  {'size':>6}")
        for i, (ts, cnt) in enumerate(ordered[:_CLUSTER_LIST_CAP], 1):
            print(f"     {i:>3}  {_to_cairo(ts):%Y-%m-%d %H:%M:%S}    {cnt:>6,}")
        if len(ordered) > _CLUSTER_LIST_CAP:
            print(f"     … {len(ordered) - _CLUSTER_LIST_CAP} more clear clusters")
    print()

    verdict = _verdict_word(agg_pct)
    print(f"  >>> PROBE 2 VERDICT — Bulks identifiable via create_date: {verdict}  "
          f"(aggregate {agg_pct:.1f}% of post-mig target leads fall in clear "
          f">= {_CLEAR_MIN}-lead exact-timestamp clusters; "
          f"CLEAN>= {_CLEAN_PCT:.0f}% / PARTIAL>= {_PARTIAL_PCT:.0f}%)")
    print()
    return {"per_campaign": per_campaign, "agg_pct": agg_pct}


# ── PROBE 3 — Batch-identifier field ─────────────────────────────────────────────

def probe_3(ctx: dict, p2: dict, legacy_days: set) -> None:
    print(_SEP)
    print("  PROBE 3 — BATCH-IDENTIFIER FIELD  (is there an import-batch marker?)")
    print(_SEP)
    candidates = ctx["batchid_candidates"]
    print(f"  crm.lead fields whose NAME/label hints at an import batch / source / "
          f"reference ({len(candidates)} found):")
    print(f"     {'technical name':<28} | {'type':<12} | label")
    print(f"     {'-'*28}-+-{'-'*12}-+-{'-'*34}")
    for name, typ, label in candidates:
        pulled = "  [pulled]" if name in ctx["bulk_extra"] else ""
        print(f"     {name:<28} | {typ:<12} | {label}{pulled}")
    print()

    # fill-rate (non-empty) for the fields we pulled on the whole population.
    # A "batch-like" field (name/label says import/batch/upload) is what could
    # actually IDENTIFY a bulk; source/medium/referred are marketing-channel
    # labels that span many bulks, so they are NOT batch ids even at 100% fill.
    _BATCH_LIKE = re.compile(r"import|batch|upload", re.I)
    lead_fields = ctx["lead_fields"]
    rows = ctx["rows"]
    postmig = [r for r in rows if not (r["_cairo"]
               and r["_cairo"].strftime("%Y-%m-%d") in legacy_days)]
    print(f"  fill-rate of pulled candidate fields (non-empty)  "
          f"[B = batch-like / C = channel label]:")
    print(f"     {'field':<28} | {'kind':<5} | {'all leads':>15} | {'post-migration':>15}")
    print(f"     {'-'*28}-+-{'-'*5}-+-{'-'*15}-+-{'-'*15}")
    batch_like = []
    for name in ctx["bulk_extra"]:
        label = str(lead_fields.get(name, {}).get("string") or "")
        is_batch = bool(_BATCH_LIKE.search(name) or _BATCH_LIKE.search(label))
        all_f = sum(1 for r in rows if _has(r.get(name)))
        pm_f = sum(1 for r in postmig if _has(r.get(name)))
        pm_pct = 100.0 * pm_f / max(len(postmig), 1)
        print(f"     {name:<28} | {'B' if is_batch else 'C':<5} | "
              f"{all_f:>6,} ({100.0*all_f/max(len(rows),1):>4.0f}%) | "
              f"{pm_f:>6,} ({pm_pct:>4.0f}%)")
        if is_batch:
            batch_like.append((name, pm_pct))
    if not ctx["bulk_extra"]:
        print(f"     (no light-typed candidate field to pull)")
    print()

    # Evaluate the best-filled batch-like field for a 1:1 correspondence with the
    # Probe-2 exact-timestamp clusters (BOTH directions). A reliable batch id must
    # (a) be well filled and (b) partition leads the SAME way create_date does:
    #   value -> one timestamp  AND  timestamp -> one value.
    reliable = "NONE"
    if batch_like:
        name, pm_pct = max(batch_like, key=lambda kv: kv[1])
        # post-mig TARGET leads with this field filled (the design population)
        triples = []  # (campaign_id, exact_ts, value)
        for cid, info in p2["per_campaign"].items():
            for r in info["rows"]:
                v = r.get(name)
                if _has(v):
                    triples.append((cid, r["create_date"], str(v)))
        val_to_ts, ts_to_val, val_to_camp = (defaultdict(set), defaultdict(set),
                                             defaultdict(set))
        for cid, ts, v in triples:
            val_to_ts[v].add(ts)
            ts_to_val[ts].add(v)
            val_to_camp[v].add(cid)
        n_val, n_ts = len(val_to_ts), len(ts_to_val)
        val_1to1 = 100.0 * sum(1 for v in val_to_ts if len(val_to_ts[v]) == 1) / max(n_val, 1)
        ts_1to1 = 100.0 * sum(1 for t in ts_to_val if len(ts_to_val[t]) == 1) / max(n_ts, 1)
        cross = sum(1 for v in val_to_camp if len(val_to_camp[v]) > 1)
        sample = sorted(val_to_ts)[:6]
        print(f"  batch-like field under test : {name!r}  "
              f"(post-mig target leads filled = {len(triples):,})")
        print(f"     distinct batch values            : {n_val:,}")
        print(f"     distinct exact timestamps        : {n_ts:,}")
        print(f"     value -> single timestamp        : {val_1to1:.0f}%")
        print(f"     timestamp -> single value        : {ts_1to1:.0f}%")
        print(f"     values spanning >1 campaign      : {cross:,}/{n_val:,}  "
              f"(>0 => a bulk is a cross-campaign upload event)")
        print(f"     sample values                    : {sample}")
        if pm_pct >= 80.0 and val_1to1 >= 90.0 and ts_1to1 >= 90.0:
            reliable = name
        print()
        print(f"  Interpretation: {name!r} {'IS' if reliable != 'NONE' else 'is NOT'} "
              f"effectively 1:1 with the create_date clusters — it "
              f"{'corroborates' if reliable != 'NONE' else 'does not cleanly partition'} "
              f"the bulks.")
    else:
        print(f"  No batch-like (import/batch/upload) field was pulled.")
    print(f"  NOTE: source_id / medium_id are marketing-CHANNEL labels (each spans many")
    print(f"        bulks over time) — high fill, but NOT per-bulk identifiers.")
    print()
    print(f"  >>> PROBE 3 VERDICT — Reliable batch-id field: {reliable} "
          f"(else fall back to create_date clustering)")
    print()


# ── PROBE 4 — Sizing & sufficiency ───────────────────────────────────────────────

def probe_4(ctx: dict, p2: dict) -> None:
    print(_SEP)
    print("  PROBE 4 — SIZING & SUFFICIENCY  (enough bulks of meaningful size?)")
    print(_SEP)
    print(f"  A 'bulk' = a clear exact-timestamp cluster (>= {_CLEAR_MIN} leads). "
          f"'Meaningful' = bulk size >= {_MEANINGFUL_SIZE}.")
    print(f"  Sufficiency = >= {_ENOUGH_BULKS} meaningful bulks (for a trend).")
    print()
    print(f"  {'campaign':<28} | {'bulks':>5} | {'meaningful':>10} | {'min':>5} | "
          f"{'med':>5} | {'max':>6} | {'span (Cairo days)':<25} | trend?")
    print(f"  {'-'*28}-+-{'-'*5}-+-{'-'*10}-+-{'-'*5}-+-{'-'*5}-+-{'-'*6}-+-"
          f"{'-'*25}-+-{'-'*6}")
    id_to_name = ctx["id_to_name"]
    for cid in ctx["targets"]:
        info = p2["per_campaign"].get(cid)
        if not info:
            continue
        clear = info["clear_exact"]
        sizes = sorted(clear.values())
        n_bulks = len(sizes)
        meaningful = [s for s in sizes if s >= _MEANINGFUL_SIZE]
        if sizes:
            ts_sorted = sorted(clear, key=lambda t: _to_cairo(t))
            span = (f"{_to_cairo(ts_sorted[0]):%Y-%m-%d} -> "
                    f"{_to_cairo(ts_sorted[-1]):%Y-%m-%d}")
            mn, md, mx = sizes[0], int(statistics.median(sizes)), sizes[-1]
        else:
            span, mn, md, mx = "—", 0, 0, 0
        enough = "YES" if len(meaningful) >= _ENOUGH_BULKS else "NO"
        print(f"  {(id_to_name.get(cid) or '(empty)')[:28]:<28} | {n_bulks:>5,} | "
              f"{len(meaningful):>10,} | {mn:>5,} | {md:>5,} | {mx:>6,} | "
              f"{span:<25} | {enough}")
    print()


# ── PROBE 5 — Maturation feasibility ─────────────────────────────────────────────

async def probe_5(client, ctx: dict) -> None:
    print(_SEP)
    print("  PROBE 5 — MATURATION FEASIBILITY  (stage-timing fields for fixed-age conv.)")
    print(_SEP)
    lead_fields = ctx["lead_fields"]
    timing = []
    for name in sorted(lead_fields):
        meta = lead_fields[name]
        typ = meta.get("type", "")
        label = str(meta.get("string") or "")
        if (_TIMING_RE.search(name) or _TIMING_RE.search(label)) and \
                typ in ("date", "datetime", "selection", "boolean", "char"):
            timing.append((name, typ, label))
    print(f"  stage-timing / outcome-dating fields on crm.lead ({len(timing)} found) "
          f"— with non-empty fill-rate across the population:")
    print(f"     {'technical name':<26} | {'type':<10} | {'fill':>13} | label")
    print(f"     {'-'*26}-+-{'-'*10}-+-{'-'*13}-+-{'-'*28}")
    total = len(ctx["rows"])
    for name, typ, label in timing:
        filled = await _count(client, [(name, "!=", False)])
        pct = 100.0 * filled / max(total, 1)
        print(f"     {name:<26} | {typ:<10} | {filled:>6,} ({pct:>4.0f}%) | {label}")
    print()
    print("  NOTE: feasibility only — no conversion-at-fixed-age computed here. A usable")
    print("  'conversion at 30 days' needs a per-lead OUTCOME timestamp (date_closed /")
    print("  date_won) whose fill-rate is high enough on bulk leads to age them.")
    print()


# ── main ─────────────────────────────────────────────────────────────────────────

async def main() -> int:
    run_at = datetime.now(timezone.utc)
    print(_SEP)
    print("  CAMPAIGN BULKS — DISCOVERY (READ-ONLY, aggregates only, $0 AI)")
    print(f"  Run at (UTC)    : {run_at:%Y-%m-%d %H:%M:%S UTC}")
    print(f"  Today (Cairo)   : {datetime.now(_CAIRO_TZ).date().isoformat()}")
    print(f"  ALLOWED_METHODS : {sorted(ALLOWED_METHODS)}")
    print(f"  Population/ctx  : ALL leads incl. archived — context={_CTX_ALL}")
    print("  READ-ONLY. Direct JSON-RPC. No FastAPI. No OpenAI. AI cost = $0.00")
    print(_SEP)
    print()

    async with OdooClient() as client:
        ctx = await setup(client)
        legacy_days = probe_1(ctx)
        p2 = probe_2(ctx, legacy_days)
        probe_3(ctx, p2, legacy_days)
        probe_4(ctx, p2)
        await probe_5(client, ctx)

    print(_SEP)
    print("  DISCOVERY COMPLETE — numbers only; no build, no design decision, no Odoo write.")
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
