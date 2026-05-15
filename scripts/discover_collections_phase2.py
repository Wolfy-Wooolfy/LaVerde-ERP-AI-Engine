"""
discover_collections_phase2.py — Module 2 Phase 2 Discovery
READ-ONLY: search, search_read, read, search_count, read_group, fields_get ONLY.
No create/write/unlink. No OpenAI. AI cost = $0.00.

Resolves the 8 Phase 2 dependencies from docs/MODULE_2_MVP_DESIGN.md §7.
Strategic decisions from §8 applied as fixed facts (Q1–Q5 resolved).

Run from any directory:
    python scripts/discover_collections_phase2.py
"""

import sys
import os
import uuid
from datetime import date, datetime
from io import StringIO
from pathlib import Path

import httpx
from dotenv import load_dotenv

# .env is at the project root (parent of scripts/)
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

# ── READ-ONLY ENFORCEMENT ─────────────────────────────────────────────────────
# execute() raises immediately if method is not in this set.
ALLOWED_METHODS = frozenset({
    "search", "search_read", "search_count",
    "read", "read_group", "fields_get",
})

# ── RPC BUDGET ────────────────────────────────────────────────────────────────
BUDGET_CEILING = 200
_budget = {"used": 0, "warned": False}  # "warned" ensures one-time chatter at -10


def budget_tick(label=""):
    _budget["used"] += 1
    remaining = BUDGET_CEILING - _budget["used"]
    if remaining <= 10 and not _budget["warned"]:
        _budget["warned"] = True
        print(
            f"  !! BUDGET WARNING: {_budget['used']}/{BUDGET_CEILING} RPCs used. "
            f"{remaining} remaining. [at call: {label}]"
        )
    if _budget["used"] > BUDGET_CEILING:
        raise RuntimeError(
            f"RPC budget ceiling ({BUDGET_CEILING}) exceeded. "
            f"Stopping cleanly. Last attempted call: {label}"
        )


# ── 2026-05-14 SNAPSHOT CONSTANTS (Business Context §9) ──────────────────────
SNAPSHOT_ALL = {
    "amount":                      6_123_549_625.23,
    "paid_amount":                 3_491_180_448.95,
    "x_studio_actual_paid_amount": 2_970_724_764.85,
    "due_amount":                  2_632_369_176.28,
    "total_due_amount":            3_152_824_860.38,
}
SNAPSHOT_LATE = {
    "amount":                        373_147_294.00,
    "paid_amount":                    60_542_414.60,
    "x_studio_actual_paid_amount":    60_542_414.60,
    "due_amount":                    312_604_879.40,
    "total_due_amount":              312_604_879.40,
}
# paid_amount − x_studio_actual_paid_amount from 2026-05-14 snapshot
PENDING_CHECK_EXPOSURE_BASELINE = 520_455_684.10

TOLERANCE_EGP = 1.00  # ±1 EGP pass/fail threshold

ODOO_URL  = os.environ["ODOO_URL"].rstrip("/") + "/jsonrpc"
ODOO_DB   = os.environ["ODOO_DB"]
ODOO_USER = os.environ["ODOO_USERNAME"]
ODOO_KEY  = os.environ["ODOO_API_KEY"]

TODAY = date.today().isoformat()  # used in Late domain candidates (S2)

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
    budget_tick(f"{model}.{method}")
    return rpc(client, "object", "execute_kw",
               [ODOO_DB, uid, ODOO_KEY, model, method, args, kwargs or {}])


def search_read(client, uid, model, domain, fields, limit=None, order=None):
    kw = {"fields": fields}
    if limit is not None:
        kw["limit"] = limit
    if order:
        kw["order"] = order
    return execute(client, uid, model, "search_read", [domain], kw)


def search_count(client, uid, model, domain):
    return execute(client, uid, model, "search_count", [domain])


def fields_get(client, uid, model):
    return execute(client, uid, model, "fields_get", [],
                   {"attributes": ["string", "type", "relation", "help", "required"]})


def read_group(client, uid, model, domain, fields, groupby, limit=None):
    kw = {"lazy": False}
    if limit is not None:
        kw["limit"] = limit
    return execute(client, uid, model, "read_group",
                   [domain, fields, groupby], kw)


# ── AGGREGATION WITH FALLBACK ─────────────────────────────────────────────────

def aggregate_totals(client, uid, model, domain, agg_fields):
    """
    Sum agg_fields over domain via read_group(groupby=[]).
    Falls back to groupby=['id'] if Odoo rejects an empty groupby,
    logging record count and elapsed time per the approved A5 requirement.
    Returns {field_name: float}.
    """
    agg_specs = [f"{f}:sum" for f in agg_fields]
    try:
        rows = read_group(client, uid, model, domain, agg_specs, [])
        if rows:
            return {f: (rows[0].get(f) or 0.0) for f in agg_fields}
        return {f: 0.0 for f in agg_fields}
    except Exception as primary_err:
        print(f"  [FALLBACK] read_group(groupby=[]) failed: {primary_err}")
        cnt = safe_count(client, uid, model, domain)
        print(f"  [FALLBACK] Record count in domain: {cnt}")
        print(f"  [FALLBACK] Attempting groupby=['id'] — may be slow for {cnt} records...")
        t0 = datetime.now()
        rows = read_group(client, uid, model, domain, agg_specs, ["id"])
        elapsed = (datetime.now() - t0).total_seconds()
        print(f"  [FALLBACK] groupby=['id'] returned {len(rows)} rows in {elapsed:.1f}s")
        totals = {f: 0.0 for f in agg_fields}
        for row in rows:
            for f in agg_fields:
                totals[f] += row.get(f) or 0.0
        return totals


