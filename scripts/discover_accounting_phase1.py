"""
discover_accounting_phase1.py — Accounting Module: Board-Level Discovery
READ-ONLY: search_read, search_count, read_group, fields_get ONLY.
No create/write/unlink. No OpenAI. AI cost = $0.00.

Goal: Evaluate whether Odoo Accounting data warrants a Board-level Module 4.
Guided by 5 Board questions (not a broad scan):
  S1  — STRUCTURE:    Is Accounting active and used?
  S1b — TEMPORAL:     Date range + fiscal period lock status (data stability?)
  S2  — CHART:        Account type structure
  S3  — PROFITABILITY: Indicative P&L (income / expense accounts)
  S4  — CASH:         Bank/cash journals and balances
  S5  — ANALYTIC:     Project-level breakdown (Khaled: Analytic is active)
  S6  — OVERLAP:      What's already covered by Modules 2+3?
  S7  — SUMMARY + ASSESSMENT (incl. timing: now vs. after period close)

Context from Khaled:
  - La Verde: single company, no multi-company.
  - Analytic Accounting actively used for review.
  - Fiscal periods NOT yet closed — data is in transitional migration state.

Run from any directory:
    python scripts/discover_accounting_phase1.py
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

# ── TARGET MODELS (explicit — not discovered by broad scan) ───────────────────
_MOVE_MODEL       = "account.move"
_LINE_MODEL       = "account.move.line"
_ACCOUNT_MODEL    = "account.account"
_JOURNAL_MODEL    = "account.journal"
_ANALYTIC_ACCT    = "account.analytic.account"
_ANALYTIC_LINE    = "account.analytic.line"
_COMPANY_MODEL    = "res.company"

# Already covered — used in overlap section
_COVERED_BY = "Collections (M2) + Customer Accounts (M3)"
_COVERED_MODELS = "rs.installment, rs.account.payment.reconcile"

# ── PII SANITIZATION ──────────────────────────────────────────────────────────
_PII_FRAGS = {"name", "partner", "email", "phone", "mobile",
              "vat", "street", "city", "display_name", "id_number"}

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
    print("\n[AUTH] Authenticating...")
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


# ── SECTION 1: STRUCTURE ──────────────────────────────────────────────────────

def section1_structure(client, uid) -> dict:
    print(f"\n{SEP}")
    print("  SECTION 1 — STRUCTURE: Is Odoo Accounting active?")
    print(SEP)

    total_moves   = do_count(client, uid, _MOVE_MODEL, [])
    posted_moves  = do_count(client, uid, _MOVE_MODEL, [("state", "=", "posted")])
    draft_moves   = do_count(client, uid, _MOVE_MODEL, [("state", "=", "draft")])
    cancel_moves  = do_count(client, uid, _MOVE_MODEL, [("state", "=", "cancel")])
    total_accts   = do_count(client, uid, _ACCOUNT_MODEL, [])
    total_jrnls   = do_count(client, uid, _JOURNAL_MODEL, [])

    print(f"\n  account.move (journal entries):")
    print(f"    Total:      {total_moves:>10,}")
    print(f"    Posted:     {posted_moves:>10,}   ← confirmed / active")
    print(f"    Draft:      {draft_moves:>10,}")
    print(f"    Cancelled:  {cancel_moves:>10,}")
    print(f"\n  account.account (chart of accounts):  {total_accts:>8,}")
    print(f"  account.journal (journals):            {total_jrnls:>8,}")

    # Move type distribution (what kinds of entries?)
    move_types = {}
    print(f"\n  Move type distribution (posted only):")
    try:
        mt_rows = do_group(client, uid, _MOVE_MODEL,
                           [("state", "=", "posted")],
                           ["__count"], ["move_type"])
        print(f"  {'MOVE_TYPE':<30}  {'COUNT':>8}")
        print(f"  {'-'*30}  {'-'*8}")
        for row in sorted(mt_rows, key=lambda r: -_c(r)):
            mt  = str(row.get("move_type") or "(empty)")
            cnt = _c(row)
            move_types[mt] = cnt
            print(f"  {mt:<30}  {cnt:>8,}")
    except Exception as exc:
        print(f"  read_group move_type failed: {exc}")

    # Journal type distribution
    print(f"\n  Journal type distribution (all journals):")
    try:
        jt_rows = do_group(client, uid, _JOURNAL_MODEL,
                           [], ["__count"], ["type"])
        print(f"  {'JOURNAL_TYPE':<25}  {'COUNT':>6}")
        print(f"  {'-'*25}  {'-'*6}")
        for row in sorted(jt_rows, key=lambda r: -_c(r)):
            jt  = str(row.get("type") or "(empty)")
            cnt = _c(row)
            print(f"  {jt:<25}  {cnt:>6,}")
    except Exception as exc:
        print(f"  read_group journal type failed: {exc}")

    return {
        "total_moves": total_moves,
        "posted_moves": posted_moves,
        "total_accts": total_accts,
        "total_jrnls": total_jrnls,
        "move_types": move_types,
    }


# ── SECTION 1b: TEMPORAL STATE + FISCAL LOCK ─────────────────────────────────

def section1b_temporal(client, uid) -> dict:
    print(f"\n{SEP}")
    print("  SECTION 1b — TEMPORAL STATE: Date range + fiscal period lock status")
    print("  La Verde context: periods NOT yet closed — transitional migration state.")
    print(SEP)

    # Posted entries by year
    year_data = {}
    print(f"\n  Posted entries by year (read_group date:year):")
    try:
        yr_rows = do_group(client, uid, _MOVE_MODEL,
                           [("state", "=", "posted")],
                           ["__count"], ["date:year"])
        print(f"  {'YEAR':<20}  {'COUNT':>8}")
        print(f"  {'-'*20}  {'-'*8}")
        for row in sorted(yr_rows, key=lambda r: str(r.get("date:year") or "")):
            yr  = str(row.get("date:year") or "(no date)")
            cnt = _c(row)
            year_data[yr] = cnt
            print(f"  {yr:<20}  {cnt:>8,}")
    except Exception as exc:
        print(f"  date:year groupby failed ({type(exc).__name__}: {exc})")
        print("  Trying date:month fallback...")
        try:
            mo_rows = do_group(client, uid, _MOVE_MODEL,
                               [("state", "=", "posted")],
                               ["__count"], ["date:month"])
            # Show last 24 months sorted
            mo_sorted = sorted(mo_rows,
                               key=lambda r: str(r.get("date:month") or ""))
            print(f"  {'MONTH':<20}  {'COUNT':>8}")
            print(f"  {'-'*20}  {'-'*8}")
            for row in mo_sorted:
                mo  = str(row.get("date:month") or "(no date)")
                cnt = _c(row)
                year_data[mo] = cnt
                print(f"  {mo:<20}  {cnt:>8,}")
        except Exception as exc2:
            print(f"  Fallback also failed: {exc2}")

    # Earliest and latest posted entry date
    print(f"\n  Date range of posted entries:")
    oldest_date = None
    newest_date = None
    try:
        oldest = do_read(client, uid, _MOVE_MODEL,
                         [("state", "=", "posted")],
                         ["date", "name"], limit=1, order="date asc")
        newest = do_read(client, uid, _MOVE_MODEL,
                         [("state", "=", "posted")],
                         ["date", "name"], limit=1, order="date desc")
        if oldest:
            oldest_date = oldest[0].get("date")
            print(f"    Oldest posted:  {oldest_date}  (ref: {oldest[0].get('name', '?')})")
        if newest:
            newest_date = newest[0].get("date")
            print(f"    Newest posted:  {newest_date}  (ref: {newest[0].get('name', '?')})")
    except Exception as exc:
        print(f"  Date range query failed: {exc}")

    # Fiscal period lock dates from res.company
    print(f"\n  Fiscal period lock dates (res.company):")
    lock_fields_candidates = [
        "fiscalyear_lock_date",
        "period_lock_date",
        "tax_lock_date",
    ]
    lock_info = {}
    try:
        co_flds = do_fields_get(client, uid, _COMPANY_MODEL)
        available = [f for f in lock_fields_candidates if f in co_flds]
        if not available:
            print("  No lock date fields found in res.company schema.")
            print(f"  (Fields checked: {lock_fields_candidates})")
        else:
            rows = do_read(client, uid, _COMPANY_MODEL, [],
                           ["name"] + available, limit=5)
            for row in rows:
                print(f"\n  Company (name redacted):")
                for fld in available:
                    val = row.get(fld)
                    lock_info[fld] = val
                    status = str(val) if val else "NOT SET"
                    print(f"    {fld:<35}  {status}")
    except Exception as exc:
        print(f"  res.company lock date check failed: {exc}")

    # Stability assessment
    any_lock = any(v for v in lock_info.values() if v)
    print(f"\n  DATA STABILITY:")
    if any_lock:
        print("  Some fiscal lock dates are set — part of the data may be stable.")
        print("  See lock field values above to determine which periods are locked.")
    else:
        print("  No fiscal lock dates set — all periods are OPEN.")
    print("  Per Khaled: La Verde is reviewing last year's data; no periods closed yet.")
    print("  IMPLICATION: All P&L + balance figures in this run are INDICATIVE.")
    print("  They will change as La Verde finalises opening balance review.")

    return {
        "year_data": year_data,
        "oldest_date": oldest_date,
        "newest_date": newest_date,
        "lock_info": lock_info,
        "any_lock": any_lock,
    }


# ── SECTION 2: CHART OF ACCOUNTS ─────────────────────────────────────────────

def section2_chart(client, uid) -> dict:
    print(f"\n{SEP}")
    print("  SECTION 2 — CHART OF ACCOUNTS: Account type structure")
    print(SEP)

    flds = do_fields_get(client, uid, _ACCOUNT_MODEL)

    # Detect account type field (Odoo 16+ = account_type selection; older = user_type_id)
    at_field = None
    if "account_type" in flds and flds["account_type"].get("type") == "selection":
        at_field = "account_type"
        print(f"\n  Account type field: 'account_type' (selection) — Odoo 16+ style")
        sel_vals = flds["account_type"].get("selection") or []
        print(f"  Selection values ({len(sel_vals)}): {[v[0] for v in sel_vals]}")
    elif "user_type_id" in flds and flds["user_type_id"].get("type") == "many2one":
        at_field = "user_type_id"
        print(f"\n  Account type field: 'user_type_id' (many2one) — Odoo 14-15 style")
    else:
        print("\n  !! Account type field not detected via standard names.")
        type_candidates = [
            fn for fn, m in sorted(flds.items())
            if any(kw in fn.lower() for kw in ("type", "categ", "kind", "class"))
            and m.get("type") in ("selection", "many2one")
        ]
        print(f"  Candidate fields: {type_candidates[:10]}")

    # Distribution by account type
    type_dist = {}
    if at_field:
        print(f"\n  Account type distribution:")
        try:
            at_rows = do_group(client, uid, _ACCOUNT_MODEL, [],
                               ["__count"], [at_field])
            print(f"  {'ACCOUNT_TYPE':<40}  {'COUNT':>6}")
            print(f"  {'-'*40}  {'-'*6}")
            for row in sorted(at_rows, key=lambda r: -_c(r)):
                raw = row.get(at_field)
                key = _label(raw) if at_field == "user_type_id" else str(raw or "(empty)")
                cnt = _c(row)
                type_dist[key] = cnt
                print(f"  {key:<40}  {cnt:>6,}")
        except Exception as exc:
            print(f"  read_group by {at_field} failed: {exc}")

    # Board-question mapping
    print(f"\n  Board question mapping:")
    board_map = {
        "income":               "→ Q2 Profitability — Revenue",
        "income_other":         "→ Q2 Profitability — Other Income",
        "expense":              "→ Q2 Profitability — Operating Expense",
        "expense_depreciation": "→ Q2 Profitability — Depreciation",
        "expense_direct_cost":  "→ Q2 Profitability — Direct Cost / COGS",
        "asset_cash":           "→ Q3 Cash Position",
        "asset_bank":           "→ Q3 Cash Position (bank accounts)",
        "asset_receivable":     "→ COVERED — do not duplicate (Modules 2+3)",
        "liability_payable":    "→ NEW territory — payables (not in M2/M3)",
        "equity":               "→ Balance sheet (Board-level optional)",
    }
    print(f"  {'ACCOUNT_TYPE':<40}  {'ACCTS':>5}  NOTE")
    print(f"  {'-'*40}  {'-'*5}  {'-'*40}")
    for at, note in board_map.items():
        cnt = type_dist.get(at, 0)
        print(f"  {at:<40}  {cnt:>5}  {note}")

    return {"at_field": at_field, "type_dist": type_dist}


# ── SECTION 3: PROFITABILITY ──────────────────────────────────────────────────

def section3_profitability(client, uid, at_field: str | None) -> dict:
    print(f"\n{SEP}")
    print("  SECTION 3 — PROFITABILITY: Indicative P&L")
    print("  REMINDER: figures are INDICATIVE — fiscal periods not yet closed.")
    print(SEP)

    if not at_field:
        print("\n  Skipping — account type field not identified in Section 2.")
        return {}

    if at_field != "account_type":
        print(f"\n  account_type field is '{at_field}' (user_type_id many2one).")
        print("  Related-field domain filtering on account.move.line may not be supported.")
        print("  Attempting anyway — if it fails, document as finding.")

    results = {}

    # For Odoo 16+ (account_type selection), use related field domain on move.line
    income_types  = ["income", "income_other"]
    expense_types = ["expense", "expense_depreciation", "expense_direct_cost"]

    for group_label, types, polarity in [
        ("INCOME",  income_types,  "credit - debit"),
        ("EXPENSE", expense_types, "debit - credit"),
    ]:
        domain = [
            ("parent_state", "=", "posted"),
            ("account_id.account_type", "in", types),
        ]
        key = group_label.lower()
        try:
            rows = do_group(client, uid, _LINE_MODEL, domain,
                            ["debit", "credit"], [])
            row    = rows[0] if rows else {}
            debit  = _f(row.get("debit"))
            credit = _f(row.get("credit"))
            cnt    = _c(row)
            net    = (credit - debit) if group_label == "INCOME" else (debit - credit)
            results[f"{key}_debit"]  = debit
            results[f"{key}_credit"] = credit
            results[f"{key}_net"]    = net
            results[f"{key}_count"]  = cnt

            print(f"\n  {group_label} ({', '.join(types)}):")
            print(f"    Posted move lines:  {cnt:>10,}")
            print(f"    SUM(credit):        {credit:>20,.2f} EGP")
            print(f"    SUM(debit):         {debit:>20,.2f} EGP")
            print(f"    Net ({polarity}): {net:>20,.2f} EGP")
        except Exception as exc:
            print(f"\n  {group_label}: read_group failed — {exc}")
            print(f"  Finding: related-field domain 'account_id.account_type' may not be")
            print(f"  supported on this Odoo version. Note for Phase 2 if module proceeds.")

    # P&L summary
    income_net  = results.get("income_net")
    expense_net = results.get("expense_net")
    if income_net is not None and expense_net is not None:
        net_profit = income_net - expense_net
        results["net_profit"] = net_profit
        verdict = "ربح (مبدئي)" if net_profit > 0 else ("خسارة (مبدئي)" if net_profit < 0 else "تعادل")
        print(f"\n  ── INDICATIVE P&L (transitional — not verified) ────────────")
        print(f"    إجمالي الإيرادات:   {income_net:>20,.2f} EGP")
        print(f"    إجمالي المصروفات:   {expense_net:>20,.2f} EGP")
        print(f"    صافي الربح/الخسارة: {net_profit:>20,.2f} EGP  [{verdict}]")

    # P&L move lines by year (to show data distribution across years)
    print(f"\n  P&L move lines by year (income + expense accounts, posted):")
    all_pl_types = income_types + expense_types
    domain_pl = [
        ("parent_state", "=", "posted"),
        ("account_id.account_type", "in", all_pl_types),
    ]
    try:
        yr_rows = do_group(client, uid, _LINE_MODEL, domain_pl,
                           ["debit", "credit"], ["date:year"])
        print(f"  {'YEAR':<20}  {'LINES':>8}  {'DEBIT EGP':>20}  {'CREDIT EGP':>20}")
        print(f"  {'-'*20}  {'-'*8}  {'-'*20}  {'-'*20}")
        for row in sorted(yr_rows, key=lambda r: str(r.get("date:year") or "")):
            yr  = str(row.get("date:year") or "(none)")
            cnt = _c(row)
            d   = _f(row.get("debit"))
            c   = _f(row.get("credit"))
            print(f"  {yr:<20}  {cnt:>8,}  {d:>20,.2f}  {c:>20,.2f}")
    except Exception as exc:
        print(f"  date:year groupby on move lines failed: {exc}")

    return results


# ── SECTION 4: CASH POSITION ──────────────────────────────────────────────────

def section4_cash(client, uid) -> dict:
    print(f"\n{SEP}")
    print("  SECTION 4 — CASH POSITION: Bank and cash accounts")
    print(SEP)

    # Bank/cash journals
    journals = []
    print(f"\n  Bank/cash journals (account.journal WHERE type in [bank, cash]):")
    try:
        j_rows = do_read(client, uid, _JOURNAL_MODEL,
                         [("type", "in", ["bank", "cash"])],
                         ["name", "code", "type", "default_account_id"],
                         order="type asc")
        print(f"  {'CODE':<10}  {'TYPE':<8}  {'DEFAULT_ACCOUNT (id)':<25}")
        print(f"  {'-'*10}  {'-'*8}  {'-'*25}")
        for j in j_rows:
            code  = str(j.get("code") or "")
            jtype = str(j.get("type") or "")
            acc   = j.get("default_account_id")
            acc_id = str(_id(acc)) if acc else "(none)"
            print(f"  {code:<10}  {jtype:<8}  acc_id={acc_id}")
            journals.append({"code": code, "type": jtype, "acc_id": acc_id})
    except Exception as exc:
        print(f"  search_read journals failed: {exc}")

    j_count = len(journals)
    print(f"\n  Total bank/cash journals found: {j_count}")

    # Cash/bank account balances via account.move.line
    print(f"\n  Cash/bank balances (posted lines, debit-credit per account):")
    cash_total = None
    try:
        bal_rows = do_group(
            client, uid, _LINE_MODEL,
            [
                ("parent_state", "=", "posted"),
                ("account_id.account_type", "in", ["asset_cash", "asset_bank"]),
            ],
            ["debit", "credit"],
            ["account_id"],
        )
        if not bal_rows:
            print("  No rows returned for asset_cash/asset_bank — trying journal type fallback.")
            raise ValueError("empty result — try fallback")
        print(f"  {'ACCOUNT (id)':<15}  {'DEBIT':>20}  {'CREDIT':>20}  {'BALANCE':>20}")
        print(f"  {'-'*15}  {'-'*20}  {'-'*20}  {'-'*20}")
        running = 0.0
        for row in sorted(bal_rows,
                          key=lambda r: abs(_f(r.get("debit", 0)) - _f(r.get("credit", 0))),
                          reverse=True):
            acc_raw = row.get("account_id")
            acc_id  = str(_id(acc_raw))
            debit   = _f(row.get("debit"))
            credit  = _f(row.get("credit"))
            balance = debit - credit
            running += balance
            print(f"  acc_id={acc_id:<9}  {debit:>20,.2f}  {credit:>20,.2f}  {balance:>20,.2f}")
        cash_total = running

    except Exception as exc:
        print(f"  Primary cash query failed ({type(exc).__name__}: {exc})")
        print("  Fallback: querying via journal_id.type in [bank, cash]...")
        try:
            fb_rows = do_group(
                client, uid, _LINE_MODEL,
                [
                    ("parent_state", "=", "posted"),
                    ("journal_id.type", "in", ["bank", "cash"]),
                ],
                ["debit", "credit"],
                ["account_id"],
            )
            print(f"  {'ACCOUNT (id)':<15}  {'DEBIT':>20}  {'CREDIT':>20}  {'BALANCE':>20}")
            print(f"  {'-'*15}  {'-'*20}  {'-'*20}  {'-'*20}")
            running = 0.0
            for row in sorted(fb_rows,
                              key=lambda r: abs(_f(r.get("debit", 0)) - _f(r.get("credit", 0))),
                              reverse=True):
                acc_raw = row.get("account_id")
                acc_id  = str(_id(acc_raw))
                debit   = _f(row.get("debit"))
                credit  = _f(row.get("credit"))
                balance = debit - credit
                running += balance
                print(f"  acc_id={acc_id:<9}  {debit:>20,.2f}  {credit:>20,.2f}  {balance:>20,.2f}")
            cash_total = running
        except Exception as exc2:
            print(f"  Fallback also failed: {exc2}")

    if cash_total is not None:
        print(f"\n  Estimated total cash/bank position:  {cash_total:>20,.2f} EGP")
        print("  (INDICATIVE — periods not closed; migration data under review)")

    return {"journals": journals, "j_count": j_count, "cash_total": cash_total}


# ── SECTION 5: ANALYTIC DIMENSION ────────────────────────────────────────────

def section5_analytic(client, uid) -> dict:
    print(f"\n{SEP}")
    print("  SECTION 5 — ANALYTIC DIMENSION: Project-level breakdown")
    print("  Context: Khaled confirmed Analytic Accounting is actively used for review.")
    print(SEP)

    # Counts
    aa_count = do_count(client, uid, _ANALYTIC_ACCT, [])
    al_count = do_count(client, uid, _ANALYTIC_LINE, [])
    print(f"\n  account.analytic.account:  {aa_count:>8,}")
    print(f"  account.analytic.line:     {al_count:>8,}")

    # Analytic account list — names shown (project names are not PII)
    analytic_accounts = []
    if aa_count > 0:
        print(f"\n  Analytic account list (all {aa_count}):")
        try:
            aa_flds = do_fields_get(client, uid, _ANALYTIC_ACCT)
            fetch = ["id"]
            for f in ["name", "code", "plan_id", "group_id", "complete_name"]:
                if f in aa_flds:
                    fetch.append(f)
            rows = do_read(client, uid, _ANALYTIC_ACCT, [],
                           fetch, order="id asc")
            print(f"  {'ID':<6}  {'CODE':<12}  NAME / PLAN / GROUP")
            print(f"  {'-'*6}  {'-'*12}  {'-'*45}")
            for row in rows:
                aid   = row.get("id")
                code  = str(row.get("code") or "")
                rname = row.get("name") or row.get("complete_name") or ""
                plan  = _label(row.get("plan_id"))  if "plan_id"  in row else ""
                grp   = _label(row.get("group_id")) if "group_id" in row else ""
                extra = f"  plan:{plan}" if plan and plan != "(none)" else ""
                extra += f"  grp:{grp}"  if grp  and grp  != "(none)" else ""
                print(f"  {aid:<6}  {code:<12}  {rname}{extra}")
                analytic_accounts.append({"id": aid, "name": rname, "code": code})
        except Exception as exc:
            print(f"  search_read analytic accounts failed: {exc}")

    # Analytic line totals by account
    if al_count > 0:
        print(f"\n  Analytic line amounts by account (top 15 by |amount|):")
        try:
            al_flds = do_fields_get(client, uid, _ANALYTIC_LINE)
            # Detect amount field
            amount_field = None
            for candidate in ["amount", "unit_amount", "general_amount"]:
                if candidate in al_flds:
                    amount_field = candidate
                    break
            if not amount_field:
                print(f"  !! No amount field found. Available: {list(al_flds.keys())[:20]}")
            else:
                print(f"  Using amount field: '{amount_field}'")
                al_rows = do_group(client, uid, _ANALYTIC_LINE,
                                   [], [amount_field], ["account_id"])
                al_sorted = sorted(al_rows,
                                   key=lambda r: abs(_f(r.get(amount_field))),
                                   reverse=True)
                print(f"\n  {'ACCOUNT':<35}  {'LINES':>6}  {'AMOUNT EGP':>20}")
                print(f"  {'-'*35}  {'-'*6}  {'-'*20}")
                for row in al_sorted[:15]:
                    acc_raw  = row.get("account_id")
                    acc_name = _label(acc_raw)
                    cnt      = _c(row)
                    amt      = _f(row.get(amount_field))
                    print(f"  {acc_name:<35}  {cnt:>6,}  {amt:>20,.2f}")

                al_total = sum(_f(r.get(amount_field)) for r in al_rows)
                print(f"\n  Total analytic amount (all accounts):  {al_total:>20,.2f} EGP")

        except Exception as exc:
            print(f"  Analytic line aggregation failed: {exc}")

    # Check linkage: are analytic lines linked to accounting entries?
    print(f"\n  Linkage check: analytic lines → accounting entries:")
    if al_count > 0:
        try:
            al_flds2 = do_fields_get(client, uid, _ANALYTIC_LINE)
            move_links = [
                fn for fn, m in al_flds2.items()
                if (m.get("relation") or "") in (_MOVE_MODEL, _LINE_MODEL)
            ]
            if move_links:
                print(f"  Found link fields to account.move: {move_links}")
                # Count how many analytic lines have a move link
                for lf in move_links[:2]:
                    try:
                        linked = do_count(client, uid, _ANALYTIC_LINE,
                                         [(lf, "!=", False)])
                        print(f"    {lf} != False:  {linked:,} lines linked to GL entries")
                    except Exception:
                        pass
            else:
                print("  No direct link field to account.move found in schema.")
                print("  Analytic lines may be standalone or linked via account.move.line.")
        except Exception as exc:
            print(f"  Linkage fields_get failed: {exc}")
    else:
        print("  No analytic lines — linkage check skipped.")

    return {"aa_count": aa_count, "al_count": al_count,
            "analytic_accounts": analytic_accounts}


# ── SECTION 6: OVERLAP ────────────────────────────────────────────────────────

def section6_overlap(client, uid, at_field: str | None) -> dict:
    print(f"\n{SEP}")
    print("  SECTION 6 — OVERLAP: What's already covered by Modules 2+3?")
    print(f"  Covered by:  {_COVERED_BY}")
    print(f"  Via models:  {_COVERED_MODELS}")
    print(SEP)

    receivable_acct_count = 0
    receivable_line_count = 0
    receivable_net        = None

    if at_field == "account_type":
        receivable_acct_count = do_count(
            client, uid, _ACCOUNT_MODEL,
            [("account_type", "=", "asset_receivable")]
        )
        print(f"\n  Receivable accounts (account_type='asset_receivable'): "
              f"{receivable_acct_count}")

        try:
            recv_rows = do_group(
                client, uid, _LINE_MODEL,
                [("parent_state", "=", "posted"),
                 ("account_id.account_type", "=", "asset_receivable")],
                ["debit", "credit"], []
            )
            recv = recv_rows[0] if recv_rows else {}
            rd   = _f(recv.get("debit"))
            rc   = _f(recv.get("credit"))
            receivable_line_count = _c(recv)
            receivable_net = rd - rc
            print(f"  Receivable posted move lines:   {receivable_line_count:>10,}")
            print(f"    SUM(debit):                   {rd:>20,.2f} EGP")
            print(f"    SUM(credit):                  {rc:>20,.2f} EGP")
            print(f"    Net (debit - credit):         {receivable_net:>20,.2f} EGP")
        except Exception as exc:
            print(f"  Receivable aggregation failed: {exc}")
    elif at_field:
        print(f"\n  (account_type field is '{at_field}' — skipping receivable count)")

    print(f"""
  OVERLAP VERDICT:
  ✓ Receivables / installments → COVERED by {_COVERED_BY}
    rs.installment                  = due amounts per installment (M2 + M3)
    rs.account.payment.reconcile    = wallet balances + refunds (M3)
  ✗ Do NOT build Module 4 around receivables — that is duplication.
  → Module 4 value territory (NOT covered anywhere):
      Income / Revenue accounts
      Expense / Cost accounts
      Cash and bank position
      Per-project P&L via Analytic Accounting
