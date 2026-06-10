"""
verify_ca_drilldown_fix_live.py — Session 18 (N2) live verification of the
CA drill-down overpayment-credit fix (Decision 18.2).

READ-ONLY: search, search_read, search_count, read, read_group, fields_get ONLY.
No create/write/unlink. No OpenAI. AI cost = $0.00.

Run AFTER the Decision 6.4 ritual (kill python, clear __pycache__, start
uvicorn WITHOUT --reload — scripts/start_server.bat).

Checks:
  A. partner 62112 (يوسف بدر شرهان دخيل):
       HTTP 200 (this used to be the 500 crash),
       overpaid_credit_egp == 450.00 ± 0.01,
       data_quality_warning is null,
       triple agreement — response late+future + direct neg_sum + direct
       pos_sum ≈ direct all_posted_due (|delta| < 1.0 EGP), and
       response credit == -direct neg_sum ± 0.01.
  B. two more affected partners (live class-(a) scan, ids != 62112):
       HTTP 200 each + credit agrees with direct -neg_sum ± 0.01.
  C. one unaffected partner (posted installments, no settled nonzero rows):
       HTTP 200, overpaid_credit_egp == 0.0, data_quality_warning is null.

AUTH EVIDENCE (verbatim sources):
  Direct Odoo JSON-RPC (ground truth, no FastAPI cache contamination):
    diagnose_ca_drilldown_anomaly.py rpc()/execute()/connect() pattern
    (itself verbatim from discover_m3s6_drilldown.py lines 59-84, 112-119).
  FastAPI session-cookie auth (post-A2, Decision 18.1):
    scripts/_lib/api_session.py login() — POST /login {username, password,
    next} → 303 + cookie; ONE login per process (limiter 10/minute).

Usage:
    python scripts/verify_ca_drilldown_fix_live.py [--url http://localhost:8000]
Exit code: 0 = all checks PASS, 1 = any FAIL.
"""

import argparse
import io
import os
import sys
import uuid
from pathlib import Path

import httpx
from dotenv import load_dotenv

from _lib.api_session import ApiLoginError, login as api_login

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

# Force UTF-8 stdout — Windows console defaults to cp1252
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Config: direct Odoo (discover_m3s6_drilldown.py lines 37-41 pattern) ─────
ODOO_URL  = os.environ["ODOO_URL"].rstrip("/") + "/jsonrpc"
ODOO_DB   = os.environ["ODOO_DB"]
ODOO_USER = os.environ["ODOO_USERNAME"]
ODOO_KEY  = os.environ["ODOO_API_KEY"]

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")

TARGET_PARTNER  = 62112        # يوسف بدر شرهان دخيل — the original 500 crash
EXPECTED_CREDIT = 450.00       # paid 259,450 against 259,000 on inst 66422
TOL_CREDIT      = 0.01
TOL_IDENTITY    = 1.0          # same tolerance as drilldown_service.py

_MODEL = "rs.installment"

ALLOWED_METHODS = frozenset({
    "search", "search_read", "search_count",
    "read", "read_group", "fields_get",
})

SEP  = "=" * 72
SEP2 = "-" * 72

_results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    _results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}]  {name}")
    print(f"          {detail}")


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


def rg_sum(client, uid, domain) -> tuple[float, int]:
    """SUM(due_amount) + __count over domain (read_group, no groupby)."""
    rows = execute(client, uid, _MODEL, "read_group",
                   [domain, ["due_amount"], []], {"lazy": False})
    row = rows[0] if rows else {}
    return float(row.get("due_amount") or 0.0), int(row.get("__count") or 0)


# ── Direct-Odoo ground truth per partner ─────────────────────────────────────

