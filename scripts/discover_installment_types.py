"""
discover_installment_types.py — Stage 7, Gate 1 Discovery
READ-ONLY: search_read, search_count, fields_get ONLY.
No create/write/unlink. No OpenAI. AI cost = $0.00.

Inventories all records in rs.installment.type:
  ID, name, sequence, code (if field exists).

Stops cleanly after printing the full type list.
Khaled reviews the output before any feature code is written.

Run from any directory:
    python scripts/discover_installment_types.py
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

SEP  = "=" * 72
SEP2 = "-" * 72

_TYPE_MODEL = "rs.installment.type"


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


def fields_get(client, uid, model):
    return execute(client, uid, model, "fields_get", [],
                   {"attributes": ["string", "type", "relation", "help", "required"]})


def search_count(client, uid, model, domain):
    return execute(client, uid, model, "search_count", [domain])


def search_read(client, uid, model, domain, fields, order=None):
    kw: dict = {"fields": fields}
    if order:
        kw["order"] = order
    return execute(client, uid, model, "search_read", [domain], kw)


# ── AUTHENTICATION ────────────────────────────────────────────────────────────

def connect(client):
    print("\n[AUTH] Authenticating...")
    uid = rpc(client, "common", "authenticate",
              [ODOO_DB, ODOO_USER, ODOO_KEY, {}])
    if not uid:
        raise RuntimeError("Auth failed — check .env credentials")
    print(f"  OK uid={uid}")
    return uid


# ── SECTIONS ──────────────────────────────────────────────────────────────────

def section1_fields_inventory(client, uid):
    print(f"\n{SEP}")
    print(f"  SECTION 1: Field Inventory — {_TYPE_MODEL}")
    print(SEP)

    flds = fields_get(client, uid, _TYPE_MODEL)
    print(f"\n  {len(flds)} fields found on {_TYPE_MODEL}:\n")
    print(f"  {'FIELD':<35} {'TYPE':<12}  LABEL")
    print(f"  {'-'*35} {'-'*12}  {'-'*35}")
    for name, meta in sorted(flds.items()):
        ftype  = meta.get("type", "")
        label  = meta.get("string", "")
        print(f"  {name:<35} {ftype:<12}  {label}")

    has_code     = "code"     in flds
    has_sequence = "sequence" in flds
    print(f"\n  has 'code' field:     {has_code}")
    print(f"  has 'sequence' field: {has_sequence}")
    return has_code, has_sequence


def section2_type_count(client, uid):
    print(f"\n{SEP}")
    print(f"  SECTION 2: Record Count — {_TYPE_MODEL}")
    print(SEP)

    total = search_count(client, uid, _TYPE_MODEL, [])
    print(f"\n  Total records in {_TYPE_MODEL}: {total}")

    if total == 13:
        print("  [OK] Matches Phase 2 finding of 13 types.")
    else:
        print(f"  !! UNEXPECTED: Phase 2 documented 13 types, live shows {total}.")
        print("     Stop and investigate before proceeding.")
    return int(total)


def section3_full_type_list(client, uid, has_code: bool, has_sequence: bool):
    print(f"\n{SEP}")
    print(f"  SECTION 3: Full Type List — {_TYPE_MODEL}")
    print(SEP)

    # Phase 1 reveals the model has category_code and category_id instead of code/sequence.
    # Always fetch both — they are the structural identifiers available on this model.
    fetch_fields = ["id", "name", "category_code", "category_id"]
    if has_sequence:
        fetch_fields.append("sequence")
    if has_code:
        fetch_fields.append("code")

    order = "id asc"
    rows = search_read(client, uid, _TYPE_MODEL, [], fetch_fields, order=order)

    print(f"\n  Fetched {len(rows)} records (fields: {fetch_fields}, order: {order})\n")

    # Dynamic column widths
    col_id   = max(4, max(len(str(r["id"])) for r in rows) + 1)
    col_nm   = max(6, max(len(str(r.get("name") or "")) for r in rows) + 2)
    col_cat  = max(13, max(len(str(r.get("category_code") or "")) for r in rows) + 2)
    col_catid = max(15, max(len(str(r.get("category_id") or "")) for r in rows) + 2)
    if has_sequence:
        col_seq = 10
    if has_code:
        col_cod = max(6, max(len(str(r.get("code") or "")) for r in rows) + 2)

    hdr = f"  {'ID':<{col_id}}  {'NAME':<{col_nm}}  {'CATEGORY_CODE':<{col_cat}}  {'CATEGORY_ID':<{col_catid}}"
    if has_sequence:
        hdr += f"  {'SEQUENCE':<{col_seq}}"
    if has_code:
        hdr += f"  {'CODE':<{col_cod}}"
    print(hdr)

    divider = f"  {'-'*col_id}  {'-'*col_nm}  {'-'*col_cat}  {'-'*col_catid}"
    if has_sequence:
        divider += f"  {'-'*col_seq}"
    if has_code:
        divider += f"  {'-'*col_cod}"
    print(divider)

    for r in rows:
        cat_code  = str(r.get("category_code") or "")
        cat_id    = str(r.get("category_id") or "")
        line = (
            f"  {str(r['id']):<{col_id}}"
            f"  {str(r.get('name') or ''):<{col_nm}}"
            f"  {cat_code:<{col_cat}}"
            f"  {cat_id:<{col_catid}}"
        )
        if has_sequence:
            line += f"  {str(r.get('sequence') or ''):<{col_seq}}"
        if has_code:
            line += f"  {str(r.get('code') or ''):<{col_cod}}"
        print(line)

    print(f"\n  Total rows: {len(rows)}")
    return rows


def section4_reconcile_with_business_context(rows):
    """Cross-reference returned types against the 8 documented in Business Context §7."""
    print(f"\n{SEP}")
    print("  SECTION 4: Reconciliation vs. Business Context §7 (8 documented types)")
    print(SEP)

    # The 8 documented Arabic names from Business Context §7.
    # We match by name substring (case-insensitive) since Odoo may have extra spacing/diacritics.
    documented_ar = [
        "المقدمة",
        "قسط دوري",
        "وديعة الصيانة",
        "مصاريف إدارية",
        "الجراج",
        "النادي",
        "مرافق",
        "الغرامات",
    ]

    type_names = [str(r.get("name") or "") for r in rows]

    print(f"\n  Documented Arabic names (§7) and match status:")
    for ar_name in documented_ar:
        matched = any(ar_name in n or n in ar_name for n in type_names)
        status  = "MATCHED" if matched else "NOT FOUND in live data"
        print(f"    {ar_name:<25}  {status}")

    print(f"\n  Total live types: {len(rows)}")
    print(f"  Documented:       8")
    print(f"  Undocumented:     {len(rows) - 8}  ← require Khaled's review before coding begins")

    print(f"\n{SEP2}")
    print("  GATE 1 — STOP")
    print(SEP2)
    print("""
  The full type list is printed above. No feature code has been written.

  ACTION REQUIRED from Khaled:
    1. Review the list above against the live Odoo UI.
    2. For each of the undocumented type IDs (those NOT matching the 8 in §7),
       supply the official Arabic name to use in Board-facing output.
    3. Confirm the Odoo ID for each of the 8 documented types.

  Only after Khaled provides the completed ID → Arabic name mapping
  will the implementation (Deliverables 2–10) begin.
""")


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    output_path = Path(__file__).parent / f"discover_installment_types_{TODAY}.txt"
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
                safe = data.encode(self.terminal.encoding or "utf-8", errors="replace").decode(
                    self.terminal.encoding or "utf-8", errors="replace"
                )
                self.terminal.write(safe)
        def flush(self):
            self.buf.flush()
            self.terminal.flush()

    sys.stdout = Tee(sys.__stdout__, output_buffer)

    try:
        print(SEP)
        print("  Stage 7 — Gate 1 Discovery: rs.installment.type")
        print(f"  Run date: {TODAY}")
        print(f"  Odoo URL: {ODOO_URL}")
        print(SEP)

        with httpx.Client() as client:
            uid = connect(client)

            has_code, has_sequence = section1_fields_inventory(client, uid)
            total = section2_type_count(client, uid)
            rows  = section3_full_type_list(client, uid, has_code, has_sequence)
            section4_reconcile_with_business_context(rows)

        print(f"\n  RPCs used: 3 (fields_get + search_count + search_read)")
        print(f"  All read-only. No data modified.")

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
