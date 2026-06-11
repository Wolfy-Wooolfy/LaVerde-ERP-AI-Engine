"""
Live verification for KPI 7 v2 — Dues & Collections — Current Periods (Decision 19.1).

Session 19 (N3 implementation): rewritten for the v2 full-period three-segment
buckets. The v1 checks (forward-looking [today, period_end] windows, June
collapse groups, type_breakdown, cheques_record_count, drill_down_domain) are
gone with the v1 payload.

Per bucket, TRIPLE AGREEMENT:
  (a) endpoint values (GET /api/v1/collections/kpi/expected-forecast,
      session-cookie auth via scripts/_lib/api_session.py — Decision 18.1), vs
  (b) direct read_group over the SAME full-period domain
      [state=post, date>=period_start, date<=period_end] via OdooClient, vs
  (c) direct search_count over the same domain for record_count.

Plus:
  - Internal sum invariant per bucket:
      |period_total − (collected_cleared + cheques_pending + remaining)| < 1.0 EGP
  - Window arithmetic cross-check — independent mirror of
    _compute_period_bounds() (kpi_service.py, ported from N3 discovery bc0d2cd)
  - Nesting: month ≤ quarter ≤ half ≤ year on period_total and record_count
    (strict < expected today; equality → WARN, violation → FAIL)
  - Drift vs the N3 discovery anchors (2026-06-11) FLAGGED if structural (>10%)
  - Cache-hit second call, response headers

READ-ONLY: GET requests + read_group/search_count direct RPCs only
(ALLOWED_METHODS enforced by OdooClient). No create/write/unlink.
No OpenAI. AI cost = $0.00.

Usage:
    python scripts/verify_kpi7_live.py [--url http://localhost:8000]

Requires the FastAPI server to be running and reachable.
Set VERIFY_USERNAME / VERIFY_PASSWORD env vars to override the default
admin credentials.

Exit 0  — all assertions passed (FLAGs allowed)
Exit 1  — at least one assertion failed or the server was unreachable
Exit 2  — Decision 6.4 ritual not confirmed (KPI7_VERIFY_CONFIRMED != "1")

Appends one tab-separated row to logs/kpi7_verification.log on each run.

NOTE — Decision 6.4 restart ritual REQUIRED before running:
    1. Kill any uvicorn --reload server (and all python processes)
    2. Confirm port 8000 is free
    3. Purge __pycache__ everywhere
    4. Start clean: python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
       (scripts/start_server.bat encodes steps 1-4)
    5. Run this script immediately (no warm-up call needed)

N3 discovery anchors (2026-06-11, intraday drift acceptable, commit bc0d2cd):
  month   : 390   records / 48,792,323.00  EGP period_total
  quarter : 1,200 records / 179,288,988.00 EGP
  half    : 2,418 records / 379,103,871.00 EGP
  year    : 4,704 records / 733,782,299.50 EGP
"""

import argparse
import asyncio
import calendar
import io
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

# Run from the PROJECT ROOT (python scripts/verify_kpi7_live.py): backend
# Settings resolves .env relative to CWD. Both the repo root (backend.*) and
# scripts/ (_lib.*) go on sys.path so imports work either way.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _lib.api_session import ApiLoginError, login as api_login
from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient

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

_MODEL = "rs.installment"

_BUCKET_NAMES = ("this_month", "this_quarter", "this_half", "this_year")
_BUCKET_FIELDS = (
    "period_start", "period_end", "record_count", "period_total_egp",
    "collected_cleared_egp", "cheques_pending_egp", "remaining_egp",
)

# N3 discovery anchors (2026-06-11, commit bc0d2cd). Drift >10% = structural FLAG.
_ANCHORS = {
    "this_month":   (390,   48_792_323.00),
    "this_quarter": (1_200, 179_288_988.00),
    "this_half":    (2_418, 379_103_871.00),
    "this_year":    (4_704, 733_782_299.50),
}
_DRIFT_PCT_STRUCTURAL = 10.0

