"""
diagnose_ca_drilldown_anomaly.py — Customer Accounts drill-down HTTP 500 diagnosis.

READ-ONLY: search, search_read, search_count, read, read_group, fields_get ONLY.
No create/write/unlink. No OpenAI. AI cost = $0.00.

Sections:
  A — Partner resolution: ilike on full name "يوسف بدر شرهان دخيل",
      falling back to shorter tokens if zero matches.
  B — Reproduce the service aggregates exactly (base_all / late_domain /
      future_domain from drilldown_service.py lines 167-175). Print delta.
  C — due_amount + count grouped by payment_state (from RPC 2 results — no extra RPC).
  D — Unpaid/partial installments with date = False (null).
  E — payment_state NOT IN [unpaid,partial] AND due_amount != 0 — full rows.
  F — Portfolio-wide scan: row counts, distinct partner counts, first 10 names.
  G — Live endpoint probe: GET /api/v1/customer-accounts/customer/{partner_id}.
  H — Log grep: "Integrity assertion FAILED" in logs/.

RPC count: exactly 8 (RPCs 1-8, all direct Odoo JSON-RPC except section G).

AUTH EVIDENCE (verbatim sources):
  RPCs 1-8 (direct Odoo JSON-RPC — no FastAPI cache/rate-limiter contamination):
    discover_m3s6_drilldown.py lines 37-41:  ODOO_URL/DB/USERNAME/API_KEY env vars
    discover_m3s6_drilldown.py lines 59-74:  rpc() via POST /jsonrpc
    discover_m3s6_drilldown.py lines 77-84:  execute() via execute_kw
    discover_m3s6_drilldown.py lines 112-119: connect() via common/authenticate
  Endpoint probe (FastAPI HTTP Basic Auth):
    verify_kpi1_live.py line 37:  USERNAME = os.environ.get("VERIFY_USERNAME", "admin")
    verify_kpi1_live.py line 38:  PASSWORD = os.environ.get("VERIFY_PASSWORD", "password")
    verify_kpi1_live.py line 115: client.get(url, auth=(USERNAME, PASSWORD))
  today (Cairo-local):
    backend/modules/customer_accounts/services/cache.py line 26:
    datetime.now(ZoneInfo("Africa/Cairo")).date().isoformat()

Usage:
    python scripts/diagnose_ca_drilldown_anomaly.py [--url http://localhost:8000]
"""

import argparse
import io
import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

# Force UTF-8 stdout — Windows console defaults to cp1252
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Config: direct Odoo (discover_m3s6_drilldown.py lines 37-41 pattern) ─────
ODOO_URL  = os.environ["ODOO_URL"].rstrip("/") + "/jsonrpc"
ODOO_DB   = os.environ["ODOO_DB"]
ODOO_USER = os.environ["ODOO_USERNAME"]
ODOO_KEY  = os.environ["ODOO_API_KEY"]

# FastAPI HTTP Basic Auth (verify_kpi1_live.py lines 37-38 pattern)
BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")
FA_USER     = os.environ.get("VERIFY_USERNAME", "admin")
FA_PASS     = os.environ.get("VERIFY_PASSWORD", "password")

# today: exact same expression as cache.today_str() (cache.py line 26)
TODAY = datetime.now(ZoneInfo("Africa/Cairo")).date().isoformat()

PARTNER_NAME = "يوسف بدر شرهان دخيل"
_MODEL       = "rs.installment"
_PARTNER_MOD = "res.partner"

# READ-ONLY enforcement (discover_m3s6_drilldown.py pattern)
ALLOWED_METHODS = frozenset({
    "search", "search_read", "search_count",
    "read", "read_group", "fields_get",
})

SEP  = "=" * 72
SEP2 = "-" * 72
_ANOMALY_FIELDS = [
    "id", "date", "installment_type_id", "state", "payment_state",
    "amount", "paid_amount", "x_studio_actual_paid_amount", "due_amount",
]


