"""
scripts/verify_marketing_attribution_buyer_timeline_live.py — Marketing Attribution
(per-MEDIA-BUYER TIMELINE, Slice 3) LIVE verification (READ-ONLY, $0 AI).

Proves the per-buyer timeline ties 1:1 to the already-verified windowed board view
and is internally consistent, against live Odoo:

  §B1 — TIMELINE (last3 preset) == /windowed (last3) PER BUYER: for every media buyer
        on the windowed board (window="last3"), the buyer timeline's summed period
        funnel (total + 4 groups) equals that buyer's WindowedBuyerRow — EXACTLY
        (diff < 1.0 lead required; exact expected, same migration-excluded lead set,
        same Cairo window). The /windowed surface is itself independently cross-checked
        against the campaign windowing + direct search_count in its own verifier, so
        this anchors the timeline to verified ground truth.
  §B2 — INTERNAL consistency: each timeline's window months align with the board window;
        every period's 4 groups sum to its lead_count; and Σ periods' lead_count ==
        header.total_leads_in_window.
  §B3 — MIGRATION excluded: a buyer timeline's legacy_days_excluded equals the detected
        migration day set; AND a CUSTOM range crossing Nov-2025 (2025-10..2026-01)
        reports the in-range legacy days as excluded (and there is >0 to exclude).
  §B4 — NOT-FOUND path: a bogus buyer_id raises BuyerNotFoundError (the live 404 path);
        and every real confirmed buyer DID resolve a timeline (implicit from §B1).

Method discipline: READ-ONLY (search_read / read_group / search_count only).
ALLOWED_METHODS untouched. No FastAPI. No OpenAI. AI cost = $0.00.

Pre-flight (Decision 6.4, run manually BEFORE this script): kill all python; purge all
__pycache__; start uvicorn WITHOUT --reload. This script talks to Odoo directly.

Usage (from project root):
    python scripts/verify_marketing_attribution_buyer_timeline_live.py
"""

import asyncio
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.modules.campaign_performance.services import cache as _cp_cache  # noqa: E402
from backend.modules.campaign_performance.services.timeline_service import (  # noqa: E402
    get_legacy_migration_days,
)
from backend.modules.marketing_attribution.domain import GROUP_ORDER  # noqa: E402
from backend.modules.marketing_attribution.services import cache as _ma_cache  # noqa: E402
from backend.modules.marketing_attribution.services.attribution_service import (  # noqa: E402
    get_attribution_overview_windowed,
)
from backend.modules.marketing_attribution.services.buyer_timeline_service import (  # noqa: E402
    BuyerNotFoundError,
    get_buyer_timeline,
)
from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient  # noqa: E402

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_SEP = "=" * 100
_BOGUS_BUYER_ID = 1_000_000_000


def _ok(flag: bool) -> str:
    return "PASS" if flag else "**FAIL**"


def _groups(outcomes: list[dict]) -> dict[str, int]:
    return {o["group"]: o["count"] for o in outcomes}


def _sum_periods(tl: dict) -> tuple[int, dict[str, int]]:
    total = sum(p["lead_count"] for p in tl["periods"])
    groups = {g: 0 for g in GROUP_ORDER}
    for p in tl["periods"]:
        for o in p["outcomes"]:
            groups[o["group"]] += o["count"]
    return total, groups


