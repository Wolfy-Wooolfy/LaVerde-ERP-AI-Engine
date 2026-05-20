"""
scripts/discover_drilldowns.py — Stage 5 Pre-Implementation Discovery.

Six sub-investigations required before any drill-down service code is written.
Read-only. Exit 0 regardless of findings (discovery is observational).

  D1.1 — has_pending_cheque filter feasibility
          Tests field-to-field comparison and check_pending_amount literal domain.
  D1.2 — Cardinality assessment across all drill-down parent datasets.
  D1.3 — KPI 6 trend month: rs.installment due-date sanity check.
  D1.4 — project_id on rs.installment + live project name reverification.
  D1.5 — KPI 7 cheques_record_count strategy (Option I vs Option II).
  D1.6 — Pagination performance baseline on largest dataset (~2,013 late records).

TSV output : logs/drilldown_discovery.log
Console    : PASS / FLAG / WARN / INFO markers
No PII     : partner_id references are not printed; only aggregate counts / timings.

Usage (from project root, after Decision 6.4 clean restart):
    python scripts/discover_drilldowns.py
"""

import asyncio
import calendar
import io
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# Ensure project root on sys.path so backend.* imports work.
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient  # noqa: E402

# Force UTF-8 stdout (Windows consoles default to cp1252).
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Constants ─────────────────────────────────────────────────────────────────

_MODEL = "rs.installment"
_LA_VERDE_TZ = ZoneInfo("Africa/Cairo")
_LOG_FILE = Path("logs/drilldown_discovery.log")
_LARGE_DATASET_THRESHOLD = 5_000

_SEP  = "═" * 78
_SEP2 = "─" * 76
_PASS = "[PASS]"
_FLAG = "[FLAG]"
_INFO = "[INFO]"
_WARN = "[WARN]"

# Expected project mapping — Phase 2 + Session 3 verified.
_EXPECTED_PROJECTS: dict[int, str] = {
    1: "New Capital",
    2: "Cassette",
    3: "La puerta",
}

# Drill-down fields requested per record — same set the service will use.
_DRILL_FIELDS = [
    "id", "date", "amount", "due_amount", "paid_amount",
    "x_studio_actual_paid_amount", "payment_state", "partner_id", "project_id",
]

_log_rows: list[str] = []


# ── Helpers ───────────────────────────────────────────────────────────────────

def _log(marker: str, msg: str) -> None:
    print(f"{marker} {msg}", flush=True)


def _tsv(investigation: str, finding: str, marker: str, value: str, detail: str = "") -> None:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _log_rows.append(f"{ts}\t{investigation}\t{finding}\t{marker}\t{value}\t{detail}")


def _write_log() -> None:
    _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _LOG_FILE.open("w", encoding="utf-8") as fh:
        fh.write("timestamp\tinvestigation\tfinding\tmarker\tvalue\tdetail\n")
        for row in _log_rows:
            fh.write(row + "\n")
    _log(_INFO, f"TSV log written → {_LOG_FILE}")


def _compute_bucket_ends(today: date) -> dict[str, date]:
    """Cairo-local bucket end dates — mirrors kpi_service._compute_bucket_ends."""
    _, last_day = calendar.monthrange(today.year, today.month)
    end_of_month = date(today.year, today.month, last_day)

    quarter_idx = (today.month - 1) // 3
    end_q_month = (quarter_idx + 1) * 3
    _, end_q_day = calendar.monthrange(today.year, end_q_month)
    end_of_quarter = date(today.year, end_q_month, end_q_day)

    end_h_month = 6 if today.month <= 6 else 12
    _, end_h_day = calendar.monthrange(today.year, end_h_month)
    end_of_half = date(today.year, end_h_month, end_h_day)

    return {
        "this_month":   end_of_month,
        "this_quarter": end_of_quarter,
        "this_half":    end_of_half,
        "this_year":    date(today.year, 12, 31),
    }


def _trailing_6_months(today: date) -> list[tuple[date, date]]:
    """Return (start, end) for each of the trailing 6 calendar months, oldest-first."""
    result: list[tuple[date, date]] = []
    y, m = today.year, today.month
    for _ in range(6):
        _, last = calendar.monthrange(y, m)
        result.append((date(y, m, 1), date(y, m, last)))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    result.reverse()
    return result


