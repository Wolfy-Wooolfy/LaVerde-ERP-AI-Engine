"""
Live verification for Refunds alert section — GET /api/v1/customer-accounts/refunds/summary.

Usage:
    python scripts/verify_refunds_live.py [--url http://localhost:8000]

Requires the FastAPI server to be running and reachable.
Set VERIFY_USERNAME / VERIFY_PASSWORD env vars (or .env) to override
the default admin credentials.  Set ODOO_URL / ODOO_DB / ODOO_API_KEY
to enable the direct-Odoo identity check (recommended on a fresh server run).

Exit 0  — all assertions passed
Exit 1  — at least one assertion failed or the server was unreachable

Appends one tab-separated row to logs/refunds_verification.log on each run.

── Model and domain ──────────────────────────────────────────────────────────
Model : rs.account.payment.reconcile
Domain: [('state','=','post'), ('amount','<',0)]
Flow direction indicator: sign of `amount` (not payment_type — unreliable;
  all 205 live records have payment_type='inbound' including the 7 refunds).
  Confirmed in MODULE_3_DISCOVERY_PHASE_3.md §4.1.

Baseline (M3-S1 discovery, 2026-05-23, commit 00f3abf):
    total_refunds      = −719,812.00 EGP
    refund_count       = 7
    null_partner_count = 0  (M3-S1 §6: all 7 have known partner — not null FK)
"""

import argparse
import io
import os
import sys
import xmlrpc.client
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv

from _lib.api_session import ApiLoginError, login as api_login

load_dotenv(dotenv_path=".env")

# Force UTF-8 stdout (Windows consoles default to cp1252)
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Config ────────────────────────────────────────────────────────────────────

DEFAULT_URL  = os.environ.get("BACKEND_URL", "http://localhost:8000")
USERNAME     = os.environ.get("VERIFY_USERNAME", "admin")
PASSWORD     = os.environ.get("VERIFY_PASSWORD", "password")
ODOO_URL     = os.environ.get("ODOO_URL", "")
ODOO_DB      = os.environ.get("ODOO_DB", "")
ODOO_USER    = os.environ.get("ODOO_USERNAME", "")
ODOO_API_KEY = os.environ.get("ODOO_API_KEY", "")
ENDPOINT     = "/api/v1/customer-accounts/refunds/summary"
LOG_FILE     = "logs/refunds_verification.log"

# M3-S1 snapshot (2026-05-23, commit 00f3abf)
BASELINE_TOTAL        = -719_812.00
BASELINE_COUNT        = 7
BASELINE_NULL_PARTNER = 0
BASELINE_DATE         = "2026-05-23"

# Sanity bounds — refund portfolio is small and relatively stable
MIN_TOTAL         = -5_000_000.0   # −5M — more than 7× baseline is a FINDING
MAX_TOTAL         = -1.0           # must be strictly negative (has refunds)
MIN_COUNT         = 1
MAX_COUNT         = 200
IDENTITY_DELTA    = 1.0            # EGP

_SEP = "═" * 63

# ── Helpers ───────────────────────────────────────────────────────────────────

_PASS    = "[PASS]"
_FAIL    = "[FAIL]"
_INFO    = "[INFO]"
_FINDING = "[FINDING]"


def _log(prefix: str, msg: str) -> None:
    print(f"{prefix} {msg}", flush=True)


def _check(label: str, condition: bool, detail: str = "") -> bool:
    tag = _PASS if condition else _FAIL
    _log(tag, f"{label}{(' — ' + detail) if detail else ''}")
    return condition


def _append_log_row(
    run_at: str,
    api_total: float | str,
    odoo_total: float | str,
    identity_delta: float | str,
    count: int | str,
    null_partner: int | str,
    cache_status: str,
    rpc_ms: int | str,
    error: str = "",
) -> None:
    os.makedirs("logs", exist_ok=True)
    log_exists = os.path.isfile(LOG_FILE)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        if not log_exists:
            f.write(
                "run_at\tapi_total_refunds\todoo_direct_total\tidentity_delta\t"
                "refund_count\tnull_partner_count\tcache_status\trpc_duration_ms\terror\n"
            )
        f.write(
            f"{run_at}\t{api_total}\t{odoo_total}\t{identity_delta}\t"
            f"{count}\t{null_partner}\t{cache_status}\t{rpc_ms}\t{error}\n"
        )