async def main():
    print(_SEP)
    print("  MARKETING ATTRIBUTION (PER-MEDIA-BUYER TIMELINE) LIVE VERIFICATION (READ-ONLY, $0)")
    print(f"  ALLOWED_METHODS : {sorted(ALLOWED_METHODS)}")
    print(_SEP)
    print()

    fail_count = 0
    _cp_cache.clear()
    _ma_cache.clear()

    async with OdooClient() as client:
        detected = await get_legacy_migration_days(client)
        print(f"  detected legacy migration days: {sorted(detected)}")
        print()

        # ══════════════════════════════════════════════════════════════════════
        # §B1 — TIMELINE (last3) == /windowed (last3) per buyer  (the core identity)
        # ══════════════════════════════════════════════════════════════════════
        print(_SEP)
        print("  §B1 — buyer TIMELINE (last3) total + 4-group == that buyer's /windowed (last3) row")
        print(_SEP)
        mbw = await get_attribution_overview_windowed(client=client, window="last3")
        print(f"  /windowed window={mbw['window']} [{mbw['window_start_month']}..{mbw['window_end_month']}]  "
              f"buyers={len(mbw['buyers'])}  windowed_pop={mbw['total_leads_population']:,}")
        if not mbw["buyers"]:
            print("  **FLAG** no media buyers on the windowed board this window — nothing to reconcile.")
            fail_count += 1
        timelines: dict[int, dict] = {}
        b1_ok = True
        for b in mbw["buyers"]:
            tl = await get_buyer_timeline(client=client, buyer_id=b["buyer_id"], window_months=3)
            timelines[b["buyer_id"]] = tl
            tl_total, tl_groups = _sum_periods(tl)
            bg = _groups(b["outcomes"])
            diff = abs(tl_total - b["total_attributed"])
            groups_ok = all(tl_groups[g] == bg.get(g, 0) for g in GROUP_ORDER)
            window_ok = (
                tl["window_start_month"] == mbw["window_start_month"]
                and tl["window_end_month"] == mbw["window_end_month"]
            )
            row_ok = (diff < 1.0) and (diff == 0) and groups_ok and window_ok
            b1_ok = b1_ok and row_ok
            print(f"     {b['buyer_name'][:22]:<23} TIMELINE={tl_total:>6,}  /windowed={b['total_attributed']:>6,}  "
                  f"diff={diff}  groups={_ok(groups_ok)}  window={_ok(window_ok)}  {_ok(row_ok)}")
            # per-group detail line
            print(f"         groups timeline={[tl_groups[g] for g in GROUP_ORDER]}  "
                  f"/windowed={[bg.get(g, 0) for g in GROUP_ORDER]}")
        fail_count += 0 if b1_ok else 1
        print(f"  §B1 every buyer's timeline reconciles 1:1 with /windowed             {_ok(b1_ok)}")
        print()

        # ══════════════════════════════════════════════════════════════════════
        # §B2 — INTERNAL consistency (per-month reconciliation + window total)
        # ══════════════════════════════════════════════════════════════════════
        print(_SEP)
        print("  §B2 — INTERNAL consistency (per-month groups sum to lead_count; Σ periods == header total)")
        print(_SEP)
        b2_ok = True
        for bid, tl in timelines.items():
            per_month_ok = all(
                sum(o["count"] for o in p["outcomes"]) == p["lead_count"]
                for p in tl["periods"]
            )
            total_ok = sum(p["lead_count"] for p in tl["periods"]) == tl["header"]["total_leads_in_window"]
            this_ok = per_month_ok and total_ok
            b2_ok = b2_ok and this_ok
            print(f"     {tl['header']['buyer_name'][:22]:<23} months={len(tl['periods'])}  "
                  f"per-month recon={_ok(per_month_ok)}  Σ==header={_ok(total_ok)}  "
                  f"campaigns={tl['header']['attributing_campaign_count']}  {_ok(this_ok)}")
        fail_count += 0 if b2_ok else 1
        print(f"  §B2 internal consistency                                             {_ok(b2_ok)}")
        print()

        # ══════════════════════════════════════════════════════════════════════
        # §B3 — MIGRATION excluded (preset echoes detected; custom range drops legacy)
        # ══════════════════════════════════════════════════════════════════════
        print(_SEP)
        print("  §B3 — MIGRATION excluded (legacy_days_excluded echoes detected; custom range crossing Nov-2025)")
        print(_SEP)
        b3_ok = True
        if timelines:
            sample_bid = next(iter(timelines))
            sample = timelines[sample_bid]
            echo_ok = set(sample["legacy_days_excluded"]) == set(detected)
            b3_ok = b3_ok and echo_ok
            print(f"  preset timeline legacy_days_excluded == detected set : {_ok(echo_ok)} "
                  f"({len(sample['legacy_days_excluded'])} days)")
            cust = await get_buyer_timeline(
                client=client, buyer_id=sample_bid,
                start_month="2025-10", end_month="2026-01",
            )
            legacy_in_range = sorted(d for d in detected if "2025-10" <= d[:7] <= "2026-01")
            excl_ok = set(legacy_in_range).issubset(set(cust["legacy_days_excluded"]))
            legacy_removed = len(legacy_in_range) > 0
            range_ok = excl_ok and legacy_removed and cust["is_custom_range"]
            b3_ok = b3_ok and range_ok
            print(f"  custom 2025-10..2026-01 is_custom_range={cust['is_custom_range']}  "
                  f"in-range legacy={legacy_in_range}")
            print(f"  service legacy_days_excluded ⊇ in-range : {_ok(excl_ok)}   legacy present (>0) : {_ok(legacy_removed)}")
        else:
            b3_ok = False
            print("  **FLAG** no timeline available to check migration exclusion.")
        fail_count += 0 if b3_ok else 1
        print(f"  §B3 migration excluded                                               {_ok(b3_ok)}")
        print()

        # ══════════════════════════════════════════════════════════════════════
        # §B4 — NOT-FOUND path (bogus buyer_id → BuyerNotFoundError, the live 404)
        # ══════════════════════════════════════════════════════════════════════
        print(_SEP)
        print("  §B4 — NOT-FOUND path (a bogus buyer_id raises BuyerNotFoundError)")
        print(_SEP)
        try:
            await get_buyer_timeline(client=client, buyer_id=_BOGUS_BUYER_ID, window_months=3)
            nf_ok = False
            print(f"  buyer_id={_BOGUS_BUYER_ID} unexpectedly returned a timeline  **FAIL**")
        except BuyerNotFoundError:
            nf_ok = True
            print(f"  buyer_id={_BOGUS_BUYER_ID} -> BuyerNotFoundError  {_ok(True)}")
        real_resolved = len(timelines) == len(mbw["buyers"]) and len(timelines) > 0
        b4_ok = nf_ok and real_resolved
        fail_count += 0 if b4_ok else 1
        print(f"  every real windowed buyer resolved a timeline ({len(timelines)}/{len(mbw['buyers'])}) : {_ok(real_resolved)}")
        print(f"  §B4 not-found path                                                   {_ok(b4_ok)}")
        print()

    print(_SEP)
    if fail_count == 0:
        print("  BUYER-TIMELINE VERIFICATION COMPLETE — ALL CHECKS (§B1–§B4) PASSED.")
    else:
        print(f"  BUYER-TIMELINE VERIFICATION COMPLETE — {fail_count} CHECK(S) FAILED/FLAGGED. STOP and report.")
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