def compare_to_snapshot(label, computed, snapshot):
    """Print pass/fail per field. Returns True if all pass."""
    print(f"\n  Cross-check vs 2026-05-14 snapshot ({label}):")
    print(f"  {'FIELD':<45} {'COMPUTED':>20} {'SNAPSHOT':>20} {'DELTA':>14}  RESULT")
    print(f"  {'-'*45} {'-'*20} {'-'*20} {'-'*14}  {'-'*6}")
    all_pass = True
    for field, expected in snapshot.items():
        actual = computed.get(field, 0.0)
        delta = actual - expected
        result = "PASS" if abs(delta) <= TOLERANCE_EGP else "FAIL"
        if result == "FAIL":
            all_pass = False
        print(f"  {field:<45} {actual:>20,.2f} {expected:>20,.2f} {delta:>+14,.2f}  {result}")
    return all_pass


# ── OUTPUT HELPERS ────────────────────────────────────────────────────────────

def header(n, title):
    print(f"\n{SEP}")
    print(f"  SECTION {n}: {title}")
    print(SEP)


def subheader(title):
    print(f"\n  --- {title} ---")


def sanitize(value, field_name=""):
    """Sanitize PII before printing. Identical to Phase 1 version — no changes."""
    if value is None or value is False:
        return value
    sensitive_fields = {
        "name", "partner_name", "customer_name", "display_name",
        "phone", "mobile", "email", "vat", "id_number",
        "street", "street2", "city",
    }
    field_lower = field_name.lower()
    if any(s in field_lower for s in sensitive_fields):
        if isinstance(value, str) and value:
            return f"[REDACTED:{field_name}]"
        if isinstance(value, list) and len(value) == 2:
            return [value[0], f"[REDACTED:{field_name}]"]
    return value


# ── MODEL HELPERS (same pattern as Phase 1) ───────────────────────────────────

def safe_count(client, uid, model, domain=None):
    try:
        return search_count(client, uid, model, domain or [])
    except Exception as e:
        return f"ERROR: {e}"


def try_fields_get(client, uid, model):
    try:
        return fields_get(client, uid, model)
    except Exception as e:
        print(f"    fields_get failed: {e}")
        return {}


def try_read_group_by(client, uid, model, groupby_field, label):
    subheader(f"read_group by {groupby_field} ({label})")
    try:
        rows = read_group(client, uid, model, [], ["__count"], [groupby_field])
        for r in rows:
            val = r.get(groupby_field, "?")
            cnt = r.get("__count", 0)
            print(f"    {str(val):<45} {cnt:>8}")
    except Exception as e:
        print(f"    Could not group by {groupby_field}: {e}")


# ── AUTHENTICATION ────────────────────────────────────────────────────────────

def connect():
    client = httpx.Client()
    print("\n[AUTH] Authenticating...")
    uid = rpc(client, "common", "authenticate",
              [ODOO_DB, ODOO_USER, ODOO_KEY, {}])
    if not uid:
        raise RuntimeError("Auth failed — check .env credentials")
    print(f"  OK uid={uid}")
    return client, uid


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 1 — Date Fields on rs.installment
# Goal: identify the due date field and payment posting date field.
# Approach: fields_get filtered by name patterns; search_read 3 sample records.
# Resolves: Dependency #2 (date field names); informs Dependency #3 denominator.
# ═════════════════════════════════════════════════════════════════════════════

# Prompt-specified patterns: "date", "due", "payment", "post"
# Added: "schedule" (installment schedule dates), "maturity" (date_maturity is
# the standard Odoo receivables due-date field name in RS Accounting context)
DATE_FIELD_PATTERNS = ("date", "due", "payment", "post", "schedule", "maturity")


