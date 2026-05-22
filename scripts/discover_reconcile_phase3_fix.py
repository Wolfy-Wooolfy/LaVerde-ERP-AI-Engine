"""
discover_reconcile_phase3_fix.py — Phase 3 Fix: rs.account.payment.reconcile
READ-ONLY: search_read, search_count, read_group, fields_get ONLY.
No create/write/unlink. No OpenAI. AI cost = $0.00.

Fixes the Section 6 error in discover_reconcile_phase3.py, which ran its
sample/currency/sign analysis on rs.account.check (highest record count)
instead of rs.account.payment.reconcile (the actual wallet model).

Targets rs.account.payment.reconcile BY NAME — not by record count.

Answers:
  F1. 3 sample records with real field values (amount, reconciled_amount,
      residual_amount, currency, state).
  F2. Confirm field types for the three balance fields via fields_get.
  F3. OQ1 — search_count for negative residual_amount.
  F4. OQ3 — selection values + read_group distribution for 'type' and
      'payment_type'.

Run from any directory:
    python scripts/discover_reconcile_phase3_fix.py
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

_TARGET_MODEL = "rs.account.payment.reconcile"

SEP  = "=" * 72
SEP2 = "-" * 72

_PII_FRAGMENTS = {
    "name", "partner", "email", "phone", "mobile",
    "vat", "street", "city", "display_name", "id_number",
}


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


def do_fields_get(client, uid, model, attributes=None):
    attrs = attributes or ["string", "type", "relation"]
    return execute(client, uid, model, "fields_get", [], {"attributes": attrs})


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


# ── AUTHENTICATION ────────────────────────────────────────────────────────────

def connect(client):
    print("\n[AUTH] Authenticating...")
    uid = rpc(client, "common", "authenticate",
              [ODOO_DB, ODOO_USER, ODOO_KEY, {}])
    if not uid:
        raise RuntimeError("Auth failed — check .env credentials")
    print(f"  OK uid={uid}")
    return uid


# ── SANITIZE ──────────────────────────────────────────────────────────────────

def sanitize(record: dict) -> dict:
    out = {}
    for k, v in record.items():
        if any(frag in k.lower() for frag in _PII_FRAGMENTS):
            out[k] = "[REDACTED]"
        else:
            out[k] = v
    return out


# ── FIX SECTION 1: Sample Records ─────────────────────────────────────────────

def fix1_sample_records(client, uid):
    print(f"\n{SEP}")
    print(f"  FIX-1: Sample Records — {_TARGET_MODEL}  (limit=3, order=id asc)")
    print(SEP)

    # Fetch all fields — let Odoo return everything, then filter display
    flds = do_fields_get(client, uid, _TARGET_MODEL)
    all_field_names = sorted(flds.keys())

    rows = do_search_read(
        client, uid, _TARGET_MODEL, [],
        all_field_names, limit=3, order="id asc",
    )

    print(f"\n  {len(rows)} records returned.\n")
    for i, raw in enumerate(rows, 1):
        rec = sanitize(raw)
        print(f"  Record {i}:")
        for k, v in sorted(rec.items()):
            print(f"    {k:<45}  {v}")
        print()

    return flds


# ── FIX SECTION 2: Balance Field Type Confirmation ────────────────────────────

def fix2_balance_field_types(flds: dict):
    print(f"\n{SEP}")
    print("  FIX-2: Balance Field Type Confirmation — fields_get result")
    print(SEP)

    target_fields = ["amount", "reconciled_amount", "residual_amount"]

    print(f"\n  {'FIELD':<30}  {'TYPE':<12}  {'LABEL':<30}  CURRENCY RELATION")
    print(f"  {'-'*30}  {'-'*12}  {'-'*30}  {'-'*20}")

    for fn in target_fields:
        if fn not in flds:
            print(f"  {fn:<30}  NOT FOUND")
            continue
        meta  = flds[fn]
        ftype = meta.get("type", "")
        label = meta.get("string", "")
        rel   = meta.get("relation", "") or ""
        print(f"  {fn:<30}  {ftype:<12}  {label:<30}  {rel}")

    # Also show currency fields
    print(f"\n  Currency-related fields on {_TARGET_MODEL}:")
    print(f"  {'FIELD':<30}  {'TYPE':<12}  RELATION")
    print(f"  {'-'*30}  {'-'*12}  {'-'*30}")
    for fn, meta in sorted(flds.items()):
        ftype = meta.get("type", "")
        rel   = meta.get("relation", "") or ""
        if rel == "res.currency" or "currency" in fn.lower():
            print(f"  {fn:<30}  {ftype:<12}  {rel}")


# ── FIX SECTION 3: OQ1 — Negative residual_amount ────────────────────────────

def fix3_oq1_negative_residual(client, uid):
    print(f"\n{SEP}")
    print("  FIX-3: OQ1 — Negative residual_amount check")
    print(SEP)

    total        = do_search_count(client, uid, _TARGET_MODEL, [("state", "=", "post")])
    neg_residual = do_search_count(client, uid, _TARGET_MODEL,
                                   [("residual_amount", "<", 0), ("state", "=", "post")])
    zero_residual = do_search_count(client, uid, _TARGET_MODEL,
                                    [("residual_amount", "=", 0), ("state", "=", "post")])
    pos_residual  = do_search_count(client, uid, _TARGET_MODEL,
                                    [("residual_amount", ">", 0), ("state", "=", "post")])

    print(f"\n  Domain: state = 'post'  (total: {total} records)")
    print(f"\n  {'residual_amount > 0  (funds still held):':<45}  {pos_residual}")
    print(f"  {'residual_amount = 0  (fully applied / zero balance):':<45}  {zero_residual}")
    print(f"  {'residual_amount < 0  (over-applied / debit):':<45}  {neg_residual}")

    if neg_residual == 0:
        print(f"\n  OQ1 CLOSED: residual_amount is always >= 0.")
        print(f"  The wallet balance never represents a debit. Safe to sum with")
        print(f"  installment amounts without sign-flip risk.")
    else:
        print(f"\n  !! OQ1 OPEN: {neg_residual} records have residual_amount < 0.")
        print(f"  Negative wallet balance exists. Investigate before using in any KPI sum.")

    return neg_residual


# ── FIX SECTION 4: OQ3 — type and payment_type semantics ─────────────────────

def fix4_oq3_type_fields(client, uid):
    print(f"\n{SEP}")
    print("  FIX-4: OQ3 — 'type' and 'payment_type' field semantics")
    print(SEP)

    # Get selection values from fields_get
    flds_sel = do_fields_get(client, uid, _TARGET_MODEL,
                             attributes=["string", "type", "selection"])

    for field_name in ("type", "payment_type"):
        print(f"\n{SEP2}")
        print(f"  Field: '{field_name}'")
        print(SEP2)

        if field_name not in flds_sel:
            print(f"  NOT FOUND on {_TARGET_MODEL}")
            continue

        meta      = flds_sel[field_name]
        ftype     = meta.get("type", "")
        label     = meta.get("string", "")
        selection = meta.get("selection") or []

        print(f"  Label: '{label}'  Type: {ftype}")

        if selection:
            print(f"\n  Selection values from fields_get:")
            print(f"  {'VALUE':<30}  DISPLAY LABEL")
            print(f"  {'-'*30}  {'-'*30}")
            for val, disp in selection:
                print(f"  {str(val):<30}  {disp}")
        else:
            print(f"  No static selection values in schema (may be dynamic).")

        # read_group to see live distribution
        print(f"\n  Live distribution (read_group groupby='{field_name}'):")
        try:
            rows = do_read_group(
                client, uid, _TARGET_MODEL,
                [],
                [field_name, "__count"],
                [field_name],
            )
            if not rows:
                print(f"  (no records or groupby returned empty)")
            else:
                print(f"  {'VALUE':<30}  {'COUNT':>8}")
                print(f"  {'-'*30}  {'-'*8}")
                for row in sorted(rows, key=lambda r: -(r.get("__count") or 0)):
                    val = row.get(field_name)
                    cnt = row.get("__count") or 0
                    print(f"  {str(val):<30}  {cnt:>8}")
        except Exception as exc:
            print(f"  read_group failed: {exc}")

    # Interpretation guidance
    print(f"\n{SEP2}")
    print("  Interpretation note:")
    print(SEP2)
    print("""
  §15 defines two reconcile scenarios:
    Scenario A — Initial reservation (حجز مبدئي): customer pays before
                 choosing a unit. Funds held until plan is created.
    Scenario B — Ownership transfer (نقل ملكية): funds from old owner
                 moved to new owner's wallet via reconcile.

  If 'type' or 'payment_type' has exactly 2 values that map to these
  two scenarios, the field can be used to distinguish them in future
  KPI segmentation. If the values don't map cleanly, both scenarios
  may share the same type value — and distinction would require looking
  at whether the record has a reservation_id vs. a termination_id.
""")


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    output_path = Path(__file__).parent / f"discover_reconcile_phase3_fix_{TODAY}.txt"
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
        print(f"  Phase 3 Fix: {_TARGET_MODEL} — targeted analysis")
        print(f"  Run date:   {TODAY}")
        print(f"  Odoo URL:   {ODOO_URL}")
        print(f"  Target:     {_TARGET_MODEL}  (by name, not by record count)")
        print(f"  Fixes:      Section 6 error in discover_reconcile_phase3.py")
        print(f"  Closes:     OQ1 (negative residual), OQ3 (type field semantics)")
        print(f"  Cost:       $0.00  (no OpenAI calls)")
        print(SEP)

        with httpx.Client() as client:
            uid = connect(client)

            flds = fix1_sample_records(client, uid)
            fix2_balance_field_types(flds)
            fix3_oq1_negative_residual(client, uid)
            fix4_oq3_type_fields(client, uid)

        print(f"\n  All read-only. No data modified in Odoo.")
        print(f"  RPCs used: ~10 (fields_get ×2, search_read ×1, search_count ×4, read_group ×2)")

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
