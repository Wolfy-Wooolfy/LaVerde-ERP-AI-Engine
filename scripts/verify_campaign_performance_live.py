"""
scripts/verify_campaign_performance_live.py — Campaign Performance (Level 1)
identity-equal LIVE verification (READ-ONLY, $0 AI).

Proves the campaign-centric per-campaign funnel:
  §6a — each funnel (every listed campaign row + the long tail + the junk "None"
        bucket + the no-campaign bucket) has its 4 stage-groups summing to its
        lead_count (0 mismatches).
  §6b — 1:1 RECONCILIATION: for each of the 4 CONFIRMED campaigns, the campaign's
        funnel (this module) EQUALS the corresponding per-buyer funnel from the
        shipped marketing_attribution get_attribution_overview() — exact equality
        of lead_count == total_attributed and of all 4 group counts.
  §6c — global population identity: listed + long_tail + junk + no_campaign ==
        total_leads_population == the shipped module's population.
  §6d — INDEPENDENT Odoo cross-check: for the top listed campaigns, each group
        count equals a direct search_count over (campaign filter + an independent
        group->stage_id domain) — validates the AGGREGATION, not self-agreement.
  §6e — the junk campaign literally named "None" is a DATA-QUALITY flag, never a
        list row.
  STATUS — the count of campaigns in each attribution_status across ALL real
        campaigns (confirmed / dominant / mixed / no_buyer / excluded_channel).

Buyer-display rule is a DISPLAY choice for this view only; it does NOT change the
shipped module's strict attribution metric. The §6b check compares STAGE-GROUP
counts (not buyer labels), so it is unaffected and must pass exactly.

Method discipline: READ-ONLY (search_count / read_group / search_read only).
ALLOWED_METHODS untouched. No FastAPI. No OpenAI. AI cost = $0.00.

Pre-flight (run manually BEFORE this script): kill all python processes; purge all
__pycache__; (uvicorn, if used, WITHOUT --reload). Talks to Odoo directly.

Usage (from project root):
    python scripts/verify_campaign_performance_live.py
"""

import asyncio
import io
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.modules.campaign_performance import domain  # noqa: E402
from backend.modules.campaign_performance.domain import (  # noqa: E402
    GROUP_INTERESTED,
    GROUP_NEW,
    GROUP_NO_RESULT,
    GROUP_ORDER,
    GROUP_WON,
)
from backend.modules.campaign_performance.services import cache as _cp_cache  # noqa: E402
from backend.modules.campaign_performance.services.campaign_service import (  # noqa: E402
    get_campaign_performance_overview,
)
from backend.modules.marketing_attribution import domain as ma_domain  # noqa: E402
from backend.modules.marketing_attribution.services import cache as _ma_cache  # noqa: E402
from backend.modules.marketing_attribution.services.attribution_service import (  # noqa: E402
    get_attribution_overview,
)
from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient  # noqa: E402

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_LEAD = "crm.lead"
_STAGE = "crm.stage"
_SEP = "=" * 100
_SEP2 = "-" * 100
_CTX_ALL = {"active_test": False}
_TOP_N_CROSSCHECK = 5


def _ok(flag: bool) -> str:
    return "PASS" if flag else "**FAIL**"


def _group_counts(outcomes: list[dict]) -> dict[str, int]:
    return {o["group"]: o["count"] for o in outcomes}


async def _count(client, dom):
    return await client.execute_kw(
        _LEAD, "search_count", args=[dom], kwargs={"context": _CTX_ALL}
    )