def section1_date_fields(client, uid):
    header(1, "Date Fields on rs.installment")
    print(f"  Filter patterns applied to field names: {DATE_FIELD_PATTERNS}")
    print("  Also including all fields whose Odoo type is 'date' or 'datetime'.")
    print(f"  Today (used as boundary in S2 domain candidates): {TODAY}")

    flds = try_fields_get(client, uid, "rs.installment")
    if not flds:
        print("  ERROR: fields_get returned nothing — date field discovery cannot proceed.")
        return [], {}

    # Two-pass filter: type is date/datetime OR name matches a pattern
    date_candidates = {
        name: meta for name, meta in flds.items()
        if (
            meta.get("type") in ("date", "datetime")
            or any(p in name.lower() for p in DATE_FIELD_PATTERNS)
        )
        and not name.startswith("activity_")
        and not name.startswith("message_")
    }

    print(f"\n  Found {len(date_candidates)} date-candidate fields:\n")
    print(f"  {'FIELD':<45} {'TYPE':<12}  LABEL")
    print(f"  {'-'*45} {'-'*12}  {'-'*40}")
    for name, meta in sorted(date_candidates.items()):
        print(f"  {name:<45} {meta.get('type', ''):<12}  {meta.get('string', '')}")

    # Sample 3 records: ONLY date-candidate fields + safe structural anchors.
    # partner_id is deliberately excluded — returns [id, "Customer Name"] (PII).
    safe_sample_fields = sorted(date_candidates.keys()) + [
        "state", "payment_state", "amount",
        "project_id", "installment_type_id",
    ]
    subheader("3 sample records — date-candidate fields only (partner_id excluded)")
    try:
        samples = search_read(
            client, uid, "rs.installment", [],
            list(dict.fromkeys(safe_sample_fields)),  # dedup, preserve order
            limit=3,
        )
        for i, rec in enumerate(samples, 1):
            print(f"\n  --- Sample {i} ---")
            for k, v in rec.items():
                print(f"    {k:<45} = {sanitize(v, k)}")
    except Exception as e:
        print(f"  search_read failed: {e}")

    # Classify candidates for downstream sections
    due_hints = [n for n in date_candidates
                 if any(p in n.lower() for p in
                        ("due_date", "date_due", "maturity", "schedule"))]
    payment_hints = [n for n in date_candidates
                     if any(p in n.lower() for p in ("payment", "post", "paid"))]
    plain_date = ["date"] if "date" in date_candidates else []

    print(f"\n  Due-date candidates   : {due_hints or plain_date or ['NONE FOUND']}")
    print(f"  Payment/posting-date  : {payment_hints or plain_date or ['NONE FOUND']}")
    print(f"  Ambiguous ('date')    : {plain_date}")

    return list(date_candidates.keys()), flds


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 2 — "Late" Installment Domain
# Goal: identify the exact domain reproducing the Collections Mgmt Late view.
# Baselines: SUM(amount)=373,147,294.00  SUM(due_amount)=312,604,879.40
# Resolves: Dependency #1
# ═════════════════════════════════════════════════════════════════════════════

def section2_late_domain(client, uid, date_field_names, all_fields):
    header(2, '"Late" Installment Domain')
    print(f"  Baseline SUM(amount)    = {SNAPSHOT_LATE['amount']:>22,.2f} EGP")
    print(f"  Baseline SUM(due_amount)= {SNAPSHOT_LATE['due_amount']:>22,.2f} EGP")
    print(f"  Tolerance: ±{TOLERANCE_EGP:.2f} EGP")
    print(f"  Today (boundary for Candidates B/C): {TODAY}")

    # Pick best due-date field — priority order matches Odoo naming conventions
    due_field = None
    for candidate in ("due_date", "date_due", "date_maturity",
                      "schedule_date", "date_schedule", "date"):
        if candidate in date_field_names:
            due_field = candidate
            break
    if due_field is None and date_field_names:
        due_field = date_field_names[0]

    print(f"\n  Due-date field selected for domain tests: {due_field or 'NONE'}")
    if not due_field:
        print("  WARNING: No due-date field found. Candidates B and C will be omitted.")

    # Candidate D: boolean flag fields matching late/overdue patterns
    flag_fields = [
        n for n, meta in (all_fields or {}).items()
        if meta.get("type") == "boolean"
        and any(p in n.lower() for p in ("is_late", "overdue", "is_overdue", "late"))
    ]
    if flag_fields:
        print(f"  Boolean flag fields found (Candidate D): {flag_fields}")
    else:
        print("  Candidate D: no boolean flag field (is_late / overdue / "
              "is_overdue / late) found — skipped.")

    # Build candidate list
    agg_fields = ["amount", "due_amount", "paid_amount",
                  "x_studio_actual_paid_amount", "total_due_amount"]
    candidates = [
        ("A", [("payment_state", "in", ["unpaid", "partial"])]),
    ]
    if due_field:
        candidates += [
            ("B", [("payment_state", "in", ["unpaid", "partial"]),
                   (due_field, "<", TODAY)]),
            ("C", [("state", "=", "post"),
                   ("payment_state", "in", ["unpaid", "partial"]),
                   (due_field, "<", TODAY)]),
        ]
    for ff in flag_fields:
        candidates.append((f"D ({ff}=True)", [(ff, "=", True)]))

    results = []
    for label, domain in candidates:
        subheader(f"Candidate {label}")
        print(f"    Domain: {domain}")
        cnt = safe_count(client, uid, "rs.installment", domain)
        print(f"    search_count = {cnt}")
        try:
            totals = aggregate_totals(client, uid, "rs.installment", domain, agg_fields)
            amt_delta = totals["amount"]     - SNAPSHOT_LATE["amount"]
            due_delta = totals["due_amount"] - SNAPSHOT_LATE["due_amount"]
            amt_ok    = abs(amt_delta) <= TOLERANCE_EGP
            due_ok    = abs(due_delta) <= TOLERANCE_EGP
            print(f"    SUM(amount)    = {totals['amount']:>22,.2f}  "
                  f"delta={amt_delta:>+14,.2f}  {'MATCH' if amt_ok else 'NO MATCH'}")
            print(f"    SUM(due_amount)= {totals['due_amount']:>22,.2f}  "
                  f"delta={due_delta:>+14,.2f}  {'MATCH' if due_ok else 'NO MATCH'}")
            results.append({
                "label": label, "domain": domain, "count": cnt,
                "totals": totals,
                "amount_match": amt_ok, "due_match": due_ok,
                "both_match": amt_ok and due_ok,
                "amount_delta_abs": abs(amt_delta),
            })
        except Exception as e:
            print(f"    aggregate_totals failed: {e}")
            results.append({
                "label": label, "domain": domain, "count": cnt,
                "both_match": False, "amount_delta_abs": float("inf"),
            })

    # Determine winner — winning['label'] includes the flag field name if D wins
    winners = [r for r in results if r.get("both_match")]
    if not winners:
        best = min(
            (r for r in results if "totals" in r),
            key=lambda r: r["amount_delta_abs"],
            default=None,
        )
        print(f"\n  !! NO candidate matched both baselines within ±{TOLERANCE_EGP} EGP.")
        print("  !! STOPPING — data inconsistency. Report to Khaled before implementing.")
        if best:
            print(f"  !! Closest: Candidate {best['label']}  "
                  f"amount delta={best['amount_delta_abs']:+,.2f} EGP")
        return None, due_field

    winning = winners[0]
    ruled_out = [r["label"] for r in results if not r.get("both_match")]

    print(f"\n  WINNING DOMAIN: Candidate {winning['label']}")
    print(f"  Domain: {winning['domain']}")
    print(f"  Record count: {winning['count']}")
    if ruled_out:
        print(f"  Ruled out: {ruled_out}")

    subheader("Winning domain — full 5-column breakdown")
    for f, v in winning["totals"].items():
        snap_val = SNAPSHOT_LATE.get(f)
        snap_str = f"  (snapshot: {snap_val:,.2f})" if snap_val is not None else ""
        print(f"    {f:<45} {v:>22,.2f}{snap_str}")

    return winning["domain"], due_field


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 3 — Pending Check Exposure Reconciliation
# Goal: confirm SUM(paid_amount) − SUM(x_studio_actual_paid_amount) ≈ 520.5M
#       and compare to SUM(check_pending_amount).
# Resolves: Dependency #7
# ═════════════════════════════════════════════════════════════════════════════