def _odoo_direct_query() -> float | None:
    """Query Odoo directly via XMLRPC — same domain as Refunds endpoint.

    Returns SUM(amount) for refund records or None if credentials are not set.
    """
    if not all([ODOO_URL, ODOO_DB, ODOO_USER, ODOO_API_KEY]):
        _log(_INFO, "Odoo credentials not set — skipping direct identity check.")
        _log(_INFO, "Set ODOO_URL, ODOO_DB, ODOO_USERNAME, ODOO_API_KEY in .env to enable.")
        return None

    try:
        common = xmlrpc.client.ServerProxy(
            f"{ODOO_URL.rstrip('/')}/xmlrpc/2/common", allow_none=True
        )
        uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_API_KEY, {})
        if not uid:
            _log(_INFO, "Odoo authentication failed — skipping direct identity check.")
            return None

        models = xmlrpc.client.ServerProxy(
            f"{ODOO_URL.rstrip('/')}/xmlrpc/2/object", allow_none=True
        )
        domain = [("state", "=", "post"), ("amount", "<", 0)]
        rows = models.execute_kw(
            ODOO_DB, uid, ODOO_API_KEY,
            "rs.account.payment.reconcile", "read_group",
            [domain, ["amount"], ["partner_id"]],
            {"lazy": False},
        )
        odoo_total = sum(float(r.get("amount") or 0.0) for r in rows)
        odoo_count = sum(int(r.get("__count") or 0) for r in rows)
        _log(_INFO, f"Direct Odoo XMLRPC: {len(rows)} partner groups, "
                    f"SUM(amount) = {odoo_total:,.2f} EGP, records = {odoo_count}")
        return odoo_total

    except Exception as exc:
        _log(_INFO, f"Direct Odoo query failed (skipping identity check): {exc}")
        return None


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_URL, help="Backend base URL")
    args = parser.parse_args()
    base_url: str = args.url.rstrip("/")

    url    = f"{base_url}{ENDPOINT}"
    run_at = datetime.now(timezone.utc).isoformat()

    _log(_INFO, f"Target: GET {url}")
    _log(_INFO, f"Auth user: {USERNAME}")
    _log(_INFO, (
        f"Baseline: {BASELINE_TOTAL:,.2f} EGP / {BASELINE_COUNT} records / "
        f"{BASELINE_NULL_PARTNER} null-partner ({BASELINE_DATE})"
    ))

    failures: list[str] = []

    # ── Step 1: ONE login per process (limiter 10/minute), then GET ──────────
    try:
        client = api_login(base_url)
    except ApiLoginError as exc:
        _log(_FAIL, f"Session login failed: {exc}")
        _append_log_row(run_at, "", "", "", "", "", "", "", error=f"login failed: {exc}")
        return 1
    except httpx.ConnectError as exc:
        msg = f"Cannot reach {base_url} — is the server running? ({exc})"
        _log(_FAIL, msg)
        _append_log_row(run_at, "", "", "", "", "", "", "", error=msg)
        return 1

    try:
        r = client.get(ENDPOINT, timeout=60)

        # ── Step 2: Status code ───────────────────────────────────────────────────
        ok = _check("HTTP 200", r.status_code == 200, f"got {r.status_code}")
        if not ok:
            _log(_INFO, f"Response body: {r.text[:500]}")
            _append_log_row(run_at, "", "", "", "", "", "", "", error=f"HTTP {r.status_code}")
            return 1

        body: dict = r.json()
        _log(_INFO, (
            f"total_refunds={body.get('total_refunds')}, "
            f"refund_count={body.get('refund_count')}, "
            f"null_partner_count={body.get('null_partner_count')}, "
            f"cache_status={body.get('cache_status')}, "
            f"rpc_ms={body.get('rpc_duration_ms')}"
        ))

        # ── Step 3: Required keys ─────────────────────────────────────────────────
        required_keys = (
            "total_refunds", "refund_count", "null_partner_count",
            "currency", "as_of", "cache_status", "rpc_duration_ms", "domain",
        )
        for k in required_keys:
            if not _check(f"key '{k}' present", k in body):
                failures.append(f"missing_key_{k}")

        if failures:
            _append_log_row(run_at, "", "", "", "", "", "", "", error=f"missing keys: {failures}")
            return 1

        # ── Step 4: Extract values ────────────────────────────────────────────────
        api_total:        float = float(body["total_refunds"])
        refund_count:     int   = int(body["refund_count"])
        null_partner:     int   = int(body["null_partner_count"])
        cache_status:     str   = body["cache_status"]
        rpc_ms:           int   = int(body["rpc_duration_ms"])
        delta_baseline          = api_total - BASELINE_TOTAL

        # ── Step 5: Direct Odoo query (identity check) ────────────────────────────
        odoo_direct: float | None = _odoo_direct_query()
        identity_delta_str = ""
        if odoo_direct is not None:
            identity_delta = abs(api_total - odoo_direct)
            identity_delta_str = f"{identity_delta:.2f}"
            if not _check(
                f"identity check: |api − odoo_direct| < {IDENTITY_DELTA:.0f} EGP",
                identity_delta < IDENTITY_DELTA,
                f"api={api_total:,.2f}, odoo={odoo_direct:,.2f}, delta={identity_delta:.4f} EGP",
            ):
                failures.append("identity_check_failed")
        else:
            _log(_INFO, "Identity check skipped (Odoo credentials not available).")

        # ── Step 6: Structured summary ────────────────────────────────────────────
        delta_sign = "+" if delta_baseline >= 0 else ""
        print()
        print(_SEP)
        print("Refunds — Alert Section Verification")
        print(f"Run timestamp          : {run_at}")
        print(_SEP)
        print(f"API total_refunds      : {api_total:>20,.2f} EGP")
        if odoo_direct is not None:
            print(f"Odoo direct value      : {odoo_direct:>20,.2f} EGP")
            print(f"Identity delta         : {abs(api_total - odoo_direct):>20,.4f} EGP")
        print(f"Baseline total         : {BASELINE_TOTAL:>20,.2f} EGP ({BASELINE_DATE})")
        print(f"Delta vs baseline      : {delta_sign}{delta_baseline:>19,.2f} EGP")
        print(f"Refund count           : {refund_count:>20,}")
        print(f"Baseline count         : {BASELINE_COUNT:>20,}  ({BASELINE_DATE})")
        print(f"Null partner count     : {null_partner:>20,}")
        print(f"Baseline null-partner  : {BASELINE_NULL_PARTNER:>20,}  ({BASELINE_DATE})")
        print(f"Cache status           : {cache_status:>20}")
        print(f"RPC duration           : {rpc_ms:>17} ms")
        print(f"Domain used            : {body.get('domain')}")
        print(_SEP)
        print()

        # ── Step 7: Value assertions ──────────────────────────────────────────────
        if not _check("total_refunds < 0 (must be negative)",
                      api_total < 0,
                      f"got {api_total:,.2f}"):
            failures.append("total_refunds_not_negative")

        if api_total < MIN_TOTAL:
            _log(
                _FINDING,
                f"total_refunds={api_total:,.2f} EGP is below MIN={MIN_TOTAL:,.2f} EGP "
                "(>7× baseline magnitude). May indicate new bulk refund entries. "
                "Stop and report.",
            )
            failures.append("total_refunds_below_sanity_min")
        else:
            _check("total_refunds >= sanity MIN", True, f"{api_total:,.2f} >= {MIN_TOTAL:,.2f} EGP")

        if not _check("refund_count >= MIN_COUNT",
                      refund_count >= MIN_COUNT,
                      f"got {refund_count}"):
            failures.append("refund_count_below_min")

        if not _check("refund_count <= MAX_COUNT",
                      refund_count <= MAX_COUNT,
                      f"got {refund_count}"):
            failures.append("refund_count_above_max")

        if not _check("null_partner_count >= 0",
                      null_partner >= 0,
                      f"got {null_partner}"):
            failures.append("null_partner_negative")

        if not _check("null_partner_count <= refund_count",
                      null_partner <= refund_count,
                      f"null={null_partner}, total={refund_count}"):
            failures.append("null_partner_exceeds_total")

        if not _check("currency == 'EGP'",
                      body.get("currency") == "EGP",
                      f"got {body.get('currency')!r}"):
            failures.append("wrong_currency")

        if not _check("cache_status in {fresh, cached}",
                      cache_status in {"fresh", "cached"},
                      f"got {cache_status!r}"):
            failures.append("bad_cache_status")

        # ── Step 8: Domain shape ──────────────────────────────────────────────────
        domain: list = body.get("domain", [])
        if not _check("domain has 2 clauses", len(domain) == 2, f"got {len(domain)}"):
            failures.append("domain_wrong_length")
        else:
            if not _check("domain[0] == ['state','=','post']",
                          domain[0] == ["state", "=", "post"],
                          f"got {domain[0]!r}"):
                failures.append("domain_clause0_wrong")
            if not _check("domain[1] == ['amount','<',0]",
                          domain[1] == ["amount", "<", 0],
                          f"got {domain[1]!r}"):
                failures.append("domain_clause1_wrong — must use amount<0 not payment_type")

        # ── Step 9: Response headers ──────────────────────────────────────────────
        cc  = r.headers.get("cache-control", "")
        xcs = r.headers.get("x-cache-status", "")
        _check("Cache-Control: private",    "private"    in cc, f"header: {cc!r}")
        _check("Cache-Control: max-age=60", "max-age=60" in cc, f"header: {cc!r}")
        _check("X-Cache-Status present",    bool(xcs),          f"got {xcs!r}")

        # ── Step 10: Second request — cache hit ───────────────────────────────────
        _log(_INFO, "Issuing second request to verify cache hit ...")
        r2 = client.get(ENDPOINT, timeout=30)
        body2: dict = r2.json()
        if not _check("second call cache_status == 'cached'",
                      body2.get("cache_status") == "cached",
                      f"got {body2.get('cache_status')!r}"):
            failures.append("cache_not_hit_on_second_call")
        if not _check("second call rpc_duration_ms == 0",
                      int(body2.get("rpc_duration_ms", -1)) == 0,
                      f"got {body2.get('rpc_duration_ms')}"):
            failures.append("cache_rpc_ms_nonzero")
    finally:
        client.close()

    # ── Result ────────────────────────────────────────────────────────────────
    _append_log_row(
        run_at=run_at,
        api_total=f"{api_total:.2f}",
        odoo_total=f"{odoo_direct:.2f}" if odoo_direct is not None else "",
        identity_delta=identity_delta_str,
        count=refund_count,
        null_partner=null_partner,
        cache_status=cache_status,
        rpc_ms=rpc_ms,
    )

    if failures:
        _log(_FAIL, f"Verification FAILED — {len(failures)} assertion(s): {failures}")
        return 1

    print()
    _log(_PASS, "All assertions passed.")
    print()
    if odoo_direct is None:
        print("Manual identity check (Odoo credentials not set):")
        print("  1. Open Odoo → Reconcile Payments (rs.account.payment.reconcile)")
        print("  2. Filter: State = Posted, Amount < 0")
        print("  3. Group By: Partner (partner_id), Measure: Amount")
        print("  4. Compare the total to 'API total_refunds' above.")
        print("     Expected: identity-equal (< 1 EGP delta at the same moment).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
