"""
scripts/discover_kpi2_redefinition.py — Stage 2.5 Pre-Implementation Discovery

Verifies 4 hypotheses on the Late installment subset to gate the KPI 2 formula
redefinition from PATH C (due_amount) to PATH A (amount − actual_paid_amount).
Trigger: Decision 11.13. Reverses: Decision 10.1. Session 12.

Sections:
  Section 0   — Header / Audit trail (UTC + Cairo timestamps, today date)
  Section 0.5 — Field discovery: confirms total_due_amount field name via fields_get
  Section 1   — Late universe baseline (single combined read_group, all monetary fields)
  Section 2   — H1: SUM(amount) = SUM(paid_amount) + SUM(due_amount)
  Section 3   — H2: SUM(amount) = SUM(actual_paid) + SUM(total_due) [PATH A identity]
  Section 4   — H3: SUM(paid) − SUM(actual_paid) > 0 AND ≈ SUM(check_pending_amount)
  Section 5   — H4: record_count invariant (domain-only, formula-independent)
  Section 6   — Gate verdict (all 4 PASS → exit 0; any FAIL → exit 1)
  Section 7   — Phase complete summary

RPC budget: 3 actual (≤ 6 limit).
  RPC 0: fields_get on rs.installment (Section 0.5)
  RPC 1: read_group — all monetary fields combined (Section 1)
  RPC 2: read_group — standalone check_pending_amount (Section 4)

Identity mismatch thresholds (Amendment 2, Stage 2.5):
  delta < 1.00 EGP           : identity holds, no flag
  1.00 ≤ delta < 1,000 EGP   : micro-drift, log INFO, PASS (no flag)
  delta ≥ 1,000 EGP          : log WARNING, FAIL

Hard constraints:
  - READ-ONLY. ALLOWED_METHODS unchanged.
  - No PII: no customer names, partner IDs, or contract numbers in output.

Exit 0: GATE PASS — all 4 hypotheses confirmed, proceed to Phase B.
Exit 1: GATE FAIL — at least 1 hypothesis failed, await Khaled decision.

Usage (from project root):
    $env:PYTHONPATH = "."; C:\\Python310\\python.exe scripts/discover_kpi2_redefinition.py
"""

import asyncio
import io
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient

# Force UTF-8 stdout (Windows consoles default to cp1252).
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Constants ─────────────────────────────────────────────────────────────────

_MODEL       = "rs.installment"
_LA_VERDE_TZ = ZoneInfo("Africa/Cairo")
_OUTPUT_FILE = Path(__file__).parent / "discover_kpi2_redefinition_output.txt"

_FORBIDDEN_WRITE_METHODS = frozenset({"create", "write", "unlink"})

# Identity mismatch thresholds (Amendment 2).
# Applied to H2 (PATH A identity: amount = actual_paid + total_due).
_H2_DELTA_EXACT = 1.00      # below: identity holds exactly, no flag
_H2_DELTA_MICRO = 1000.00   # [EXACT, MICRO): micro-drift, INFO, PASS; ≥ MICRO: WARNING, FAIL

# H3: derived cheques vs stored check_pending_amount.
_H3_PARITY_TOLERANCE = 1.00  # ±1.00 EGP

# H1: PATH C identity (amount = paid + due) — strict tolerance.
_H1_TOLERANCE = 1.00

_SEP  = "═" * 78
_SEP2 = "─" * 76
_PASS = "[PASS]"
_FAIL = "[FAIL]"
_INFO = "[INFO]"
_WARN = "[WARN]"


# ── Tee ───────────────────────────────────────────────────────────────────────

class _Tee:
    """Mirrors all writes to both the original stdout and an output file."""

    def __init__(self, filepath: Path) -> None:
        self._file   = filepath.open("w", encoding="utf-8", errors="replace")
        self._stdout = sys.stdout

    def write(self, data: str) -> int:
        self._stdout.write(data)
        self._file.write(data)
        return len(data)

    def flush(self) -> None:
        self._stdout.flush()
        self._file.flush()

    def close(self) -> None:
        self._file.close()

    @property
    def encoding(self) -> str:
        return getattr(self._stdout, "encoding", "utf-8")

    @property
    def errors(self) -> str:
        return getattr(self._stdout, "errors", "replace")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _assert_read_only() -> None:
    violations = ALLOWED_METHODS & _FORBIDDEN_WRITE_METHODS
    if violations:
        raise RuntimeError(
            f"ALLOWED_METHODS contains forbidden write method(s): {sorted(violations)}. "
            "Halting before any RPC."
        )


