"""
discover_module3_phase1.py — M3-S1: Customer Accounts Pre-Implementation Discovery
READ-ONLY: search_read, search_count, read_group, fields_get ONLY.
No create/write/unlink. No OpenAI. AI cost = $0.00.

Answers:
  Sec 1 — KPI A baseline: SUM(due_amount) state='post', groupby partner_id.
           Null-partner check vs flat aggregate.
  Sec 2 — KPI B baseline: Late domain, groupby partner_id, top 20 + concentration.
           R1a: groupby total vs flat (domain integrity — must be exact).
           R1b: PATH A vs total_due_amount identity (kpi_service.py tiers).
           Conditional: search_count partner_id=False on Late domain if R1a != 0.
  Sec 3 — KPI C baseline: SUM(residual_amount) where state='post' + residual>0.
  Sec 4 — Refunds: state='post' + amount<0 — total, count, null-partner count.
  Sec 5 — OQ2: rs.account.payment.reconcile.line record count + sample if > 0.
  Sec 6 — OQ4: rs.account.payment.reconcile.request state distribution + structure.

Run from any directory:
    python scripts/discover_module3_phase1.py
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

_INSTALLMENT_MODEL = "rs.installment"
_RECONCILE_MODEL   = "rs.account.payment.reconcile"
_RECONCILE_LINE    = "rs.account.payment.reconcile.line"
_RECONCILE_REQUEST = "rs.account.payment.reconcile.request"

LATE_DOMAIN = [
    ("state",         "=",  "post"),
    ("payment_state", "in", ["unpaid", "partial"]),
    ("date",          "<",  TODAY),
]

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


def do_search_count(client, uid, model, domain):
    return execute(client, uid, model, "search_count", [domain])


def do_search_read(client, uid, model, domain, fields, limit=None, order=None):
    kw = {"fields": fields}
    if limit is not None:
        kw["limit"] = limit
    if order:
        kw["order"] = order
    return execute(client, uid, model, "search_read", [domain], kw)


def do_read_group(client, uid, model, domain, agg_fields, groupby):
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

def _egp(val) -> float:
    return float(val) if val else 0.0


def _cnt(row) -> int:
    return int(row.get("__count") or 0)


def _sanitize(record: dict) -> dict:
    out = {}
    for k, v in record.items():
        if any(frag in k.lower() for frag in _PII_FRAGS):
            out[k] = "[REDACTED]"
        else:
            out[k] = v
    return out


def _partner_id_int(raw) -> int:
    if isinstance(raw, (list, tuple)) and raw:
        return int(raw[0])
    return int(raw) if raw else 0


# ── SECTION 1: KPI A — Total Customer Receivables ─────────────────────────────

def section_kpia(client, uid) -> dict:
    print(f"\n{SEP}")
    print("  SECTION 1 — KPI A: إجمالي المستحق على العملاء")
    print(f"  Model: {_INSTALLMENT_MODEL}")
    print(f"  Domain: [('state','=','post')]")
    print(f"  Measure: due_amount | Groupby: partner_id")
    print(SEP)

    # A1 — grouped by partner_id
    a1_rows = do_read_group(client, uid, _INSTALLMENT_MODEL,
                            [("state", "=", "post")],
                            ["due_amount"], ["partner_id"])

    a1_total    = sum(_egp(r.get("due_amount")) for r in a1_rows)
    a1_partners = len(a1_rows)

    print(f"\n  A1 — groupby partner_id:")
    print(f"    Distinct partners (groups):  {a1_partners:>10,}")
    print(f"    SUM(due_amount):             {a1_total:>20,.2f} EGP")

    # A2 — flat aggregate (no groupby) for null-partner integrity check
    a2_rows = do_read_group(client, uid, _INSTALLMENT_MODEL,
                            [("state", "=", "post")],
                            ["due_amount"], [])
    a2_row   = a2_rows[0] if a2_rows else {}
    a2_total = _egp(a2_row.get("due_amount"))
    a2_count = _cnt(a2_row)

    print(f"\n  A2 — flat aggregate (no groupby):")
    print(f"    Total records (state='post'): {a2_count:>10,}")
    print(f"    SUM(due_amount):              {a2_total:>20,.2f} EGP")

    delta_a = abs(a1_total - a2_total)
    print(f"\n  NULL-PARTNER CHECK:")
    print(f"    A1 grouped sum:   {a1_total:>20,.2f} EGP")
    print(f"    A2 flat sum:      {a2_total:>20,.2f} EGP")
    print(f"    Delta:            {delta_a:>20,.2f} EGP  ", end="")

    null_install_count = None
    if delta_a < 0.01:
        print("→ MATCH ✓  No null-partner installments affecting KPI A.")
    else:
        print(f"!! MISMATCH")
        null_install_count = do_search_count(
            client, uid, _INSTALLMENT_MODEL,
            [("state", "=", "post"), ("partner_id", "=", False)]
        )
        print(f"\n  FINDING A: {null_install_count} installment(s) state='post' with partner_id=False")
        print(f"  These are EXCLUDED from groupby total.")
        print(f"  KPI A grouped value ({a1_total:,.2f}) understates reality by {delta_a:,.2f} EGP.")

    print(f"\n  ── BASELINE KPI A ─────────────────────────────────────────")
    print(f"    إجمالي المستحق:   {a1_total:>20,.2f} EGP")
    print(f"    عدد العملاء:      {a1_partners:>10,}")

    return {
        "a1_total": a1_total,
        "a1_partners": a1_partners,
        "a2_total": a2_total,
        "null_install_count": null_install_count,
    }


# ── SECTION 2: KPI B — Top Overdue Customers ──────────────────────────────────

def section_kpib(client, uid) -> dict:
    print(f"\n{SEP}")
    print("  SECTION 2 — KPI B: أعلى العملاء تأخراً")
    print(f"  Model: {_INSTALLMENT_MODEL}")
    print(f"  Domain: Late (state='post' + payment_state in [unpaid,partial] + date<{TODAY})")
    print(f"  Measure: due_amount | Groupby: partner_id")
    print(SEP)

    # B1 — all overdue partners (no limit — needed for full total + concentration)
    b1_rows = do_read_group(client, uid, _INSTALLMENT_MODEL,
                            LATE_DOMAIN, ["due_amount"], ["partner_id"])

    b1_total    = sum(_egp(r.get("due_amount")) for r in b1_rows)
    b1_partners = len(b1_rows)

    # Sort by due_amount descending in Python
    b1_sorted = sorted(b1_rows,
                       key=lambda r: _egp(r.get("due_amount")),
                       reverse=True)

    print(f"\n  B1 — groupby partner_id (all partners, no limit):")
    print(f"    Overdue partners (groups):   {b1_partners:>10,}")
    print(f"    SUM(due_amount):             {b1_total:>20,.2f} EGP")

    # Top 20 — partner names redacted, partner_id integer shown
    print(f"\n  Top 20 overdue customers (names redacted):")
    print(f"  {'RANK':<5}  {'DUE_AMOUNT EGP':>20}  {'INSTALLMENTS':>12}  PARTNER_ID")
    print(f"  {'-'*5}  {'-'*20}  {'-'*12}  {'-'*20}")
    for i, row in enumerate(b1_sorted[:20], 1):
        amt  = _egp(row.get("due_amount"))
        cnt  = _cnt(row)
        pid  = _partner_id_int(row.get("partner_id"))
        print(f"  {i:<5}  {amt:>20,.2f}  {cnt:>12,}  [REDACTED id={pid}]")

    # Concentration ratio — top 10
    top10_sum     = sum(_egp(r.get("due_amount")) for r in b1_sorted[:10])
    concentration = (top10_sum / b1_total * 100) if b1_total > 0 else 0.0

    print(f"\n  CONCENTRATION RATIO:")
    print(f"    Top 10 sum:   {top10_sum:>20,.2f} EGP")
    print(f"    Grand total:  {b1_total:>20,.2f} EGP")
    print(f"    أعلى 10 عملاء = {concentration:.1f}% من إجمالي التأخير")

    # B2 — flat aggregate for R1a + R1b
    b2_rows = do_read_group(
        client, uid, _INSTALLMENT_MODEL, LATE_DOMAIN,
        ["due_amount", "amount", "x_studio_actual_paid_amount", "total_due_amount"],
        []
    )
    b2_row    = b2_rows[0] if b2_rows else {}
    b2_due    = _egp(b2_row.get("due_amount"))
    b2_amount = _egp(b2_row.get("amount"))
    b2_actual = _egp(b2_row.get("x_studio_actual_paid_amount"))
    b2_tda    = _egp(b2_row.get("total_due_amount"))
    b2_path_a = b2_amount - b2_actual
    b2_count  = _cnt(b2_row)

    print(f"\n  B2 — flat aggregate (same Late domain, no groupby):")
    print(f"    Total records matched:        {b2_count:>10,}")
    print(f"    SUM(due_amount):              {b2_due:>20,.2f} EGP")
    print(f"    SUM(amount):                  {b2_amount:>20,.2f} EGP")
    print(f"    SUM(x_studio_actual_paid):    {b2_actual:>20,.2f} EGP")
    print(f"    PATH A (amount - actual):     {b2_path_a:>20,.2f} EGP")
    print(f"    SUM(total_due_amount):        {b2_tda:>20,.2f} EGP")

    # ── R1a: domain integrity (groupby total must equal flat total exactly) ──
    delta_r1a       = abs(b1_total - b2_due)
    null_late_count = None

    print(f"\n  R1a — DOMAIN INTEGRITY (groupby partner sum vs flat sum):")
    print(f"    B1 Python sum (grouped):  {b1_total:>20,.2f} EGP")
    print(f"    B2 flat total:            {b2_due:>20,.2f} EGP")
    print(f"    Delta:                    {delta_r1a:>20,.2f} EGP  ", end="")

    if delta_r1a < 0.01:
        print("→ EXACT MATCH ✓")
        print("  Late domain applies with equal accuracy to partner-grouped and flat queries.")
        print("  R1 (MODULE_3_PLAN.md §6): domain integrity CONFIRMED.")
    else:
        print(f"!! MISMATCH")
        # Conditional RPC: how many late records have partner_id=False?
        null_late_count = do_search_count(
            client, uid, _INSTALLMENT_MODEL,
            LATE_DOMAIN + [("partner_id", "=", False)]
        )
        print(f"\n  FINDING R1a: {null_late_count} late installment(s) with partner_id=False.")
        print(f"  These records ARE in the flat total but are NOT in any partner group.")
        print(f"  The grouped total ({b1_total:,.2f}) understates the true late figure by {delta_r1a:,.2f} EGP.")
        print(f"  KPI B backend must decide: exclude null-partner records (current groupby behaviour)")
        print(f"  or add a separate 'unknown partner' row.")

    # ── R1b: KPI 2 identity — PATH A vs total_due_amount (kpi_service.py tiers) ──
    delta_r1b = abs(b2_path_a - b2_tda)

    print(f"\n  R1b — KPI 2 IDENTITY (PATH A vs total_due_amount):")
    print(f"    PATH A = amount - x_studio_actual_paid:  {b2_path_a:>20,.2f} EGP")
    print(f"    SUM(total_due_amount):                   {b2_tda:>20,.2f} EGP")
    print(f"    Delta:                                   {delta_r1b:>20,.2f} EGP  ", end="")

    if delta_r1b < 1.0:
        print("→ IDENTITY HOLDS ✓  (< 1 EGP — kpi_service.py tier: no flag)")
    elif delta_r1b < 1000.0:
        print(f"→ micro-drift ({delta_r1b:,.2f} EGP)  (kpi_service.py tier: INFO log, acceptable)")
    else:
        print(f"!! IDENTITY MISMATCH  (kpi_service.py tier: WARNING — kpi2_identity_mismatch)")
        print(f"  FINDING R1b: delta={delta_r1b:,.2f} EGP exceeds 1,000 EGP threshold.")
        print(f"  Document as finding. Do NOT adjust values to reconcile.")

    print(f"\n  ── BASELINE KPI B ─────────────────────────────────────────")
    print(f"    إجمالي التأخير:        {b1_total:>20,.2f} EGP")
    print(f"    عدد العملاء المتأخرين: {b1_partners:>10,}")
    print(f"    أعلى 10 عملاء =        {concentration:.1f}% من الإجمالي")

    return {
        "b1_total":        b1_total,
        "b1_partners":     b1_partners,
        "b2_due":          b2_due,
        "b2_path_a":       b2_path_a,
        "b2_tda":          b2_tda,
        "concentration":   concentration,
        "delta_r1a":       delta_r1a,
        "delta_r1b":       delta_r1b,
        "null_late_count": null_late_count,
    }


# ── SECTION 3: KPI C — Unallocated Wallet Balance ─────────────────────────────

def section_kpic(client, uid) -> dict:
    print(f"\n{SEP}")
    print("  SECTION 3 — KPI C: رصيد المحفظة غير المخصص")
    print(f"  Model: {_RECONCILE_MODEL}")
    print(f"  Domain: [('state','=','post'), ('residual_amount','>',0)]")
    print(f"  Measure: residual_amount | Groupby: partner_id")
    print(SEP)

    c1_domain = [("state", "=", "post"), ("residual_amount", ">", 0)]
    c1_rows   = do_read_group(client, uid, _RECONCILE_MODEL,
                              c1_domain, ["residual_amount"], ["partner_id"])

    c1_total     = sum(_egp(r.get("residual_amount")) for r in c1_rows)
    c1_partners  = len(c1_rows)
    c1_rec_count = sum(_cnt(r) for r in c1_rows)

    print(f"\n  C1 — groupby partner_id (residual_amount > 0 only):")
    print(f"    Reconcile records matched:       {c1_rec_count:>10,}")
    print(f"    Distinct partners with balance:  {c1_partners:>10,}")
    print(f"    SUM(residual_amount):            {c1_total:>20,.2f} EGP")

    print(f"\n  ── BASELINE KPI C ─────────────────────────────────────────")
    print(f"    إجمالي المحفظة غير المخصص:  {c1_total:>20,.2f} EGP")
    print(f"    عدد العملاء بالرصيد:         {c1_partners:>10,}")

    return {
        "c1_total":     c1_total,
        "c1_partners":  c1_partners,
        "c1_rec_count": c1_rec_count,
    }


# ── SECTION 4: Refunds / الاستردادات ──────────────────────────────────────────

def section_refunds(client, uid) -> dict:
    print(f"\n{SEP}")
    print("  SECTION 4 — الاستردادات / Refunds")
    print(f"  Model: {_RECONCILE_MODEL}")
    print(f"  Domain: [('state','=','post'), ('amount','<',0)]")
    print(SEP)

    ref_domain = [("state", "=", "post"), ("amount", "<", 0)]

    # Flat aggregate
    df_rows = do_read_group(client, uid, _RECONCILE_MODEL,
                            ref_domain, ["amount"], [])
    df_row       = df_rows[0] if df_rows else {}
    df_total     = _egp(df_row.get("amount"))
    df_rec_count = _cnt(df_row)

    print(f"\n  Flat aggregate (all refunds):")
    print(f"    Records (amount < 0):  {df_rec_count:>10,}")
    print(f"    SUM(amount):           {df_total:>20,.2f} EGP  (negative = outflow to customers)")

    # Null partner count
    d2_count = do_search_count(client, uid, _RECONCILE_MODEL,
                               ref_domain + [("partner_id", "=", False)])

    pct_unknown = (d2_count / df_rec_count * 100) if df_rec_count > 0 else 0.0

    print(f"\n  Null-partner check (عميل غير معروف):")
    print(f"    Refunds with partner_id=False:  {d2_count:>10,}")
    if df_rec_count > 0:
        print(f"    % of total refund records:      {pct_unknown:>10.1f}%")

    print(f"\n  ── BASELINE الاستردادات ──────────────────────────────────")
    print(f"    إجمالي:         {df_total:>20,.2f} EGP")
    print(f"    عدد السجلات:    {df_rec_count:>10,}")
    print(f"    عميل غير معروف: {d2_count:>10,}  ({pct_unknown:.1f}%)")

    return {
        "df_total":     df_total,
        "df_rec_count": df_rec_count,
        "d2_null":      d2_count,
    }


# ── SECTION 5: OQ2 — rs.account.payment.reconcile.line ───────────────────────

def section_oq2(client, uid) -> dict:
    print(f"\n{SEP}")
    print("  SECTION 5 — OQ2: Reconcile Sub-lines — When Is It Populated?")
    print(f"  Model: {_RECONCILE_LINE}")
    print(SEP)

    total = do_search_count(client, uid, _RECONCILE_LINE, [])
    print(f"\n  search_count([]): {total} record(s)")

    if total == 0:
        print("\n  OQ2 STATUS: Still 0 records (unchanged from Phase 3, 2026-05-22).")
        print("  Sub-lines remain unused. Per-installment application history is NOT")
        print("  recorded on the wallet side. Impact: Low — does not affect KPI A/B/C.")
    else:
        print(f"\n  !! OQ2 NEW DATA: {total} record(s) found (was 0 at Phase 3).")
        print("  Fetching sample (limit=3, sanitized)...")
        try:
            rows = do_search_read(
                client, uid, _RECONCILE_LINE, [],
                ["id", "create_date", "state", "amount", "reconcile_id"],
                limit=3, order="id asc",
            )
            for i, row in enumerate(rows, 1):
                safe = _sanitize(row)
                print(f"\n  Record {i}:")
                for k, v in sorted(safe.items()):
                    print(f"    {k:<40}  {v}")
        except Exception as exc:
            print(f"  search_read failed: {exc}")

    return {"oq2_total": total}


# ── SECTION 6: OQ4 — rs.account.payment.reconcile.request ────────────────────

def section_oq4(client, uid) -> dict:
    print(f"\n{SEP}")
    print("  SECTION 6 — OQ4: Reconcile Request — Workflow Role")
    print(f"  Model: {_RECONCILE_REQUEST}")
    print(SEP)

    # OQ4-1: state distribution
    state_rows    = do_read_group(client, uid, _RECONCILE_REQUEST,
                                  [], ["id"], ["state"])
    total_requests = sum(_cnt(r) for r in state_rows)

    print(f"\n  OQ4-1 — State distribution (total: {total_requests} request(s)):")
    print(f"  {'STATE':<25}  {'COUNT':>8}")
    print(f"  {'-'*25}  {'-'*8}")
    for row in sorted(state_rows, key=lambda r: -_cnt(r)):
        print(f"  {str(row.get('state')):<25}  {_cnt(row):>8}")

    # OQ4-2: sample request records (no partner fields)
    print(f"\n  OQ4-2 — Sample request records (limit=3, non-PII fields only):")
    try:
        rows = do_search_read(
            client, uid, _RECONCILE_REQUEST, [],
            ["id", "name", "state", "create_date", "date"],
            limit=3, order="id asc",
        )
        for i, row in enumerate(rows, 1):
            print(f"\n  Request {i}:")
            for k, v in sorted(row.items()):
                print(f"    {k:<40}  {v}")
    except Exception as exc:
        print(f"  search_read failed: {exc}")

    # OQ4-3: how many reconcile payments per request?
    print(f"\n  OQ4-3 — Payments per request (groupby reconcile_request_id on {_RECONCILE_MODEL}):")
    try:
        req_rows = do_read_group(client, uid, _RECONCILE_MODEL,
                                 [("state", "=", "post")],
                                 ["amount"], ["reconcile_request_id"])
        print(f"  {'REQUEST (ID only)':>20}  {'PAYMENTS':>10}  {'SUM(amount) EGP':>20}")
        print(f"  {'-'*20}  {'-'*10}  {'-'*20}")
        for row in sorted(req_rows, key=lambda r: -_cnt(r)):
            req_raw = row.get("reconcile_request_id")
            req_id  = _partner_id_int(req_raw)
            cnt     = _cnt(row)
            amt     = _egp(row.get("amount"))
            print(f"  {'request id=' + str(req_id):>20}  {cnt:>10,}  {amt:>20,.2f}")
    except Exception as exc:
        print(f"  read_group by reconcile_request_id failed: {exc}")

    return {"oq4_total_requests": total_requests}


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    output_path   = Path(__file__).parent / f"discover_module3_phase1_{TODAY}.txt"
    output_buffer = StringIO()

    class Tee:
        def __init__(self, terminal, buf):
            self.terminal = terminal
            self.buf      = buf

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
        print("  M3-S1: Customer Accounts — Pre-Implementation Discovery")
        print(f"  Run date:   {TODAY}")
        print(f"  Odoo URL:   {ODOO_URL}")
        print(f"  Cost:       $0.00  (no OpenAI calls, read-only RPCs only)")
        print(f"  Today:      {TODAY}  (Late domain boundary)")
        print(SEP)

        with httpx.Client() as client:
            uid      = connect(client)
            kpia     = section_kpia(client, uid)
            kpib     = section_kpib(client, uid)
            kpic     = section_kpic(client, uid)
            refunds  = section_refunds(client, uid)
            oq2      = section_oq2(client, uid)
            oq4      = section_oq4(client, uid)

        # ── DISCOVERY SUMMARY ─────────────────────────────────────────────────
        print(f"\n{SEP}")
        print("  DISCOVERY SUMMARY — M3-S1")
        print(SEP)

        print(f"\n  KPI A — إجمالي المستحق على العملاء:")
        print(f"    Baseline:   {kpia['a1_total']:>20,.2f} EGP")
        print(f"    Partners:   {kpia['a1_partners']:>10,}")
        if abs(kpia['a1_total'] - kpia['a2_total']) < 0.01:
            print(f"    Null-partner check: PASS")
        else:
            n = kpia['null_install_count'] or 0
            print(f"    Null-partner check: FINDING — {n} record(s) excluded from groupby")

        print(f"\n  KPI B — أعلى العملاء تأخراً:")
        print(f"    Total late:  {kpib['b1_total']:>20,.2f} EGP")
        print(f"    Partners:    {kpib['b1_partners']:>10,}")
        print(f"    أعلى 10 =    {kpib['concentration']:.1f}%")

        if kpib['delta_r1a'] < 0.01:
            print(f"    R1a (domain integrity): PASS — exact match")
        else:
            n = kpib['null_late_count'] or 0
            print(f"    R1a (domain integrity): FINDING — delta={kpib['delta_r1a']:,.2f} EGP"
                  f"  ({n} null-partner record(s))")

        if kpib['delta_r1b'] < 1.0:
            print(f"    R1b (KPI2 identity):    PASS — delta={kpib['delta_r1b']:.4f} EGP")
        elif kpib['delta_r1b'] < 1000.0:
            print(f"    R1b (KPI2 identity):    micro-drift={kpib['delta_r1b']:,.2f} EGP (acceptable)")
        else:
            print(f"    R1b (KPI2 identity):    FINDING — delta={kpib['delta_r1b']:,.2f} EGP")

        print(f"\n  KPI C — رصيد المحفظة غير المخصص:")
        print(f"    Baseline:   {kpic['c1_total']:>20,.2f} EGP")
        print(f"    Partners:   {kpic['c1_partners']:>10,}")

        print(f"\n  الاستردادات:")
        print(f"    Total:      {refunds['df_total']:>20,.2f} EGP")
        print(f"    Records:    {refunds['df_rec_count']:>10,}")
        pct = (refunds['d2_null'] / refunds['df_rec_count'] * 100
               if refunds['df_rec_count'] > 0 else 0.0)
        print(f"    Null partner: {refunds['d2_null']:>8,}  ({pct:.1f}%)")

        oq2_status = ("STILL EMPTY — unchanged from Phase 3"
                      if oq2['oq2_total'] == 0
                      else f"NEW DATA — {oq2['oq2_total']} record(s)")
        print(f"\n  OQ2 (reconcile.line): {oq2_status}")
        print(f"  OQ4 (reconcile.request): {oq4['oq4_total_requests']} request(s) — see §6 above")

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
