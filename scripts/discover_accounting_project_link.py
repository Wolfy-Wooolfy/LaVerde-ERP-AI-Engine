"""
discover_accounting_project_link.py — Accounting Module: Project-Link Discovery
READ-ONLY: fields_get / search_read / search_count / read_group ONLY.
No create/write/unlink. No OpenAI. AI cost = $0.00.

Goal: Discover and document the two project-linking mechanisms in La Verde Odoo,
and determine whether they reference the same entity (unification feasibility).

  Mechanism 1 — Revenue via Real Estate Details (custom fields on account.move)
  Mechanism 2 — Expenses via Analytic Distribution (standard Odoo field on account.move.line)

Key questions:
  Q1 — Real Estate Details: field names on account.move + account.move.line + coverage
  Q2 — Analytic hierarchy: Project-level accounts, parent structure, Phase → Project path
  Q3 — Unification (DATA-LEVEL): are the two "Project" references the same entity?
       Method: read actual project IDs from RE Details entries; compare with
       analytic account IDs at Project level. Verdict: identical / same-model-different-
       records / separate-entities / inconclusive.
  Q4 — Production expense state: in_invoice/in_refund count + expense line count
  Q5 — Move types carrying Real Estate project data

Production only: laverde.odoo.com (ODOO_URL from .env)
Test DB observation from Khaled's manual inspection is noted inline but generates NO RPCs.
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

# ── TARGET MODELS ─────────────────────────────────────────────────────────────
_MOVE_MODEL    = "account.move"
_LINE_MODEL    = "account.move.line"
_ANALYTIC_ACCT = "account.analytic.account"

# Keywords to identify Real Estate Details fields
_RE_KEYWORDS = {"unit", "building", "zone", "phase", "project", "reservation"}

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


def do_group(client, uid, model, domain, agg_fields, groupby):
    return execute(client, uid, model, "read_group",
                   [domain, agg_fields, groupby], {"lazy": False})


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

def _f(val) -> float:
    return float(val) if val else 0.0


def _c(row) -> int:
    return int(row.get("__count") or 0)


def _label(val) -> str:
    if isinstance(val, (list, tuple)) and len(val) == 2:
        return str(val[1])
    return str(val) if val else "(none)"


def _id(val):
    if isinstance(val, (list, tuple)) and val:
        return val[0]
    return val


def _find_re_fields(fields_dict: dict) -> dict:
    """Return fields whose technical name contains a Real Estate keyword."""
    found = {}
    for fname, fmeta in fields_dict.items():
        low = fname.lower()
        if any(kw in low for kw in _RE_KEYWORDS):
            found[fname] = fmeta
    return found


# ── SECTION 1: Schema — Real Estate Details + Analytic fields ────────────────

def section1_schema(client, uid) -> dict:
    print(f"\n{SEP}")
    print("  SECTION 1 — Schema Discovery")
    print("  Goal: technical field names on account.move and account.move.line")
    print("  A) Real Estate Details fields (unit/building/zone/phase/project/reservation)")
    print("  B) Analytic fields (analytic_distribution and related)")
    print(SEP)

    # --- A: account.move ---
    print(f"\n  A) account.move — fields_get")
    flds_move = do_fields_get(client, uid, _MOVE_MODEL)
    re_fields_move = _find_re_fields(flds_move)

    if re_fields_move:
        print(f"\n  Found {len(re_fields_move)} RE candidate field(s) on account.move:")
        print(f"  {'FIELD_NAME':<45}  {'TYPE':<12}  {'RELATION':<35}  STRING")
        print(f"  {'-'*45}  {'-'*12}  {'-'*35}  {'-'*20}")
        for fname, meta in sorted(re_fields_move.items()):
            ftype    = meta.get("type", "")
            relation = meta.get("relation", "") or ""
            fstring  = meta.get("string", "")
            print(f"  {fname:<45}  {ftype:<12}  {relation:<35}  {fstring}")
    else:
        print("\n  !! No Real Estate candidate fields found on account.move.")
        print(f"  Keywords searched: {sorted(_RE_KEYWORDS)}")
        print("  This is a significant finding — showing all many2one fields for inspection:")
        m2o = {fn: fm for fn, fm in flds_move.items() if fm.get("type") == "many2one"}
        for fname, meta in sorted(m2o.items())[:40]:
            print(f"    {fname:<50}  → {meta.get('relation','')}")

    # --- B: account.move.line ---
    print(f"\n  B) account.move.line — fields_get")
    flds_line = do_fields_get(client, uid, _LINE_MODEL)

    # RE fields on move.line
    re_fields_line = _find_re_fields(flds_line)
    if re_fields_line:
        print(f"\n  Found {len(re_fields_line)} RE candidate field(s) on account.move.line:")
        for fname, meta in sorted(re_fields_line.items()):
            ftype    = meta.get("type", "")
            relation = meta.get("relation", "") or ""
            fstring  = meta.get("string", "")
            print(f"    {fname:<45}  {ftype:<12}  {relation:<35}  {fstring}")
    else:
        print("  No RE candidate fields on account.move.line.")

    # Analytic fields on move.line
    analytic_fields_line = {fn: fm for fn, fm in flds_line.items()
                            if "analytic" in fn.lower()}
    print(f"\n  Analytic fields on account.move.line:")
    if analytic_fields_line:
        for fname, meta in sorted(analytic_fields_line.items()):
            ftype    = meta.get("type", "")
            relation = meta.get("relation", "") or ""
            fstring  = meta.get("string", "")
            print(f"    {fname:<45}  {ftype:<12}  {relation:<35}  {fstring}")
    else:
        print("  No 'analytic' fields found on account.move.line.")

    return {
        "re_fields_move": re_fields_move,
        "re_fields_line": re_fields_line,
        "analytic_fields_line": analytic_fields_line,
        "all_move_fields": flds_move,
    }


# ── SECTION 2: Revenue coverage via Real Estate Details ──────────────────────

def section2_revenue_coverage(client, uid, re_fields_move: dict) -> dict:
    print(f"\n{SEP}")
    print("  SECTION 2 — Revenue Coverage: entries with Project set (Real Estate Details)")
    print("  Goal: coverage count + move_type breakdown")
    print(SEP)

    # Find the project field specifically
    project_field = None
    for fname in re_fields_move:
        if "project" in fname.lower():
            project_field = fname
            break

    if not project_field:
        print("\n  !! No 'project' field found among RE fields on account.move.")
        print(f"  Available RE fields: {list(re_fields_move.keys())}")
        print("  Cannot measure project coverage — document as finding.")
        return {"project_field": None, "coverage_count": None, "total_posted": None}

    fmeta = re_fields_move[project_field]
    print(f"\n  Project field identified: '{project_field}'")
    print(f"  Type: {fmeta.get('type')}  Relation: {fmeta.get('relation','')}  String: {fmeta.get('string','')}")

    total_posted = do_count(client, uid, _MOVE_MODEL, [("state", "=", "posted")])
    with_project = do_count(client, uid, _MOVE_MODEL,
                            [("state", "=", "posted"), (project_field, "!=", False)])
    without_project = total_posted - with_project
    pct = (with_project / total_posted * 100) if total_posted else 0.0

    print(f"\n  Posted entries (total):         {total_posted:>10,}")
    print(f"  Posted entries WITH Project:    {with_project:>10,}  ({pct:.1f}%)")
    print(f"  Posted entries WITHOUT Project: {without_project:>10,}")

    # Move type breakdown for entries with project
    print(f"\n  Move type breakdown (entries WITH project set):")
    try:
        mt_rows = do_group(client, uid, _MOVE_MODEL,
                           [("state", "=", "posted"), (project_field, "!=", False)],
                           ["__count"], ["move_type"])
        print(f"  {'MOVE_TYPE':<30}  {'COUNT':>8}")
        print(f"  {'-'*30}  {'-'*8}")
        for row in sorted(mt_rows, key=lambda r: -_c(r)):
            mt  = str(row.get("move_type") or "(empty)")
            cnt = _c(row)
            print(f"  {mt:<30}  {cnt:>8,}")
    except Exception as exc:
        print(f"  read_group move_type failed: {exc}")

    return {
        "project_field": project_field,
        "project_field_meta": fmeta,
        "total_posted": total_posted,
        "coverage_count": with_project,
    }


# ── SECTION 3: Analytic Account hierarchy ────────────────────────────────────

def section3_analytic_hierarchy(client, uid) -> dict:
    print(f"\n{SEP}")
    print("  SECTION 3 — Analytic Hierarchy: Project-level accounts + Phase → Project path")
    print("  Goal: identify root accounts (Project level) for data-level comparison in §5")
    print(SEP)

    aa_flds = do_fields_get(client, uid, _ANALYTIC_ACCT)
    has_parent       = "parent_id" in aa_flds
    has_complete     = "complete_name" in aa_flds
    has_plan         = "plan_id" in aa_flds
    print(f"\n  Available key fields: parent_id={has_parent}, complete_name={has_complete}, plan_id={has_plan}")

    fetch_fields = ["id", "name"]
    if has_complete:
        fetch_fields.append("complete_name")
    if has_parent:
        fetch_fields.append("parent_id")
    if has_plan:
        fetch_fields.append("plan_id")

    project_accounts = []  # root-level = Project tier

    if has_parent:
        root_rows = do_read(client, uid, _ANALYTIC_ACCT,
                            [("parent_id", "=", False)],
                            fetch_fields, order="id asc")
        print(f"\n  Root-level analytic accounts (parent_id=False) — {len(root_rows)} found:")
        print(f"  These are the Project-tier accounts:")
        print(f"  {'ID':<8}  NAME")
        print(f"  {'-'*8}  {'-'*55}")
        for row in root_rows:
            aid  = row.get("id")
            name = row.get("complete_name") or row.get("name") or ""
            project_accounts.append({"id": aid, "name": name})
            print(f"  {aid:<8}  {name}")
    else:
        print("\n  parent_id not available — fetching sample of all accounts for hierarchy clues:")
        all_rows = do_read(client, uid, _ANALYTIC_ACCT, [],
                           fetch_fields, limit=50, order="id asc")
        for row in all_rows:
            aid  = row.get("id")
            name = row.get("complete_name") or row.get("name") or ""
            project_accounts.append({"id": aid, "name": name})
            print(f"  {aid:<8}  {name}")

    # Children per root (to confirm hierarchy depth and Phase→Project path)
    if has_parent and project_accounts:
        print(f"\n  Children per Project (confirms Phase → Project path via parent_id):")
        try:
            child_rows = do_group(client, uid, _ANALYTIC_ACCT,
                                  [("parent_id", "!=", False)],
                                  ["__count"], ["parent_id"])
            child_rows_sorted = sorted(child_rows, key=lambda r: -_c(r))
            print(f"  {'PARENT (Project-tier?)':<45}  {'DIRECT CHILDREN':>15}")
            print(f"  {'-'*45}  {'-'*15}")
            for row in child_rows_sorted[:20]:
                parent_raw  = row.get("parent_id")
                parent_name = _label(parent_raw)
                cnt = _c(row)
                print(f"  {parent_name:<45}  {cnt:>15,}")
        except Exception as exc:
            print(f"  Child count groupby failed: {exc}")

    # Total count for context
    total_aa = do_count(client, uid, _ANALYTIC_ACCT, [])
    root_count = len(project_accounts)
    print(f"\n  Total analytic accounts: {total_aa:,}   Root (Project-tier): {root_count}")

    return {
        "project_accounts": project_accounts,
        "has_parent": has_parent,
        "total_aa": total_aa,
    }


# ── SECTION 4: Production expense state ──────────────────────────────────────

def section4_production_expenses(client, uid) -> dict:
    print(f"\n{SEP}")
    print("  SECTION 4 — Production Expense State")
    print("  EXPECTED RESULT: likely zero — production has not received expense data yet.")
    print("  Zero is the expected finding here, not a failure.")
    print(SEP)

    # Vendor bills (in_invoice / in_refund)
    in_invoice_count = do_count(client, uid, _MOVE_MODEL,
                                [("state", "=", "posted"),
                                 ("move_type", "in", ["in_invoice", "in_refund"])])
    print(f"\n  Posted vendor bills (in_invoice + in_refund): {in_invoice_count:>8,}")

    # All posted move_types for context
    print(f"\n  All posted move_types (context):")
    try:
        mt_rows = do_group(client, uid, _MOVE_MODEL,
                           [("state", "=", "posted")],
                           ["__count"], ["move_type"])
        for row in sorted(mt_rows, key=lambda r: -_c(r)):
            mt  = str(row.get("move_type") or "(empty)")
            cnt = _c(row)
            print(f"    {mt:<30}  {cnt:>8,}")
    except Exception as exc:
        print(f"  move_type groupby failed: {exc}")

    # Expense lines count + amounts
    exp_count  = None
    exp_debit  = None
    exp_credit = None
    expense_types = ["expense", "expense_depreciation", "expense_direct_cost"]
    try:
        exp_rows = do_group(client, uid, _LINE_MODEL,
                            [("parent_state", "=", "posted"),
                             ("account_id.account_type", "in", expense_types)],
                            ["debit", "credit"], [])
        row        = exp_rows[0] if exp_rows else {}
        exp_count  = _c(row)
        exp_debit  = _f(row.get("debit"))
        exp_credit = _f(row.get("credit"))
        print(f"\n  Expense move lines (posted, account_type in {expense_types}):")
        print(f"    Line count:   {exp_count:>10,}")
        print(f"    SUM(debit):   {exp_debit:>20,.2f} EGP")
        print(f"    SUM(credit):  {exp_credit:>20,.2f} EGP")
    except Exception as exc:
        print(f"\n  Expense line query failed: {exc}")

    # Test DB context — manual observation by Khaled, NO RPCs to test DB
    print(f"\n  ── Test DB context (laverde-test.odoo.com) ──────────────────────────────")
    print(f"  Source: Khaled's manual inspection — NOT an RPC. Zero RPCs sent to test DB.")
    print(f"  Observed: 9 vendor bills (in_invoice), total ~85,496 EGP.")
    print(f"  Analytic Distribution on those bills links at Phase level")
    print(f"    (e.g. 'PhasePhase1998', not Project directly).")
    print(f"  Phase.parent_id → Project (to be confirmed vs §3 hierarchy).")
    print(f"  This is a MODEL for how expenses will look when they enter production.")
    print(f"  It is NOT a statement about production state (production measured above).")

    # Explicit implication if production is zero
    if in_invoice_count == 0:
        print(f"\n  ⚠️  IMPLICATION (production = 0 vendor bills):")
        print(f"  The expense-linking mechanism (Analytic Distribution) is documented")
        print(f"  from schema (fields_get in §1) + test DB observation (above) ONLY.")
        print(f"  It cannot be verified against real production expense data at this time.")
        print(f"  DEFERRED: verify Analytic Distribution in a later discovery session")
        print(f"  after expense data is entered into production.")
    else:
        print(f"\n  ✓ Production has {in_invoice_count} vendor bills — Analytic Distribution")
        print(f"  can be verified against real production data (see §5 unification).")

    return {
        "in_invoice_count": in_invoice_count,
        "exp_count": exp_count,
        "exp_debit": exp_debit,
        "exp_credit": exp_credit,
    }


# ── SECTION 5: Unification — DATA-LEVEL comparison ───────────────────────────

def section5_unification(client, uid,
                         project_field,
                         project_field_meta: dict,
                         project_accounts: list,
                         all_move_fields: dict) -> dict:
    print(f"\n{SEP}")
    print("  SECTION 5 — Unification: are the two 'Project' references the same entity?")
    print("  Method: DATA-LEVEL comparison, not schema-only.")
    print("  Step A: read actual Project values (ID + name) from RE Details on posted entries")
    print("  Step B: list Project-tier analytic accounts (from §3)")
    print("  Step C: compare IDs and names — same model? overlapping records?")
    print("  Verdict: identical / same-model-different-records / separate-entities / inconclusive")
    print(SEP)

    if not project_field:
        print("\n  Cannot perform unification — no project field was found in §1.")
        print("  VERDICT: INCONCLUSIVE — Real Estate Details project field not identified.")
        return {"verdict": "inconclusive", "reason": "project field not found in §1"}

    project_field_type     = project_field_meta.get("type", "")
    project_field_relation = project_field_meta.get("relation", "") or ""

    print(f"\n  RE Details project field: '{project_field}'")
    print(f"  Field type:     {project_field_type}")
    print(f"  Relation model: {project_field_relation or '(none — not a relational field)'}")

    # ── Step A: sample actual project values from entries ─────────────────────
    print(f"\n  Step A — Reading actual Project values from account.move (up to 50 entries):")
    re_project_values = []
    try:
        sample_rows = do_read(client, uid, _MOVE_MODEL,
                              [("state", "=", "posted"), (project_field, "!=", False)],
                              ["id", project_field],
                              limit=50, order="id desc")

        seen_projects = {}
        for row in sample_rows:
            pval = row.get(project_field)
            if isinstance(pval, (list, tuple)) and len(pval) == 2:
                pid, pname = pval[0], pval[1]
            elif isinstance(pval, int):
                pid, pname = pval, str(pval)
            elif pval:
                pid, pname = None, str(pval)
            else:
                continue
            if pid not in seen_projects:
                seen_projects[pid] = pname
                re_project_values.append({"id": pid, "name": pname})

        print(f"  Distinct Project values in RE Details ({len(re_project_values)} found):")
        print(f"  {'ID':<10}  NAME")
        print(f"  {'-'*10}  {'-'*55}")
        for pv in re_project_values:
            print(f"  {str(pv['id']):<10}  {pv['name']}")

        if not re_project_values:
            print("  No distinct project values extracted — entries may exist but field returns no data.")

    except Exception as exc:
        print(f"  Sample read failed: {exc}")
        return {"verdict": "inconclusive", "reason": f"sample read error: {exc}",
                "re_project_values": []}

    # ── Step B: analytic Project-tier accounts ────────────────────────────────
    print(f"\n  Step B — Analytic Project-tier accounts (root-level from §3):")
    print(f"  {'ID':<10}  NAME")
    print(f"  {'-'*10}  {'-'*55}")
    for pa in project_accounts:
        print(f"  {str(pa['id']):<10}  {pa['name']}")
    if not project_accounts:
        print("  (none found — see §3)")

    # ── Step C: comparison ────────────────────────────────────────────────────
    print(f"\n  Step C — Comparison:")

    relation_matches_model = (project_field_relation == _ANALYTIC_ACCT)

    re_ids   = {pv["id"] for pv in re_project_values if pv["id"] is not None}
    aa_ids   = {pa["id"] for pa in project_accounts}
    re_names = {pv["name"] for pv in re_project_values if pv["name"]}
    aa_names = {pa["name"] for pa in project_accounts if pa["name"]}

    ids_overlap   = re_ids & aa_ids
    names_overlap = re_names & aa_names

    print(f"  RE relation model:       {project_field_relation or '(none)'}")
    print(f"  Analytic account model:  {_ANALYTIC_ACCT}")
    print(f"  Relation matches model:  {relation_matches_model}")
    print(f"  RE project IDs:          {sorted(re_ids)}")
    print(f"  Analytic root IDs:       {sorted(aa_ids)}")
    print(f"  ID overlap:              {sorted(ids_overlap)}")
    print(f"  Name overlap:            {sorted(names_overlap)}")

    # ── Verdict ───────────────────────────────────────────────────────────────
    if relation_matches_model and ids_overlap:
        verdict = "identical"
        verdict_note = (
            f"RE Details '{project_field}' points to account.analytic.account, "
            f"and {len(ids_overlap)} IDs overlap with root-level analytic accounts. "
            f"Revenue and expense Project references are the same entity. "
            f"Module 4 can unify both mechanisms on analytic account ID with no mapping table."
        )
    elif relation_matches_model and re_ids and not ids_overlap:
        verdict = "same-model-different-records"
        verdict_note = (
            f"RE Details '{project_field}' relation is account.analytic.account (same model), "
            f"but IDs do not overlap with root-level (Project-tier) accounts. "
            f"Likely cause: RE Details references Phase-level accounts (not the root). "
            f"Module 4 would need to traverse parent_id to reach Project level for both mechanisms. "
            f"No separate mapping table needed, but a parent_id join is required."
        )
    elif relation_matches_model and not re_ids:
        verdict = "inconclusive"
        verdict_note = (
            f"RE Details '{project_field}' relation is account.analytic.account, "
            f"but no entries with the project field set were found in the sample — "
            f"could not extract actual IDs for comparison. "
            f"Re-run after confirming that entries with Project data exist in production."
        )
    elif not relation_matches_model and project_field_relation:
        verdict = "separate-entities"
        verdict_note = (
            f"RE Details '{project_field}' points to '{project_field_relation}', "
            f"NOT to account.analytic.account. These are separate entity types. "
            f"Module 4 would need a mapping between '{project_field_relation}' records "
            f"and account.analytic.account records to unify revenue and expense by project. "
            f"This is a significant architectural constraint — must be resolved before planning."
        )
    else:
        verdict = "inconclusive"
        verdict_note = (
            "Could not determine unification — either no project values found in RE Details, "
            "no analytic project accounts found, or field type is not relational. "
            "Further investigation needed."
        )

    print(f"\n  {'─'*65}")
    print(f"  UNIFICATION VERDICT: {verdict.upper()}")
    print(f"  {verdict_note}")
    print(f"  {'─'*65}")

    return {
        "verdict": verdict,
        "verdict_note": verdict_note,
        "re_project_values": re_project_values,
        "relation_matches_model": relation_matches_model,
        "ids_overlap": sorted(ids_overlap),
        "names_overlap": sorted(names_overlap),
    }


# ── SECTION 6: Summary ────────────────────────────────────────────────────────

def section6_summary(s1, s2, s3, s4, s5):
    print(f"\n{SEP}")
    print("  SECTION 6 — DISCOVERY SUMMARY")
    print(f"  Date: {TODAY}   Cost: $0.00   Target: production (laverde.odoo.com)")
    print(SEP)

    re_fields_move    = s1.get("re_fields_move", {})
    analytic_flds_ln  = s1.get("analytic_fields_line", {})

    project_field     = s2.get("project_field")
    coverage          = s2.get("coverage_count")
    total_posted      = s2.get("total_posted")
    pct = (coverage / total_posted * 100) if (coverage is not None and total_posted) else 0.0

    project_accounts  = s3.get("project_accounts", [])
    total_aa          = s3.get("total_aa", 0)

    in_inv            = s4.get("in_invoice_count")
    exp_cnt           = s4.get("exp_count")

    verdict           = s5.get("verdict", "?")
    verdict_note      = s5.get("verdict_note", "")

    print(f"""
  Q1 — Mechanism 1: Real Estate Details (Revenue)
  {'─'*65}
  RE fields on account.move:      {len(re_fields_move)}
  Field names:                    {list(re_fields_move.keys())}
  Project field identified:       {project_field or 'NOT FOUND'}
  Coverage:                       {f"{coverage:,} / {total_posted:,} posted entries ({pct:.1f}%)" if coverage is not None else "see §2"}

  Q2 — Mechanism 2: Analytic Distribution (Expenses)
  {'─'*65}
  Analytic fields on move.line:   {list(analytic_flds_ln.keys())}
  Analytic Project-tier accounts: {len(project_accounts)} (of {total_aa:,} total analytic accounts)
  Project names:                  {[pa['name'] for pa in project_accounts]}

  Q3 — Unification (data-level)
  {'─'*65}
  Verdict:   {verdict.upper()}
  Detail:    {verdict_note}

  Q4 — Production Expense State
  {'─'*65}
  Posted vendor bills (in_invoice/in_refund):  {in_inv if in_inv is not None else '?':>8}
  Expense move lines (GL, posted):             {exp_cnt if exp_cnt is not None else 'query failed':>8}
  Test DB (Khaled manual, NOT production):     9 vendor bills, ~85,496 EGP — MODEL only
  {'⚠️  Expense mechanism documented from schema + test DB observation ONLY.' if in_inv == 0 else '  ✓ Production expenses present.'}
  {'  Verification deferred to later discovery after expenses enter production.' if in_inv == 0 else ''}
