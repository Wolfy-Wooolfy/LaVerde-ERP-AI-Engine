"""
scripts/verify_marketing_attribution_grand_coverage_live.py — Marketing Attribution
(media-buyer page, PINNED ALL-TIME GRAND COVERAGE) LIVE verification (READ-ONLY, $0 AI).

Proves the window-independent attribution-coverage footer is exact and ties to the
shipped numbers — the buyer-page parallel of verify_campaign_grand_totals_live.py:

  §C1 — INCL ties to the shipped overview (no regression): grand_coverage.incl
        attributed_total / population / coverage_pct equal get_attribution_overview's
        total_attributed (~77,164) / total_leads_population (~146,925) /
        attribution_pct (~52.5%).
  §C2 — Σ groups reconcile: Σ incl groups == incl.attributed_total ; Σ excl groups ==
        excl.attributed_total.
  §C3 — Per-group subtraction: for every group excl[g] == incl[g] − migration_attr[g]
        and excl[g] >= 0, where migration_attr[g] is re-derived INDEPENDENTLY (a fresh
        read_group on the (attributing campaigns AND legacy-day OR-domain), classified
        through the shared classify_stage). Also Σ migration_attr == the service's
        migration_attributed_total.
  §C4 — population_excl == population − migration_total AND coverage_excl ==
        excl.attributed_total / population_excl (the ongoing, non-migration coverage).
  §C5 — migration_total sanity: grand_coverage.migration_total ties to Σ legacy-day
        search_counts AND to the campaign grand_totals migration_total (~125,769).

Method discipline: READ-ONLY (search_read / read_group / search_count only).
ALLOWED_METHODS untouched. No FastAPI. No OpenAI. AI cost = $0.00.

Pre-flight (run manually BEFORE this script): kill all python; purge all
__pycache__. Talks to Odoo directly.

Usage (from project root):
    python scripts/verify_marketing_attribution_grand_coverage_live.py
"""

