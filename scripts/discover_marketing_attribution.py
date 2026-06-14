"""
scripts/discover_marketing_attribution.py — Marketing Attribution live-data
discovery (SESSION M-DISCOVERY).

READ-ONLY discovery. AGGREGATES & STATISTICS ONLY. No build, no KPI design, no
product decisions. The goal is to resolve every `[OPEN — needs live discovery]`
item in docs/MARKETING_ATTRIBUTION_DISCOVERY.md with real numbers, and answer
THE DECISIVE QUESTION:

    "Is `Campaign Name` reliable enough to attribute leads to Media Buyers?"

PRIVACY RULE (HARD): output AGGREGATES AND STATISTICS ONLY. NEVER print
individual customer data — no customer/contact names, no phone numbers, no
emails, no per-lead PII rows. Campaign-name free text is fetched and classified
IN PYTHON; only aggregates, distinct initials *codes*, sanitized *shape*
examples, counts and percentages are ever printed. Distinct Media-Buyer / Stage
labels are internal staff/process labels (safe to show).

Method discipline:
  - READ-ONLY: fields_get / search_count / read_group / search_read only.
  - NO create/write/unlink. ALLOWED_METHODS untouched.
  - No FastAPI. No OpenAI. AI cost = $0.00.
  - Every Odoo domain used is printed next to its number, so each figure is
    reproducible.

Usage (from project root, server NOT required — talks to Odoo directly):
    python scripts/discover_marketing_attribution.py
"""

import asyncio
import io
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# sys.path.insert so the script runs without PYTHONPATH set (settled convention).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient  # noqa: E402

# Force UTF-8 stdout (Windows consoles default to cp1252).
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_MODEL = "crm.lead"
_SEP = "=" * 100
_SEP2 = "-" * 100

# The UI labels from the vision doc that we must map to technical field names.
_TARGET_LABELS = [
    "Campaign",
    "Campaign Name",
    "Medium",
    "Source",
    "Channel",
    "Media Buyer",
    "Media Buyer Manager",
    "Adset Name",
    "Referred By",
    "Sales Team",
    "Days to Assign",
    "Days to Close",
    "Stage",
]

# The convention the vision doc claims: 1-4 letters, then a dash, then the
# campaign name. Applied IN PYTHON (never as an Odoo field-level regex).
_CONVENTION = re.compile(r"^[A-Za-z]{1,4}\s*-\s*.+")

# Pagination size for the single-field campaign-name fetch and the coverage fetch.
_PAGE = 5000


# ── Privacy helpers ───────────────────────────────────────────────────────────

def _is_nonempty(v) -> bool:
    """True iff a char/text value carries real content (Odoo empties are False)."""
    return bool(v) and str(v).strip() != ""


def _sanitize_example(v: str) -> str:
    """Return a PII-safe rendition of a free-text value for a shape example.

    Redacts anything that could be personal: runs of >=4 digits (phone/ID-like),
    e-mail addresses, and '@'-bearing tokens. Also caps length. The initials +
    marketing-campaign-label shapes (e.g. 'YM-GCC ABO LAVERDE') carry no PII and
    survive intact; genuinely risky values get their risky parts masked.
    """
    s = str(v).strip()
    s = re.sub(r"\S+@\S+", "<email>", s)
    s = re.sub(r"\d{4,}", "<num>", s)
    if "@" in s:
        s = "<redacted>"
    if len(s) > 48:
        s = s[:48] + "…"
    return s


def _mask_shape(v: str) -> str:
    """Character-class mask: letters→A, digits→9, other kept. Pure shape, no PII."""
    s = str(v).strip()
    out = []
    for ch in s:
        if ch.isalpha():
            out.append("A")
        elif ch.isdigit():
            out.append("9")
        else:
            out.append(ch)
    masked = "".join(out)
    if len(masked) > 40:
        masked = masked[:40] + "…"
    return masked


# ── Paginated single-pass fetch ───────────────────────────────────────────────

async def _fetch_all(client: OdooClient, domain: list, fields: list[str]) -> list[dict]:
    """search_read the whole domain in pages of _PAGE, ordered by id."""
    rows: list[dict] = []
    offset = 0
    while True:
        page = await client.execute_kw(
            _MODEL, "search_read",
            args=[domain],
            kwargs={"fields": fields, "order": "id", "limit": _PAGE, "offset": offset},
        )
        rows.extend(page)
        if len(page) < _PAGE:
            break
        offset += _PAGE
    return rows


# ── Step 1: field resolution ──────────────────────────────────────────────────

