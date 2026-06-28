"""
Live verification for KPI 5b — Collection Rate per Project MTD & YTD.

Usage:
    python scripts/verify_kpi5b_live.py [--url http://localhost:8000]

Requires the FastAPI server to be running (Decision 6.4: clean restart before running).
Set VERIFY_USERNAME / VERIFY_PASSWORD env vars (or .env) to override defaults.

Exit 0  — all assertions passed
Exit 1  — at least one assertion failed or the server was unreachable

IMPORTANT (Decision 6.4): Before running, restart the server cleanly:
  1. Stop all python processes
  2. Purge __pycache__: Get-ChildItem -Path . -Filter __pycache__ -Recurse -Directory | Remove-Item -Recurse -Force
  3. Start uvicorn WITHOUT --reload: python -m uvicorn backend.main:app
  4. Then run this script

Appends one tab-separated row per run to logs/kpi5b_verification.log.

Cross-KPI consistency (Decision 7.3): KPI 5b total_numerator_egp and
total_denominator_egp must equal KPI 4 standalone values within 0.01 EGP.
D0 confirmed: zero delta (no null-project installments in any period).

D0 baseline denominators (2026-05-17):
  New Capital (id=1) YTD : 162,112,391.00 EGP / 1,458 records
  Cassette    (id=2) YTD : 138,966,586.00 EGP /   391 records
  La puerta   (id=3) YTD :   1,804,000.00 EGP /    12 records
  TOTAL       YTD        : 302,882,977.00 EGP / 1,861 records
"""

import argparse
import asyncio
import io
import os
import sys
from datetime import date, datetime, time as dt_time, timezone
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv

from _lib.api_session import ApiLoginError, login as api_login

load_dotenv(dotenv_path=".env")

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Config ────────────────────────────────────────────────────────────────────

DEFAULT_URL  = os.environ.get("BACKEND_URL", "http://localhost:8000")
USERNAME     = os.environ.get("VERIFY_USERNAME", "admin")
PASSWORD     = os.environ.get("VERIFY_PASSWORD", "password")
ENDPOINT_5B  = "/api/v1/collections/kpi/collection-rate-by-project"
ENDPOINT_KPI4 = "/api/v1/collections/kpi/collection-rate"
LOG_FILE     = "logs/kpi5b_verification.log"

# D0 Checkpoint 1 baselines (2026-05-17) — YTD denominators
_BASELINE_YTD_DEN = {
    1: 162_112_391.00,
    2: 138_966_586.00,
    3:   1_804_000.00,
}
_BASELINE_TOLERANCE = 5_000_000.00  # allows daily data-entry drift

_PROJECT_NAMES = {1: "New Capital", 2: "Cassette", 3: "La puerta"}

_LA_VERDE_TZ = ZoneInfo("Africa/Cairo")
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


def _rate_label(rate: "float | None") -> str:
    if rate is None:
        return "[INFO] None (zero denominator — Decision 6.3)"
    if rate == 0.0:
        return "[INFO] 0.00% (zero numerator — data entry not yet complete)"
    if rate < 5.0:
        return f"[WARN] {rate:.4f}% (low)"
    if rate <= 200.0:
        return f"[PASS] {rate:.4f}%"
    return f"[WARN] {rate:.4f}% (>200% — investigate)"