def direct_figures(client, uid, partner_id: int, today: str) -> dict:
    base_all = [("state", "=", "post"), ("partner_id", "=", partner_id)]
    unpaid   = base_all + [("payment_state", "in", ["unpaid", "partial"])]
    settled  = base_all + [("payment_state", "not in", ["unpaid", "partial"])]

    all_due,  _ = rg_sum(client, uid, base_all)
    late_due, _ = rg_sum(client, uid, unpaid + [("date", "<",  today)])
    fut_due,  _ = rg_sum(client, uid, unpaid + [("date", ">=", today)])
    neg_sum, neg_cnt = rg_sum(client, uid, settled + [("due_amount", "<", 0)])
    pos_sum, pos_cnt = rg_sum(client, uid, settled + [("due_amount", ">", 0)])

    return {
        "all_due": all_due, "late_due": late_due, "future_due": fut_due,
        "neg_sum": neg_sum, "neg_count": neg_cnt,
        "pos_sum": pos_sum, "pos_count": pos_cnt,
    }


# ── Partner selection ─────────────────────────────────────────────────────────

def pick_partners(client, uid) -> tuple[list[int], int]:
    """Two affected partner ids (class (a), != TARGET) + one unaffected id."""
    class_a = [
        ("state",         "=",      "post"),
        ("payment_state", "not in", ["unpaid", "partial"]),
        ("due_amount",    "!=",      0),
    ]
    rows = execute(client, uid, _MODEL, "read_group",
                   [class_a, ["due_amount"], ["partner_id"]], {"lazy": False})
    affected_ids: list[int] = []
    for r in rows:
        raw = r.get("partner_id")
        pid = int(raw[0]) if isinstance(raw, (list, tuple)) and raw else 0
        if pid and pid not in affected_ids:
            affected_ids.append(pid)
    extra = [pid for pid in affected_ids if pid != TARGET_PARTNER][:2]

    clean_rows = execute(client, uid, _MODEL, "search_read",
                         [[("state", "=", "post"),
                           ("partner_id", "not in", affected_ids)],
                          ["partner_id"]],
                         {"limit": 1})
    raw = clean_rows[0]["partner_id"] if clean_rows else None
    clean_id = int(raw[0]) if isinstance(raw, (list, tuple)) and raw else 0
    if not clean_id:
        raise RuntimeError("Could not find an unaffected partner with posted installments.")

    print(f"  Affected partners (class a) : {len(affected_ids)} total")
    print(f"  Extra affected picks        : {extra}")
    print(f"  Unaffected (clean) pick     : {clean_id}")
    return extra, clean_id


# ── Endpoint call ─────────────────────────────────────────────────────────────