def section3_pending_check(client, uid):
    header(3, "Pending Check Exposure Reconciliation")
    print(f"  Baseline (derived from snapshot): {PENDING_CHECK_EXPOSURE_BASELINE:,.2f} EGP")
    print("  Formula: SUM(paid_amount) − SUM(x_studio_actual_paid_amount)")

    agg_fields = [
        "paid_amount", "x_studio_actual_paid_amount",
        "check_pending_amount", "check_approved_amount",
    ]
    totals = aggregate_totals(client, uid, "rs.installment", [], agg_fields)

    derived = totals["paid_amount"] - totals["x_studio_actual_paid_amount"]
    native  = totals["check_pending_amount"]
    delta_vs_baseline    = derived - PENDING_CHECK_EXPOSURE_BASELINE
    delta_derived_native = derived - native

    print(f"\n  SUM(paid_amount)                  = {totals['paid_amount']:>22,.2f}")
    print(f"  SUM(x_studio_actual_paid_amount)  = {totals['x_studio_actual_paid_amount']:>22,.2f}")
    print(f"  SUM(check_pending_amount)         = {native:>22,.2f}")
    print(f"  SUM(check_approved_amount)        = {totals['check_approved_amount']:>22,.2f}")
    print(f"\n  DERIVED exposure (paid − actual)  = {derived:>22,.2f}")
    print(f"  vs. 2026-05-14 baseline           = {PENDING_CHECK_EXPOSURE_BASELINE:>22,.2f}")
    print(f"  Delta vs. baseline                = {delta_vs_baseline:>+22,.2f}  "
          + ("PASS" if abs(delta_vs_baseline) <= TOLERANCE_EGP
             else "NOTE: data likely changed since 2026-05-14 snapshot"))
    print(f"\n  DERIVED vs. check_pending_amount  = {delta_derived_native:>+22,.2f}")

    if abs(delta_derived_native) <= TOLERANCE_EGP:
        conclusion = "check_pending_amount == derived formula — canonical field confirmed"
        print(f"  CONCLUSION: {conclusion}")
        print("  => KPI 3 may use check_pending_amount directly (simpler query).")
    else:
        conclusion = (
            f"check_pending_amount DIFFERS from derived formula by "
            f"{delta_derived_native:+,.2f} EGP — use derived formula for KPI 3"
        )
        print(f"  CONCLUSION: {conclusion}")
        print("  => Stick with derived formula (paid_amount − x_studio_actual_paid_amount).")

    return totals, conclusion


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 4 — rs.installment.type Records
# Goal: inventory all 8 installment types (IDs, names, sequence).
# Resolves: Dependency #6
# ═════════════════════════════════════════════════════════════════════════════