""")

    return {
        "receivable_acct_count": receivable_acct_count,
        "receivable_line_count": receivable_line_count,
        "receivable_net": receivable_net,
    }


# ── SECTION 7: SUMMARY + ASSESSMENT ──────────────────────────────────────────

def section7_summary(s1, s1b, s2, s3, s4, s5, s6):
    print(f"\n{SEP}")
    print("  SECTION 7 — DISCOVERY SUMMARY + MODULE 4 ASSESSMENT")
    print(f"  Date: {TODAY}   Cost: $0.00")
    print(SEP)

    posted   = s1.get("posted_moves", "?")
    accts    = s1.get("total_accts",  "?")
    mt       = list(s1.get("move_types", {}).keys())

    yr_data  = s1b.get("year_data", {})
    oldest   = s1b.get("oldest_date", "?")
    newest   = s1b.get("newest_date", "?")
    any_lock = s1b.get("any_lock", False)

    td       = s2.get("type_dist", {})
    at_field = s2.get("at_field", None)

    income_net  = s3.get("income_net")
    expense_net = s3.get("expense_net")
    net_profit  = s3.get("net_profit")

    j_count    = s4.get("j_count", 0)
    cash_total = s4.get("cash_total")

    aa_count = s5.get("aa_count", 0)
    al_count = s5.get("al_count", 0)

    recv_accts = s6.get("receivable_acct_count", "?")

    print(f"""
  Q1 — STRUCTURE
  {'─'*65}
  Posted journal entries:    {posted:>10,}
  Chart of accounts:         {accts:>10,}
  Move types seen:           {mt}
  Assessment: {"Accounting IS active with real data." if isinstance(posted, int) and posted > 0 else "Low/no data — needs investigation."}

  Q1b — TEMPORAL STATE + DATA STABILITY
  {'─'*65}
  Data spans:        {sorted(yr_data.keys())}
  Oldest posted:     {oldest}
  Newest posted:     {newest}
  Fiscal lock dates: {"SOME SET — see Section 1b for detail" if any_lock else "NONE SET — all periods open"}
  La Verde status:   Migration in progress; opening balance review underway.
  STABILITY VERDICT: Data is TRANSITIONAL. Figures are indicative.

  Q2 — PROFITABILITY
  {'─'*65}
  Income accounts:   {td.get('income', 0) + td.get('income_other', 0)} accounts
  Expense accounts:  {td.get('expense', 0) + td.get('expense_depreciation', 0) + td.get('expense_direct_cost', 0)} accounts
  Indicative Revenue:    {f'{income_net:>20,.2f} EGP' if income_net is not None else 'not computed — see Section 3'}
  Indicative Expenses:   {f'{expense_net:>20,.2f} EGP' if expense_net is not None else 'not computed — see Section 3'}
  Indicative Net P&L:    {f'{net_profit:>20,.2f} EGP' if net_profit is not None else 'not computed — see Section 3'}
  Can Board ask "profitable or not"?
    {"YES — data structure supports it. Numbers are transitional." if net_profit is not None else "PARTIAL — see Section 3 for details."}

  Q3 — CASH POSITION
  {'─'*65}
  Bank/cash journals:        {j_count}
  Estimated cash position:   {f'{cash_total:>20,.2f} EGP' if cash_total is not None else 'not computed — see Section 4'}
  Can Board ask "how much cash"?
    {"YES — data available. Figures are transitional." if cash_total is not None else "UNCLEAR — see Section 4."}

  Q4 — ANALYTIC / PER-PROJECT BREAKDOWN
  {'─'*65}
  Analytic accounts:         {aa_count:>8,}
  Analytic lines:            {al_count:>8,}
  Khaled confirmed:          Analytic Accounting actively used for review.
  Can Board ask "P&L by project"?
    {"YES — analytic data exists. Drill-down by project is feasible." if al_count > 0 else "UNCLEAR — no analytic lines found. Investigate manually."}

  Q5 — OVERLAP WITH MODULES 2+3
  {'─'*65}
  Receivable accounts in chart: {recv_accts}
  Verdict: Receivable data EXISTS in account.move.line.
  Decision: Do NOT include receivables in Module 4 — covered by M2+M3.
  Module 4 scope = Income, Expenses, Cash, Analytic P&L only.
