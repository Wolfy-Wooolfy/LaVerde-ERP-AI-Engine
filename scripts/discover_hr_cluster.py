"""
Read-only pre-implementation discovery for the HR app cluster.

Confirms model existence, record counts, field metadata, and data-quality
patterns for all HR-related models before any module code is written.

This script:
  - Calls ONLY read methods: search_count, search_read, read_group, fields_get.
  - Writes nothing to Odoo.
  - Costs $0 in AI.
  - Prints no PII (IDs, states, dates, counts only — no names, emails, wages,
    phone numbers, or private fields).
  - Attendance/Payroll results are labelled PROVISIONAL (test data — real
    entry begins June 2026).
  - Appends one TSV row per check to logs/hr_discovery.log.
  - Exits 0 on completion regardless of findings.

Usage:
    python scripts/discover_hr_cluster.py
"""

import asyncio
import io
import os
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any

from backend.core.exceptions import OdooQueryError
from backend.shared.odoo.client import OdooClient

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Visual constants ───────────────────────────────────────────────────────────

_SEP  = "═" * 80
_SEP2 = "─" * 80

_INFO          = "[INFO]"
_PASS          = "[PASS]"
_FLAG          = "[FLAG]"
_NOT_INSTALLED = "[NOT_INSTALLED]"

# ── RPC counter ────────────────────────────────────────────────────────────────

_rpc_count: int = 0


async def _rpc(
    client: OdooClient,
    model: str,
    method: str,
    args: list | None = None,
    kwargs: dict | None = None,
) -> Any:
    global _rpc_count
    _rpc_count += 1
    return await client.execute_kw(
        model, method, args=args or [], kwargs=kwargs or {}
    )


# ── TSV log ────────────────────────────────────────────────────────────────────

_LOG_FILE = "logs/hr_discovery.log"
_TSV_COLS = "run_at\tsection\tcheck_name\tmarker\tvalue\tnote\n"
_run_at   = ""
_log_header_written = False


def _tsv(section: str, check_name: str, marker: str, value: str, note: str = "") -> None:
    global _log_header_written
    os.makedirs("logs", exist_ok=True)
    needs_header = not os.path.isfile(_LOG_FILE) or not _log_header_written
    with open(_LOG_FILE, "a", encoding="utf-8") as f:
        if needs_header:
            f.write(_TSV_COLS)
            _log_header_written = True
        f.write(f"{_run_at}\t{section}\t{check_name}\t{marker}\t{value}\t{note}\n")


# ── Model probe helper ─────────────────────────────────────────────────────────

async def _probe_model(
    client: OdooClient,
    model: str,
    section: str = "S1",
) -> tuple[dict | None, int | None]:
    """
    Confirm model existence via fields_get, then get total record count.
    Returns (fields_meta, count) on success, (None, None) if not installed.
    Catches OdooQueryError only — auth/network errors propagate normally.
    """
    try:
        fields_meta: dict = await _rpc(
            client, model, "fields_get",
            kwargs={"attributes": ["string", "type", "relation"]},
        )
        count: int = await _rpc(client, model, "search_count", args=[[]])
        _tsv(section, f"{model}.count", _PASS, str(count), "model installed")
        return fields_meta, count
    except OdooQueryError as exc:
        _tsv(section, f"{model}.count", _NOT_INSTALLED, "0", str(exc)[:120])
        return None, None


# ── Main ───────────────────────────────────────────────────────────────────────