def section4_installment_types(client, uid):
    header(4, "rs.installment.type Records")
    print("  Expected: 8 types per Business Context §7 "
          "(Down Payment, Regular, Maintenance, Admin Fees, Garage, Club, Facilities, Penalties)")

    flds = try_fields_get(client, uid, "rs.installment.type")
    if not flds:
        print("  ERROR: fields_get failed — cannot inventory types.")
        return []

    print(f"  Available fields: {sorted(flds.keys())}")

    try:
        records = search_read(client, uid, "rs.installment.type", [], list(flds.keys()))
    except Exception as e:
        print(f"  search_read failed: {e}")
        return []

    print(f"\n  Found {len(records)} records:\n")
    skip_keys = {"__last_update", "create_uid", "write_uid",
                 "create_date", "write_date", "message_ids",
                 "message_follower_ids", "activity_ids"}
    display_keys = [k for k in flds if k not in skip_keys]

    print(f"  {'ID':>5}  {'NAME':<45}  {'SEQ':>5}  OTHER")
    print(f"  {'-'*5}  {'-'*45}  {'-'*5}  {'-'*40}")
    for rec in records:
        rid      = rec.get("id", "?")
        name     = rec.get("name", "")
        seq      = rec.get("sequence", "")
        rest     = {k: v for k, v in rec.items()
                    if k in display_keys and k not in ("id", "name", "sequence")}
        # sanitize() called consistently; type names are not PII but rule is absolute
        safe_name = sanitize(name, "name") if isinstance(name, str) else name
        print(f"  {rid:>5}  {str(safe_name):<45}  {str(seq):>5}  {rest}")

    # Identify the Penalties type (Business Context §7 type #8)
    penalty_records = [
        r for r in records
        if any(p in str(r.get("name", "")).lower()
               for p in ("penalt", "غرامة", "غرام"))
    ]
    if penalty_records:
        pr = penalty_records[0]
        print(f"\n  Penalties type: ID={pr['id']}  name='{pr.get('name')}'")
    else:
        print("\n  WARNING: Penalties type not identified by name — review list above.")

    return records


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 5 — rs.structure.project Inventory
# Goal: confirm IDs and names of 3 projects.
# NOTE: Phase 1 queried rs.structure.project.type (wrong). This is correct.
# Resolves: Dependency #4
# ═════════════════════════════════════════════════════════════════════════════

def section5_projects(client, uid):
    header(5, "rs.structure.project Inventory")
    print("  Phase 1 error: queried rs.structure.project.type (1 record, wrong model).")
    print("  This section queries rs.structure.project directly.")
    print("  Expected: 3 projects — New Capital (id=1), Cassette (id=2), La puerta (id=3)")

    flds = try_fields_get(client, uid, "rs.structure.project")
    if not flds:
        print("  ERROR: fields_get failed.")
        return []

    display_fields = ["id", "name", "active"]
    for candidate in ("code", "sequence", "state"):
        if candidate in flds:
            display_fields.append(candidate)

    print(f"  Fields retrieved: {display_fields}")

    try:
        records = search_read(client, uid, "rs.structure.project", [], display_fields)
    except Exception as e:
        print(f"  search_read failed: {e}")
        return []

    print(f"\n  Found {len(records)} project record(s):\n")
    print(f"  {'ID':>5}  {'NAME':<40}  {'ACTIVE':>6}  OTHER")
    print(f"  {'-'*5}  {'-'*40}  {'-'*6}  {'-'*30}")
    for rec in records:
        rid       = rec.get("id", "?")
        name      = rec.get("name", "")
        active    = rec.get("active", "?")
        rest      = {k: v for k, v in rec.items() if k not in ("id", "name", "active")}
        safe_name = sanitize(name, "name") if isinstance(name, str) else name
        print(f"  {rid:>5}  {str(safe_name):<40}  {str(active):>6}  {rest}")

    expected_fragments = {
        "new capital": "New Capital",
        "cassette":    "Cassette",
        "la puerta":   "La puerta",
    }
    found_names_lower = [str(r.get("name", "")).lower() for r in records]
    print()
    for fragment, display in expected_fragments.items():
        matched = any(fragment in fn for fn in found_names_lower)
        print(f"  {'CONFIRMED' if matched else 'NOT FOUND'}: {display}")

    return records


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 6 — rs.account.payment.installment Basic Inventory
# Goal: confirm payment posting date field. Scoped to date-field need only.
# The payment posting date drives KPI 6 (6-Month Trend) per resolved Q4.
# Resolves: Dependency #5 (scoped)
# ═════════════════════════════════════════════════════════════════════════════

# Priority-ordered list for picking the payment posting date field.
# Most specific (unambiguous) first; plain "date" last as fallback only.
PAYMENT_POSTING_DATE_PRIORITY = (
    "date_posted", "posting_date", "post_date",
    "payment_date", "date_payment",
    "date_done", "date_validate", "date_confirmed",
    "effective_date", "value_date",
    "date",
)

# Pattern set for the fields_get filter (superset of priority list)
PAYMENT_DATE_FIELD_PATTERNS = (
    "date", "payment_date", "date_payment", "date_posted", "posting_date",
    "post_date", "value_date", "effective_date", "date_done",
    "date_validate", "date_confirmed",
)


