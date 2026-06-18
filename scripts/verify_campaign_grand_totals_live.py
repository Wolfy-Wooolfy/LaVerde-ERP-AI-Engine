"""
scripts/verify_campaign_grand_totals_live.py — Campaign Performance (Level 1,
PINNED GRAND TOTALS) LIVE verification (READ-ONLY, $0 AI).

Proves the window-independent grand-totals block is exact and ties to the shipped
numbers:

  §G1 — INCL ties to the population: get_campaign_grand_totals().incl.total equals an
        INDEPENDENT full search_count (active_test=False, migration INCLUDED) AND the
        shipped overview's total_leads_population (expected 146,925).
  §G2 — EXCL is incl − migration two independent ways: excl.total == incl.total −
        (Σ legacy-day search_counts) AND == (full population − Σ legacy-day counts)
        (expected ~21,156 = 146,925 − ~125,769).
  §G3 — Σ groups reconcile: Σ incl groups == incl.total ; Σ excl groups == excl.total.
  §G4 — Per-group subtraction: for every group excl[g] == incl[g] − migration[g] and
        excl[g] >= 0, where migration[g] is re-derived INDEPENDENTLY (a fresh
        read_group on the legacy-day OR-domain, classified through the shared
        classify_stage).
  §G5 — INCL funnel == overview aggregate funnel (regression): the incl 4-group funnel
        equals Σ over the overview's per-campaign rows + long_tail + junk + no_campaign
        — i.e. no drift from the shipped per-campaign numbers.

Method discipline: READ-ONLY (search_read / read_group / search_count only).
ALLOWED_METHODS untouched. No FastAPI. No OpenAI. AI cost = $0.00.

Pre-flight (run manually BEFORE this script): kill all python; purge all
__pycache__. Talks to Odoo directly.

Usage (from project root):
    python scripts/verify_campaign_grand_totals_live.py
"""

import asyncio
import io
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.modules.campaign_performance import domain  # noqa: E402
from backend.modules.campaign_performance.domain import (  # noqa: E402
    GROUP_ORDER,
    classify_stage,
)
from backend.modules.campaign_performance.services import cache as _cp_cache  # noqa: E402
from backend.modules.campaign_performance.services.campaign_service import (  # noqa: E402
    get_campaign_grand_totals,
    get_campaign_performance_overview,
)
from backend.modules.campaign_performance.services.timeline_service import (  # noqa: E402
    get_legacy_migration_days,
)
from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient  # noqa: E402

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_LEAD = "crm.lead"
_STAGE = "crm.stage"
_SEP = "=" * 100
_CTX_ALL = {"active_test": False}
_CAIRO = ZoneInfo("Africa/Cairo")


def _ok(flag: bool) -> str:
    return "PASS" if flag else "**FAIL**"


def _cairo_to_utc_str(cairo_dt: datetime) -> str:
    return cairo_dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _day_bounds_utc(day_str: str) -> tuple[str, str]:
    """A Cairo "YYYY-MM-DD" day → its [day_start, next_day_start) UTC bounds. SAME
    Cairo→UTC handling the service's _legacy_days_domain uses (fresh next-day date,
    DST-safe)."""
    d = datetime.strptime(day_str, "%Y-%m-%d").date()
    nxt = d + timedelta(days=1)
    lo = datetime(d.year, d.month, d.day, tzinfo=_CAIRO)
    hi = datetime(nxt.year, nxt.month, nxt.day, tzinfo=_CAIRO)
    return _cairo_to_utc_str(lo), _cairo_to_utc_str(hi)


def _legacy_domain(days) -> list:
    """OR of each legacy day's [day_start, next_day_start) UTC range (positive)."""
    ranges = []
    for d in sorted(days):
        lo, hi = _day_bounds_utc(d)
        ranges.append(["&", ("create_date", ">=", lo), ("create_date", "<", hi)])
    dom = ["|"] * (len(ranges) - 1)
    for rng in ranges:
        dom.extend(rng)
    return dom