_SEP  = "═" * 72
_SEP2 = "─" * 70
_PASS = "[PASS]"
_FAIL = "[FAIL]"
_INFO = "[INFO]"
_WARN = "[WARN]"
_FLAG = "[FLAG]"


def _log(prefix: str, msg: str) -> None:
    print(f"{prefix} {msg}", flush=True)


def _check(label: str, condition: bool, detail: str = "") -> bool:
    marker = _PASS if condition else _FAIL
    _log(marker, f"{label}{(' — ' + detail) if detail else ''}")
    return condition


def _expected_period_bounds(today: date) -> "dict[str, tuple[date, date]]":
    """Independent mirror of kpi_service._compute_period_bounds() (v2,
    Decision 19.1 — ported from N3 discovery bc0d2cd). Recomputed here from
    the API's own today_cairo so each bucket's window is checked against the
    calendar arithmetic, not against itself."""
    _, last_day = calendar.monthrange(today.year, today.month)
    month = (date(today.year, today.month, 1),
             date(today.year, today.month, last_day))

    quarter_idx   = (today.month - 1) // 3
    q_start_month = quarter_idx * 3 + 1
    q_end_month   = (quarter_idx + 1) * 3
    _, q_last_day = calendar.monthrange(today.year, q_end_month)
    quarter = (date(today.year, q_start_month, 1),
               date(today.year, q_end_month, q_last_day))

    if today.month <= 6:
        half = (date(today.year, 1, 1), date(today.year, 6, 30))
    else:
        half = (date(today.year, 7, 1), date(today.year, 12, 31))

    year = (date(today.year, 1, 1), date(today.year, 12, 31))

    return {
        "this_month":   month,
        "this_quarter": quarter,
        "this_half":    half,
        "this_year":    year,
    }


def _domain(start_str: str, end_str: str) -> list:
    """Full-period v2 domain — NO payment_state filter (Decision 19.1)."""
    return [
        ("state", "=", "post"),
        ("date", ">=", start_str),
        ("date", "<=", end_str),
    ]


async def _fetch_direct(windows: "dict[str, tuple[str, str]]") -> "dict[str, dict]":
    """Direct Odoo cross-check: per bucket, one read_group (4 sum fields) +
    one search_count over the same domain. 8 RPCs total. READ-ONLY."""
    out: dict = {}
    async with OdooClient() as client:
        for bname, (start_str, end_str) in windows.items():
            dom = _domain(start_str, end_str)
            rows = await client.execute_kw(
                _MODEL, "read_group",
                args=[dom, ["amount", "paid_amount", "x_studio_actual_paid_amount", "due_amount"], []],
                kwargs={"lazy": False},
            )
            row = rows[0] if rows else {}
            count = await client.execute_kw(_MODEL, "search_count", args=[dom])
            paid   = float(row.get("paid_amount") or 0.0)
            actual = float(row.get("x_studio_actual_paid_amount") or 0.0)
            out[bname] = {
                "period_total":      float(row.get("amount") or 0.0),
                "collected_cleared": actual,
                "cheques_pending":   paid - actual,
                "remaining":         float(row.get("due_amount") or 0.0),
                "record_count":      int(count),
                "rg_count":          int(row.get("__count") or 0),
            }
    return out


