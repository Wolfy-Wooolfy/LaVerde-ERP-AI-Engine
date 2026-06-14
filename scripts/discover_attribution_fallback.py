"""
scripts/discover_attribution_fallback.py — Marketing Attribution FALLBACK-FIELD
selection + corrected UI-binding exemplar (READ-ONLY, numbers only).

Two cheap questions before locking the design:
  (1) Does campaign_id (100% filled, top values look buyer-coded: FB-AY, FB-AM…)
      ENCODE the media buyer well enough to be the fallback? Measure concentration
      (one-buyer share per campaign) + clean rescue volume, head-to-head against
      campaign_name (char, 33.1% filled).
  (2) Produce a CORRECT UI-binding exemplar: a SINGLE-SET lead (exactly one of
      media_buyer_id / direct_media_buyer_id set) so opening it in Odoo reveals
      which field the "Media Buyer" widget binds to.

Method discipline:
  - READ-ONLY: only read_group / search_count / search_read. ALLOWED_METHODS
    untouched. No create/write/unlink. No FastAPI. No OpenAI. AI cost = $0.00.
  - Population = ALL leads, context={'active_test': False} on every count.
  - Every figure prints its exact domain + context.

Usage (from project root; uvicorn NOT required):
    python scripts/discover_attribution_fallback.py
"""

import asyncio
import io
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient  # noqa: E402

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_MODEL = "crm.lead"
_SEP = "=" * 100
_CTX_ALL = {"active_test": False}
_TOTAL = 146_815  # prior-run population baseline (active_test=False); re-counted below

_BUYER = "media_buyer_id"
_DIRECT = "direct_media_buyer_id"
_TOPN = 20

_DOC = Path(__file__).resolve().parents[1] / "docs" / "MARKETING_ATTRIBUTION_DISCOVERY_DATA.md"


# ── helpers ───────────────────────────────────────────────────────────────────

def _key_label(v):
    """(key, label) for a groupby value: m2o -> (id, name); scalar -> (v, str)."""
    if isinstance(v, (list, tuple)) and len(v) == 2:
        return v[0], str(v[1])
    if v is False or v is None:
        return None, "(empty)"
    return v, str(v)


def _buyer_name(v):
    if isinstance(v, (list, tuple)) and len(v) == 2:
        return str(v[1])
    return "(empty)" if not v else str(v)


def _flag(share):
    if share >= 90:
        return "CLEAN (>=90% one buyer)"
    if share >= 50:
        return "MIXED (50-90%)"
    return "NOISE (<50%)"


async def _count(client, domain):
    return await client.execute_kw(
        _MODEL, "search_count", args=[domain], kwargs={"context": _CTX_ALL}
    )


async def _analyze(client, dim_field):
    """Concentration (one-buyer share per dim value) + per-dim rescue volume.

    Returns: {key: {'label', 'total', 'buyers': Counter, 'rescue': int}}
    where total = leads with BOTH dim and buyer set; rescue = leads with dim set
    but buyer EMPTY (a fallback on dim would newly attribute these).
    """
    both_domain = [(dim_field, "!=", False), (_BUYER, "!=", False)]
    rg = await client.execute_kw(
        _MODEL, "read_group",
        args=[both_domain, [_BUYER], [dim_field, _BUYER]],
        kwargs={"context": _CTX_ALL, "lazy": False},
    )
    per = {}
    for r in rg:
        key, label = _key_label(r.get(dim_field))
        cnt = r.get("__count") or 0
        d = per.setdefault(key, {"label": label, "total": 0, "buyers": Counter(), "rescue": 0})
        d["total"] += cnt
        d["buyers"][_buyer_name(r.get(_BUYER))] += cnt

    rescue_domain = [(dim_field, "!=", False), (_BUYER, "=", False)]
    rg2 = await client.execute_kw(
        _MODEL, "read_group",
        args=[rescue_domain, [dim_field], [dim_field]],
        kwargs={"context": _CTX_ALL, "lazy": False},
    )
    for r in rg2:
        key, label = _key_label(r.get(dim_field))
        cnt = r.get("__count") or 0
        d = per.setdefault(key, {"label": label, "total": 0, "buyers": Counter(), "rescue": 0})
        d["rescue"] += cnt
    return per, both_domain, rescue_domain


