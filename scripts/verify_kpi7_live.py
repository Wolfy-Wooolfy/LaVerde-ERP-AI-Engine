"""
Live verification for KPI 7 — Expected Collections Forecast.

Session 19 (D-8 migration #1, Decision 18.1): HTTP Basic auth replaced with
session-cookie login via scripts/_lib/api_session.py. Also adds Step 8b —
window arithmetic cross-check mirroring _compute_bucket_ends()
(backend/modules/collections/services/kpi_service.py:1182-1217), including
the June-2026 triple nesting collapse (this_month = this_quarter = this_half
= 2026-06-30 — correct calendar nesting, not a bug).

READ-ONLY: GET requests against the FastAPI app only. No direct Odoo RPC.
No create/write/unlink. No OpenAI. AI cost = $0.00.

AUTH EVIDENCE (verbatim sources):
  FastAPI session-cookie auth (post-A2, Decision 18.1):
    scripts/_lib/api_session.py login() — POST /login {username, password,
    next} → 303 + cookie; ONE login per process (limiter 10/minute).
    get_current_user() (backend/api/deps.py:16-21) reads
    request.session.get("username") → 401 otherwise. No HTTP Basic path
    exists anywhere in the app.

Usage:
    python scripts/verify_kpi7_live.py [--url http://localhost:8000]

Requires the FastAPI server to be running and reachable.
Set VERIFY_USERNAME / VERIFY_PASSWORD env vars to override the default
admin credentials.

Exit 0  — all assertions passed
Exit 1  — at least one assertion failed or the server was unreachable
Exit 2  — Decision 6.4 ritual not confirmed (KPI7_VERIFY_CONFIRMED != "1")

Appends one tab-separated row to logs/kpi7_verification.log on each run.

NOTE — Decision 6.4 restart ritual REQUIRED before running:
    1. Kill any uvicorn --reload server
    2. Purge __pycache__:  find . -type d -name __pycache__ | xargs rm -rf
    3. Start clean:        uvicorn backend.main:app --host 0.0.0.0 --port 8000
    4. Run this script immediately (no warm-up call needed)

EGP amounts are forward-looking and change daily — no fixed EGP baseline is
asserted. Structural checks (nesting, nulls, field types, period boundaries,
window arithmetic) are deterministic and always pass on correct data.

Phase 0 Discovery baseline (2026-05-18, for reference only):
  this_month   : 133 records / 22,719,871.00 EGP
  this_quarter : 355 records / 55,527,209.00 EGP
  this_half    : 355 records / 55,527,209.00 EGP  (Q2/H1 collapse in May 2026)
  this_year    : 1934 records / 337,946,411.00 EGP
"""

import argparse
import calendar
import io
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

