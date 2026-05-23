"""
discover_m3s6_drilldown.py — M3-S6: Customer Drill-Down Discovery
READ-ONLY: search_read, search_count, read_group, fields_get ONLY.
No create/write/unlink. No OpenAI. AI cost = $0.00.

Answers:
  Sec 1 — Identify sample customer: top overdue from KPI B Late domain.
  Sec 2 — DR1: what field represents "الإجمالي الأصلي" for نسبة السداد?
           Candidates: SUM(amount) vs SUM(due_amount)+SUM(paid_amount), etc.
  Sec 3 — DR2: what field/model represents "آخر دفعة فعلية"?
           Approach A: MAX(write_date) on paid/partial rs.installment
           Approach B: rs.account.payment.installment.date via payment_line IDs
  Sec 4 — Explicit field recommendations for DR1 and DR2.

Run from repo root:
    python scripts/discover_m3s6_drilldown.py
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

_INSTALLMENT_MODEL  = "rs.installment"
_PAYMENT_INST_MODEL = "rs.account.payment.installment"

LATE_DOMAIN = [
    ("state",         "=",  "post"),
    ("payment_state", "in", ["unpaid", "partial"]),
    ("date",          "<",  TODAY),
]

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


def do_fields_get(client, uid, model, attributes=None):
    return execute(client, uid, model, "fields_get", [],
                   {"attributes": attributes or ["string", "type"]})


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


def _partner_id_int(raw) -> int:
    if isinstance(raw, (list, tuple)) and raw:
        return int(raw[0])
    return int(raw) if raw else 0


def _partner_name(raw) -> str:
    if isinstance(raw, (list, tuple)) and len(raw) > 1:
        return str(raw[1])
    return ""


# ── SECTIONS ──────────────────────────────────────────────────────────────────

def section_1_identify_sample(client, uid) -> tuple[int, float, int]:
    """Return (partner_id, late_due_amount, late_count) for rank-1 overdue customer."""
    print(f"\n{SEP}")
    print("  SECTION 1: Identify Sample Customer (Rank 1 — KPI B Late Domain)")
    print(SEP)
    print(f"  Today  : {TODAY}")
    print(f"  Domain : {LATE_DOMAIN}")

    rows = do_read_group(
        client, uid, _INSTALLMENT_MODEL,
        LATE_DOMAIN,
        ["due_amount"],
        ["partner_id"],
    )
    if not rows:
        raise RuntimeError("KPI B read_group returned 0 rows — cannot identify sample customer")

    rows_sorted = sorted(rows, key=lambda r: _egp(r.get("due_amount")), reverse=True)
    top = rows_sorted[0]

    partner_id = _partner_id_int(top.get("partner_id"))
    due_amount = _egp(top.get("due_amount"))
    inst_count = _cnt(top)

    print(f"\n  Rank-1 customer:")
    print(f"    partner_id              = {partner_id}")
    print(f"    customer_name           = [REDACTED]")
    print(f"    late due_amount         = {due_amount:>20,.2f} EGP")
    print(f"    late installment_count  = {inst_count}")
    print(f"\n  Baseline (M3-S1, 2026-05-23): ~= 18,202,000.00 EGP / 76 installments")
    delta = abs(due_amount - 18_202_000.00)
    if delta < 2_000.0:
        print(f"    delta vs baseline = {delta:.2f} EGP — PASS")
    else:
        print(f"    delta vs baseline = {delta:,.2f} EGP — NOTE: data shifted since M3-S1")

    return partner_id, due_amount, inst_count


def section_2_dr1_original_total(client, uid, partner_id: int) -> None:
    """DR1: confirm the correct field(s) for 'الإجمالي الأصلي' and 'نسبة السداد'."""
    print(f"\n{SEP}")
    print("  SECTION 2: DR1 — الإجمالي الأصلي (Payment Ratio Denominator)")
    print(SEP)
    print("  Goal: which field = 'full original contracted amount' for this customer?")
    print("  Also verify: late + future == unpaid total (التصحيح المفاهيمي assertion).")
    print(SEP2)

    # 2A — ALL posted installments (all payment states)
    domain_all = [("state", "=", "post"), ("partner_id", "=", partner_id)]
    rows_all = do_read_group(
        client, uid, _INSTALLMENT_MODEL,
        domain_all,
        ["amount", "due_amount", "paid_amount", "x_studio_actual_paid_amount"],
        [],
    )
    row_all      = rows_all[0] if rows_all else {}
    total_amount = _egp(row_all.get("amount"))
    total_due    = _egp(row_all.get("due_amount"))
    total_paid   = _egp(row_all.get("paid_amount"))
    total_actual = _egp(row_all.get("x_studio_actual_paid_amount"))
    total_count  = _cnt(row_all)

    print(f"\n  2A — ALL posted installments for partner_id={partner_id}:")
    print(f"    __count                              = {total_count}")
    print(f"    SUM(amount)                          = {total_amount:>22,.2f} EGP  ← candidate: إجمالي الأصلي")
    print(f"    SUM(due_amount)                      = {total_due:>22,.2f} EGP  ← remaining balance")
    print(f"    SUM(paid_amount)                     = {total_paid:>22,.2f} EGP  ← incl. pending cheques")
    print(f"    SUM(x_studio_actual_paid_amount)     = {total_actual:>22,.2f} EGP  ← cash received only")

    eq1_delta = abs(total_amount - (total_due + total_paid))
    print(f"\n  EQ1: amount == due_amount + paid_amount ?")
    print(f"    {total_amount:,.2f} == {total_due + total_paid:,.2f}  "
          f"delta={eq1_delta:.4f}  {'PASS' if eq1_delta < 1.0 else 'FAIL'}")

    # نسبة السداد candidates
    ratio_actual = (total_actual / total_amount * 100) if total_amount > 0 else 0.0
    ratio_paid   = (total_paid   / total_amount * 100) if total_amount > 0 else 0.0
    print(f"\n  نسبة السداد candidates (over SUM(amount) as denominator):")
    print(f"    Formula A (cash):   x_studio_actual_paid_amount / amount = {ratio_actual:.2f}%")
    print(f"    Formula B (cheque): paid_amount / amount                  = {ratio_paid:.2f}%")

    # 2B — Unpaid/partial (all dates) — غير المدفوع total
    domain_unpaid = [
        ("state", "=", "post"),
        ("partner_id", "=", partner_id),
        ("payment_state", "in", ["unpaid", "partial"]),
    ]
    rows_unpaid = do_read_group(
        client, uid, _INSTALLMENT_MODEL,
        domain_unpaid,
        ["amount", "due_amount"],
        [],
    )
    row_unpaid    = rows_unpaid[0] if rows_unpaid else {}
    unpaid_due    = _egp(row_unpaid.get("due_amount"))
    unpaid_amount = _egp(row_unpaid.get("amount"))
    unpaid_count  = _cnt(row_unpaid)

    print(f"\n  2B — Unpaid/Partial (all dates — إجمالي غير المدفوع):")
    print(f"    __count        = {unpaid_count}")
    print(f"    SUM(amount)    = {unpaid_amount:>22,.2f} EGP")
    print(f"    SUM(due_amount)= {unpaid_due:>22,.2f} EGP  ← 'إجمالي عليه' for panel header")

    # 2C — Late (متأخر: date < today, unpaid/partial)
    domain_late = LATE_DOMAIN + [("partner_id", "=", partner_id)]
    rows_late = do_read_group(
        client, uid, _INSTALLMENT_MODEL,
        domain_late,
        ["amount", "due_amount"],
        [],
    )
    row_late   = rows_late[0] if rows_late else {}
    late_due   = _egp(row_late.get("due_amount"))
    late_count = _cnt(row_late)

    print(f"\n  2C — Late (متأخر: date < {TODAY}, unpaid/partial):")
    print(f"    __count        = {late_count}")
    print(f"    SUM(due_amount)= {late_due:>22,.2f} EGP  ← 'منها متأخر'")

    # 2D — Future (مستقبلي: date >= today, unpaid/partial)
    domain_future = [
        ("state", "=", "post"),
        ("partner_id", "=", partner_id),
        ("payment_state", "in", ["unpaid", "partial"]),
        ("date", ">=", TODAY),
    ]
    rows_future = do_read_group(
        client, uid, _INSTALLMENT_MODEL,
        domain_future,
        ["amount", "due_amount"],
        [],
    )
    row_future    = rows_future[0] if rows_future else {}
    future_due    = _egp(row_future.get("due_amount"))
    future_count  = _cnt(row_future)

    print(f"\n  2D — Future (مستقبلي: date >= {TODAY}, unpaid/partial):")
    print(f"    __count        = {future_count}")
    print(f"    SUM(due_amount)= {future_due:>22,.2f} EGP  ← 'منها مستقبلي'")

    # ASSERTION: late + future == unpaid total (التصحيح المفاهيمي)
    sum_count  = late_count + future_count
    sum_due    = late_due + future_due
    due_delta  = abs(sum_due - unpaid_due)
    cnt_ok     = sum_count == unpaid_count
    due_ok     = due_delta < 1.0

    print(f"\n  ASSERTION — متأخر + مستقبلي = إجمالي عليه (التصحيح المفاهيمي):")
    print(f"    count: {late_count} + {future_count} = {sum_count} vs {unpaid_count} "
          f"— {'PASS' if cnt_ok else 'FAIL'}")
    print(f"    due:   {late_due:,.2f} + {future_due:,.2f} = {sum_due:,.2f} "
          f"vs {unpaid_due:,.2f}  delta={due_delta:.4f}  {'PASS' if due_ok else 'FAIL'}")

    if cnt_ok and due_ok:
        print(f"    ASSERTION HOLDS — both count and amount match exactly.")
    else:
        print(f"    WARNING: assertion failed — investigate before building endpoint.")


def section_3_dr2_last_payment(client, uid, partner_id: int) -> None:
    """DR2: confirm the correct field/model for 'آخر دفعة فعلية'."""
    print(f"\n{SEP}")
    print("  SECTION 3: DR2 — آخر دفعة فعلية (Last Payment Date)")
    print(SEP)
    print("  Approach A: MAX(write_date) on paid/partial rs.installment records.")
    print("  Approach B: MAX(date) on rs.account.payment.installment via payment_line IDs.")
    print(SEP2)

    # 3A — write_date on paid/partial installments
    domain_paid = [
        ("state", "=", "post"),
        ("partner_id", "=", partner_id),
        ("payment_state", "in", ["paid", "partial"]),
    ]
    paid_count = do_search_count(client, uid, _INSTALLMENT_MODEL, domain_paid)
    print(f"\n  3A — write_date on paid/partial rs.installment:")
    print(f"    Total paid/partial installments for customer: {paid_count}")

    if paid_count > 0:
        paid_rows = do_search_read(
            client, uid, _INSTALLMENT_MODEL,
            domain_paid,
            ["id", "date", "write_date", "payment_state", "amount"],
            order="write_date desc",
            limit=3,
        )
        print(f"    Top 3 by write_date desc:")
        for r in paid_rows:
            print(f"      id={r['id']}, due_date={r.get('date')}, "
                  f"write_date={r.get('write_date')}, "
                  f"state={r.get('payment_state')}, "
                  f"amount={_egp(r.get('amount')):,.2f}")
        if paid_rows:
            print(f"    → write_date MAX (fallback candidate): {paid_rows[0].get('write_date')}")
    else:
        print("    No paid/partial installments for this customer.")

    # 3B — fields_get on rs.account.payment.installment
    print(f"\n  3B — fields_get on {_PAYMENT_INST_MODEL}:")
    try:
        pmt_fields = do_fields_get(
            client, uid, _PAYMENT_INST_MODEL,
            ["string", "type", "relation"],
        )
        interesting = {
            k: v for k, v in pmt_fields.items()
            if any(s in k.lower() for s in (
                "date", "partner", "installment", "amount",
                "residual", "state", "payment",
            ))
        }
        print(f"    Filtered fields ({len(interesting)} matching date/partner/installment/amount):")
        for fname, fmeta in sorted(interesting.items()):
            rel = fmeta.get("relation", "")
            rel_str = f" → {rel}" if rel else ""
            print(f"      {fname:<42} {fmeta.get('type',''):<14} "
                  f"'{fmeta.get('string','')}'  {rel_str}")
    except Exception as e:
        print(f"    fields_get failed: {e}")
        pmt_fields = {}
        interesting = {}

    # 3C — try by partner_id directly if field exists
    has_partner = "partner_id" in pmt_fields
    print(f"\n  3C — partner_id on {_PAYMENT_INST_MODEL}: {'YES' if has_partner else 'NO'}")
    if has_partner:
        try:
            agg = do_read_group(
                client, uid, _PAYMENT_INST_MODEL,
                [("partner_id", "=", partner_id), ("state", "=", "post")],
                ["date"],
                [],
            )
            row = agg[0] if agg else {}
            print(f"    read_group (no groupby): __count={_cnt(row)}, date={row.get('date')}")
            if _cnt(row) > 0:
                # Get max date record directly
                top_pmt = do_search_read(
                    client, uid, _PAYMENT_INST_MODEL,
                    [("partner_id", "=", partner_id), ("state", "=", "post")],
                    ["id", "date", "state"],
                    order="date desc",
                    limit=3,
                )
                print(f"    Top 3 by date desc (partner_id path):")
                for r in top_pmt:
                    print(f"      id={r['id']}, date={r.get('date')}, state={r.get('state')}")
                if top_pmt:
                    print(f"    → MAX date (partner_id path): {top_pmt[0].get('date')}")
        except Exception as e:
            print(f"    Failed: {e}")
    else:
        print("    Skipping 3C — no partner_id field on this model.")

    # 3D — payment_line (one2many) approach: gather IDs then cross-query
    print(f"\n  3D — payment_line one2many approach:")
    try:
        inst_rows = do_search_read(
            client, uid, _INSTALLMENT_MODEL,
            [("state", "=", "post"), ("partner_id", "=", partner_id)],
            ["id", "payment_line"],
        )
        all_pmt_ids: list[int] = []
        for row in inst_rows:
            pl = row.get("payment_line") or []
            if isinstance(pl, list):
                all_pmt_ids.extend(int(x) for x in pl)

        print(f"    Scanned {len(inst_rows)} installments → "
              f"{len(all_pmt_ids)} payment_line IDs collected.")

        if all_pmt_ids:
            top_pmts = do_search_read(
                client, uid, _PAYMENT_INST_MODEL,
                [("id", "in", all_pmt_ids)],
                ["id", "date", "state"],
                order="date desc",
                limit=5,
            )
            print(f"    Top 5 payment.installment records by date desc:")
            for r in top_pmts:
                print(f"      id={r['id']}, date={r.get('date')}, state={r.get('state')}")
            if top_pmts:
                print(f"\n    → MAX payment posting date (payment_line path): {top_pmts[0].get('date')}")
            else:
                print("    search_read on payment.installment returned 0 rows.")
        else:
            print("    No payment_line IDs found — customer has no linked payment records.")
    except Exception as e:
        print(f"    payment_line approach failed: {e}")


def section_4_summary() -> None:
    print(f"\n{SEP}")
    print("  SECTION 4: Recommendation Summary — DR1 / DR2")
    print(SEP)
    print("""
  DR1 — الإجمالي الأصلي (نسبة السداد denominator):
  ─────────────────────────────────────────────────────────────────────────
  FIELD: SUM(amount) on ALL posted installments for partner_id.
  This is the full contractual face value (paid + unpaid combined).

  نسبة السداد = SUM(x_studio_actual_paid_amount) / SUM(amount) × 100

  Why x_studio_actual_paid_amount (not paid_amount)?
    paid_amount includes pending cheques (received but not yet banked).
    x_studio_actual_paid_amount = confirmed cash only (PATH A, Collections KPI 2).

  See 2A output above for actual values on sample customer.
  ─────────────────────────────────────────────────────────────────────────

  DR2 — آخر دفعة فعلية (last payment date):
  ─────────────────────────────────────────────────────────────────────────
  PREFERRED: MAX(date) from rs.account.payment.installment
    via payment_line IDs from rs.installment (Approach 3D).
    date = payment posting datetime — semantically correct.

  IF partner_id EXISTS on payment.installment (3C result):
    simpler path: MAX(date) WHERE partner_id = X AND state = 'post'.

  FALLBACK: MAX(write_date) on paid/partial rs.installment (Approach 3A).
    write_date can update for non-payment reasons — less reliable.

  See Section 3 output above. If both approaches return the same date,
  prefer the payment.installment path for semantic correctness.
  ─────────────────────────────────────────────────────────────────────────
