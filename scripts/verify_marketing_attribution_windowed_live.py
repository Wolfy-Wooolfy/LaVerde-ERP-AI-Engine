"""
scripts/verify_marketing_attribution_windowed_live.py — Marketing Attribution
(WINDOWED per-media-buyer) LIVE verification (READ-ONLY, $0 AI).

Proves the windowed buyer view scopes correctly, ties 1:1 to the already-verified
campaign windowing, and does not regress the shipped all-time attribution page:

  §M1 — WINDOWED BUYER == CAMPAIGN WINDOWED (current month): each windowed buyer's
        total + 4-group funnel equals Σ over that buyer's attributing campaigns of the
        campaign-windowed current-month row (get_campaign_performance_windowed). With
        the 1:1 confirmed map this is Ahmed==FB-AY, Yomna==Outsource-Y, Ali==FB-LA,
        Abdallah==FB-AM. (The campaign windowing is itself independently cross-checked
        against the per-campaign timeline + direct search_count in its own verifier.)
  §M2 — GLOBAL windowed identity: Σ buyers' attributed + unattributed == windowed
        population; AND that population EQUALS the campaign-windowed population for the
        same window (both window the SAME migration-excluded lead set).
  §M3 — COVERAGE: coverage_pct == round(100 * attributed / windowed population, 2),
        and total_attributed == Σ buyers' totals.
  §M4 — ALL-TIME regression: the shipped un-windowed overview (the "All-time" preset's
        path) still reconciles — each buyer's total_attributed equals an INDEPENDENT
        search_count over that buyer's attributing campaign ids (migration INCLUDED) —
        plus its attribution_pct / total_attributed / population are reported.
  §M5 — CUSTOM range crossing Nov-2025 EXCLUDES the migration: for 2025-10..2026-01,
        the detected legacy days in range are reported as excluded, legacy is actually
        removed (>0), the windowed population matches the campaign-windowed population
        for the same custom range, and attributed + unattributed reconciles.

Method discipline: READ-ONLY (search_read / read_group / search_count only).
ALLOWED_METHODS untouched. No FastAPI. No OpenAI. AI cost = $0.00.

Pre-flight (run manually BEFORE this script): kill all python; purge all
__pycache__; (uvicorn, if used, WITHOUT --reload). Talks to Odoo directly.

Usage (from project root):
    python scripts/verify_marketing_attribution_windowed_live.py
"""

import asyncio
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.modules.campaign_performance import domain as cp_domain  # noqa: E402
from backend.modules.campaign_performance.services import cache as _cp_cache  # noqa: E402
from backend.modules.campaign_performance.services.campaign_service import (  # noqa: E402
    get_campaign_performance_windowed,
)
from backend.modules.campaign_performance.services.timeline_service import (  # noqa: E402
    get_legacy_migration_days,
)
from backend.modules.marketing_attribution.domain import GROUP_ORDER  # noqa: E402
from backend.modules.marketing_attribution.services import cache as _ma_cache  # noqa: E402
from backend.modules.marketing_attribution.services.attribution_service import (  # noqa: E402
    get_attribution_overview,
    get_attribution_overview_windowed,
)
from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient  # noqa: E402

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_LEAD = "crm.lead"
_SEP = "=" * 100
_CTX_ALL = {"active_test": False}


def _ok(flag: bool) -> str:
    return "PASS" if flag else "**FAIL**"


def _groups(outcomes: list[dict]) -> dict[str, int]:
    return {o["group"]: o["count"] for o in outcomes}


async def _count(client, dom):
    return await client.execute_kw(
        _LEAD, "search_count", args=[dom], kwargs={"context": _CTX_ALL}
    )