async def _resolve_fields(client: OdooClient) -> dict:
    fields = await client.execute_kw(
        _MODEL, "fields_get",
        args=[],
        kwargs={"attributes": ["string", "type", "relation"]},
    )

    print(_SEP)
    print("  STEP 1 — FIELD RESOLUTION  (fields_get on crm.lead)")
    print(_SEP)
    print(f"  crm.lead exposes {len(fields)} fields.")
    print()
    print(f"  {'UI label':<22} | {'technical name':<34} | {'type':<10} | target model / note")
    print(f"  {'-'*22}-+-{'-'*34}-+-{'-'*10}-+-{'-'*28}")

    resolved: dict[str, str | None] = {}
    candidates: dict[str, list[str]] = {}
    for label in _TARGET_LABELS:
        ll = label.lower()
        # exact label match (case-insensitive) first, then substring.
        exact = [(n, m) for n, m in fields.items()
                 if (m.get("string") or "").strip().lower() == ll]
        subs = [(n, m) for n, m in fields.items()
                if ll in (m.get("string") or "").strip().lower()]
        cands = exact if exact else subs
        candidates[label] = [n for n, _ in cands]

        if not cands:
            resolved[label] = None
            print(f"  {label:<22} | {'<UNRESOLVED>':<34} | {'-':<10} | "
                  f"!! no field whose label matches — FLAG")
            continue

        # Prefer a single exact match; otherwise show all and pick first (flag).
        pick_name, pick_meta = cands[0]
        resolved[label] = pick_name
        flag = "" if len(cands) == 1 else f"  (!! {len(cands)} candidates — see below)"
        rel = pick_meta.get("relation") or ""
        print(f"  {label:<22} | {pick_name:<34} | {pick_meta.get('type',''):<10} | "
              f"{rel}{flag}")
        if len(cands) > 1:
            for n, m in cands:
                print(f"       candidate: {n:<34} type={m.get('type',''):<10} "
                      f"label={m.get('string')!r} relation={m.get('relation') or ''}")

    # Stage / type / active semantics confirmation.
    print()
    print("  Core semantics:")
    for key in ("stage_id", "type", "active"):
        m = fields.get(key)
        if m:
            print(f"     {key:<12} type={m.get('type',''):<10} relation={m.get('relation') or '-':<12} "
                  f"label={m.get('string')!r}")
        else:
            print(f"     {key:<12} !! NOT PRESENT on crm.lead — FLAG")
    print()
    return {"fields": fields, "resolved": resolved, "candidates": candidates}


# ── Step 2: population baseline ───────────────────────────────────────────────

async def _count(client: OdooClient, domain: list) -> int:
    return await client.execute_kw(_MODEL, "search_count", args=[domain], kwargs={})


async def _population_baseline(client: OdooClient) -> dict:
    print(_SEP)
    print("  STEP 2 — POPULATION BASELINE  (search_count; domain printed per number)")
    print(_SEP)

    domains = {
        "active leads+opps (default active=True)": [],
        "  type=lead     (active=True)": [("type", "=", "lead")],
        "  type=opp      (active=True)": [("type", "=", "opportunity")],
        "archived only (active=False)": [("active", "=", False)],
        "ALL incl. archived (active in [T,F])": [("active", "in", [True, False])],
        "  type=lead     (incl. archived)": [("active", "in", [True, False]), ("type", "=", "lead")],
        "  type=opp      (incl. archived)": [("active", "in", [True, False]), ("type", "=", "opportunity")],
    }
    counts: dict[str, int] = {}
    for label, dom in domains.items():
        n = await _count(client, dom)
        counts[label] = n
        print(f"     {label:<42} = {n:>8,}   domain={dom}")
    print()
    print("  CHOSEN attribution population for steps 3-5: ALL incl. archived")
    print("     domain = [('active','in',[True,False])]")
    print("     rationale: archived (Lost) leads still carry attribution + a stage and")
    print("     are central to OUTCOME analysis; excluding them would hide poor outcomes.")
    print("     (Final population choice is FLAGGED for Khaled — opp-only is the alt.)")
    print()
    return counts


# ── Step 3 + 4 + 5 over the chosen population ─────────────────────────────────

