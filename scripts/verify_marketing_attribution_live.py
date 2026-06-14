"""
scripts/verify_marketing_attribution_live.py — Marketing Attribution
identity-equal LIVE verification (READ-ONLY, $0 AI).

Proves the module's numbers match independent direct Odoo queries, and surfaces
the amendment evidence (A1/A2/A4/A5). NOTHING here is the module re-running
itself except where explicitly labelled "MODULE"; every "ODOO" number is an
independent search_count / read_group issued by this script.

What it checks:
  A5  — for ALL 4 confirmed campaigns: DERIVED dominant buyer + concentration
        (independent read_group over both-set leads) vs the DOCUMENTED buyer and
        the >=90% gate. Any mismatch -> printed as a STOP/FLAG.
  is_won — independent read of crm.stage(is_won=True); must equal exactly the 4
        اشترى stages.
  A4(i)  — full crm.stage table: id, name, is_won, and the group this script
        assigns (built from live is_won + exact name sets, NOT from the module's
        classify_stage) — for Khaled to confirm by eye against Odoo.
  A4(ii) — exact live stage-name check for "New","New X","Follow up","Interested";
        any spelling/case/spacing drift is flagged loudly (those leads would
        silently fall into بلا نتيجة).
  §7 totals — per confirmed buyer: MODULE total_attributed vs ODOO search_count
        over the buyer's gated campaign ids (side by side).
  A4(iii) — per confirmed buyer × 4 groups: MODULE group count vs ODOO
        search_count over (campaign filter + this script's independent
        group->stage_id map). Validates the AGGREGATION, not self-agreement.
  reconcile — per buyer: Σ(4 groups) == total.
  A2  — module attribution_pct printed next to the ~52.6% discovery ceiling, with
        the archived-included note and the pending-shortfall accounting.

Method discipline: READ-ONLY (search_count / read_group / search_read only).
ALLOWED_METHODS untouched. No FastAPI. No OpenAI. AI cost = $0.00.

Pre-flight (run manually BEFORE this script, per §7):
    kill all python processes; purge all __pycache__; (uvicorn, if used, WITHOUT
    --reload). This script talks to Odoo directly and does not require uvicorn.

Usage (from project root):
    python scripts/verify_marketing_attribution_live.py
"""

import asyncio
import io
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.modules.marketing_attribution import domain  # noqa: E402
from backend.modules.marketing_attribution.domain import (  # noqa: E402
    GROUP_INTERESTED,
    GROUP_NEW,
    GROUP_NO_RESULT,
    GROUP_ORDER,
    GROUP_WON,
)
from backend.modules.marketing_attribution.services import cache as _cache  # noqa: E402
from backend.modules.marketing_attribution.services.attribution_service import (  # noqa: E402
    get_attribution_overview,
)
from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient  # noqa: E402

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_LEAD = "crm.lead"
_CAMPAIGN = "utm.campaign"
_STAGE = "crm.stage"
_SEP = "=" * 100
_SEP2 = "-" * 100
_CTX_ALL = {"active_test": False}

_DISCOVERY_ATTRIBUTABLE_CEILING = 52.6  # %, from MARKETING_ATTRIBUTION_DISCOVERY_DATA.md §5b/§6


def _m2o(v):
    if isinstance(v, (list, tuple)) and len(v) == 2:
        return int(v[0]), str(v[1])
    return None, None


async def _count(client, domain_):
    return await client.execute_kw(
        _LEAD, "search_count", args=[domain_], kwargs={"context": _CTX_ALL}
    )


def _ok(flag: bool) -> str:
    return "PASS" if flag else "**FAIL**"