async def run() -> None:
    global _run_at

    today   = date.today()
    _run_at = datetime.now(timezone.utc).isoformat()

    print(_SEP)
    print("HR Cluster Discovery  — Read-Only Pre-Implementation")
    print(f"Run timestamp  : {_run_at}")
    print(f"Today          : {today}")
    print(_SEP)

    # Standard models for Section 1
    S1_MODELS = [
        "hr.employee",
        "hr.contract",
        "hr.attendance",
        "hr.leave",
        "hr.leave.allocation",
        "hr.payslip",
        "hr.payslip.run",
        "hr.applicant",
        "hr.job",
        "hr.department",
    ]

    async with OdooClient() as client:

        # ══════════════════════════════════════════════════════════════════════
        # SECTION 1 — Model existence + record counts
        # ══════════════════════════════════════════════════════════════════════
        print()
        print(_SEP2)
        print("[S1] Model existence + record counts")
        print(_SEP2)
        print(f"    {'Model':<32} {'Status':<18} Count")
        print(f"    {'-'*32} {'-'*18} -----")

        fields_cache: dict[str, dict | None] = {}
        counts: dict[str, int | None] = {}

        for model in S1_MODELS:
            fm, cnt = await _probe_model(client, model)
            fields_cache[model] = fm
            counts[model] = cnt
            mark    = _PASS if fm is not None else _NOT_INSTALLED
            cnt_str = f"{cnt:,}" if cnt is not None else "N/A"
            print(f"    {model:<32} {mark:<18} {cnt_str}")

        # ══════════════════════════════════════════════════════════════════════
        # SECTION 2 — Custom model discovery (Overtime, Business Missions)
        # ══════════════════════════════════════════════════════════════════════
        print()
        print(_SEP2)
        print("[S2] Custom model discovery — Overtime + Business Missions")
        print("     Probing ir.model for overtime / mission keywords and all hr.* models")
        print(_SEP2)

        ir_queries: list[tuple[str, list]] = [
            ("model ilike 'overtime'",  [("model", "ilike", "overtime")]),
            ("model ilike 'mission'",   [("model", "ilike", "mission")]),
            ("name  ilike 'overtime'",  [("name",  "ilike", "overtime")]),
            ("name  ilike 'mission'",   [("name",  "ilike", "mission")]),
            ("model =like  'hr.%'",     [("model", "=like", "hr.%")]),
        ]

        discovered: dict[str, dict] = {}  # technical name → {name, state}

        for label, domain in ir_queries:
            rows: list[dict] = await _rpc(
                client, "ir.model", "search_read",
                args=[domain],
                kwargs={"fields": ["model", "name", "state"]},
            )
            print(f"    [{label}]  → {len(rows)} result(s)")
            for r in rows:
                tech = r.get("model", "")
                if tech and tech not in discovered:
                    discovered[tech] = {
                        "name":  r.get("name", ""),
                        "state": r.get("state", ""),
                    }

        # Custom (state=manual) models not in the standard S1 set
        custom_models = {
            m: info for m, info in discovered.items()
            if info.get("state") == "manual" and m not in S1_MODELS
        }

        print()
        print(f"    Custom (state=manual) models outside the standard hr.* set: {len(custom_models)}")

        # Keyword-specific candidate sets (regardless of state — they may be base-module models)
        overtime_models = sorted({
            m for m, info in discovered.items()
            if "overtime" in m.lower() or "overtime" in info.get("name", "").lower()
        })
        mission_models = sorted({
            m for m, info in discovered.items()
            if "mission" in m.lower() or "mission" in info.get("name", "").lower()
        })

        # Print full custom list with counts
        if custom_models:
            print()
            print(f"    {'Technical model':<45} {'Label':<35} {'State':<8} Count")
            print(f"    {'-'*45} {'-'*35} {'-'*8} -----")
            for tech_model, info in sorted(custom_models.items()):
                try:
                    cnt = await _rpc(client, tech_model, "search_count", args=[[]])
                    cnt_str = f"{cnt:,}"
                    _tsv("S2", f"{tech_model}.count", _PASS, str(cnt), info.get("name", ""))
                except OdooQueryError as exc:
                    cnt_str = "ERROR"
                    _tsv("S2", f"{tech_model}.count", _FLAG, "0", str(exc)[:80])
                print(
                    f"    {tech_model:<45} {info.get('name',''):<35} "
                    f"{info.get('state',''):<8} {cnt_str}"
                )

        # fields_get for Overtime and Business Missions models specifically
        print()
        for target_label, target_models in [
            ("Overtime",          overtime_models),
            ("Business Missions", mission_models),
        ]:
            if not target_models:
                print(f"    {target_label}: no model found matching keyword — FLAG for manual review")
                _tsv("S2", f"{target_label.lower().replace(' ','_')}.model",
                     _FLAG, "NOT_FOUND", "no ir.model match")
                continue

            print(f"    {target_label} model(s) found: {target_models}")
            for m in target_models:
                info = discovered.get(m, {})
                # Record count
                try:
                    cnt = await _rpc(client, m, "search_count", args=[[]])
                    _tsv("S2", f"{m}.count", _PASS, str(cnt), target_label)
                except OdooQueryError as exc:
                    cnt = None
                    _tsv("S2", f"{m}.count", _FLAG, "0", str(exc)[:80])

                cnt_str = f"{cnt:,}" if cnt is not None else "ERROR"
                print(f"      {m}  ({info.get('name','')})  state={info.get('state','')}  count={cnt_str}")

                # fields_get
                try:
                    fm: dict = await _rpc(
                        client, m, "fields_get",
                        kwargs={"attributes": ["string", "type", "relation"]},
                    )
                    print(f"      Fields ({len(fm)} total):")
                    print(f"        {'Field name':<42} {'type':<14} relation")
                    print(f"        {'-'*42} {'-'*14} --------")
                    for fn, fi in sorted(fm.items()):
                        rel = fi.get("relation", "")
                        print(f"        {fn:<42} {fi.get('type',''):<14} {rel}")
                    _tsv("S2", f"{m}.fields_get", _PASS, str(len(fm)), f"{target_label} field count")
                except OdooQueryError as exc:
                    print(f"      fields_get ERROR: {exc}")
                    _tsv("S2", f"{m}.fields_get", _FLAG, "0", str(exc)[:120])

        # ══════════════════════════════════════════════════════════════════════
        # SECTION 3 — Employees data quality  (NON-PII only)
        # ══════════════════════════════════════════════════════════════════════
        print()
        print(_SEP2)
        print("[S3] Employees data quality  (NON-PII: IDs, states, counts, dates only)")
        print(_SEP2)

        EMP_MODEL = "hr.employee"
        emp_meta  = fields_cache.get(EMP_MODEL)

        if emp_meta is None:
            print(f"    SKIP — {EMP_MODEL} not installed")
            _tsv("S3", "skip", _NOT_INSTALLED, "0", "hr.employee not installed")
        else:
            # ── S3.0 Discover the hire/start date field ────────────────────
            print("    [S3.0] Discovering date fields on hr.employee ...")
            DATE_KWS = ("hire", "contract", "join", "start", "trial", "initial")
            date_candidates = {
                fn: fi for fn, fi in emp_meta.items()
                if fi.get("type") in ("date", "datetime")
                and any(kw in fn.lower() for kw in DATE_KWS)
            }
            print(f"    Candidate date fields: {list(date_candidates.keys())}")

            if "hire_date" in date_candidates:
                date_field: str | None = "hire_date"
            elif "first_contract_date" in date_candidates:
                date_field = "first_contract_date"
            elif date_candidates:
                date_field = next(iter(sorted(date_candidates)))
            else:
                date_field = None

            print(f"    Selected date field : {date_field!r}  (used for min/max below)")
            _tsv("S3", "hire_date_field", _INFO, str(date_field),
                 f"candidates={list(date_candidates.keys())}")

            # ── S3.1 Active / inactive counts ─────────────────────────────
            print()
            print("    [S3.1] Active vs inactive")
            active_cnt   = await _rpc(client, EMP_MODEL, "search_count",
                                      args=[[("active", "=", True)]])
            inactive_cnt = await _rpc(client, EMP_MODEL, "search_count",
                                      args=[[("active", "=", False)]])
            print(f"    active=True   : {active_cnt:>6,}")
            print(f"    active=False  : {inactive_cnt:>6,}")
            _tsv("S3", "employee.active_true",  _PASS, str(active_cnt),  "")
            _tsv("S3", "employee.active_false", _PASS, str(inactive_cnt), "")

            # ── S3.2 Structural gaps ───────────────────────────────────────
            # Include inactive employees in gap checks (active domain bypass)
            print()
            print("    [S3.2] Structural gaps (active + inactive employees)")
            no_dept    = await _rpc(client, EMP_MODEL, "search_count",
                                    args=[[("active", "in", [True, False]),
                                           ("department_id", "=", False)]])
            no_job     = await _rpc(client, EMP_MODEL, "search_count",
                                    args=[[("active", "in", [True, False]),
                                           ("job_id", "=", False)]])
            no_manager = await _rpc(client, EMP_MODEL, "search_count",
                                    args=[[("active", "in", [True, False]),
                                           ("parent_id", "=", False)]])
            print(f"    no department : {no_dept:>6,}")
            print(f"    no job title  : {no_job:>6,}")
            print(f"    no manager    : {no_manager:>6,}")
            _tsv("S3", "employee.no_department", _INFO, str(no_dept),    "")
            _tsv("S3", "employee.no_job",        _INFO, str(no_job),     "")
            _tsv("S3", "employee.no_manager",    _INFO, str(no_manager), "")

            # ── S3.3 read_group by department (groupby field only) ─────────
            print()
            print("    [S3.3] Distribution by department (active employees, __count only)")
            dept_rows: list[dict] = await _rpc(
                client, EMP_MODEL, "read_group",
                args=[[("active", "=", True)], ["department_id"], ["department_id"]],
                kwargs={"lazy": False},
            )
            print(f"    {'Department':<50} Count")
            print(f"    {'-'*50} -----")
            for row in dept_rows:
                dept = row.get("department_id")
                label = dept[1] if isinstance(dept, (list, tuple)) and len(dept) > 1 else str(dept)
                print(f"    {label:<50} {row.get('__count', 0):>5,}")
            _tsv("S3", "employee.by_department", _PASS, str(len(dept_rows)),
                 f"{len(dept_rows)} department group(s)")

            # ── S3.4 read_group by job title (groupby field only) ──────────
            print()
            print("    [S3.4] Distribution by job title (active employees, __count only)")
            job_rows: list[dict] = await _rpc(
                client, EMP_MODEL, "read_group",
                args=[[("active", "=", True)], ["job_id"], ["job_id"]],
                kwargs={"lazy": False},
            )
            print(f"    {'Job title':<50} Count")
            print(f"    {'-'*50} -----")
            for row in sorted(job_rows, key=lambda r: r.get("__count", 0), reverse=True):
                job = row.get("job_id")
                label = job[1] if isinstance(job, (list, tuple)) and len(job) > 1 else str(job)
                print(f"    {label:<50} {row.get('__count', 0):>5,}")
            _tsv("S3", "employee.by_job", _PASS, str(len(job_rows)),
                 f"{len(job_rows)} job group(s)")

            # ── S3.5 Date range for discovered date field ──────────────────
            if date_field:
                print()
                print(f"    [S3.5] {date_field} range (active employees, field != False)")
                domain_has_date = [("active", "=", True), (date_field, "!=", False)]
                min_rows = await _rpc(
                    client, EMP_MODEL, "search_read",
                    args=[domain_has_date],
                    kwargs={"fields": [date_field], "limit": 1, "order": f"{date_field} asc"},
                )
                max_rows = await _rpc(
                    client, EMP_MODEL, "search_read",
                    args=[domain_has_date],
                    kwargs={"fields": [date_field], "limit": 1, "order": f"{date_field} desc"},
                )
                min_val = min_rows[0].get(date_field) if min_rows else None
                max_val = max_rows[0].get(date_field) if max_rows else None
                print(f"    {date_field} earliest : {min_val}")
                print(f"    {date_field} latest   : {max_val}")
                _tsv("S3", f"{date_field}.min", _PASS, str(min_val), "active employees")
                _tsv("S3", f"{date_field}.max", _PASS, str(max_val), "active employees")

            # create_date range (always exists — system field)
            if "create_date" in emp_meta:
                print()
                print("    [S3.6] create_date range (active employees)")
                min_cre = await _rpc(
                    client, EMP_MODEL, "search_read",
                    args=[[("active", "=", True)]],
                    kwargs={"fields": ["create_date"], "limit": 1, "order": "create_date asc"},
                )
                max_cre = await _rpc(
                    client, EMP_MODEL, "search_read",
                    args=[[("active", "=", True)]],
                    kwargs={"fields": ["create_date"], "limit": 1, "order": "create_date desc"},
                )
                min_cre_val = min_cre[0].get("create_date") if min_cre else None
                max_cre_val = max_cre[0].get("create_date") if max_cre else None
                print(f"    create_date earliest : {min_cre_val}")
                print(f"    create_date latest   : {max_cre_val}")
                _tsv("S3", "create_date.min", _PASS, str(min_cre_val), "active employees")
                _tsv("S3", "create_date.max", _PASS, str(max_cre_val), "active employees")

        # ══════════════════════════════════════════════════════════════════════
        # SECTION 4 — Contracts
        # ══════════════════════════════════════════════════════════════════════
        print()
        print(_SEP2)
        print("[S4] Contracts")
        print(_SEP2)

        CON_MODEL = "hr.contract"
        con_meta  = fields_cache.get(CON_MODEL)

        if con_meta is None:
            print(f"    SKIP — {CON_MODEL} not installed")
            _tsv("S4", "skip", _NOT_INSTALLED, "0", "hr.contract not installed")
        else:
            # ── S4.1 Discover actual state values FIRST ────────────────────
            print("    [S4.1] Discover actual contract state values")
            state_rows: list[dict] = await _rpc(
                client, CON_MODEL, "read_group",
                args=[[], ["state"], ["state"]],
                kwargs={"lazy": False},
            )
            print(f"    {'State key':<20} Count")
            print(f"    {'-'*20} -----")

            running_key: str | None = None
            state_counts: dict[str, int] = {}
            for row in state_rows:
                sk  = row.get("state") or "(false)"
                cnt = int(row.get("__count") or 0)
                state_counts[sk] = cnt
                print(f"    {sk!r:<20} {cnt:>6,}")
                _tsv("S4", f"contract.state.{sk}", _PASS, str(cnt), "")
                # Standard Odoo hr.contract running state is 'open'; some versions use 'running' or 'in_progress'
                if sk in ("open", "running", "in_progress"):
                    running_key = sk

            if running_key is None:
                # No unambiguous key found — STOP, do not guess.
                print()
                print(f"    {_FLAG} STOP — running state key not identified.")
                print(f"           Expected one of: 'open', 'running', 'in_progress'")
                print(f"           Actual keys found: {list(state_counts.keys())}")
                print(f"           ACTION REQUIRED: Khaled — inspect the state keys above,")
                print(f"           confirm which corresponds to 'Running' in the Odoo UI,")
                print(f"           then provide the correct key so S4.3 can proceed.")
                _tsv("S4", "running_state.NOT_IDENTIFIED", _FLAG, "STOP",
                     f"keys_found={list(state_counts.keys())}; S4.3 skipped — manual identification required")
            else:
                print(f"    Running state key confirmed: {running_key!r}")
                _tsv("S4", "running_state.identified", _PASS, str(running_key), "")

            # ── S4.2 Field type metadata (no wage) ────────────────────────
            print()
            print("    [S4.2] Field metadata: date_start, date_end, state")
            contract_fields: dict = await _rpc(
                client, CON_MODEL, "fields_get",
                args=[["date_start", "date_end", "state"]],
                kwargs={"attributes": ["string", "type"]},
            )
            print(f"    {'Field':<15} {'type':<14} Label")
            print(f"    {'-'*15} {'-'*14} -----")
            for fn, fi in contract_fields.items():
                print(f"    {fn:<15} {fi.get('type',''):<14} {fi.get('string','')}")
                _tsv("S4", f"contract.field.{fn}", _PASS,
                     fi.get("type", ""), fi.get("string", ""))

            # ── S4.3 Contracts expiring in next 60 days ───────────────────
            print()
            today_str  = today.isoformat()
            cutoff_str = (today + timedelta(days=60)).isoformat()
            print(f"    [S4.3] Contracts expiring ≤60 days (state={running_key!r}, "
                  f"date_end in [{today_str} … {cutoff_str}])")
            if running_key:
                expiring = await _rpc(
                    client, CON_MODEL, "search_count",
                    args=[[
                        ("state", "=", running_key),
                        ("date_end", ">=", today_str),
                        ("date_end", "<=", cutoff_str),
                    ]],
                )
                marker = _FLAG if expiring > 0 else _PASS
                print(f"    Expiring contracts : {expiring:,}  {marker}")
                _tsv("S4", "contract.expiring_60d", marker, str(expiring),
                     f"state={running_key!r}, date_end in [{today_str}, {cutoff_str}]")
            else:
                print(f"    SKIP — no running state key identified")

            # ── S4.4 Contracts with no end date ───────────────────────────
            print()
            print("    [S4.4] Contracts with date_end = False")
            no_end = await _rpc(
                client, CON_MODEL, "search_count",
                args=[[("date_end", "=", False)]],
            )
            print(f"    No end date : {no_end:,}")
            _tsv("S4", "contract.no_end_date", _INFO, str(no_end), "")

        # ══════════════════════════════════════════════════════════════════════
        # SECTION 5 — Attendance / Payroll  (PROVISIONAL — test data)
        # ══════════════════════════════════════════════════════════════════════
        print()
        print(_SEP2)
        print("[S5] Attendance / Payroll")
        print("     !! PROVISIONAL — test data; real HR entry begins June 2026 !!")
        print("     !! Negative extra-hours values are test-data artifacts, not real anomalies !!")
        print(_SEP2)

        ATT_MODEL = "hr.attendance"
        att_meta  = fields_cache.get(ATT_MODEL)

        if att_meta is None:
            print(f"    SKIP — {ATT_MODEL} not installed")
            _tsv("S5", "attendance.skip", _NOT_INSTALLED, "0", "hr.attendance not installed")
        else:
            att_count = counts.get(ATT_MODEL, 0)
            print(f"    [S5.1] hr.attendance total records: {att_count:,}  (test data)")
            _tsv("S5", "attendance.total", _INFO, str(att_count),
                 "test data — real entry begins Jun 2026")

            # Discover extra/overtime fields; exclude relation types (many2one etc. can
            # pull related-record display names into the search_read result).
            _RELATION_TTYPES = {"many2one", "one2many", "many2many"}
            _extra_all = [
                (fn, att_meta[fn].get("type", ""))
                for fn in sorted(att_meta)
                if "extra" in fn.lower() or "overtime" in fn.lower()
            ]
            extra_fields      = [fn for fn, tt in _extra_all if tt not in _RELATION_TTYPES]
            extra_fields_excl = [(fn, tt) for fn, tt in _extra_all if tt in _RELATION_TTYPES]

            print(f"    Extra/overtime fields (safe, non-relation): {extra_fields}")
            if extra_fields_excl:
                print(f"    Extra/overtime fields EXCLUDED (relation type — could pull names): {extra_fields_excl}")
                _tsv("S5", "attendance.extra_fields_excluded", _FLAG, str(extra_fields_excl),
                     "relation fields excluded from sample read — name+type logged only")
            _tsv("S5", "attendance.extra_fields_safe", _INFO, str(extra_fields), "")

            # ── S5.2 Monthly buckets ───────────────────────────────────────
            print()
            print("    [S5.2] Monthly attendance buckets (check_in:month, __count only)")
            try:
                month_rows: list[dict] = await _rpc(
                    client, ATT_MODEL, "read_group",
                    args=[[], ["check_in"], ["check_in:month"]],
                    kwargs={"lazy": False},
                )
                print(f"    {'Month':<22} Count")
                print(f"    {'-'*22} -----")
                for row in month_rows:
                    mk  = str(row.get("check_in:month") or row.get("check_in") or "?")
                    cnt = int(row.get("__count") or 0)
                    print(f"    {mk:<22} {cnt:>8,}")
                _tsv("S5", "attendance.by_month", _INFO, str(len(month_rows)),
                     f"{len(month_rows)} month bucket(s) — test data")
            except OdooQueryError as exc:
                print(f"    {_FLAG} Monthly groupby failed: {exc}")
                _tsv("S5", "attendance.by_month", _FLAG, "0", str(exc)[:120])

            # ── S5.3 One sample attendance record (strictly no employee_id) ─
            print()
            print("    [S5.3] One sample attendance record")
            # Base safe fields — NO employee_id or any name field
            _SAFE_BASE = ["id", "check_in", "check_out", "worked_hours"]
            # extra/overtime fields discovered above are structural, not PII
            safe_fields = _SAFE_BASE + extra_fields
            print(f"           Fields requested: {safe_fields}")
            print(f"           employee_id intentionally EXCLUDED (PII guard)")

            sample: list[dict] = await _rpc(
                client, ATT_MODEL, "search_read",
                args=[[]],
                kwargs={"fields": safe_fields, "limit": 1, "order": "id desc"},
            )
            if sample:
                row = sample[0]
                for fn in safe_fields:
                    val = row.get(fn)
                    if val is not None and val is not False:
                        print(f"    {fn:<38} : {val}")
            else:
                print("    (no attendance records found)")
            _tsv("S5", "attendance.sample_read", _PASS, "1",
                 f"fields={safe_fields}; employee_id excluded")

            # ── S5.4 Negative extra-hours hypothesis ──────────────────────
            print()
            print("    [S5.4] Negative extra-hours hypothesis")
            print("    The UI shows 'Worked Extra Hours' values like −15537:16.")
            print("    Hypothesis: the system working schedule defines expected weekly hours.")
            print("    Since test data was loaded but employees did not actually check in for")
            print("    their full scheduled hours, the formula:")
            print("      extra_hours = worked_hours − expected_hours")
            print("    produces a large negative number for every employee.")
            print("    This is a test-data artifact, NOT a real HR anomaly.")
            print("    Verification path: read resource.calendar (working schedule) and compare")
            print("    expected_hours (if present in attendance fields) vs worked_hours on sample.")
            _tsv("S5", "negative_extra_hours.hypothesis", _INFO, "test_data_artifact",
                 "worked_hours < expected_hours because test data has no real check-ins")

        # Payslip counts (reuse Section 1 results)
        print()
        for ps_model in ("hr.payslip", "hr.payslip.run"):
            ps_cnt = counts.get(ps_model)
            if ps_cnt is not None:
                print(f"    {ps_model:<32} count={ps_cnt:,}  (provisional — test data)")
            else:
                print(f"    {ps_model:<32} NOT_INSTALLED")
            _tsv("S5", f"{ps_model}.count",
                 _INFO if ps_cnt is not None else _NOT_INSTALLED,
                 str(ps_cnt) if ps_cnt is not None else "NOT_INSTALLED",
                 "provisional")

        # ══════════════════════════════════════════════════════════════════════
        # SECTION 6 — Findings summary
        # ══════════════════════════════════════════════════════════════════════
        print()
        print(_SEP)
        print("[S6] FINDINGS SUMMARY")
        print(_SEP)

        _MODEL_NOTES: dict[str, tuple[str, str]] = {
            "hr.employee":         ("real",  "Core workforce — confirm count vs UI ~136"),
            "hr.contract":         ("real",  "Core — 124 Running / 12 Expired (per UI)"),
            "hr.attendance":       ("test",  "Test data only — real entry begins Jun 2026"),
            "hr.leave":            ("empty", "Time Off — expected 0 records"),
            "hr.leave.allocation": ("empty", "Leave Allocations — expected 0 records"),
            "hr.payslip":          ("test",  "Payroll — expected 0 (provisional)"),
            "hr.payslip.run":      ("test",  "Payslip Runs — expected 0 (provisional)"),
            "hr.applicant":        ("empty", "Recruitment applications — expected 0"),
            "hr.job":              ("real",  "Job positions — per UI ~84"),
            "hr.department":       ("real",  "Flat hierarchy per UI — all under top mgmt"),
        }

        print()
        print(
            f"  {'Model':<30} {'Installed':^12} {'Count':>10}  "
            f"{'Data type':<8}  Board-relevance note"
        )
        print(
            f"  {'-'*30} {'-'*12} {'-'*10}  "
            f"{'-'*8}  {'-'*40}"
        )
        for model in S1_MODELS:
            fm    = fields_cache.get(model)
            cnt   = counts.get(model)
            inst  = "YES" if fm is not None else "NO"
            cnt_s = f"{cnt:,}" if cnt is not None else "N/A"
            dtype, note = _MODEL_NOTES.get(model, ("?", ""))
            print(f"  {model:<30} {inst:^12} {cnt_s:>10}  {dtype:<8}  {note}")

        print()
        print(f"  Total RPC calls issued: {_rpc_count}")
        _tsv("S6", "total_rpc_count", _INFO, str(_rpc_count), "")

        print()
        print(_SEP)
        print("  Discovery complete. No module code was written.")
        print("  Review output above, then approve docs/HR_CLUSTER_DISCOVERY.md generation.")
        print(_SEP)


if __name__ == "__main__":
    asyncio.run(run())