""")


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    # Reconfigure stdout to UTF-8 so Arabic/special chars render on Windows.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    buf = StringIO()
    orig_stdout = sys.stdout

    class Tee:
        def write(self, s):
            orig_stdout.write(s)
            buf.write(s)
        def flush(self):
            orig_stdout.flush()

    sys.stdout = Tee()
    try:
        print(SEP)
        print("  Module 3 — M3-S6: Customer Drill-Down Discovery")
        import datetime as _dt
        print(f"  Run at : {_dt.datetime.now().isoformat()}")
        print(f"  Today  : {TODAY}")
        print(f"  ALLOWED_METHODS: {sorted(ALLOWED_METHODS)}")
        print("  READ-ONLY. No writes. No OpenAI. AI cost = $0.00")
        print(SEP)

        with httpx.Client() as client:
            uid = connect(client)
            partner_id, late_due, late_count = section_1_identify_sample(client, uid)
            section_2_dr1_original_total(client, uid, partner_id)
            section_3_dr2_last_payment(client, uid, partner_id)
            section_4_summary()

        print(f"\n{SEP}")
        print("  DONE")
        print(SEP)

    finally:
        sys.stdout = orig_stdout
        out_path = Path(__file__).parent / f"discover_m3s6_drilldown_{TODAY}.txt"
        out_path.write_text(buf.getvalue(), encoding="utf-8")
        print(f"\n[TEE] Output saved to: {out_path}")


if __name__ == "__main__":
    main()
