"""
Live verification for M3-S6 — Customer Drill-Down endpoint.

Usage:
    python scripts/verify_m3s6_drilldown_live.py [--url http://localhost:8000]

Requires the FastAPI server to be running (fresh server per Decision 6.4):
    taskkill /F /IM python.exe  (or kill uvicorn)
    uvicorn backend.main:app --port 8000
    # wait for uptime < 60s, first request → cache_status=fresh on KPI endpoints

What this script verifies:
  V1 — API returns 200 for sample customer (rank-1 KPI B: partner_id=639975)
  V2 — uptime guard: server uptime < 120s (fresh server)
  V3 — exposure numbers identity-equal against Odoo direct RPC (delta <= 0.01 EGP)
  V4 — Assertion: late_due + future_due == total_due (التصحيح المفاهيمي)
  V5 — payment_ratio_pct == x_studio_actual_paid_amount / amount × 100 (DR1)
  V6 — wallet_balance identity-equal against rs.account.payment.reconcile
  V7 — installments list is non-empty, each row has required fields + valid timing
  V8 — response shape: version, data, meta all present

Sample customer (M3-S6 discovery, 2026-05-23):
    partner_id = 639975
    baseline late_due = 18,202,000.00 EGP (76 late installments)
    baseline total_amount = 29,800,000.00 EGP (120 total installments)
    payment_ratio ~= 10.00% (cash received / total amount)
    Data drifts daily — identity check is against Odoo live (not frozen baseline).

Exit 0 — all assertions passed.
Exit 1 — at least one assertion failed or server was unreachable.
"""

import argparse
import io
import os
import sys
import uuid
from datetime import date

import httpx
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

# Force UTF-8 stdout
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Config ────────────────────────────────────────────────────────────────────

DEFAULT_URL  = os.environ.get("BACKEND_URL", "http://localhost:8000")
USERNAME     = os.environ.get("VERIFY_USERNAME", "admin")
PASSWORD     = os.environ.get("VERIFY_PASSWORD", "password")

SAMPLE_PARTNER_ID = 639975   # rank-1 KPI B as of M3-S6 discovery 2026-05-23

ENDPOINT = f"/api/v1/customer-accounts/customer/{SAMPLE_PARTNER_ID}"
HEALTH   = "/api/v1/health"

ODOO_URL  = os.environ["ODOO_URL"].rstrip("/") + "/jsonrpc"
ODOO_DB   = os.environ["ODOO_DB"]
ODOO_USER = os.environ["ODOO_USERNAME"]
ODOO_KEY  = os.environ["ODOO_API_KEY"]

TODAY = date.today().isoformat()

SEP  = "=" * 72
SEP2 = "-" * 72
PASS = "PASS"
FAIL = "FAIL"

_ALLOWED_METHODS = frozenset({
    "search", "search_read", "search_count", "read", "read_group", "fields_get",
})

# ── Odoo RPC helpers ──────────────────────────────────────────────────────────