def section6_payment_installment(client, uid, installment_date_fields):
    """
    installment_date_fields: list of date-candidate field names returned by S1.
    Used in A4 to programmatically determine whether the posting date field
    is also present on rs.installment (Note 3).
    """
    header(6, "rs.account.payment.installment Basic Inventory")
    print("  Scope: confirm payment posting date field only (per Dependency #5 scope).")
    print("  Q4 resolved: payment posting date (cash inflow) drives KPI 6 trend axis.")
    print(f"  Date field priority order: {PAYMENT_POSTING_DATE_PRIORITY}")

    cnt = safe_count(client, uid, "rs.account.payment.installment")
    print(f"\n  Total records: {cnt}")

    flds = try_fields_get(client, uid, "rs.account.payment.installment")
    if not flds:
        print("  ERROR: fields_get failed.")
        return [], None

    date_candidates = {
        name: meta for name, meta in flds.items()
        if (
            meta.get("type") in ("date", "datetime")
            or any(p in name.lower() for p in PAYMENT_DATE_FIELD_PATTERNS)
        )
        and not name.startswith("activity_")
        and not name.startswith("message_")
    }

    print(f"\n  Date-candidate fields on rs.account.payment.installment "
          f"({len(date_candidates)} found):\n")
    print(f"  {'FIELD':<45} {'TYPE':<12}  LABEL")
    print(f"  {'-'*45} {'-'*12}  {'-'*40}")
    for name, meta in sorted(date_candidates.items()):
        print(f"  {name:<45} {meta.get('type', ''):<12}  {meta.get('string', '')}")

    if "state" in flds:
        try_read_group_by(client, uid, "rs.account.payment.installment",
                          "state", "state distribution")

    # Sample 3 records — date fields + state + installment link only. No PII.
    sample_fields = sorted(date_candidates.keys()) + ["state", "installment_id"]
    for amount_candidate in ("amount", "paid_amount", "total_amount", "amount_total"):
        if amount_candidate in flds:
            sample_fields.append(amount_candidate)
            break

    subheader("3 sample records — date fields + state + installment_id (no PII)")
    try:
        samples = search_read(
            client, uid, "rs.account.payment.installment", [],
            list(dict.fromkeys(sample_fields)),
            limit=3,
        )
        for i, rec in enumerate(samples, 1):
            print(f"\n  --- Sample {i} ---")
            for k, v in rec.items():
                print(f"    {k:<45} = {sanitize(v, k)}")
    except Exception as e:
        print(f"  search_read failed: {e}")

    # Pick best posting date field using priority order
    posting_date_field = None
    for candidate in PAYMENT_POSTING_DATE_PRIORITY:
        if candidate in date_candidates:
            posting_date_field = candidate
            break

    print(f"\n  Best payment posting date field: {posting_date_field or 'NONE IDENTIFIED'}")

    # ── Payment Posting Date Field Location (Note 3 — programmatic cross-check) ──
    subheader("Payment Posting Date Field Location (A4 follow-up)")
    if posting_date_field:
        print(f"  Field '{posting_date_field}' confirmed on rs.account.payment.installment.")
        is_on_installment = posting_date_field in (installment_date_fields or [])
        if is_on_installment:
            print(f"  Field '{posting_date_field}' is ALSO present on rs.installment.")
            print("  => KPI 6 can filter rs.installment directly. No join needed.")
            print("  => KPI 4 payment-date period filter also uses rs.installment directly.")
        else:
            print(f"  Field '{posting_date_field}' is NOT on rs.installment.")
            print()
            print("  IMPORTANT — implementation impact on KPI 4 and KPI 6:")
            print("  KPI 6 (6-Month Trend) and the payment-date period of KPI 4")
            print("  (Collection Rate) will require a join:")
            print()
            print("    rs.installment.payment_line")
            print("      → rs.account.payment.installment.line  (one2many, per Phase 1 §3)")
            print("      → rs.account.payment.installment")
            print(f"         .{posting_date_field}  ← payment posting date")
            print()
            print("  This adds implementation complexity: a two-step search_read rather")
            print("  than a direct domain filter on rs.installment.")
            print("  Flag this in MODULE_2_MVP_DESIGN.md if a follow-up commit is needed.")
    else:
        print("  No clear payment posting date field found on rs.account.payment.installment.")
        print("  Escalate to Khaled before implementing KPI 4 and KPI 6.")

    return list(date_candidates.keys()), posting_date_field


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 7 — Late + Pending Check Overlap
# Goal: count installments that are both Late AND have check_pending_amount > 0.
# Business Context §15 open item: where do late installments with pending checks go?
# Resolves: Dependency #8
# ═════════════════════════════════════════════════════════════════════════════