async def main():
    print(_SEP)
    print("  MARKETING ATTRIBUTION — IDENTITY-EQUAL LIVE VERIFICATION (READ-ONLY, $0)")
    print(f"  ALLOWED_METHODS : {sorted(ALLOWED_METHODS)}")
    print(f"  Population/ctx  : ALL leads incl. archived — context={_CTX_ALL}")
    print(f"  Confirmed cfg   : {sorted(domain.CONFIRMED_BUYER_CAMPAIGNS)}")
    print(f"  Denylist cfg    : {sorted(domain.DENYLIST_CAMPAIGNS)}")
    print(_SEP)
    print()

    fail_count = 0
    _cache.clear()  # ensure the module re-queries live, not a stale cache entry

    async with OdooClient() as client:
        # ── resolve campaign names -> ids (independent of the module) ──────────
        campaigns = await client.execute_kw(
            _CAMPAIGN, "search_read", args=[[]],
            kwargs={"fields": ["id", "name"], "context": _CTX_ALL},
        )
        name_to_ids: dict[str, list[int]] = {}
        id_to_name: dict[int, str] = {}
        for c in campaigns:
            name_to_ids.setdefault(str(c.get("name") or ""), []).append(int(c["id"]))
            id_to_name[int(c["id"])] = str(c.get("name") or "")

        # ── A5 — derived dominant buyer + concentration per confirmed campaign ─
        print(_SEP)
        print("  A5 — DOMINANT-BUYER SANITY (all 4 confirmed campaigns; independent read_group)")
        print(_SEP)
        print(f"  {'campaign':<16} | {'id(s)':<12} | {'derived dominant':<18} | {'conc':>7} | "
              f"{'documented':<18} | result")
        print(f"  {'-'*16}-+-{'-'*12}-+-{'-'*18}-+-{'-'*7}-+-{'-'*18}-+-{'-'*8}")
        for cname in sorted(domain.CONFIRMED_BUYER_CAMPAIGNS):
            cids = name_to_ids.get(cname, [])
            documented = domain.DOCUMENTED_DOMINANT_BUYER.get(cname, "?")
            if not cids:
                print(f"  {cname:<16} | {'(none)':<12} | {'—':<18} | {'—':>7} | "
                      f"{documented:<18} | **FAIL: name did not resolve**")
                fail_count += 1
                continue
            rg = await client.execute_kw(
                _LEAD, "read_group",
                args=[[("campaign_id", "in", cids), ("media_buyer_id", "!=", False)],
                      ["media_buyer_id"], ["media_buyer_id"]],
                kwargs={"context": _CTX_ALL, "lazy": False},
            )
            buyers = Counter()
            for r in rg:
                bid, bname = _m2o(r.get("media_buyer_id"))
                if bid is not None:
                    buyers[bname] += int(r.get("__count") or 0)
            both_total = sum(buyers.values())
            if both_total == 0:
                print(f"  {cname:<16} | {str(cids):<12} | {'(no both-set)':<18} | {'—':>7} | "
                      f"{documented:<18} | **FAIL: no both-set leads**")
                fail_count += 1
                continue
            dom_name, dom_cnt = buyers.most_common(1)[0]
            conc = 100.0 * dom_cnt / both_total
            name_ok = dom_name.strip() == documented.strip()
            conc_ok = dom_cnt * 100 >= both_total * 90
            ok = name_ok and conc_ok
            if not ok:
                fail_count += 1
            note = "" if ok else (
                f"  <- {'NAME MISMATCH ' if not name_ok else ''}"
                f"{'CONC<90% ' if not conc_ok else ''}STOP/FLAG"
            )
            print(f"  {cname:<16} | {str(cids):<12} | {dom_name[:18]:<18} | {conc:>6.1f}% | "
                  f"{documented:<18} | {_ok(ok)}{note}")
        print()

        # ── crm.stage table + is_won check + exact-name check (A4) ─────────────
        stages = await client.execute_kw(
            _STAGE, "search_read", args=[[]],
            kwargs={"fields": ["id", "name", "is_won"], "order": "sequence asc, id asc"},
        )
        won_ids, new_ids, interested_ids, no_result_ids = [], [], [], []
        won_names = []
        for s in stages:
            sid, sname, is_won = int(s["id"]), str(s.get("name") or ""), bool(s.get("is_won"))
            if is_won:
                won_ids.append(sid)
                won_names.append(sname)
            elif sname in domain.NEW_STAGE_NAMES:
                new_ids.append(sid)
            elif sname in domain.INTERESTED_STAGE_NAMES:
                interested_ids.append(sid)
            else:
                no_result_ids.append(sid)

        def _group_of(sid, sname, is_won):
            if is_won:
                return GROUP_WON
            if sname in domain.NEW_STAGE_NAMES:
                return GROUP_NEW
            if sname in domain.INTERESTED_STAGE_NAMES:
                return GROUP_INTERESTED
            return GROUP_NO_RESULT

        print(_SEP)
        print("  A4(i) — FULL crm.stage TABLE (independent group map; confirm by eye vs Odoo)")
        print(_SEP)
        print(f"  {'id':>4} | {'is_won':<6} | {'group':<10} | name")
        print(f"  {'-'*4}-+-{'-'*6}-+-{'-'*10}-+-{'-'*40}")
        for s in stages:
            sid, sname, is_won = int(s["id"]), str(s.get("name") or ""), bool(s.get("is_won"))
            print(f"  {sid:>4} | {str(is_won):<6} | {_group_of(sid, sname, is_won):<10} | {sname}")
        print(f"  (no stage) / stage_id=False -> {GROUP_NEW}")
        print()

        # is_won check — expect exactly the 4 documented اشترى stages
        expected_won = {
            "Draft Reservation", "Initial Reservation", "Reservation",
            "Down Payment Confirm & Contracted",
        }
        won_ok = set(won_names) == expected_won
        if not won_ok:
            fail_count += 1
        print(_SEP)
        print("  is_won CHECK — crm.stage.is_won=True must equal exactly the 4 اشترى stages")
        print(_SEP)
        print(f"  live is_won=True : {sorted(won_names)}")
        print(f"  expected         : {sorted(expected_won)}")
        print(f"  result           : {_ok(won_ok)}")
        print()

        # A4(ii) — exact name presence check
        print(_SEP)
        print("  A4(ii) — EXACT live stage-name check for جديد/مهتم source names")
        print(_SEP)
        live_names = {str(s.get('name') or '') for s in stages}
        for needed in sorted(domain.NEW_STAGE_NAMES | domain.INTERESTED_STAGE_NAMES):
            present = needed in live_names
            if not present:
                fail_count += 1
            print(f"  {needed!r:<16} present in live crm.stage : {_ok(present)}")
        print()

        # ── run the MODULE (inject this client; default production config) ─────
        result = await get_attribution_overview(client=client)

        # map module confirmed campaigns -> derived dominant + per-buyer grouping
        conf = result["confirmed_campaigns"]
        buyer_campaigns: dict[int, list[int]] = {}
        buyer_name_by_id: dict[int, str] = {}
        for c in conf:
            buyer_campaigns.setdefault(c["dominant_buyer_id"], []).append(c["campaign_id"])
            buyer_name_by_id[c["dominant_buyer_id"]] = c["dominant_buyer_name"]
        module_buyer = {b["buyer_id"]: b for b in result["buyers"]}

        # ── §7 totals + A4(iii) per-group identity, side by side ──────────────
        print(_SEP)
        print("  §7 + A4(iii) — IDENTITY-EQUAL: MODULE vs ODOO (per confirmed buyer)")
        print(_SEP)
        for bid, cids in buyer_campaigns.items():
            mrow = module_buyer.get(bid)
            bname = buyer_name_by_id.get(bid, str(bid))
            print(f"  BUYER {bname!r} (id={bid})  campaigns={cids} "
                  f"({[id_to_name.get(c) for c in cids]})")
            if mrow is None:
                print("     **FAIL: buyer present in confirmed_campaigns but missing from buyers list**")
                fail_count += 1
                print()
                continue

            odoo_total = await _count(client, [("campaign_id", "in", cids)])
            mod_total = mrow["total_attributed"]
            t_ok = odoo_total == mod_total
            if not t_ok:
                fail_count += 1
            print(f"     {'TOTAL attributed':<22} MODULE={mod_total:>8,}  ODOO={odoo_total:>8,}  {_ok(t_ok)}")

            mod_groups = {o["group"]: o["count"] for o in mrow["outcomes"]}
            group_domains = {
                GROUP_NEW: ["|", ("stage_id", "in", new_ids), ("stage_id", "=", False)],
                GROUP_INTERESTED: [("stage_id", "in", interested_ids)],
                GROUP_WON: [("stage_id", "in", won_ids)],
                GROUP_NO_RESULT: [("stage_id", "in", no_result_ids)],
            }
            group_sum = 0
            for g in GROUP_ORDER:
                dom = [("campaign_id", "in", cids)] + group_domains[g]
                odoo_g = await _count(client, dom)
                mod_g = mod_groups.get(g, 0)
                g_ok = odoo_g == mod_g
                if not g_ok:
                    fail_count += 1
                group_sum += mod_g
                print(f"     {g:<22} MODULE={mod_g:>8,}  ODOO={odoo_g:>8,}  {_ok(g_ok)}")

            rec_ok = group_sum == mod_total
            if not rec_ok:
                fail_count += 1
            print(f"     {'RECONCILE Σgroups':<22} {group_sum:,} == total {mod_total:,}  {_ok(rec_ok)}")
            print()

        # ── A2 — attribution_pct vs discovery ceiling ─────────────────────────
        print(_SEP)
        print("  A2 — ATTRIBUTION COVERAGE RECONCILIATION (archived-INCLUDED population)")
        print(_SEP)
        pct = result["attribution_pct"]
        n_pending = len(result["pending_campaigns"])
        print(f"  population (incl. archived)  = {result['total_leads_population']:,}")
        print(f"  total_attributed (confirmed) = {result['total_attributed']:,}")
        print(f"  module attribution_pct       = {pct:.2f}%")
        print(f"  discovery attributable ceil  = ~{_DISCOVERY_ATTRIBUTABLE_CEILING}% "
              f"(~34.7% recorded + ~18% inferred; both measured on the SAME "
              f"archived-included 146,814 population — DISCOVERY_DATA §4a/§5b)")
        print(f"  NOTE: the module attributes CONFIRMED campaigns ONLY, so "
              f"attribution_pct <= ~{_DISCOVERY_ATTRIBUTABLE_CEILING}% is EXPECTED; the")
        print(f"        shortfall is accounted for by the {n_pending} pending "
              f"campaign(s) below + non-confirmed qualifying channels.")
        if n_pending == 0 and pct < _DISCOVERY_ATTRIBUTABLE_CEILING - 5:
            print(f"  **FLAG (A2): pending is EMPTY but attribution_pct ({pct:.2f}%) is "
                  f"materially below ~{_DISCOVERY_ATTRIBUTABLE_CEILING}% — STOP and report the gap.**")
            fail_count += 1
        print()

        # ── pending + alerts + warnings ───────────────────────────────────────
        print(_SEP)
        print("  PENDING CAMPAIGNS (qualify >=90%, not denied, NOT YET confirmed — §3.5)")
        print(_SEP)
        if not result["pending_campaigns"]:
            print("  (none)")
        for p in result["pending_campaigns"]:
            print(f"  {p['campaign_name']!r} (id={p['campaign_id']}) -> "
                  f"{p['dominant_buyer_name']!r}  conc={p['concentration']:.1f}%  "
                  f"both_set={p['both_set_count']:,}  leads={p['lead_count']:,}")
        print()

        print(_SEP)
        print("  INTEGRITY ALERTS (A1 — confirmed campaign failed the gate; locked-decision drift)")
        print(_SEP)
        if not result["integrity_alerts"]:
            print("  (none — all confirmed campaigns qualify and are not denied)")
        for a in result["integrity_alerts"]:
            print(f"  {a}")
            fail_count += 1
        print()

        print(_SEP)
        print("  CONFIG WARNINGS (A3 — unresolved or duplicate configured names)")
        print(_SEP)
        if not result["config_warnings"]:
            print("  (none)")
        for w in result["config_warnings"]:
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
