"""
Live verification for Stage 5 — Drill-Down Endpoints (Module 2 Collections Dashboard).

Decision 14.1–14.12: five paginated drill-down endpoints verified against
their parent KPIs with identity-equal assertions.

Usage:
    DRILLDOWN_VERIFY_CONFIRMED=1 python scripts/verify_drilldowns_live.py [--url http://localhost:8000]

Requires the FastAPI server to be running and reachable.
Set VERIFY_USERNAME / VERIFY_PASSWORD env vars (or .env) to override
the default admin credentials.

Exit 0  — all assertions passed
Exit 1  — at least one assertion failed or the server was unreachable
Exit 2  — Decision 6.4 ritual not confirmed (DRILLDOWN_VERIFY_CONFIRMED not set)

Appends one tab-separated row to logs/drilldown_verify_<date>.log on each run.

NOTE — Decision 6.4 restart ritual REQUIRED before running:
    (1) Stop-Process -Name python -Force
    (2) Get-ChildItem -Path . -Filter __pycache__ -Recurse -Directory |
            Remove-Item -Recurse -Force
    (3) python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
            (NO --reload flag)
    (4) $env:DRILLDOWN_VERIFY_CONFIRMED = "1"
    (5) python scripts/verify_drilldowns_live.py

Field-mapping audit (read before editing V2 / V3):
  V2: bucket.amount  = SUM(rs.installment.amount)  — face value (contractual).
      bucket.due_amount = SUM(rs.installment.due_amount) — remaining balance.
      Both identities are asserted: SUM(item["amount"]) == bucket.amount,
      SUM(item["due_amount"]) == bucket.due_amount.
  V3: KPI 1 value    = SUM(rs.installment.amount) for state=post (ALL payment states).
      Portfolio identity: SUM(customer["total_amount"]) == KPI 1 value.
      Do NOT sum total_due — that is remaining balance and does not equal KPI 1.
"""

import argparse
import io
import os
import sys
from datetime import date
from datetime import datetime
from datetime import timezone

import httpx
from dotenv import load_dotenv

from _lib.api_session import ApiLoginError, login as api_login

load_dotenv(dotenv_path=".env")

