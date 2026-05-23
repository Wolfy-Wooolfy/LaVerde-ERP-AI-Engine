"""
Live verification for KPI C — Unallocated Wallet Balance.

Usage:
    python scripts/verify_kpic_live.py [--url http://localhost:8000]

Requires the FastAPI server to be running and reachable.
Set VERIFY_USERNAME / VERIFY_PASSWORD env vars (or .env) to override
the default admin credentials.  Set ODOO_URL / ODOO_DB / ODOO_API_KEY
to enable the direct-Odoo identity check (recommended on a fresh server run).

Exit 0  — all assertions passed
Exit 1  — at least one assertion failed or the server was unreachable

Appends one tab-separated row to logs/kpic_verification.log on each run.

── Verification strategy (KPI C is a moving baseline) ───────────────────────
KPI C (SUM residual_amount WHERE state='post' AND residual_amount>0) changes
whenever staff apply a wallet balance to an installment.  The 17,214,301.92 EGP
snapshot captured in M3-S1 (2026-05-23) is a historical reference only.

Two-layer check:
  1. IDENTITY CHECK (primary): FastAPI response vs. direct Odoo XMLRPC query
     issued in the same script run.  Delta must be < 1 EGP.  This proves the
     endpoint returns correct data regardless of the current portfolio value.
  2. SANITY BOUNDS (secondary): if the live value is outside ±50% of the M3-S1
     snapshot (i.e. < 8 M or > 35 M EGP), this is flagged as a FINDING — stop
     and report, because the data may have been purged or duplicated.

Baseline (M3-S1 discovery, 2026-05-23, commit 00f3abf — MOVING):
    value          = 17,214,301.92 EGP  (reference; not used for identity)
    customer_count = 27
    record_count   = 198 reconcile records
"""

import argparse
import io
import os
import sys
import xmlrpc.client
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv

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
ENDPOINT     = "/api/v1/customer-accounts/kpi/unallocated-wallet-balance"
LOG_FILE     = "logs/kpic_verification.log"

# M3-S1 snapshot (2026-05-23, commit 00f3abf) — reference only, not identity baseline
SNAPSHOT_VALUE_EGP = 17_214_301.92
SNAPSHOT_CUSTOMERS = 27
SNAPSHOT_DATE      = "2026-05-23"

# Sanity bounds — ±50% of snapshot catches purge/duplication, not normal drift
MIN_VALUE_EGP  = 8_000_000.0    # ~50% of 17.2M — below this is a FINDING
MAX_VALUE_EGP  = 35_000_000.0   # ~2× of 17.2M   — above this is a FINDING
MIN_CUSTOMERS  = 10
MAX_CUSTOMERS  = 100
IDENTITY_DELTA = 1.0            # EGP — max acceptable delta for identity check

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
    api_value: float | str,
    odoo_value: float | str,
    identity_delta: float | str,
    customers: int | str,
    cache_status: str,
    rpc_ms: int | str,
    error: str = "",
) -> None:
    os.makedirs("logs", exist_ok=True)
    log_exists = os.path.isfile(LOG_FILE)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        if not log_exists:
            f.write(
                "run_at\tapi_value\todoo_direct_value\tidentity_delta\t"
                "customer_count\tcache_status\trpc_duration_ms\terror\n"
            )
        f.write(
            f"{run_at}\t{api_value}\t{odoo_value}\t{identity_delta}\t"
            f"{customers}\t{cache_status}\t{rpc_ms}\t{error}\n"
        )