async def main():
    print(_SEP)
    print("  MARKETING ATTRIBUTION (WINDOWED MEDIA-BUYER) LIVE VERIFICATION (READ-ONLY, $0)")
    print(f"  ALLOWED_METHODS : {sorted(ALLOWED_METHODS)}")
    print(f"  Population/ctx  : ALL leads incl. archived — context={_CTX_ALL}")
    print(f"  Default window  : {cp_domain.DEFAULT_WINDOW}  presets={list(cp_domain.WINDOW_PRESETS)}")
    print(_SEP)
    print()

    fail_count = 0
    _cp_cache.clear()
    _ma_cache.clear()

    async with OdooClient() as client:
        detected = await get_legacy_migration_days(client)

        # ══════════════════════════════════════════════════════════════════════
        # §M1 — WINDOWED buyer (current) == Σ its campaigns' campaign-windowed rows
        # ══════════════════════════════════════════════════════════════════════
        print(_SEP)
        print("  §M1 — WINDOWED buyer (current month) == Σ its attributing campaigns' campaign-windowed rows")
        print(_SEP)
        mbw = await get_attribution_overview_windowed(client=client, window="current")
        cw = await get_campaign_performance_windowed(client=client, window="current")
        cw_rows = {c["campaign_id"]: c for c in cw["campaigns"]}
        print(f"  window={mbw['window']} [{mbw['window_start_month']}..{mbw['window_end_month']}]  "
              f"buyers={len(mbw['buyers'])}  windowed_pop={mbw['total_leads_population']:,}")
        m1_ok = True
        for b in mbw["buyers"]:
            exp_total = sum(cw_rows.get(cid, {}).get("lead_count", 0) for cid in b["campaign_ids"])
            exp_groups = {g: 0 for g in GROUP_ORDER}
            for cid in b["campaign_ids"]:
                row = cw_rows.get(cid)
                if row:
                    for o in row["outcomes"]:
                        exp_groups[o["group"]] += o["count"]
            bg = _groups(b["outcomes"])
            row_ok = (exp_total == b["total_attributed"]) and all(
                exp_groups[g] == bg.get(g, 0) for g in GROUP_ORDER
            )
            m1_ok = m1_ok and row_ok
            camps = [cw_rows.get(cid, {}).get("campaign_name", cid) for cid in b["campaign_ids"]]
            print(f"     {b['buyer_name'][:22]:<23} BUYER={b['total_attributed']:>6,}  "
                  f"Σcampaigns={exp_total:>6,}  via {camps}  {_ok(row_ok)}")
        fail_count += 0 if m1_ok else 1
        print(f"  §M1 windowed buyer == campaign windowed (per-buyer total + funnel)   {_ok(m1_ok)}")
        print()

        # ══════════════════════════════════════════════════════════════════════
        # §M2 — GLOBAL windowed identity + independent population cross-check
        # ══════════════════════════════════════════════════════════════════════
        print(_SEP)
        print("  §M2 — GLOBAL windowed identity (attributed + unattributed == windowed population)")
        print(_SEP)
        attributed = sum(b["total_attributed"] for b in mbw["buyers"])
        ua = mbw["unattributed"]["lead_count"]
        recon_ok = (attributed + ua) == mbw["total_leads_population"]
        pop_ok = mbw["total_leads_population"] == cw["total_leads_population"]
        fail_count += 0 if (recon_ok and pop_ok) else 1
        print(f"  Σ attributed={attributed:,}  unattributed={ua:,}  "
              f"Σ={attributed + ua:,}  ==  windowed population {mbw['total_leads_population']:,}  {_ok(recon_ok)}")
        print(f"  windowed population {mbw['total_leads_population']:,}  ==  campaign-windowed population "
              f"{cw['total_leads_population']:,} (same migration-excluded set)  {_ok(pop_ok)}")
        print()

        # ══════════════════════════════════════════════════════════════════════
        # §M3 — COVERAGE
        # ══════════════════════════════════════════════════════════════════════
        print(_SEP)
        print("  §M3 — COVERAGE (coverage_pct == attributed / windowed population)")
        print(_SEP)
        pop = mbw["total_leads_population"]
        cov_exp = round(100.0 * attributed / pop, 2) if pop else 0.0
        cov_ok = (mbw["coverage_pct"] == cov_exp) and (attributed == mbw["total_attributed"])
        fail_count += 0 if cov_ok else 1
        print(f"  coverage_pct={mbw['coverage_pct']:.2f}%  expected={cov_exp:.2f}%  "
              f"total_attributed={mbw['total_attributed']:,} == Σ buyers {attributed:,}  {_ok(cov_ok)}")
        print()

        # ══════════════════════════════════════════════════════════════════════
        # §M4 — ALL-TIME overview unchanged (the "All-time" preset path; no regression)
        # ══════════════════════════════════════════════════════════════════════
        print(_SEP)
        print("  §M4 — ALL-TIME overview regression (per-buyer total == independent search_count, migration INCLUDED)")
        print(_SEP)
        ov = await get_attribution_overview(client=client)
        m4_ok = True
        for b in ov["buyers"]:
            odoo_total = await _count(client, [("campaign_id", "in", b["campaign_ids"])])
            t_ok = odoo_total == b["total_attributed"]
            m4_ok = m4_ok and t_ok
            print(f"     {b['buyer_name'][:22]:<23} MODULE={b['total_attributed']:>7,}  "
                  f"ODOO={odoo_total:>7,}  {_ok(t_ok)}")
        fail_count += 0 if m4_ok else 1
        print(f"  attribution_pct={ov['attribution_pct']:.2f}%  total_attributed={ov['total_attributed']:,}  "
              f"population={ov['total_leads_population']:,}")
        print(f"  §M4 all-time per-buyer totals reconcile                              {_ok(m4_ok)}")
        print()

        # ══════════════════════════════════════════════════════════════════════
        # §M5 — CUSTOM range 2025-10..2026-01 (crosses Nov-2025) excludes the migration
        # ══════════════════════════════════════════════════════════════════════
        print(_SEP)
        print("  §M5 — CUSTOM range 2025-10..2026-01 (crosses Nov-2025 migration) excludes legacy")
        print(_SEP)
        cust = await get_attribution_overview_windowed(
            client=client, start_month="2025-10", end_month="2026-01"
        )
        ccw = await get_campaign_performance_windowed(
            client=client, start_month="2025-10", end_month="2026-01"
        )
        legacy_in_range = sorted(d for d in detected if "2025-10" <= d[:7] <= "2026-01")
        excl_ok = set(legacy_in_range).issubset(set(cust["legacy_days_excluded"]))
        legacy_removed = len(legacy_in_range) > 0
        c_attr = sum(b["total_attributed"] for b in cust["buyers"])
        c_ua = cust["unattributed"]["lead_count"]
        c_ident_ok = (c_attr + c_ua) == cust["total_leads_population"]
        c_pop_ok = cust["total_leads_population"] == ccw["total_leads_population"]
        m5_ok = excl_ok and legacy_removed and c_ident_ok and c_pop_ok
        fail_count += 0 if m5_ok else 1
        print(f"  is_custom_range={cust['is_custom_range']}  window=[{cust['window_start_month']}.."
              f"{cust['window_end_month']}]  windowed_pop={cust['total_leads_population']:,}")
        print(f"  legacy days in range (excluded): {legacy_in_range}")
        print(f"  service legacy_days_excluded ⊇ in-range : {_ok(excl_ok)}")
        print(f"  legacy actually present in range (>0)   : {_ok(legacy_removed)}")
        print(f"  attributed {c_attr:,} + unattributed {c_ua:,} == population {cust['total_leads_population']:,}  {_ok(c_ident_ok)}")
        print(f"  windowed population == campaign-windowed population {ccw['total_leads_population']:,}  {_ok(c_pop_ok)}")
        print(f"  §M5 custom range excludes migration                                  {_ok(m5_ok)}")
        if cust["integrity_alerts"]:
            for a in cust["integrity_alerts"]:
                print(f"     INTEGRITY ALERT: {a}")
        print()

    print(_SEP)
    if fail_count == 0:
        print("  WINDOWED VERIFICATION COMPLETE — ALL CHECKS (§M1–§M5) PASSED.")
    else:
        print(f"  WINDOWED VERIFICATION COMPLETE — {fail_count} CHECK(S) FAILED/FLAGGED. STOP and report.")
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
