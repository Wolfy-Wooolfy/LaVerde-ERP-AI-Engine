"""
scripts/discover_phase_0_5_ui_artifacts.py — Phase 0.5 Discovery: UI Artifacts

Investigates 4 UI artifacts surfaced during the Phase 0 cross-check (2026-05-18):
  A. Pre-existing KPI-named Favorites on the All Installments view (ir.filters)
  B. "Has Checks" and "All Checks Collected" filter/group-by options
  C. "Checks" tab on the installment form view (check_ids many2many relation)
  D. Undocumented field labels (total_due_amount, reservation_id, etc.)

5 objectives → 6 script sections:
  Section 1 — Checks relation + statistical proof of cheques-on-future-installments
  Section 2 — "Has Checks" and "All Checks Collected" (handles 3 artifact types)
  Section 3 — Undocumented fields inventory
  Section 4 — Pre-existing KPI Favorites (ir.filters)
  Section 5 — UI field label verification
  Section 6 — PATH A / B / C recommendation

Hard constraints:
  - READ-ONLY: _assert_read_only() is the first call in main().
  - No PII: no customer names, partner names, or partner IDs in output.
  - No OpenAI calls. AI cost = $0.00.
  - No Phase 1 KPI 7 service code.
  - Tees stdout to scripts/discover_phase_0_5_ui_artifacts_output.txt.

Usage (from project root):
    $env:PYTHONPATH = "."; C:\\Python310\\python.exe scripts/discover_phase_0_5_ui_artifacts.py
"""

import asyncio
import calendar
import io
import sys
import time
from datetime import date, datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient

# Force UTF-8 stdout (Windows consoles default to cp1252).
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Constants ─────────────────────────────────────────────────────────────────

_MODEL        = "rs.installment"
_CHECK_MODEL  = "rs.account.check"   # confirmed many2many target from Phase 1 Discovery
_FILTERS_MODEL = "ir.filters"
_VIEW_MODEL   = "ir.ui.view"
_LA_VERDE_TZ  = ZoneInfo("Africa/Cairo")
_OUTPUT_FILE  = Path(__file__).parent / "discover_phase_0_5_ui_artifacts_output.txt"

_FORBIDDEN_WRITE_METHODS = frozenset({"create", "write", "unlink"})

# Odoo boilerplate field names — skip in all inventory printouts.
_SKIP_FIELDS = frozenset({
    "__last_update", "create_date", "create_uid", "write_date", "write_uid",
    "display_name", "message_follower_ids", "message_ids", "activity_ids",
    "message_needaction", "message_needaction_counter", "message_has_error",
    "message_attachment_count", "message_main_attachment_id",
    "website_message_ids", "message_is_follower",
})

_SEP  = "═" * 78
_SEP2 = "─" * 76
_PASS = "[PASS]"
_FLAG = "[FLAG]"
_INFO = "[INFO]"
_STOP = "[STOP]"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _assert_read_only() -> None:
    violations = ALLOWED_METHODS & _FORBIDDEN_WRITE_METHODS
    if violations:
        raise RuntimeError(
            f"ALLOWED_METHODS contains forbidden write method(s): {sorted(violations)}. "
            "Halting before any RPC."
        )


def _compute_bucket_ends(today: date) -> dict:
    """Identical to discover_kpi7.py — KPI 7 bucket end dates."""
    _, last_day = calendar.monthrange(today.year, today.month)
    end_of_month = date(today.year, today.month, last_day)
    quarter_idx = (today.month - 1) // 3
    q_end_month = (quarter_idx + 1) * 3
    _, last_q = calendar.monthrange(today.year, q_end_month)
    end_of_quarter = date(today.year, q_end_month, last_q)
    end_of_half = date(today.year, 6, 30) if today.month <= 6 else date(today.year, 12, 31)
    end_of_year = date(today.year, 12, 31)
    return {
        "this_month":   end_of_month,
        "this_quarter": end_of_quarter,
        "this_half":    end_of_half,
        "this_year":    end_of_year,
    }


def _pct_label(pct: float) -> str:
    if pct >= 30:
        return f"{pct:.1f}% ≥ 30% — future installments DO carry checks → PATH B viable"
    elif pct >= 10:
        return f"{pct:.1f}% in 10-30% — mixed pattern — drill into the WHY"
    else:
        return f"{pct:.1f}% < 10% — checks NOT on future installments → PATH C likely"


def _sanitize_user(user_id_raw) -> str:
    if isinstance(user_id_raw, (list, tuple)) and user_id_raw:
        return f"user_id={user_id_raw[0]}"
    if user_id_raw is False or user_id_raw is None:
        return "global"
    return f"user_id={user_id_raw}"


# ── Main async function ───────────────────────────────────────────────────────