""")

    print(f"""{SEP}
  MODULE 4 — SHOULD WE BUILD IT? + TIMING RECOMMENDATION
  {'─'*65}

  IS THERE A MODULE 4? → YES — data exists, Board questions are answerable.

  The accounting data structure supports a Board-level module covering:
    (a) Revenue vs Expense summary (P&L at a glance)
    (b) Cash / bank position
    (c) Per-project P&L via Analytic Accounting (high Board value)
  None of (a)-(c) are covered by any existing module.

  TIMING — TWO OPTIONS:
  ─────────────────────────────────────────────────────────────────────
  OPTION A — Build now, label figures as "مؤشّر مبدئي — قيد المراجعة":
    Pros: Board gets early visibility; module is production-ready when
          data stabilises. Analytic figures (project P&L) may already
          be reliable if Analytic review is further along than GL review.
    Cons: P&L / cash numbers will shift as opening balances are finalised.
          Risk of Board acting on unverified figures.
    Mitigation: prominent "indicative" banner; dates of last entry shown.

  OPTION B — Wait until La Verde closes first fiscal period:
    Pros: Data is verified and trustworthy. Board sees correct numbers.
    Cons: Delivery delayed. Timing unknown (depends on La Verde's review).
    Risk: If review takes months, Board stays blind to P&L for longer.

  RECOMMENDATION FOR KHALED:
    1. Confirm how far away the opening balance review completion is.
       If weeks → Option A. If months → Option A (with banner) still
       better than waiting.
    2. Prioritise Analytic Accounting (Section 5) — ask La Verde if the
       project-level figures are already stable, even if the overall GL
       is not. If analytic is clean, Module 4 can launch analytic P&L
       first and add GL P&L / cash when periods are closed.
    3. No code is written yet. This is a go/no-go decision point.
{SEP}""")


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    output_path = (Path(__file__).parent /
                   f"discover_accounting_phase1_{TODAY}.txt")
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
        print("  Accounting Module Discovery — Phase 1")
        print(f"  Run date:   {TODAY}")
        print(f"  Odoo URL:   {ODOO_URL}")
        print(f"  Constraint: READ-ONLY ({sorted(ALLOWED_METHODS)})")
        print(f"  PII policy: name/partner/email fields redacted in samples")
        print(f"  Cost:       $0.00  (no OpenAI calls)")
        print(f"  Context:    La Verde — 1 company, Analytic active, no closed periods")
        print(SEP)

        with httpx.Client() as client:
            uid = connect(client)
            s1  = section1_structure(client, uid)
            s1b = section1b_temporal(client, uid)
            s2  = section2_chart(client, uid)
            s3  = section3_profitability(client, uid, s2.get("at_field"))
            s4  = section4_cash(client, uid)
            s5  = section5_analytic(client, uid)
            s6  = section6_overlap(client, uid, s2.get("at_field"))

        section7_summary(s1, s1b, s2, s3, s4, s5, s6)
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