def _append_log(
    run_at: str,
    today_cairo: str,
    year_records: "int | str",
    year_total: "float | str",
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
            f"{run_at}\t{today_cairo}\t{year_records}\t{year_total}\t"
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

    _log(_INFO, f"Target : GET {base_url}{ENDPOINT}   (KPI 7 v2 — Decision 19.1)")
    _log(_INFO, f"Auth   : session-cookie (Decision 18.1) — "
                f"user {os.environ.get('VERIFY_USERNAME', 'admin')!r}")
    _log(_INFO, f"ALLOWED_METHODS : {sorted(ALLOWED_METHODS)}  (read-only direct RPC for triple agreement)")

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

        buckets              = body["buckets"]
        currency             = body["currency"]
        today_cairo_str      = body["today_cairo"]
        cache_status         = body["cache_status"]
        rpc_ms               = body["rpc_duration_ms"]
        data_quality_warning = body.get("data_quality_warning")

        # ── Step 4: Scalar top-level checks ──────────────────────────────────
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
                        "(negative_cheques or kpi7_identity_mismatch — Decision 18.2 pattern)")

        # ── Step 5: All 4 bucket keys + v2 field sets ────────────────────────
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

        print()
        _log(_INFO, "Per-bucket v2 field verification:")
        for bname in _BUCKET_NAMES:
            b = buckets[bname]
            for field in _BUCKET_FIELDS:
                if not _check(f"  {bname}.{field} present", field in b):
                    failures.append(f"{bname}_missing_{field}")
            for legacy in ("bucket", "amount", "due_amount", "cheques_in_pipeline",
                           "cheques_record_count", "drill_down_domain",
                           "cheques_drill_down_domain", "type_breakdown"):
                if not _check(f"  {bname}: v1 field '{legacy}' absent", legacy not in b,
                              "v1 payload leaked into v2"):
                    failures.append(f"{bname}_v1_leak_{legacy}")

            for dfield in ("period_start", "period_end"):
                val = b.get(dfield, "")
                if not _check(f"  {bname}.{dfield} is YYYY-MM-DD",
                              bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(val))),
                              f"got {val!r}"):
                    failures.append(f"{bname}_{dfield}_format")

            for nfield in ("period_total_egp", "collected_cleared_egp",
                           "cheques_pending_egp", "remaining_egp"):
                val = b.get(nfield)
                if not _check(f"  {bname}.{nfield} is numeric",
                              isinstance(val, (int, float)), f"got {val!r}"):
                    failures.append(f"{bname}_{nfield}_not_numeric")
            if not _check(f"  {bname}.record_count is int >= 0",
                          isinstance(b.get("record_count"), int) and b["record_count"] >= 0,
                          f"got {b.get('record_count')!r}"):
                failures.append(f"{bname}_record_count_invalid")

            # cheques_pending < 0 is a data anomaly → must be accompanied by the warning
            if float(b.get("cheques_pending_egp") or 0.0) < 0:
                _log(_WARN, f"  {bname}.cheques_pending_egp < 0 (unclamped anomaly)")
                if not _check(f"  {bname}: negative cheques flagged in data_quality_warning",
                              data_quality_warning == "negative_cheques",
                              f"warning is {data_quality_warning!r}"):
                    failures.append(f"{bname}_negative_cheques_unflagged")

        # ── Step 6: Internal sum invariant (< 1.0 EGP per bucket) ────────────
        print()
        _log(_INFO, "Internal sum invariant: cleared + pending + remaining == period_total (< 1.0 EGP):")
        for bname in _BUCKET_NAMES:
            b = buckets[bname]
            total   = float(b.get("period_total_egp") or 0.0)
            cleared = float(b.get("collected_cleared_egp") or 0.0)
            pending = float(b.get("cheques_pending_egp") or 0.0)
            remain  = float(b.get("remaining_egp") or 0.0)
            delta   = total - (cleared + pending + remain)
            ok = abs(delta) < 1.0
            if not _check(f"  {bname}: invariant holds", ok,
                          f"delta = {delta:,.4f} EGP"):
                failures.append(f"{bname}_invariant_broken")
                if not _check(f"  {bname}: invariant breach flagged in data_quality_warning",
                              data_quality_warning in {"kpi7_identity_mismatch", "negative_cheques"},
                              f"warning is {data_quality_warning!r}"):
                    failures.append(f"{bname}_invariant_unflagged")

        # ── Step 7: Window arithmetic cross-check ────────────────────────────
        print()
        _log(_INFO, "Window arithmetic cross-check — independent mirror of "
                    "_compute_period_bounds (v2, Decision 19.1):")
        today_d: "date | None"
        try:
            today_d = date.fromisoformat(today_cairo_str)
        except ValueError:
            today_d = None
            _check("today_cairo parseable as date", False, repr(today_cairo_str))
            failures.append("today_cairo_unparseable")

        if today_d is not None:
            expected = _expected_period_bounds(today_d)
            for bname in _BUCKET_NAMES:
                exp_start, exp_end = expected[bname]
                got_start = buckets[bname].get("period_start", "")
                got_end   = buckets[bname].get("period_end", "")
                if not _check(f"  {bname}.period_start == calendar arithmetic ({exp_start.isoformat()})",
                              got_start == exp_start.isoformat(), f"got {got_start!r}"):
                    failures.append(f"{bname}_period_start_mismatch")
                if not _check(f"  {bname}.period_end == calendar arithmetic ({exp_end.isoformat()})",
                              got_end == exp_end.isoformat(), f"got {got_end!r}"):
                    failures.append(f"{bname}_period_end_mismatch")
            windows_set = {(buckets[n].get("period_start"), buckets[n].get("period_end"))
                           for n in _BUCKET_NAMES}
            if not _check("all four v2 windows are DISTINCT (no June collapse)",
                          len(windows_set) == 4, f"got {sorted(windows_set)}"):
                failures.append("windows_not_distinct")

        # ── Step 8: Nesting month ≤ quarter ≤ half ≤ year ────────────────────
        print()
        _log(_INFO, "Nesting (full periods nest ⇒ totals/counts nest; strict < expected today):")
        for metric in ("period_total_egp", "record_count"):
            vals = [buckets[n].get(metric, 0) for n in _BUCKET_NAMES]
            detail = " → ".join(
                f"{n.split('_')[1][:2]}={v:,.0f}" for n, v in zip(_BUCKET_NAMES, vals)
            )
            ok_le = all(vals[i] <= vals[i + 1] for i in range(3))
            if not _check(f"  {metric}: month ≤ quarter ≤ half ≤ year", ok_le, detail):
                failures.append(f"nesting_violation_{metric}")
            else:
                strict = all(vals[i] < vals[i + 1] for i in range(3))
                if not strict:
                    _log(_WARN, f"  {metric}: not strictly increasing ({detail}) — "
                                "equality is unexpected mid-year; review.")

        # ── Step 9: TRIPLE AGREEMENT — endpoint vs read_group vs search_count ─
        print()
        _log(_INFO, "Triple agreement — endpoint vs direct read_group vs search_count (8 RPCs):")
        windows = {n: (buckets[n]["period_start"], buckets[n]["period_end"])
                   for n in _BUCKET_NAMES}
        direct = asyncio.run(_fetch_direct(windows))
        for bname in _BUCKET_NAMES:
            b, d = buckets[bname], direct[bname]
            for api_field, d_field in (
                ("period_total_egp",      "period_total"),
                ("collected_cleared_egp", "collected_cleared"),
                ("cheques_pending_egp",   "cheques_pending"),
                ("remaining_egp",         "remaining"),
            ):
                a_val = float(b.get(api_field) or 0.0)
                d_val = d[d_field]
                if not _check(
                    f"  {bname}.{api_field}: endpoint == direct read_group (±1.0)",
                    abs(a_val - d_val) < 1.0,
                    f"endpoint {a_val:,.2f} vs direct {d_val:,.2f} "
                    f"(delta {a_val - d_val:,.2f})",
                ):
                    failures.append(f"{bname}_{api_field}_direct_mismatch")
            if not _check(
                f"  {bname}.record_count: endpoint == search_count",
                int(b.get("record_count") or 0) == d["record_count"],
                f"endpoint {b.get('record_count')} vs search_count {d['record_count']}",
            ):
                failures.append(f"{bname}_record_count_direct_mismatch")
            if not _check(
                f"  {bname}: read_group __count == search_count",
                d["rg_count"] == d["record_count"],
                f"rg {d['rg_count']} vs sc {d['record_count']}",
            ):
                failures.append(f"{bname}_rg_sc_count_mismatch")

        # ── Step 10: Drift vs N3 discovery anchors (FLAG if structural) ──────
        print()
        _log(_INFO, f"Drift vs N3 discovery anchors (2026-06-11, bc0d2cd) — FLAG if > {_DRIFT_PCT_STRUCTURAL:.0f}%:")
        for bname in _BUCKET_NAMES:
            anchor_cnt, anchor_total = _ANCHORS[bname]
            got_cnt   = int(buckets[bname].get("record_count") or 0)
            got_total = float(buckets[bname].get("period_total_egp") or 0.0)
            cnt_pct   = abs(got_cnt - anchor_cnt) / anchor_cnt * 100 if anchor_cnt else 0.0
            tot_pct   = abs(got_total - anchor_total) / anchor_total * 100 if anchor_total else 0.0
            structural = cnt_pct > _DRIFT_PCT_STRUCTURAL or tot_pct > _DRIFT_PCT_STRUCTURAL
            lbl = _FLAG if structural else _PASS
            _log(lbl, f"  {bname}: count {got_cnt:,} vs {anchor_cnt:,} ({cnt_pct:.2f}%) | "
                      f"total {got_total:,.2f} vs {anchor_total:,.2f} ({tot_pct:.2f}%)")
            if structural:
                _log(_FLAG, f"  {bname}: drift exceeds {_DRIFT_PCT_STRUCTURAL:.0f}% — structural; "
                            "investigate before trusting the anchors (data entry may have moved).")

        # ── Step 11: Response headers ─────────────────────────────────────────
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

        # ── Step 12: Second request — cache hit (same client, NO re-login) ───
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
    print("KPI 7 v2 — Dues & Collections — Current Periods — Verification")
    print(f"Run timestamp : {run_at}")
    print(f"today_cairo   : {today_cairo_str}  (Cairo-local date, cache key boundary)")
    print(_SEP)
    print(f"  {'Bucket':<14} {'Start':<12} {'End':<12} {'Records':>8}  "
          f"{'Total (EGP)':>18}  {'Cleared (EGP)':>16}  {'Cheques (EGP)':>16}  {'Remaining (EGP)':>18}")
    print(f"  {_SEP2}")
    for bname in _BUCKET_NAMES:
        b = buckets.get(bname, {})
        print(f"  {bname:<14} {b.get('period_start',''):<12} {b.get('period_end',''):<12} "
              f"{int(b.get('record_count', 0)):>8,}  "
              f"{float(b.get('period_total_egp', 0)):>18,.2f}  "
              f"{float(b.get('collected_cleared_egp', 0)):>16,.2f}  "
              f"{float(b.get('cheques_pending_egp', 0)):>16,.2f}  "
              f"{float(b.get('remaining_egp', 0)):>18,.2f}")
    print(f"  {_SEP2}")
    year_b     = buckets.get("this_year", {})
    year_total = float(year_b.get("period_total_egp", 0))
    year_cnt   = int(year_b.get("record_count", 0))
    print()
    print(f"  data_quality_warning : {data_quality_warning!r}")
    print(f"  cache_status         : {cache_status}")
    print(f"  rpc_duration_ms      : {rpc_ms}  (4 RPCs expected on cache miss — one read_group per bucket)")
    print()
    print("  ─── MANUAL CROSS-CHECK (optional — triple agreement above is the gate) ─")
    print("  Odoo: Collections Mgmt → All Installments, filter State = Posted only")
    print("  (NO payment-status filter), date range per bucket window, Pivot")
    print("  measures: Amount / Paid Amount / Actual Paid Amount / Due Amount.")
    print(_SEP)

    # ── Log row ───────────────────────────────────────────────────────────────
    _append_log(
        run_at=run_at,
        today_cairo=today_cairo_str,
        year_records=year_cnt,
        year_total=f"{year_total:.2f}",
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