async def main():
    print(_SEP)
    print("  CAMPAIGN PERFORMANCE (LEVEL 1) — IDENTITY-EQUAL LIVE VERIFICATION (READ-ONLY, $0)")
    print(f"  ALLOWED_METHODS : {sorted(ALLOWED_METHODS)}")
    print(f"  Population/ctx  : ALL leads incl. archived — context={_CTX_ALL}")
    print(f"  Confirmed cfg   : {sorted(domain.CONFIRMED_BUYER_CAMPAIGNS)}")
    print(f"  Denylist cfg    : {sorted(domain.DENYLIST_CAMPAIGNS)}")
    print(f"  Junk labels     : {sorted(domain.JUNK_CAMPAIGN_NAMES)}")
    print(f"  Threshold (def) : {domain.DEFAULT_MIN_LEAD_THRESHOLD}  "
          f"floor={domain.DOMINANT_FLOOR_PCT}%  min_both_set={domain.MIN_BOTH_SET_FOR_BUYER}")
    print(_SEP)
    print()

    fail_count = 0
    _cp_cache.clear()
    _ma_cache.clear()

    async with OdooClient() as client:
        # ── run both modules (inject the same client; production config) ───────
        cp = await get_campaign_performance_overview(client=client)
        cp_all = await get_campaign_performance_overview(client=client, min_lead_threshold=1)
        module = await get_attribution_overview(client=client)

        # ── build independent stage group buckets (for §6d cross-check) ────────
        stages = await client.execute_kw(
            _STAGE, "search_read", args=[[]],
            kwargs={"fields": ["id", "name", "is_won"]},
        )
        won_ids, new_ids, interested_ids, no_result_ids = [], [], [], []
        for s in stages:
            sid, sname, is_won = int(s["id"]), str(s.get("name") or ""), bool(s.get("is_won"))
            if is_won:
                won_ids.append(sid)
            elif sname in ma_domain.NEW_STAGE_NAMES:
                new_ids.append(sid)
            elif sname in ma_domain.INTERESTED_STAGE_NAMES:
                interested_ids.append(sid)
            else:
                no_result_ids.append(sid)

        # ── §6a — every funnel's 4 groups sum to its lead_count ────────────────
        print(_SEP)
        print("  §6a — EACH FUNNEL'S 4 STAGE-GROUPS SUM TO ITS lead_count")
        print(_SEP)
        a_mismatches = 0
        funnels = [(f"campaign {c['campaign_name']!r}", c["outcomes"], c["lead_count"])
                   for c in cp["campaigns"]]
        if cp["long_tail"]:
            funnels.append(("long_tail", cp["long_tail"]["outcomes"], cp["long_tail"]["lead_count"]))
        for key in ("junk_none", "no_campaign"):
            b = cp["data_quality"].get(key)
            if b:
                funnels.append((f"data_quality.{key}", b["outcomes"], b["lead_count"]))
        for label, outcomes, total in funnels:
            gsum = sum(o["count"] for o in outcomes)
            if gsum != total:
                a_mismatches += 1
                print(f"  **FAIL** {label}: Σgroups={gsum:,} != lead_count={total:,}")
        a_ok = a_mismatches == 0
        if not a_ok:
            fail_count += 1
        print(f"  funnels checked : {len(funnels):,}   mismatches : {a_mismatches}   {_ok(a_ok)}")
        print()

        # ── §6b — 1:1 reconciliation with the shipped module ───────────────────
        print(_SEP)
        print("  §6b — 1:1 RECONCILIATION: confirmed campaign funnel == shipped per-buyer funnel")
        print(_SEP)
        cp_by_id = {c["campaign_id"]: c for c in cp_all["campaigns"]}
        module_buyer = {b["buyer_id"]: b for b in module["buyers"]}
        for cc in module["confirmed_campaigns"]:
            cid = cc["campaign_id"]
            bid = cc["dominant_buyer_id"]
            cname = cc["campaign_name"]
            bname = cc["dominant_buyer_name"]
            print(f"  campaign {cname!r} (id={cid})  <->  buyer {bname!r} (id={bid})")
            row = cp_by_id.get(cid)
            buyer = module_buyer.get(bid)
            if row is None or buyer is None:
                print("     **FAIL** campaign row or buyer row missing")
                fail_count += 1
                print()
                continue
            # 1:1 sanity — this buyer must attribute exactly this one campaign
            one_to_one = buyer["campaign_ids"] == [cid]
            if not one_to_one:
                print(f"     **FAIL** buyer attributes campaigns {buyer['campaign_ids']} — not 1:1 with {cid}")
                fail_count += 1
            total_ok = row["lead_count"] == buyer["total_attributed"]
            if not total_ok:
                fail_count += 1
            print(f"     {'lead_count == total_attributed':<34} "
                  f"CP={row['lead_count']:>8,}  MA={buyer['total_attributed']:>8,}  {_ok(total_ok)}")
            cp_g = _group_counts(row["outcomes"])
            ma_g = _group_counts(buyer["outcomes"])
            for g in GROUP_ORDER:
                g_ok = cp_g.get(g, 0) == ma_g.get(g, 0)
                if not g_ok:
                    fail_count += 1
                print(f"     {g:<34} CP={cp_g.get(g,0):>8,}  MA={ma_g.get(g,0):>8,}  {_ok(g_ok)}")
            print()

        # ── §6c — global population identity ───────────────────────────────────
        print(_SEP)
        print("  §6c — GLOBAL POPULATION IDENTITY")
        print(_SEP)
        listed = sum(c["lead_count"] for c in cp["campaigns"])
        tail = cp["long_tail"]["lead_count"] if cp["long_tail"] else 0
        junk = cp["data_quality"]["junk_none"]["lead_count"] if cp["data_quality"]["junk_none"] else 0
        ncamp = cp["data_quality"]["no_campaign"]["lead_count"] if cp["data_quality"]["no_campaign"] else 0
        recon = listed + tail + junk + ncamp
        pop = cp["total_leads_population"]
        recon_ok = recon == pop
        pop_ok = pop == module["total_leads_population"]
        if not recon_ok:
            fail_count += 1
        if not pop_ok:
            fail_count += 1
        print(f"  listed={listed:,}  long_tail={tail:,}  junk_None={junk:,}  no_campaign={ncamp:,}")
        print(f"  Σ = {recon:,}  ==  population {pop:,}                  {_ok(recon_ok)}")
        print(f"  CP population {pop:,}  ==  MA population {module['total_leads_population']:,}   {_ok(pop_ok)}")
        print()

        # ── §6d — independent Odoo cross-check on the top listed campaigns ─────
        print(_SEP)
        print(f"  §6d — INDEPENDENT ODOO CROSS-CHECK (top {_TOP_N_CROSSCHECK} listed campaigns)")
        print(_SEP)
        group_domains = {
            GROUP_NEW: ["|", ("stage_id", "in", new_ids), ("stage_id", "=", False)],
            GROUP_INTERESTED: [("stage_id", "in", interested_ids)],
            GROUP_WON: [("stage_id", "in", won_ids)],
            GROUP_NO_RESULT: [("stage_id", "in", no_result_ids)],
        }
        for c in cp["campaigns"][:_TOP_N_CROSSCHECK]:
            cid = c["campaign_id"]
            print(f"  campaign {c['campaign_name']!r} (id={cid})  lead_count={c['lead_count']:,}")
            cp_g = _group_counts(c["outcomes"])
            for g in GROUP_ORDER:
                dom = [("campaign_id", "=", cid)] + group_domains[g]
                odoo_g = await _count(client, dom)
                g_ok = odoo_g == cp_g.get(g, 0)
                if not g_ok:
                    fail_count += 1
                print(f"     {g:<22} CP={cp_g.get(g,0):>8,}  ODOO={odoo_g:>8,}  {_ok(g_ok)}")
            print()

        # ── §6e — junk "None" is a data-quality flag, not a list row ──────────
        print(_SEP)
        print("  §6e — JUNK 'None' CAMPAIGN IS A DATA-QUALITY FLAG (not a list row)")
        print(_SEP)
        in_rows = [c for c in cp_all["campaigns"] if c["campaign_name"] in domain.JUNK_CAMPAIGN_NAMES]
        junk_bucket = cp["data_quality"]["junk_none"]
        not_a_row = not in_rows
        is_flagged = junk_bucket is not None
        if not not_a_row:
            fail_count += 1
        print(f"  'None' absent from campaign rows (even at threshold=1) : {_ok(not_a_row)}")
        if junk_bucket:
            print(f"  data_quality.junk_none : ids={junk_bucket['campaign_ids']} "
                  f"lead_count={junk_bucket['lead_count']:,}  flagged={_ok(is_flagged)}")
        else:
            print("  data_quality.junk_none : (none present in live data)")
        print()

        # ── STATUS distribution across ALL real campaigns ──────────────────────
        print(_SEP)
        print("  ATTRIBUTION-STATUS DISTRIBUTION (all real campaigns; junk + no-campaign excluded)")
        print(_SEP)
        status_counts = Counter(c["attribution_status"] for c in cp_all["campaigns"])
        for st in ("confirmed", "dominant", "mixed", "no_buyer", "excluded_channel"):
            print(f"  {st:<18} : {status_counts.get(st, 0):>5,}")
        print(f"  {'TOTAL real':<18} : {sum(status_counts.values()):>5,}  "
              f"(of {cp['total_campaigns_with_leads']:,} campaigns with leads)")
        print()
        print("  CONFIRMED / DOMINANT campaigns (buyer shown):")
        for c in cp_all["campaigns"]:
            if c["attribution_status"] in ("confirmed", "dominant"):
                print(f"     {c['campaign_name']!r:<28} status={c['attribution_status']:<9} "
                      f"buyer={c['media_buyer_name']!r:<20} conc={c['concentration']:.1f}%  "
                      f"both_set={c['both_set_count']:,}  leads={c['lead_count']:,}")
        print()

        # ── integrity alerts + config warnings ─────────────────────────────────
        print(_SEP)
        print("  INTEGRITY ALERTS (confirmed campaign no longer holds >=90% — locked-decision drift)")
        print(_SEP)
        if not cp["integrity_alerts"]:
            print("  (none)")
        for a in cp["integrity_alerts"]:
            print(f"  {a}")
            fail_count += 1
        print()
        print(_SEP)
        print("  CONFIG WARNINGS (unresolved or duplicate configured names)")
        print(_SEP)
        if not cp["config_warnings"]:
            print("  (none)")
        for w in cp["config_warnings"]:
            print(f"  {w}")
        print()

    print(_SEP)
    if fail_count == 0:
        print("  VERIFICATION COMPLETE — ALL CHECKS PASSED.")
    else:
        print(f"  VERIFICATION COMPLETE — {fail_count} CHECK(S) FAILED/FLAGGED. STOP and report.")
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