# ── RPC core — verbatim from discover_m3s6_drilldown.py lines 59-84 ──────────

def rpc(client: httpx.Client, service: str, method: str, args: list):
    r = client.post(
        ODOO_URL,
        json={
            "jsonrpc": "2.0",
            "method":  "call",
            "id":      str(uuid.uuid4()),
            "params":  {"service": service, "method": method, "args": args},
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
            f"Method '{method}' not in ALLOWED_METHODS — read-only enforcement."
        )
    return rpc(client, "object", "execute_kw",
               [ODOO_DB, uid, ODOO_KEY, model, method, args, kwargs or {}])


def connect(client) -> int:
    uid = rpc(client, "common", "authenticate",
              [ODOO_DB, ODOO_USER, ODOO_KEY, {}])
    if not uid:
        raise RuntimeError("Odoo auth failed — check .env credentials")
    return uid


def do_search_read(client, uid, model, domain, fields, limit=None, order=None):
    kw: dict = {"fields": fields}
    if limit is not None:
        kw["limit"] = limit
    if order:
        kw["order"] = order
    return execute(client, uid, model, "search_read", [domain], kw)


def do_read_group(client, uid, model, domain, agg_fields, groupby):
    return execute(client, uid, model, "read_group",
                   [domain, agg_fields, groupby], {"lazy": False})


def _egp(val) -> float:
    return float(val) if val else 0.0


def _cnt(row: dict) -> int:
    return int(row.get("__count") or 0)


def _partner_name_str(raw) -> str:
    if isinstance(raw, (list, tuple)) and len(raw) > 1:
        return str(raw[1])
    return str(raw or "")


def _partner_id_int(raw) -> int:
    if isinstance(raw, (list, tuple)) and raw:
        return int(raw[0])
    return int(raw) if raw else 0


# ── SECTION A: Partner resolution ─────────────────────────────────────────────

def section_a_partner_resolve(client, uid) -> int:
    print(f"\n{SEP}")
    print("  SECTION A  Partner Resolution")
    print(SEP)
    print(f"  Target name: {PARTNER_NAME}")

    partner_id = None
    tokens = [
        PARTNER_NAME,
        "يوسف بدر شرهان",
        "يوسف بدر",
        "يوسف",
    ]
    for token in tokens:
        # RPC 1 (first successful token terminates the loop)
        rows = do_search_read(
            client, uid, _PARTNER_MOD,
            [("name", "ilike", token.strip())],
            ["id", "name"],
            limit=20,
        )
        print(f"\n  ilike '{token.strip()}' → {len(rows)} match(es):")
        for r in rows:
            print(f"    id={r['id']}  name={r['name']}")
        if rows and partner_id is None:
            partner_id = rows[0]["id"]
            print(f"  → Resolved partner_id={partner_id} (first match of '{token.strip()}')")
            if token == PARTNER_NAME:
                break   # full-name match found — no need for shorter tokens

    if partner_id is None:
        print("\n[FAIL]  No partner found for any token — cannot continue.")
        sys.exit(1)

    return partner_id


# ── SECTION B+C: Aggregates + delta + per-state breakdown ─────────────────────

def section_bc_aggregates(client, uid, partner_id: int) -> list:
    print(f"\n{SEP}")
    print("  SECTION B  Service Aggregate Reproduction")
    print("             (exact domains — drilldown_service.py lines 167-175)")
    print(SEP)

    base_all      = [("state", "=", "post"), ("partner_id", "=", partner_id)]
    unpaid_domain = base_all + [("payment_state", "in", ["unpaid", "partial"])]
    late_domain   = unpaid_domain + [("date", "<",  TODAY)]
    future_domain = unpaid_domain + [("date", ">=", TODAY)]

    print(f"\n  today (ZoneInfo Africa/Cairo) : {TODAY}")
    print(f"  base_all      : {base_all}")
    print(f"  late_domain   : {late_domain}")
    print(f"  future_domain : {future_domain}")

    # RPC 2: base_all grouped by payment_state → supplies both B (all_due) and C (breakdown)
    all_by_state = do_read_group(client, uid, _MODEL, base_all, ["due_amount"], ["payment_state"])
    # RPC 3: late aggregate
    late_rows    = do_read_group(client, uid, _MODEL, late_domain,   ["due_amount"], [])
    # RPC 4: future aggregate
    future_rows  = do_read_group(client, uid, _MODEL, future_domain, ["due_amount"], [])

    all_due    = sum(_egp(r.get("due_amount")) for r in all_by_state)
    late_due   = _egp((late_rows[0]   if late_rows   else {}).get("due_amount"))
    future_due = _egp((future_rows[0] if future_rows else {}).get("due_amount"))
    total_due  = late_due + future_due
    delta      = abs(total_due - all_due)

    print(f"\n  all_posted_due       : {all_due:>22,.4f} EGP")
    print(f"  late_due             : {late_due:>22,.4f} EGP")
    print(f"  future_due           : {future_due:>22,.4f} EGP")
    print(f"  late + future        : {total_due:>22,.4f} EGP")
    print(f"  delta (abs)          : {delta:>22,.4f} EGP")
    if delta >= 1.0:
        print(f"  [FAIL]  delta >= 1.0 EGP — ASSERTION WOULD FIRE in drilldown_service.py:270")
    else:
        print(f"  [PASS]  delta < 1.0 EGP — assertion would NOT fire")

    # SECTION C uses the same read_group result — no extra RPC
    print(f"\n{SEP}")
    print("  SECTION C  due_amount + count grouped by payment_state  (from RPC 2)")
    print(SEP)
    print(f"\n  {'payment_state':<30}  {'count':>8}  {'due_amount':>22}")
    print(f"  {'-'*30}  {'-'*8}  {'-'*22}")
    for row in sorted(all_by_state, key=lambda r: _egp(r.get("due_amount")), reverse=True):
        state = str(row.get("payment_state") or "(none)")
        print(f"  {state:<30}  {_cnt(row):>8}  {_egp(row.get('due_amount')):>22,.4f} EGP")

    return all_by_state


# ── SECTION D: Null-date unpaid/partial ───────────────────────────────────────

def section_d_null_date(client, uid, partner_id: int) -> None:
    print(f"\n{SEP}")
    print("  SECTION D  Unpaid/Partial installments with date = False (null)")
    print(SEP)

    base_all      = [("state", "=", "post"), ("partner_id", "=", partner_id)]
    unpaid_domain = base_all + [("payment_state", "in", ["unpaid", "partial"])]

    # RPC 5
    rows = do_search_read(
        client, uid, _MODEL,
        unpaid_domain + [("date", "=", False)],
        _ANOMALY_FIELDS,
    )
    print(f"\n  count: {len(rows)}")
    if rows:
        _print_anomaly_rows(rows)
    else:
        print("  (none)")


# ── SECTION E: Non-unpaid/partial with due_amount != 0 ────────────────────────

def section_e_wrong_state_nonzero_due(client, uid, partner_id: int) -> None:
    print(f"\n{SEP}")
    print("  SECTION E  payment_state NOT IN [unpaid,partial]  AND  due_amount != 0")
    print(SEP)

    base_all = [("state", "=", "post"), ("partner_id", "=", partner_id)]

    # RPC 6
    rows = do_search_read(
        client, uid, _MODEL,
        base_all + [
            ("payment_state", "not in", ["unpaid", "partial"]),
            ("due_amount",    "!=",      0),
        ],
        _ANOMALY_FIELDS,
    )
    print(f"\n  count: {len(rows)}")
    if rows:
        _print_anomaly_rows(rows)
    else:
        print("  (none)")


def _print_anomaly_rows(rows: list) -> None:
    hdr = (
        f"  {'id':>8}  {'date':<12}  {'type_id':>8}  {'state':<8}  "
        f"{'pmt_state':<14}  {'amount':>14}  {'paid_amt':>14}  "
        f"{'actual_paid':>14}  {'due_amount':>14}"
    )
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for r in rows:
        tid_raw = r.get("installment_type_id")
        tid_v   = tid_raw[0] if isinstance(tid_raw, (list, tuple)) and tid_raw else (tid_raw or "")
        print(
            f"  {r['id']:>8}  "
            f"{str(r.get('date') or 'NULL'):<12}  "
            f"{str(tid_v):>8}  "
            f"{str(r.get('state') or ''):<8}  "
            f"{str(r.get('payment_state') or ''):<14}  "
            f"{_egp(r.get('amount')):>14,.2f}  "
            f"{_egp(r.get('paid_amount')):>14,.2f}  "
            f"{_egp(r.get('x_studio_actual_paid_amount')):>14,.2f}  "
            f"{_egp(r.get('due_amount')):>14,.2f}"
        )


# ── SECTION F: Portfolio-wide scan ────────────────────────────────────────────

def section_f_portfolio_scan(client, uid) -> None:
    print(f"\n{SEP}")
    print("  SECTION F  Portfolio-Wide Scan")
    print(SEP)

    global_class_a = [
        ("state",         "=",      "post"),
        ("payment_state", "not in", ["unpaid", "partial"]),
        ("due_amount",    "!=",      0),
    ]
    global_class_b = [
        ("state",         "=",    "post"),
        ("payment_state", "in",   ["unpaid", "partial"]),
        ("date",          "=",    False),
    ]

    # RPC 7: class (a) grouped by partner_id + payment_state
    class_a_rows = do_read_group(
        client, uid, _MODEL, global_class_a, ["due_amount"],
        ["partner_id", "payment_state"],
    )

    # RPC 8: class (b) grouped by partner_id
    class_b_rows = do_read_group(
        client, uid, _MODEL, global_class_b, ["due_amount"],
        ["partner_id"],
    )

    # ── Class (a) ─────────────────────────────────────────────────────────────
    total_a_rows = sum(_cnt(r) for r in class_a_rows)
    print(f"\n  CLASS (a): payment_state NOT IN [unpaid,partial]  AND  due_amount != 0")
    print(f"  Total matching rows: {total_a_rows}")

    by_state: dict[str, list] = {}
    partner_seen_a: dict[int, str] = {}
    for r in class_a_rows:
        state = str(r.get("payment_state") or "(none)")
        by_state.setdefault(state, [0, 0.0])
        by_state[state][0] += _cnt(r)
        by_state[state][1] += _egp(r.get("due_amount"))
        pid = _partner_id_int(r.get("partner_id"))
        if pid and pid not in partner_seen_a:
            partner_seen_a[pid] = _partner_name_str(r.get("partner_id"))

    print(f"\n  Per payment_state:")
    print(f"  {'payment_state':<20}  {'count':>8}  {'sum(due_amount)':>22}")
    print(f"  {'-'*20}  {'-'*8}  {'-'*22}")
    for state, (cnt, amt) in sorted(by_state.items()):
        print(f"  {state:<20}  {cnt:>8}  {amt:>22,.4f} EGP")

    print(f"\n  Distinct affected partners: {len(partner_seen_a)}")
    print(f"  First 10 partner names:")
    for pid, pname in list(partner_seen_a.items())[:10]:
        print(f"    id={pid:<8}  {pname}")

    # ── Class (b) ─────────────────────────────────────────────────────────────
    total_b_rows = sum(_cnt(r) for r in class_b_rows)
    print(f"\n  CLASS (b): payment_state IN [unpaid,partial]  AND  date = False")
    print(f"  Total matching rows: {total_b_rows}")

    partner_seen_b: dict[int, str] = {}
    for r in class_b_rows:
        pid = _partner_id_int(r.get("partner_id"))
        if pid and pid not in partner_seen_b:
            partner_seen_b[pid] = _partner_name_str(r.get("partner_id"))

    print(f"  Distinct affected partners: {len(partner_seen_b)}")
    print(f"  First 10 partner names:")
    for pid, pname in list(partner_seen_b.items())[:10]:
        print(f"    id={pid:<8}  {pname}")


# ── SECTION G: Live endpoint probe ────────────────────────────────────────────

def section_g_endpoint_probe(partner_id: int, base_url: str) -> None:
    print(f"\n{SEP}")
    print("  SECTION G  Live Endpoint Probe  (FastAPI HTTP Basic Auth — verify_kpi1_live.py:115)")
    print(SEP)
    url = f"{base_url.rstrip('/')}/api/v1/customer-accounts/customer/{partner_id}"
    print(f"\n  GET {url}")
    print(f"  auth user : {FA_USER}")
    try:
        with httpx.Client(timeout=30) as client:
            r = client.get(url, auth=(FA_USER, FA_PASS))
        print(f"  HTTP status : {r.status_code}")
        try:
            body = r.json()
            print(f"  JSON body   :\n{json.dumps(body, ensure_ascii=False, indent=2)[:3000]}")
        except Exception:
            print(f"  Body (raw)  : {r.text[:500]}")
    except httpx.ConnectError as exc:
        print(f"  [WARN]  Cannot reach {base_url} — server not running? ({exc})")
        print(f"  Run scripts/start_server.bat (Decision 6.4 ritual) then re-run section G.")


# ── SECTION H: Log grep ───────────────────────────────────────────────────────

def section_h_log_grep() -> None:
    print(f"\n{SEP}")
    print("  SECTION H  Log Grep — 'Integrity assertion FAILED'")
    print(SEP)

    search_dirs   = [Path("logs"), Path(".")]
    search_globs  = ["*.log", "*.txt"]
    found_any     = False

    for d in search_dirs:
        if not d.exists():
            continue
        for glob in search_globs:
            for log_file in sorted(d.glob(glob)):
                try:
                    text = log_file.read_text(encoding="utf-8", errors="replace")
                    hits = [ln for ln in text.splitlines()
                            if "Integrity assertion FAILED" in ln]
                    if hits:
                        found_any = True
                        print(f"\n  File: {log_file}")
                        for h in hits[-20:]:
                            print(f"    {h}")
                except Exception as exc:
                    print(f"  [WARN] Could not read {log_file}: {exc}")

    if not found_any:
        print("\n  No matches found in logs/ — server may not be running or")
        print("  log output may not be captured to a file.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Diagnose CA drill-down HTTP 500 for a given partner."
    )
    parser.add_argument("--url", default=BACKEND_URL, help="FastAPI base URL")
    args = parser.parse_args()

    print(SEP)
    print("  diagnose_ca_drilldown_anomaly.py")
    print(f"  today (ZoneInfo Africa/Cairo) : {TODAY}")
    print(f"  target partner name           : {PARTNER_NAME}")
    print(f"  Odoo direct RPC URL           : {ODOO_URL}")
    print(f"  FastAPI endpoint base URL     : {args.url}")
    print(SEP)

    with httpx.Client(timeout=60) as odoo_client:
        print("\n[AUTH] Authenticating to Odoo (direct JSON-RPC, discover_m3s6_drilldown.py pattern)...")
        uid = connect(odoo_client)
        print(f"  OK uid={uid}")

        partner_id = section_a_partner_resolve(odoo_client, uid)
        section_bc_aggregates(odoo_client, uid, partner_id)
        section_d_null_date(odoo_client, uid, partner_id)
        section_e_wrong_state_nonzero_due(odoo_client, uid, partner_id)
        section_f_portfolio_scan(odoo_client, uid)

    section_g_endpoint_probe(partner_id, args.url)
    section_h_log_grep()

    print(f"\n{SEP}")
    print("  DONE")
    print(SEP)


if __name__ == "__main__":
    main()
