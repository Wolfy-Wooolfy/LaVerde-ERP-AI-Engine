"""
discover_project_accounting_link.py — Project-Accounting Link: Directed Discovery
READ-ONLY: fields_get / search_read / search_count / read_group ONLY.
No create/write/unlink. No OpenAI. AI cost = $0.00.

Context: The previous discovery (discover_accounting_project_link.py, 2026-05-28)
concluded SEPARATE-ENTITIES because it compared rs.structure.project IDs against
account.analytic.account and found they are different models. That script never
asked rs.structure.project about itself.

Khaled's manual inspection of Cassette in production (Accounting tab) revealed:
  - An "Analytic Plan" field set to "Project#Cassette"
  - A full GL account map per project (Revenue, Maintenance, Penalty, Discount, etc.)

This script asks rs.structure.project directly. Goal: determine the exact technical
path from project to analytic/GL data, and compare two linking mechanisms for
Module 4 per-project P&L.

Questions:
  Q1 — rs.structure.project fields_get: find analytic + GL account fields
  Q2 — Data sample: read all 3 projects with those fields (populated? complete?)
  Q3 — Path A: analytic plan/account on project → how to reach actual analytic accounts
  Q4 — Path B: dedicated GL accounts per project → are they distinct? transactions?
  Q5 — Comparison + verdict: which path is more robust for Module 4?
  Q6 — Verdict on SEPARATE-ENTITIES: wrong, incomplete, or correct?

Production only: laverde.odoo.com (ODOO_URL from .env)
"""

import sys
import os
import uuid
from datetime import date
from io import StringIO
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

# ── READ-ONLY ENFORCEMENT ─────────────────────────────────────────────────────
ALLOWED_METHODS = frozenset({
    "search", "search_read", "search_count",
    "read", "read_group", "fields_get",
})

ODOO_URL  = os.environ["ODOO_URL"].rstrip("/") + "/jsonrpc"
ODOO_DB   = os.environ["ODOO_DB"]
ODOO_USER = os.environ["ODOO_USERNAME"]
ODOO_KEY  = os.environ["ODOO_API_KEY"]

TODAY = date.today().isoformat()

_PROJECT_MODEL  = "rs.structure.project"
_ANALYTIC_ACCT  = "account.analytic.account"
_ANALYTIC_PLAN  = "account.analytic.plan"
_MOVE_LINE      = "account.move.line"
_ACCOUNT        = "account.account"

# Keywords to detect analytic fields on the project model
_ANALYTIC_KEYWORDS = {"analytic", "plan"}
# Keywords to detect GL account fields (account pointers) on the project model
_GL_ACCOUNT_KEYWORDS = {
    "account", "revenue", "income", "expense", "maintenance",
    "penalty", "discount", "installment", "deferred", "reservation",
    "delivered", "modification", "service", "facility", "club",
    "garage", "credit",
}

SEP  = "=" * 72
SEP2 = "-" * 72


# ── RPC CORE ──────────────────────────────────────────────────────────────────

def rpc(client, service, method, args):
    r = client.post(
        ODOO_URL,
        json={
            "jsonrpc": "2.0",
            "method": "call",
            "id": str(uuid.uuid4()),
            "params": {"service": service, "method": method, "args": args},
        },
        timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"Odoo RPC error: {data['error']}")
    return data["result"]


def execute(client, uid, model, method, args, kwargs=None):
    if method not in ALLOWED_METHODS:
        raise RuntimeError(
            f"Method '{method}' is NOT in ALLOWED_METHODS "
            f"({sorted(ALLOWED_METHODS)}). Read-only enforcement."
        )
    return rpc(client, "object", "execute_kw",
               [ODOO_DB, uid, ODOO_KEY, model, method, args, kwargs or {}])


def do_fields_get(client, uid, model):
    return execute(client, uid, model, "fields_get", [],
                   {"attributes": ["string", "type", "relation", "selection"]})


def do_count(client, uid, model, domain):
    return execute(client, uid, model, "search_count", [domain])


def do_read(client, uid, model, domain, fields, limit=None, order=None):
    kw = {"fields": fields}
    if limit is not None:
        kw["limit"] = limit
    if order:
        kw["order"] = order
    return execute(client, uid, model, "search_read", [domain], kw)


def do_group(client, uid, model, domain, agg_fields, groupby, lazy=False):
    return execute(client, uid, model, "read_group",
                   [domain, agg_fields, groupby], {"lazy": lazy})


