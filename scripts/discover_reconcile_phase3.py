"""
discover_reconcile_phase3.py — Phase 3 Discovery: Reconcile / Customer Wallet
READ-ONLY: search_read, search_count, read_group, fields_get ONLY.
No create/write/unlink. No OpenAI. AI cost = $0.00.

Goal: identify the Odoo model that backs the Reconcile (customer wallet) concept
described in MODULE_2_BUSINESS_CONTEXT.md §15 — unallocated funds per customer,
not yet applied to any rs.installment.

Starting assumption: model name unknown. No candidate is pre-confirmed.
The script discovers candidates through evidence, not presumption.

Run from any directory:
    python scripts/discover_reconcile_phase3.py
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

# ── PII FIELD NAME FRAGMENTS (sanitize any field whose name contains one) ─────
_PII_FRAGMENTS = {
    "name", "partner", "email", "phone", "mobile",
    "vat", "street", "city", "display_name", "id_number",
}

# rs.installment uses EGP — we test whether reconcile matches this.
RS_INSTALLMENT_CURRENCY_CONTEXT = "EGP (inferred from rs.installment monetary fields)"


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
                   {"attributes": ["string", "type", "relation"]})


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
    """Replace PII field values with '[REDACTED]'. Keeps all other values intact."""
    out = {}
    for k, v in record.items():
        if any(frag in k.lower() for frag in _PII_FRAGMENTS):
            out[k] = "[REDACTED]"
        else:
            out[k] = v
    return out


# ── SECTION 1: Broad Model Scan ───────────────────────────────────────────────

def section1_broad_model_scan(client, uid):
    print(f"\n{SEP}")
    print("  SECTION 1: Broad Model Scan — searching ir.model")
    print(SEP)

    # Three independent keyword sweeps.
    patterns = [
        ("reconcile", "reconcile"),
        ("wallet",    "wallet"),
        ("balance",   "balance"),
    ]

    all_hits: dict[str, str] = {}  # model_name -> label

    for kw, label in patterns:
        rows = do_search_read(
            client, uid, "ir.model",
            [("model", "like", kw)],
            ["model", "name"],
        )
        print(f"\n  Pattern '{kw}': {len(rows)} hit(s)")
        for r in rows:
            mn = r["model"]
            nm = r["name"]
            if mn not in all_hits:
                all_hits[mn] = nm
                print(f"    {mn:<55}  {nm}")

    # Also list everything under rs.account.* — Phase 1 noted "likely under rs.account.*".
    rows_rsacc = do_search_read(
        client, uid, "ir.model",
        [("model", "like", "rs.account.")],
        ["model", "name"],
    )
    print(f"\n  Full rs.account.* namespace ({len(rows_rsacc)} model(s)):")
    for r in rows_rsacc:
        mn = r["model"]
        nm = r["name"]
        marker = "  <-- already in hits" if mn in all_hits else ""
        if mn not in all_hits:
            all_hits[mn] = nm
        print(f"    {mn:<55}  {nm}{marker}")

    print(f"\n  Total unique candidate models: {len(all_hits)}")
    return all_hits  # model_name -> label


# ── SECTION 2: Record Counts ──────────────────────────────────────────────────

def section2_record_counts(client, uid, candidates: dict[str, str]):
    print(f"\n{SEP}")
    print("  SECTION 2: Record Counts — all candidate models")
    print(SEP)

    # Always include the four models named explicitly in Phase 1 RS Accounting inventory,
    # even if the broad scan missed them for some reason.
    phase1_explicit = {
        "rs.account.payment.reconcile":         "Real Estate Accounting Payment Reconcile",
        "rs.account.payment.reconcile.line":    "Real Estate Accounting Payment Reconcile",
        "rs.account.payment.reconcile.request": "Real Estate Accounting Payment Reconcile Request",
        "rs.account.payment.check.reconcile":   "Real Estate Accounting Payment Check Reconcile",
    }
    merged = {**candidates}
    for m, lbl in phase1_explicit.items():
        if m not in merged:
            merged[m] = lbl
            print(f"  [Phase1-explicit] Adding {m} (not found in broad scan)")

    counts: dict[str, int] = {}
    print(f"\n  {'MODEL':<55}  {'COUNT':>8}  LABEL")
    print(f"  {'-'*55}  {'-'*8}  {'-'*35}")

    for model, label in sorted(merged.items()):
        try:
            cnt = do_search_count(client, uid, model, [])
        except Exception as exc:
            cnt = -1
            print(f"  {model:<55}  {'ERROR':>8}  {str(exc)[:60]}")
            counts[model] = -1
            continue
        counts[model] = cnt
        flag = "  <-- has records" if cnt > 0 else ""
        print(f"  {model:<55}  {cnt:>8}{flag}  {label}")

    non_zero = {m: c for m, c in counts.items() if c > 0}
    print(f"\n  Models with records: {len(non_zero)}")
    for m, c in sorted(non_zero.items(), key=lambda x: -x[1]):
        print(f"    {m:<55}  {c:>8}")

    return counts


# ── SECTION 3: Fields Inventory ───────────────────────────────────────────────

def section3_fields_inventory(client, uid, counts: dict[str, int]):
    print(f"\n{SEP}")
    print("  SECTION 3: Fields Inventory — models with records")
    print(SEP)

    non_zero_models = sorted(
        [m for m, c in counts.items() if c > 0],
        key=lambda m: -counts[m],
    )

    if not non_zero_models:
        print("\n  !! No models with records found. Check Section 2 errors.")
        return {}

    all_fields: dict[str, dict] = {}  # model -> {field_name: meta}

    for model in non_zero_models:
        print(f"\n{SEP2}")
        print(f"  Model: {model}  (records: {counts[model]})")
        print(SEP2)

        try:
            flds = do_fields_get(client, uid, model)
        except Exception as exc:
            print(f"  fields_get failed: {exc}")
            continue

        all_fields[model] = flds
        print(f"\n  {len(flds)} fields total.\n")
        print(f"  {'FIELD':<40}  {'TYPE':<12}  {'RELATION':<35}  LABEL")
        print(f"  {'-'*40}  {'-'*12}  {'-'*35}  {'-'*30}")
        for fname, meta in sorted(flds.items()):
            ftype    = meta.get("type", "")
            relation = meta.get("relation", "") or ""
            label    = meta.get("string", "")
            print(f"  {fname:<40}  {ftype:<12}  {relation:<35}  {label}")

        # ── Highlight fields of discovery interest ──
        print(f"\n  -- Fields of discovery interest --")

        partner_fields = [
            (fn, m) for fn, m in flds.items()
            if m.get("type") == "many2one" and "partner" in (m.get("relation") or "").lower()
        ]
        monetary_fields = [fn for fn, m in flds.items() if m.get("type") == "monetary"]
        float_fields    = [fn for fn, m in flds.items() if m.get("type") == "float"]
        state_fields    = [fn for fn, m in flds.items() if fn in ("state", "status")]
        currency_fields = [
            fn for fn, m in flds.items()
            if "currency" in fn.lower() or m.get("relation", "") == "res.currency"
        ]
        payment_plan_fields = [
            fn for fn, m in flds.items()
            if "payment" in (m.get("relation") or "").lower()
               or "plan" in (m.get("relation") or "").lower()
        ]
        installment_fields = [
            fn for fn, m in flds.items()
            if (m.get("relation") or "") == "rs.installment"
        ]
        reservation_fields = [
            fn for fn, m in flds.items()
            if (m.get("relation") or "") in ("rs.reservation", "rs.contract")
        ]

        print(f"    Partner (customer) fields:      {partner_fields or 'NONE'}")
        print(f"    Monetary fields:                {monetary_fields or 'NONE'}")
        print(f"    Float fields (balance?):        {float_fields or 'NONE'}")
        print(f"    State/status fields:            {state_fields or 'NONE'}")
        print(f"    Currency fields:                {currency_fields or 'NONE'}")
        print(f"    Payment-plan/term relation flds:{payment_plan_fields or 'NONE'}")
        print(f"    rs.installment relation fields: {installment_fields or 'NONE'}")
        print(f"    Reservation/contract flds:      {reservation_fields or 'NONE'}")

    return all_fields


# ── SECTION 4: State Machine ──────────────────────────────────────────────────

def section4_state_machine(client, uid, all_fields: dict[str, dict],
                            counts: dict[str, int]):
    print(f"\n{SEP}")
    print("  SECTION 4: State Machine — read_group by state/status")
    print(SEP)

    found_any = False
    for model, flds in all_fields.items():
        for state_field in ("state", "status"):
            if state_field not in flds:
                continue
            found_any = True
            print(f"\n  Model: {model}  field: '{state_field}'")
            try:
                rows = do_read_group(
                    client, uid, model,
                    [],
                    [f"{state_field}", "__count"],
                    [state_field],
                )
                print(f"\n  {'STATE VALUE':<30}  {'COUNT':>8}")
                print(f"  {'-'*30}  {'-'*8}")
                for row in sorted(rows, key=lambda r: -(r.get("__count") or 0)):
                    sv  = str(row.get(state_field) or "(empty)")
                    cnt = row.get("__count") or 0
                    print(f"  {sv:<30}  {cnt:>8}")
            except Exception as exc:
                print(f"  read_group failed: {exc}")

    if not found_any:
        print("\n  No 'state' or 'status' field found in any model with records.")
        print("  This is itself a finding — reconcile may be stateless (always active).")


# ── SECTION 5: Linkage Summary ────────────────────────────────────────────────

def section5_linkage(all_fields: dict[str, dict]):
    """Derived from fields_get output — no extra RPCs."""
    print(f"\n{SEP}")
    print("  SECTION 5: Linkage Summary (derived from fields_get, no extra RPCs)")
    print(SEP)

    # Relations we care about, per the approved plan
    relations_of_interest = {
        "res.partner":                        "Customer",
        "rs.installment":                     "Installment",
        "rs.payment.term":                    "Payment Term",
        "rs.payment.plan":                    "Payment Plan",
        "rs.reservation":                     "Reservation",
        "rs.contract":                        "Contract",
        "rs.account.payment":                 "RS Payment",
        "rs.account.payment.cash":            "RS Cash Payment",
        "rs.account.payment.installment":     "RS Payment Installment",
        "rs.account.payment.installment.line":"RS Payment Installment Line",
    }

    for model, flds in all_fields.items():
        print(f"\n  Model: {model}")
        found_links = False
        for fname, meta in sorted(flds.items()):
            rel = meta.get("relation") or ""
            if rel in relations_of_interest:
                found_links = True
                ftype = meta.get("type", "")
                label = meta.get("string", "")
                desc  = relations_of_interest[rel]
                print(f"    {fname:<40}  {ftype:<12} -> {rel:<45}  [{desc}]  '{label}'")
        if not found_links:
            print("    (no links to key models found)")


# ── SECTION 6: Sample Records + Currency/Sign Analysis ───────────────────────

def section6_samples_and_currency(client, uid,
                                   all_fields: dict[str, dict],
                                   counts: dict[str, int]):
    print(f"\n{SEP}")
    print("  SECTION 6: Sample Records + Balance Field / Currency / Sign Analysis")
    print(SEP)
    print(f"\n  Context: rs.installment uses {RS_INSTALLMENT_CURRENCY_CONTEXT}.")
    print("  We verify whether the reconcile balance field is monetary, has a")
    print("  currency_id, and whether that currency is EGP — required for a")
    print("  correct sum of (installments_paid + reconcile_balance) per §15.\n")

    if not all_fields:
        print("  No models with records — skipping sample analysis.")
        return {}

    # Pick primary candidate: model with most records (most likely to be the wallet).
    primary = max(all_fields.keys(), key=lambda m: counts.get(m, 0))
    primary_count = counts.get(primary, 0)
    print(f"  Primary candidate (highest record count): {primary}  ({primary_count} records)")

    flds = all_fields[primary]

    # ── Identify balance field candidates ──
    monetary_fields = [fn for fn, m in flds.items() if m.get("type") == "monetary"]
    float_fields    = [fn for fn, m in flds.items() if m.get("type") == "float"]
    currency_fields = [
        fn for fn, m in flds.items()
        if m.get("type") == "many2one" and m.get("relation") == "res.currency"
    ]

    print(f"\n  Monetary fields on {primary}: {monetary_fields or 'NONE'}")
    print(f"  Float fields on {primary}:    {float_fields or 'NONE'}")
    print(f"  Currency many2one fields:     {currency_fields or 'NONE'}")

    # Fields to fetch for sample records — balance candidates + partner + state + currency
    fetch = ["id"]
    for fn in monetary_fields:
        fetch.append(fn)
    for fn in float_fields:
        fetch.append(fn)
    for fn in currency_fields:
        fetch.append(fn)
    for fn in ("state", "status", "partner_id", "currency_id"):
        if fn in flds and fn not in fetch:
            fetch.append(fn)

    fetch = list(dict.fromkeys(fetch))  # deduplicate, preserve order

    print(f"\n  Fetching 3 sample records (fields: {fetch}) ...")

    try:
        rows = do_search_read(client, uid, primary, [], fetch, limit=3, order="id asc")
    except Exception as exc:
        print(f"  search_read failed: {exc}")
        return {}

    print(f"\n  Sample records (sanitized — PII fields redacted):\n")
    for i, raw in enumerate(rows, 1):
        rec = sanitize(raw)
        print(f"  Record {i}:")
        for k, v in rec.items():
            print(f"    {k:<40}  {v}")
        print()

    # ── Currency analysis ──
    print(f"{SEP2}")
    print("  Balance field analysis:")
    print(SEP2)

    if not monetary_fields and not float_fields:
        print("\n  !! No monetary or float fields found on primary candidate.")
        print("     Balance may be stored differently, or this may not be the wallet model.")
        return {"primary": primary, "balance_type": None, "currency_match": None}

    # Determine type
    if monetary_fields:
        print(f"\n  Balance field type: MONETARY (field(s): {monetary_fields})")
        print("  Monetary fields in Odoo are paired with a currency_id.")
    elif float_fields:
        print(f"\n  Balance field type: FLOAT (field(s): {float_fields})")
        print("  Float fields are NOT currency-aware — no currency_id pairing.")

    # Currency: read from sample records
    currencies_seen = set()
    for raw in rows:
        if "currency_id" in raw and raw["currency_id"]:
            # currency_id is a many2one — Odoo returns [id, display_name]
            if isinstance(raw["currency_id"], (list, tuple)):
                currencies_seen.add(raw["currency_id"][1])
            else:
                currencies_seen.add(str(raw["currency_id"]))

    if currencies_seen:
        print(f"\n  Currency values in samples: {currencies_seen}")
        if currencies_seen == {"EGP"}:
            print("  MATCH: currency is EGP — consistent with rs.installment. [HOMOGENEOUS]")
        else:
            print("  !! MISMATCH or multiple currencies found.")
            print("     Currency homogeneity with rs.installment is NOT confirmed.")
            print("     This is an open question for Section 7.")
    elif currency_fields:
        print("\n  currency_id field exists but all 3 samples have no value — inconclusive.")
    else:
        print("\n  No currency_id field found.")
        if monetary_fields:
            print("  Monetary field without explicit currency_id — Odoo may use company currency.")
        if float_fields and not monetary_fields:
            print("  Float field: currency homogeneity with rs.installment cannot be confirmed.")

    # Sign analysis
    print(f"\n  Sign analysis (positive = 'funds held for customer'; negative = ambiguous):")
    balance_field_names = monetary_fields + float_fields
    for raw in rows:
        for fn in balance_field_names:
            val = raw.get(fn)
            if val is not None:
                sign = "positive" if val > 0 else ("zero" if val == 0 else "NEGATIVE")
                print(f"    record id={raw.get('id')}  {fn}={val}  [{sign}]")

    # Attempt aggregate: min, max, count of negative values
    if balance_field_names:
        primary_balance = balance_field_names[0]
        print(f"\n  Aggregate check on '{primary_balance}':")
        try:
            neg_count = do_search_count(client, uid, primary, [(primary_balance, "<", 0)])
            zero_count = do_search_count(client, uid, primary, [(primary_balance, "=", 0)])
            pos_count  = do_search_count(client, uid, primary, [(primary_balance, ">", 0)])
            print(f"    positive ({primary_balance} > 0):  {pos_count}")
            print(f"    zero     ({primary_balance} = 0):  {zero_count}")
            print(f"    negative ({primary_balance} < 0):  {neg_count}")
            if neg_count > 0:
                print("    !! Negative values exist — balance can represent a debit, not just credit.")
            else:
                print("    OK: no negative values found — balance is always credit (funds held).")
        except Exception as exc:
            print(f"    search_count for sign analysis failed: {exc}")

    return {
        "primary": primary,
        "balance_type": "monetary" if monetary_fields else ("float" if float_fields else None),
        "currency_fields": currency_fields,
        "currencies_seen": list(currencies_seen),
    }


# ── SECTION 7: Open Questions Gate ───────────────────────────────────────────

def section7_open_questions(counts: dict[str, int],
                             all_fields: dict[str, dict],
                             analysis: dict):
    print(f"\n{SEP}")
    print("  SECTION 7: Open Questions — items requiring Khaled review")
    print(SEP)

    oqs = []

    # OQ1 — if no model has records
    non_zero = {m: c for m, c in counts.items() if c > 0}
    if not non_zero:
        oqs.append(
            "OQ1: No candidate model has records. The reconcile wallet may live under "
            "a model name not matching 'reconcile', 'wallet', or 'balance', and not "
            "under rs.account.*. A wider keyword sweep or direct Odoo UI inspection "
            "of the RS Accounting app is required."
        )

    # OQ2 — multiple non-zero models
    if len(non_zero) > 1:
        names = ", ".join(sorted(non_zero.keys()))
        oqs.append(
            f"OQ2: Multiple models have records ({names}). "
            "It is unclear which is the primary wallet model and which are line/request "
            "tables. Khaled should confirm the primary model by navigating to it in the "
            "Odoo UI (RS Accounting → Reconcile menu, if one exists)."
        )

    # OQ3 — balance field type
    bt = analysis.get("balance_type")
    if bt == "float":
        oqs.append(
            "OQ3: The balance field is type FLOAT, not MONETARY. "
            "Float fields are not currency-aware in Odoo. Currency homogeneity with "
            "rs.installment (EGP) cannot be confirmed from the schema alone. "
            "Impact: the planned sum (installments_paid + reconcile_balance) may silently "
            "mix currencies if La Verde ever operates in multiple currencies. "
            "Khaled must confirm the company currency and whether multi-currency is used."
        )
    elif bt is None and non_zero:
        oqs.append(
            "OQ3: No monetary or float fields found on the primary candidate. "
            "The balance may be stored as an integer, char, or on a related model. "
            "The field backing the wallet balance is unconfirmed — Module 3 cannot proceed."
        )

    # OQ4 — currency mismatch
    currencies = analysis.get("currencies_seen", [])
    if currencies and set(currencies) != {"EGP"}:
        oqs.append(
            f"OQ4: Currency in sample records is not EGP (found: {currencies}). "
            "Currency homogeneity with rs.installment is NOT confirmed. "
            "The sum (installments_paid + reconcile_balance) may be in different currencies. "
            "This must be resolved before any KPI that combines both values."
        )

    # OQ5 — no state field found
    primary = analysis.get("primary")
    if primary and primary in all_fields:
        if "state" not in all_fields[primary] and "status" not in all_fields[primary]:
            oqs.append(
                "OQ5: No 'state' or 'status' field found on the primary candidate. "
                "The reconcile model may be stateless (always active) or the lifecycle "
                "may be managed on a related model. "
                "Implications: a 'current balance' query may simply be search_count/sum "
                "without a state filter, but this is unconfirmed."
            )

    # OQ6 — Down Payment linkage unclear
    if primary and primary in all_fields:
        flds = all_fields[primary]
        has_plan_link = any(
            (flds[fn].get("relation") or "") in (
                "rs.payment.term", "rs.payment.plan", "rs.installment"
            )
            for fn in flds
        )
        if not has_plan_link:
            oqs.append(
                "OQ6: No direct field linking the reconcile record to rs.payment.term, "
                "rs.payment.plan, or rs.installment was found. "
                "§15 states the reconcile balance is applied toward the Down Payment of "
                "a new plan. If this application does not create a link field on the "
                "reconcile model itself, the application event may live elsewhere "
                "(e.g., on rs.account.payment or rs.payment.term). "
                "This linkage is required for Module 3 to trace applied reconcile balances."
            )

    if oqs:
        print()
        for q in oqs:
            print(f"  {q}\n")
    else:
        print("\n  No blocking open questions identified.")

    print(f"{SEP2}")
    print("  GATE — STOP")
    print(SEP2)
    print("""
  Discovery output is printed above and saved to the .txt file.
  No feature code has been written. No Module 3 design has been assumed.

  ACTION REQUIRED from Khaled before any Module 3 work begins:
    1. Review the candidate models and record counts (Section 2).
    2. Confirm which model is the primary wallet model (OQ2 if raised).
    3. Confirm the balance field name and that it is EGP-denominated (OQ3/OQ4).
    4. Confirm whether the Down Payment linkage is on this model or elsewhere (OQ6).
    5. Review any additional open questions listed above.

  Only after Khaled confirms the model, balance field, and currency alignment
  will Module 3 design and implementation begin.
""")


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    output_path = Path(__file__).parent / f"discover_reconcile_phase3_{TODAY}.txt"
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
        print("  Phase 3 Discovery: Reconcile / Customer Wallet Model")
        print(f"  Run date:   {TODAY}")
        print(f"  Odoo URL:   {ODOO_URL}")
        print(f"  Constraint: READ-ONLY (ALLOWED_METHODS = {sorted(ALLOWED_METHODS)})")
        print(f"  PII policy: partner/name/email/phone fields redacted in samples")
        print(f"  Cost:       $0.00 (no OpenAI calls)")
        print(SEP)

        with httpx.Client() as client:
            uid = connect(client)

            candidates  = section1_broad_model_scan(client, uid)
            counts      = section2_record_counts(client, uid, candidates)
            all_fields  = section3_fields_inventory(client, uid, counts)
            section4_state_machine(client, uid, all_fields, counts)
            section5_linkage(all_fields)
            analysis    = section6_samples_and_currency(client, uid, all_fields, counts)
            section7_open_questions(counts, all_fields, analysis)

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