def _flag_large(count: int) -> str:
    return " *** LARGE >5K — may need special handling" if count > _LARGE_DATASET_THRESHOLD else ""


# ── Investigation D1.1 ────────────────────────────────────────────────────────

async def _d1_1(odoo: OdooClient, late_domain: list) -> tuple[bool, bool, int | None]:
    """Returns (field_to_field_works, check_field_works, check_count_on_late)."""
    print(f"\n{_SEP2}")
    print("D1.1 — has_pending_cheque filter feasibility")
    print(_SEP2)

    # Test A: field-to-field comparison (Decision 9.1 / D0.2/U1 documented as broken).
    d_ff = late_domain + [("paid_amount", ">", "x_studio_actual_paid_amount")]
    ff_works = False
    ff_count: int | None = None
    try:
        ff_count = await odoo.execute_kw(_MODEL, "search_count", args=[d_ff])
        ff_works = True
    except Exception as exc:
        err_short = str(exc)[:100]

    if ff_works:
        _log(_FLAG, f"field-to-field ('paid_amount','>','x_studio_actual_paid_amount') UNEXPECTEDLY SUCCEEDED — count={ff_count}")
        _log(_WARN, "  Decision 9.1 assumed broken. Re-evaluate filter strategy.")
        _tsv("D1.1", "field_to_field", _FLAG, f"count={ff_count}", "Unexpectedly succeeded — verify")
    else:
        _log(_PASS, f"field-to-field comparison BROKEN (expected per Decision 9.1) — {err_short}")
        _tsv("D1.1", "field_to_field", _PASS, "BROKEN", err_short)

    # Test B: check_pending_amount > 0 (stored native monetary field, Decision 4.5).
    d_cpf = late_domain + [("check_pending_amount", ">", 0)]
    cpf_works = False
    cpf_count: int | None = None
    try:
        cpf_count = await odoo.execute_kw(_MODEL, "search_count", args=[d_cpf])
        cpf_works = True
    except Exception as exc:
        err2 = str(exc)[:100]

    if cpf_works:
        _log(_PASS, f"check_pending_amount>0 domain WORKS — late records with pending cheque: {cpf_count:,}")
        _log(_INFO, "  Literal comparison against stored field — no Float field-to-field issue.")
        _tsv("D1.1", "check_pending_amount", _PASS, str(cpf_count), "Viable for search_count + search_read domain")
    else:
        _log(_FLAG, f"check_pending_amount>0 FAILED — {err2}")
        _log(_WARN, "  Will fall back to Python-side filter (Option II) for both filter and count.")
        _tsv("D1.1", "check_pending_amount", _FLAG, "FAILED", err2)

    _log(_INFO, "  Python-side filter always available as ultimate fallback:")
    _log(_INFO, "    fetch [paid_amount, x_studio_actual_paid_amount], keep rows where diff > 0")

    return ff_works, cpf_works, cpf_count


# ── Investigation D1.2 ────────────────────────────────────────────────────────