# Force UTF-8 stdout (Windows consoles default to cp1252)
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Decision 6.4 ritual text ──────────────────────────────────────────────────

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
│  4. Set environment: $env:DRILLDOWN_VERIFY_CONFIRMED = "1"      │
│  5. Re-run: python scripts/verify_drilldowns_live.py            │
└─────────────────────────────────────────────────────────────────┘
"""

# ── Config ────────────────────────────────────────────────────────────────────

DEFAULT_URL  = os.environ.get("BACKEND_URL", "http://localhost:8000")
USERNAME     = os.environ.get("VERIFY_USERNAME", "admin")
PASSWORD     = os.environ.get("VERIFY_PASSWORD", "password")

_API_PREFIX  = "/api/v1/collections"
_KPI_PREFIX  = f"{_API_PREFIX}/kpi"
_DD_PREFIX   = f"{_API_PREFIX}/drilldown"

_SEP  = "═" * 72
_SEP2 = "─" * 70
_PASS = "[PASS]"
_FAIL = "[FAIL]"
_INFO = "[INFO]"
_WARN = "[WARN]"

_BUCKET_URL_KEYS   = ("month", "quarter", "half", "year")
_BUCKET_INTERNALS  = ("this_month", "this_quarter", "this_half", "this_year")
_PROJECT_IDS       = (1, 2, 3)

# Tolerance for floating-point monetary identity checks (EGP).
# KPI values may be cached while drilldowns are live; 1 EGP absorbs normal drift.
_MONEY_TOL = 1.0


# ── Helpers ───────────────────────────────────────────────────────────────────

_PASS_MARK = "[PASS]"
_FAIL_MARK = "[FAIL]"


def _log(prefix: str, msg: str) -> None:
    print(f"{prefix} {msg}", flush=True)


def _check(label: str, condition: bool, detail: str = "") -> bool:
    marker = _PASS_MARK if condition else _FAIL_MARK
    _log(marker, f"{label}{(' — ' + detail) if detail else ''}")
    return condition


def _append_log(
    run_at: str,
    result: str,
    passed: int,
    failed: int,
    failures: list[str],
) -> None:
    today_str = date.today().isoformat()
    log_file  = f"logs/drilldown_verify_{today_str}.log"
    os.makedirs("logs", exist_ok=True)
    log_exists = os.path.isfile(log_file)
    with open(log_file, "a", encoding="utf-8") as f:
        if not log_exists:
            f.write("run_at\tresult\tpassed\tfailed\tfailures\n")
        f.write(
            f"{run_at}\t{result}\t{passed}\t{failed}\t"
            f"{','.join(failures) if failures else 'none'}\n"
        )
    _log(_INFO, f"Log appended: {log_file}")


def _trailing_months(n: int = 6) -> list[str]:
    """Return the n trailing calendar months as YYYY-MM strings, most recent first."""
    result = []
    y, m = date.today().year, date.today().month
    for _ in range(n):
        result.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return result


# ── Core pagination helper ────────────────────────────────────────────────────

def walk_all_pages(
    http: httpx.Client,
    url: str,
    extra_params: dict | None = None,
    data_key: str = "items",
    page_size: int = 50,
) -> tuple[list[dict], int, int]:
    """Walk all cursor-based pages for a drill-down endpoint.

    Accumulates data[data_key] across all pages.
    Returns (all_items, page_count, total_count_from_meta).

    Safety cap: 200 pages (200 × 50 = 10,000 records). If exceeded, raises
    RuntimeError — a cursor bug causing an infinite loop must not hang the script.
    On each page asserts version=="1.0" and meta.request_id present.
    """
    params: dict = {"page_size": page_size}
    if extra_params:
        params.update(extra_params)

    all_items:   list[dict] = []
    page_count   = 0
    total_count  = 0
    cursor: str | None = None

    while True:
        if cursor is not None:
            params["cursor"] = cursor
        elif "cursor" in params:
            del params["cursor"]

        r = http.get(url, params=params)
        if r.status_code != 200:
            raise RuntimeError(
                f"walk_all_pages: HTTP {r.status_code} from {url}  "
                f"params={params}  body={r.text[:300]}"
            )

        body = r.json()

        if body.get("version") != "1.0":
            raise RuntimeError(
                f"walk_all_pages: version != '1.0' — got {body.get('version')!r}"
            )
        if not body.get("meta", {}).get("request_id"):
            raise RuntimeError("walk_all_pages: meta.request_id missing or empty")

        data  = body.get("data", {})
        items = data.get(data_key, [])
        all_items.extend(items)

        meta = body.get("meta", {})
        if page_count == 0:
            total_count = int(meta.get("total_count", 0))

        page_count += 1

        if page_count >= 200:
            raise RuntimeError(
                f"walk_all_pages: safety cap (200 pages) reached for {url}. "
                f"Cursor bug suspected — aborting to prevent infinite loop."
            )

        cursor = meta.get("cursor_next") or None
        if cursor is None:
            break

    return all_items, page_count, total_count


# ── Parent KPI fetcher ────────────────────────────────────────────────────────

def fetch_parent_kpi(
    http: httpx.Client,
    url: str,
) -> dict:
    """GET a KPI endpoint and return its JSON body.

    Raises RuntimeError if the HTTP status is not 200.
    Reads live (no local caching) so natural data drift between sessions is captured.
    """
    r = http.get(url)
    if r.status_code != 200:
        raise RuntimeError(
            f"fetch_parent_kpi: HTTP {r.status_code} from {url}  body={r.text[:300]}"
        )
    return r.json()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    # ── Decision 6.4 ritual guard ─────────────────────────────────────────────
    if os.environ.get("DRILLDOWN_VERIFY_CONFIRMED") != "1":
        print(_RITUAL)
        print("REFUSED. Set DRILLDOWN_VERIFY_CONFIRMED=1 after completing")
        print("the ritual above, then re-run this script.")
        sys.exit(2)

    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_URL, help="Backend base URL")
    args = parser.parse_args()
    base_url: str = args.url.rstrip("/")

    run_at = datetime.now(timezone.utc).isoformat()

    _log(_INFO, f"Target  : {base_url}")
    _log(_INFO, f"Auth    : {USERNAME}")
    _log(_INFO, f"Run at  : {run_at}")
    print()

    failures:     list[str] = []
    block_results: dict[str, bool] = {}   # V1..V8 → True/False

    # ── Login once (limiter 10/minute), reuse the client for every request ────
    try:
        http = api_login(base_url)
    except ApiLoginError as exc:
        _log(_FAIL, f"Session login failed: {exc}")
        _append_log(run_at, "FAIL", 0, 1, ["login_failed"])
        return 1
    except httpx.ConnectError as exc:
        _log(_FAIL, f"Cannot reach {base_url} — is the server running? ({exc})")
        _append_log(run_at, "FAIL", 0, 1, ["connect_error"])
        return 1

    try:

        # ── V1 — Late drill-down identity-equal ───────────────────────────────
        print(_SEP)
        _log(_INFO, "V1 — Late drill-down identity-equal")
        print(_SEP2)
        v1_ok = True
        try:
            # Read parent KPI 2 live (used as identity target)
            kpi2 = fetch_parent_kpi(
                http, f"{base_url}{_KPI_PREFIX}/late-uncollected"
            )
            kpi2_value  = float(kpi2["value"])
            kpi2_count  = int(kpi2["record_count"])
            _log(_INFO, f"  KPI 2 value        : EGP {kpi2_value:>22,.2f}")
            _log(_INFO, f"  KPI 2 record_count : {kpi2_count:>26,}")

            items, page_count, total_count = walk_all_pages(
                http,
                f"{base_url}{_DD_PREFIX}/late",
            )
            late_sum   = sum(item["late_amount"] for item in items)
            item_count = len(items)
            _log(_INFO, f"  Drilldown pages    : {page_count:>26,}")
            _log(_INFO, f"  Drilldown items    : {item_count:>26,}")
            _log(_INFO, f"  Drilldown total_count (meta) : {total_count:>16,}")
            _log(_INFO, f"  SUM(late_amount)   : EGP {late_sum:>22,.2f}")
            _log(_INFO, f"  Delta vs KPI 2     : EGP {abs(late_sum - kpi2_value):>22,.4f}")

            delta = abs(late_sum - kpi2_value)
            if not _check(
                "V1a: |SUM(late_amount) − KPI2.value| <= 1.00 EGP",
                delta <= _MONEY_TOL,
                f"sum={late_sum:,.2f}  kpi2={kpi2_value:,.2f}  delta={delta:.4f}",
            ):
                failures.append("v1_sum_delta")
                v1_ok = False

            if not _check(
                "V1b: drilldown total_count == KPI 2 record_count",
                total_count == kpi2_count,
                f"drilldown={total_count}  kpi2={kpi2_count}",
            ):
                failures.append("v1_count_mismatch")
                v1_ok = False

            if not _check(
                "V1c: accumulated items == total_count",
                item_count == total_count,
                f"items={item_count}  total_count={total_count}",
            ):
                failures.append("v1_items_vs_total")
                v1_ok = False

        except RuntimeError as exc:
            _log(_FAIL, f"V1 aborted: {exc}")
            failures.append("v1_error")
            v1_ok = False

        block_results["V1"] = v1_ok
        print()

        # ── V2 — Forecast drill-down identity-equal (4 buckets) ───────────────
        print(_SEP)
        _log(_INFO, "V2 — Forecast drill-down identity-equal (4 buckets)")
        print(_SEP2)
        v2_ok = True
        try:
            kpi7 = fetch_parent_kpi(
                http, f"{base_url}{_KPI_PREFIX}/expected-forecast"
            )
            buckets_kpi7 = kpi7.get("buckets", {})

            print(
                f"  {'Bucket':<12} {'KPI7.amount':>18} {'DD sum(amount)':>18} "
                f"{'Δ':>10}  {'KPI7.due_amount':>18} {'DD sum(due_amount)':>18} {'Δ':>10}"
            )
            print(f"  {'─'*12} {'─'*18} {'─'*18} {'─'*10}  {'─'*18} {'─'*18} {'─'*10}")

            for url_key, internal in zip(_BUCKET_URL_KEYS, _BUCKET_INTERNALS):
                bkt = buckets_kpi7.get(internal, {})

                # Fail clearly if the whole bucket is absent (schema regression).
                if not bkt:
                    _log(_FAIL, f"V2: bucket '{internal}' absent from KPI 7 response")
                    failures.append(f"v2_bucket_{internal}_absent")
                    v2_ok = False
                    continue

                kpi7_amount = float(bkt.get("amount", 0))
                kpi7_count  = int(bkt.get("record_count", 0))

                # Defensive: do NOT silently default due_amount to 0.
                # A missing field would pass V2b when both sides are 0 (empty bucket),
                # or produce a confusing "expected 0.00 got N" message otherwise.
                has_kpi7_due = "due_amount" in bkt
                kpi7_due     = float(bkt["due_amount"]) if has_kpi7_due else None

                items, page_count, total_count = walk_all_pages(
                    http,
                    f"{base_url}{_DD_PREFIX}/forecast/{url_key}",
                )
                dd_amount = sum(float(it.get("amount",     0)) for it in items)
                dd_due    = sum(float(it.get("due_amount", 0)) for it in items)
                delta_amt = abs(dd_amount - kpi7_amount)

                due_col = f"{kpi7_due:>18,.2f}" if has_kpi7_due else f"  {'ABSENT':>16}"
                delta_due_col = f"{abs(dd_due - kpi7_due):>10.4f}" if has_kpi7_due else f"  {'N/A':>8}"
                print(
                    f"  {url_key:<12} {kpi7_amount:>18,.2f} {dd_amount:>18,.2f} "
                    f"{delta_amt:>10.4f}  {due_col} {dd_due:>18,.2f} {delta_due_col}"
                )

                if not _check(
                    f"V2a-{url_key}: |SUM(amount) − bucket.amount| <= 1.00 EGP",
                    delta_amt <= _MONEY_TOL,
                    f"dd={dd_amount:,.2f}  kpi7={kpi7_amount:,.2f}  delta={delta_amt:.4f}",
                ):
                    failures.append(f"v2_amount_{url_key}")
                    v2_ok = False

                # V2b: due_amount identity — explicit FAIL if field is absent.
                if not has_kpi7_due:
                    _log(
                        _FAIL,
                        f"V2b-{url_key}: bucket.due_amount ABSENT from KPI 7 response "
                        f"— field may have been removed from ForecastBucket schema. "
                        f"Drilldown dd_due={dd_due:,.2f}  No KPI 7 value to compare against.",
                    )
                    failures.append(f"v2_due_{url_key}_absent")
                    v2_ok = False
                else:
                    delta_due = abs(dd_due - kpi7_due)
                    if not _check(
                        f"V2b-{url_key}: |SUM(due_amount) − bucket.due_amount| <= 1.00 EGP",
                        delta_due <= _MONEY_TOL,
                        f"dd={dd_due:,.2f}  kpi7={kpi7_due:,.2f}  delta={delta_due:.4f}",
                    ):
                        failures.append(f"v2_due_{url_key}")
                        v2_ok = False

                if not _check(
                    f"V2c-{url_key}: total_count == KPI 7 record_count",
                    total_count == kpi7_count,
                    f"drilldown={total_count}  kpi7={kpi7_count}",
                ):
                    failures.append(f"v2_count_{url_key}")
                    v2_ok = False

        except RuntimeError as exc:
            _log(_FAIL, f"V2 aborted: {exc}")
            failures.append("v2_error")
            v2_ok = False

        block_results["V2"] = v2_ok
        print()

        # ── V3 — Portfolio drill-down identity-equal ──────────────────────────
        print(_SEP)
        _log(_INFO, "V3 — Portfolio drill-down identity-equal")
        print(_SEP2)
        _log(_INFO, "  Identity: SUM(customer.total_amount) == KPI 1 value")
        _log(_INFO, "  (KPI 1 = SUM(rs.installment.amount) for state=post; same domain as portfolio drilldown)")
        v3_ok = True
        try:
            kpi1 = fetch_parent_kpi(
                http, f"{base_url}{_KPI_PREFIX}/total-portfolio-value"
            )
            kpi1_value = float(kpi1["value"])
            kpi1_count = int(kpi1["record_count"])
            _log(_INFO, f"  KPI 1 value        : EGP {kpi1_value:>22,.2f}")
            _log(_INFO, f"  KPI 1 record_count : {kpi1_count:>26,}")

            customers, page_count, total_count = walk_all_pages(
                http,
                f"{base_url}{_DD_PREFIX}/portfolio",
                data_key="customers",
            )
            port_total_amount = sum(float(c["total_amount"]) for c in customers)
            port_total_due    = sum(float(c["total_due"])    for c in customers)
            cust_count        = len(customers)
            delta = abs(port_total_amount - kpi1_value)

            _log(_INFO, f"  Portfolio pages    : {page_count:>26,}")
            _log(_INFO, f"  Portfolio customers: {cust_count:>26,}")
            _log(_INFO, f"  SUM(total_amount)  : EGP {port_total_amount:>22,.2f}")
            _log(_INFO, f"  SUM(total_due)     : EGP {port_total_due:>22,.2f}  (remaining balance — not KPI 1)")
            _log(_INFO, f"  Delta vs KPI 1     : EGP {delta:>22,.4f}")

            if not _check(
                "V3a: |SUM(customer.total_amount) − KPI1.value| <= 1.00 EGP",
                delta <= _MONEY_TOL,
                f"portfolio={port_total_amount:,.2f}  kpi1={kpi1_value:,.2f}  delta={delta:.4f}",
            ):
                failures.append("v3_amount_delta")
                v3_ok = False

        except RuntimeError as exc:
            _log(_FAIL, f"V3 aborted: {exc}")
            failures.append("v3_error")
            v3_ok = False

        block_results["V3"] = v3_ok
        print()

        # ── V4 — Project drill-down identity-equal (3 projects) ───────────────
        print(_SEP)
        _log(_INFO, "V4 — Project drill-down identity-equal (3 projects)")
        print(_SEP2)
        _log(_INFO, "  Identity: SUM(item.due_amount) == KPI 5 late_uncollected per project")
        v4_ok = True
        try:
            kpi5 = fetch_parent_kpi(
                http, f"{base_url}{_KPI_PREFIX}/late-uncollected-by-project"
            )
            # Index KPI 5 by project_id
            kpi5_by_pid = {int(p["project_id"]): p for p in kpi5.get("projects", [])}

            print(
                f"  {'Project':<16} {'KPI5.late_uncollected':>22} "
                f"{'DD SUM(due_amount)':>22} {'Δ':>12}"
            )
            print(f"  {'─'*16} {'─'*22} {'─'*22} {'─'*12}")

            for pid in _PROJECT_IDS:
                kpi5_late = float(kpi5_by_pid.get(pid, {}).get("late_uncollected", 0))
                kpi5_cnt  = int(kpi5_by_pid.get(pid, {}).get("record_count", 0))

                items, page_count, total_count = walk_all_pages(
                    http,
                    f"{base_url}{_DD_PREFIX}/project/{pid}",
                )
                dd_due   = sum(float(it["due_amount"]) for it in items)
                delta    = abs(dd_due - kpi5_late)
                pid_name = {1: "New Capital", 2: "Cassette", 3: "La puerta"}.get(pid, str(pid))

                print(
                    f"  {pid_name:<16} {kpi5_late:>22,.2f} {dd_due:>22,.2f} {delta:>12.4f}"
                )

                if not _check(
                    f"V4a-project{pid}: |SUM(due_amount) − KPI5.late_uncollected| <= 1.00 EGP",
                    delta <= _MONEY_TOL,
                    f"dd={dd_due:,.2f}  kpi5={kpi5_late:,.2f}  delta={delta:.4f}",
                ):
                    failures.append(f"v4_project{pid}_delta")
                    v4_ok = False

                if not _check(
                    f"V4b-project{pid}: total_count == KPI 5 record_count",
                    total_count == kpi5_cnt,
                    f"drilldown={total_count}  kpi5={kpi5_cnt}",
                ):
                    failures.append(f"v4_project{pid}_count")
                    v4_ok = False

        except RuntimeError as exc:
            _log(_FAIL, f"V4 aborted: {exc}")
            failures.append("v4_error")
            v4_ok = False

        block_results["V4"] = v4_ok
        print()

        # ── V5 — Trend drill-down sanity (NOT identity-equal) ─────────────────
        print(_SEP)
        _log(_INFO, "V5 — Trend drill-down sanity + pagination")
        print(_SEP2)
        _log(_INFO, "  Note: trend uses rs.installment due-date axis; KPI 6 uses actual payment dates.")
        _log(_INFO, "  No identity-equal assertion — sanity check only (endpoint works, paginates).")
        v5_ok = True
        try:
            trailing = _trailing_months(6)
            test_month: str | None = None
            for month_str in trailing:
                items, page_count, total_count = walk_all_pages(
                    http,
                    f"{base_url}{_DD_PREFIX}/trend/{month_str}",
                )
                _log(_INFO, f"  Month {month_str}: {total_count} records, {page_count} page(s)")
                if total_count > 0 and test_month is None:
                    test_month = month_str
                    test_items      = items
                    test_page_count = page_count
                    test_total      = total_count

            if test_month is None:
                _log(_WARN, "  No in-range month with data found — sanity limited to empty-page shape.")
                # Still check that the most recent month returned a valid empty envelope.
                _check(
                    "V5a: most recent month returns valid envelope (total_count=0)",
                    True,
                    "no data in trailing 6 months — empty envelope is correct",
                )
            else:
                _log(_INFO, f"  Using month {test_month!r} for pagination sanity.")
                if not _check(
                    f"V5a: trend/{test_month} returns data (total_count > 0)",
                    test_total > 0,
                    f"total_count={test_total}",
                ):
                    failures.append("v5_no_data")
                    v5_ok = False

                if not _check(
                    f"V5b: accumulated items == total_count",
                    len(test_items) == test_total,
                    f"items={len(test_items)}  total_count={test_total}",
                ):
                    failures.append("v5_items_vs_total")
                    v5_ok = False

                _check(
                    f"V5c: pagination terminates within safety cap ({test_page_count} pages)",
                    test_page_count < 200,
                    f"pages={test_page_count}",
                )

                # Verify version + request_id already validated inside walk_all_pages.
                _check("V5d: walk_all_pages asserted version=='1.0' on each page", True)
                _check("V5e: walk_all_pages asserted meta.request_id on each page", True)

        except RuntimeError as exc:
            _log(_FAIL, f"V5 aborted: {exc}")
            failures.append("v5_error")
            v5_ok = False

        block_results["V5"] = v5_ok
        print()

        # ── V6 — Request ID propagation (live) ────────────────────────────────
        print(_SEP)
        _log(_INFO, "V6 — Request ID propagation (live)")
        print(_SEP2)
        v6_ok = True
        try:
            ts_tag  = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            custom_rid = f"verify-drilldown-{ts_tag}"
            _log(_INFO, f"  Sending X-Request-ID: {custom_rid!r}")

            r = http.get(
                f"{base_url}{_DD_PREFIX}/late",
                params={"page_size": 1},
                headers={"X-Request-ID": custom_rid},
            )
            if not _check("V6a: HTTP 200", r.status_code == 200, f"got {r.status_code}"):
                failures.append("v6_http")
                v6_ok = False
            else:
                resp_rid_header = r.headers.get("x-request-id", "")
                body = r.json()
                resp_rid_body   = body.get("meta", {}).get("request_id", "")

                _log(_INFO, f"  Response header X-Request-ID: {resp_rid_header!r}")
                _log(_INFO, f"  Response body meta.request_id: {resp_rid_body!r}")

                if not _check(
                    "V6b: response header X-Request-ID echoes custom value",
                    resp_rid_header == custom_rid,
                    f"expected={custom_rid!r}  got={resp_rid_header!r}",
                ):
                    failures.append("v6_header_echo")
                    v6_ok = False

                if not _check(
                    "V6c: meta.request_id in body echoes custom value",
                    resp_rid_body == custom_rid,
                    f"expected={custom_rid!r}  got={resp_rid_body!r}",
                ):
                    failures.append("v6_body_echo")
                    v6_ok = False

            # Also verify omitted header generates a UUID4 hex (32 chars, no hyphens).
            r2 = http.get(
                f"{base_url}{_DD_PREFIX}/late",
                params={"page_size": 1},
            )
            if r2.status_code == 200:
                auto_rid = r2.json().get("meta", {}).get("request_id", "")
                _log(_INFO, f"  Auto-generated request_id: {auto_rid!r}")
                if not _check(
                    "V6d: omitted X-Request-ID auto-generates 32-char hex",
                    len(auto_rid) == 32 and auto_rid.isalnum() and auto_rid == auto_rid.lower(),
                    f"got={auto_rid!r}",
                ):
                    failures.append("v6_auto_rid")
                    v6_ok = False

        except RuntimeError as exc:
            _log(_FAIL, f"V6 aborted: {exc}")
            failures.append("v6_error")
            v6_ok = False

        block_results["V6"] = v6_ok
        print()

        # ── V7 — Tri-state filter live check (late endpoint) ──────────────────
        print(_SEP)
        _log(_INFO, "V7 — Tri-state filter live check (late endpoint)")
        print(_SEP2)
        _log(_INFO, "  Assert: count(has_pending_cheque=True) + count(has_pending_cheque=False) == count(omitted)")
        _log(_INFO, "  This live-verifies the D4 tri-state fix against real data.")
        v7_ok = True
        try:
            _, _, count_all = walk_all_pages(
                http, f"{base_url}{_DD_PREFIX}/late",
                page_size=200,
            )
            _, _, count_true = walk_all_pages(
                http, f"{base_url}{_DD_PREFIX}/late",
                extra_params={"has_pending_cheque": "true"},
                page_size=200,
            )
            _, _, count_false = walk_all_pages(
                http, f"{base_url}{_DD_PREFIX}/late",
                extra_params={"has_pending_cheque": "false"},
                page_size=200,
            )

            _log(_INFO, f"  count(all)   : {count_all:>8,}")
            _log(_INFO, f"  count(true)  : {count_true:>8,}")
            _log(_INFO, f"  count(false) : {count_false:>8,}")
            _log(_INFO, f"  true+false   : {count_true + count_false:>8,}  (must equal count_all)")

            if not _check(
                "V7a: count_true + count_false == count_all (partition exhaustive)",
                count_true + count_false == count_all,
                f"true={count_true}  false={count_false}  sum={count_true+count_false}  all={count_all}",
            ):
                failures.append("v7_partition")
                v7_ok = False

            if not _check(
                "V7b: count_true >= 0 and count_false >= 0",
                count_true >= 0 and count_false >= 0,
                f"true={count_true}  false={count_false}",
            ):
                failures.append("v7_negative_count")
                v7_ok = False

        except RuntimeError as exc:
            _log(_FAIL, f"V7 aborted: {exc}")
            failures.append("v7_error")
            v7_ok = False

        block_results["V7"] = v7_ok
        print()

        # ── V8 — KPI 7 cheques_record_count is int (Decision 14.6) ────────────
        print(_SEP)
        _log(_INFO, "V8 — KPI 7 cheques_record_count is int >= 0 (Decision 14.6, Stage 5 D3.5)")
        print(_SEP2)
        v8_ok = True
        try:
            kpi7_v8 = fetch_parent_kpi(
                http, f"{base_url}{_KPI_PREFIX}/expected-forecast"
            )
            bkts = kpi7_v8.get("buckets", {})

            for internal in _BUCKET_INTERNALS:
                bkt = bkts.get(internal, {})
                crc = bkt.get("cheques_record_count")
                if not _check(
                    f"V8-{internal}: cheques_record_count is int >= 0",
                    isinstance(crc, int) and crc >= 0,
                    f"got {crc!r} (type={type(crc).__name__})",
                ):
                    failures.append(f"v8_{internal}_crc")
                    v8_ok = False
                else:
                    _log(_INFO, f"  {internal}: cheques_record_count = {crc}")

        except RuntimeError as exc:
            _log(_FAIL, f"V8 aborted: {exc}")
            failures.append("v8_error")
            v8_ok = False

        block_results["V8"] = v8_ok
        print()
    finally:
        http.close()

    # ── Summary table ─────────────────────────────────────────────────────────
    total_pass = sum(1 for v in block_results.values() if v)
    total_fail = sum(1 for v in block_results.values() if not v)

    print(_SEP)
    print("Stage 5 D6 — Drill-Down Live Verification")
    print(f"Run timestamp : {run_at}")
    print(_SEP)
    print(f"  {'Block':<6} {'Description':<50} {'Result'}")
    print(f"  {_SEP2}")

    _BLOCK_DESC = {
        "V1": "Late drill-down identity-equal",
        "V2": "Forecast drill-down identity-equal (4 buckets)",
        "V3": "Portfolio drill-down identity-equal",
        "V4": "Project drill-down identity-equal (3 projects)",
        "V5": "Trend drill-down sanity + pagination",
        "V6": "Request ID propagation (live)",
        "V7": "Tri-state filter partition (late endpoint)",
        "V8": "KPI 7 cheques_record_count is int (Decision 14.6)",
    }
    for block, ok in block_results.items():
        mark = _PASS_MARK if ok else _FAIL_MARK
        print(f"  {block:<6} {_BLOCK_DESC[block]:<50} {mark}")

    print(f"  {_SEP2}")
    summary_mark = _PASS_MARK if total_fail == 0 else _FAIL_MARK
    print(f"  {'TOTAL':<6} {len(block_results)} blocks {'':>34} {summary_mark}  {total_pass} PASS / {total_fail} FAIL")
    print(_SEP)

    if failures:
        print()
        _log(_FAIL, f"Failed assertions: {failures}")

    # ── TSV log ───────────────────────────────────────────────────────────────
    result_str = "PASS" if total_fail == 0 else "FAIL"
    _append_log(run_at, result_str, total_pass, total_fail, failures)

    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