# ── AUTH ──────────────────────────────────────────────────────────────────────

def connect(client):
    print("\n[AUTH] Authenticating to production (laverde.odoo.com)...")
    uid = rpc(client, "common", "authenticate",
              [ODOO_DB, ODOO_USER, ODOO_KEY, {}])
    if not uid:
        raise RuntimeError("Auth failed — check .env credentials")
    print(f"  OK uid={uid}")
    return uid


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _label(val):
    if isinstance(val, (list, tuple)) and len(val) == 2:
        return f"[{val[0]}] {val[1]}"
    return str(val) if val else "(none/False)"


def _id(val):
    if isinstance(val, (list, tuple)) and val:
        return val[0]
    if isinstance(val, int):
        return val
    return None


def _name(val):
    if isinstance(val, (list, tuple)) and len(val) == 2:
        return str(val[1])
    return str(val) if val else ""


def _c(row):
    return int(row.get("__count") or 0)


def _f(val):
    return float(val) if val else 0.0


def _field_matches(fname, fmeta, keywords):
    low = fname.lower()
    string_low = (fmeta.get("string") or "").lower()
    return any(kw in low or kw in string_low for kw in keywords)


# ── SECTION 1: fields_get on rs.structure.project ────────────────────────────

def section1_project_fields(client, uid):
    print(f"\n{SEP}")
    print("  SECTION 1 — rs.structure.project: fields_get")
    print("  Goal: find analytic fields + GL account fields in the Accounting tab")
    print(SEP)

    flds = do_fields_get(client, uid, _PROJECT_MODEL)
    total = len(flds)
    print(f"\n  Total fields on {_PROJECT_MODEL}: {total}")

    # Analytic fields
    analytic_fields = {}
    for fname, fmeta in flds.items():
        low = fname.lower()
        string_low = (fmeta.get("string") or "").lower()
        if any(kw in low or kw in string_low for kw in _ANALYTIC_KEYWORDS):
            analytic_fields[fname] = fmeta

    # GL account pointer fields (many2one to account.account)
    gl_fields = {}
    for fname, fmeta in flds.items():
        if fmeta.get("type") == "many2one" and fmeta.get("relation") == _ACCOUNT:
            gl_fields[fname] = fmeta

    # Also capture many2one fields pointing to any analytic model
    analytic_m2o = {}
    for fname, fmeta in flds.items():
        if fmeta.get("type") == "many2one" and "analytic" in (fmeta.get("relation") or ""):
            analytic_m2o[fname] = fmeta

    # Print analytic fields
    print(f"\n  A) Analytic-related fields (name/string contains 'analytic' or 'plan'):")
    if analytic_fields or analytic_m2o:
        combined = {**analytic_fields, **analytic_m2o}
        print(f"  {'FIELD_NAME':<45}  {'TYPE':<12}  {'RELATION':<35}  STRING")
        print(f"  {'-'*45}  {'-'*12}  {'-'*35}  {'-'*25}")
        for fname, meta in sorted(combined.items()):
            ftype    = meta.get("type", "")
            relation = meta.get("relation", "") or ""
            fstring  = meta.get("string", "")
            print(f"  {fname:<45}  {ftype:<12}  {relation:<35}  {fstring}")
    else:
        print("  !! No analytic or plan fields found by name/string search.")
        print("  Showing ALL many2one fields for manual inspection:")
        for fname, meta in sorted(flds.items()):
            if meta.get("type") == "many2one":
                print(f"    {fname:<50}  → {meta.get('relation','')}  ({meta.get('string','')})")

    # Print GL account fields
    print(f"\n  B) GL account fields (many2one → account.account): {len(gl_fields)} found")
    if gl_fields:
        print(f"  {'FIELD_NAME':<45}  STRING")
        print(f"  {'-'*45}  {'-'*40}")
        for fname, meta in sorted(gl_fields.items()):
            print(f"  {fname:<45}  {meta.get('string','')}")
    else:
        print("  !! No many2one fields pointing to account.account found.")
        print("  Searching by keyword (account/revenue/expense) in field name/string:")
        keyword_matches = {}
        for fname, fmeta in flds.items():
            if _field_matches(fname, fmeta, _GL_ACCOUNT_KEYWORDS):
                keyword_matches[fname] = fmeta
        if keyword_matches:
            print(f"  {'FIELD_NAME':<45}  {'TYPE':<12}  {'RELATION':<35}  STRING")
            print(f"  {'-'*45}  {'-'*12}  {'-'*35}  {'-'*25}")
            for fname, meta in sorted(keyword_matches.items()):
                ftype    = meta.get("type", "")
                relation = meta.get("relation", "") or ""
                fstring  = meta.get("string", "")
                print(f"  {fname:<45}  {ftype:<12}  {relation:<35}  {fstring}")
        else:
            print("  No account-related fields found by keyword either.")

    return {
        "all_fields": flds,
        "analytic_fields": {**analytic_fields, **analytic_m2o},
        "gl_fields": gl_fields,
    }


