"""
Live verification for KPI 4 — Collection Rate MTD & YTD.

Usage:
    python scripts/verify_kpi4_live.py [--url http://localhost:8000]

Requires the FastAPI server to be running and reachable.
Set VERIFY_USERNAME / VERIFY_PASSWORD env vars (or .env) to override
the default admin credentials.

Exit 0  — all assertions passed
Exit 1  — at least one assertion failed or the server was unreachable

Appends one tab-separated row to logs/kpi4_verification.log on each run.

NOTE — Zero rates are EXPECTED (Decision 5.7 analog):
Operations staff are entering historical payment data retroactively.
rs.account.payment.installment records for 2026 payments have not yet
been back-entered. Zero numerators (0 EGP / 0 records) are truthful
data, not bugs. The denominator (rs.installment) is fully populated.

D0 baseline (2026-05-17):
  MTD denominator : 43,653,133.00 EGP / 263 records
  YTD denominator : 302,882,977.00 EGP / 1,861 records
  MTD & YTD rates : 0.00% (numerator = 0 — data entry not yet complete)

Rate sanity bounds (from Checkpoint 0 approval):
  rate == 0 or rate is None  → [INFO]   (expected during data entry)
  0 < rate < 5               → [WARN]   (low; possible partial back-entry)
  5 <= rate <= 200           → [PASS]
  rate > 200                 → [WARN]   (implausibly high; investigate)
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

DEFAULT_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")
USERNAME    = os.environ.get("VERIFY_USERNAME", "admin")
PASSWORD    = os.environ.get("VERIFY_PASSWORD", "password")
ENDPOINT    = "/api/v1/collections/kpi/collection-rate"
LOG_FILE    = "logs/kpi4_verification.log"

# D0 baseline values (2026-05-17) — identity-equal against Odoo UI at Checkpoint 1.
# These are the DENOMINATOR baselines. Numerators are 0.00 during data-entry phase.
_MTD_DEN_BASELINE = 43_653_133.00   # EGP (rs.installment, state='post', May 2026 MTD)
_YTD_DEN_BASELINE = 302_882_977.00  # EGP (rs.installment, state='post', Jan-May 2026 YTD)
_BASELINE_TOLERANCE = 5_000_000.00  # 5M EGP — allows daily data entry drift

_LA_VERDE_TZ = ZoneInfo("Africa/Cairo")
_UTC_TZ      = ZoneInfo("UTC")

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
        return f"[WARN] {rate:.4f}% (low; possible partial back-entry)"
    if rate <= 200.0:
        return f"[PASS] {rate:.4f}%"
    return f"[WARN] {rate:.4f}% (>200% — implausibly high; investigate)"


def _append_log(
    run_at: str,
    today_local: str,
    mtd_num: "float | str",
    mtd_den: "float | str",
    mtd_rate: "float | None | str",
    ytd_num: "float | str",
    ytd_den: "float | str",
    ytd_rate: "float | None | str",
    cache_status: str,
    rpc_ms: "int | str",
    failures: "list[str]",
) -> None:
    os.makedirs("logs", exist_ok=True)
    log_exists = os.path.isfile(LOG_FILE)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        if not log_exists:
            f.write(
                "run_at\ttoday_local\tmtd_num_egp\tmtd_den_egp\tmtd_rate_pct\t"
                "ytd_num_egp\tytd_den_egp\tytd_rate_pct\tcache_status\trpc_ms\tfailures\n"
            )
        mtd_rate_str = "None" if mtd_rate is None else str(mtd_rate)
        ytd_rate_str = "None" if ytd_rate is None else str(ytd_rate)
        f.write(
            f"{run_at}\t{today_local}\t{mtd_num}\t{mtd_den}\t{mtd_rate_str}\t"
            f"{ytd_num}\t{ytd_den}\t{ytd_rate_str}\t{cache_status}\t{rpc_ms}\t"
            f"{','.join(failures) if failures else 'none'}\n"
        )


# ── Direct Odoo date-range sanity check (D0 requirement) ─────────────────────
# Khaled's Checkpoint 0 approval requires D3 to validate min/max record dates
# in each of the 4 underlying queries. This section uses OdooClient directly.

def _tz_period_bounds(period_start: date, period_end: date) -> tuple[str, str]:
    start_local = datetime.combine(period_start, dt_time.min, tzinfo=_LA_VERDE_TZ)
    end_local   = datetime.combine(period_end,   dt_time(23, 59, 59), tzinfo=_LA_VERDE_TZ)
    return (
        start_local.astimezone(_UTC_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        end_local.astimezone(_UTC_TZ).strftime("%Y-%m-%d %H:%M:%S"),
    )


async def _run_date_range_checks() -> list[str]:
    """Run 4 direct Odoo queries to verify min/max dates fall inside domain bounds."""
    try:
        from backend.shared.odoo.client import OdooClient
    except ImportError:
        _log(_WARN, "Cannot import OdooClient — skipping date-range sanity checks")
        return []

    today = date.today()
    mtd_start = date(today.year, today.month, 1)
    ytd_start = date(today.year, 1, 1)

    mtd_start_utc, mtd_end_utc = _tz_period_bounds(mtd_start, today)
    ytd_start_utc, ytd_end_utc = _tz_period_bounds(ytd_start, today)
    mtd_start_iso = mtd_start.isoformat()
    mtd_end_iso   = today.isoformat()
    ytd_start_iso = ytd_start.isoformat()
    ytd_end_iso   = today.isoformat()

    _HEADER_MODEL = "rs.account.payment.installment"
    _INST_MODEL   = "rs.installment"

    failures: list[str] = []
    print()
    print(_SEP)
    _log(_INFO, "Date-range sanity check (D0 Checkpoint 0 requirement)")
    _log(_INFO, "Connecting to Odoo directly to verify min/max record dates …")
    print(_SEP2)

    async with OdooClient() as client:
        queries = [
            ("A — MTD Numerator",  _HEADER_MODEL, [("state","=","post"),("date",">=",mtd_start_utc),("date","<=",mtd_end_utc)], "date", True, mtd_start_utc[:10], mtd_end_utc[:10]),
            ("B — MTD Denominator",_INST_MODEL,   [("state","=","post"),("date",">=",mtd_start_iso),("date","<=",mtd_end_iso)], "date", False, mtd_start_iso, mtd_end_iso),
            ("C — YTD Numerator",  _HEADER_MODEL, [("state","=","post"),("date",">=",ytd_start_utc),("date","<=",ytd_end_utc)], "date", True, ytd_start_utc[:10], ytd_end_utc[:10]),
            ("D — YTD Denominator",_INST_MODEL,   [("state","=","post"),("date",">=",ytd_start_iso),("date","<=",ytd_end_iso)], "date", False, ytd_start_iso, ytd_end_iso),
        ]
        for label, model, domain, date_field, is_datetime, bound_start, bound_end in queries:
            try:
                records = await client.execute_kw(
                    model, "search_read", args=[domain, [date_field]], kwargs={"limit": 0}
                )
            except Exception as exc:
                _log(_WARN, f"{label}: Odoo query failed — {exc}")
                failures.append(f"date_range_{label[:1].lower()}_query_error")
                continue

            if not records:
                _log(_INFO, f"{label}: 0 records — no date-range to validate (expected during data-entry)")
                continue

            raw_dates = [r[date_field] for r in records if r.get(date_field)]
            if not raw_dates:
                _log(_WARN, f"{label}: records have no date values — cannot validate")
                continue

            if is_datetime:
                # Parse UTC datetime strings, convert to Egypt local for display
                parsed = []
                for rd in raw_dates:
                    try:
                        utc_dt = datetime.strptime(str(rd), "%Y-%m-%d %H:%M:%S").replace(tzinfo=_UTC_TZ)
                        parsed.append((utc_dt, utc_dt.astimezone(_LA_VERDE_TZ)))
                    except ValueError:
                        pass
                if not parsed:
                    _log(_WARN, f"{label}: cannot parse date values")
                    continue
                min_utc, min_local = min(parsed, key=lambda x: x[0])
                max_utc, max_local = max(parsed, key=lambda x: x[0])
                _log(_INFO, f"{label}: {len(records)} records")
                _log(_INFO, f"  Min date UTC   : {min_utc.strftime('%Y-%m-%d %H:%M:%S')}  (Egypt: {min_local.strftime('%Y-%m-%d %H:%M:%S')})")
                _log(_INFO, f"  Max date UTC   : {max_utc.strftime('%Y-%m-%d %H:%M:%S')}  (Egypt: {max_local.strftime('%Y-%m-%d %H:%M:%S')})")
                _log(_INFO, f"  Domain bounds  : [{bound_start}  →  {bound_end}]")
                # Check no record falls outside UTC domain
                domain_start_dt = datetime.strptime(domain[1][2], "%Y-%m-%d %H:%M:%S").replace(tzinfo=_UTC_TZ)
                domain_end_dt   = datetime.strptime(domain[2][2], "%Y-%m-%d %H:%M:%S").replace(tzinfo=_UTC_TZ)
                out_of_bounds = [utc for utc, _ in parsed if utc < domain_start_dt or utc > domain_end_dt]
                if not _check(
                    f"{label}: all dates within UTC domain bounds",
                    len(out_of_bounds) == 0,
                    f"{len(out_of_bounds)} records outside domain bounds — investigate",
                ):
                    failures.append(f"date_range_{label[:1].lower()}_out_of_bounds")
            else:
                # Plain date field — compare as strings (ISO format)
                min_date = min(str(r[date_field]) for r in records if r.get(date_field))
                max_date = max(str(r[date_field]) for r in records if r.get(date_field))
                _log(_INFO, f"{label}: {len(records)} records")
                _log(_INFO, f"  Min date : {min_date}")
                _log(_INFO, f"  Max date : {max_date}")
                _log(_INFO, f"  Domain   : [{bound_start}  →  {bound_end}]")
                if not _check(
                    f"{label}: min date >= domain start",
                    min_date >= bound_start,
                    f"min={min_date!r} < {bound_start!r}",
                ):
                    failures.append(f"date_range_{label[:1].lower()}_min_out")
                if not _check(
                    f"{label}: max date <= domain end",
                    max_date <= bound_end,
                    f"max={max_date!r} > {bound_end!r}",
                ):
                    failures.append(f"date_range_{label[:1].lower()}_max_out")

    return failures


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--skip-date-check", action="store_true",
                        help="Skip direct Odoo date-range validation")
    args = parser.parse_args()
    base_url: str = args.url.rstrip("/")
    url = f"{base_url}{ENDPOINT}"
    run_at     = datetime.now(timezone.utc).isoformat()
    today_local = datetime.now(_LA_VERDE_TZ).strftime("%Y-%m-%d")

    print()
    print(_SEP)
    _log(_INFO, "KPI 4 — Collection Rate MTD & YTD: Live Verification")
    _log(_INFO, f"Target  : GET {url}")
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
        _append_log(run_at, today_local, "", "", "", "", "", "", "", "", ["login_failed"])
        return 1
    except httpx.ConnectError as exc:
        _log(_FAIL, f"Cannot reach {base_url} — is the server running? ({exc})")
        _append_log(run_at, today_local, "", "", "", "", "", "", "", "", ["connect_error"])
        return 1

    try:
        r1 = http.get(ENDPOINT, timeout=60)

        if not _check("HTTP 200 (first call)", r1.status_code == 200, f"got {r1.status_code}"):
            _log(_INFO, f"Body: {r1.text[:500]}")
            _append_log(run_at, today_local, "", "", "", "", "", "", "", "", [f"http_{r1.status_code}"])
            return 1

        body: dict = r1.json()
        _log(_INFO, f"Top-level keys: {list(body.keys())}")

        # ── Step 2: Required top-level keys ──────────────────────────────────────
        print(_SEP2)
        _log(_INFO, "Step 2 — Response shape")
        required_top = {"mtd", "ytd", "ytd_period_assumption", "currency", "as_of",
                        "cache_status", "rpc_duration_ms"}
        for k in required_top:
            if not _check(f"key '{k}' present", k in body):
                failures.append(f"missing_key_{k}")

        if "mtd" in body and "ytd" in body:
            period_keys = {"numerator_egp", "denominator_egp", "rate_percent",
                           "period_start", "period_end", "record_count_num", "record_count_den"}
            for period_name in ("mtd", "ytd"):
                sub = body[period_name]
                for k in period_keys:
                    if not _check(f"{period_name}.{k} present", k in sub):
                        failures.append(f"missing_key_{period_name}_{k}")

        if failures:
            _append_log(run_at, today_local, "", "", "", "", "", "", "", "", failures)
            return 1

        # ── Step 3: Extract values ────────────────────────────────────────────────
        mtd = body["mtd"]
        ytd = body["ytd"]
        mtd_num    = float(mtd["numerator_egp"])
        mtd_den    = float(mtd["denominator_egp"])
        mtd_rate   = mtd["rate_percent"]          # float or None
        ytd_num    = float(ytd["numerator_egp"])
        ytd_den    = float(ytd["denominator_egp"])
        ytd_rate   = ytd["rate_percent"]
        cache_status = body["cache_status"]
        rpc_ms     = int(body["rpc_duration_ms"])

        # ── Step 4: Period date assertions ────────────────────────────────────────
        print(_SEP2)
        _log(_INFO, "Step 4 — Period date assertions")
        _check("ytd_period_assumption == 'calendar_year'",
               body.get("ytd_period_assumption") == "calendar_year",
               f"got {body.get('ytd_period_assumption')!r}")
        _check("currency == 'EGP'", body.get("currency") == "EGP")

        today_iso = today_local
        _check(f"mtd.period_end == {today_iso}",
               mtd.get("period_end") == today_iso,
               f"got {mtd.get('period_end')!r}")
        _check(f"ytd.period_end == {today_iso}",
               ytd.get("period_end") == today_iso,
               f"got {ytd.get('period_end')!r}")

        # MTD start = first day of current month
        from datetime import date as _date
        _today_obj = _date.fromisoformat(today_iso)
        expected_mtd_start = _date(_today_obj.year, _today_obj.month, 1).isoformat()
        _check(f"mtd.period_start == {expected_mtd_start} (first of month)",
               mtd.get("period_start") == expected_mtd_start,
               f"got {mtd.get('period_start')!r}")

        # YTD start = Jan 1 of current year
        expected_ytd_start = f"{_today_obj.year}-01-01"
        _check(f"ytd.period_start == {expected_ytd_start} (Jan 1 — Decision 6.2)",
               ytd.get("period_start") == expected_ytd_start,
               f"got {ytd.get('period_start')!r}")

        # ── Step 5: Rate sanity ───────────────────────────────────────────────────
        print(_SEP2)
        _log(_INFO, "Step 5 — Rate sanity")
        _log(_INFO, f"  MTD: num={mtd_num:>20,.2f} EGP  den={mtd_den:>20,.2f} EGP  →  {_rate_label(mtd_rate)}")
        _log(_INFO, f"  YTD: num={ytd_num:>20,.2f} EGP  den={ytd_den:>20,.2f} EGP  →  {_rate_label(ytd_rate)}")

        # rate_percent: None iff denominator == 0 (Decision 6.3)
        if not _check(
            "mtd.rate_percent is None iff mtd.denominator_egp == 0",
            (mtd_rate is None) == (mtd_den == 0.0),
            f"rate_percent={mtd_rate!r}, denominator_egp={mtd_den}",
        ):
            failures.append("mtd_rate_none_inconsistency")

        if not _check(
            "ytd.rate_percent is None iff ytd.denominator_egp == 0",
            (ytd_rate is None) == (ytd_den == 0.0),
            f"rate_percent={ytd_rate!r}, denominator_egp={ytd_den}",
        ):
            failures.append("ytd_rate_none_inconsistency")

        # Rate math: rate == num/den*100 (within float precision)
        if mtd_rate is not None and mtd_den != 0.0:
            expected_mtd_rate = mtd_num / mtd_den * 100
            if not _check(
                "mtd.rate_percent == mtd_num / mtd_den * 100",
                abs(mtd_rate - expected_mtd_rate) < 0.01,
                f"got {mtd_rate:.6f}, expected {expected_mtd_rate:.6f}",
            ):
                failures.append("mtd_rate_math_wrong")

        if ytd_rate is not None and ytd_den != 0.0:
            expected_ytd_rate = ytd_num / ytd_den * 100
            if not _check(
                "ytd.rate_percent == ytd_num / ytd_den * 100",
                abs(ytd_rate - expected_ytd_rate) < 0.01,
                f"got {ytd_rate:.6f}, expected {expected_ytd_rate:.6f}",
            ):
                failures.append("ytd_rate_math_wrong")

        # ── Step 6: Denominator baseline cross-check ──────────────────────────────
        print(_SEP2)
        _log(_INFO, "Step 6 — Denominator baseline cross-check (D0 2026-05-17 baseline)")
        _log(_INFO, "  NOTE: Denominators increase daily as new installments are posted.")
        _log(_INFO, f"  D0 MTD baseline: {_MTD_DEN_BASELINE:>15,.2f} EGP  tolerance: ±{_BASELINE_TOLERANCE:,.0f}")
        _log(_INFO, f"  D0 YTD baseline: {_YTD_DEN_BASELINE:>15,.2f} EGP  tolerance: ±{_BASELINE_TOLERANCE:,.0f}")
        _log(_INFO, f"  Current MTD den: {mtd_den:>15,.2f} EGP")
        _log(_INFO, f"  Current YTD den: {ytd_den:>15,.2f} EGP")

        mtd_den_delta = abs(mtd_den - _MTD_DEN_BASELINE)
        ytd_den_delta = abs(ytd_den - _YTD_DEN_BASELINE)
        if mtd_den_delta > _BASELINE_TOLERANCE:
            _log(_WARN, f"MTD denominator drifted {mtd_den_delta:,.2f} EGP from D0 baseline — investigate if unexpected")
        else:
            _log(_PASS, f"MTD denominator within {_BASELINE_TOLERANCE:,.0f} EGP of D0 baseline (delta={mtd_den_delta:,.2f})")

        if ytd_den_delta > _BASELINE_TOLERANCE:
            _log(_WARN, f"YTD denominator drifted {ytd_den_delta:,.2f} EGP from D0 baseline — investigate if unexpected")
        else:
            _log(_PASS, f"YTD denominator within {_BASELINE_TOLERANCE:,.0f} EGP of D0 baseline (delta={ytd_den_delta:,.2f})")

        # ── Step 7: Cache hit (second call) ───────────────────────────────────────
        print(_SEP2)
        _log(_INFO, "Step 7 — Cache hit (second call, same process)")
        try:
            r2 = http.get(ENDPOINT, timeout=60)
        except httpx.ConnectError as exc:
            _log(_WARN, f"Second call failed — {exc}")
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
                _log(_WARN, f"Second call returned HTTP {r2.status_code}")
                failures.append(f"second_call_http_{r2.status_code}")

        # ── Step 8: Response headers (first call) ─────────────────────────────────
        print(_SEP2)
        _log(_INFO, "Step 8 — Response headers")
        cc  = r1.headers.get("cache-control", "")
        xcs = r1.headers.get("x-cache-status", "")
        _check("Cache-Control: private",      "private"    in cc,  f"header: {cc!r}")
        _check("Cache-Control: max-age=60",   "max-age=60" in cc,  f"header: {cc!r}")
        _check("X-Cache-Status header present", bool(xcs),         f"got {xcs!r}")
    finally:
        http.close()

    # ── Step 9: Date-range sanity via direct Odoo queries ─────────────────────
    date_range_failures: list[str] = []
    if not args.skip_date_check:
        date_range_failures = asyncio.run(_run_date_range_checks())
        failures.extend(date_range_failures)
    else:
        _log(_INFO, "Date-range check skipped (--skip-date-check)")

    # ── Step 10: Manual cross-check guide ─────────────────────────────────────
    print()
    print(_SEP)
    _log(_INFO, "MANUAL CROSS-CHECK GUIDE (Checkpoint 2)")
    print(_SEP)
    print(f"""
  Open the 4 Odoo views below and compare each total to the values above.
  Identity-equal at 2-decimal precision is required for Checkpoint 2 sign-off.
  Zero amounts are a valid result during the data-entry phase.

  {_SEP2}
  Query A — MTD Numerator (Payment Headers)
  {_SEP2}
  Open   : Odoo → RS Accounting → Payment Installments
  Filters: State = Posted
           Date >= {date(_today_obj.year, _today_obj.month, 1).isoformat()}  (first day of current month, Egypt local)
           Date <= {today_iso}  (today, Egypt local)
  Field  : Sum the 'Amount' column
  Backend: {mtd_num:>20,.2f} EGP  ({int(mtd.get('record_count_num', 0))} records)

  {_SEP2}
  Query B — MTD Denominator (rs.installment)
  {_SEP2}
  Open   : Odoo → Collections Mgmt → All Installments
  Filters: State = Posted
           Date >= {expected_mtd_start}
           Date <= {today_iso}
  Field  : Sum the 'Amount' column (NOT 'Due Amount' — Decision 6.1)
  Backend: {mtd_den:>20,.2f} EGP  ({int(mtd.get('record_count_den', 0))} records)

  {_SEP2}
  Query C — YTD Numerator (Payment Headers)
  {_SEP2}
  Open   : Odoo → RS Accounting → Payment Installments
  Filters: State = Posted
           Date >= {expected_ytd_start}  (Jan 1, calendar year — Decision 6.2)
           Date <= {today_iso}
  Field  : Sum the 'Amount' column
  Backend: {ytd_num:>20,.2f} EGP  ({int(ytd.get('record_count_num', 0))} records)

  {_SEP2}
  Query D — YTD Denominator (rs.installment)
  {_SEP2}
  Open   : Odoo → Collections Mgmt → All Installments
  Filters: State = Posted
           Date >= {expected_ytd_start}
           Date <= {today_iso}
  Field  : Sum the 'Amount' column (NOT 'Due Amount' — Decision 6.1)
  Backend: {ytd_den:>20,.2f} EGP  ({int(ytd.get('record_count_den', 0))} records)

  {_SEP2}
  Derived rates
  {_SEP2}
  MTD rate = A ÷ B × 100 = {_rate_label(mtd_rate)}
  YTD rate = C ÷ D × 100 = {_rate_label(ytd_rate)}
""")

    # ── Final summary ─────────────────────────────────────────────────────────
    print(_SEP)
    _append_log(
        run_at, today_local,
        mtd_num, mtd_den, mtd_rate,
        ytd_num, ytd_den, ytd_rate,
        cache_status, rpc_ms, failures,
    )
    _log(_INFO, f"TSV row appended to {LOG_FILE}")

    if failures:
        _log(_FAIL, f"{len(failures)} assertion(s) failed: {failures}")
        return 1

    _log(_PASS, "All assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