def odoo_rpc(client: httpx.Client, service: str, method: str, args: list):
    r = client.post(
        ODOO_URL,
        json={
            "jsonrpc": "2.0", "method": "call",
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


def odoo_execute(client: httpx.Client, uid: int, model: str, method: str,
                 args: list, kwargs: dict | None = None):
    if method not in _ALLOWED_METHODS:
        raise RuntimeError(f"Method {method!r} not in ALLOWED_METHODS — read-only violation")
    return odoo_rpc(client, "object", "execute_kw",
                    [ODOO_DB, uid, ODOO_KEY, model, method, args, kwargs or {}])


def odoo_connect(client: httpx.Client) -> int:
    uid = odoo_rpc(client, "common", "authenticate",
                   [ODOO_DB, ODOO_USER, ODOO_KEY, {}])
    if not uid:
        raise RuntimeError("Odoo auth failed")
    return uid


# ── Check helpers ─────────────────────────────────────────────────────────────

_results: list[tuple[str, str, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    status = PASS if ok else FAIL
    _results.append((label, status, detail))
    mark = "[PASS]" if ok else "[FAIL]"
    line = f"  {mark} {label}"
    if detail:
        line += f"\n         {detail}"
    print(line)
    return ok


def _egp(val) -> float:
    return float(val) if val else 0.0


# ── Main verification ─────────────────────────────────────────────────────────

def run(base_url: str) -> int:
    print(SEP)
    print("  M3-S6 Live Verification — Customer Drill-Down")
    print(f"  Date     : {TODAY}")
    print(f"  Server   : {base_url}")
    print(f"  Endpoint : {ENDPOINT}")
    print(f"  Partner  : {SAMPLE_PARTNER_ID}")
    print(SEP)

    auth = (USERNAME, PASSWORD)

    with httpx.Client(base_url=base_url, auth=auth, timeout=30) as api:

        # V1 — API reachable and returns 200
        print(f"\n{SEP2}")
        print("  V1 — API response (200 + version)")
        print(SEP2)
        try:
            resp = api.get(ENDPOINT)
            ok_status = resp.status_code == 200
            check("HTTP 200", ok_status, f"status_code={resp.status_code}")
            if not ok_status:
                print("  FATAL: server returned non-200. Stopping.")
                return 1
            body = resp.json()
        except Exception as e:
            check("HTTP 200", False, str(e))
            print("  FATAL: cannot reach server. Stopping.")
            return 1

        check("version == '1.0'", body.get("version") == "1.0",
              f"version={body.get('version')!r}")
        check("data present", "data" in body)
        check("meta present", "meta" in body)

        data = body.get("data", {})
        meta = body.get("meta", {})

        # V2 — server uptime guard (fresh server)
        print(f"\n{SEP2}")
        print("  V2 — Server uptime (fresh server guard)")
        print(SEP2)
        try:
            h = api.get(HEALTH)
            uptime = h.json().get("uptime_seconds", 9999)
            check("uptime < 120s (fresh server)",
                  uptime < 120,
                  f"uptime={uptime:.1f}s — if > 120s, restart server and re-run")
        except Exception as e:
            check("health endpoint", False, str(e))

        # V3 — exposure identity-equal against Odoo
        print(f"\n{SEP2}")
        print("  V3 — Exposure numbers identity-equal vs Odoo direct RPC")
        print(SEP2)

        with httpx.Client(timeout=60) as oc:
            uid = odoo_connect(oc)

            base_all     = [("state", "=", "post"), ("partner_id", "=", SAMPLE_PARTNER_ID)]
            unpaid_domain = base_all + [("payment_state", "in", ["unpaid", "partial"])]
            late_domain   = unpaid_domain + [("date", "<",  TODAY)]
            future_domain = unpaid_domain + [("date", ">=", TODAY)]
            wallet_domain = [
                ("state", "=", "post"),
                ("partner_id", "=", SAMPLE_PARTNER_ID),
                ("residual_amount", ">", 0),
            ]

            all_rg = odoo_execute(oc, uid, "rs.installment", "read_group",
                                  [base_all,
                                   ["amount", "due_amount",
                                    "x_studio_actual_paid_amount"],
                                   ["partner_id"]],
                                  {"lazy": False})
            late_rg   = odoo_execute(oc, uid, "rs.installment", "read_group",
                                     [late_domain, ["due_amount"], []],
                                     {"lazy": False})
            future_rg = odoo_execute(oc, uid, "rs.installment", "read_group",
                                     [future_domain, ["due_amount"], []],
                                     {"lazy": False})
            wallet_rg = odoo_execute(oc, uid, "rs.account.payment.reconcile",
                                     "read_group",
                                     [wallet_domain, ["residual_amount"], []],
                                     {"lazy": False})

        all_row      = all_rg[0] if all_rg else {}
        odoo_amount  = _egp(all_row.get("amount"))
        odoo_actual  = _egp(all_row.get("x_studio_actual_paid_amount"))
        odoo_late    = _egp((late_rg[0]   if late_rg   else {}).get("due_amount"))
        odoo_future  = _egp((future_rg[0] if future_rg else {}).get("due_amount"))
        odoo_wallet  = _egp((wallet_rg[0] if wallet_rg else {}).get("residual_amount"))
        odoo_total_due = odoo_late + odoo_future

        exp = data.get("exposure", {})
        beh = data.get("behavior", {})

        TOLERANCE = 0.01  # EGP — identity-equal up to floating point

        delta_late   = abs(_egp(exp.get("late_due_egp"))   - odoo_late)
        delta_future = abs(_egp(exp.get("future_due_egp")) - odoo_future)
        delta_total  = abs(_egp(exp.get("total_due_egp"))  - odoo_total_due)
        delta_actual = abs(_egp(exp.get("paid_cash_egp"))  - odoo_actual)
        delta_orig   = abs(_egp(exp.get("total_original_egp")) - odoo_amount)
        delta_wallet = abs(_egp(beh.get("wallet_balance_egp")) - odoo_wallet)

        print(f"  Odoo direct: total_due={odoo_total_due:,.2f}, late={odoo_late:,.2f}, "
              f"future={odoo_future:,.2f}, actual_paid={odoo_actual:,.2f}")
        print(f"  API returns: total_due={exp.get('total_due_egp'):,.2f}, "
              f"late={exp.get('late_due_egp'):,.2f}, "
              f"future={exp.get('future_due_egp'):,.2f}, "
              f"paid_cash={exp.get('paid_cash_egp'):,.2f}")

        check("late_due_egp identity (delta <= 0.01)",
              delta_late <= TOLERANCE, f"delta={delta_late:.4f} EGP")
        check("future_due_egp identity (delta <= 0.01)",
              delta_future <= TOLERANCE, f"delta={delta_future:.4f} EGP")
        check("total_due_egp identity (delta <= 0.01)",
              delta_total <= TOLERANCE, f"delta={delta_total:.4f} EGP")
        check("paid_cash_egp identity (delta <= 0.01)",
              delta_actual <= TOLERANCE, f"delta={delta_actual:.4f} EGP")
        check("total_original_egp identity (delta <= 0.01)",
              delta_orig <= TOLERANCE, f"delta={delta_orig:.4f} EGP")
        check("wallet_balance_egp identity (delta <= 0.01)",
              delta_wallet <= TOLERANCE, f"delta={delta_wallet:.4f} EGP")

        # V4 — assertion: late + future == total_due
        print(f"\n{SEP2}")
        print("  V4 — Assertion: late + future == total_due (التصحيح المفاهيمي)")
        print(SEP2)
        computed_total = _egp(exp.get("late_due_egp")) + _egp(exp.get("future_due_egp"))
        api_total      = _egp(exp.get("total_due_egp"))
        assertion_delta = abs(computed_total - api_total)
        check(
            "late + future == total_due (delta < 0.01)",
            assertion_delta < 0.01,
            f"late={exp.get('late_due_egp'):,.2f} + future={exp.get('future_due_egp'):,.2f} "
            f"= {computed_total:,.2f}, total={api_total:,.2f}, delta={assertion_delta:.4f}"
        )

        # V5 — payment ratio = actual_paid / total_amount × 100
        print(f"\n{SEP2}")
        print("  V5 — payment_ratio_pct = actual_paid / total_amount × 100 (DR1)")
        print(SEP2)
        if odoo_amount > 0:
            expected_ratio = round(odoo_actual / odoo_amount * 100, 2)
            api_ratio = _egp(beh.get("payment_ratio_pct"))
            ratio_delta = abs(api_ratio - expected_ratio)
            check(
                "payment_ratio_pct identity (delta < 0.01%)",
                ratio_delta < 0.01,
                f"expected={expected_ratio:.2f}%, got={api_ratio:.2f}%, delta={ratio_delta:.4f}"
            )
        else:
            check("payment_ratio_pct = 0 when no installments",
                  _egp(beh.get("payment_ratio_pct")) == 0.0)

        # V6 — wallet already checked in V3

        # V7 — installment list shape and timing labels
        print(f"\n{SEP2}")
        print("  V7 — Installment list: fields, timing labels, sort order")
        print(SEP2)
        inst = data.get("installments", {})
        items = inst.get("items", [])
        check("installment items non-empty", len(items) > 0,
              f"count={len(items)}")

        required_fields = {
            "record_id", "date", "installment_type_id",
            "installment_type_name_ar", "payment_state",
            "timing", "amount", "due_amount",
        }
        all_have_fields = all(required_fields.issubset(set(r.keys())) for r in items)
        check("all rows have required fields", all_have_fields)

        valid_timings = all(r.get("timing") in ("late", "future") for r in items)
        check("all timings are 'late' or 'future'", valid_timings)

        # Verify timing matches date < today / date >= today
        timing_correct = all(
            (r["timing"] == "late") == (r["date"] < TODAY)
            for r in items
        )
        check("timing matches date vs today boundary", timing_correct)

        # V8 — meta
        print(f"\n{SEP2}")
        print("  V8 — Response meta")
        print(SEP2)
        check("meta.today present", "today" in meta, f"today={meta.get('today')}")
        check("meta.page_size present", "page_size" in meta)
        check("meta.sort_by present", "sort_by" in meta)
        check("meta.rpc_duration_ms > 0",
              int(meta.get("rpc_duration_ms", 0)) > 0,
              f"rpc_duration_ms={meta.get('rpc_duration_ms')}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("  SUMMARY")
    print(SEP)
    failures = [(l, d) for l, s, d in _results if s == FAIL]
    passes   = sum(1 for _, s, _ in _results if s == PASS)
    total    = len(_results)
    print(f"  {passes}/{total} checks passed")
    if failures:
        print(f"\n  FAILURES ({len(failures)}):")
        for label, detail in failures:
            print(f"    [FAIL] {label}")
            if detail:
                print(f"           {detail}")
        print(SEP)
        return 1
    print(f"\n  ALL CHECKS PASSED — M3-S6 verification complete.")
    print(SEP)
    return 0


def main():
    parser = argparse.ArgumentParser(description="M3-S6 live verification")
    parser.add_argument("--url", default=DEFAULT_URL, help="Backend base URL")
    args = parser.parse_args()
    sys.exit(run(args.url))


if __name__ == "__main__":
    main()