async def _count(client, dom):
    return await client.execute_kw(
        _LEAD, "search_count", args=[dom], kwargs={"context": _CTX_ALL}
    )


def _groups(line) -> dict:
    return {o["group"]: o["count"] for o in line["groups"]}


async def main():
    now_cairo = datetime.now(_CAIRO)

    print(_SEP)
    print("  CAMPAIGN PERFORMANCE (LEVEL 1 — PINNED GRAND TOTALS) LIVE VERIFICATION (READ-ONLY, $0)")
    print(f"  ALLOWED_METHODS : {sorted(ALLOWED_METHODS)}")
    print(f"  Population/ctx  : ALL leads incl. archived — context={_CTX_ALL}")
    print(f"  Today (Cairo)   : {now_cairo.date().isoformat()}")
    print(f"  Tunables        : LEGACY_DAY_MIN={domain.LEGACY_DAY_MIN:,}")
    print(_SEP)
    print()

    fail_count = 0
    _cp_cache.clear()

    async with OdooClient() as client:
        detected = await get_legacy_migration_days(client)
        gt = await get_campaign_grand_totals(client=client)
        ov = await get_campaign_performance_overview(client=client)

        incl = _groups(gt["incl"])
        excl = _groups(gt["excl"])

        # stage_info for independent classification of the migration slice
        stages = await client.execute_kw(
            _STAGE, "search_read", args=[[]], kwargs={"fields": ["id", "name", "is_won"]}
        )
        stage_info = {
            int(s["id"]): {"name": str(s.get("name") or ""), "is_won": bool(s.get("is_won"))}
            for s in stages
        }

        full_pop = await _count(client, [])

        # ══════════════════════════════════════════════════════════════════════
        # §G1 — INCL ties to the full population + the overview total
        # ══════════════════════════════════════════════════════════════════════
        print(_SEP)
        print("  §G1 — INCL total == independent full population == overview population")
        print(_SEP)
        g1_ok = gt["incl"]["total"] == full_pop == ov["total_leads_population"]
        fail_count += 0 if g1_ok else 1
        print(f"  grand incl.total = {gt['incl']['total']:,}")
        print(f"  independent full search_count (incl. migration) = {full_pop:,}")
        print(f"  overview total_leads_population                  = {ov['total_leads_population']:,}")
        print(f"  §G1 all three equal                                        {_ok(g1_ok)}")
        print()

        # ══════════════════════════════════════════════════════════════════════
        # §G2 — EXCL == incl − migration, two independent ways
        # ══════════════════════════════════════════════════════════════════════
        print(_SEP)
        print("  §G2 — EXCL total == incl − migration  (== full − Σ legacy-day counts)")
        print(_SEP)
        sum_days = 0
        for d in sorted(detected):
            lo, hi = _day_bounds_utc(d)
            n = await _count(client, [("create_date", ">=", lo), ("create_date", "<", hi)])
            sum_days += n
            print(f"     legacy day {d}  count={n:>8,}")
        # service-reported migration vs independent OR-domain count
        mig_count = await _count(client, _legacy_domain(detected)) if detected else 0
        mig_match = mig_count == sum_days == gt["migration_total"]
        indep_excl = full_pop - sum_days
        g2_ok = (
            gt["excl"]["total"] == gt["incl"]["total"] - gt["migration_total"] == indep_excl
            and mig_match
        )
        fail_count += 0 if g2_ok else 1
        print(f"  migration_total (service) = {gt['migration_total']:,}  "
              f"OR-domain count = {mig_count:,}  Σ days = {sum_days:,}   {_ok(mig_match)}")
        print(f"  grand excl.total = {gt['excl']['total']:,}")
        print(f"  incl − migration = {gt['incl']['total'] - gt['migration_total']:,}")
        print(f"  full − Σ days    = {indep_excl:,}")
        print(f"  §G2 excl reconciles two independent ways                   {_ok(g2_ok)}")
        print()

        # ══════════════════════════════════════════════════════════════════════
        # §G3 — Σ groups reconcile to each total
        # ══════════════════════════════════════════════════════════════════════
        print(_SEP)
        print("  §G3 — Σ incl groups == incl.total ; Σ excl groups == excl.total")
        print(_SEP)
        g3_ok = (
            sum(incl.values()) == gt["incl"]["total"]
            and sum(excl.values()) == gt["excl"]["total"]
        )
        fail_count += 0 if g3_ok else 1
        print(f"  Σ incl groups = {sum(incl.values()):,}  ==  incl.total {gt['incl']['total']:,}")
        print(f"  Σ excl groups = {sum(excl.values()):,}  ==  excl.total {gt['excl']['total']:,}")
        print(f"  §G3 group sums reconcile                                   {_ok(g3_ok)}")
        print()

        # ══════════════════════════════════════════════════════════════════════
        # §G4 — per-group: excl[g] == incl[g] − migration[g]  AND  excl[g] >= 0
        # ══════════════════════════════════════════════════════════════════════
        print(_SEP)
        print("  §G4 — per-group excl == incl − migration  &  excl >= 0  (migration re-derived)")
        print(_SEP)
        mig_groups = {g: 0 for g in GROUP_ORDER}
        if detected:
            mig_rows = await client.execute_kw(
                _LEAD, "read_group",
                args=[_legacy_domain(detected), ["stage_id"], ["stage_id"]],
                kwargs={"context": _CTX_ALL, "lazy": False},
            )
            for r in mig_rows:
                cnt = int(r.get("__count") or 0)
                sid_raw = r.get("stage_id")
                sid = int(sid_raw[0]) if isinstance(sid_raw, (list, tuple)) else None
                mig_groups[classify_stage(sid, stage_info)] += cnt
        g4_ok = True
        for g in GROUP_ORDER:
            row_ok = excl[g] == incl[g] - mig_groups[g] and excl[g] >= 0
            g4_ok = g4_ok and row_ok
            print(f"     {g:<10} incl={incl[g]:>8,}  migration={mig_groups[g]:>8,}  "
                  f"excl={excl[g]:>8,}  {_ok(row_ok)}")
        fail_count += 0 if g4_ok else 1
        print(f"  §G4 per-group subtraction + non-negative                  {_ok(g4_ok)}")
        print()

        # ══════════════════════════════════════════════════════════════════════
        # §G5 — INCL funnel == overview aggregate funnel (no drift)
        # ══════════════════════════════════════════════════════════════════════
        print(_SEP)
        print("  §G5 — INCL funnel == overview aggregate (rows + long_tail + junk + no_campaign)")
        print(_SEP)
        agg = {g: 0 for g in GROUP_ORDER}
        for c in ov["campaigns"]:
            for o in c["outcomes"]:
                agg[o["group"]] += o["count"]
        if ov["long_tail"]:
            for o in ov["long_tail"]["outcomes"]:
                agg[o["group"]] += o["count"]
        for bkey in ("junk_none", "no_campaign"):
            b = ov["data_quality"][bkey]
            if b:
                for o in b["outcomes"]:
                    agg[o["group"]] += o["count"]
        g5_ok = all(agg[g] == incl[g] for g in GROUP_ORDER)
        fail_count += 0 if g5_ok else 1
        for g in GROUP_ORDER:
            row_ok = agg[g] == incl[g]
            print(f"     {g:<10} overview_agg={agg[g]:>8,}  grand_incl={incl[g]:>8,}  {_ok(row_ok)}")
        print(f"  §G5 incl funnel ties to overview aggregate                {_ok(g5_ok)}")
        print()

    print(_SEP)
    if fail_count == 0:
        print("  GRAND-TOTALS VERIFICATION COMPLETE — ALL CHECKS (§G1–§G5) PASSED.")
    else:
        print(f"  GRAND-TOTALS VERIFICATION COMPLETE — {fail_count} CHECK(S) FAILED. STOP and report.")
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