# ── SECTION 2: Data sample — all 3 projects ──────────────────────────────────

def section2_project_data(client, uid, analytic_fields, gl_fields):
    print(f"\n{SEP}")
    print("  SECTION 2 — Data sample: read all 3 projects with analytic + GL fields")
    print("  Goal: are these fields populated? complete for all 3 projects?")
    print(SEP)

    # Determine which fields to fetch
    base_fields = ["id", "name"]
    analytic_fetch = list(analytic_fields.keys())
    gl_fetch = list(gl_fields.keys())
    fetch_fields = base_fields + analytic_fetch + gl_fetch

    all_projects = do_read(client, uid, _PROJECT_MODEL, [], fetch_fields, order="id asc")

    print(f"\n  Total projects found: {len(all_projects)}")
    print()

    project_data = []
    for proj in all_projects:
        pid   = proj.get("id")
        pname = proj.get("name") or ""
        print(f"  {'─'*65}")
        print(f"  PROJECT id={pid}  name='{pname}'")

        # Analytic fields
        print(f"\n    Analytic fields:")
        if analytic_fetch:
            for fname in sorted(analytic_fetch):
                val = proj.get(fname)
                meta = analytic_fields.get(fname, {})
                ftype = meta.get("type", "?")
                relation = meta.get("relation", "") or ""
                string = meta.get("string", fname)
                print(f"      {string:<40}  [{fname}]  {ftype} → {relation}")
                print(f"        value: {_label(val)}")
        else:
            print("      (no analytic fields identified)")

        # GL account fields
        print(f"\n    GL account fields (many2one → account.account):")
        if gl_fetch:
            filled = 0
            empty  = 0
            for fname in sorted(gl_fetch):
                val = proj.get(fname)
                string = gl_fields.get(fname, {}).get("string", fname)
                filled_marker = "✓" if val else "✗"
                if val:
                    filled += 1
                else:
                    empty += 1
                print(f"      {filled_marker}  {string:<45}  {_label(val)}")
            print(f"\n      Summary: {filled} filled / {empty} empty")
        else:
            print("      (no GL account fields identified)")

        project_data.append({
            "id": pid,
            "name": pname,
            "raw": proj,
            "analytic_values": {f: proj.get(f) for f in analytic_fetch},
            "gl_values": {f: proj.get(f) for f in gl_fetch},
        })

    print(f"\n  {'─'*65}")
    return project_data


# ── SECTION 3: Path A — Analytic plan/account investigation ──────────────────

