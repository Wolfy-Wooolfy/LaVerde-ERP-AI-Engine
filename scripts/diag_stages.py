"""
Diagnostic script: All CRM stages — total lead count vs overdue count.
READ-ONLY. Zero OpenAI calls. Uses search_read and read_group only.

Run from project root:
    python scripts/diag_stages.py
"""

import os
import uuid

import httpx
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

ODOO_URL = os.environ["ODOO_URL"].rstrip("/") + "/jsonrpc"
ODOO_DB = os.environ["ODOO_DB"]
ODOO_USERNAME = os.environ["ODOO_USERNAME"]
ODOO_API_KEY = os.environ["ODOO_API_KEY"]

BASE_DOMAIN = [
    ["type", "=", "opportunity"],
    ["opportunity_status", "=", "resolved"],
]


def rpc(client, service, method, args):
    r = client.post(
        ODOO_URL,
        json={"jsonrpc": "2.0", "method": "call", "id": str(uuid.uuid4()),
              "params": {"service": service, "method": method, "args": args}},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"Odoo error: {data['error']}")
    return data["result"]


def execute(client, uid, model, method, args, kwargs=None):
    return rpc(client, "object", "execute_kw",
               [ODOO_DB, uid, ODOO_API_KEY, model, method, args, kwargs or {}])


def count(client, uid, domain):
    rows = execute(client, uid, "crm.lead", "read_group",
                   [domain, ["__count"], []], {})
    return rows[0].get("__count", 0) if rows else 0


def main():
    SEP = "=" * 72

    print(SEP)
    print("CRM Stage Diagnostic — READ-ONLY — ZERO OpenAI calls")
    print(SEP)

    with httpx.Client() as client:
        print("\n[1] Authenticating...")
        uid = rpc(client, "common", "authenticate",
                  [ODOO_DB, ODOO_USERNAME, ODOO_API_KEY, {}])
        if not uid:
            raise RuntimeError("Authentication failed — check .env credentials")
        print(f"    OK uid={uid}")

        # ── All crm.stage records ──────────────────────────────────────────
        print("\n[2] Fetching all crm.stage records (search_read)...")
        stages = execute(
            client, uid, "crm.stage", "search_read",
            [[]],
            {"fields": ["id", "name", "sequence", "team_id", "fold"],
             "limit": 200, "order": "sequence asc"},
        )
        print(f"    Found {len(stages)} stage records\n")

        print(f"  {'ID':>5}  {'SEQ':>4}  {'FOLD':>5}  {'TEAM':<30}  NAME")
        print("  " + "-" * 68)
        for s in stages:
            team = s.get("team_id")
            team_name = team[1] if team else "(global/shared)"
            fold = "YES" if s.get("fold") else "no"
            print(f"  {s['id']:>5}  {s.get('sequence', 0):>4}  "
                  f"{fold:>5}  {team_name:<30}  {s['name']}")

        # ── Per-stage: total vs overdue ────────────────────────────────────
        print("\n[3] Lead counts per stage (read_group, read-only)...")
        print(f"\n  {'ID':>5}  {'TOTAL':>7}  {'OVERDUE':>8}  {'DIFF':>6}  NAME")
        print("  " + "-" * 68)

        grand_total = grand_overdue = 0
        new_total = new_overdue = 0
        new_stages_found = []

        for s in stages:
            sid, sname = s["id"], s["name"]
            total_cnt = count(client, uid, BASE_DOMAIN + [["stage_id", "=", sid]])
            overdue_cnt = count(client, uid,
                                BASE_DOMAIN + [["activity_state", "=", "overdue"],
                                               ["stage_id", "=", sid]])

            if total_cnt == 0 and overdue_cnt == 0:
                continue  # skip empty stages

            diff = total_cnt - overdue_cnt
            flag = "  <- NEW" if "new" in sname.lower() else ""
            print(f"  {sid:>5}  {total_cnt:>7}  {overdue_cnt:>8}  {diff:>6}  {sname}{flag}")
            grand_total += total_cnt
            grand_overdue += overdue_cnt

            if "new" in sname.lower():
                new_total += total_cnt
                new_overdue += overdue_cnt
                team = s.get("team_id")
                new_stages_found.append({
                    "id": sid, "name": sname,
                    "team": team[1] if team else "global",
                    "total": total_cnt, "overdue": overdue_cnt,
                })

        print("  " + "-" * 68)
        print(f"  {'ALL':>5}  {grand_total:>7}  {grand_overdue:>8}  "
              f"  (all non-empty stages)")

        # ── New stage summary ──────────────────────────────────────────────
        print(f"\n[4] Stages matching 'new' in name:")
        if not new_stages_found:
            print("    NONE FOUND -- stage name does not contain 'new'")
        for ns in new_stages_found:
            print(f"    stage_id={ns['id']}  name='{ns['name']}'  "
                  f"team='{ns['team']}'  total={ns['total']}  overdue={ns['overdue']}")

        print(f"\n    AI reports (overdue only): {new_overdue}")
        print(f"    Odoo shows you (all leads): {new_total}")
        print(f"    Gap (hidden leads):         {new_total - new_overdue}")

    print(f"\n{SEP}")
    print("Done. No writes to Odoo. No OpenAI calls made.")
    print(SEP)


if __name__ == "__main__":
    main()