""")

    print(f"  IMPLICATIONS FOR MODULE 4")
    print(f"  {'─'*65}")
    if verdict == "identical":
        print("""  Both mechanisms reference the same analytic account entity.
  Revenue (RE Details) + Expense (Analytic Distribution) can be unified
  on analytic account ID for per-project P&L. No mapping table needed.
  Remaining blocker: expenses have not entered production yet.
  P&L by project can be built once expense data arrives.""")
    elif verdict == "same-model-different-records":
        print("""  Both mechanisms use account.analytic.account, but may reference
  different hierarchy levels (RE Details → Phase, Analytic → Phase too).
  Module 4 unification: traverse parent_id to align at Project level.
  No separate mapping table needed — parent_id join is sufficient.
  Remaining blocker: expenses not yet in production.""")
    elif verdict == "separate-entities":
        print("""  Revenue and expense Project references are SEPARATE entity types.
  Module 4 cannot join them directly — a mapping table is required.
  This must be designed before Module 4 planning can proceed.
  Action: confirm with La Verde how RE project entity relates to analytic accounts.""")
    elif verdict == "inconclusive":
        print("""  Unification verdict could not be determined from available data.
  Likely cause: production has no entries with RE project field set,
  OR analytic project accounts could not be isolated.
  Action: investigate manually in Odoo UI, then re-run discovery.""")

    print(f"\n{SEP}")
    print(f"  All reads were read-only. No data modified in Odoo.")
    print(f"  Production endpoint: {ODOO_URL}")
    print(f"  Test DB context: from Khaled's manual inspection only — zero RPCs to test DB.")
    print(SEP)


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    output_path = (Path(__file__).parent /
                   f"discover_accounting_project_link_{TODAY}.txt")
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
        print("  Accounting Project-Link Discovery")
        print(f"  Run date:   {TODAY}")
        print(f"  Odoo URL:   {ODOO_URL}")
        print(f"  Constraint: READ-ONLY ({sorted(ALLOWED_METHODS)})")
        print(f"  PII policy: customer/vendor names redacted; field names + aggregates OK")
        print(f"  Cost:       $0.00  (no OpenAI calls)")
        print(f"  Target:     production only — NOT the test DB (laverde-test.odoo.com)")
        print(SEP)

        with httpx.Client() as client:
            uid = connect(client)
            s1  = section1_schema(client, uid)
            s2  = section2_revenue_coverage(client, uid, s1["re_fields_move"])
            s3  = section3_analytic_hierarchy(client, uid)
            s4  = section4_production_expenses(client, uid)
            s5  = section5_unification(
                client, uid,
                project_field      = s2.get("project_field"),
                project_field_meta = s2.get("project_field_meta", {}),
                project_accounts   = s3.get("project_accounts", []),
                all_move_fields    = s1.get("all_move_fields", {}),
            )

        section6_summary(s1, s2, s3, s4, s5)

        print(f"\n  All read-only. No data modified in Odoo.")

    except Exception as exc:
        print(f"\n[FATAL] {exc}")
        raise
    finally:
        sys.stdout = sys.__stdout__
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(output_buffer.getvalue())
        print(f"\n  Output saved to: {output_path}")


if __name__ == "__main__":
    main()