async def main() -> None:
    _assert_read_only()

    run_at       = datetime.now(timezone.utc)
    today_cairo  = datetime.now(_LA_VERDE_TZ).date()
    today_str    = today_cairo.isoformat()
    bucket_ends  = _compute_bucket_ends(today_cairo)
    end_of_year_str  = bucket_ends["this_year"].isoformat()
    end_of_month_str = bucket_ends["this_month"].isoformat()

    # State collected across sections — used in PATH recommendation (Section 6).
    year_pct:        float          = 0.0
    month_pct:       float          = 0.0
    checks_field:    Optional[str]  = None
    has_checks_field: Optional[str] = None
    all_checks_field: Optional[str] = None
    check_fields_raw: dict          = {}
    all_inst_fields:  dict          = {}

    # ─────────────────────────────────────────────────────────────────────────
    # Section 0 — Header / Setup
    # ─────────────────────────────────────────────────────────────────────────
    print(_SEP)
    print("  Phase 0.5 — UI-Driven Discovery: rs.installment UI Artifacts")
    print(f"  Run at (UTC) : {run_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  Today (Cairo): {today_str}")
    print(f"  Bucket ends  : month={end_of_month_str}  year={end_of_year_str}")
    print(f"  ALLOWED_METHODS : {sorted(ALLOWED_METHODS)}")
    print("  READ-ONLY. No writes. No OpenAI. AI cost = $0.00")
    print(_SEP)
    print()
    print("  Context: Phase 0 bucket totals verified identity-equal (2026-05-18).")
    print("  This script resolves 4 UI artifacts not captured by RPC-only discovery:")
    print("    A. Pre-existing KPI-named Favorites (ir.filters)")
    print("    B. 'Has Checks' and 'All Checks Collected' filter options")
    print("    C. 'Checks' tab on installment form → check_ids relation details")
    print("    D. Undocumented field labels (total_due_amount, reservation_id, etc.)")
    print()

    async with OdooClient() as client:

        # ─────────────────────────────────────────────────────────────────────
        # SECTION 1 — Checks relation + statistical proof
        # ─────────────────────────────────────────────────────────────────────
        print(_SEP)
        print("SECTION 1 — 'Checks' tab: check_ids relation + statistical proof")
        print(_SEP)
        print()

        # 1a — Single fields_get for ALL rs.installment fields (all attributes needed
        # by both Section 1 and Section 2 — one RPC instead of two).
        print("  1a. fields_get(rs.installment, all fields)")
        t0 = time.monotonic()
        all_inst_fields = await client.execute_kw(
            _MODEL, "fields_get", args=[[]],
            kwargs={"attributes": [
                "type", "string", "required", "relation",
                "compute", "store", "depends", "related", "readonly",
            ]},
        )
        ms_1a = int((time.monotonic() - t0) * 1000)
        print(f"      → {len(all_inst_fields)} total fields on {_MODEL}  ({ms_1a} ms)")
        print()

        # Extract relational fields whose name or relation hints at checks.
        relational_types = {"one2many", "many2one", "many2many"}
        check_related_fields: dict = {}
        for fname, finfo in sorted(all_inst_fields.items()):
            if finfo.get("type") not in relational_types:
                continue
            relation = finfo.get("relation", "")
            label    = finfo.get("string", "")
            needle   = (fname + " " + relation + " " + label).lower()
            if "check" in needle:
                check_related_fields[fname] = finfo

        if check_related_fields:
            print("  Check-related relational fields on rs.installment:")
            for fname, fi in sorted(check_related_fields.items()):
                print(
                    f"    {fname:<42} type={fi.get('type'):<10} "
                    f"relation={fi.get('relation','—'):<40} "
                    f"label='{fi.get('string','')}'"
                )
            # Pick the many2many field pointing to rs.account.check as the primary checks field.
            for fname, fi in check_related_fields.items():
                if fi.get("type") == "many2many" and "check" in fi.get("relation", "").lower():
                    checks_field = fname
                    break
            if checks_field is None:
                # Fall back to any check-related relational field.
                checks_field = next(iter(check_related_fields), None)
            print(f"\n  Primary checks field identified: {checks_field!r}")
        else:
            print(f"  {_FLAG} No check-related relational fields found on rs.installment!")
            print("        Expected 'check_ids' (many2many → rs.account.check) from Phase 1.")
        print()

        # 1b — fields_get on rs.account.check — full field inventory.
        print("  1b. fields_get(rs.account.check) — check model field inventory")
        t0 = time.monotonic()
        try:
            check_fields_raw = await client.execute_kw(
                _CHECK_MODEL, "fields_get", args=[[]],
                kwargs={"attributes": ["type", "string", "required", "relation"]},
            )
            ms_1b = int((time.monotonic() - t0) * 1000)
            print(f"      → {len(check_fields_raw)} fields on {_CHECK_MODEL}  ({ms_1b} ms)")
            print()
            print(f"  {'Field':<45} {'Type':<14} {'UI Label':<40} {'Relation'}")
            print(f"  {'-'*45} {'-'*14} {'-'*40} {'-'*30}")
            for fname, fi in sorted(check_fields_raw.items()):
                if fname in _SKIP_FIELDS:
                    continue
                rel = fi.get("relation", "")
                rel_str = f"→ {rel}" if rel else ""
                print(
                    f"  {fname:<45} {fi.get('type',''):<14} "
                    f"{fi.get('string',''):<40} {rel_str}"
                )
        except Exception as exc:
            ms_1b = int((time.monotonic() - t0) * 1000)
            print(f"      {_FLAG} fields_get on {_CHECK_MODEL} failed ({ms_1b} ms): {exc}")
        print()

        # 1c — Total record count on rs.account.check.
        print("  1c. search_count(rs.account.check, []) — total check records")
        t0 = time.monotonic()
        try:
            total_checks = await client.execute_kw(
                _CHECK_MODEL, "search_count", args=[[]], kwargs={}
            )
            ms_1c = int((time.monotonic() - t0) * 1000)
            print(f"      → {total_checks:,} total check records  ({ms_1c} ms)")
        except Exception as exc:
            print(f"      {_FLAG} search_count failed: {exc}")
        print()

        # ── Statistical proof (MODIFICATION 1) ───────────────────────────────

        kpi7_year_domain = [
            ("state", "=", "post"),
            ("payment_state", "in", ["unpaid", "partial"]),
            ("date", ">=", today_str),
            ("date", "<=", end_of_year_str),
        ]
        kpi7_month_domain = [
            ("state", "=", "post"),
            ("payment_state", "in", ["unpaid", "partial"]),
            ("date", ">=", today_str),
            ("date", "<=", end_of_month_str),
        ]

        # e_pre — KPI 7 year-bucket universe.
        print("  e_pre. total_future_unpaid — KPI 7 this_year bucket universe")
        print(f"      domain: {kpi7_year_domain}")
        t0 = time.monotonic()
        total_future_unpaid = await client.execute_kw(
            _MODEL, "search_count", args=[kpi7_year_domain], kwargs={}
        )
        ms_epre = int((time.monotonic() - t0) * 1000)
        print(f"      result: {total_future_unpaid:,} records  ({ms_epre} ms)")
        print()

        # e_post — same universe, only those with check records attached.
        future_unpaid_with_checks = 0
        if checks_field:
            year_with_checks_domain = kpi7_year_domain + [(checks_field, "!=", False)]
            print(f"  e_post. future_unpaid_with_checks — year bucket + {checks_field} != False")
            print(f"      domain: {year_with_checks_domain}")
            t0 = time.monotonic()
            try:
                future_unpaid_with_checks = await client.execute_kw(
                    _MODEL, "search_count", args=[year_with_checks_domain], kwargs={}
                )
                ms_epost = int((time.monotonic() - t0) * 1000)
                print(f"      result: {future_unpaid_with_checks:,} records  ({ms_epost} ms)")
            except Exception as exc:
                ms_epost = int((time.monotonic() - t0) * 1000)
                print(f"      {_FLAG} {checks_field} != False failed ({ms_epost} ms): {exc}")
                print(f"      Falling back to check_pending_amount > 0 as proxy ...")
                t0 = time.monotonic()
                try:
                    future_unpaid_with_checks = await client.execute_kw(
                        _MODEL, "search_count",
                        args=[kpi7_year_domain + [("check_pending_amount", ">", 0)]],
                        kwargs={},
                    )
                    ms_fb = int((time.monotonic() - t0) * 1000)
                    print(f"      proxy (check_pending_amount > 0): {future_unpaid_with_checks:,}  ({ms_fb} ms)")
                except Exception as exc2:
                    print(f"      {_FLAG} Proxy also failed: {exc2}")
            print()

        # e_calc — Compute percentage + threshold interpretation.
        print("  e_calc. Statistical interpretation — year bucket")
        if total_future_unpaid > 0:
            year_pct = future_unpaid_with_checks / total_future_unpaid * 100
            print(f"      total_future_unpaid       : {total_future_unpaid:,}")
            print(f"      future_unpaid_with_checks : {future_unpaid_with_checks:,}")
            print(f"      percentage                : {year_pct:.2f}%")
            print(f"      Threshold: {_pct_label(year_pct)}")
        else:
            year_pct = 0.0
            print(f"      {_FLAG} total_future_unpaid = 0 — cannot compute percentage")
        print()

        # e_subset — this_month bucket (near-term signal).
        print("  e_subset. this_month bucket check stats (strongest near-term signal)")
        print(f"      date range: {today_str} → {end_of_month_str}")
        t0 = time.monotonic()
        total_month = await client.execute_kw(
            _MODEL, "search_count", args=[kpi7_month_domain], kwargs={}
        )
        ms_m = int((time.monotonic() - t0) * 1000)
        print(f"      total this_month (future unpaid): {total_month:,}  ({ms_m} ms)")

        month_with_checks = 0
        if checks_field and total_month > 0:
            month_with_checks_domain = kpi7_month_domain + [(checks_field, "!=", False)]
            t0 = time.monotonic()
            try:
                month_with_checks = await client.execute_kw(
                    _MODEL, "search_count", args=[month_with_checks_domain], kwargs={}
                )
                ms_mwc = int((time.monotonic() - t0) * 1000)
                print(f"      this_month with checks attached: {month_with_checks:,}  ({ms_mwc} ms)")
                month_pct = month_with_checks / total_month * 100
                print(f"      this_month percentage: {_pct_label(month_pct)}")
            except Exception as exc:
                print(f"      {_FLAG} Exception: {exc}")
        elif total_month == 0:
            print(f"      {_INFO} this_month has 0 future unpaid records — skipping check count")
        print()

        # e_samples — 10 future installments; verify statistical finding.
        print("  e_samples. 10 future installments — verify statistical finding")
        print("      (IDs and amounts only — no customer names)")
        sample_read_fields = [
            "id", "date", "amount", "paid_amount",
            "x_studio_actual_paid_amount", "payment_state",
            "check_pending_amount",
        ]
        if checks_field:
            sample_read_fields.append(checks_field)

        t0 = time.monotonic()
        sample_recs = await client.execute_kw(
            _MODEL, "search_read",
            args=[kpi7_year_domain, sample_read_fields],
            kwargs={"limit": 10, "order": "date asc"},
        )
        ms_samp = int((time.monotonic() - t0) * 1000)
        print(f"      → {len(sample_recs)} records  ({ms_samp} ms)")
        print()
        for rec in sample_recs:
            chk_ids = rec.get(checks_field, []) if checks_field else []
            chk_count = len(chk_ids) if isinstance(chk_ids, list) else "?"
            has_chk = bool(chk_ids) if isinstance(chk_ids, list) else bool(chk_ids)
            marker = "✓ HAS CHECKS" if has_chk else "  no checks"
            print(
                f"      id={rec['id']:>7} | {rec.get('date','?')} "
                f"| paid={rec.get('paid_amount', 0.0):>10,.2f} "
                f"| actual={rec.get('x_studio_actual_paid_amount', 0.0):>10,.2f} "
                f"| chk_pending={rec.get('check_pending_amount', 0.0):>10,.2f} "
                f"| {marker} ({chk_count})"
            )

        # For installments that have checks, read the check records (sanitized).
        if checks_field and check_fields_raw:
            recs_with_checks = [r for r in sample_recs if r.get(checks_field)]
            if recs_with_checks:
                print()
                print(f"      Fetching check records for {len(recs_with_checks)} installment(s) "
                      f"(no partner/name fields):")
                # Build a safe field list: non-relational, non-PII.
                safe_chk_fields = ["id"] + [
                    f for f, fi in sorted(check_fields_raw.items())
                    if fi.get("type") not in ("many2one", "one2many", "many2many")
                    and f not in _SKIP_FIELDS
                    and not any(kw in f.lower() for kw in ["partner", "name", "email", "phone"])
                ][:12]

                for inst_rec in recs_with_checks[:3]:
                    raw_ids = inst_rec[checks_field]
                    if not isinstance(raw_ids, list):
                        continue
                    ids_to_fetch = raw_ids[:5]
                    print(f"\n        installment id={inst_rec['id']} → "
                          f"{len(raw_ids)} check(s) (showing first {len(ids_to_fetch)}): {ids_to_fetch}")
                    t0 = time.monotonic()
                    try:
                        chk_recs = await client.execute_kw(
                            _CHECK_MODEL, "read",
                            args=[ids_to_fetch, safe_chk_fields],
                            kwargs={},
                        )
                        ms_cr = int((time.monotonic() - t0) * 1000)
                        for cr in chk_recs:
                            row = " | ".join(
                                f"{k}={v}"
                                for k, v in cr.items()
                                if v is not False and v is not None and v != 0 and v != ""
                            )
                            print(f"          {row}")
                    except Exception as exc:
                        print(f"          {_FLAG} read failed: {exc}")
            else:
                print()
                print(f"      {_INFO} None of the 10 sampled records have {checks_field} set.")
                if year_pct < 10:
                    print(f"           Consistent with year_pct={year_pct:.1f}% < 10% finding.")
        print()

        # ─────────────────────────────────────────────────────────────────────
        # SECTION 2 — "Has Checks" and "All Checks Collected" (MODIFICATION 2)
        # ─────────────────────────────────────────────────────────────────────
        print(_SEP)
        print("SECTION 2 — 'Has Checks' / 'All Checks Collected' fields (3-type probe)")
        print(_SEP)
        print()

        # 2a — Use the all_inst_fields dict already fetched in Section 1 (no extra RPC).
        print("  2a. Filtering all_inst_fields (from Section 1) for 'check' keywords")
        print("      Attributes checked: type, string, compute, store, depends, related")
        print()

        _CHECK_KW = {"check"}  # broad match — catches check_ids, check_pending_amount, has_checks, etc.
        check_candidate_fields: dict = {}
        for fname, finfo in sorted(all_inst_fields.items()):
            label_lower = finfo.get("string", "").lower()
            name_lower  = fname.lower()
            if any(kw in label_lower or kw in name_lower for kw in _CHECK_KW):
                check_candidate_fields[fname] = finfo

        if check_candidate_fields:
            print(f"  Fields matching 'check' keywords — {len(check_candidate_fields)} found:")
            for fname, fi in sorted(check_candidate_fields.items()):
                print(f"\n    {fname}")
                for attr in ["type", "string", "compute", "store", "depends",
                             "related", "readonly", "relation"]:
                    val = fi.get(attr)
                    if val is not None and val is not False and val != "" and val != []:
                        print(f"      {attr:<12}: {val}")
        else:
            print(f"  {_FLAG} No fields matching 'check' keywords found on rs.installment.")
        print()

        # Identify has_checks and all_checks_collected specifically.
        for fname, fi in check_candidate_fields.items():
            label = fi.get("string", "").lower()
            name  = fname.lower()
            if "has_check" in name or "has check" in label:
                has_checks_field = fname
            if "all_check" in name or "all check" in label:
                all_checks_field = fname

        # 2b — Branch on store + type for each candidate.
        for fname, fi in sorted(check_candidate_fields.items()):
            ftype    = fi.get("type")
            is_stored = fi.get("store")
            # Native fields (not computed) don't have a store attribute — treat as stored.
            if is_stored is None:
                is_stored = True

            print(f"  2b. Field '{fname}'  type={ftype}  store={is_stored}")

            if ftype == "boolean" and is_stored:
                print(f"      → Type 1 (stored boolean) — running search_count with '{fname}'=True")
                t0 = time.monotonic()
                try:
                    cnt_all = await client.execute_kw(
                        _MODEL, "search_count",
                        args=[[(fname, "=", True)]], kwargs={}
                    )
                    ms_c1 = int((time.monotonic() - t0) * 1000)
                    print(f"         ALL installments where {fname}=True : {cnt_all:,}  ({ms_c1} ms)")

                    t0 = time.monotonic()
                    cnt_future = await client.execute_kw(
                        _MODEL, "search_count",
                        args=[kpi7_year_domain + [(fname, "=", True)]], kwargs={}
                    )
                    ms_c2 = int((time.monotonic() - t0) * 1000)
                    print(f"         KPI 7 universe + {fname}=True       : {cnt_future:,}  ({ms_c2} ms)")
                    if total_future_unpaid > 0:
                        fpct = cnt_future / total_future_unpaid * 100
                        print(f"         pct of future unpaid              : {fpct:.2f}%")
                        print(f"         Threshold: {_pct_label(fpct)}")
                except Exception as exc:
                    print(f"         {_FLAG} search_count failed: {exc}")

            elif not is_stored:
                print(f"      → Type 2 (computed-not-stored) — search_count unsafe.")
                print(f"         Reading 10 future installments with this field instead.")
                t0 = time.monotonic()
                try:
                    s10 = await client.execute_kw(
                        _MODEL, "search_read",
                        args=[kpi7_year_domain, ["id", fname]],
                        kwargs={"limit": 10},
                    )
                    ms_s10 = int((time.monotonic() - t0) * 1000)
                    print(f"         10 records  ({ms_s10} ms):")
                    for rec in s10:
                        print(f"           id={rec['id']:>7} | {fname}={rec.get(fname)}")
                except Exception as exc:
                    print(f"         {_FLAG} search_read failed: {exc}")

            else:
                # Non-boolean stored field (e.g., monetary, many2many).
                print(f"      → Type 3 (non-boolean: {ftype}) — documenting semantics only.")
                for attr in ["string", "relation", "compute", "depends"]:
                    val = fi.get(attr)
                    if val:
                        print(f"         {attr}: {val}")

            print()

        # 2c — If no has_checks / all_checks_collected fields found, inspect ir.ui.view.
        if has_checks_field is None and all_checks_field is None:
            print(f"  2c. No dedicated has_checks / all_checks_collected field found.")
            print(f"      'Has Checks' is likely a view-level filter expression on {checks_field!r}.")
            print(f"      Inspecting ir.ui.view for All Installments search view XML ...")
            print()

            # Step 1: Get view names/types without arch_db (cheap).
            t0 = time.monotonic()
            try:
                all_views = await client.execute_kw(
                    _VIEW_MODEL, "search_read",
                    args=[[("model", "=", _MODEL)], ["id", "name", "type"]],
                    kwargs={"order": "type asc, id asc"},
                )
                ms_vn = int((time.monotonic() - t0) * 1000)
                print(f"      {len(all_views)} views registered for model '{_MODEL}'  ({ms_vn} ms):")
                for v in all_views:
                    print(f"        id={v['id']:>6} | type={v.get('type','?'):<8} | name={v.get('name','?')}")
                print()

                # Step 2: Fetch arch_db only for search and form views.
                target_ids = [v["id"] for v in all_views if v.get("type") in ("search", "form")]
                if target_ids:
                    print(f"      Fetching arch_db for {len(target_ids)} search/form view(s) ...")
                    t0 = time.monotonic()
                    view_archs = await client.execute_kw(
                        _VIEW_MODEL, "read",
                        args=[target_ids, ["id", "name", "type", "arch_db"]],
                        kwargs={},
                    )
                    ms_arch = int((time.monotonic() - t0) * 1000)
                    print(f"      ({ms_arch} ms)")
                    print()

                    _ARCH_KW = [
                        "has_check", "Has Check", "All Check", "all_check",
                        "check_ids", "All Checks Collected",
                    ]
                    any_found = False
                    for v in view_archs:
                        arch = v.get("arch_db") or ""
                        found_kws = [kw for kw in _ARCH_KW if kw in arch]
                        if not found_kws:
                            continue
                        any_found = True
                        print(f"      {_FLAG} View id={v['id']} '{v.get('name')}' "
                              f"(type={v.get('type')}) — keywords: {found_kws}")
                        lines = arch.splitlines()
                        for kw in found_kws[:3]:
                            for i, line in enumerate(lines):
                                if kw in line:
                                    ctx = lines[max(0, i - 2): min(len(lines), i + 3)]
                                    print(f"         Context for '{kw}':")
                                    for cl in ctx:
                                        print(f"           {cl.strip()[:120]}")
                                    break
                        print()

                    if not any_found:
                        print(f"      {_INFO} No check-related keywords found in any view XML.")
                        print(f"           'Has Checks' may be a client-side Odoo UI groupby on a many2many count.")
            except Exception as exc:
                print(f"      {_FLAG} ir.ui.view inspection failed: {exc}")
        print()

        # ─────────────────────────────────────────────────────────────────────
        # SECTION 3 — Undocumented field labels
        # ─────────────────────────────────────────────────────────────────────
        print(_SEP)
        print("SECTION 3 — Undocumented fields on rs.installment (Objective 3)")
        print(_SEP)
        print()

        _PROBE_FIELDS = [
            "total_due_amount",
            "reservation_id",
            "contract_id",
            "phase_id",
            "building_id",
            "zone_id",
            "unit_id",
        ]

        print("  3a. fields_get for 7 probe fields")
        t0 = time.monotonic()
        probe_info = await client.execute_kw(
            _MODEL, "fields_get",
            args=[_PROBE_FIELDS],
            kwargs={"attributes": ["type", "string", "required", "relation", "compute", "store"]},
        )
        ms_3a = int((time.monotonic() - t0) * 1000)
        print(f"      ({ms_3a} ms)")
        print()

        hdr = f"  {'Field':<28} {'Type':<13} {'UI Label':<32} {'Relation':<34} Compute/Store"
        print(hdr)
        print(f"  {'-'*28} {'-'*13} {'-'*32} {'-'*34} {'-'*20}")
        for fname in _PROBE_FIELDS:
            fi = probe_info.get(fname, {})
            if not fi:
                print(f"  {fname:<28} {'NOT FOUND':<13}")
                continue
            ftype   = fi.get("type", "?")
            fstr    = fi.get("string", "?")
            frel    = fi.get("relation", "—")
            fcomp   = fi.get("compute") or "—"
            fstore  = str(fi.get("store", "?"))
            print(f"  {fname:<28} {ftype:<13} {fstr:<32} {frel:<34} comp={fcomp!r} store={fstore}")
        print()

        # 3b — For each relational field: search_count + safe sample read.
        print("  3b. Related model record counts and samples (non-PII)")
        print()
        _PII_KEYWORDS = {"partner", "name", "email", "phone", "street", "mobile", "contact"}

        for fname in _PROBE_FIELDS:
            fi = probe_info.get(fname, {})
            if not fi:
                continue
            if fi.get("type") not in ("many2one", "one2many", "many2many"):
                print(f"  {fname} — {fi.get('type')} (no related model to probe)")
                continue
            relation = fi.get("relation")
            if not relation:
                continue

            print(f"  {fname} → {relation}")

            # Count.
            t0 = time.monotonic()
            try:
                rel_count = await client.execute_kw(
                    relation, "search_count", args=[[]], kwargs={}
                )
                ms_rc = int((time.monotonic() - t0) * 1000)
                print(f"    search_count([]) = {rel_count:,}  ({ms_rc} ms)")
            except Exception as exc:
                print(f"    {_FLAG} search_count failed: {exc}")
                print()
                continue

            # Get safe fields from the related model.
            t0 = time.monotonic()
            try:
                rel_all_fields = await client.execute_kw(
                    relation, "fields_get", args=[[]],
                    kwargs={"attributes": ["type", "string"]},
                )
                safe_fields = ["id"] + [
                    f for f, fi2 in rel_all_fields.items()
                    if fi2.get("type") in (
                        "integer", "float", "monetary", "date", "datetime",
                        "selection", "char", "boolean",
                    )
                    and not any(kw in f.lower() for kw in _PII_KEYWORDS)
                    and f not in _SKIP_FIELDS
                ][:8]

                sample = await client.execute_kw(
                    relation, "search_read",
                    args=[[], safe_fields],
                    kwargs={"limit": 3},
                )
                ms_rs = int((time.monotonic() - t0) * 1000)
                print(f"    3 sample records (fields: {safe_fields})  ({ms_rs} ms):")
                for rec in sample:
                    print(f"      {rec}")
            except Exception as exc:
                ms_rs = int((time.monotonic() - t0) * 1000)
                print(f"    {_FLAG} sample read failed ({ms_rs} ms): {exc}")
            print()

        # ─────────────────────────────────────────────────────────────────────
        # SECTION 4 — Pre-existing KPI Favorites (ir.filters)
        # ─────────────────────────────────────────────────────────────────────
        print(_SEP)
        print("SECTION 4 — Pre-existing KPI Favorites on All Installments (Objective 4)")
        print(_SEP)
        print()

        print(f"  search_read(ir.filters, [('model_id','=','{_MODEL}')])")
        t0 = time.monotonic()
        try:
            all_filters = await client.execute_kw(
                _FILTERS_MODEL, "search_read",
                args=[[("model_id", "=", _MODEL)],
                      ["name", "domain", "context", "user_id", "sort"]],
                kwargs={},
            )
            ms_4 = int((time.monotonic() - t0) * 1000)
            print(f"  → {len(all_filters)} saved filters found  ({ms_4} ms)")
            print()

            for flt in all_filters:
                uid_str = _sanitize_user(flt.get("user_id"))
                print(f"  ── '{flt.get('name', '?')}'  ({uid_str})")
                domain_raw = flt.get("domain", "—")
                print(f"     domain  : {domain_raw}")
                ctx_raw = flt.get("context")
                if ctx_raw and ctx_raw != "{}":
                    print(f"     context : {ctx_raw}")
                sort_val = flt.get("sort")
                if sort_val:
                    print(f"     sort    : {sort_val}")

                # Cross-check domain direction vs our KPI implementations.
                d_str = str(domain_raw)
                fname_lower = flt.get("name", "").lower()
                if "overdue" in fname_lower or "late" in fname_lower or "outstanding" in fname_lower:
                    if "'date'" in d_str and ("< " in d_str or "'<'" in d_str or '"<"' in d_str):
                        print(f"     {_INFO} backward-looking (date < TODAY) — consistent with KPI 2 domain")
                    elif "'date'" in d_str and (">=" in d_str or "'>='" in d_str or '">="' in d_str):
                        print(f"     {_INFO} forward-looking (date >= TODAY) — consistent with KPI 7 domain")
                    elif "'date'" not in d_str:
                        print(f"     {_INFO} no date filter — portfolio-wide (KPI 1 / KPI 3 pattern)")
                if "collected" in fname_lower or "paid" in fname_lower:
                    print(f"     {_INFO} Likely KPI 4 / collections-rate related — compare against kpi_service.py")
                print()

        except Exception as exc:
            print(f"  {_FLAG} ir.filters search_read failed: {exc}")
        print()

        # ─────────────────────────────────────────────────────────────────────
        # SECTION 5 — UI field label verification
        # ─────────────────────────────────────────────────────────────────────
        print(_SEP)
        print("SECTION 5 — UI field label verification (Objective 5)")
        print(_SEP)
        print()

        _UI_LABELS = [
            ("Amount",               "amount"),
            ("Paid Amount",          "paid_amount"),
            ("Due Amount",           "due_amount"),
            ("Actual Paid Amount",   "x_studio_actual_paid_amount"),
            ("Total Due Amount",     "total_due_amount"),
            ("Payment Period",       "payment_type_id"),
            ("Reservation",          "reservation_id"),
            ("Contract",             "contract_id"),
        ]
        label_field_names = [f for _, f in _UI_LABELS]

        print("  fields_get for 8 observed UI label fields")
        t0 = time.monotonic()
        label_info = await client.execute_kw(
            _MODEL, "fields_get",
            args=[label_field_names],
            kwargs={"attributes": ["type", "string"]},
        )
        ms_5 = int((time.monotonic() - t0) * 1000)
        print(f"  ({ms_5} ms)")
        print()

        col1, col2, col3 = 32, 37, 37
        print(f"  | {'UI Label (observed)':<{col1}} | {'Technical Field':<{col2}} "
              f"| {'fields_get string':<{col3}} | Match?")
        print(f"  |{'-'*(col1+2)}|{'-'*(col2+2)}|{'-'*(col3+2)}|-------")
        for ui_label, fname in _UI_LABELS:
            fi = label_info.get(fname, {})
            fg_string = fi.get("string", "NOT FOUND")
            if fg_string == ui_label:
                match_str = _PASS
            elif fg_string == "NOT FOUND":
                match_str = f"{_FLAG} NOT FOUND"
            else:
                match_str = f"{_FLAG} MISMATCH"
            print(f"  | {ui_label:<{col1}} | {fname:<{col2}} | {fg_string:<{col3}} | {match_str}")
        print()

        # ─────────────────────────────────────────────────────────────────────
        # SECTION 6 — PATH Recommendation
        # ─────────────────────────────────────────────────────────────────────
        print(_SEP)
        print("SECTION 6 — Phase 1 KPI 7 PATH Recommendation")
        print(_SEP)
        print()
        print("  Evidence summary:")
        print(f"    checks_field on rs.installment  : {checks_field!r}")
        print(f"    has_checks_field                 : {has_checks_field!r}")
        print(f"    all_checks_field                 : {all_checks_field!r}")
        print(f"    year_pct (future installs w/ chk): {year_pct:.2f}%")
        print(f"    month_pct (this_month w/ chk)    : {month_pct:.2f}%")
        print()

        # PATH determination based on Section 1 statistical proof.
        if year_pct >= 30:
            path = "B"
            print("  ██ RECOMMENDED PATH: B ██")
            print()
            print("  Rationale:")
            print(f"    {year_pct:.1f}% of future unpaid installments have check records attached.")
            print(f"    The {checks_field!r} many2many on rs.installment → {_CHECK_MODEL}")
            print(f"    provides a queryable, per-installment cheques pipeline for forecast buckets.")
            print()
            print("  Phase 1 implementation under PATH B:")
            print("    1. For each bucket: search installments in bucket domain that have check_ids.")
            print("    2. Read those check records, sum amount for checks in 'pending/pipeline' state.")
            print("    3. cheques_in_pipeline = SUM of check amounts in pipeline states.")
            print("    4. cheques_record_count = COUNT of those check records.")
            print("    PREREQUISITE: Section 1b must have revealed the check state field + values.")
            print("    KEY QUESTION for Khaled: which check states count as 'in pipeline'?")

        elif year_pct >= 10:
            path = "A"
            print("  ██ RECOMMENDED PATH: A (Alternative B, cheques annotation kept) ██")
            print()
            print("  Rationale:")
            print(f"    {year_pct:.1f}% of future unpaid installments have check records (10-30% band).")
            print("    Mixed pattern: some future installments carry checks, most do not.")
            print("    Alternative B formula (paid_amount - actual_paid) will correctly show")
            print("    near-zero for near-term buckets and a small positive for the year bucket.")
            print("    This is NOT misleading — it accurately reflects the data.")
            print()
            print("  Phase 1 implementation under PATH A:")
            print("    No change to the plan. Proceed with Alternative B as specified in")
            print("    docs/KPI7_DISCOVERY_FINDINGS.md §Phase 1 Implementation Decisions.")
            print("    cheques_record_count = null (not available via read_group net formula).")

        else:
            path = "C"
            print("  ██ RECOMMENDED PATH: C ██")
            print()
            print("  Rationale:")
            print(f"    Only {year_pct:.1f}% of future unpaid installments have check records (< 10%).")
            print("    La Verde's cheque workflow does not update paid_amount on installments")
            print("    before their due date. The Alternative B formula (paid_amount - actual_paid)")
            print("    will structurally show 0 EGP on 3 of 4 KPI 7 cards — visual clutter.")
            print()
            print("  Phase 1 implementation under PATH C:")
            print("    Remove cheques_in_pipeline annotation from KPI 7 forecast card UI.")
            print("    The backend response shape CAN still include cheques_in_pipeline: 0.0")
            print("    for completeness (and future use if the workflow changes).")
            print("    The UI simply does not render the amber annotation when the value is 0.")
            print("    This is consistent with Stage 4 spec §4.4: 'when cheques_in_pipeline == 0,")
            print("    hide the annotation entirely'.")
            print()
            print("    KPI 2 (Late Installments) cheques annotation is UNAFFECTED — late installs")
            print("    DO have paid_amount > 0 when cheques have been credited.")

        print()
        print(f"  Awaiting Khaled's PATH decision (A / B / C) before Phase 1 begins.")
        print()
        print(_SEP)
        print()
        print("  ████████████████████████████████████████████████████████████████████████")
        print("  PHASE 0.5 COMPLETE. AWAITING KHALED REVIEW AND PHASE 1 APPROVAL.")
        print("  ████████████████████████████████████████████████████████████████████████")
        print()
        print(_SEP)


# ── Entry point ───────────────────────────────────────────────────────────────

def run() -> None:
    """Sync wrapper: Tee stdout to file, run async main, restore stdout."""
    output_buffer = StringIO()

    class Tee:
        def __init__(self, *streams):
            self.streams = streams

        def write(self, data: str) -> None:
            for s in self.streams:
                s.write(data)

        def flush(self) -> None:
            for s in self.streams:
                s.flush()

    original_stdout = sys.stdout
    sys.stdout = Tee(original_stdout, output_buffer)

    exit_code = 0
    try:
        asyncio.run(main())
    except SystemExit as exc:
        exit_code = int(exc.code) if exc.code is not None else 1
    except Exception as exc:
        print(f"\n  FATAL ERROR: {exc}")
        import traceback
        traceback.print_exc()
        exit_code = 1
    finally:
        sys.stdout = original_stdout
        output_text = output_buffer.getvalue()
        try:
            _OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
            _OUTPUT_FILE.write_text(output_text, encoding="utf-8")
            print(f"\n  Output saved to: {_OUTPUT_FILE}")
        except Exception as write_exc:
            print(f"\n  WARNING: could not save output file: {write_exc}")

    if exit_code != 0:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    run()
