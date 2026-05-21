"""
Diagnostic — Stage 6 bug: project drill-down shows "فشل التحميل", Console silent.

Root cause (from static analysis):
  _renderInstallments() in drilldown.js line 272 calls _escHtml(pName).
  _escHtml is NOT defined anywhere in the IIFE; only _esc() exists (line 418).
  ReferenceError is thrown inside .then(), caught silently by .catch(),
  which renders the Arabic error UI with NO console.error call.

This script confirms the network layer is NOT the cause:

  P1. project/1  → 200 with data.project_name_ar and data.project_name_en
  P2. project/2  → 200 with project name fields present
  P3. project/3  → 200 with project name fields present
  P4. Response shape: data.items[0] has customer_name, total, payment_state
  P5. project/1?payment_state=unpaid → 200 (filter works)
  P6. project/1?sort_by=amount&sort_dir=asc → 200 (sort works)

All P1-P6 passing → bug is in JS rendering, not networking.

Usage (server must be running):
    python scripts/diagnose_project_drilldown_eschtml.py [--url http://localhost:8000]
"""

import argparse
import io
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

DEFAULT_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")
USERNAME    = os.environ.get("VERIFY_USERNAME", "admin")
PASSWORD    = os.environ.get("VERIFY_PASSWORD", "password")

_DD   = "/api/v1/collections/drilldown"
_PASS = "[PASS]"
_FAIL = "[FAIL]"
_INFO = "[INFO]"


def check(label: str, condition: bool, detail: str = "") -> bool:
    mark = _PASS if condition else _FAIL
    print(f"{mark}  {label}{('  [' + detail + ']') if detail else ''}", flush=True)
    return condition


def probe(http: httpx.Client, url: str, params: dict, auth: tuple):
    try:
        r = http.get(url, params=params, auth=auth, timeout=15)
        return r.status_code, r
    except Exception as exc:
        print(f"{_FAIL}  Request error: {exc}", flush=True)
        return -1, None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_URL)
    args  = parser.parse_args()
    base  = args.url.rstrip("/")
    auth  = (USERNAME, PASSWORD)

    print(f"\n{_INFO}  Target : {base}")
    print(f"{_INFO}  Auth   : {USERNAME}")
    print()
    print("─" * 70)
    print("Confirming: project endpoint is NOT the source of 'فشل التحميل'")
    print("Root cause is _escHtml undefined in drilldown.js IIFE (line 272)")
    print("─" * 70)

    failures = []

    with httpx.Client() as http:

        # P1 — project/1 returns 200 with project name fields
        sc, r = probe(http, f"{base}{_DD}/project/1", {"page_size": 1}, auth)
        ok = sc == 200
        if not check("P1: project/1 → 200", ok, f"got {sc}"):
            failures.append("P1")
        else:
            body = r.json()
            data = body.get("data", {})
            has_ar = "project_name_ar" in data
            has_en = "project_name_en" in data
            check("P1a: data.project_name_ar present", has_ar,
                  f"value={data.get('project_name_ar')!r}")
            check("P1b: data.project_name_en present", has_en,
                  f"value={data.get('project_name_en')!r}")
            if not has_ar:
                failures.append("P1a")
            if not has_en:
                failures.append("P1b")

        # P2 — project/2 returns 200
        sc, _ = probe(http, f"{base}{_DD}/project/2", {"page_size": 1}, auth)
        if not check("P2: project/2 → 200", sc == 200, f"got {sc}"):
            failures.append("P2")

        # P3 — project/3 returns 200
        sc, _ = probe(http, f"{base}{_DD}/project/3", {"page_size": 1}, auth)
        if not check("P3: project/3 → 200", sc == 200, f"got {sc}"):
            failures.append("P3")

        # P4 — response shape: data.items[0] has required row fields
        sc, r = probe(http, f"{base}{_DD}/project/1", {"page_size": 1}, auth)
        if sc == 200 and r is not None:
            items = r.json().get("data", {}).get("items", [])
            if items:
                row = items[0]
                for field in ("customer_name", "total", "payment_state"):
                    ok = field in row
                    if not check(f"P4: items[0].{field} present", ok,
                                 f"value={row.get(field)!r}"):
                        failures.append(f"P4-{field}")
            else:
                print(f"{_INFO}  P4: data.items is empty — no row fields to check (no overdue records?)")

        # P5 — filter by payment_state=unpaid works
        sc, _ = probe(http, f"{base}{_DD}/project/1", {"payment_state": "unpaid", "page_size": 1}, auth)
        if not check("P5: project/1?payment_state=unpaid → 200", sc == 200, f"got {sc}"):
            failures.append("P5")

        # P6 — sort params accepted
        sc, _ = probe(http, f"{base}{_DD}/project/1",
                      {"sort_by": "amount", "sort_dir": "asc", "page_size": 1}, auth)
        if not check("P6: project/1?sort_by=amount&sort_dir=asc → 200", sc == 200, f"got {sc}"):
            failures.append("P6")

    print()
    print("─" * 70)
    if failures:
        print(f"[FAIL]  {len(failures)} unexpected result(s): {failures}")
        print("        If P1-P3 fail: server is down or project IDs differ.")
        print("        If P4-P6 fail: endpoint response shape has changed.")
        return 1
    else:
        print("[PASS]  All checks passed.")
        print("        Network layer is healthy. Bug is in drilldown.js line 272:")
        print("        _escHtml(pName) — _escHtml is not defined in the IIFE.")
        print("        Fix: _title.textContent = pName + ' — ' + (S.dd_title_project || 'Late Detail');")
        return 0


if __name__ == "__main__":
    sys.exit(main())