def _egp(v: float) -> str:
    return f"{v:,.2f} EGP"


def _delta_label(delta: float) -> str:
    """Human-readable delta with tier classification."""
    if delta < _H2_DELTA_EXACT:
        return f"{delta:.6f} EGP [exact]"
    if delta < _H2_DELTA_MICRO:
        return f"{delta:,.4f} EGP [micro-drift]"
    return f"{delta:,.2f} EGP [MISMATCH]"


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> int:
    _assert_read_only()

    run_at_utc   = datetime.now(timezone.utc)
    run_at_cairo = datetime.now(_LA_VERDE_TZ)
    today_cairo  = run_at_cairo.date()
    today_str    = today_cairo.isoformat()

    # Canonical Candidate C late domain — immutable per MODULE_2_DISCOVERY_PHASE_2.md §3.
    late_domain = [
        ("state",         "=",  "post"),
        ("payment_state", "in", ["unpaid", "partial"]),
        ("date",          "<",  today_str),
    ]

    # Hypothesis result tracking.
    h1_pass = h2_pass = h3_pass = h4_pass = False
    failures: list[str] = []

    # ─────────────────────────────────────────────────────────────────────────
    # Section 0 — Header / Audit trail
    # ─────────────────────────────────────────────────────────────────────────
    print(_SEP)
    print("  Stage 2.5 — KPI 2 Redefinition Pre-Implementation Discovery")
    print(_SEP)
    print(f"  Run timestamp (UTC)   : {run_at_utc.isoformat()}")
    print(f"  Run timestamp (Cairo) : {run_at_cairo.isoformat()}")
    print(f"  Late domain today date: {today_str}")
    print(f"  ALLOWED_METHODS       : {sorted(ALLOWED_METHODS)}")
    print("  READ-ONLY. No writes. No OpenAI. AI cost = $0.00")
    print(_SEP)
    print()
    print("  Purpose: verify 4 hypotheses on Late subset before KPI 2 formula change.")
    print("  Decision 11.13 (PATH A): value = amount - actual_paid_amount")
    print("  Reverses Decision 10.1 (PATH C): value = due_amount")
    print(f"  Late domain: {late_domain}")
    print()

    async with OdooClient() as client:

        # ─────────────────────────────────────────────────────────────────────
        # Section 0.5 — Field Discovery (RPC 0)
        # ─────────────────────────────────────────────────────────────────────
        print(_SEP)
        print("SECTION 0.5 — Field Discovery (fields_get on rs.installment)")
        print(_SEP)
        print()
        print("  0a. fields_get(rs.installment, attributes=['string', 'type'])")

        t0 = time.monotonic()
        try:
            all_fields: dict = await client.execute_kw(
                _MODEL,
                "fields_get",
                args=[],
                kwargs={"attributes": ["string", "type"]},
            )
        except Exception as exc:
            ms_05 = int((time.monotonic() - t0) * 1000)
            print(f"      {_FAIL} fields_get failed ({ms_05} ms): {exc}")
            print()
            print("  GATE FAIL — fields_get RPC error. Cannot proceed.")
            failures.append("fields_get_rpc_error")
            _print_gate_fail(failures)
            return 1

        ms_05 = int((time.monotonic() - t0) * 1000)
        print(f"      → {len(all_fields)} fields returned  ({ms_05} ms)")
        print()

        # Candidate 1: total_due_amount (expected)
        # Candidate 2: x_studio_total_due_amount (fallback)
        total_due_field: str | None = None

        if "total_due_amount" in all_fields:
            total_due_field = "total_due_amount"
            finfo = all_fields["total_due_amount"]
            print(f"  {_PASS} Primary candidate 'total_due_amount' found.")
            print(f"        type  = {finfo.get('type')!r}")
            print(f"        label = {finfo.get('string')!r}")
        elif "x_studio_total_due_amount" in all_fields:
            total_due_field = "x_studio_total_due_amount"
            finfo = all_fields["x_studio_total_due_amount"]
            print(f"  {_INFO} Primary 'total_due_amount' absent.")
            print(f"  {_PASS} Fallback 'x_studio_total_due_amount' found.")
            print(f"        type  = {finfo.get('type')!r}")
            print(f"        label = {finfo.get('string')!r}")
        else:
            print(f"  {_FAIL} Neither 'total_due_amount' nor 'x_studio_total_due_amount' found.")
            print()
            print("  All rs.installment fields matching /due/ (case-insensitive):")
            due_fields = {
                name: info
                for name, info in sorted(all_fields.items())
                if "due" in name.lower()
                or "due" in (info.get("string") or "").lower()
            }
            if due_fields:
                for fname, finfo in due_fields.items():
                    print(
                        f"    {fname:<48}  "
                        f"type={str(finfo.get('type')):<12}  "
                        f"label={finfo.get('string')!r}"
                    )
            else:
                print("    (no fields matching /due/ found)")
            print()
            failures.append("total_due_field_not_found")
            _print_gate_fail(failures)
            return 1

        print()
        print(f"  Using field name: '{total_due_field}' throughout remaining sections.")
        print()

        # ─────────────────────────────────────────────────────────────────────
        # Section 1 — Late Domain Universe Baseline (RPC 1)
        # ─────────────────────────────────────────────────────────────────────
        print(_SEP)
        print("SECTION 1 — Late Domain Universe Baseline")
        print(_SEP)
        print()
        print("  1a. read_group(late_domain, [amount, due_amount, paid_amount,")
        print(f"                               x_studio_actual_paid_amount, {total_due_field}], [])")

        t0 = time.monotonic()
        try:
            rg_rows = await client.execute_kw(
                _MODEL,
                "read_group",
                args=[
                    late_domain,
                    [
                        "amount",
                        "due_amount",
                        "paid_amount",
                        "x_studio_actual_paid_amount",
                        total_due_field,
                    ],
                    [],
                ],
                kwargs={"lazy": False},
            )
        except Exception as exc:
            ms_1 = int((time.monotonic() - t0) * 1000)
            print(f"      {_FAIL} read_group failed ({ms_1} ms): {exc}")
            failures.append("read_group_rpc_error")
            _print_gate_fail(failures)
            return 1

        ms_1 = int((time.monotonic() - t0) * 1000)
        print(f"      → {len(rg_rows)} row(s) returned  ({ms_1} ms)")
        print()

        row = rg_rows[0] if rg_rows else {}
        record_count      = int(row.get("__count") or 0)
        sum_amount        = float(row.get("amount") or 0)
        sum_due_amount    = float(row.get("due_amount") or 0)
        sum_paid_amount   = float(row.get("paid_amount") or 0)
        sum_actual_paid   = float(row.get("x_studio_actual_paid_amount") or 0)
        sum_total_due     = float(row.get(total_due_field) or 0)

        derived_path_c    = sum_due_amount                          # current formula
        derived_path_a    = sum_amount - sum_actual_paid            # new formula (PATH A)
        derived_cheques   = max(sum_paid_amount - sum_actual_paid, 0.0)

        print("  Universe sums:")
        print(f"    record_count                           : {record_count:,}")
        print(f"    SUM(amount)                            : {_egp(sum_amount)}")
        print(f"    SUM(due_amount)                        : {_egp(sum_due_amount)}")
        print(f"    SUM(paid_amount)                       : {_egp(sum_paid_amount)}")
        print(f"    SUM(x_studio_actual_paid_amount)       : {_egp(sum_actual_paid)}")
        print(f"    SUM({total_due_field:<30}) : {_egp(sum_total_due)}")
        print()
        print("  Derived values:")
        print(f"    PATH C value  (due_amount)             : {_egp(derived_path_c)}")
        print(f"    PATH A value  (amount - actual_paid)   : {_egp(derived_path_a)}")
        print(f"    Cheques delta (paid - actual_paid)     : {_egp(derived_cheques)}")
        print(f"    PATH A − PATH C delta                  : {_egp(derived_path_a - derived_path_c)}")
        print()

        # ─────────────────────────────────────────────────────────────────────
        # Section 2 — H1: SUM(amount) = SUM(paid_amount) + SUM(due_amount)
        # ─────────────────────────────────────────────────────────────────────
        print(_SEP)
        print("SECTION 2 — H1: SUM(amount) = SUM(paid_amount) + SUM(due_amount)")
        print("            [PATH C identity — validates existing formula is consistent]")
        print(_SEP)
        print()

        h1_lhs   = sum_amount
        h1_rhs   = sum_paid_amount + sum_due_amount
        h1_delta = abs(h1_lhs - h1_rhs)

        print(f"    SUM(amount)                = {_egp(h1_lhs)}")
        print(f"    SUM(paid_amount)           = {_egp(sum_paid_amount)}")
        print(f"    SUM(due_amount)            = {_egp(sum_due_amount)}")
        print(f"    SUM(paid) + SUM(due)       = {_egp(h1_rhs)}")
        print(f"    delta                      = {_egp(h1_delta)}")
        print(f"    tolerance                  = {_egp(_H1_TOLERANCE)}")
        print()

        if h1_delta < _H1_TOLERANCE:
            print(f"  {_PASS} H1 PASS: identity holds (delta = {h1_delta:.4f} EGP < {_H1_TOLERANCE:.2f} EGP)")
            h1_pass = True
        else:
            print(f"  {_FAIL} H1 FAIL: identity broken — delta = {_egp(h1_delta)} ≥ {_egp(_H1_TOLERANCE)}")
            print("         The existing PATH C formula may be inconsistent with Odoo data.")
            print("         STOP — report to Khaled before any code change.")
            h1_pass = False
            failures.append(f"H1_delta_{h1_delta:.2f}")
        print()

        # ─────────────────────────────────────────────────────────────────────
        # Section 3 — H2: SUM(amount) = SUM(actual_paid) + SUM(total_due)
        # ─────────────────────────────────────────────────────────────────────
        print(_SEP)
        print(f"SECTION 3 — H2: SUM(amount) = SUM(actual_paid) + SUM({total_due_field})")
        print("            [PATH A identity — validates new formula is safe to use]")
        print(_SEP)
        print()
        print("  Identity mismatch thresholds (Amendment 2):")
        print(f"    delta < {_H2_DELTA_EXACT:.2f} EGP           : PASS — identity holds exactly")
        print(f"    {_H2_DELTA_EXACT:.2f} ≤ delta < {_H2_DELTA_MICRO:,.0f} EGP : PASS — micro-drift, acceptable")
        print(f"    delta ≥ {_H2_DELTA_MICRO:,.0f} EGP         : FAIL — identity mismatch")
        print()

        h2_lhs   = sum_amount
        h2_rhs   = sum_actual_paid + sum_total_due
        h2_delta = abs(h2_lhs - h2_rhs)

        print(f"    SUM(amount)                = {_egp(h2_lhs)}")
        print(f"    SUM(actual_paid_amount)    = {_egp(sum_actual_paid)}")
        print(f"    SUM({total_due_field:<22}) = {_egp(sum_total_due)}")
        print(f"    SUM(actual) + SUM(total_due) = {_egp(h2_rhs)}")
        print(f"    delta                      = {_delta_label(h2_delta)}")
        print()

        if h2_delta < _H2_DELTA_EXACT:
            print(f"  {_PASS} H2 PASS: identity holds exactly (delta = {h2_delta:.6f} EGP)")
            h2_pass = True
        elif h2_delta < _H2_DELTA_MICRO:
            print(f"  {_INFO} H2 PASS: micro-drift {h2_delta:,.4f} EGP — acceptable (< {_H2_DELTA_MICRO:,.0f} EGP)")
            print("         Note: may reflect rounding in Odoo Studio field computation.")
            print("         PATH A formula is safe to use.")
            h2_pass = True
        else:
            print(f"  {_FAIL} H2 FAIL: identity mismatch {_egp(h2_delta)} ≥ {_egp(_H2_DELTA_MICRO)}")
            print("         SUM(amount) - SUM(actual_paid) does NOT equal SUM(total_due).")
            print(f"         SUM(amount) - SUM(actual_paid) = {_egp(derived_path_a)}")
            print(f"         SUM({total_due_field}) = {_egp(sum_total_due)}")
            print("         PATH A formula is NOT safe. Decision 11.13 must be re-evaluated.")
            print("         STOP — report to Khaled before any code change.")
            h2_pass = False
            failures.append(f"H2_delta_{h2_delta:.2f}")
        print()

        # ─────────────────────────────────────────────────────────────────────
        # Section 4 — H3: cheques > 0 AND ≈ SUM(check_pending_amount) (RPC 2)
        # ─────────────────────────────────────────────────────────────────────
        print(_SEP)
        print("SECTION 4 — H3: SUM(paid) − SUM(actual_paid) > 0")
        print("            AND ≈ SUM(check_pending_amount) on same domain")
        print(_SEP)
        print()
        print("  4a. Standalone read_group(late_domain, [check_pending_amount], [])")
        print("      Purpose: cross-check derived cheques against Odoo computed field.")

        t0 = time.monotonic()
        standalone_chq: float | None = None
        try:
            chq_rows = await client.execute_kw(
                _MODEL,
                "read_group",
                args=[late_domain, ["check_pending_amount"], []],
                kwargs={"lazy": False},
            )
            ms_4 = int((time.monotonic() - t0) * 1000)
            chq_row = chq_rows[0] if chq_rows else {}
            standalone_chq = float(chq_row.get("check_pending_amount") or 0)
            print(f"      → {_egp(standalone_chq)}  ({ms_4} ms)")
        except Exception as exc:
            ms_4 = int((time.monotonic() - t0) * 1000)
            print(f"      {_WARN} standalone check_pending_amount RPC failed ({ms_4} ms): {exc}")
            print("         H3 cross-check cannot be completed. Treating as FAIL.")
            h3_pass = False
            failures.append("H3_standalone_rpc_failed")

        print()

        if standalone_chq is not None:
            cheques_raw = sum_paid_amount - sum_actual_paid
            h3_condition1 = cheques_raw > 0
            h3_parity_delta = abs(derived_cheques - standalone_chq)
            h3_condition2 = h3_parity_delta < _H3_PARITY_TOLERANCE

            print("  H3 — Condition 1: SUM(paid_amount) − SUM(actual_paid_amount) > 0")
            print(f"    SUM(paid_amount)             = {_egp(sum_paid_amount)}")
            print(f"    SUM(actual_paid_amount)      = {_egp(sum_actual_paid)}")
            print(f"    Derived (paid - actual)      = {_egp(cheques_raw)}")
            print(f"    Derived clamped ≥ 0          = {_egp(derived_cheques)}")

            if h3_condition1:
                print(f"  {_PASS} Condition 1 PASS: {_egp(cheques_raw)} > 0 — cheques in pipeline confirmed")
            else:
                print(f"  {_FAIL} Condition 1 FAIL: paid − actual_paid ≤ 0")
                print("         No cheques in pipeline at this moment.")
                print("         H3 requires > 0. This may be a transient data state.")
                failures.append("H3_cond1_no_cheques")
            print()

            print("  H3 — Condition 2: derived ≈ SUM(check_pending_amount)")
            print(f"    Derived cheques (from RPC 1) = {_egp(derived_cheques)}")
            print(f"    Stored  (check_pending_amount) = {_egp(standalone_chq)}")
            print(f"    Parity delta                 = {h3_parity_delta:.4f} EGP")
            print(f"    Tolerance                    = {_egp(_H3_PARITY_TOLERANCE)}")

            if h3_condition2:
                print(f"  {_PASS} Condition 2 PASS: derived ≈ stored (delta = {h3_parity_delta:.4f} EGP)")
            else:
                print(f"  {_FAIL} Condition 2 FAIL: parity delta {_egp(h3_parity_delta)} ≥ {_egp(_H3_PARITY_TOLERANCE)}")
                print("         Service formula (paid - actual_paid) does NOT match Odoo computed field.")
                print("         This is a data quality anomaly. STOP and report to Khaled.")
                failures.append(f"H3_parity_delta_{h3_parity_delta:.2f}")
            print()

            h3_pass = h3_condition1 and h3_condition2
            if h3_pass:
                print(f"  {_PASS} H3 PASS: both conditions hold")
            else:
                print(f"  {_FAIL} H3 FAIL: one or more conditions failed (see above)")
        print()

        # ─────────────────────────────────────────────────────────────────────
        # Section 5 — H4: record_count invariant
        # ─────────────────────────────────────────────────────────────────────
        print(_SEP)
        print("SECTION 5 — H4: record_count invariant (domain-only, formula-independent)")
        print(_SEP)
        print()
        print("  H4 asserts the KPI 2 redefinition is a VALUE change, not a DOMAIN change.")
        print("  The Late domain is the same 3-clause Candidate C domain regardless of")
        print("  whether the value is computed as due_amount or amount − actual_paid_amount.")
        print("  Therefore record_count (= __count from read_group) is identical under")
        print("  both formulas — it is determined solely by the domain.")
        print()
        print(f"    record_count from RPC 1 (single read_group, Late domain): {record_count:,}")
        print()

        if record_count > 0:
            print(f"  {_PASS} H4 PASS: record_count = {record_count:,} > 0")
            print("         Domain is valid and populated. Count is formula-independent")
            print("         (domain unchanged between PATH C and PATH A).")
            h4_pass = True
        else:
            print(f"  {_FAIL} H4 FAIL: record_count = 0")
            print("         No Late records found. Domain may be wrong or data is empty.")
            print("         STOP — report to Khaled. This is unexpected.")
            h4_pass = False
            failures.append("H4_record_count_zero")
        print()

    # End async with OdooClient

    # ─────────────────────────────────────────────────────────────────────────
    # Section 6 — Gate Verdict
    # ─────────────────────────────────────────────────────────────────────────
    print(_SEP)
    print("SECTION 6 — GATE VERDICT")
    print(_SEP)
    print()
    print("  Hypothesis results:")
    print(f"    H1 (PATH C identity — amount = paid + due)     : {'PASS' if h1_pass else 'FAIL'}")
    print(f"    H2 (PATH A identity — amount = actual + total) : {'PASS' if h2_pass else 'FAIL'}")
    print(f"    H3 (cheques > 0 AND derived ≈ stored)          : {'PASS' if h3_pass else 'FAIL'}")
    print(f"    H4 (record_count invariant)                    : {'PASS' if h4_pass else 'FAIL'}")
    print()

    all_pass = h1_pass and h2_pass and h3_pass and h4_pass

    if all_pass:
        print(f"  ┌{'─' * 76}┐")
        print(f"  │{'GATE PASS — proceed to Phase B':^76}│")
        print(f"  │{'':^76}│")
        print(f"  │{'Decision 11.13 PATH A is verified safe for KPI 2 redefinition.':^76}│")
        print(f"  │{'All 4 hypotheses PASS. Backend service formula change may proceed.':^76}│")
        print(f"  └{'─' * 76}┘")
        exit_code = 0
    else:
        _print_gate_fail(failures)
        exit_code = 1

    print()

    # ─────────────────────────────────────────────────────────────────────────
    # Section 7 — Phase Complete Summary
    # ─────────────────────────────────────────────────────────────────────────
    print(_SEP)
    print("SECTION 7 — PHASE COMPLETE")
    print(_SEP)
    print()
    print("  3 RPCs executed. No writes performed.")
    print()
    print("  RPC budget:")
    print(f"    RPC 0  fields_get(rs.installment)               → {len(all_fields)} fields")
    print(f"    RPC 1  read_group(late_domain, 5 fields)         → {record_count:,} records")
    chq_val = standalone_chq if standalone_chq is not None else 0.0
    print(f"    RPC 2  read_group(check_pending_amount)          → {_egp(chq_val)}")
    print()
    print("  Key values captured:")
    print(f"    PATH C value  (due_amount)           : {_egp(derived_path_c)}")
    print(f"    PATH A value  (amount - actual_paid) : {_egp(derived_path_a)}")
    print(f"    Delta (PATH A − PATH C)              : {_egp(derived_path_a - derived_path_c)}")
    print(f"    Cheques in pipeline                  : {_egp(derived_cheques)}")
    print(f"    Total due field name used            : '{total_due_field}'")
    print()
    print(f"  Gate: {'PASS' if all_pass else 'FAIL'}  |  Failures: {failures if failures else 'none'}")
    print()
    print(f"  Output teed to: {_OUTPUT_FILE}")
    print()
    print(_SEP)
    print("  STAGE 2.5 PRE-IMPLEMENTATION DISCOVERY — PHASE COMPLETE")
    print(_SEP)

    return exit_code


def _print_gate_fail(failures: list[str]) -> None:
    print(f"  ┌{'─' * 76}┐")
    print(f"  │{'GATE FAIL — STOP. Do NOT proceed to Phase B.':^76}│")
    print(f"  │{'':^76}│")
    print(f"  │{'Await Khaled decision before any backend service code change.':^76}│")
    print(f"  └{'─' * 76}┘")
    print()
    print("  Failed hypotheses:")
    for f in failures:
        print(f"    - {f}")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tee = _Tee(_OUTPUT_FILE)
    sys.stdout = tee
    try:
        exit_code = asyncio.run(main())
    finally:
        sys.stdout = tee._stdout
        tee.close()
    sys.exit(exit_code)