def _report_dim(title, dim_field, per, both_domain, rescue_domain):
    print(_SEP)
    print(f"  {title}")
    print(f"  concentration domain (both set) = {both_domain}  context={_CTX_ALL}")
    print(f"  rescue domain (dim set, buyer empty) = {rescue_domain}  context={_CTX_ALL}")
    print(_SEP)

    ranked = sorted(per.items(), key=lambda kv: -kv[1]["total"])
    top = ranked[:_TOPN]

    print(f"  {'campaign label':<30} | {'both':>7} | {'top buyer':<16} | {'share':>6} | "
          f"{'flag':<24} | {'rescue':>7}")
    print(f"  {'-'*30}-+-{'-'*7}-+-{'-'*16}-+-{'-'*6}-+-{'-'*24}-+-{'-'*7}")
    clean_rescue_top = 0
    clean_keys_top = []
    for key, d in top:
        total = d["total"]
        if total == 0:
            top_buyer, share = "(none both-set)", 0.0
            flag = "n/a (0 both-set)"
        else:
            top_buyer, top_cnt = d["buyers"].most_common(1)[0]
            share = 100 * top_cnt / total
            flag = _flag(share)
        if flag.startswith("CLEAN"):
            clean_rescue_top += d["rescue"]
            clean_keys_top.append(key)
        lab = (d["label"][:29]) if d["label"] else "(empty)"
        print(f"  {lab:<30} | {total:>7,} | {top_buyer[:16]:<16} | {share:>5.1f}% | "
              f"{flag:<24} | {d['rescue']:>7,}")
    print()

    # All-campaign clean rescue (bonus: clean concentration beyond the top 20).
    clean_rescue_all = 0
    clean_n_all = 0
    for key, d in per.items():
        if d["total"] == 0:
            continue
        _, top_cnt = d["buyers"].most_common(1)[0]
        if 100 * top_cnt / d["total"] >= 90:
            clean_rescue_all += d["rescue"]
            clean_n_all += 1
    print(f"  CLEAN rescue volume (sum over CLEAN campaigns IN TOP {_TOPN}) = "
          f"{clean_rescue_top:,}   ({len(clean_keys_top)} clean campaigns)")
    print(f"  CLEAN rescue volume (sum over ALL clean campaigns, any rank)  = "
          f"{clean_rescue_all:,}   ({clean_n_all} clean campaigns)")
    print()
    return clean_rescue_top, clean_rescue_all


# ── deliverable 4 — corrected single-set UI-binding exemplars ─────────────────

async def _ui_exemplars(client):
    print(_SEP)
    print("  CORRECTED UI-BINDING EXEMPLARS — SINGLE-SET leads (exactly one MB field)")
    print(_SEP)

    domA = [(_BUYER, "!=", False), (_DIRECT, "=", False)]   # media_buyer_id only
    domB = [(_DIRECT, "!=", False), (_BUYER, "=", False)]   # direct_media_buyer_id only
    nA = await _count(client, domA)
    nB = await _count(client, domB)
    print(f"  pattern A — {_BUYER} set, {_DIRECT} empty : {nA:,}   domain={domA}  context={_CTX_ALL}")
    print(f"  pattern B — {_DIRECT} set, {_BUYER} empty : {nB:,}   domain={domB}  context={_CTX_ALL}")
    pattern, dom = ("A", domA) if nA >= nB else ("B", domB)
    print(f"  -> most populous single-set pattern: {pattern}  (showing up to 3)")
    print()

    rows = await client.execute_kw(
        _MODEL, "search_read",
        args=[dom],
        kwargs={"fields": ["id", "name", _BUYER, _DIRECT], "context": _CTX_ALL,
                "limit": 3, "order": "id"},
    )
    first_id = None
    for r in rows:
        mb_set = bool(r.get(_BUYER))
        dr_set = bool(r.get(_DIRECT))
        exactly_one = mb_set != dr_set
        if first_id is None:
            first_id = r["id"]
        print(f"  lead id={r['id']}  name={r.get('name')!r}")
        print(f"       media_buyer_id        = {_buyer_name(r.get(_BUYER))}")
        print(f"       direct_media_buyer_id = {_buyer_name(r.get(_DIRECT))}")
        print(f"       ASSERT exactly one of the two set : "
              f"{'PASS' if exactly_one else 'FAIL'}")
        print()
    return pattern, first_id