def section3_path_a(client, uid, project_data, analytic_fields):
    print(f"\n{SEP}")
    print("  SECTION 3 — PATH A: Analytic Plan/Account on Project")
    print("  Goal: determine the exact path from project → analytic accounts")
    print("        that link to actual expense/revenue journal lines")
    print(SEP)

    if not analytic_fields:
        print("\n  !! No analytic fields found on rs.structure.project (§1A was empty).")
        print("  Path A is NOT available — cannot link via analytic plan/account.")
        return {"path_a_available": False, "reason": "no analytic fields on project model"}

    # Find the primary analytic link field
    # Priority: many2one to account.analytic.plan > account.analytic.account > other
    plan_fields = {k: v for k, v in analytic_fields.items()
                   if v.get("relation") == _ANALYTIC_PLAN}
    aa_fields   = {k: v for k, v in analytic_fields.items()
                   if v.get("relation") == _ANALYTIC_ACCT}
    other_fields = {k: v for k, v in analytic_fields.items()
                    if k not in plan_fields and k not in aa_fields}

    print(f"\n  Analytic field breakdown:")
    print(f"    → account.analytic.plan:    {len(plan_fields)} field(s): {list(plan_fields.keys())}")
    print(f"    → account.analytic.account: {len(aa_fields)} field(s): {list(aa_fields.keys())}")
    print(f"    → other:                    {len(other_fields)} field(s): {list(other_fields.keys())}")

    results = {"path_a_available": False}

    # ── Case A1: field → account.analytic.plan ────────────────────────────────
    if plan_fields:
        primary_field = next(iter(plan_fields))
        primary_meta  = plan_fields[primary_field]
        print(f"\n  Primary link: '{primary_field}' ({primary_meta.get('string','')}) → {_ANALYTIC_PLAN}")
        print(f"\n  Investigating account.analytic.plan model...")

        plan_flds = do_fields_get(client, uid, _ANALYTIC_PLAN)
        print(f"  Fields on account.analytic.plan ({len(plan_flds)} total):")
        # Show fields that could link plan → accounts
        link_fields_on_plan = {}
        for fname, fmeta in plan_flds.items():
            rel = fmeta.get("relation") or ""
            if "analytic" in rel or "analytic" in fname.lower():
                link_fields_on_plan[fname] = fmeta
        if link_fields_on_plan:
            print(f"  {'FIELD_NAME':<40}  {'TYPE':<12}  {'RELATION':<35}  STRING")
            print(f"  {'-'*40}  {'-'*12}  {'-'*35}  {'-'*20}")
            for fname, meta in sorted(link_fields_on_plan.items()):
                print(f"  {fname:<40}  {meta.get('type',''):<12}  "
                      f"{meta.get('relation',''):<35}  {meta.get('string','')}")
        else:
            print("  No analytic-linking fields found on account.analytic.plan.")
            # Show all fields
            print("  All fields on account.analytic.plan:")
            for fname, meta in sorted(plan_flds.items()):
                print(f"    {fname:<40}  {meta.get('type',''):<12}  "
                      f"{meta.get('relation',''):<35}  {meta.get('string','')}")

        # Read the actual plan records linked from the projects
        plan_ids = []
        for proj in project_data:
            val = proj["analytic_values"].get(primary_field)
            pid = _id(val)
            if pid and pid not in plan_ids:
                plan_ids.append(pid)

        print(f"\n  Plan IDs referenced by projects: {plan_ids}")
        if plan_ids:
            fetch_plan_fields = ["id", "name"] + list(link_fields_on_plan.keys())
            plan_records = do_read(client, uid, _ANALYTIC_PLAN,
                                   [("id", "in", plan_ids)],
                                   fetch_plan_fields)
            print(f"  Plan records ({len(plan_records)} found):")
            for pr in plan_records:
                print(f"    id={pr.get('id')}  name='{pr.get('name')}'")
                for lf in link_fields_on_plan:
                    print(f"      {lf}: {_label(pr.get(lf))}")

            # Count analytic accounts belonging to these plans
            for pid in plan_ids:
                aa_count = do_count(client, uid, _ANALYTIC_ACCT,
                                    [("plan_id", "=", pid)])
                print(f"\n  account.analytic.account WHERE plan_id={pid}: {aa_count} accounts")

            # Check if analytic lines on these plans have amounts
            print(f"\n  Checking analytic line amounts for these plans (via plan_id):")
            try:
                for pid in plan_ids:
                    rows = do_group(client, uid, "account.analytic.line",
                                    [("account_id.plan_id", "=", pid)],
                                    ["amount", "__count"], [])
                    if rows:
                        row = rows[0]
                        amt = _f(row.get("amount"))
                        cnt = _c(row)
                        print(f"    plan_id={pid}: {cnt} lines, total amount={amt:,.2f} EGP")
                    else:
                        print(f"    plan_id={pid}: no analytic lines")
            except Exception as exc:
                print(f"    Analytic line query failed: {exc}")

        results.update({
            "path_a_available": bool(plan_ids),
            "mechanism": "project → plan_field → account.analytic.plan → account.analytic.account",
            "primary_field": primary_field,
            "plan_ids": plan_ids,
            "plan_flds": plan_flds,
        })

    # ── Case A2: field → account.analytic.account (direct) ───────────────────
    elif aa_fields:
        primary_field = next(iter(aa_fields))
        primary_meta  = aa_fields[primary_field]
        print(f"\n  Primary link: '{primary_field}' ({primary_meta.get('string','')}) → {_ANALYTIC_ACCT} (direct)")

        aa_ids = []
        for proj in project_data:
            val = proj["analytic_values"].get(primary_field)
            aid = _id(val)
            if aid and aid not in aa_ids:
                aa_ids.append(aid)

        print(f"  Analytic account IDs referenced by projects: {aa_ids}")
        if aa_ids:
            aa_records = do_read(client, uid, _ANALYTIC_ACCT,
                                 [("id", "in", aa_ids)],
                                 ["id", "name", "plan_id"])
            for ar in aa_records:
                print(f"    id={ar.get('id')}  name='{ar.get('name')}'  "
                      f"plan_id={_label(ar.get('plan_id'))}")

            # Check analytic line amounts
            print(f"\n  Analytic line amounts for these accounts:")
            try:
                rows = do_group(client, uid, "account.analytic.line",
                                [("account_id", "in", aa_ids)],
                                ["amount", "__count"], ["account_id"])
                for row in rows:
                    print(f"    {_label(row.get('account_id'))}: "
                          f"{_c(row)} lines, total={_f(row.get('amount')):,.2f} EGP")
            except Exception as exc:
                print(f"  Analytic line query failed: {exc}")

        results.update({
            "path_a_available": bool(aa_ids),
            "mechanism": "project → aa_field → account.analytic.account (direct)",
            "primary_field": primary_field,
            "analytic_ids": aa_ids,
        })

    else:
        print("\n  Analytic fields exist but none point to account.analytic.plan or .account.")
        print("  Path A mechanism is unclear — manual investigation needed.")

    return results