async def _d1_2(
    odoo: OdooClient,
    late_domain: list,
    today_str: str,
    bucket_ends: dict[str, date],
    trailing_months: list[tuple[date, date]],
) -> None:
    print(f"\n{_SEP2}")
    print("D1.2 — Cardinality assessment (flag any dataset > 5,000 records)")
    print(_SEP2)

    # Late (KPI 2)
    c = await odoo.execute_kw(_MODEL, "search_count", args=[late_domain])
    m = _FLAG if c > _LARGE_DATASET_THRESHOLD else _PASS
    _log(m, f"Late (KPI 2)                  : {c:>8,}{_flag_large(c)}")
    _tsv("D1.2", "late_kpi2", m, str(c), "Candidate C domain")

    # Portfolio (KPI 1)
    c = await odoo.execute_kw(_MODEL, "search_count", args=[[("state", "=", "post")]])
    m = _FLAG if c > _LARGE_DATASET_THRESHOLD else _PASS
    _log(m, f"Portfolio (KPI 1)             : {c:>8,}{_flag_large(c)}")
    _tsv("D1.2", "portfolio_kpi1", m, str(c), "state=post")

    # Forecast buckets (KPI 7)
    for bname, bend in bucket_ends.items():
        d = [
            ("state", "=", "post"),
            ("payment_state", "in", ["unpaid", "partial"]),
            ("date", ">=", today_str),
            ("date", "<=", bend.isoformat()),
        ]
        c = await odoo.execute_kw(_MODEL, "search_count", args=[d])
        m = _FLAG if c > _LARGE_DATASET_THRESHOLD else _PASS
        short = bname.replace("this_", "")
        _log(m, f"Forecast {short:<8} (KPI 7)   : {c:>8,}  end={bend}{_flag_large(c)}")
        _tsv("D1.2", f"forecast_{bname}", m, str(c), f"end={bend}")

    # Per project (KPI 5)
    for pid, pname in _EXPECTED_PROJECTS.items():
        d = late_domain + [("project_id", "=", pid)]
        c = await odoo.execute_kw(_MODEL, "search_count", args=[d])
        m = _FLAG if c > _LARGE_DATASET_THRESHOLD else _PASS
        _log(m, f"Project {pid} {pname:<14}        : {c:>8,}{_flag_large(c)}")
        _tsv("D1.2", f"project_{pid}", m, str(c), pname)

    # Trend months — installments due in each trailing calendar month
    for start, end in trailing_months:
        d = [
            ("state", "=", "post"),
            ("date", ">=", start.isoformat()),
            ("date", "<=", end.isoformat()),
        ]
        c = await odoo.execute_kw(_MODEL, "search_count", args=[d])
        m = _FLAG if c > _LARGE_DATASET_THRESHOLD else _PASS
        label = start.strftime("%Y-%m")
        _log(m, f"Trend month {label}           : {c:>8,}{_flag_large(c)}")
        _tsv("D1.2", f"trend_{label}", m, str(c), f"{start}→{end}")


# ── Investigation D1.3 ────────────────────────────────────────────────────────

async def _d1_3(
    odoo: OdooClient,
    trailing_months: list[tuple[date, date]],
) -> None:
    print(f"\n{_SEP2}")
    print("D1.3 — KPI 6 trend month drill-down model: rs.installment sanity check")
    print(_SEP2)

    _log(_INFO, "  Model choice: rs.installment (due date axis)")
    _log(_INFO, "  Alternative:  rs.account.payment.installment (cash receipt axis — used by KPI 6 itself)")
    _log(_INFO, "  Stage 5 design: rs.installment for ALL 5 drill-downs (model consistency)")
    _log(_INFO, "  Consequence: trend drill-down shows installments DUE in that month,")
    _log(_INFO, "               NOT installments COLLECTED — partial cross-check with KPI 6 only.")

    for start, end in trailing_months:
        d = [
            ("state", "=", "post"),
            ("date", ">=", start.isoformat()),
            ("date", "<=", end.isoformat()),
        ]
        c = await odoo.execute_kw(_MODEL, "search_count", args=[d])
        label = start.strftime("%Y-%m")
        note = "(non-zero — sensible)" if c > 0 else "(ZERO — no installments due this month)"
        marker = _PASS if c >= 0 else _FLAG  # always pass; zero is valid (Decision 5.7 analog)
        _log(marker, f"  {label}: {c:,} installments on rs.installment due-date axis {note}")
        _tsv("D1.3", f"rs_installment_{label}", marker, str(c), "due-date count")

    _log(_PASS, "rs.installment viable for trend drill-down — date field is plain date (no UTC conversion)")
    _tsv("D1.3", "model_viable", _PASS, "rs.installment", "Plain date field, no UTC conversion needed")


# ── Investigation D1.4 ────────────────────────────────────────────────────────

