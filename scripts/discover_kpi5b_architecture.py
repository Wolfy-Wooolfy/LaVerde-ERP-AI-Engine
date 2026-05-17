"""
D0 — KPI 5b Architecture Discovery: Collection Rate per Project.

Open question: does rs.account.payment.installment (HEADER) carry a project_id
field (Branch A — direct filter), or must we join via the LINE model
rs.account.payment.installment.line → rs.installment.project_id (Branch B)?

This script answers that question via fields_get, then queries all data needed
for Checkpoint 1 verification.

Architecture under test (Decision 6.1 extended to per-project):
  Collection Rate per Project (%) =
    NUMERATOR   / DENOMINATOR * 100
  Branch A numerator: SUM(HEADER.amount) WHERE state='post' AND date in period
                       AND HEADER.project_id = N
  Branch B numerator: SUM(LINE.amount) WHERE LINE.installment_id in project N's
                       installment IDs AND LINE.payment_id.state='post'
                       AND LINE.payment_id.date in period
  Denominator (both): SUM(rs.installment.amount) WHERE state='post'
                       AND project_id=N AND date in period

This script:
  - Determines Branch A or B via a single fields_get RPC.
  - Counts null project_id installments (all-time, MTD, YTD) per Risk 1.
  - Branch B only: paginated pre-fetch of installment IDs per project (limit=5000);
    multi-project sanity check on 10 sampled HEADERs per Risk 3.
  - Queries numerator and denominator for 3 projects × 2 periods (12 RPCs).
  - Queries global KPI 4 totals for cross-check (4 RPCs).
  - Prints 6-row per-project table with TOTAL and global cross-check deltas.
  - Prints manual Odoo navigation guide for Checkpoint 1 (3 YTD denominator checks).
  - Appends 6 TSV rows to logs/kpi5b_discovery.log (3 projects × 2 periods).
  - Exits 0 on completion regardless of findings.

Hard constraint: MUST NOT import from backend.modules.collections.services.
  _tz_period_bounds() is inlined here. OdooClient is used directly.

Usage:
    python scripts/discover_kpi5b_architecture.py
"""

import asyncio
import io
import os
import sys
import time
from datetime import date, datetime, time as dt_time, timezone
from zoneinfo import ZoneInfo

from backend.shared.odoo.client import OdooClient

# Force UTF-8 stdout (Windows consoles default to cp1252)
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Constants ─────────────────────────────────────────────────────────────────

_HEADER_MODEL = "rs.account.payment.installment"
_LINE_MODEL   = "rs.account.payment.installment.line"
_INST_MODEL   = "rs.installment"

_PROJECT_NAMES: dict[int, str] = {
    1: "New Capital",
    2: "Cassette",
    3: "La puerta",
}

# Egypt observes DST: UTC+2 Nov-Apr, UTC+3 May-Oct (re-introduced 2023). Decision 5.9.
_LA_VERDE_TZ = ZoneInfo("Africa/Cairo")
_UTC_TZ      = ZoneInfo("UTC")

_LOG_FILE = "logs/kpi5b_discovery.log"
_SEP  = "═" * 78
_SEP2 = "─" * 76
_INFO = "[INFO]"
_PASS = "[PASS]"
_FLAG = "[FLAG]"
_WARN = "[WARN]"


# ── Period computation (inlined — must not import from services) ───────────────