from _lib.api_session import ApiLoginError, login as api_login

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Decision 6.4 ritual enforcement ──────────────────────────────────────────

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
│  4. Set environment: $env:KPI7_VERIFY_CONFIRMED = "1"           │
│  5. Re-run: python scripts/verify_kpi7_live.py                  │
└─────────────────────────────────────────────────────────────────┘
"""

# ── Config ────────────────────────────────────────────────────────────────────

DEFAULT_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")
ENDPOINT    = "/api/v1/collections/kpi/expected-forecast"
LOG_FILE    = "logs/kpi7_verification.log"

_BUCKET_NAMES = ("this_month", "this_quarter", "this_half", "this_year")
_BUCKET_FIELDS = (
    "bucket", "period_start", "period_end", "amount", "record_count",
    "due_amount", "cheques_in_pipeline", "cheques_record_count",
    "drill_down_domain", "cheques_drill_down_domain", "type_breakdown",
)

_SEP  = "═" * 72
_SEP2 = "─" * 70
_PASS = "[PASS]"
_FAIL = "[FAIL]"
_INFO = "[INFO]"
_WARN = "[WARN]"


def _log(prefix: str, msg: str) -> None:
    print(f"{prefix} {msg}", flush=True)


def _check(label: str, condition: bool, detail: str = "") -> bool:
    marker = _PASS if condition else _FAIL
    _log(marker, f"{label}{(' — ' + detail) if detail else ''}")
    return condition


def _expected_bucket_ends(today: date) -> "dict[str, date]":
    """Independent mirror of _compute_bucket_ends()
    (backend/modules/collections/services/kpi_service.py:1182-1217).

    Recomputed here from the API's own today_cairo so each bucket's
    period_end is checked against the window arithmetic, not against itself.
    Pure calendar math on plain date objects — no ZoneInfo (Decision 9.2).
    """
    _, last_day = calendar.monthrange(today.year, today.month)
    end_of_month = date(today.year, today.month, last_day)

    quarter_idx = (today.month - 1) // 3          # 0=Q1, 1=Q2, 2=Q3, 3=Q4
    end_q_month = (quarter_idx + 1) * 3           # 3, 6, 9, or 12
    _, end_q_day = calendar.monthrange(today.year, end_q_month)
    end_of_quarter = date(today.year, end_q_month, end_q_day)

    end_h_month = 6 if today.month <= 6 else 12
    _, end_h_day = calendar.monthrange(today.year, end_h_month)
    end_of_half = date(today.year, end_h_month, end_h_day)

    end_of_year = date(today.year, 12, 31)

    return {
        "this_month":   end_of_month,
        "this_quarter": end_of_quarter,
        "this_half":    end_of_half,
        "this_year":    end_of_year,
    }


def _append_log(
    run_at: str,
    today_cairo: str,
    year_records: "int | str",
    year_amount: "float | str",
    cache_status: str,
    rpc_ms: "int | str",
    data_quality_warning: "str | None",
    failures: "list[str]",
) -> None:
    os.makedirs("logs", exist_ok=True)
    log_exists = os.path.isfile(LOG_FILE)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        if not log_exists:
            f.write(
                "run_at\ttoday_cairo\tyear_records\tyear_amount\t"
                "cache_status\trpc_ms\tdata_quality_warning\tfailures\n"
            )
        f.write(
            f"{run_at}\t{today_cairo}\t{year_records}\t{year_amount}\t"
            f"{cache_status}\t{rpc_ms}\t{data_quality_warning or 'none'}\t"
            f"{','.join(failures) if failures else 'none'}\n"
        )


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    # ── Decision 6.4 ritual guard ─────────────────────────────────────────────
    if os.environ.get("KPI7_VERIFY_CONFIRMED") != "1":
        print(_RITUAL)
        print("REFUSED. Set KPI7_VERIFY_CONFIRMED=1 after completing")
        print("the ritual above, then re-run this script.")
        sys.exit(2)

    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_URL)
    args = parser.parse_args()
    base_url: str = args.url.rstrip("/")
    run_at = datetime.now(timezone.utc).isoformat()

    _log(_INFO, f"Target : GET {base_url}{ENDPOINT}")
    _log(_INFO, f"Auth   : session-cookie (Decision 18.1) — "
                f"user {os.environ.get('VERIFY_USERNAME', 'admin')!r}")

    failures: list[str] = []

    # ── Step 1: ONE login per process (limiter 10/minute), then GET ──────────
    try:
        client = api_login(base_url)
    except ApiLoginError as exc:
        _log(_FAIL, f"Session login failed: {exc}")
        _append_log(run_at, "", "", "", "", "", None, ["login_failed"])
        return 1
    except httpx.ConnectError as exc:
        _log(_FAIL, f"Cannot reach {base_url} — run scripts/start_server.bat "
                    f"(Decision 6.4 ritual) first. ({exc})")
        _append_log(run_at, "", "", "", "", "", None, ["connect_error"])
        return 1
    _log(_INFO, "Session cookie acquired — client reused for every request.")

    try:
        r = client.get(ENDPOINT, timeout=60)

        # ── Step 2: HTTP 200 ─────────────────────────────────────────────────
        if not _check("HTTP 200", r.status_code == 200, f"got {r.status_code}"):
            _log(_INFO, f"Body: {r.text[:500]}")
            _append_log(run_at, "", "", "", "", "", None, [f"http_{r.status_code}"])
            return 1

        body: dict = r.json()
        _log(_INFO, f"Top-level keys: {list(body.keys())}")

        # ── Step 3: Required top-level keys ──────────────────────────────────
        top_keys = ("buckets", "currency", "today_cairo", "cache_status",
                    "rpc_duration_ms", "data_quality_warning")
        for k in top_keys:
            if not _check(f"key '{k}' present", k in body):
                failures.append(f"missing_top_key_{k}")

        if failures:
            _append_log(run_at, "", "", "", "", "", None, failures)
            return 1

        # ── Step 4: Extract top-level values ─────────────────────────────────
        buckets             = body["buckets"]
        currency            = body["currency"]
        today_cairo_str     = body["today_cairo"]
        cache_status        = body["cache_status"]
        rpc_ms              = body["rpc_duration_ms"]
        data_quality_warning = body.get("data_quality_warning")

        # ── Step 5: Scalar top-level checks ──────────────────────────────────
        if not _check("currency == 'EGP'", currency == "EGP", f"got {currency!r}"):
            failures.append("wrong_currency")

        if not _check(
            "today_cairo is YYYY-MM-DD",
            bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", today_cairo_str)),
            f"got {today_cairo_str!r}",
        ):
            failures.append("bad_today_cairo_format")

        if not _check(
            "cache_status in {fresh, cached}",
            cache_status in {"fresh", "cached"},
            f"got {cache_status!r}",
        ):
            failures.append("bad_cache_status")

        if data_quality_warning is not None:
            _log(_WARN, f"data_quality_warning: {data_quality_warning!r} "
                        f"(negative cheques_raw in one or more buckets — data anomaly in Odoo Studio fields)")

        # ── Step 6: All 4 bucket keys present ────────────────────────────────
        if not _check("buckets is a dict", isinstance(buckets, dict)):
            failures.append("buckets_not_dict")
            _append_log(run_at, today_cairo_str, "", "", cache_status, rpc_ms, data_quality_warning, failures)
            return 1

        for bname in _BUCKET_NAMES:
            if not _check(f"bucket key '{bname}' present", bname in buckets):
                failures.append(f"missing_bucket_{bname}")

        if any(f.startswith("missing_bucket_") for f in failures):
            _append_log(run_at, today_cairo_str, "", "", cache_status, rpc_ms, data_quality_warning, failures)
            return 1

        # ── Step 7: Per-bucket field checks ──────────────────────────────────
        print()
        _log(_INFO, "Per-bucket field verification:")
        for bname in _BUCKET_NAMES:
            b = buckets[bname]
            for field in _BUCKET_FIELDS:
                if not _check(f"  {bname}.{field} present", field in b):
                    failures.append(f"{bname}_missing_{field}")

            # bucket name self-consistency
            if not _check(f"  {bname}.bucket == '{bname}'", b.get("bucket") == bname,
                          f"got {b.get('bucket')!r}"):
                failures.append(f"{bname}_bucket_name_wrong")

            # period_start == today_cairo
            if not _check(f"  {bname}.period_start == today_cairo",
                          b.get("period_start") == today_cairo_str,
                          f"got {b.get('period_start')!r}"):
                failures.append(f"{bname}_period_start_wrong")

            # period_end >= today_cairo (forward-looking: end is same day or later)
            period_end = b.get("period_end", "")
            if not _check(f"  {bname}.period_end is YYYY-MM-DD",
                          bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", period_end)),
                          f"got {period_end!r}"):
                failures.append(f"{bname}_period_end_format")
            elif not _check(f"  {bname}.period_end >= today_cairo",
                            period_end >= today_cairo_str,
                            f"{period_end} < {today_cairo_str}"):
                failures.append(f"{bname}_period_end_in_past")

            # numeric fields >= 0
            for num_field in ("amount", "due_amount", "cheques_in_pipeline"):
                val = b.get(num_field)
                if not _check(f"  {bname}.{num_field} >= 0.0",
                              isinstance(val, (int, float)) and val >= 0.0,
                              f"got {val!r}"):
                    failures.append(f"{bname}_{num_field}_negative")

            if not _check(f"  {bname}.record_count >= 0",
                          isinstance(b.get("record_count"), int) and b["record_count"] >= 0,
                          f"got {b.get('record_count')!r}"):
                failures.append(f"{bname}_record_count_negative")

            # cheques_record_count — int >= 0 from Stage 5 (Decision 14.6)
            _cr = b.get("cheques_record_count")
            if not _check(
                f"  {bname}.cheques_record_count is int >= 0 (Stage 5, Decision 14.6)",
                isinstance(_cr, int) and _cr >= 0,
                f"got {_cr!r}",
            ):
                failures.append(f"{bname}_cheques_record_count_not_int")

            if not _check(f"  {bname}.cheques_drill_down_domain is null (Alt B)",
                          b.get("cheques_drill_down_domain") is None,
                          f"got {b.get('cheques_drill_down_domain')!r}"):
                failures.append(f"{bname}_cheques_drill_down_domain_not_null")

            # type_breakdown: list, identity-equal to bucket amount (Stage 7)
            tb = b.get("type_breakdown")
            if not _check(f"  {bname}.type_breakdown is a list",
                          isinstance(tb, list),
                          f"got {type(tb).__name__}"):
                failures.append(f"{bname}_type_breakdown_not_list")
            else:
                tb_sum = sum(float(e.get("amount", 0)) for e in tb)
                if not _check(
                    f"  {bname}.type_breakdown sums to bucket amount (±0.01)",
                    abs(tb_sum - float(b.get("amount", 0))) < 0.01,
                    f"breakdown {tb_sum:,.2f} vs bucket {float(b.get('amount', 0)):,.2f}",
                ):
                    failures.append(f"{bname}_type_breakdown_sum_mismatch")

            # drill_down_domain: 4-clause list
            domain = b.get("drill_down_domain", [])
            if not _check(f"  {bname}.drill_down_domain has 4 clauses",
                          isinstance(domain, list) and len(domain) == 4,
                          f"got {len(domain) if isinstance(domain, list) else type(domain).__name__}"):
                failures.append(f"{bname}_domain_clause_count")
            else:
                _check(f"  {bname}.domain[0] == state=post", domain[0] == ["state", "=", "post"])
                _check(f"  {bname}.domain[1] == payment_state IN [unpaid,partial]",
                       domain[1] == ["payment_state", "in", ["unpaid", "partial"]])
                _check(f"  {bname}.domain[2] == date >= today_cairo",
                       domain[2] == ["date", ">=", today_cairo_str])
                _check(f"  {bname}.domain[3] == date <= period_end",
                       domain[3] == ["date", "<=", period_end])

        # ── Step 8: Nesting invariant ────────────────────────────────────────
        print()
        _log(_INFO, "Nesting invariant (this_month ⊆ this_quarter ⊆ this_half ⊆ this_year):")
        for metric in ("amount", "record_count", "due_amount", "cheques_in_pipeline"):
            vals = [buckets[n].get(metric, 0) for n in _BUCKET_NAMES]
            ok = all(vals[i] <= vals[i + 1] for i in range(3))
            detail = " → ".join(
                f"{n.split('_')[1][:2]}={v:,.0f}" for n, v in zip(_BUCKET_NAMES, vals)
            )
            if not _check(f"  {metric} nests correctly", ok, detail):
                failures.append(f"nesting_violation_{metric}")

        # period_end nesting
        ends = [buckets[n].get("period_end", "") for n in _BUCKET_NAMES]
        ok_ends = all(ends[i] <= ends[i + 1] for i in range(3))
        if not _check(
            "  period_end nests correctly",
            ok_ends,
            " → ".join(f"{n.split('_')[1][:2]}={e}" for n, e in zip(_BUCKET_NAMES, ends)),
        ):
            failures.append("nesting_violation_period_end")

        # ── Step 8b: Window arithmetic cross-check ───────────────────────────
        print()
        _log(_INFO, "Window arithmetic cross-check — independent mirror of "
                    "_compute_bucket_ends (kpi_service.py:1182-1217):")
        today_d: "date | None"
        try:
            today_d = date.fromisoformat(today_cairo_str)
        except ValueError:
            today_d = None
            _check("today_cairo parseable as date", False, repr(today_cairo_str))
            failures.append("today_cairo_unparseable")

        if today_d is not None:
            expected_ends = _expected_bucket_ends(today_d)
            for bname in _BUCKET_NAMES:
                exp = expected_ends[bname].isoformat()
                got = buckets[bname].get("period_end", "")
                if not _check(f"  {bname}.period_end == window arithmetic ({exp})",
                              got == exp, f"got {got!r}"):
                    failures.append(f"{bname}_window_mismatch")

            # Collapse groups: buckets whose calendar windows coincide must
            # return identical aggregates (same domain ⇒ same numbers).
            groups: "dict[str, list[str]]" = {}
            for bname in _BUCKET_NAMES:
                groups.setdefault(expected_ends[bname].isoformat(), []).append(bname)
            for end_iso, members in groups.items():
                if len(members) > 1:
                    _log(_INFO, f"  Collapse group: {', '.join(members)} all end "
                                f"{end_iso} (correct calendar nesting, not a bug)")
                    for metric in ("record_count", "amount", "due_amount",
                                   "cheques_in_pipeline", "cheques_record_count"):
                        vals = {buckets[m].get(metric) for m in members}
                        if not _check(
                            f"  collapsed buckets identical on {metric}",
                            len(vals) == 1,
                            str({m: buckets[m].get(metric) for m in members}),
                        ):
                            failures.append(f"collapse_mismatch_{metric}")

        # ── Step 9: Response headers ─────────────────────────────────────────
        print()
        _log(_INFO, "Response headers:")
        cc = r.headers.get("cache-control", "")
        if not _check("Cache-Control: private",    "private"    in cc, f"header: {cc!r}"):
            failures.append("header_no_private")
        if not _check("Cache-Control: max-age=60", "max-age=60" in cc, f"header: {cc!r}"):
            failures.append("header_wrong_max_age")
        xcs = r.headers.get("x-cache-status", "")
        if not _check("X-Cache-Status present", bool(xcs), f"got {xcs!r}"):
            failures.append("header_no_x_cache_status")

        # ── Step 10: Second request — cache hit (same client, NO re-login) ───
        print()
        _log(_INFO, "Second request — verifying cache hit ...")
        r2 = client.get(ENDPOINT, timeout=30)
        body2 = r2.json()
        if not _check(
            "second call cache_status == 'cached'",
            body2.get("cache_status") == "cached",
            f"got {body2.get('cache_status')!r}",
        ):
            failures.append("cache_not_hit_on_second_call")
        if not _check(
            "second call rpc_duration_ms == 0",
            int(body2.get("rpc_duration_ms", -1)) == 0,
            f"got {body2.get('rpc_duration_ms')}",
        ):
            failures.append("cache_rpc_ms_nonzero")
    finally:
        client.close()

    # ── Structured output ─────────────────────────────────────────────────────
    print()
    print(_SEP)
    print("KPI 7 — Expected Collections Forecast Verification")
    print(f"Run timestamp : {run_at}")
    print(f"today_cairo   : {today_cairo_str}  (Cairo-local date, cache key boundary)")
    print(_SEP)
    print(f"  {'Bucket':<16} {'Start':<12} {'End':<12} {'Records':>8}  "
          f"{'Amount (EGP)':>22}  {'Due Amt (EGP)':>22}  {'Cheques EGP':>14}")
    print(f"  {_SEP2}")
    all_ends = [buckets.get(n, {}).get("period_end", "") for n in _BUCKET_NAMES]
    for bname in _BUCKET_NAMES:
        b    = buckets.get(bname, {})
        strt = b.get("period_start", "")
        end  = b.get("period_end",   "")
        cnt  = int(b.get("record_count", 0))
        amt  = float(b.get("amount", 0))
        due  = float(b.get("due_amount", 0))
        chq  = float(b.get("cheques_in_pipeline", 0))
        collapse_note = " ← collapse" if all_ends.count(end) > 1 else ""
        print(f"  {bname:<16} {strt:<12} {end:<12} {cnt:>8,}  "
              f"{amt:>22,.2f}  {due:>22,.2f}  {chq:>14,.2f}{collapse_note}")
    print(f"  {_SEP2}")
    year_b   = buckets.get("this_year", {})
    year_amt = float(year_b.get("amount", 0))
    year_cnt = int(year_b.get("record_count", 0))
    print()
    print(f"  data_quality_warning : {data_quality_warning!r}")
    print(f"  cache_status         : {cache_status}")
    print(f"  rpc_duration_ms      : {rpc_ms}  (16 RPCs expected on cache miss: "
          f"8 bucket read_group + 4 cheques search_count + 4 type-breakdown read_group)")
    print()

    print("  ─── MANUAL CROSS-CHECK (optional — structural checks above are the gate) ─")
    print()
    print("  Open: Collections Mgmt → All Installments")
    print("  Filters: State = Posted  AND  Payment Status IN [Unpaid, Partially Paid]")
    print("  Switch to Pivot view, measure = Amount.  Add date range per bucket:")
    print()
    print(f"  {'Bucket':<16} {'Start':<12} {'End':<12}  "
          f"{'Records (API)':>14}  {'Amount (API)':>22}  {'Odoo UI':>10}  {'Delta':>10}")
    print(f"  {'─'*16} {'─'*12} {'─'*12}  {'─'*14}  {'─'*22}  {'─'*10}  {'─'*10}")
    for bname in _BUCKET_NAMES:
        b   = buckets.get(bname, {})
        strt = b.get("period_start", "")
        end  = b.get("period_end",   "")
        cnt  = int(b.get("record_count", 0))
        amt  = float(b.get("amount", 0))
        print(f"  {bname:<16} {strt:<12} {end:<12}  {cnt:>14,}  {amt:>22,.2f}  "
              f"{'[  ?  ]':>10}  {'[  ?  ]':>10}")
    print()
    print("  If all buckets match Odoo UI (±1 EGP) → KPI 7 live-verified.")
    print("  Note: buckets whose end dates coincide return identical results —")
    print("        e.g. Jun 2026: this_month = this_quarter = this_half all end")
    print("        2026-06-30 (triple collapse). Correct calendar nesting, not a bug.")
    print()
    print(_SEP)

    # ── Log row ───────────────────────────────────────────────────────────────
    _append_log(
        run_at=run_at,
        today_cairo=today_cairo_str,
        year_records=year_cnt,
        year_amount=f"{year_amt:.2f}",
        cache_status=cache_status,
        rpc_ms=rpc_ms,
        data_quality_warning=data_quality_warning,
        failures=failures,
    )

    if failures:
        _log(_FAIL, f"Verification FAILED — {len(failures)} assertion(s): {failures}")
        return 1

    _log(_PASS, "All assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
