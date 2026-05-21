"""
Diagnostic — Stage 6 bug: payment_state=paid sends HTTP 422.

Reproduces the bug reported during browser verification:
- Selecting the "paid" filter chip on any non-portfolio drill-down
  panel causes a 422 Unprocessable Entity.

Checks:
  D1. late?payment_state=paid             → 422 (invalid literal)
  D2. forecast/month?payment_state=paid   → 422
  D3. project/1?payment_state=paid        → 422
  D4. trend/{month}?payment_state=paid    → 422 (trend ALSO rejects paid)
  D5. late?payment_state=unpaid           → 200 (valid value still works)
  D6. late (no payment_state param)       → 200 (All-chip omit-param behavior OK)
  D7. late?has_pending_cheque=true        → 200 (cheque toggle not the cause)

Usage (server must be running, no ritual required — read-only probe):
    python scripts/diagnose_paid_filter_422.py [--url http://localhost:8000]
"""

import argparse
import io
import os
import sys
from datetime import date

import httpx
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

DEFAULT_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")
USERNAME    = os.environ.get("VERIFY_USERNAME", "admin")
PASSWORD    = os.environ.get("VERIFY_PASSWORD", "password")

_DD  = "/api/v1/collections/drilldown"

_PASS = "[PASS]"
_FAIL = "[FAIL]"
_INFO = "[INFO]"


def _trailing_month() -> str:
    """Return most-recent trailing calendar month as YYYY-MM."""
    y, m = date.today().year, date.today().month
    m -= 1
    if m == 0:
        m, y = 12, y - 1
    return f"{y:04d}-{m:02d}"


def check(label: str, condition: bool, detail: str = "") -> bool:
    mark = _PASS if condition else _FAIL
    print(f"{mark}  {label}{('  [' + detail + ']') if detail else ''}", flush=True)
    return condition


def probe(http: httpx.Client, url: str, params: dict, auth: tuple) -> int:
    try:
        r = http.get(url, params=params, auth=auth, timeout=15)
        return r.status_code
    except Exception as exc:
        print(f"{_FAIL}  Request error: {exc}", flush=True)
        return -1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_URL)
    args  = parser.parse_args()
    base  = args.url.rstrip("/")
    auth  = (USERNAME, PASSWORD)
    month = _trailing_month()

    print(f"\n{_INFO}  Target : {base}")
    print(f"{_INFO}  Auth   : {USERNAME}")
    print(f"{_INFO}  Trend month for D4: {month}")
    print()
    print("─" * 70)
    print("Reproducing: payment_state=paid sends 422 (invalid Literal)")
    print("─" * 70)

    failures = []

    with httpx.Client() as http:

        # D1 — late?payment_state=paid → 422
        sc = probe(http, f"{base}{_DD}/late", {"payment_state": "paid", "page_size": 1}, auth)
        if not check("D1: late?payment_state=paid → 422", sc == 422, f"got {sc}"):
            failures.append("D1")

        # D2 — forecast/month?payment_state=paid → 422
        sc = probe(http, f"{base}{_DD}/forecast/month", {"payment_state": "paid", "page_size": 1}, auth)
        if not check("D2: forecast/month?payment_state=paid → 422", sc == 422, f"got {sc}"):
            failures.append("D2")

        # D3 — project/1?payment_state=paid → 422
        sc = probe(http, f"{base}{_DD}/project/1", {"payment_state": "paid", "page_size": 1}, auth)
        if not check("D3: project/1?payment_state=paid → 422", sc == 422, f"got {sc}"):
            failures.append("D3")

        # D4 — trend/{month}?payment_state=paid → 422  (trend ALSO rejects paid)
        sc = probe(http, f"{base}{_DD}/trend/{month}", {"payment_state": "paid", "page_size": 1}, auth)
        if not check(
            f"D4: trend/{month}?payment_state=paid → 422  (Literal[unpaid,partial] — no paid)",
            sc == 422,
            f"got {sc}",
        ):
            failures.append("D4")

        print()
        print("─" * 70)
        print("Confirming: valid requests are NOT broken (All-chip, unpaid, cheque toggle)")
        print("─" * 70)

        # D5 — late?payment_state=unpaid → 200  (valid value)
        sc = probe(http, f"{base}{_DD}/late", {"payment_state": "unpaid", "page_size": 1}, auth)
        if not check("D5: late?payment_state=unpaid → 200  (valid value unaffected)", sc == 200, f"got {sc}"):
            failures.append("D5")

        # D6 — late (no payment_state) → 200  (All-chip omits param entirely)
        sc = probe(http, f"{base}{_DD}/late", {"page_size": 1}, auth)
        if not check("D6: late (no payment_state param) → 200  (All-chip behavior correct)", sc == 200, f"got {sc}"):
            failures.append("D6")

        # D7 — late?has_pending_cheque=true → 200  (cheque toggle not the 422 cause)
        sc = probe(http, f"{base}{_DD}/late", {"has_pending_cheque": "true", "page_size": 1}, auth)
        if not check(
            "D7: late?has_pending_cheque=true → 200  (cheque toggle is valid; not the cause of 422)",
            sc == 200,
            f"got {sc}",
        ):
            failures.append("D7")

    print()
    print("─" * 70)
    if failures:
        print(f"[FAIL]  {len(failures)} unexpected result(s): {failures}")
        print("        D1-D4 FAIL means server is down or the bug was already fixed.")
        print("        D5-D7 FAIL means a broader regression exists.")
        return 1
    else:
        print("[PASS]  All 7 diagnostic checks match expected behaviour.")
        print("        Root cause confirmed: payment_state=paid → 422 on all 4 endpoints.")
        print("        Cheque toggle is NOT the cause; valid params are unaffected.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