def _tz_period_bounds(period_start: date, period_end: date) -> tuple[str, str]:
    """Convert Egypt-local period boundaries to UTC datetime strings.

    Mirrors kpi_service._tz_period_bounds() (Decision 5.9). Inlined here
    because this script must not import from backend.modules.collections.services.
    """
    start_local = datetime.combine(period_start, dt_time.min, tzinfo=_LA_VERDE_TZ)
    end_local   = datetime.combine(period_end, dt_time(23, 59, 59), tzinfo=_LA_VERDE_TZ)
    return (
        start_local.astimezone(_UTC_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        end_local.astimezone(_UTC_TZ).strftime("%Y-%m-%d %H:%M:%S"),
    )


def _compute_period_bounds(today: date) -> dict:
    mtd_start = today.replace(day=1)
    mtd_end   = today
    ytd_start = today.replace(month=1, day=1)  # Decision 6.2: calendar year
    ytd_end   = today

    mtd_start_utc, mtd_end_utc = _tz_period_bounds(mtd_start, mtd_end)
    ytd_start_utc, ytd_end_utc = _tz_period_bounds(ytd_start, ytd_end)

    return {
        "mtd_start": mtd_start, "mtd_end": mtd_end,
        "ytd_start": ytd_start, "ytd_end": ytd_end,
        "mtd_start_utc": mtd_start_utc, "mtd_end_utc": mtd_end_utc,
        "ytd_start_utc": ytd_start_utc, "ytd_end_utc": ytd_end_utc,
        "mtd_start_iso": mtd_start.isoformat(), "mtd_end_iso": mtd_end.isoformat(),
        "ytd_start_iso": ytd_start.isoformat(), "ytd_end_iso": ytd_end.isoformat(),
    }


# ── Architecture detection ────────────────────────────────────────────────────

async def _check_header_project_id_field(client: OdooClient) -> bool:
    """Return True if HEADER model exposes a project_id field (→ Branch A).

    Uses fields_get to introspect the HEADER model. If project_id is absent,
    we must use the indirect LINE join (Branch B). 1 RPC.
    """
    t0 = time.monotonic()
    try:
        fields = await client.execute_kw(
            _HEADER_MODEL,
            "fields_get",
            args=[["project_id"]],
            kwargs={"attributes": ["string", "type", "relation"]},
        )
    except Exception as exc:
        raise RuntimeError(f"fields_get on {_HEADER_MODEL} failed: {exc}") from exc
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    has_field = isinstance(fields, dict) and bool(fields.get("project_id"))

    print(f"\n{_INFO} fields_get on {_HEADER_MODEL} [{elapsed_ms}ms]")
    print(f"     project_id field present: {has_field}")
    if has_field:
        finfo = fields["project_id"]
        print(f"     type     : {finfo.get('type')}")
        print(f"     string   : {finfo.get('string')}")
        print(f"     relation : {finfo.get('relation')}")
        print(f"     → BRANCH A: direct ('project_id', '=', N) filter on {_HEADER_MODEL}")
    else:
        print(f"     → BRANCH B: indirect join {_LINE_MODEL} → {_INST_MODEL}.project_id")
        print(f"       Domain will use ('installment_id', 'in', <project N's inst IDs>)")
    return has_field


# ── Null project_id analysis (Risk 1) ─────────────────────────────────────────

async def _query_nullproject_counts(client: OdooClient, b: dict) -> dict:
    """Count/sum rs.installment records with no project_id (state=post).

    Three RPCs: all-time count, MTD count+amount, YTD count+amount.
    These null-project records explain any delta between per-project sums
    and global KPI 4 totals. Per Risk 1: penalty installments may lack project_id.
    """
    print(f"\n{_INFO} Null project_id analysis (3 RPCs)...")
    base_domain = [("state", "=", "post"), ("project_id", "=", False)]

    # All-time count
    t0 = time.monotonic()
    all_time_count = await client.execute_kw(
        _INST_MODEL, "search_count", args=[base_domain], kwargs={}
    )
    all_time_ms = int((time.monotonic() - t0) * 1000)

    # MTD count + amount
    mtd_domain = base_domain + [
        ("date", ">=", b["mtd_start_iso"]),
        ("date", "<=", b["mtd_end_iso"]),
    ]
    t0 = time.monotonic()
    rows = await client.execute_kw(
        _INST_MODEL, "read_group", args=[mtd_domain, ["amount"], []], kwargs={"lazy": False}
    )
    mtd_ms = int((time.monotonic() - t0) * 1000)
    row = rows[0] if rows else {}
    mtd_count  = int(row.get("__count") or 0)
    mtd_amount = float(row.get("amount") or 0.0)

    # YTD count + amount
    ytd_domain = base_domain + [
        ("date", ">=", b["ytd_start_iso"]),
        ("date", "<=", b["ytd_end_iso"]),
    ]
    t0 = time.monotonic()
    rows = await client.execute_kw(
        _INST_MODEL, "read_group", args=[ytd_domain, ["amount"], []], kwargs={"lazy": False}
    )
    ytd_ms = int((time.monotonic() - t0) * 1000)
    row = rows[0] if rows else {}
    ytd_count  = int(row.get("__count") or 0)
    ytd_amount = float(row.get("amount") or 0.0)

    print(f"     All-time null-project installments : {all_time_count:,} records [{all_time_ms}ms]")
    print(f"     MTD null-project installments      : {mtd_count:,} records / {mtd_amount:,.2f} EGP [{mtd_ms}ms]")
    print(f"     YTD null-project installments      : {ytd_count:,} records / {ytd_amount:,.2f} EGP [{ytd_ms}ms]")

    if all_time_count == 0:
        print(f"     {_PASS} No null-project installments — sum-of-projects must equal global KPI 4.")
    else:
        print(f"     {_WARN} {all_time_count:,} null-project installments exist.")
        print(f"            Cross-check delta ≈ null-project amounts above (denominator side).")

    return {
        "all_time": {"count": all_time_count, "elapsed_ms": all_time_ms},
        "mtd":      {"count": mtd_count, "amount": mtd_amount, "elapsed_ms": mtd_ms},
        "ytd":      {"count": ytd_count, "amount": ytd_amount, "elapsed_ms": ytd_ms},
    }


# ── Branch B helpers ──────────────────────────────────────────────────────────

async def _fetch_installment_ids_for_project(client: OdooClient, project_id: int) -> list[int]:
    """Paginated fetch of all posted rs.installment IDs for one project (Branch B).

    No date filter — all posted installments for the project are needed because
    payments can arrive for any installment regardless of its due date. The date
    filter for the period is applied on LINE.payment_id.date, not installment.date.
    Pagination at limit=5000 (Risk 2 approval).
    """
    domain = [("state", "=", "post"), ("project_id", "=", project_id)]
    all_ids: list[int] = []
    offset = 0
    limit  = 5000
    page   = 0
    t_total = time.monotonic()

    while True:
        t0 = time.monotonic()
        records = await client.execute_kw(
            _INST_MODEL,
            "search_read",
            args=[domain, ["id"]],
            kwargs={"limit": limit, "offset": offset},
        )
        elapsed = int((time.monotonic() - t0) * 1000)
        page += 1
        ids = [r["id"] for r in records]
        all_ids.extend(ids)
        print(f"       Page {page}: {len(ids):,} IDs fetched [{elapsed}ms] (offset={offset})")
        if len(records) < limit:
            break
        offset += limit

    total_ms = int((time.monotonic() - t_total) * 1000)
    print(f"     Total: {len(all_ids):,} installment IDs for project {project_id} "
          f"({_PROJECT_NAMES[project_id]}) [{total_ms}ms]")
    if len(all_ids) > 8000:
        print(f"     {_WARN} Large ID set ({len(all_ids):,} IDs) — D1 service may need chunked IN clause.")
    return all_ids


async def _check_multiproject_headers(client: OdooClient) -> None:
    """Branch B sanity check: sample 10 HEADERs, verify each links to one project.

    Per Risk 3 guidance: multi-project bulk payments are rare but possible.
    Prints [WARN] if any sampled HEADER links to installments from >1 project.
    Uses 3 RPCs total: HEADERs → LINEs → installment project_ids.
    """
    print(f"\n{_INFO} Multi-project sanity check on 10 sampled HEADERs (3 RPCs)...")

    # RPC 1: sample 10 posted HEADERs
    t0 = time.monotonic()
    headers = await client.execute_kw(
        _HEADER_MODEL,
        "search_read",
        args=[[("state", "=", "post")], ["id"]],
        kwargs={"limit": 10},
    )
    header_ids = [h["id"] for h in headers]
    print(f"     Sampled {len(header_ids)} HEADER records [{int((time.monotonic()-t0)*1000)}ms]")

    if not header_ids:
        print(f"     {_INFO} No posted HEADER records found — skipping.")
        return

    # RPC 2: all LINEs for those HEADERs
    t0 = time.monotonic()
    lines = await client.execute_kw(
        _LINE_MODEL,
        "search_read",
        args=[[("payment_id", "in", header_ids)], ["payment_id", "installment_id"]],
        kwargs={},
    )
    raw_inst_ids: set[int] = set()
    for line in lines:
        inst_raw = line.get("installment_id")
        if isinstance(inst_raw, (list, tuple)) and inst_raw:
            raw_inst_ids.add(int(inst_raw[0]))
        elif inst_raw:
            raw_inst_ids.add(int(inst_raw))
    inst_ids = list(raw_inst_ids)
    print(f"     Found {len(lines):,} LINE records, {len(inst_ids):,} unique installments "
          f"[{int((time.monotonic()-t0)*1000)}ms]")

    if not inst_ids:
        print(f"     {_WARN} No LINE records for sampled HEADERs — LINE model may be empty or "
              f"field name differs from 'payment_id'/'installment_id'.")
        print(f"            Investigate before D1.")
        return

    # RPC 3: installment project_ids
    t0 = time.monotonic()
    installments = await client.execute_kw(
        _INST_MODEL,
        "search_read",
        args=[[("id", "in", inst_ids)], ["id", "project_id"]],
        kwargs={},
    )
    inst_to_proj: dict[int, int | None] = {}
    for inst in installments:
        pid_raw = inst.get("project_id")
        if isinstance(pid_raw, (list, tuple)) and pid_raw:
            inst_to_proj[inst["id"]] = int(pid_raw[0])
        elif pid_raw:
            inst_to_proj[inst["id"]] = int(pid_raw)
        else:
            inst_to_proj[inst["id"]] = None
    print(f"     Mapped {len(installments):,} installments to project_ids "
          f"[{int((time.monotonic()-t0)*1000)}ms]")

    # Build payment_id → set of project_ids
    payment_to_projects: dict[int, set] = {}
    for line in lines:
        pay_raw = line.get("payment_id")
        pay_id = int(pay_raw[0]) if isinstance(pay_raw, (list, tuple)) and pay_raw else (int(pay_raw) if pay_raw else None)
        inst_raw = line.get("installment_id")
        inst_id = int(inst_raw[0]) if isinstance(inst_raw, (list, tuple)) and inst_raw else (int(inst_raw) if inst_raw else None)
        if pay_id is not None and inst_id is not None:
            proj = inst_to_proj.get(inst_id)
            payment_to_projects.setdefault(pay_id, set()).add(proj)

    multi_count = 0
    for pay_id, proj_set in payment_to_projects.items():
        clean = {p for p in proj_set if p is not None}
        if len(clean) > 1:
            multi_count += 1
            proj_names = [_PROJECT_NAMES.get(p, f"id={p}") for p in sorted(clean)]
            print(f"     {_WARN} HEADER id={pay_id} links to installments from "
                  f"{len(clean)} projects: {proj_names}")

    if multi_count == 0:
        print(f"     {_PASS} All {len(payment_to_projects)} sampled HEADERs link to a single project.")
        print(f"            No cross-project payment detected in sample.")
    else:
        print(f"     {_WARN} {multi_count}/{len(payment_to_projects)} sampled HEADERs span multiple projects.")
        print(f"            Branch B per-project amounts will double-count these HEADERs.")
        print(f"            Investigate and report before D1.")


# ── Per-project numerator queries ─────────────────────────────────────────────

async def _query_numerator_branch_a(
    client: OdooClient,
    project_id: int,
    utc_start: str,
    utc_end: str,
) -> dict:
    """Branch A: direct project_id filter on HEADER (1 read_group RPC)."""
    domain = [
        ("state",      "=",  "post"),
        ("date",       ">=", utc_start),
        ("date",       "<=", utc_end),
        ("project_id", "=",  project_id),
    ]
    t0 = time.monotonic()
    rows = await client.execute_kw(
        _HEADER_MODEL, "read_group",
        args=[domain, ["amount"], []],
        kwargs={"lazy": False},
    )
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    row = rows[0] if rows else {}
    return {
        "amount":       float(row.get("amount") or 0.0),
        "record_count": int(row.get("__count") or 0),
        "elapsed_ms":   elapsed_ms,
        "domain":       domain,
        "model":        _HEADER_MODEL,
    }


async def _query_numerator_branch_b(
    client: OdooClient,
    project_id: int,
    installment_ids: list[int],
    utc_start: str,
    utc_end: str,
) -> dict:
    """Branch B: filter LINE model by installment_id set and payment UTC date (1 read_group RPC).

    Decision 5.10 note: for period TOTAL summation (no monthly groupby) the
    UTC domain is sufficient — timezone boundary effects only matter for monthly
    bucketing. This mirrors how the service will use read_group for total sums.
    """
    if not installment_ids:
        return {
            "amount": 0.0, "record_count": 0, "elapsed_ms": 0,
            "domain": [], "model": _LINE_MODEL,
        }

    domain = [
        ("installment_id",   "in", installment_ids),
        ("payment_id.state", "=",  "post"),
        ("payment_id.date",  ">=", utc_start),
        ("payment_id.date",  "<=", utc_end),
    ]
    t0 = time.monotonic()
    rows = await client.execute_kw(
        _LINE_MODEL, "read_group",
        args=[domain, ["amount"], []],
        kwargs={"lazy": False},
    )
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    row = rows[0] if rows else {}
    return {
        "amount":       float(row.get("amount") or 0.0),
        "record_count": int(row.get("__count") or 0),
        "elapsed_ms":   elapsed_ms,
        "domain": [
            f"installment_id in [{len(installment_ids)} IDs — project {project_id}]",
            ("payment_id.state", "=", "post"),
            ("payment_id.date",  ">=", utc_start),
            ("payment_id.date",  "<=", utc_end),
        ],
        "model": _LINE_MODEL,
    }


# ── Per-project denominator query ─────────────────────────────────────────────

async def _query_denominator_per_project(
    client: OdooClient,
    project_id: int,
    iso_start: str,
    iso_end: str,
) -> dict:
    """Denominator: posted rs.installment for one project within date period (1 read_group RPC).

    Uses rs.installment.amount (contractual face value), NOT due_amount.
    Decision 6.1: due_amount is self-referential and time-unstable.
    """
    domain = [
        ("state",      "=",  "post"),
        ("project_id", "=",  project_id),
        ("date",       ">=", iso_start),
        ("date",       "<=", iso_end),
    ]
    t0 = time.monotonic()
    rows = await client.execute_kw(
        _INST_MODEL, "read_group",
        args=[domain, ["amount"], []],
        kwargs={"lazy": False},
    )
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    row = rows[0] if rows else {}
    return {
        "amount":       float(row.get("amount") or 0.0),
        "record_count": int(row.get("__count") or 0),
        "elapsed_ms":   elapsed_ms,
        "domain":       domain,
    }


# ── Global KPI 4 cross-check queries ─────────────────────────────────────────

async def _query_kpi4_global_totals(client: OdooClient, b: dict) -> dict:
    """Query global KPI 4 numerator and denominator for cross-check (4 read_group RPCs).

    Sum-of-projects must equal these totals (± null-project records).
    """
    print(f"\n{_INFO} Global KPI 4 cross-check totals (4 RPCs)...")
    t0_all = time.monotonic()

    # Global MTD numerator (HEADER)
    domain = [("state","=","post"), ("date",">=",b["mtd_start_utc"]), ("date","<=",b["mtd_end_utc"])]
    rows = await client.execute_kw(_HEADER_MODEL, "read_group", args=[domain, ["amount"], []], kwargs={"lazy": False})
    row = rows[0] if rows else {}
    mtd_num = float(row.get("amount") or 0.0); mtd_num_ct = int(row.get("__count") or 0)

    # Global MTD denominator (rs.installment)
    domain = [("state","=","post"), ("date",">=",b["mtd_start_iso"]), ("date","<=",b["mtd_end_iso"])]
    rows = await client.execute_kw(_INST_MODEL, "read_group", args=[domain, ["amount"], []], kwargs={"lazy": False})
    row = rows[0] if rows else {}
    mtd_den = float(row.get("amount") or 0.0); mtd_den_ct = int(row.get("__count") or 0)

    # Global YTD numerator (HEADER)
    domain = [("state","=","post"), ("date",">=",b["ytd_start_utc"]), ("date","<=",b["ytd_end_utc"])]
    rows = await client.execute_kw(_HEADER_MODEL, "read_group", args=[domain, ["amount"], []], kwargs={"lazy": False})
    row = rows[0] if rows else {}
    ytd_num = float(row.get("amount") or 0.0); ytd_num_ct = int(row.get("__count") or 0)

    # Global YTD denominator (rs.installment)
    domain = [("state","=","post"), ("date",">=",b["ytd_start_iso"]), ("date","<=",b["ytd_end_iso"])]
    rows = await client.execute_kw(_INST_MODEL, "read_group", args=[domain, ["amount"], []], kwargs={"lazy": False})
    row = rows[0] if rows else {}
    ytd_den = float(row.get("amount") or 0.0); ytd_den_ct = int(row.get("__count") or 0)

    total_ms = int((time.monotonic() - t0_all) * 1000)
    print(f"     MTD: num={mtd_num:,.2f} EGP ({mtd_num_ct:,} recs)  "
          f"den={mtd_den:,.2f} EGP ({mtd_den_ct:,} recs)  [{total_ms}ms]")
    print(f"     YTD: num={ytd_num:,.2f} EGP ({ytd_num_ct:,} recs)  "
          f"den={ytd_den:,.2f} EGP ({ytd_den_ct:,} recs)")

    return {
        "mtd_num": mtd_num, "mtd_num_ct": mtd_num_ct,
        "mtd_den": mtd_den, "mtd_den_ct": mtd_den_ct,
        "ytd_num": ytd_num, "ytd_num_ct": ytd_num_ct,
        "ytd_den": ytd_den, "ytd_den_ct": ytd_den_ct,
        "elapsed_ms": total_ms,
    }


# ── Output helpers ────────────────────────────────────────────────────────────

def _compute_rate_str(numerator: float, denominator: float) -> str:
    if denominator == 0.0:
        return "N/A"
    return f"{numerator / denominator * 100:.2f}%"


def _print_table(
    b: dict,
    results: dict,
    kpi4_globals: dict,
    null_counts: dict,
    branch: str,
) -> None:
    print()
    print(_SEP)
    print(f"KPI 5b — COLLECTION RATE PER PROJECT (Branch {branch})")
    print(_SEP)

    c_proj   = 14
    c_period =  5
    c_num    = 22
    c_den    = 22
    c_rate   = 10
    c_nr     = 10
    c_dr     = 10

    hdr = (
        f"  {'Project':<{c_proj}}  {'Per':<{c_period}}  "
        f"{'Numerator (EGP)':>{c_num}}  {'Denominator (EGP)':>{c_den}}  "
        f"{'Rate %':>{c_rate}}  {'Num Recs':>{c_nr}}  {'Den Recs':>{c_dr}}"
    )
    sep_line = (
        f"  {'-'*c_proj}  {'-'*c_period}  "
        f"{'-'*c_num}  {'-'*c_den}  "
        f"{'-'*c_rate}  {'-'*c_nr}  {'-'*c_dr}"
    )

    for period_label in ["MTD", "YTD"]:
        print()
        print(f"  ── {period_label} ──────────────────────────────────────────────")
        print(hdr)
        print(sep_line)

        total_num = 0.0; total_den = 0.0
        total_nr  = 0;   total_dr  = 0

        for pid in sorted(_PROJECT_NAMES.keys()):
            r   = results.get((pid, period_label), {})
            num = r.get("num", {})
            den = r.get("den", {})
            n_amt  = num.get("amount", 0.0)
            d_amt  = den.get("amount", 0.0)
            n_recs = num.get("record_count", 0)
            d_recs = den.get("record_count", 0)
            rate   = _compute_rate_str(n_amt, d_amt)
            total_num += n_amt; total_den += d_amt
            total_nr  += n_recs; total_dr += d_recs
            print(
                f"  {_PROJECT_NAMES[pid]:<{c_proj}}  {period_label:<{c_period}}  "
                f"{n_amt:>{c_num},.2f}  {d_amt:>{c_den},.2f}  "
                f"{rate:>{c_rate}}  {n_recs:>{c_nr},}  {d_recs:>{c_dr},}"
            )

        print(sep_line)
        tot_rate = _compute_rate_str(total_num, total_den)
        print(
            f"  {'TOTAL (3 proj)':<{c_proj}}  {period_label:<{c_period}}  "
            f"{total_num:>{c_num},.2f}  {total_den:>{c_den},.2f}  "
            f"{tot_rate:>{c_rate}}  {total_nr:>{c_nr},}  {total_dr:>{c_dr},}"
        )

        prefix       = "mtd" if period_label == "MTD" else "ytd"
        g_num        = kpi4_globals[f"{prefix}_num"]
        g_num_ct     = kpi4_globals[f"{prefix}_num_ct"]
        g_den        = kpi4_globals[f"{prefix}_den"]
        g_den_ct     = kpi4_globals[f"{prefix}_den_ct"]
        g_rate       = _compute_rate_str(g_num, g_den)

        print(
            f"  {'KPI4 Global':<{c_proj}}  {period_label:<{c_period}}  "
            f"{g_num:>{c_num},.2f}  {g_den:>{c_den},.2f}  "
            f"{g_rate:>{c_rate}}  {g_num_ct:>{c_nr},}  {g_den_ct:>{c_dr},}"
        )
        print(sep_line)

        # Cross-check deltas
        delta_num = g_num - total_num
        delta_den = g_den - total_den
        null_info = null_counts.get(prefix, {})
        null_amt  = null_info.get("amount", 0.0)
        null_ct   = null_info.get("count", 0)

        if abs(delta_num) < 0.01 and abs(delta_den) < 0.01:
            print(f"  {_PASS} {period_label} cross-check: sum-of-projects matches global KPI 4 exactly.")
        else:
            num_flag = _PASS if abs(delta_num) < 0.01 else (_FLAG if abs(delta_num) > 1.0 else _INFO)
            den_flag = _PASS if abs(delta_den) < 0.01 else _WARN
            print(f"  {num_flag} {period_label} numerator delta  : {delta_num:+,.2f} EGP")
            print(f"  {den_flag} {period_label} denominator delta : {delta_den:+,.2f} EGP")
            if null_ct > 0:
                print(f"         Null-project installments in {period_label}: {null_ct:,} recs / "
                      f"{null_amt:,.2f} EGP")
                if abs(abs(delta_den) - null_amt) < 1.0:
                    print(f"         {_PASS} Delta matches null-project amount — fully explained.")
                else:
                    print(f"         {_FLAG} Delta does NOT fully match null-project amount "
                          f"— investigate before D1.")

    print()
    print(_SEP2)
    print("NULL PROJECT_ID SUMMARY (rs.installment, state=post)")
    print(_SEP2)
    print(f"  All-time null records : {null_counts['all_time']['count']:,}")
    print(f"  MTD null records      : {null_counts['mtd']['count']:,}  /  "
          f"{null_counts['mtd'].get('amount', 0.0):,.2f} EGP")
    print(f"  YTD null records      : {null_counts['ytd']['count']:,}  /  "
          f"{null_counts['ytd'].get('amount', 0.0):,.2f} EGP")
    if null_counts['all_time']['count'] == 0:
        print(f"  {_PASS} No null-project records — cross-check deltas must be zero.")
    else:
        print(f"  {_WARN} Null-project records exist (likely penalty installments per Risk 1).")
        print(f"         D1 service: KPI 5b totals will differ from KPI 4 by this amount.")
    print()


def _print_cross_check_guide(b: dict, results: dict, branch: str) -> None:
    print(_SEP)
    print("MANUAL CROSS-CHECK GUIDE — Checkpoint 1")
    print(_SEP)
    print()
    print("  Khaled: verify 3 YTD denominator totals in Odoo (one per project).")
    print("  Identity-equal at 2-decimal precision required before D1 proceeds.")
    print()

    for pid in sorted(_PROJECT_NAMES.keys()):
        proj_name = _PROJECT_NAMES[pid]
        r   = results.get((pid, "YTD"), {})
        den = r.get("den", {})
        num = r.get("num", {})

        print(f"  {'─'*70}")
        print(f"  YTD Denominator — {proj_name} (project_id={pid})")
        print(f"  {'─'*70}")
        print(f"  Open   : Odoo → Collections Mgmt → All Installments")
        print(f"  Filters: State = Posted")
        print(f"           Project = {proj_name}")
        print(f"           Date >= {b['ytd_start_iso']}  (Jan 1 calendar year — Decision 6.2)")
        print(f"           Date <= {b['ytd_end_iso']}   (today)")
        print(f"  Action : Sum the 'Amount' column (NOT 'Due Amount' — Decision 6.1).")
        print(f"  D0 says: {den.get('amount', 0.0):>26,.2f} EGP  "
              f"({den.get('record_count', 0):,} records)")
        print()

        print(f"  YTD Numerator — {proj_name}")
        if branch == "A":
            print(f"  Open   : Odoo → RS Accounting → Payment Installments")
            print(f"  Filters: State = Posted")
            print(f"           Project = {proj_name}")
            print(f"           Date >= {b['ytd_start_iso']}  (Egypt local)")
            print(f"           Date <= {b['ytd_end_iso']}")
            print(f"  UTC domain used: [{b['ytd_start_utc']}  →  {b['ytd_end_utc']}]")
        else:
            print(f"  Branch B: queried via {_LINE_MODEL}.")
            print(f"  Installment IDs for this project were pre-fetched and used as IN filter.")
            print(f"  Domain: installment_id in [project {pid} IDs],")
            print(f"          payment_id.state='post',")
            print(f"          payment_id.date >= {b['ytd_start_utc']},")
            print(f"          payment_id.date <= {b['ytd_end_utc']}")
        print(f"  D0 says: {num.get('amount', 0.0):>26,.2f} EGP  "
              f"({num.get('record_count', 0):,} records)")
        print()

    print(f"  {'─'*70}")
    print(f"  Cross-check: sum of 3 YTD denominators vs KPI 4 global YTD")
    print(f"  {'─'*70}")
    ytd_den_total = sum(
        results.get((pid, "YTD"), {}).get("den", {}).get("amount", 0.0)
        for pid in _PROJECT_NAMES
    )
    print(f"  Sum of project YTD denominators: {ytd_den_total:>20,.2f} EGP")
    print(f"  KPI 4 global YTD denominator   : see global row in table above")
    print(f"  Delta should equal null-project YTD amount (if any).")
    print()
    print(f"  If any denominator disagrees with Odoo: STOP and report the delta.")
    print(f"  Do NOT proceed to D1 until all 3 YTD denominator totals are identity-equal.")
    print(_SEP)


def _append_tsv(
    run_at: str,
    b: dict,
    results: dict,
    branch: str,
    kpi4_globals: dict,
    total_rpc_ms: int,
) -> None:
    """Append 6 TSV rows to logs/kpi5b_discovery.log (3 projects × 2 periods)."""
    os.makedirs("logs", exist_ok=True)
    log_exists = os.path.isfile(_LOG_FILE)

    with open(_LOG_FILE, "a", encoding="utf-8") as f:
        if not log_exists:
            f.write(
                "run_at\tbranch\tproject_id\tproject_name\tperiod\t"
                "period_start\tperiod_end\t"
                "num_egp\tnum_recs\tden_egp\tden_recs\trate\t"
                "global_num_egp\tglobal_den_egp\tdelta_num\tdelta_den\t"
                "total_rpc_ms\n"
            )
        for period_label in ["MTD", "YTD"]:
            prefix = "mtd" if period_label == "MTD" else "ytd"
            period_start = b[f"{prefix}_start_iso"]
            period_end   = b[f"{prefix}_end_iso"]
            g_num = kpi4_globals[f"{prefix}_num"]
            g_den = kpi4_globals[f"{prefix}_den"]
            for pid in sorted(_PROJECT_NAMES.keys()):
                r   = results.get((pid, period_label), {})
                num = r.get("num", {})
                den = r.get("den", {})
                n_amt  = num.get("amount", 0.0)
                d_amt  = den.get("amount", 0.0)
                n_recs = num.get("record_count", 0)
                d_recs = den.get("record_count", 0)
                rate   = _compute_rate_str(n_amt, d_amt)

                # Accumulate project totals inline for delta computation
                # (written per-row; global delta only meaningful on TOTAL row — logged separately)
                f.write(
                    f"{run_at}\t{branch}\t{pid}\t{_PROJECT_NAMES[pid]}\t{period_label}\t"
                    f"{period_start}\t{period_end}\t"
                    f"{n_amt:.2f}\t{n_recs}\t{d_amt:.2f}\t{d_recs}\t{rate}\t"
                    f"{g_num:.2f}\t{g_den:.2f}\t"
                    f"\t\t"  # delta_num/delta_den on TOTAL row only — left blank per-project
                    f"{total_rpc_ms}\n"
                )

    print(f"\n{_INFO} 6 TSV rows appended to {_LOG_FILE} (3 projects × 2 periods)")


# ── Main ──────────────────────────────────────────────────────────────────────

async def run() -> None:
    today  = date.today()
    run_at = datetime.now(timezone.utc).isoformat()
    b      = _compute_period_bounds(today)

    print(_SEP)
    print("KPI 5b — Collection Rate per Project: Architecture Discovery (D0)")
    print(f"Run timestamp : {run_at}")
    print(f"Today (local) : {today}")
    print(_SEP)
    print()
    print("  Period bounds:")
    print(f"    MTD : {b['mtd_start']}  →  {b['mtd_end']}")
    print(f"    YTD : {b['ytd_start']}  →  {b['ytd_end']}  (Jan 1 calendar year, Decision 6.2)")
    print()
    print("  Numerator UTC boundaries (Africa/Cairo → UTC, Decision 5.9):")
    print(f"    MTD : {b['mtd_start_utc']}  →  {b['mtd_end_utc']}")
    print(f"    YTD : {b['ytd_start_utc']}  →  {b['ytd_end_utc']}")
    print()
    print("  Denominator ISO date bounds (rs.installment.date is a plain date field):")
    print(f"    MTD : {b['mtd_start_iso']}  →  {b['mtd_end_iso']}")
    print(f"    YTD : {b['ytd_start_iso']}  →  {b['ytd_end_iso']}")
    print()

    t_global_start = time.monotonic()

    async with OdooClient() as client:

        # ── Step 1: Branch determination (1 RPC) ──────────────────────────────
        print(_SEP2)
        print("STEP 1 — Branch determination")
        print(_SEP2)
        is_branch_a = await _check_header_project_id_field(client)
        branch = "A" if is_branch_a else "B"

        # ── Step 2: Null project_id analysis (3 RPCs) ─────────────────────────
        print()
        print(_SEP2)
        print("STEP 2 — Null project_id analysis")
        print(_SEP2)
        null_counts = await _query_nullproject_counts(client, b)

        # ── Step 3: Branch B pre-fetch + sanity check ─────────────────────────
        installment_ids_by_project: dict[int, list[int]] = {}
        if not is_branch_a:
            print()
            print(_SEP2)
            print("STEP 3 — Branch B: pre-fetch installment IDs per project")
            print(_SEP2)
            for pid in sorted(_PROJECT_NAMES.keys()):
                print(f"\n{_INFO} Fetching IDs for project {pid} ({_PROJECT_NAMES[pid]})...")
                installment_ids_by_project[pid] = await _fetch_installment_ids_for_project(
                    client, pid
                )
            await _check_multiproject_headers(client)

        # ── Step 4: Per-project numerator + denominator (12 RPCs) ────────────
        print()
        print(_SEP2)
        print("STEP 4 — Per-project collection rate queries (3 projects × 2 periods)")
        print(_SEP2)

        results: dict = {}
        periods = [
            ("MTD", b["mtd_start_utc"], b["mtd_end_utc"], b["mtd_start_iso"], b["mtd_end_iso"]),
            ("YTD", b["ytd_start_utc"], b["ytd_end_utc"], b["ytd_start_iso"], b["ytd_end_iso"]),
        ]

        for period_label, utc_start, utc_end, iso_start, iso_end in periods:
            print(f"\n  ── {period_label} ──────────────────────────────────────────────────")
            for pid in sorted(_PROJECT_NAMES.keys()):
                proj_name = _PROJECT_NAMES[pid]
                print(f"\n{_INFO}   {proj_name} (id={pid}) — {period_label}")

                if is_branch_a:
                    num = await _query_numerator_branch_a(client, pid, utc_start, utc_end)
                else:
                    ids = installment_ids_by_project.get(pid, [])
                    num = await _query_numerator_branch_b(client, pid, ids, utc_start, utc_end)

                den = await _query_denominator_per_project(client, pid, iso_start, iso_end)
                rate = _compute_rate_str(num["amount"], den["amount"])
                results[(pid, period_label)] = {"num": num, "den": den}

                print(f"       Num [{num['elapsed_ms']}ms]: "
                      f"{num['amount']:>22,.2f} EGP  ({num['record_count']:,} records)")
                print(f"       Den [{den['elapsed_ms']}ms]: "
                      f"{den['amount']:>22,.2f} EGP  ({den['record_count']:,} records)")
                print(f"       Rate: {rate}")

        # ── Step 5: Global KPI 4 cross-check (4 RPCs) ─────────────────────────
        print()
        print(_SEP2)
        print("STEP 5 — Global KPI 4 cross-check")
        print(_SEP2)
        kpi4_globals = await _query_kpi4_global_totals(client, b)

    total_rpc_ms = int((time.monotonic() - t_global_start) * 1000)
    print(f"\n{_INFO} Total elapsed (all RPCs): {total_rpc_ms} ms")

    # ── Output ────────────────────────────────────────────────────────────────
    _print_table(b, results, kpi4_globals, null_counts, branch)
    _print_cross_check_guide(b, results, branch)
    _append_tsv(run_at, b, results, branch, kpi4_globals, total_rpc_ms)


if __name__ == "__main__":
    asyncio.run(run())
