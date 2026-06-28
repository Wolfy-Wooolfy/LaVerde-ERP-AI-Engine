"""
Live verification for KPI 2 — Late Uncollected receivables (PATH A).

Formula: SUM(amount) − SUM(x_studio_actual_paid_amount)
Decision 11.13 (Session 12) — reverses Decision 10.1 PATH C.
Pre-implementation discovery gate: commit 14600f3 (Phase A, 2026-05-20).
Service formula change: commit 5b8457b (Phase B, 2026-05-20).

Usage:
    KPI2_VERIFY_CONFIRMED=1 python scripts/verify_kpi2_live.py [--url http://localhost:8000]

Requires the FastAPI server to be running and reachable.
Set VERIFY_USERNAME / VERIFY_PASSWORD env vars (or .env) to override
the default admin credentials.

Exit 0  — all assertions passed
Exit 1  — at least one assertion failed or the server was unreachable
Exit 2  — Decision 6.4 ritual not confirmed (KPI2_VERIFY_CONFIRMED not set)

Appends one CSV row to logs/kpi2_verification.log on each run.

NOTE — Decision 6.4 restart ritual REQUIRED before running:
    1. Kill any uvicorn --reload server
    2. Purge __pycache__:  Get-ChildItem -Path . -Filter __pycache__ -Recurse -Directory | Remove-Item -Recurse -Force
    3. Start clean:        python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
    4. Set environment:    $env:KPI2_VERIFY_CONFIRMED = "1"
    5. Re-run this script
"""

import argparse
import asyncio
import csv
import io
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

# Ensure project root is on sys.path so `backend.*` imports work when the
# script is invoked as `python scripts/verify_kpi2_live.py`.
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import httpx
from dotenv import load_dotenv

from _lib.api_session import ApiLoginError, login as api_login

load_dotenv(dotenv_path=".env")

# Force UTF-8 stdout (Windows consoles default to cp1252)
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Decision 6.4 ritual text ─────────────────────────────────────────────────

_RITUAL = """
┌─────────────────────────────────────────────────────────────────┐
│  Decision 6.4 — Pre-Verification Ritual (Windows PowerShell)    │
├─────────────────────────────────────────────────────────────────┤
│  1. Get-Process -Name python -EA SilentlyContinue |             │
│       Stop-Process -Force                                       │
│  2. Get-ChildItem -Path . -Filter __pycache__ -Recurse          │
│       -Directory | Remove-Item -Recurse -Force                  │
│  3. python -m uvicorn backend.main:app --host 0.0.0.0           │
│       --port 8000        (NO --reload flag)                     │
│  4. Set environment: $env:KPI2_VERIFY_CONFIRMED = "1"           │
│  5. Re-run: python scripts/verify_kpi2_live.py                  │
└─────────────────────────────────────────────────────────────────┘
"""

# ── Config ────────────────────────────────────────────────────────────────────

DEFAULT_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")
USERNAME = os.environ.get("VERIFY_USERNAME", "admin")
PASSWORD = os.environ.get("VERIFY_PASSWORD", "password")
ENDPOINT = "/api/v1/collections/kpi/late-uncollected"
LOG_FILE = "logs/kpi2_verification.log"

# Sanity bounds — PATH A per Decision 11.13; previously bounded PATH C value.
# Both limits still bracket the new ~329M EGP figure correctly.
# Adjust after data-entry sprint completes (~2026-06-16).
MIN_VALUE_EGP = 1_000_000.0       # at least 1M EGP (data-entry in progress)
MAX_VALUE_EGP = 2_000_000_000.0   # at most 2B EGP (sanity upper bound)
MIN_RECORD_COUNT = 1

# ── Helpers ───────────────────────────────────────────────────────────────────

_PASS = "[PASS]"
_FAIL = "[FAIL]"
_INFO = "[INFO]"
_WARN = "[WARN]"