# ── SECTION 4: Path B — Dedicated GL accounts per project ────────────────────

def section4_path_b(client, uid, project_data, gl_fields):
    print(f"\n{SEP}")
    print("  SECTION 4 — PATH B: Dedicated GL Accounts per Project")
    print("  Goal: are the GL accounts truly distinct per project?")
    print("        If yes — can we compute P&L from account.move.line directly?")
    print(SEP)

    if not gl_fields:
        print("\n  !! No GL account fields found on rs.structure.project (§1B was empty).")
        print("  Path B is NOT available.")
        return {"path_b_available": False, "reason": "no GL account fields on project model"}

    print(f"\n  GL account fields count: {len(gl_fields)}")

    # Collect all account IDs across all projects, per field
    # Check distinctness: if each project has different account IDs → truly project-specific
    field_to_project_accounts = {}  # fname → {proj_name: account_id}
    all_account_ids = set()

    for proj in project_data:
        pname = proj["name"]
        for fname, fmeta in gl_fields.items():
            val = proj["gl_values"].get(fname)
            aid = _id(val)
            aname = _name(val)
            if fname not in field_to_project_accounts:
                field_to_project_accounts[fname] = {}
            field_to_project_accounts[fname][pname] = (aid, aname)
            if aid:
                all_account_ids.add(aid)

    # Distinctness check
    print(f"\n  Distinctness check — are account IDs unique per project?")
    print(f"  {'GL FIELD':<45}  {'DISTINCT_PER_PROJECT':<20}  ACCOUNT IDs")
    print(f"  {'-'*45}  {'-'*20}  {'-'*50}")

    all_distinct = True
    any_shared   = False
    for fname in sorted(field_to_project_accounts.keys()):
        proj_accounts = field_to_project_accounts[fname]
        ids = [v[0] for v in proj_accounts.values() if v[0] is not None]
        distinct = len(ids) == len(set(ids)) and len(ids) > 0
        if not distinct:
            all_distinct = False
        if len(ids) > 0 and len(set(ids)) < len(ids):
            any_shared = True
        fstring = gl_fields[fname].get("string", fname)
        ids_str = ", ".join(str(i) for i in ids) if ids else "(all empty)"
        marker = "✓ distinct" if distinct else ("✗ shared" if any_shared else "? empty")
        print(f"  {fstring:<45}  {marker:<20}  {ids_str}")

    print(f"\n  Overall: {'all GL accounts are distinct per project' if all_distinct else 'some accounts are shared or empty'}")

    if not all_account_ids:
        print("\n  No account IDs collected — Path B accounts are all empty.")
        return {"path_b_available": False, "reason": "all GL account fields are empty"}

    # Check if these accounts have actual transactions in account.move.line
    account_ids_list = sorted(all_account_ids)
    print(f"\n  Accounts found: {account_ids_list}")
    print(f"\n  Checking account.move.line for transactions on these accounts...")

    try:
        rows = do_group(client, uid, _MOVE_LINE,
                        [("parent_state", "=", "posted"),
                         ("account_id", "in", account_ids_list)],
                        ["debit", "credit", "__count"],
                        ["account_id"])
        print(f"  {'ACCOUNT':<50}  {'LINES':>6}  {'DEBIT':>20}  {'CREDIT':>20}")
        print(f"  {'-'*50}  {'-'*6}  {'-'*20}  {'-'*20}")
        total_lines = 0
        for row in sorted(rows, key=lambda r: -_c(r)):
            acct_label = _label(row.get("account_id"))
            cnt    = _c(row)
            debit  = _f(row.get("debit"))
            credit = _f(row.get("credit"))
            total_lines += cnt
            print(f"  {acct_label:<50}  {cnt:>6,}  {debit:>20,.2f}  {credit:>20,.2f}")
        print(f"\n  Total move lines on project GL accounts: {total_lines:,}")
        if total_lines == 0:
            print("  !! Zero transactions on project GL accounts — Path B has no live data yet.")
    except Exception as exc:
        print(f"  account.move.line groupby failed: {exc}")
        total_lines = None

    # Also: can we identify project from account_id alone?
    # Build reverse map: account_id → project
    account_to_project = {}
    for proj in project_data:
        pname = proj["name"]
        for fname in gl_fields:
            val = proj["gl_values"].get(fname)
            aid = _id(val)
            fstring = gl_fields[fname].get("string", fname)
            if aid:
                if aid not in account_to_project:
                    account_to_project[aid] = []
                account_to_project[aid].append({"project": pname, "field": fstring})

    print(f"\n  Reverse map: account_id → project (for P&L attribution):")
    ambiguous = 0
    for aid in sorted(account_to_project.keys()):
        entries = account_to_project[aid]
        if len(entries) > 1:
            ambiguous += 1
            print(f"  !! account_id={aid} is shared: {entries}")
        else:
            e = entries[0]
            print(f"  account_id={aid}  → project='{e['project']}'  field='{e['field']}'")

    if ambiguous > 0:
        print(f"\n  !! {ambiguous} account(s) are shared across projects — attribution is ambiguous for those.")
    else:
        print(f"\n  ✓ All accounts are unambiguously attributed to one project.")

    return {
        "path_b_available": len(all_account_ids) > 0,
        "gl_account_count": len(all_account_ids),
        "all_distinct": all_distinct,
        "total_move_lines": total_lines,
        "account_to_project": account_to_project,
    }


