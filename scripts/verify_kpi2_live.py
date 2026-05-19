"""
Live verification for KPI 2 — Late Uncollected receivables.

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

import httpx
from dotenv import load_dotenv

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

# Sanity bounds — adjust after data-entry sprint completes (~2026-06-16)
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

async def _derive_cheques_directly(today_date_str: str) -> float:
    """Independently derives cheques_in_pipeline via direct Odoo read_group.

    Uses the same Candidate C late domain and Alternative B formula as the
    service function. Result should match the API response within ±1.00 EGP.
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
            args=[late_domain_rpc, ["paid_amount", "x_studio_actual_paid_amount"], []],
            kwargs={"lazy": False},
        )
    row = rows[0] if rows else {}
    paid   = float(row.get("paid_amount") or 0.0)
    actual = float(row.get("x_studio_actual_paid_amount") or 0.0)
    return max(paid - actual, 0.0)


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

    # ── Step 1: GET /api/v1/collections/kpi/late-uncollected ─────────────────
    try:
        with httpx.Client(timeout=30) as client:
            r = client.get(url, auth=(USERNAME, PASSWORD))
    except httpx.ConnectError as exc:
        msg = f"Cannot reach {base_url} — is the server running? ({exc})"
        _log(_FAIL, msg)
        log_row["error"] = msg
        _append_log_row(log_row)
        return 1

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

    # ── Step 8: Cross-check cheques_in_pipeline via direct Odoo read_group ───
    _log(_INFO, "Cross-checking cheques_in_pipeline against direct Odoo read_group ...")
    if date_str:
        try:
            derived = asyncio.run(_derive_cheques_directly(date_str))
            delta   = abs(cheques_in_pipeline - derived)
            _log(_INFO, f"  API cheques_in_pipeline : EGP {cheques_in_pipeline:>16,.2f}")
            _log(_INFO, f"  Odoo derived            : EGP {derived:>16,.2f}")
            _log(_INFO, f"  Delta                   : EGP {delta:>16,.4f}")
            if not _check(
                "cross-check: |API cheques - Odoo derived| <= 1.00 EGP",
                delta <= 1.0,
                f"delta={delta:.4f}",
            ):
                failures.append("cheques_cross_check_delta")
        except Exception as exc:
            _log(_WARN, f"Cross-check failed — direct Odoo RPC error: {exc}")
            _log(_WARN, "VERIFICATION INCOMPLETE — cheques cross-check did not run")
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