def section7_overlap(client, uid, late_domain):
    header(7, "Late + Pending Check Overlap")
    print("  Business Context §15 open item: late installments with pending checks.")

    if not late_domain:
        print("  SKIPPED: Section 2 found no winning Late domain. Cannot compute overlap.")
        return 0

    overlap_domain = list(late_domain) + [("check_pending_amount", ">", 0)]
    print(f"  Overlap domain: {overlap_domain}")

    cnt = safe_count(client, uid, "rs.installment", overlap_domain)
    print(f"\n  Count (Late AND check_pending_amount > 0): {cnt}")

    if isinstance(cnt, int) and cnt > 0:
        totals = aggregate_totals(
            client, uid, "rs.installment", overlap_domain,
            ["amount", "due_amount", "check_pending_amount"],
        )
        print(f"  SUM(amount)               = {totals['amount']:>22,.2f}")
        print(f"  SUM(due_amount)           = {totals['due_amount']:>22,.2f}")
        print(f"  SUM(check_pending_amount) = {totals['check_pending_amount']:>22,.2f}")
        pct = 0.0
        if SNAPSHOT_LATE["due_amount"] > 0:
            pct = totals["due_amount"] / SNAPSHOT_LATE["due_amount"] * 100
        print(f"\n  Overlap due_amount as % of Late baseline "
              f"({SNAPSHOT_LATE['due_amount']:,.2f}): {pct:.2f}%")
        if pct < 1.0:
            print("  SIGNIFICANCE: Negligible (< 1% of Late due amount).")
        elif pct < 5.0:
            print("  SIGNIFICANCE: Minor (1–5% of Late due amount). Note in doc.")
        else:
            print("  SIGNIFICANCE: Material (> 5%). "
                  "Review impact on KPI 2 accuracy before implementing.")
    elif isinstance(cnt, int) and cnt == 0:
        print("  Overlap = 0: Late installments have no pending checks.")
        print("  Consistent with snapshot: Late Paid = Late Actual Paid (Business Context §9).")
    else:
        print(f"  search_count returned: {cnt}")

    return cnt if isinstance(cnt, int) else 0


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 8 — Cross-check: Total Reconciliation Against 2026-05-14 Snapshot
# Goal: confirm all 5 baselined columns pass; print 4 reference columns.
# Resolves: implicit validation that section queries return correct aggregates.
# ═════════════════════════════════════════════════════════════════════════════

# Reference columns: valuable for design but have no snapshot baseline
REFERENCE_FIELDS = [
    "x_studio_bank_collected_amount",
    "x_studio_executive_outstanding_amount",
    "check_pending_amount",
    "check_approved_amount",
]


def section8_crosscheck(client, uid):
    header(8, "Cross-check: Total Reconciliation Against 2026-05-14 Snapshot")
    print("  Aggregating all 9 columns over rs.installment with no domain.")
    print(f"  Group 1 (5 baselined columns): pass/fail within ±{TOLERANCE_EGP:.2f} EGP")
    print("  Group 2 (4 reference columns): totals only, no snapshot baseline")

    all_agg_fields = list(SNAPSHOT_ALL.keys()) + REFERENCE_FIELDS
    totals = aggregate_totals(client, uid, "rs.installment", [], all_agg_fields)

    subheader("Group 1 — Baselined columns (5): pass/fail")
    all_pass = compare_to_snapshot("All Installments", totals, SNAPSHOT_ALL)
    print(f"\n  Group 1 result: "
          f"{'ALL PASS' if all_pass else 'SOME FAILURES — investigate before implementing'}")

    subheader("Group 2 — Reference columns (4): totals only, no snapshot baseline")
    print(f"  {'FIELD':<50} {'TOTAL':>22}")
    print(f"  {'-'*50} {'-'*22}")
    for f in REFERENCE_FIELDS:
        val = totals.get(f, 0.0)
        print(f"  {f:<50} {val:>22,.2f}")
    print("\n  (no 2026-05-14 snapshot baseline available for Group 2 columns)")

    subheader("Reconciliation equation checks (Business Context §8)")
    eq1_lhs = totals.get("amount", 0.0)
    eq1_rhs = totals.get("paid_amount", 0.0) + totals.get("due_amount", 0.0)
    eq2_rhs = (totals.get("x_studio_actual_paid_amount", 0.0)
               + totals.get("total_due_amount", 0.0))
    print(f"  EQ1: amount == paid_amount + due_amount")
    print(f"       {eq1_lhs:,.2f} == {eq1_rhs:,.2f}  "
          f"delta={eq1_lhs - eq1_rhs:>+14,.2f}  "
          + ("PASS" if abs(eq1_lhs - eq1_rhs) <= TOLERANCE_EGP else "FAIL"))
    print(f"  EQ2: amount == x_studio_actual_paid_amount + total_due_amount")
    print(f"       {eq1_lhs:,.2f} == {eq2_rhs:,.2f}  "
          f"delta={eq1_lhs - eq2_rhs:>+14,.2f}  "
          + ("PASS" if abs(eq1_lhs - eq2_rhs) <= TOLERANCE_EGP else "FAIL"))

    return totals, all_pass


# ═════════════════════════════════════════════════════════════════════════════
# DEPENDENCY RESOLUTION SUMMARY
# ═════════════════════════════════════════════════════════════════════════════