# ── SECTION 5: Comparison — Path A vs Path B + SEPARATE-ENTITIES verdict ─────

def section5_comparison(path_a, path_b, project_data, gl_fields):
    print(f"\n{SEP}")
    print("  SECTION 5 — COMPARISON: Path A vs Path B")
    print("  + Verdict on SEPARATE-ENTITIES from previous discovery")
    print(SEP)

    pa_avail = path_a.get("path_a_available", False)
    pb_avail = path_b.get("path_b_available", False)

    print(f"""
  PATH A — Analytic Plan/Account on Project
  {'─'*65}
  Available:   {pa_avail}
  Mechanism:   {path_a.get('mechanism', 'N/A')}
  Primary field: {path_a.get('primary_field', 'N/A')}
  Plan IDs found: {path_a.get('plan_ids', path_a.get('analytic_ids', 'N/A'))}
  How data flows: project.{path_a.get('primary_field','?')} → analytic plan/account
                  → account.analytic.line (amount = ? — see §3)
  Limitation: analytic line amounts = 0.00 EGP in production (known from prior discovery).
              Path A depends on analytic amounts being populated — currently blocked.

  PATH B — Dedicated GL Accounts per Project
  {'─'*65}
  Available:   {pb_avail}
  GL account fields: {path_b.get('gl_account_count', 0)} accounts across {len(project_data)} projects
  All accounts distinct: {path_b.get('all_distinct', '?')}
  Posted move lines on these accounts: {path_b.get('total_move_lines', '?')}
  How data flows: project → fixed account.account IDs per project
                  → account.move.line grouped by account_id (direct GL query)
  Limitation: only as complete as the GL account coverage — if La Verde posts
              revenue/expenses to accounts NOT in this map, they are missed.
""")

    # Recommendation
    print(f"  RECOMMENDATION FOR MODULE 4")
    print(f"  {'─'*65}")

    pb_lines = path_b.get("total_move_lines")
    pb_distinct = path_b.get("all_distinct", False)

    if pb_avail and pb_distinct and (pb_lines is None or pb_lines >= 0):
        if pb_lines and pb_lines > 0:
            rec = "PATH B (GL accounts) — PREFERRED"
            rationale = (
                "Path B is simpler, more direct, and already has live data. "
                "Each project has its own dedicated account.account IDs for each "
                "revenue/expense category. Module 4 can compute per-project P&L "
                "by querying account.move.line WHERE account_id IN (project's GL accounts). "
                "No analytic amounts needed — bypasses the analytic = 0 blocker entirely. "
                "The SEPARATE-ENTITIES problem from the previous discovery is irrelevant "
                "for this path: we never need to join rs.structure.project to "
                "account.analytic.account."
            )
        else:
            rec = "PATH B (GL accounts) — PREFERRED once expenses enter GL"
            rationale = (
                "Path B is structurally sound — each project has distinct GL accounts. "
                "But zero move lines currently means expenses haven't been posted yet. "
                "Once expenses appear in GL (the known blocker for Module 4), "
                "Path B will work without analytic amounts. "
                "Path A remains blocked until analytic amounts are populated. "
                "The SEPARATE-ENTITIES problem is irrelevant for Path B."
            )
    elif pa_avail and not pb_avail:
        rec = "PATH A (analytic) — only option, but currently blocked"
        rationale = (
            "Path B accounts not available or not distinct. "
            "Path A via analytic plan is the only structural link, "
            "but is blocked until analytic line amounts are populated. "
            "Investigate with La Verde when analytic amounts will be filled."
        )
    else:
        rec = "NEEDS FURTHER INVESTIGATION"
        rationale = "Both paths have limitations. See §3 and §4 for details."

    print(f"  {rec}")
    print(f"\n  Rationale:")
    for line in rationale.split(". "):
        if line.strip():
            print(f"    {line.strip()}.")

    # Verdict on SEPARATE-ENTITIES
    print(f"\n  {'─'*65}")
    print(f"  VERDICT ON PREVIOUS 'SEPARATE-ENTITIES' FINDING")
    print(f"  {'─'*65}")

    if pa_avail or pb_avail:
        verdict = "INCOMPLETE (not wrong in the narrow sense, but misleading)"
        verdict_detail = (
            "The previous discovery correctly observed that rs.structure.project "
            "and account.analytic.account are different models. That is true. "
            "BUT it concluded 'architectural constraint / mapping table needed' — "
            "which is wrong. The project model itself contains the mapping:\n"
            "  - Path A: rs.structure.project has an analytic plan/account field "
            "    that directly links it to the analytic hierarchy.\n"
            "  - Path B: rs.structure.project has dedicated GL account fields "
            "    (Revenue, Maintenance, Penalty, etc.) so per-project P&L "
            "    can be computed directly from account.move.line without any "
            "    external mapping table.\n"
            "The previous script asked account.move and account.analytic.account "
            "but never asked rs.structure.project — it missed the built-in mapping."
        )
    else:
        verdict = "CANNOT FULLY REVERSE — both paths have issues"
        verdict_detail = (
            "The SEPARATE-ENTITIES conclusion may still be partially valid. "
            "See §3 and §4 for specifics."
        )

    print(f"  Verdict: {verdict}")
    print()
    for line in verdict_detail.split("\n"):
        print(f"  {line}")

    return {
        "recommendation": rec,
        "verdict_on_separate_entities": verdict,
        "path_a_preferred": pa_avail and not pb_avail,
        "path_b_preferred": pb_avail and pb_distinct,
    }