def _log(prefix: str, msg: str) -> None:
    print(f"{prefix} {msg}", flush=True)


def _append_log_row(row: dict) -> None:
    os.makedirs("logs", exist_ok=True)
    file_exists = os.path.isfile(LOG_FILE)
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["run_at", "result", "value_egp", "record_count",
                        "cache_status", "rpc_duration_ms", "error"],
        )
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


# ── Assertions ────────────────────────────────────────────────────────────────

def _check(label: str, condition: bool, detail: str = "") -> bool:
    if condition:
        _log(_PASS, f"{label}{(' — ' + detail) if detail else ''}")
    else:
        _log(_FAIL, f"{label}{(' — ' + detail) if detail else ''}")
    return condition


# ── Cross-check via direct Odoo read_group ────────────────────────────────────

async def _derive_cheques_directly(today_date_str: str) -> dict:
    """Independently derives KPI 2 field sums via direct Odoo read_group.

    Uses the same Candidate C late domain as the service function.
    Returns a dict with all four monetary sums needed for PATH A
    cross-checks (H2 identity, Total Due, cheques subset).
    Single read_group call — 1 RPC total.
    """
    from backend.shared.odoo.client import OdooClient  # noqa: PLC0415
    late_domain_rpc = [
        ("state", "=", "post"),
        ("payment_state", "in", ["unpaid", "partial"]),
        ("date", "<", today_date_str),
    ]
    async with OdooClient() as odoo:
        rows = await odoo.execute_kw(
            "rs.installment",
            "read_group",
            args=[late_domain_rpc,
                  ["amount", "paid_amount", "x_studio_actual_paid_amount", "total_due_amount"],
                  []],
            kwargs={"lazy": False},
        )
    row = rows[0] if rows else {}
    amount_sum  = float(row.get("amount") or 0.0)
    paid_sum    = float(row.get("paid_amount") or 0.0)
    actual_sum  = float(row.get("x_studio_actual_paid_amount") or 0.0)
    total_due   = float(row.get("total_due_amount") or 0.0)
    return {
        "amount_sum":          amount_sum,
        "paid_sum":            paid_sum,
        "actual_paid_sum":     actual_sum,
        "total_due_sum":       total_due,
        "cheques_in_pipeline": max(paid_sum - actual_sum, 0.0),
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    # ── Decision 6.4 ritual guard ─────────────────────────────────────────────
    if os.environ.get("KPI2_VERIFY_CONFIRMED") != "1":
        print(_RITUAL)
        print("REFUSED. Set KPI2_VERIFY_CONFIRMED=1 after completing")
        print("the ritual above, then re-run this script.")
        sys.exit(2)

    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_URL, help="Backend base URL")
    args = parser.parse_args()
    base_url: str = args.url.rstrip("/")

    url = f"{base_url}{ENDPOINT}"
    _log(_INFO, f"Target: GET {url}")
    _log(_INFO, f"Auth user: {USERNAME}")

    run_at = datetime.now(timezone.utc).isoformat()
    log_row: dict = {
        "run_at": run_at,
        "result": "FAIL",
        "value_egp": "",
        "record_count": "",
        "cache_status": "",
        "rpc_duration_ms": "",
        "error": "",
    }

    failures: list[str] = []

    # ── Step 1: ONE login per process (limiter 10/minute), then GET ──────────
    try:
        client = api_login(base_url)
    except ApiLoginError as exc:
        _log(_FAIL, f"Session login failed: {exc}")
        log_row["error"] = f"login failed: {exc}"
        _append_log_row(log_row)
        return 1
    except httpx.ConnectError as exc:
        msg = f"Cannot reach {base_url} — is the server running? ({exc})"
        _log(_FAIL, msg)
        log_row["error"] = msg
        _append_log_row(log_row)
        return 1

    try:
        r = client.get(ENDPOINT, timeout=30)

        # ── Step 2: Status code ───────────────────────────────────────────────────
        ok = _check("HTTP 200", r.status_code == 200,
                    f"got {r.status_code}")
        if not ok:
            failures.append("http_status")
            _log(_INFO, f"Response body: {r.text[:500]}")
            log_row["error"] = f"HTTP {r.status_code}"
            _append_log_row(log_row)
            return 1

        body: dict = r.json()
        _log(_INFO, f"Response body: {body}")

        # ── Step 3: Required keys ─────────────────────────────────────────────────
        required_keys = ("value", "currency", "record_count", "as_of",
                         "cache_status", "rpc_duration_ms", "domain",
                         "cheques_in_pipeline", "cheques_record_count",
                         "drill_down_domain", "cheques_drill_down_domain",
                         "data_quality_warning")
        for k in required_keys:
            if not _check(f"key '{k}' present", k in body):
                failures.append(f"missing_key_{k}")

        if failures:
            log_row["error"] = f"missing keys: {failures}"
            _append_log_row(log_row)
            return 1

        # ── Step 4: Value sanity ──────────────────────────────────────────────────
        value: float = float(body["value"])
        record_count: int = int(body["record_count"])
        cache_status: str = body["cache_status"]
        rpc_ms: int = int(body["rpc_duration_ms"])

        log_row.update({
            "value_egp": f"{value:,.2f}",
            "record_count": record_count,
            "cache_status": cache_status,
            "rpc_duration_ms": rpc_ms,
        })

        _log(_INFO, f"Late Uncollected: EGP {value:>20,.2f}")
        _log(_INFO, f"Record count:     {record_count:>20,}")
        _log(_INFO, f"Cache status:     {cache_status:>20}")
        _log(_INFO, f"RPC duration:     {rpc_ms:>17} ms")

        if not _check("value >= MIN_VALUE_EGP",
                      value >= MIN_VALUE_EGP,
                      f"{value:,.2f} >= {MIN_VALUE_EGP:,.2f}"):
            failures.append("value_below_min")

        if not _check("value <= MAX_VALUE_EGP",
                      value <= MAX_VALUE_EGP,
                      f"{value:,.2f} <= {MAX_VALUE_EGP:,.2f}"):
            failures.append("value_above_max")

        if not _check("record_count >= MIN_RECORD_COUNT",
                      record_count >= MIN_RECORD_COUNT,
                      f"got {record_count}"):
            failures.append("record_count_zero")

        if not _check("currency == 'EGP'",
                      body.get("currency") == "EGP",
                      f"got {body.get('currency')!r}"):
            failures.append("wrong_currency")

        if not _check("cache_status in {fresh, cached}",
                      cache_status in {"fresh", "cached"},
                      f"got {cache_status!r}"):
            failures.append("bad_cache_status")

        # ── Step 5: Response headers ──────────────────────────────────────────────
        cc = r.headers.get("cache-control", "")
        _check("Cache-Control: private", "private" in cc, f"header: {cc!r}")
        _check("Cache-Control: max-age=60", "max-age=60" in cc, f"header: {cc!r}")
        xcs = r.headers.get("x-cache-status", "")
        _check("X-Cache-Status header present", bool(xcs), f"got {xcs!r}")

        # ── Step 6: Domain shape ──────────────────────────────────────────────────
        domain: list = body.get("domain", [])
        if _check("domain has 3 clauses", len(domain) == 3, f"got {len(domain)}"):
            _check("domain[0] == state=post",
                   domain[0] == ["state", "=", "post"])
            _check("domain[1] == payment_state in [unpaid,partial]",
                   domain[1] == ["payment_state", "in", ["unpaid", "partial"]])
            _check("domain[2][0] == date", domain[2][0] == "date")
            _check("domain[2][1] == <", domain[2][1] == "<")
            date_str = domain[2][2]
            try:
                parsed_date = date.fromisoformat(date_str)
                today_utc = date.today()
                delta_days = abs((parsed_date - today_utc).days)
                if not _check(
                    "domain[2][2] is a valid recent ISO date",
                    delta_days <= 1,
                    f"got {date_str!r}",
                ):
                    failures.append("domain_date_stale")
                    _log(_INFO, f"  delta from UTC today: {delta_days} day(s)")
            except ValueError as exc:
                _log(_FAIL, f"domain[2][2] is not a valid ISO date — got {date_str!r}: {exc}")
                failures.append("domain_date_invalid")
                date_str = ""
        else:
            failures.append("domain_shape")
            date_str = ""

        # ── Step 6b: Cheques fields ───────────────────────────────────────────────
        cheques_in_pipeline: float = float(body.get("cheques_in_pipeline") or 0.0)
        cheques_record_count = body.get("cheques_record_count")
        drill_down_domain: list = body.get("drill_down_domain", [])
        cheques_drill_down_domain = body.get("cheques_drill_down_domain")
        data_quality_warning = body.get("data_quality_warning")

        _log(_INFO, f"Cheques in pipeline:  EGP {cheques_in_pipeline:>16,.2f}")
        _log(_INFO, f"Cheques record count: {str(cheques_record_count):>20}")
        _log(_INFO, f"Cheques drill_down:   {str(cheques_drill_down_domain):>20}")

        if not _check("cheques_in_pipeline >= 0",
                      cheques_in_pipeline >= 0,
                      f"{cheques_in_pipeline:,.2f}"):
            failures.append("cheques_negative")

        if not _check("cheques_in_pipeline <= value",
                      cheques_in_pipeline <= value,
                      f"{cheques_in_pipeline:,.2f} <= {value:,.2f}"):
            failures.append("cheques_exceeds_value")

        if not _check("cheques_record_count is null",
                      cheques_record_count is None,
                      f"got {cheques_record_count!r}"):
            failures.append("cheques_record_count_not_null")

        if _check("drill_down_domain has 3 clauses",
                  len(drill_down_domain) == 3,
                  f"got {len(drill_down_domain)}"):
            if not _check("drill_down_domain == legacy domain",
                          drill_down_domain == domain,
                          f"drill_down={drill_down_domain!r}"):
                failures.append("drill_down_domain_mismatch")
        else:
            failures.append("drill_down_domain_shape")

        if not _check("cheques_drill_down_domain is null",
                      cheques_drill_down_domain is None,
                      f"got {cheques_drill_down_domain!r}"):
            failures.append("cheques_drill_down_not_null")

        if data_quality_warning is not None:
            _log(_WARN, f"data_quality_warning present: {data_quality_warning!r}")

        # ── Step 7: Second request — cache hit ───────────────────────────────────
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

    # ── Step 8: Cross-check via direct Odoo read_group (PATH A — Decision 11.13) ──
    _log(_INFO, "Cross-checking KPI 2 PATH A assertions against direct Odoo read_group ...")
    if date_str:
        try:
            odoo_sums        = asyncio.run(_derive_cheques_directly(date_str))
            amount_sum       = odoo_sums["amount_sum"]
            paid_sum         = odoo_sums["paid_sum"]
            actual_paid_sum  = odoo_sums["actual_paid_sum"]
            total_due_sum    = odoo_sums["total_due_sum"]
            derived_cheques  = odoo_sums["cheques_in_pipeline"]

            _log(_INFO, f"  Odoo amount_sum              : EGP {amount_sum:>16,.2f}")
            _log(_INFO, f"  Odoo paid_sum                : EGP {paid_sum:>16,.2f}")
            _log(_INFO, f"  Odoo actual_paid_sum         : EGP {actual_paid_sum:>16,.2f}")
            _log(_INFO, f"  Odoo total_due_sum           : EGP {total_due_sum:>16,.2f}")
            _log(_INFO, f"  Derived cheques_in_pipeline  : EGP {derived_cheques:>16,.2f}")
            _log(_INFO, f"  API backend_value            : EGP {value:>16,.2f}")
            _log(_INFO, f"  API cheques_in_pipeline      : EGP {cheques_in_pipeline:>16,.2f}")

            # (a) H2 identity: backend value must equal amount_sum − actual_paid_sum to ±1 EGP.
            h2_delta = abs(value - (amount_sum - actual_paid_sum))
            _log(_INFO, f"  H2 identity delta            : EGP {h2_delta:>16,.4f}")
            if not _check(
                "cross-check (a): |backend_value - (amount_sum - actual_paid_sum)| <= 1.00 EGP",
                h2_delta <= 1.0,
                f"delta={h2_delta:.4f}",
            ):
                failures.append("cross_check_h2_identity")

            # (b) Total Due cross-check: backend value must match total_due_sum to ±1 EGP.
            #     Live confirmation of Phase A H2 on every verify run.
            td_delta = abs(value - total_due_sum)
            _log(_INFO, f"  Total Due delta              : EGP {td_delta:>16,.4f}")
            if not _check(
                "cross-check (b): |backend_value - total_due_sum| <= 1.00 EGP",
                td_delta <= 1.0,
                f"delta={td_delta:.4f}",
            ):
                failures.append("cross_check_total_due")

            # (c) Cheques subset: derived cheques must be <= backend_value.
            #     Under PATH A this is mathematically required (cheques ⊂ value).
            #     Under PATH C it was not — cheques were subtracted out of the headline.
            if not _check(
                "cross-check (c): derived cheques_in_pipeline <= backend_value (PATH A subset)",
                derived_cheques <= value,
                f"cheques={derived_cheques:,.2f}, value={value:,.2f}",
            ):
                failures.append("cross_check_cheques_subset")

            # Legacy: API cheques_in_pipeline must match Odoo-derived value to ±1 EGP.
            cheques_delta = abs(cheques_in_pipeline - derived_cheques)
            _log(_INFO, f"  Cheques API vs derived delta : EGP {cheques_delta:>16,.4f}")
            if not _check(
                "cross-check (legacy): |API cheques - Odoo derived| <= 1.00 EGP",
                cheques_delta <= 1.0,
                f"delta={cheques_delta:.4f}",
            ):
                failures.append("cheques_cross_check_delta")

        except Exception as exc:
            _log(_WARN, f"Cross-check failed — direct Odoo RPC error: {exc}")
            _log(_WARN, "VERIFICATION INCOMPLETE — PATH A cross-checks did not run")
            failures.append("cheques_cross_check_failed")
    else:
        _log(_WARN, "Cross-check skipped — domain date_str unavailable (domain shape failed earlier)")
        failures.append("cheques_cross_check_skipped")

    # ── Result ────────────────────────────────────────────────────────────────
    if failures:
        log_row["error"] = "; ".join(failures)
        _append_log_row(log_row)
        _log(_FAIL, f"Verification FAILED — {len(failures)} assertion(s): {failures}")
        return 1

    log_row["result"] = "PASS"
    _append_log_row(log_row)
    print()
    _log(_PASS, "All assertions passed.")
    print()
    print("Manual cross-check prompt (for Khaled):")
    print("  Open Odoo → Collections Mgmt → All Installments →")
    print(f"    State=Posted, Payment State IN [Unpaid, Partial], Due Date < {date_str}")
    print(f"  The Due Amount aggregate should be approximately EGP {value:,.0f}.")
    print(f"  Record count should be approximately {record_count:,}.")
    print()
    print("  Cheques cross-check:")
    print("    Add filter: Has Checks = True.")
    print("    Switch to Pivot. Measures: Paid Amount + Actual Paid Amount.")
    print("    Compute: Paid Amount − Actual Paid Amount.")
    print(f"    Expected cheques_in_pipeline: EGP {cheques_in_pipeline:,.0f} ± 1,000 EGP")
    print("    (Drift is normal if new cheques were posted since the server call.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