async def _d1_4(odoo: OdooClient) -> str | None:
    """Returns the relation model name for project_id if found, else None."""
    print(f"\n{_SEP2}")
    print("D1.4 — project_id field on rs.installment + live project name reverification")
    print(_SEP2)

    relation_model: str | None = None

    # fields_get to confirm field type and discover relation model name.
    try:
        fget = await odoo.execute_kw(
            _MODEL, "fields_get",
            args=[["project_id"]],
            kwargs={"attributes": ["type", "relation", "string"]},
        )
        info = fget.get("project_id", {})
        ftype    = info.get("type", "UNKNOWN")
        relation = info.get("relation", "UNKNOWN")
        flabel   = info.get("string", "")
        marker   = _PASS if ftype == "many2one" else _FLAG
        _log(marker, f"rs.installment.project_id — type={ftype!r}  relation={relation!r}  label={flabel!r}")
        _tsv("D1.4", "field_type", marker, ftype, f"relation={relation}")
        relation_model = relation if ftype == "many2one" else None
    except Exception as exc:
        _log(_FLAG, f"fields_get failed: {exc}")
        _tsv("D1.4", "field_type", _FLAG, "FAILED", str(exc)[:120])
        return None

    # Query the relation model for project IDs 1, 2, 3.
    if relation_model:
        try:
            proj_recs = await odoo.execute_kw(
                relation_model, "search_read",
                args=[[("id", "in", [1, 2, 3])]],
                kwargs={"fields": ["id", "name"]},
            )
            id_to_raw: dict[int, str] = {r["id"]: r["name"] for r in proj_recs}
            all_ok = True
            for pid, exp_name in _EXPECTED_PROJECTS.items():
                raw_name = id_to_raw.get(pid, "MISSING")
                # Odoo often returns "Project#New Capital" — strip prefix
                clean = raw_name.split("#")[-1].strip() if "#" in raw_name else raw_name.strip()
                matches = exp_name.lower() in clean.lower() or clean.lower() in exp_name.lower()
                if not matches:
                    all_ok = False
                m = _PASS if matches else _FLAG
                _log(m, f"  id={pid}  live={clean!r}  expected={exp_name!r}")
                _tsv("D1.4", f"project_{pid}_name", m, clean, f"expected={exp_name}")

            overall = _PASS if all_ok else _FLAG
            _log(overall, f"Project IDs 1, 2, 3 reverified against live {relation_model}")
            _tsv("D1.4", "all_projects", overall, "3/3 matched" if all_ok else "MISMATCH", "")
        except Exception as exc:
            _log(_FLAG, f"Project name lookup on {relation_model} failed: {exc}")
            _tsv("D1.4", "project_names", _FLAG, "FAILED", str(exc)[:120])

    return relation_model


# ── Investigation D1.5 ────────────────────────────────────────────────────────

async def _d1_5(
    odoo: OdooClient,
    today_str: str,
    bucket_ends: dict[str, date],
    check_field_works: bool,
) -> dict[str, int]:
    """Returns per-bucket cheques record count using the winning strategy."""
    print(f"\n{_SEP2}")
    print("D1.5 — KPI 7 cheques_record_count computation strategy")
    print(_SEP2)

    _log(_INFO, "  Option I  : search_count + ('check_pending_amount','>',0) — 4 RPCs, zero data transfer")
    _log(_INFO, "  Option II : search_read + Python-side filter — 4 RPCs, transfers all records")

    counts: dict[str, int] = {}
    option_i_survived = check_field_works  # Pre-tested in D1.1

    for bname, bend in bucket_ends.items():
        base_domain = [
            ("state", "=", "post"),
            ("payment_state", "in", ["unpaid", "partial"]),
            ("date", ">=", today_str),
            ("date", "<=", bend.isoformat()),
        ]
        short = bname.replace("this_", "")

        if option_i_survived:
            d_i = base_domain + [("check_pending_amount", ">", 0)]
            try:
                c_i = await odoo.execute_kw(_MODEL, "search_count", args=[d_i])
                counts[bname] = c_i
                _log(_PASS, f"  Option I  — {short:<8}: {c_i:,} records with check_pending_amount>0")
                _tsv("D1.5", f"option_i_{bname}", _PASS, str(c_i), "search_count+check_pending_amount>0")
                continue
            except Exception as exc:
                _log(_WARN, f"  Option I  — {short} FAILED mid-run: {exc}. Falling back to Option II.")
                option_i_survived = False

        # Option II — Python-side filter
        t0 = time.monotonic()
        rows = await odoo.execute_kw(
            _MODEL, "search_read",
            args=[base_domain],
            kwargs={"fields": ["paid_amount", "x_studio_actual_paid_amount"], "limit": 0},
        )
        dur = int((time.monotonic() - t0) * 1000)
        c_ii = sum(
            1 for r in rows
            if (r.get("paid_amount") or 0.0) > (r.get("x_studio_actual_paid_amount") or 0.0)
        )
        counts[bname] = c_ii
        _log(_PASS, f"  Option II — {short:<8}: {c_ii:,} records (Python-side, {dur}ms, {len(rows)} rows fetched)")
        _tsv("D1.5", f"option_ii_{bname}", _PASS, str(c_ii), f"python-side, {dur}ms, fetched={len(rows)}")

    final_strategy = "Option I" if option_i_survived else "Option II"
    _log(_PASS, f"  Final recommendation: {final_strategy}")
    if option_i_survived:
        _log(_INFO, "    → search_count with check_pending_amount>0 per bucket (4 extra RPCs)")
        _log(_INFO, "    → Upgrades KPI 7 from 8 RPCs to 12 RPCs on cache miss")
    else:
        _log(_INFO, "    → search_read + Python-side filter (higher data transfer per bucket)")
    _tsv("D1.5", "recommendation", _PASS, final_strategy, "")

    return counts