# ── SECTION 6: Summary ────────────────────────────────────────────────────────

def section6_summary(s1, s2, s3, s4, s5, project_data):
    print(f"\n{SEP}")
    print("  SECTION 6 — DISCOVERY SUMMARY")
    print(f"  Date: {TODAY}   Cost: $0.00   Target: production (laverde.odoo.com)")
    print(SEP)

    print(f"""
  Q1 — rs.structure.project schema
  {'─'*65}
  Total fields:          {len(s1.get('all_fields', {})):,}
  Analytic fields:       {len(s1.get('analytic_fields', {}))} — {list(s1.get('analytic_fields', {}).keys())}
  GL account fields:     {len(s1.get('gl_fields', {}))} — (many2one → account.account)

  Q2 — Data for {len(project_data)} project(s)
  {'─'*65}""")
    for proj in project_data:
        ga = {k: v for k, v in proj["gl_values"].items() if v}
        aa = {k: v for k, v in proj["analytic_values"].items() if v}
        print(f"  Project '{proj['name']}' (id={proj['id']}):")
        print(f"    Analytic fields populated: {len(aa)} / {len(proj['analytic_values'])}")
        print(f"    GL fields populated:       {len(ga)} / {len(proj['gl_values'])}")

    print(f"""
  Q3/Q4 — Path comparison
  {'─'*65}
  PATH A (analytic):   available={s3.get('path_a_available')}  mechanism={s3.get('mechanism','N/A')}
  PATH B (GL direct):  available={s4.get('path_b_available')}  distinct={s4.get('all_distinct')}  move_lines={s4.get('total_move_lines')}

  Q5 — Recommendation
  {'─'*65}
  {s5.get('recommendation', '?')}

  Q6 — SEPARATE-ENTITIES verdict
  {'─'*65}
  {s5.get('verdict_on_separate_entities', '?')}

  IMPLICATIONS FOR MODULE 4
  {'─'*65}""")

    if s4.get("path_b_available") and s4.get("all_distinct"):
        print("""  Path B (dedicated GL accounts) makes Module 4 simpler:
  - Per-project P&L = account.move.line WHERE account_id IN (project's accounts)
  - No analytic amounts needed. No mapping table. No join across models.
  - Remaining blocker: expenses still haven't entered GL (known from prior discovery).
  - Once expenses appear → Path B is immediately usable.""")
    elif s3.get("path_a_available"):
        print("""  Path A (analytic plan) is the structural link.
  - Blocked until analytic line amounts are populated.
  - Investigate with La Verde: when will analytic amounts be filled?""")
    else:
        print("  Neither path is fully confirmed — see §3 and §4.")

    print(f"\n{SEP}")
    print(f"  All reads were read-only. No data modified in Odoo.")
    print(f"  Production endpoint: {ODOO_URL}")
    print(SEP)


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    output_path = (Path(__file__).parent /
                   f"discover_project_accounting_link_{TODAY}.txt")
    output_buffer = StringIO()

    class Tee:
        def __init__(self, terminal, buf):
            self.terminal = terminal
            self.buf = buf

        def write(self, data):
            self.buf.write(data)
            try:
                self.terminal.write(data)
            except UnicodeEncodeError:
                safe = data.encode(
                    self.terminal.encoding or "utf-8", errors="replace"
                ).decode(self.terminal.encoding or "utf-8", errors="replace")
                self.terminal.write(safe)

        def flush(self):
            self.buf.flush()
            self.terminal.flush()

    sys.stdout = Tee(sys.__stdout__, output_buffer)

    try:
        print(SEP)
        print("  Project-Accounting Link: Directed Discovery")
        print(f"  Run date:   {TODAY}")
        print(f"  Odoo URL:   {ODOO_URL}")
        print(f"  Constraint: READ-ONLY ({sorted(ALLOWED_METHODS)})")
        print(f"  PII policy: field names + aggregates only. No customer/vendor names.")
        print(f"  Cost:       $0.00  (no OpenAI calls)")
        print(f"  Target:     production only (laverde.odoo.com)")
        print(f"  Context:    supersedes SEPARATE-ENTITIES verdict from 2026-05-28 discovery")
        print(SEP)

        with httpx.Client() as client:
            uid = connect(client)

            s1 = section1_project_fields(client, uid)
            project_data = section2_project_data(
                client, uid,
                analytic_fields=s1["analytic_fields"],
                gl_fields=s1["gl_fields"],
            )
            s3 = section3_path_a(client, uid, project_data, s1["analytic_fields"])
            s4 = section4_path_b(client, uid, project_data, s1["gl_fields"])
            s5 = section5_comparison(s3, s4, project_data, s1["gl_fields"])

        section6_summary(s1, {}, s3, s4, s5, project_data)

        print(f"\n  All read-only. No data modified in Odoo.")

    except Exception as exc:
        print(f"\n[FATAL] {exc}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        sys.stdout = sys.__stdout__
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(output_buffer.getvalue())
        print(f"\n  Output saved to: {output_path}")


if __name__ == "__main__":
    main()