# ── deliverable 5 — prior-doc finding on campaign_id as buyer signal ──────────

def _prior_doc_finding():
    print(_SEP)
    print("  PRIOR-DOC CHECK — did MARKETING_ATTRIBUTION_DISCOVERY_DATA.md evaluate campaign_id as a buyer signal?")
    print(_SEP)
    if not _DOC.exists():
        print(f"  doc not found at {_DOC} — cannot quote.")
        print()
        return
    hits = []
    for ln in _DOC.read_text(encoding="utf-8", errors="replace").splitlines():
        low = ln.lower()
        if "campaign_id" in low and ("buyer" in low or "identify" in low or "utm" in low):
            hits.append(ln.strip())
    if hits:
        print("  Prior doc DID mention campaign_id in a buyer/UTM context. Quoted line(s):")
        for h in hits[:3]:
            print(f"     > {h}")
        print()
        print("  Note: the prior doc ASSERTED campaign_id 'does not identify the Media Buyer'")
        print("        but did NOT measure per-campaign buyer concentration. This run does.")
    else:
        print("  Prior doc did NOT evaluate campaign_id as a buyer-encoding signal — "
              "this run is the first measurement.")
    print()


# ── main ──────────────────────────────────────────────────────────────────────

async def main():
    run_at = datetime.now(timezone.utc)
    print(_SEP)
    print("  ATTRIBUTION FALLBACK DISCOVERY (READ-ONLY, numbers only)")
    print(f"  Run at (UTC)    : {run_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  Model           : {_MODEL}")
    print(f"  ALLOWED_METHODS : {sorted(ALLOWED_METHODS)}")
    print(f"  Population/ctx  : ALL leads incl. archived — context={_CTX_ALL}")
    print("  READ-ONLY. Direct JSON-RPC. No FastAPI. No OpenAI. AI cost = $0.00")
    print(_SEP)
    print()

    async with OdooClient() as client:
        total = await _count(client, [])
        print(f"  population re-count: search_count([]) context={_CTX_ALL} = {total:,}  "
              f"(prior baseline {_TOTAL:,})")
        print()

        # Deliverable 1 + 2 — campaign_id
        per_cid, bd_c, rd_c = await _analyze(client, "campaign_id")
        cid_clean_top, cid_clean_all = _report_dim(
            "DELIVERABLE 1+2 — campaign_id -> BUYER concentration & rescue volume",
            "campaign_id", per_cid, bd_c, rd_c)

        # Deliverable 3 — campaign_name
        per_cn, bd_n, rd_n = await _analyze(client, "campaign_name")
        cn_clean_top, cn_clean_all = _report_dim(
            "DELIVERABLE 3 — campaign_name -> BUYER concentration & rescue volume",
            "campaign_name", per_cn, bd_n, rd_n)

        # Deliverable 4 — corrected exemplars
        pattern, first_single_id = await _ui_exemplars(client)

        # Deliverable 5 — prior-doc quote
        _prior_doc_finding()

        # Deliverable 6 — decisions block
        print(_SEP)
        print("  DECISIONS NEEDED (paste-ready)")
        print(_SEP)
        print()
        print("  FALLBACK FIELD — campaign_id vs campaign_name (head-to-head):")
        print(f"     campaign_id   : CLEAN rescue (top {_TOPN}) = {cid_clean_top:,} | "
              f"CLEAN rescue (all) = {cid_clean_all:,}")
        print(f"     campaign_name : CLEAN rescue (top {_TOPN}) = {cn_clean_top:,} | "
              f"CLEAN rescue (all) = {cn_clean_all:,}")
        better = "campaign_id" if cid_clean_all >= cn_clean_all else "campaign_name"
        print(f"     -> RECOMMENDED fallback (more clean newly-attributable leads): {better}")
        print(f"        (Recommendation weighs clean concentration + clean rescue volume; "
              f"final pick is Khaled's.)")
        print()
        if first_single_id is not None:
            print(f"  UI-BINDING CHECK: open single-set lead id={first_single_id} "
                  f"(pattern {pattern}) in Odoo;")
            print(f"     whichever value the 'Media Buyer' widget shows (the populated field) "
                  f"is the bound primary field.")
        else:
            print("  UI-BINDING CHECK: no single-set lead found — FLAG.")
        print()

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