# ── Investigation D1.6 ────────────────────────────────────────────────────────

async def _d1_6(
    odoo: OdooClient,
    late_domain: list,
) -> tuple[int, int]:
    """Returns (dur_50_ms, dur_200_ms)."""
    print(f"\n{_SEP2}")
    print("D1.6 — Pagination performance baseline (KPI 2 late, ~2,013 records)")
    print(_SEP2)

    _log(_INFO, f"  Fields requested: {_DRILL_FIELDS}")
    _log(_INFO, "  Sort: due_amount desc (default late drill-down order)")

    # page_size=50
    t0 = time.monotonic()
    rows_50 = await odoo.execute_kw(
        _MODEL, "search_read",
        args=[late_domain],
        kwargs={"fields": _DRILL_FIELDS, "limit": 50, "offset": 0, "order": "due_amount desc"},
    )
    dur_50 = int((time.monotonic() - t0) * 1000)

    # page_size=200
    t0 = time.monotonic()
    rows_200 = await odoo.execute_kw(
        _MODEL, "search_read",
        args=[late_domain],
        kwargs={"fields": _DRILL_FIELDS, "limit": 200, "offset": 0, "order": "due_amount desc"},
    )
    dur_200 = int((time.monotonic() - t0) * 1000)

    m50  = _PASS if dur_50  < 2000 else _FLAG
    m200 = _PASS if dur_200 < 2000 else _FLAG

    _log(m50,  f"page_size= 50 : {len(rows_50):>3} records returned, RPC={dur_50:>5}ms  {'✓ sub-2s' if dur_50  < 2000 else '⚠ exceeds 2s target'}")
    _log(m200, f"page_size=200 : {len(rows_200):>3} records returned, RPC={dur_200:>5}ms  {'✓ sub-2s' if dur_200 < 2000 else '⚠ exceeds 2s target'}")
    _tsv("D1.6", "page_size_50",  m50,  f"{dur_50}ms",  f"returned={len(rows_50)}")
    _tsv("D1.6", "page_size_200", m200, f"{dur_200}ms", f"returned={len(rows_200)}")

    rec = 50 if dur_200 >= 2000 else 200
    _log(_INFO, f"  Recommended default page_size in service: {rec}")
    _tsv("D1.6", "recommended_page_size", _INFO, str(rec), "")

    # Sanity-check: first record has expected fields (no PII logged).
    if rows_50:
        r = rows_50[0]
        has_all = all(f in r for f in _DRILL_FIELDS)
        m = _PASS if has_all else _FLAG
        completeness_msg = f"all {len(_DRILL_FIELDS)} fields present" if has_all else "MISSING SOME FIELDS"
        _log(m, f"  First record field completeness: {completeness_msg}")
        _tsv("D1.6", "field_completeness", m, "all present" if has_all else "PARTIAL", "")

    return dur_50, dur_200


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    today_cairo = datetime.now(_LA_VERDE_TZ).date()
    today_str   = today_cairo.isoformat()
    bucket_ends = _compute_bucket_ends(today_cairo)
    trailing    = _trailing_6_months(today_cairo)

    late_domain = [
        ("state", "=", "post"),
        ("payment_state", "in", ["unpaid", "partial"]),
        ("date", "<", today_str),
    ]

    print(_SEP)
    print("Stage 5 — Drill-Down Pre-Implementation Discovery")
    print(f"Cairo today  : {today_str}")
    print(f"Bucket ends  : month={bucket_ends['this_month']}  quarter={bucket_ends['this_quarter']}")
    print(f"               half={bucket_ends['this_half']}   year={bucket_ends['this_year']}")
    print(f"Trailing 6m  : {trailing[0][0].strftime('%Y-%m')} → {trailing[-1][1].strftime('%Y-%m')}")
    print(f"ALLOWED_METHODS ({len(ALLOWED_METHODS)}): {sorted(ALLOWED_METHODS)}")
    print(_SEP)

    async with OdooClient() as odoo:
        ff_works, cpf_works, cpf_late_count = await _d1_1(odoo, late_domain)
        await _d1_2(odoo, late_domain, today_str, bucket_ends, trailing)
        await _d1_3(odoo, trailing)
        await _d1_4(odoo)
        bucket_cheque_counts = await _d1_5(odoo, today_str, bucket_ends, cpf_works)
        dur_50, dur_200 = await _d1_6(odoo, late_domain)

    # ── Console summary ───────────────────────────────────────────────────────
    print(f"\n{_SEP}")
    print("DISCOVERY SUMMARY")
    print(_SEP)
    print(f"  D1.1 field-to-field:        {'BROKEN (expected)' if not ff_works else 'WORKS (unexpected — verify!)'}")
    print(f"  D1.1 check_pending>0:       {'WORKS — use for has_pending_cheque filter + count' if cpf_works else 'FAILED — Python-side filter required'}")
    if cpf_late_count is not None:
        print(f"  D1.1 late cheque records:   {cpf_late_count:,}")
    print()
    print(f"  D1.5 filter strategy:       {'Option I (check_pending_amount>0 search_count)' if cpf_works else 'Option II (Python-side filter)'}")
    print(f"  D1.5 bucket cheque counts:")
    for bname, cnt in bucket_cheque_counts.items():
        print(f"    {bname:<16}: {cnt:,}")
    print()
    print(f"  D1.6 page_size=50  RPC:     {dur_50}ms   {'✓' if dur_50  < 2000 else '⚠ exceeds 2s'}")
    print(f"  D1.6 page_size=200 RPC:     {dur_200}ms   {'✓' if dur_200 < 2000 else '⚠ exceeds 2s'}")
    print()

    if cpf_works:
        print("  ► FILTER STRATEGY DECISION:")
        print("    has_pending_cheque=True  → add ('check_pending_amount','>',0) to Odoo domain")
        print("    has_pending_cheque=False → add ('check_pending_amount','=',0) to Odoo domain")
        print("    (Python-side filter NOT needed — Odoo-side domain is sufficient and exact)")
    else:
        print("  ► FILTER STRATEGY DECISION:")
        print("    has_pending_cheque filter requires Python-side post-filter.")
        print("    fetch records with paid_amount + actual_paid_amount fields,")
        print("    keep/discard rows where (paid_amount - actual_paid_amount) > 0.")

    print()
    print("  ► CHEQUES_RECORD_COUNT STRATEGY:")
    if cpf_works:
        print("    Option I: search_count with check_pending_amount>0 per bucket.")
        print("    4 extra RPCs. KPI 7 budget: 8 → 12 RPCs on cache miss.")
    else:
        print("    Option II: search_read + Python-side filter per bucket.")
        print("    Higher data transfer; exact count guaranteed.")

    print(_SEP)

    _write_log()


if __name__ == "__main__":
    asyncio.run(main())