def _append_log(
    run_at: str,
    today_local: str,
    result_rows: "list[dict]",
    cross_kpi_pass: bool,
    cache_status: str,
    rpc_ms: "int | str",
    failures: "list[str]",
) -> None:
    os.makedirs("logs", exist_ok=True)
    log_exists = os.path.isfile(LOG_FILE)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        if not log_exists:
            f.write(
                "run_at\ttoday_local\tproject_id\tproject_name\tperiod\t"
                "num_egp\tden_egp\trate_pct\t"
                "cross_kpi_pass\tcache_status\trpc_ms\tfailures\n"
            )
        for row in result_rows:
            rate_str = "None" if row["rate"] is None else f"{row['rate']:.6f}"
            f.write(
                f"{run_at}\t{today_local}\t{row['pid']}\t{row['name']}\t{row['period']}\t"
                f"{row['num']:.2f}\t{row['den']:.2f}\t{rate_str}\t"
                f"{cross_kpi_pass}\t{cache_status}\t{rpc_ms}\t"
                f"{','.join(failures) if failures else 'none'}\n"
            )


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_URL)
    args = parser.parse_args()
    base_url: str = args.url.rstrip("/")
    url_5b   = f"{base_url}{ENDPOINT_5B}"
    url_kpi4 = f"{base_url}{ENDPOINT_KPI4}"
    run_at       = datetime.now(timezone.utc).isoformat()
    today_local  = datetime.now(_LA_VERDE_TZ).strftime("%Y-%m-%d")

    print()
    print(_SEP)
    _log(_INFO, "KPI 5b — Collection Rate per Project: Live Verification")
    _log(_INFO, f"Target  : GET {url_5b}")
    _log(_INFO, f"Auth    : {USERNAME}")
    _log(_INFO, f"Today   : {today_local} (Egypt local)")
    print(_SEP)

    failures: list[str] = []

    # ── Step 1: ONE login per process (limiter 10/minute), then fresh call ────
    _log(_INFO, "Step 1 — Fresh call …")
    try:
        http = api_login(base_url)
    except ApiLoginError as exc:
        _log(_FAIL, f"Session login failed: {exc}")
        _append_log(run_at, today_local, [], False, "", "", ["login_failed"])
        return 1
    except httpx.ConnectError as exc:
        _log(_FAIL, f"Cannot reach {base_url} — is the server running? ({exc})")
        _append_log(run_at, today_local, [], False, "", "", ["connect_error"])
        return 1

    try:
        r1 = http.get(ENDPOINT_5B, timeout=60)

        if not _check("HTTP 200 (first call)", r1.status_code == 200, f"got {r1.status_code}"):
            _log(_INFO, f"Body: {r1.text[:500]}")
            _append_log(run_at, today_local, [], False, "", "", [f"http_{r1.status_code}"])
            return 1

        body: dict = r1.json()
        _log(_INFO, f"Top-level keys: {sorted(body.keys())}")

        # ── Step 2: Top-level shape ───────────────────────────────────────────────
        print(_SEP2)
        _log(_INFO, "Step 2 — Response shape")

        required_top = {"mtd", "ytd", "ytd_period_assumption", "currency",
                        "as_of", "cache_status", "rpc_duration_ms"}
        for k in required_top:
            if not _check(f"key '{k}' present", k in body):
                failures.append(f"missing_top_{k}")

        if failures:
            _append_log(run_at, today_local, [], False, "", "", failures)
            return 1

        period_top_keys  = {"projects", "total_numerator_egp", "total_denominator_egp",
                            "total_rate_percent", "period_start", "period_end"}
        per_project_keys = {"project_id", "project_name", "numerator_egp",
                            "denominator_egp", "rate_percent",
                            "record_count_num", "record_count_den"}

        for period_name in ("mtd", "ytd"):
            sub = body.get(period_name, {})
            for k in period_top_keys:
                if not _check(f"{period_name}.{k} present", k in sub):
                    failures.append(f"missing_{period_name}_{k}")
            projs = sub.get("projects", [])
            if not _check(f"{period_name}.projects has exactly 3 entries", len(projs) == 3,
                          f"got {len(projs)}"):
                failures.append(f"{period_name}_projects_count")
            for proj in projs:
                for k in per_project_keys:
                    if not _check(f"{period_name}.project.{k} present", k in proj):
                        failures.append(f"missing_{period_name}_proj_{k}")

        if failures:
            _append_log(run_at, today_local, [], False, "", "", failures)
            return 1

        _check("currency == 'EGP'", body.get("currency") == "EGP")
        _check("ytd_period_assumption == 'calendar_year'",
               body.get("ytd_period_assumption") == "calendar_year",
               f"got {body.get('ytd_period_assumption')!r}")

        # ── Step 3: Project order and names ──────────────────────────────────────
        print(_SEP2)
        _log(_INFO, "Step 3 — Project order, names, period dates")

        for period_name in ("mtd", "ytd"):
            projs = body[period_name]["projects"]
            ids   = [p["project_id"] for p in projs]
            names = [p["project_name"] for p in projs]
            if not _check(f"{period_name}: project_ids == [1, 2, 3]", ids == [1, 2, 3],
                          f"got {ids}"):
                failures.append(f"{period_name}_project_order")
            expected_names = ["New Capital", "Cassette", "La puerta"]
            if not _check(f"{period_name}: project_names correct", names == expected_names,
                          f"got {names}"):
                failures.append(f"{period_name}_project_names")
            for p in projs:
                if not _check(f"{period_name}: {p['project_name']} name has no 'Project#' prefix",
                              not str(p["project_name"]).startswith("Project#")):
                    failures.append(f"{period_name}_raw_name")

        # Period date assertions
        _today_obj = date.fromisoformat(today_local)
        expected_mtd_start = date(_today_obj.year, _today_obj.month, 1).isoformat()
        expected_ytd_start = f"{_today_obj.year}-01-01"

        _check(f"mtd.period_start == {expected_mtd_start}",
               body["mtd"].get("period_start") == expected_mtd_start,
               f"got {body['mtd'].get('period_start')!r}")
        _check(f"mtd.period_end == {today_local}",
               body["mtd"].get("period_end") == today_local,
               f"got {body['mtd'].get('period_end')!r}")
        _check(f"ytd.period_start == {expected_ytd_start}",
               body["ytd"].get("period_start") == expected_ytd_start,
               f"got {body['ytd'].get('period_start')!r}")
        _check(f"ytd.period_end == {today_local}",
               body["ytd"].get("period_end") == today_local,
               f"got {body['ytd'].get('period_end')!r}")

        # ── Step 4: Rate math and totals ──────────────────────────────────────────
        print(_SEP2)
        _log(_INFO, "Step 4 — Rate math and totals")

        result_rows: list[dict] = []
        for period_name in ("mtd", "ytd"):
            sub   = body[period_name]
            projs = sub["projects"]

            sum_num = sum(float(p["numerator_egp"])   for p in projs)
            sum_den = sum(float(p["denominator_egp"]) for p in projs)

            if not _check(
                f"{period_name}: total_numerator_egp == sum(project numerators)",
                abs(float(sub["total_numerator_egp"]) - sum_num) < 0.01,
                f"got {sub['total_numerator_egp']:.2f}, sum={sum_num:.2f}",
            ):
                failures.append(f"{period_name}_total_num_wrong")

            if not _check(
                f"{period_name}: total_denominator_egp == sum(project denominators)",
                abs(float(sub["total_denominator_egp"]) - sum_den) < 0.01,
                f"got {sub['total_denominator_egp']:.2f}, sum={sum_den:.2f}",
            ):
                failures.append(f"{period_name}_total_den_wrong")

            for proj in projs:
                pid   = proj["project_id"]
                n_amt = float(proj["numerator_egp"])
                d_amt = float(proj["denominator_egp"])
                rate  = proj["rate_percent"]
                _log(_INFO, f"  {period_name.upper()} {_PROJECT_NAMES.get(pid, pid):12}: "
                            f"num={n_amt:>20,.2f}  den={d_amt:>20,.2f}  → {_rate_label(rate)}")
                # rate_percent: None iff denominator == 0
                if not _check(
                    f"{period_name}.{_PROJECT_NAMES.get(pid, pid)}: rate_percent is None iff den==0",
                    (rate is None) == (d_amt == 0.0),
                    f"rate={rate!r}, den={d_amt}",
                ):
                    failures.append(f"{period_name}_proj{pid}_rate_none_inconsistency")
                # rate math
                if rate is not None and d_amt != 0.0:
                    expected = n_amt / d_amt * 100
                    if not _check(
                        f"{period_name}.{_PROJECT_NAMES.get(pid, pid)}: rate = num/den*100",
                        abs(rate - expected) < 0.01,
                        f"got {rate:.6f}, expected {expected:.6f}",
                    ):
                        failures.append(f"{period_name}_proj{pid}_rate_math")
                result_rows.append({
                    "pid": pid, "name": _PROJECT_NAMES.get(pid, str(pid)),
                    "period": period_name.upper(),
                    "num": n_amt, "den": d_amt, "rate": rate,
                })

            _log(_INFO, f"  {period_name.upper()} TOTAL: "
                        f"num={sub['total_numerator_egp']:>20,.2f}  "
                        f"den={sub['total_denominator_egp']:>20,.2f}  "
                        f"→ {_rate_label(sub.get('total_rate_percent'))}")

        # ── Step 5: YTD denominator baseline cross-check ─────────────────────────
        print(_SEP2)
        _log(_INFO, "Step 5 — YTD denominator baseline (D0 2026-05-17 checkpoint)")
        _log(_INFO, "  Denominators grow daily as new installments are posted.")

        ytd_projs = body["ytd"]["projects"]
        for proj in ytd_projs:
            pid    = proj["project_id"]
            d_amt  = float(proj["denominator_egp"])
            base   = _BASELINE_YTD_DEN.get(pid, 0.0)
            delta  = abs(d_amt - base)
            name   = _PROJECT_NAMES.get(pid, str(pid))
            _log(_INFO, f"  {name:12}: backend={d_amt:>20,.2f}  D0={base:>20,.2f}  delta={delta:>12,.2f}")
            if delta > _BASELINE_TOLERANCE:
                _log(_WARN, f"  {name}: delta {delta:,.2f} exceeds tolerance {_BASELINE_TOLERANCE:,.0f} — investigate")
            else:
                _log(_PASS, f"  {name}: within tolerance")

        # ── Step 6: Cross-KPI consistency (Decision 7.3) ─────────────────────────
        print(_SEP2)
        _log(_INFO, "Step 6 — Cross-KPI consistency: KPI 5b totals == KPI 4 standalone")
        _log(_INFO, f"  Calling GET {url_kpi4} …")

        cross_kpi_pass = False
        try:
            r_kpi4 = http.get(ENDPOINT_KPI4, timeout=60)
        except httpx.ConnectError as exc:
            _log(_WARN, f"  Cannot reach KPI 4 endpoint — {exc}")
            failures.append("cross_kpi_connect_error")
        else:
            if r_kpi4.status_code != 200:
                _log(_WARN, f"  KPI 4 returned HTTP {r_kpi4.status_code}")
                failures.append(f"cross_kpi_http_{r_kpi4.status_code}")
            else:
                kpi4 = r_kpi4.json()
                for period_name in ("mtd", "ytd"):
                    kpi4_num = float(kpi4[period_name]["numerator_egp"])
                    kpi4_den = float(kpi4[period_name]["denominator_egp"])
                    kpi5b_num = float(body[period_name]["total_numerator_egp"])
                    kpi5b_den = float(body[period_name]["total_denominator_egp"])
                    delta_num = abs(kpi5b_num - kpi4_num)
                    delta_den = abs(kpi5b_den - kpi4_den)
                    _log(_INFO, f"  {period_name.upper()} KPI4 num={kpi4_num:>20,.2f}  "
                                f"KPI5b total num={kpi5b_num:>20,.2f}  Δ={delta_num:.2f}")
                    _log(_INFO, f"  {period_name.upper()} KPI4 den={kpi4_den:>20,.2f}  "
                                f"KPI5b total den={kpi5b_den:>20,.2f}  Δ={delta_den:.2f}")
                    num_ok = _check(
                        f"{period_name.upper()} numerator: KPI 5b total == KPI 4 (delta < 0.01 EGP)",
                        delta_num < 0.01,
                        f"delta={delta_num:.6f}",
                    )
                    den_ok = _check(
                        f"{period_name.upper()} denominator: KPI 5b total == KPI 4 (delta < 0.01 EGP)",
                        delta_den < 0.01,
                        f"delta={delta_den:.6f}",
                    )
                    if not num_ok:
                        failures.append(f"cross_kpi_{period_name}_num_delta")
                    if not den_ok:
                        failures.append(f"cross_kpi_{period_name}_den_delta")

                cross_kpi_num_pass = all(
                    abs(float(body[p]["total_numerator_egp"]) - float(kpi4[p]["numerator_egp"])) < 0.01
                    for p in ("mtd", "ytd")
                )
                cross_kpi_den_pass = all(
                    abs(float(body[p]["total_denominator_egp"]) - float(kpi4[p]["denominator_egp"])) < 0.01
                    for p in ("mtd", "ytd")
                )
                cross_kpi_pass = cross_kpi_num_pass and cross_kpi_den_pass

        # ── Step 7: Cache hit ─────────────────────────────────────────────────────
        print(_SEP2)
        _log(_INFO, "Step 7 — Cache hit (second call)")
        try:
            r2 = http.get(ENDPOINT_5B, timeout=60)
        except httpx.ConnectError as exc:
            _log(_WARN, f"  Second call failed — {exc}")
            failures.append("second_call_connect_error")
        else:
            if r2.status_code == 200:
                body2 = r2.json()
                if not _check("cache_status == 'cached' on second call",
                              body2.get("cache_status") == "cached",
                              f"got {body2.get('cache_status')!r}"):
                    failures.append("cache_hit_not_seen")
                if not _check("rpc_duration_ms == 0 on cached call",
                              int(body2.get("rpc_duration_ms", -1)) == 0,
                              f"got {body2.get('rpc_duration_ms')}"):
                    failures.append("cached_rpc_ms_nonzero")
                xcs2 = r2.headers.get("x-cache-status", "")
                _check("X-Cache-Status: cached on second call", xcs2 == "cached", f"got {xcs2!r}")
            else:
                failures.append(f"second_call_http_{r2.status_code}")

        # ── Step 8: Response headers ──────────────────────────────────────────────
        print(_SEP2)
        _log(_INFO, "Step 8 — Response headers (first call)")
        cc  = r1.headers.get("cache-control", "")
        xcs = r1.headers.get("x-cache-status", "")
        _check("Cache-Control: private",    "private"    in cc, f"header: {cc!r}")
        _check("Cache-Control: max-age=60", "max-age=60" in cc, f"header: {cc!r}")
        _check("X-Cache-Status present",    bool(xcs),          f"got {xcs!r}")
    finally:
        http.close()

    # ── Final summary ─────────────────────────────────────────────────────────
    print()
    print(_SEP)
    cache_status = body.get("cache_status", "")
    rpc_ms       = int(body.get("rpc_duration_ms", 0))
    _append_log(run_at, today_local, result_rows, cross_kpi_pass, cache_status, rpc_ms, failures)
    _log(_INFO, f"TSV rows appended to {LOG_FILE} (6 rows: 3 projects × 2 periods per run)")

    if failures:
        _log(_FAIL, f"{len(failures)} assertion(s) failed: {failures}")
        return 1

    _log(_PASS, "All assertions passed.")
    if cross_kpi_pass:
        _log(_PASS, "Cross-KPI consistency PASSED — KPI 5b totals match KPI 4 standalone exactly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