import asyncio
import io
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.modules.campaign_performance import domain  # noqa: E402
from backend.modules.campaign_performance.services import cache as _cp_cache  # noqa: E402
from backend.modules.campaign_performance.services.campaign_service import (  # noqa: E402
    get_campaign_grand_totals,
)
from backend.modules.campaign_performance.services.timeline_service import (  # noqa: E402
    get_legacy_migration_days,
)
from backend.modules.marketing_attribution.domain import (  # noqa: E402
    CAMPAIGN_FIELD,
    GROUP_ORDER,
    classify_stage,
)
from backend.modules.marketing_attribution.services import cache as _ma_cache  # noqa: E402
from backend.modules.marketing_attribution.services.attribution_service import (  # noqa: E402
    get_attribution_grand_coverage,
    get_attribution_overview,
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
    print("  MARKETING ATTRIBUTION (MEDIA-BUYER PAGE — PINNED ALL-TIME GRAND COVERAGE) LIVE VERIFICATION (READ-ONLY, $0)")
    print(f"  ALLOWED_METHODS : {sorted(ALLOWED_METHODS)}")
    print(f"  Population/ctx  : ALL leads incl. archived — context={_CTX_ALL}")
    print(f"  Today (Cairo)   : {now_cairo.date().isoformat()}")
    print(f"  Tunables        : LEGACY_DAY_MIN={domain.LEGACY_DAY_MIN:,}")
    print(_SEP)
    print()

    fail_count = 0
    _cp_cache.clear()
    _ma_cache.clear()

    async with OdooClient() as client:
        detected = await get_legacy_migration_days(client)
        gc = await get_attribution_grand_coverage(client=client)
        ov = await get_attribution_overview(client=client)
        cp_gt = await get_campaign_grand_totals(client=client)

        incl = _groups(gc["incl"])
        excl = _groups(gc["excl"])

        # stage_info for independent classification of the migration-attributed slice
        stages = await client.execute_kw(
            _STAGE, "search_read", args=[[]], kwargs={"fields": ["id", "name", "is_won"]}
        )
        stage_info = {
            int(s["id"]): {"name": str(s.get("name") or ""), "is_won": bool(s.get("is_won"))}
            for s in stages
        }

        # attributing campaign ids — straight from the shipped overview's confirmed set
        attributing_ids = sorted({int(c["campaign_id"]) for c in ov["confirmed_campaigns"]})

        # ══════════════════════════════════════════════════════════════════════
        # §C1 — INCL ties to the shipped overview (no regression)
        # ══════════════════════════════════════════════════════════════════════
        print(_SEP)
        print("  §C1 — INCL attributed/population/coverage == the shipped overview")
        print(_SEP)
        c1_ok = (
            gc["incl"]["attributed_total"] == ov["total_attributed"]
            and gc["incl"]["population"] == ov["total_leads_population"]
            and gc["incl"]["coverage_pct"] == ov["attribution_pct"]
        )
        fail_count += 0 if c1_ok else 1
        print(f"  grand incl.attributed_total = {gc['incl']['attributed_total']:,}   "
              f"overview total_attributed = {ov['total_attributed']:,}")
        print(f"  grand incl.population       = {gc['incl']['population']:,}   "
              f"overview total_leads_population = {ov['total_leads_population']:,}")
        print(f"  grand incl.coverage_pct     = {gc['incl']['coverage_pct']}%   "
              f"overview attribution_pct = {ov['attribution_pct']}%")
        print(f"  §C1 incl ties 1:1 to the overview                          {_ok(c1_ok)}")
        print()

        # ══════════════════════════════════════════════════════════════════════
        # §C2 — Σ groups reconcile to each attributed total
        # ══════════════════════════════════════════════════════════════════════
        print(_SEP)
        print("  §C2 — Σ incl groups == incl.attributed_total ; Σ excl groups == excl.attributed_total")
        print(_SEP)
        c2_ok = (
            sum(incl.values()) == gc["incl"]["attributed_total"]
            and sum(excl.values()) == gc["excl"]["attributed_total"]
        )
        fail_count += 0 if c2_ok else 1
        print(f"  Σ incl groups = {sum(incl.values()):,}  ==  incl.attributed_total {gc['incl']['attributed_total']:,}")
        print(f"  Σ excl groups = {sum(excl.values()):,}  ==  excl.attributed_total {gc['excl']['attributed_total']:,}")
        print(f"  §C2 group sums reconcile                                   {_ok(c2_ok)}")
        print()

        # ══════════════════════════════════════════════════════════════════════
        # §C3 — per-group excl == incl − migration_attr (re-derived) & excl >= 0
        # ══════════════════════════════════════════════════════════════════════
        print(_SEP)
        print("  §C3 — per-group excl == incl − migration_attr  &  excl >= 0  (migration_attr re-derived)")
        print(_SEP)
        mig_attr_groups = {g: 0 for g in GROUP_ORDER}
        if detected and attributing_ids:
            mig_attr_domain = ["&", (CAMPAIGN_FIELD, "in", attributing_ids), *_legacy_domain(detected)]
            mig_attr_rows = await client.execute_kw(
                _LEAD, "read_group",
                args=[mig_attr_domain, ["stage_id"], ["stage_id"]],
                kwargs={"context": _CTX_ALL, "lazy": False},
            )
            for r in mig_attr_rows:
                cnt = int(r.get("__count") or 0)
                sid_raw = r.get("stage_id")
                sid = int(sid_raw[0]) if isinstance(sid_raw, (list, tuple)) else None
                mig_attr_groups[classify_stage(sid, stage_info)] += cnt
        c3_ok = True
        for g in GROUP_ORDER:
            row_ok = excl[g] == incl[g] - mig_attr_groups[g] and excl[g] >= 0
            c3_ok = c3_ok and row_ok
            print(f"     {g:<10} incl={incl[g]:>8,}  migration_attr={mig_attr_groups[g]:>8,}  "
                  f"excl={excl[g]:>8,}  {_ok(row_ok)}")
        mig_attr_sum_ok = sum(mig_attr_groups.values()) == gc["migration_attributed_total"]
        c3_ok = c3_ok and mig_attr_sum_ok
        fail_count += 0 if c3_ok else 1
        print(f"  Σ migration_attr = {sum(mig_attr_groups.values()):,}  ==  "
              f"service migration_attributed_total {gc['migration_attributed_total']:,}   {_ok(mig_attr_sum_ok)}")
        print(f"  §C3 per-group subtraction + non-negative                  {_ok(c3_ok)}")
        print()

        # ══════════════════════════════════════════════════════════════════════
        # §C4 — population_excl == population − migration_total ; coverage_excl exact
        # ══════════════════════════════════════════════════════════════════════
        print(_SEP)
        print("  §C4 — population_excl == population − migration_total ; coverage_excl == excl/pop_excl")
        print(_SEP)
        pop_excl = gc["incl"]["population"] - gc["migration_total"]
        cov_excl = round(100.0 * gc["excl"]["attributed_total"] / pop_excl, 2) if pop_excl else 0.0
        c4_ok = (
            gc["excl"]["population"] == pop_excl
            and gc["excl"]["coverage_pct"] == cov_excl
        )
        fail_count += 0 if c4_ok else 1
        print(f"  grand excl.population = {gc['excl']['population']:,}   "
              f"population − migration_total = {pop_excl:,}")
        print(f"  grand excl.coverage_pct = {gc['excl']['coverage_pct']}%   "
              f"excl/pop_excl = {cov_excl}%   (incl coverage = {gc['incl']['coverage_pct']}%)")
        print(f"  §C4 excl population + coverage exact                       {_ok(c4_ok)}")
        print()

        # ══════════════════════════════════════════════════════════════════════
        # §C5 — migration_total sanity: Σ legacy-day counts AND campperf grand_totals
        # ══════════════════════════════════════════════════════════════════════
        print(_SEP)
        print("  §C5 — migration_total == Σ legacy-day counts == campaign grand_totals migration_total")
        print(_SEP)
        sum_days = 0
        for d in sorted(detected):
            lo, hi = _day_bounds_utc(d)
            n = await _count(client, [("create_date", ">=", lo), ("create_date", "<", hi)])
            sum_days += n
            print(f"     legacy day {d}  count={n:>8,}")
        c5_ok = gc["migration_total"] == sum_days == cp_gt["migration_total"]
        fail_count += 0 if c5_ok else 1
        print(f"  grand_coverage migration_total = {gc['migration_total']:,}")
        print(f"  Σ legacy-day counts            = {sum_days:,}")
        print(f"  campaign grand_totals migration_total = {cp_gt['migration_total']:,}")
        print(f"  §C5 migration_total ties three ways                       {_ok(c5_ok)}")
        print()

    print(_SEP)
    if fail_count == 0:
        print("  GRAND-COVERAGE VERIFICATION COMPLETE — ALL CHECKS (§C1–§C5) PASSED.")
    else:
        print(f"  GRAND-COVERAGE VERIFICATION COMPLETE — {fail_count} CHECK(S) FAILED. STOP and report.")
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