def print_dependency_summary(
    late_domain, due_field, posting_date_field,
    type_records, project_records, crosscheck_pass,
    overlap_count, check_conclusion,
):
    print(f"\n{SEP}")
    print("  DEPENDENCY RESOLUTION SUMMARY")
    print(f"  Run date  : {datetime.now().isoformat()}")
    print(f"  Today     : {TODAY}")
    print(f"  RPC used  : {_budget['used']} / {BUDGET_CEILING}")
    print(f"  AI cost   : $0.00 (no OpenAI calls)")
    print(SEP)

    if posting_date_field:
        dep2_finding = (
            f"Due-date: {due_field or 'not on rs.installment'}; "
            f"Posting-date: {posting_date_field} on rs.account.payment.installment"
        )
    else:
        dep2_finding = (
            f"Due-date: {due_field or 'not found'}; "
            "Posting-date: not identified"
        )

    dep3_status  = "RESOLVED" if due_field else "PARTIAL"
    dep3_finding = (f"Denominator = installments with due_date in period. "
                    f"Field: {due_field or 'not confirmed'}")

    rows = [
        ("#1", "Late installment domain",
         "RESOLVED" if late_domain else "UNRESOLVED",
         f"Domain: {late_domain}" if late_domain else "No candidate matched both baselines"),
        ("#2", "Date field names on rs.installment",
         "RESOLVED" if (due_field and posting_date_field) else "PARTIAL",
         dep2_finding),
        ("#3", "Collection Rate denominator (Q1=Option a)",
         dep3_status, dep3_finding),
        ("#4", "rs.structure.project records",
         "RESOLVED" if project_records else "UNRESOLVED",
         f"{len(project_records)} project(s) fetched"
         if project_records else "search_read failed"),
        ("#5", "Payment model date field (scoped)",
         "RESOLVED" if posting_date_field else "PARTIAL",
         f"Posting-date: {posting_date_field or 'not identified'}"),
        ("#6", "rs.installment.type records",
         "RESOLVED" if type_records else "UNRESOLVED",
         f"{len(type_records)} type(s) fetched"
         if type_records else "search_read failed"),
        ("#7", "check_pending_amount vs derived formula",
         "RESOLVED",
         (check_conclusion or "see S3 output")[:80]),
        ("#8", "Late + pending check overlap",
         "RESOLVED",
         f"Overlap count: {overlap_count}"),
    ]

    print(f"\n  {'#':<4}  {'DEPENDENCY':<42}  {'STATUS':<12}  KEY FINDING")
    print(f"  {'-'*4}  {'-'*42}  {'-'*12}  {'-'*55}")
    for dep, name, status, finding in rows:
        print(f"  {dep:<4}  {name:<42}  {status:<12}  {finding[:55]}")

    print(f"\n  Snapshot cross-check (S8): "
          f"{'ALL PASS' if crosscheck_pass else 'FAILURES — see S8 output above'}")
    print(SEP)


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main():
    output_path = Path(__file__).parent / "discover_collections_phase2_output.txt"
    output_buffer = StringIO()

    class Tee:
        def __init__(self, *streams):
            self.streams = streams
        def write(self, data):
            for s in self.streams:
                s.write(data)
        def flush(self):
            for s in self.streams:
                s.flush()

    sys.stdout = Tee(sys.__stdout__, output_buffer)

    print(SEP)
    print("  Module 2 — Collections Discovery Phase 2")
    print(f"  Run at : {datetime.now().isoformat()}")
    print(f"  Today  : {TODAY}")
    print(f"  ALLOWED_METHODS: {sorted(ALLOWED_METHODS)}")
    print(f"  RPC budget ceiling: {BUDGET_CEILING}")
    print("  READ-ONLY. No writes. No OpenAI. AI cost = $0.00")
    print(SEP)

    # Result accumulators — populated section by section
    late_domain        = None
    due_field          = None
    posting_date_field = None
    type_records       = []
    project_records    = []
    crosscheck_pass    = False
    overlap_count      = 0
    check_conclusion   = "not computed"

    try:
        client, uid = connect()

        with client:
            date_field_names, all_fields = section1_date_fields(client, uid)

            late_domain, due_field = section2_late_domain(
                client, uid, date_field_names, all_fields
            )

            _, check_conclusion = section3_pending_check(client, uid)

            type_records = section4_installment_types(client, uid)

            project_records = section5_projects(client, uid)

            # Pass date_field_names from S1 for programmatic A4 cross-check (Note 3)
            _, posting_date_field = section6_payment_installment(
                client, uid, date_field_names
            )

            overlap_count = section7_overlap(client, uid, late_domain)

            _, crosscheck_pass = section8_crosscheck(client, uid)

            print_dependency_summary(
                late_domain, due_field, posting_date_field,
                type_records, project_records, crosscheck_pass,
                overlap_count, check_conclusion,
            )

    except Exception as e:
        print(f"\n!! FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        print_dependency_summary(
            late_domain, due_field, posting_date_field,
            type_records, project_records, crosscheck_pass,
            overlap_count, check_conclusion,
        )
    finally:
        sys.stdout = sys.__stdout__
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(output_buffer.getvalue())
        print(f"\n  Output saved to: {output_path}")
        print(f"  Total RPC calls: {_budget['used']}")


if __name__ == "__main__":
    main()