def fetch_drilldown(api: httpx.Client, partner_id: int):
    r = api.get(f"/api/v1/customer-accounts/customer/{partner_id}")
    body = None
    try:
        body = r.json()
    except Exception:
        pass
    return r.status_code, body


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Live verification of the CA drill-down overpayment fix."
    )
    parser.add_argument("--url", default=BACKEND_URL, help="FastAPI base URL")
    args = parser.parse_args()

    print(SEP)
    print("  verify_ca_drilldown_fix_live.py  (Session 18 / Decision 18.2)")
    print(f"  Odoo direct RPC URL  : {ODOO_URL}")
    print(f"  FastAPI base URL     : {args.url}")
    print(SEP)

    # FastAPI session — ONE login for the whole process (limiter 10/minute)
    print("\n[AUTH] FastAPI session login (scripts/_lib/api_session.py)...")
    try:
        api = api_login(args.url)
    except (ApiLoginError, httpx.ConnectError) as exc:
        print(f"  [FAIL] {exc}")
        print("  Run scripts/start_server.bat (Decision 6.4 ritual) first.")
        sys.exit(1)
    print("  OK — session cookie acquired.")

    with httpx.Client(timeout=60) as odoo:
        print("\n[AUTH] Direct Odoo JSON-RPC...")
        uid = connect(odoo)
        print(f"  OK uid={uid}")

        # The endpoint computes 'today' via cache.today_str() (Africa/Cairo);
        # take it from the first response's meta so both sides use one boundary.
        print(f"\n{SEP}")
        print(f"  CHECK A  partner {TARGET_PARTNER} — the original HTTP 500 crash")
        print(SEP)
        status, body = fetch_drilldown(api, TARGET_PARTNER)
        check("A1: HTTP 200 (was 500 before Decision 18.2)",
              status == 200, f"HTTP status = {status}")
        if status != 200 or not body:
            _finish()
            return

        today    = body["meta"]["today"]
        exposure = body["data"]["exposure"]
        warning  = body["meta"]["data_quality_warning"]
        credit   = float(exposure["overpaid_credit_egp"])
        late     = float(exposure["late_due_egp"])
        future   = float(exposure["future_due_egp"])

        check("A2: overpaid_credit_egp == 450.00 ± 0.01",
              abs(credit - EXPECTED_CREDIT) <= TOL_CREDIT,
              f"credit = {credit:.2f} (expected {EXPECTED_CREDIT:.2f})")
        check("A3: data_quality_warning is null",
              warning is None, f"warning = {warning!r}")

        d = direct_figures(odoo, uid, TARGET_PARTNER, today)
        identity_lhs = late + future + d["neg_sum"] + d["pos_sum"]
        check("A4: identity — resp(late+future) + direct(neg+pos) ≈ direct all_due",
              abs(identity_lhs - d["all_due"]) < TOL_IDENTITY,
              f"{late:.2f} + {future:.2f} + ({d['neg_sum']:.2f}) + ({d['pos_sum']:.2f}) "
              f"= {identity_lhs:.2f}  vs  all_due = {d['all_due']:.2f}  "
              f"delta = {abs(identity_lhs - d['all_due']):.4f}")
        check("A5: response credit == -direct neg_sum ± 0.01",
              abs(credit - (-d["neg_sum"])) <= TOL_CREDIT,
              f"credit = {credit:.2f}  vs  -neg_sum = {-d['neg_sum']:.2f} "
              f"({d['neg_count']} negative settled row(s))")
        check("A6: overpaid_record_count == direct negative-row count",
              int(exposure["overpaid_record_count"]) == d["neg_count"],
              f"response = {exposure['overpaid_record_count']}, direct = {d['neg_count']}")

        # ── B: two more affected partners ────────────────────────────────────
        print(f"\n{SEP}")
        print("  CHECK B  two more affected partners (live class-(a) scan)")
        print(SEP)
        extra, clean_id = pick_partners(odoo, uid)
        for pid in extra:
            status, body = fetch_drilldown(api, pid)
            check(f"B: partner {pid} → HTTP 200", status == 200,
                  f"HTTP status = {status}")
            if status == 200 and body:
                dp = direct_figures(odoo, uid, pid, body["meta"]["today"])
                cr = float(body["data"]["exposure"]["overpaid_credit_egp"])
                check(f"B: partner {pid} credit == -direct neg_sum ± 0.01",
                      abs(cr - (-dp["neg_sum"])) <= TOL_CREDIT,
                      f"credit = {cr:.2f}  vs  -neg_sum = {-dp['neg_sum']:.2f}")

        # ── C: one unaffected partner ────────────────────────────────────────
        print(f"\n{SEP}")
        print("  CHECK C  unaffected partner — credit must be exactly 0")
        print(SEP)
        status, body = fetch_drilldown(api, clean_id)
        check(f"C1: partner {clean_id} → HTTP 200", status == 200,
              f"HTTP status = {status}")
        if status == 200 and body:
            cr = float(body["data"]["exposure"]["overpaid_credit_egp"])
            warning = body["meta"]["data_quality_warning"]
            check("C2: overpaid_credit_egp == 0.0", cr == 0.0, f"credit = {cr}")
            check("C3: data_quality_warning is null",
                  warning is None, f"warning = {warning!r}")

    api.close()
    _finish()


def _finish() -> None:
    fails = [r for r in _results if not r[1]]
    print(f"\n{SEP}")
    print(f"  RESULT: {len(_results) - len(fails)}/{len(_results)} checks passed"
          + ("" if not fails else f"  —  {len(fails)} FAILED"))
    print(SEP)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