async def _analyze(client: OdooClient, resolved: dict, candidates: dict) -> None:
    pop_domain = [("active", "in", [True, False])]
    total = await _count(client, pop_domain)

    cn_field = resolved.get("Campaign Name")
    mb_field = resolved.get("Media Buyer")
    mbm_field = resolved.get("Media Buyer Manager")
    adset_field = resolved.get("Adset Name")
    campaign_field = resolved.get("Campaign")
    medium_field = resolved.get("Medium")
    source_field = resolved.get("Source")
    channel_field = resolved.get("Channel")
    referred_field = resolved.get("Referred By")
    team_field = resolved.get("Sales Team")

    # ===== STEP 3: Campaign Name coverage & convention =====
    print(_SEP)
    print("  STEP 3 — CAMPAIGN NAME COVERAGE & CONVENTION")
    print(f"  population = ALL incl. archived  domain={pop_domain}  total={total:,}")
    print(_SEP)

    if not cn_field:
        print("  !! Campaign Name field UNRESOLVED — cannot run step 3. FLAG and skip.")
        cn_by_id: dict[int, str] = {}
    else:
        print(f"  Campaign Name technical field = {cn_field!r}  (single-field fetch, no PII fields)")
        cn_rows = await _fetch_all(client, pop_domain, ["id", cn_field])
        cn_by_id = {r["id"]: r.get(cn_field) for r in cn_rows}

        nonempty = [v for v in cn_by_id.values() if _is_nonempty(v)]
        empty_n = len(cn_by_id) - len(nonempty)
        print(f"     records fetched               : {len(cn_by_id):,}")
        print(f"     Campaign Name NON-EMPTY       : {len(nonempty):,}  "
              f"({100*len(nonempty)/max(total,1):.1f}% of {total:,})")
        print(f"     Campaign Name empty / false   : {empty_n:,}  "
              f"({100*empty_n/max(total,1):.1f}%)")
        print()

        matched = [v for v in nonempty if _CONVENTION.match(str(v).strip())]
        nonmatch = [v for v in nonempty if not _CONVENTION.match(str(v).strip())]
        print(f"     convention regex (Python)     : ^[A-Za-z]{{1,4}}\\s*-\\s*.+")
        print(f"     MATCH convention              : {len(matched):,}  "
              f"({100*len(matched)/max(len(nonempty),1):.1f}% of non-empty, "
              f"{100*len(matched)/max(total,1):.1f}% of population)")
        print(f"     DO NOT match                  : {len(nonmatch):,}  "
              f"({100*len(nonmatch)/max(len(nonempty),1):.1f}% of non-empty)")
        print()

        # distinct initials codes (over convention-matched set)
        codes = Counter()
        for v in matched:
            code = str(v).split("-", 1)[0].strip().upper()
            codes[code] += 1
        print(f"  INITIALS -> FREQUENCY (over {len(matched):,} convention-matched; "
              f"{len(codes)} distinct codes), sorted desc:")
        print(f"     {'code':<8} | {'leads':>8} | {'% of matched':>12}")
        print(f"     {'-'*8}-+-{'-'*8}-+-{'-'*12}")
        for code, n in codes.most_common():
            print(f"     {code:<8} | {n:>8,} | {100*n/max(len(matched),1):>11.1f}%")
        print()

        # non-match shape categories
        cats: dict[str, list[str]] = {
            "no_dash": [],
            "leading_digits": [],
            "long_or_multiword_before_dash": [],
            "dash_no_text_after": [],
            "other": [],
        }
        for v in nonmatch:
            s = str(v).strip()
            if "-" not in s:
                cats["no_dash"].append(s)
            elif s[:1].isdigit():
                cats["leading_digits"].append(s)
            else:
                before, after = s.split("-", 1)
                before, after = before.strip(), after.strip()
                if after == "":
                    cats["dash_no_text_after"].append(s)
                elif before != "" and re.fullmatch(r"[A-Za-z ]+", before):
                    # before is letters/spaces but failed regex (e.g. >4 letters or word+space)
                    cats["long_or_multiword_before_dash"].append(s)
                elif before != "":
                    cats["long_or_multiword_before_dash"].append(s)
                else:
                    cats["other"].append(s)
        print(f"  NON-MATCH SHAPE CATEGORIES (of {len(nonmatch):,} non-matching non-empty):")
        print(f"     {'category':<32} | {'count':>7} | {'%nonmatch':>9} | example (sanitized) | mask")
        print(f"     {'-'*32}-+-{'-'*7}-+-{'-'*9}-+-{'-'*20}")
        for cat, vals in cats.items():
            ex = _sanitize_example(vals[0]) if vals else ""
            mk = _mask_shape(vals[0]) if vals else ""
            pct = 100 * len(vals) / max(len(nonmatch), 1)
            print(f"     {cat:<32} | {len(vals):>7,} | {pct:>8.1f}% | {ex!r}  |  {mk}")
        print()

        # mapping skeleton (YM pre-filled only)
        print("  PROPOSED initials -> Media Buyer MAPPING SKELETON (Khaled to fill blanks):")
        for code, n in codes.most_common():
            name = "Yomna Mosaad" if code == "YM" else ""
            print(f"     {code:<8} ({n:>6,} leads) -> {name if name else '________________'}")
        print()

    # ===== STEP 4: dedicated media-buyer field & other dimensions =====
    print(_SEP)
    print("  STEP 4 — DEDICATED FIELD COVERAGE (the alternative to parsing)")
    print(f"  population = ALL incl. archived  domain={pop_domain}  total={total:,}")
    print(_SEP)

    # all candidate fields for the ambiguous labels (probe BOTH so Khaled can
    # see which dedicated field is the real signal).
    ambiguous = sorted(set(
        candidates.get("Media Buyer", [])
        + candidates.get("Media Buyer Manager", [])
        + candidates.get("Referred By", [])
    ))

    # one coverage fetch over the population (no customer PII fields)
    cov_fields = ["id", "stage_id"]
    for f in (list((mb_field, mbm_field, adset_field, campaign_field, medium_field,
              source_field, channel_field, referred_field, team_field)) + ambiguous):
        if f and f not in cov_fields:
            cov_fields.append(f)
    cov_rows = await _fetch_all(client, pop_domain, cov_fields)
    print(f"  coverage fetch fields = {cov_fields}")
    print(f"  records fetched = {len(cov_rows):,}")
    print()

    def _cov(field: str | None) -> tuple[int, int]:
        if not field:
            return (0, 0)
        pop = sum(1 for r in cov_rows if _is_nonempty(r.get(field)))
        return (pop, len(cov_rows))

    def _topvals(field: str | None, top: int = 12) -> Counter:
        c = Counter()
        if not field:
            return c
        for r in cov_rows:
            v = r.get(field)
            if isinstance(v, (list, tuple)) and len(v) == 2:  # many2one [id, name]
                c[v[1]] += 1
            elif _is_nonempty(v):
                c[str(v).strip()] += 1
        return c

    print(f"  {'dimension':<24} | {'field':<30} | {'populated':>9} | {'%':>6} | distinct")
    print(f"  {'-'*24}-+-{'-'*30}-+-{'-'*9}-+-{'-'*6}-+-{'-'*8}")
    dims = [
        ("Media Buyer", mb_field),
        ("Media Buyer Manager", mbm_field),
        ("Adset Name", adset_field),
        ("Campaign (campaign_id)", campaign_field),
        ("Medium", medium_field),
        ("Source", source_field),
        ("Channel", channel_field),
        ("Referred By", referred_field),
        ("Sales Team", team_field),
    ]
    for name, fld in dims:
        pop, tot = _cov(fld)
        distinct = len(_topvals(fld, top=10**9))
        pct = 100 * pop / max(tot, 1)
        print(f"  {name:<24} | {str(fld):<30} | {pop:>9,} | {pct:>5.1f}% | {distinct:>8,}")
    print()

    # Ambiguous-label candidates: report coverage for EVERY candidate field, so
    # Khaled can pick the real dedicated signal (two fields share each label).
    print(f"  AMBIGUOUS-LABEL CANDIDATE COVERAGE (every field sharing the label):")
    print(f"  {'candidate field':<34} | {'populated':>9} | {'%':>6} | distinct")
    print(f"  {'-'*34}-+-{'-'*9}-+-{'-'*6}-+-{'-'*8}")
    for fld in ambiguous:
        pop, tot = _cov(fld)
        distinct = len(_topvals(fld, top=10**9))
        pct = 100 * pop / max(tot, 1)
        print(f"  {fld:<34} | {pop:>9,} | {pct:>5.1f}% | {distinct:>8,}")
    print()

    # Top values for the staff-label dimensions (safe to show).
    for name, fld in (("Media Buyer", mb_field), ("Media Buyer Manager", mbm_field)):
        c = _topvals(fld)
        print(f"  TOP values — {name} ({fld!r}); {len(c)} distinct (internal staff labels):")
        if not c:
            print("     (field empty across the entire population)")
        for val, n in c.most_common(12):
            print(f"     {n:>8,}  {val}")
        print()

    # ===== STEP 5: stage availability & end-to-end overlap =====
    print(_SEP)
    print("  STEP 5 — STAGE AVAILABILITY & END-TO-END OVERLAP")
    print(f"  population = ALL incl. archived  domain={pop_domain}  total={total:,}")
    print(_SEP)

    # stage distribution via read_group (independent cross-check)
    rg = await client.execute_kw(
        _MODEL, "read_group",
        args=[pop_domain, ["stage_id"], ["stage_id"]],
        kwargs={"lazy": False},
    )
    print(f"  STAGE DISTRIBUTION (read_group on stage_id over population); "
          f"{len(rg)} distinct stages:")
    print(f"     {'stage':<40} | {'leads':>8}")
    print(f"     {'-'*40}-+-{'-'*8}")
    rg_sorted = sorted(rg, key=lambda r: -(r.get("__count") or 0))
    rg_total = 0
    for r in rg_sorted:
        st = r.get("stage_id")
        nm = st[1] if isinstance(st, (list, tuple)) and len(st) == 2 else "<no stage>"
        n = r.get("__count") or 0
        rg_total += n
        print(f"     {str(nm):<40} | {n:>8,}")
    print(f"     {'TOTAL (read_group)':<40} | {rg_total:>8,}")
    print()

    # end-to-end overlap: usable buyer signal AND a stage.
    # Two definitions of "Media Buyer populated":
    #   (a) the primary resolved field  (direct_media_buyer_id)
    #   (b) the BEST available — union of BOTH dedicated fields (the second,
    #       media_buyer_id, has higher coverage), to size the true addressable set.
    mb2_field = next((c for c in candidates.get("Media Buyer", []) if c != mb_field), None)
    stage_by_id = {r["id"]: bool(r.get("stage_id")) for r in cov_rows}
    mb_by_id = {r["id"]: _is_nonempty(r.get(mb_field)) for r in cov_rows} if mb_field else {}
    mb_any_by_id = {
        r["id"]: (_is_nonempty(r.get(mb_field)) if mb_field else False)
                 or (_is_nonempty(r.get(mb2_field)) if mb2_field else False)
        for r in cov_rows
    }

    has_conv = has_mb = has_signal = signal_and_stage = 0
    has_signal_best = best_and_stage = 0
    has_stage_total = sum(1 for v in stage_by_id.values() if v)
    for rid, has_stage in stage_by_id.items():
        cn = cn_by_id.get(rid) if cn_field else None
        conv = bool(cn) and bool(_CONVENTION.match(str(cn).strip()))
        mb_pop = mb_by_id.get(rid, False)
        signal = conv or mb_pop
        signal_best = conv or mb_any_by_id.get(rid, False)
        if conv:
            has_conv += 1
        if mb_pop:
            has_mb += 1
        if signal:
            has_signal += 1
            if has_stage:
                signal_and_stage += 1
        if signal_best:
            has_signal_best += 1
            if has_stage:
                best_and_stage += 1

    print(f"  USABLE BUYER SIGNAL = (convention-matching Campaign Name) OR (Media Buyer populated)")
    print(f"  -- definition A: Media Buyer = primary resolved field ({mb_field!r}) --")
    print(f"     leads with convention-matching Campaign Name : {has_conv:,}")
    print(f"     leads with Media Buyer populated             : {has_mb:,}")
    print(f"     leads with ANY usable buyer signal           : {has_signal:,}  "
          f"({100*has_signal/max(total,1):.1f}% of {total:,})")
    print(f"     leads with a stage (any)                     : {has_stage_total:,}  "
          f"({100*has_stage_total/max(total,1):.1f}%)")
    print(f"     END-TO-END overlap (signal AND stage)        : {signal_and_stage:,}  "
          f"({100*signal_and_stage/max(total,1):.1f}% of population; "
          f"{100*signal_and_stage/max(has_signal,1):.1f}% of signal-bearing)")
    print(f"  -- definition B: BEST available (conv OR {mb_field!r} OR {mb2_field!r}) --")
    print(f"     leads with ANY usable buyer signal (best)    : {has_signal_best:,}  "
          f"({100*has_signal_best/max(total,1):.1f}% of {total:,})")
    print(f"     END-TO-END overlap (best signal AND stage)   : {best_and_stage:,}  "
          f"({100*best_and_stage/max(total,1):.1f}% of population; "
          f"{100*best_and_stage/max(has_signal_best,1):.1f}% of signal-bearing)")
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    run_at = datetime.now(timezone.utc)
    print(_SEP)
    print("  M-DISCOVERY — Marketing Attribution live-data discovery (READ-ONLY, aggregates only)")
    print(f"  Run at (UTC) : {run_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  Model        : {_MODEL}")
    print(f"  ALLOWED_METHODS: {sorted(ALLOWED_METHODS)}")
    print("  READ-ONLY. Direct JSON-RPC. No FastAPI. No OpenAI. AI cost = $0.00")
    print(_SEP)
    print()

    async with OdooClient() as client:
        res = await _resolve_fields(client)
        await _population_baseline(client)
        await _analyze(client, res["resolved"], res["candidates"])

    print(_SEP)
    print("  M-DISCOVERY COMPLETE — numbers only. No build, no KPI design, no product decision.")
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