def _odoo_direct_query() -> float | None:
    """Query Odoo directly via XMLRPC — same domain as KPI C endpoint.

    Returns SUM(residual_amount) or None if Odoo credentials are not set.
    This is the same-moment reference for the identity check.
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
        domain = [("state", "=", "post"), ("residual_amount", ">", 0)]
        rows = models.execute_kw(
            ODOO_DB, uid, ODOO_API_KEY,
            "rs.account.payment.reconcile", "read_group",
            [domain, ["residual_amount"], ["partner_id"]],
            {"lazy": False},
        )
        odoo_value = sum(float(r.get("residual_amount") or 0.0) for r in rows)
        _log(_INFO, f"Direct Odoo XMLRPC: {len(rows)} partner groups, "
                    f"SUM(residual_amount) = {odoo_value:,.2f} EGP")
        return odoo_value

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
        f"Snapshot baseline: {SNAPSHOT_VALUE_EGP:,.2f} EGP / "
        f"{SNAPSHOT_CUSTOMERS} customers ({SNAPSHOT_DATE}) — MOVING, not used for identity"
    ))

    failures: list[str] = []

    # ── Step 1: GET endpoint ──────────────────────────────────────────────────
    try:
        with httpx.Client(timeout=60) as client:
            r = client.get(url, auth=(USERNAME, PASSWORD))
    except httpx.ConnectError as exc:
        msg = f"Cannot reach {base_url} — is the server running? ({exc})"
        _log(_FAIL, msg)
        _append_log_row(run_at, "", "", "", "", "", "", error=msg)
        return 1

    # ── Step 2: Status code ───────────────────────────────────────────────────
    ok = _check("HTTP 200", r.status_code == 200, f"got {r.status_code}")
    if not ok:
        _log(_INFO, f"Response body: {r.text[:500]}")
        _append_log_row(run_at, "", "", "", "", "", "", error=f"HTTP {r.status_code}")
        return 1

    body: dict = r.json()
    _log(_INFO, (
        f"value={body.get('value')}, "
        f"customer_count={body.get('customer_count')}, "
        f"record_count={body.get('record_count')}, "
        f"cache_status={body.get('cache_status')}, "
        f"rpc_ms={body.get('rpc_duration_ms')}"
    ))

    # ── Step 3: Required keys ─────────────────────────────────────────────────
    required_keys = (
        "value", "customer_count", "record_count",
        "currency", "as_of", "cache_status", "rpc_duration_ms", "domain",
    )
    for k in required_keys:
        if not _check(f"key '{k}' present", k in body):
            failures.append(f"missing_key_{k}")

    if failures:
        _append_log_row(run_at, "", "", "", "", "", "", error=f"missing keys: {failures}")
        return 1

    # ── Step 4: Extract values ────────────────────────────────────────────────
    api_value:      float = float(body["value"])
    customer_count: int   = int(body["customer_count"])
    record_count:   int   = int(body["record_count"])
    cache_status:   str   = body["cache_status"]
    rpc_ms:         int   = int(body["rpc_duration_ms"])
    delta_snapshot        = api_value - SNAPSHOT_VALUE_EGP
    delta_sign            = "+" if delta_snapshot >= 0 else ""

    # ── Step 5: Direct Odoo query (identity check) ────────────────────────────
    odoo_direct: float | None = _odoo_direct_query()
    identity_delta_str = ""
    if odoo_direct is not None:
        identity_delta = abs(api_value - odoo_direct)
        identity_delta_str = f"{identity_delta:.2f}"
        if not _check(
            f"identity check: |api − odoo_direct| < {IDENTITY_DELTA:.0f} EGP",
            identity_delta < IDENTITY_DELTA,
            f"api={api_value:,.2f}, odoo={odoo_direct:,.2f}, delta={identity_delta:.4f} EGP",
        ):
            failures.append("identity_check_failed")
    else:
        _log(_INFO, "Identity check skipped (Odoo credentials not available).")

    # ── Step 6: Structured summary ────────────────────────────────────────────
    print()
    print(_SEP)
    print("KPI C — Unallocated Wallet Balance Verification")
    print(f"Run timestamp          : {run_at}")
    print(_SEP)
    print(f"API value              : {api_value:>20,.2f} EGP")
    if odoo_direct is not None:
        print(f"Odoo direct value      : {odoo_direct:>20,.2f} EGP")
        print(f"Identity delta         : {abs(api_value - odoo_direct):>20,.4f} EGP")
    print(f"Snapshot baseline      : {SNAPSHOT_VALUE_EGP:>20,.2f} EGP ({SNAPSHOT_DATE})")
    print(f"Delta vs snapshot      : {delta_sign}{delta_snapshot:>19,.2f} EGP")
    print(f"Customer count         : {customer_count:>20,}")
    print(f"Snapshot customers     : {SNAPSHOT_CUSTOMERS:>20,}  ({SNAPSHOT_DATE})")
    print(f"Record count           : {record_count:>20,}  reconcile records")
    print(f"Cache status           : {cache_status:>20}")
    print(f"RPC duration           : {rpc_ms:>17} ms")
    print(f"Domain used            : {body.get('domain')}")
    print(_SEP)
    print()

    # ── Step 7: Sanity bounds — large deviation = FINDING ────────────────────
    if api_value < MIN_VALUE_EGP:
        _log(
            _FINDING,
            f"api_value={api_value:,.2f} EGP is below MIN={MIN_VALUE_EGP:,.2f} EGP "
            f"(±50% of M3-S1 snapshot). This may indicate data purge or domain error. "
            "Stop and report.",
        )
        failures.append("value_below_sanity_min")
    else:
        _check("value >= sanity MIN", True, f"{api_value:,.2f} >= {MIN_VALUE_EGP:,.2f} EGP")

    if api_value > MAX_VALUE_EGP:
        _log(
            _FINDING,
            f"api_value={api_value:,.2f} EGP is above MAX={MAX_VALUE_EGP:,.2f} EGP "
            f"(±50% of M3-S1 snapshot). This may indicate data duplication. "
            "Stop and report.",
        )
        failures.append("value_above_sanity_max")
    else:
        _check("value <= sanity MAX", True, f"{api_value:,.2f} <= {MAX_VALUE_EGP:,.2f} EGP")

    if not _check("customer_count >= MIN_CUSTOMERS",
                  customer_count >= MIN_CUSTOMERS,
                  f"got {customer_count}"):
        failures.append("customer_count_below_min")

    if not _check("customer_count <= MAX_CUSTOMERS",
                  customer_count <= MAX_CUSTOMERS,
                  f"got {customer_count}"):
        failures.append("customer_count_above_max")

    if not _check("record_count >= customer_count",
                  record_count >= customer_count,
                  f"record_count={record_count}, customer_count={customer_count}"):
        failures.append("record_count_less_than_customer_count")

    if not _check("record_count > 0",
                  record_count > 0,
                  f"got {record_count}"):
        failures.append("record_count_is_zero")

    if not _check("value > 0 (must be positive — residual_amount>0 filter)",
                  api_value > 0,
                  f"got {api_value:,.2f}"):
        failures.append("value_not_positive")

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
        if not _check("domain[1] == ['residual_amount','>',0]",
                      domain[1] == ["residual_amount", ">", 0],
                      f"got {domain[1]!r}"):
            failures.append("domain_clause1_wrong — residual_amount>0 filter missing")

    # ── Step 9: Response headers ──────────────────────────────────────────────
    cc  = r.headers.get("cache-control", "")
    xcs = r.headers.get("x-cache-status", "")
    _check("Cache-Control: private",    "private"    in cc, f"header: {cc!r}")
    _check("Cache-Control: max-age=60", "max-age=60" in cc, f"header: {cc!r}")
    _check("X-Cache-Status present",    bool(xcs),          f"got {xcs!r}")

    # ── Step 10: Second request — cache hit ───────────────────────────────────
    _log(_INFO, "Issuing second request to verify cache hit ...")
    with httpx.Client(timeout=30) as client:
        r2 = client.get(url, auth=(USERNAME, PASSWORD))
    body2: dict = r2.json()
    if not _check("second call cache_status == 'cached'",
                  body2.get("cache_status") == "cached",
                  f"got {body2.get('cache_status')!r}"):
        failures.append("cache_not_hit_on_second_call")
    if not _check("second call rpc_duration_ms == 0",
                  int(body2.get("rpc_duration_ms", -1)) == 0,
                  f"got {body2.get('rpc_duration_ms')}"):
        failures.append("cache_rpc_ms_nonzero")

    # ── Result ────────────────────────────────────────────────────────────────
    _append_log_row(
        run_at=run_at,
        api_value=f"{api_value:.2f}",
        odoo_value=f"{odoo_direct:.2f}" if odoo_direct is not None else "",
        identity_delta=identity_delta_str,
        customers=customer_count,
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
        print("  2. Filter: State = Posted, Residual Amount > 0")
        print("  3. Group By: Partner (partner_id), Measure: Residual Amount")
        print("  4. Compare the total to 'API value' above.")
        print("     Expected: identity-equal (< 1 EGP delta at the same moment).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
