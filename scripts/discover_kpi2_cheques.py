"""
scripts/discover_kpi2_cheques.py — KPI 2 Cheques Distribution Mini-Discovery

Determines how many late installments carry check records and quantifies
cheques_in_pipeline to decide Stage 2 KPI 2 implementation path.

7 sections:
  Section 0   — Header / Setup
  Section 1   — KPI 2 universe baseline (late domain totals)
  Section 1.5 — Cheques-posting workflow rate (zero new RPCs)
  Section 2   — Statistical proof: cheques on late (e_pre / e_post / e_calc)
  Section 3   — Sample inspection (5 late + has_checks=True records)
  Section 4   — Parity check: derived vs stored check_pending_amount
  Section 5   — PATH recommendation
  Section 6   — Manual cross-check sheet for Khaled
  Section 7   — PHASE COMPLETE

Hard constraints:
  - READ-ONLY. No writes. ALLOWED_METHODS unchanged.
  - No PII: no customer names, partner names, or partner IDs in output.
  - No OpenAI calls. AI cost = $0.00.
  - No Stage 2 KPI 2 service code.
  - Tees stdout to scripts/discover_kpi2_cheques_output.txt.

Usage (from project root):
    $env:PYTHONPATH = "."; C:\\Python310\\python.exe scripts/discover_kpi2_cheques.py
"""

import asyncio
import io
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient

# Force UTF-8 stdout (Windows consoles default to cp1252).
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Constants ─────────────────────────────────────────────────────────────────

_MODEL       = "rs.installment"
_LA_VERDE_TZ = ZoneInfo("Africa/Cairo")
_OUTPUT_FILE = Path(__file__).parent / "discover_kpi2_cheques_output.txt"

_FORBIDDEN_WRITE_METHODS = frozenset({"create", "write", "unlink"})

# Baseline from MODULE_2_BUSINESS_CONTEXT §9 (snapshot 2026-05-14):
# Paid Amount = Actual Paid Amount on the late set → cheques_in_pipeline = 0.
_BASELINE_DATE        = date(2026, 5, 14)
_BASELINE_CHEQUES_EGP = 0.0

_SEP  = "═" * 78
_SEP2 = "─" * 76
_PASS = "[PASS]"
_FLAG = "[FLAG]"
_INFO = "[INFO]"


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


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
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

    # ─────────────────────────────────────────────────────────────────────────
    # Section 0 — Header / Setup
    # ─────────────────────────────────────────────────────────────────────────
    print(_SEP)
    print("  KPI 2 Cheques Distribution — Mini-Discovery")
    print(f"  Run at (UTC)  : {run_at_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  Run at (Cairo): {run_at_cairo.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"  Today (Cairo) : {today_str}")
    print(f"  ALLOWED_METHODS: {sorted(ALLOWED_METHODS)}")
    print("  READ-ONLY. No writes. No OpenAI. AI cost = $0.00")
    print(_SEP)
    print()
    print("  Purpose: determine cheques distribution on LATE installments.")
    print("  Outcome determines Stage 2 KPI 2 implementation path (PATH A vs PATH C analog).")
    print(f"  late_domain = {late_domain}")
    print()

    async with OdooClient() as client:

        # ─────────────────────────────────────────────────────────────────────
        # Section 1 — KPI 2 Universe Baseline
        # ─────────────────────────────────────────────────────────────────────
        print(_SEP)
        print("SECTION 1 — KPI 2 Universe Baseline")
        print(_SEP)
        print()

        # RPC 1 — total count
        print("  1a. search_count(late_domain)")
        t0 = time.monotonic()
        total_late_count: int = await client.execute_kw(
            _MODEL, "search_count", args=[late_domain], kwargs={}
        )
        ms_1a = int((time.monotonic() - t0) * 1000)
        print(f"      total_late_count : {total_late_count:,}  ({ms_1a} ms)")
        print()

        # RPC 2 — combined read_group with all 5 monetary fields
        print("  1b. read_group(late_domain,")
        print("                 fields=[amount, due_amount, paid_amount,")
        print("                         x_studio_actual_paid_amount, check_pending_amount])")
        t0 = time.monotonic()
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
                    "check_pending_amount",
                ],
                [],
            ],
            kwargs={"lazy": False},
        )
        ms_1b = int((time.monotonic() - t0) * 1000)

        row = rg_rows[0] if rg_rows else {}
        sum_amount          = float(row.get("amount",                      0) or 0)
        sum_due_amount      = float(row.get("due_amount",                  0) or 0)
        sum_paid_amount     = float(row.get("paid_amount",                 0) or 0)
        sum_actual_paid     = float(row.get("x_studio_actual_paid_amount", 0) or 0)
        combined_stored_chq = float(row.get("check_pending_amount",        0) or 0)

        derived_cheques    = max(sum_paid_amount - sum_actual_paid, 0.0)
        cheques_pct_of_amt = (derived_cheques / sum_amount * 100) if sum_amount > 0 else 0.0

        print(f"      read_group returned {len(rg_rows)} row(s)  ({ms_1b} ms)")
        print()
        print("  Universe sums:")
        print(f"    SUM(amount)                      : {_egp(sum_amount)}")
        print(f"    SUM(due_amount)                  : {_egp(sum_due_amount)}")
        print(f"    SUM(paid_amount)                 : {_egp(sum_paid_amount)}")
        print(f"    SUM(x_studio_actual_paid_amount) : {_egp(sum_actual_paid)}")
        print(f"    SUM(check_pending_amount) [RPC 2]: {_egp(combined_stored_chq)}")
        print()
        print("  Derived cheques_in_pipeline:")
        print(f"    = max(SUM(paid_amount) - SUM(actual_paid), 0)")
        print(f"    = max({_egp(sum_paid_amount)} - {_egp(sum_actual_paid)}, 0)")
        print(f"    = {_egp(derived_cheques)}")
        print(f"    As % of SUM(amount): {cheques_pct_of_amt:.2f}%")
        print()

        # ─────────────────────────────────────────────────────────────────────
        # Section 1.5 — Cheques-Posting Workflow Rate (zero new RPCs)
        # ─────────────────────────────────────────────────────────────────────
        print(_SEP)
        print("SECTION 1.5 — Cheques-Posting Workflow Rate (Zero New RPCs)")
        print(_SEP)
        print()

        days_elapsed = (today_cairo - _BASELINE_DATE).days
        daily_rate   = (derived_cheques / days_elapsed) if days_elapsed > 0 else 0.0

        print(f"  Baseline snapshot date       : {_BASELINE_DATE.isoformat()}")
        print(f"  Today                        : {today_str}")
        print(f"  Days elapsed                 : {days_elapsed}")
        print(f"  Baseline cheques_in_pipeline : {_egp(_BASELINE_CHEQUES_EGP)}")
        print("    (MODULE_2_BUSINESS_CONTEXT §9: Paid = Actual on late set at baseline)")
        print(f"  Today cheques_in_pipeline    : {_egp(derived_cheques)}")
        print(f"  Apparent flow ({days_elapsed} days)       : {_egp(derived_cheques)}")
        print(f"  Apparent daily rate          : {_egp(daily_rate)}/day")
        print()
        print("  Note: illustrative only — not predictive.")
        print("  Reflects cheques posted against late installments in the observation window.")
        print("  A reliable rate estimate requires a longer observation window.")
        print()

        # ─────────────────────────────────────────────────────────────────────
        # Section 2 — Statistical Proof: Cheques on Late Installments
        # ─────────────────────────────────────────────────────────────────────
        print(_SEP)
        print("SECTION 2 — Statistical Proof: Cheques on Late Installments")
        print("            (mirrors Phase 0.5 Section 1 e_pre / e_post / e_calc pattern)")
        print(_SEP)
        print()

        late_with_checks_domain = late_domain + [("has_checks", "=", True)]

        # e_pre — reuse Section 1 value (no new RPC)
        print(f"  e_pre.  total_late_count (reused from Section 1 — no new RPC): {total_late_count:,}")
        print()

        # RPC 3 — late + has_checks=True
        print("  e_post. search_count(late_domain + [('has_checks', '=', True)])")
        print(f"          domain: {late_with_checks_domain}")
        t0 = time.monotonic()
        late_with_checks: int = await client.execute_kw(
            _MODEL, "search_count", args=[late_with_checks_domain], kwargs={}
        )
        ms_2 = int((time.monotonic() - t0) * 1000)
        print(f"          result: {late_with_checks:,} late installments with checks  ({ms_2} ms)")
        print()

        late_with_checks_pct = (
            late_with_checks / total_late_count * 100 if total_late_count > 0 else 0.0
        )

        print("  e_calc. Statistical interpretation:")
        print(f"    total_late_count     : {total_late_count:,}")
        print(f"    late_with_checks     : {late_with_checks:,}")
        print(f"    late_with_checks_pct : {late_with_checks_pct:.2f}%")
        print()
        print("  Threshold table:")
        print("    >= 30%      PATH A        — cheques annotation warranted (Stage 2 as planned)")
        print("    10% – 30%   PATH MIXED    — present evidence to Khaled, decide jointly")
        print("    < 10%       PATH C analog — recommend removing cheques annotation from KPI 2")
        print()
        if late_with_checks_pct >= 30:
            print(f"  → {late_with_checks_pct:.2f}% ≥ 30%           → PATH A")
        elif late_with_checks_pct >= 10:
            print(f"  → {late_with_checks_pct:.2f}% in [10%, 30%)   → PATH MIXED")
        else:
            print(f"  → {late_with_checks_pct:.2f}% < 10%           → PATH C analog")
        print()

        # ─────────────────────────────────────────────────────────────────────
        # Section 3 — Sample Inspection
        # ─────────────────────────────────────────────────────────────────────
        print(_SEP)
        print("SECTION 3 — Sample Inspection (up to 5 late + has_checks=True records)")
        print(_SEP)
        print()

        # RPC 4 — search_read
        print("  3a. search_read(late_domain + has_checks=True, limit=5, sanitized — id only)")
        t0 = time.monotonic()
        samples = []
        try:
            samples = await client.execute_kw(
                _MODEL,
                "search_read",
                args=[late_with_checks_domain],
                kwargs={
                    "fields": [
                        "id",
                        "date",
                        "amount",
                        "paid_amount",
                        "x_studio_actual_paid_amount",
                        "check_ids",
                        "check_pending_amount",
                        "all_checks_collected",
                    ],
                    "limit": 5,
                },
            )
            ms_3 = int((time.monotonic() - t0) * 1000)
            print(f"      → {len(samples)} record(s) returned  ({ms_3} ms)")
        except Exception as exc:
            ms_3 = int((time.monotonic() - t0) * 1000)
            print(f"      {_FLAG} search_read failed ({ms_3} ms): {exc}")
        print()

        if samples:
            print(
                f"  {'id':>8}  {'date':<12}  {'amount':>14}  {'paid':>14}  "
                f"{'actual_paid':>14}  {'chk_pend':>12}  {'n_checks':>8}  {'all_coll':>8}"
            )
            print(f"  {'─'*8}  {'─'*12}  {'─'*14}  {'─'*14}  {'─'*14}  {'─'*12}  {'─'*8}  {'─'*8}")
            for rec in samples:
                n_chk = len(rec.get("check_ids") or [])
                print(
                    f"  {rec['id']:>8}  "
                    f"{str(rec.get('date', '')):<12}  "
                    f"{float(rec.get('amount', 0) or 0):>14,.2f}  "
                    f"{float(rec.get('paid_amount', 0) or 0):>14,.2f}  "
                    f"{float(rec.get('x_studio_actual_paid_amount', 0) or 0):>14,.2f}  "
                    f"{float(rec.get('check_pending_amount', 0) or 0):>12,.2f}  "
                    f"{n_chk:>8}  "
                    f"{str(rec.get('all_checks_collected', False)):>8}"
                )
            print()
            paid_nonzero    = sum(1 for r in samples if float(r.get("paid_amount", 0) or 0) > 0)
            checks_nonempty = sum(1 for r in samples if len(r.get("check_ids") or []) > 0)
            print(f"  Sanity checks on {len(samples)} samples:")
            ok_paid   = _PASS if paid_nonzero > 0 else f"{_FLAG} unexpected: all paid_amount = 0"
            ok_checks = _PASS if checks_nonempty == len(samples) else f"{_FLAG} some check_ids empty despite has_checks=True"
            print(f"    paid_amount > 0     : {paid_nonzero}/{len(samples)}  {ok_paid}")
            print(f"    check_ids non-empty : {checks_nonempty}/{len(samples)}  {ok_checks}")
        else:
            print(f"  {_INFO} No samples returned (late_with_checks={late_with_checks:,})")
        print()

        # ─────────────────────────────────────────────────────────────────────
        # Section 4 — Parity Check: Derived vs Stored check_pending_amount
        # ─────────────────────────────────────────────────────────────────────
        print(_SEP)
        print("SECTION 4 — Parity Check: Derived vs Stored check_pending_amount")
        print(_SEP)
        print()
        print("  From combined read_group (RPC 2):")
        print(f"    combined_stored_chq : {_egp(combined_stored_chq)}")
        print(f"    derived formula     : {_egp(derived_cheques)}")
        print()

        # RPC 5 — standalone validation read_group
        print("  4a. Defensive standalone read_group(check_pending_amount only)")
        print("      Purpose: confirm combined RG aggregated check_pending_amount correctly.")
        t0 = time.monotonic()
        standalone_stored: Optional[float] = None
        try:
            standalone_rows = await client.execute_kw(
                _MODEL,
                "read_group",
                args=[late_domain, ["check_pending_amount"], []],
                kwargs={"lazy": False},
            )
            ms_4 = int((time.monotonic() - t0) * 1000)
            standalone_stored = float(
                (standalone_rows[0].get("check_pending_amount") or 0)
                if standalone_rows else 0
            )
            print(f"      standalone_stored : {_egp(standalone_stored)}  ({ms_4} ms)")
        except Exception as exc:
            ms_4 = int((time.monotonic() - t0) * 1000)
            print(f"      {_FLAG} Standalone read_group failed ({ms_4} ms): {exc}")
        print()

        # Assert combined vs standalone
        authoritative_stored: float
        if standalone_stored is not None:
            delta_rg = abs(combined_stored_chq - standalone_stored)
            if delta_rg < 0.01:
                print(f"  {_PASS} Combined RG ≈ Standalone RG  (delta = {delta_rg:.4f} EGP)")
                print("        Combined read_group aggregation is TRUSTWORTHY.")
                authoritative_stored = combined_stored_chq
            else:
                print(f"  {_FLAG} Combined RG vs Standalone RG delta = {_egp(delta_rg)} — UNRELIABLE")
                print(f"        Using standalone value for PATH recommendation: {_egp(standalone_stored)}")
                authoritative_stored = standalone_stored
        else:
            print(f"  {_INFO} Standalone RPC failed — using combined value as authoritative.")
            authoritative_stored = combined_stored_chq
        print()

        # Assert derived vs authoritative stored
        delta_dv = abs(derived_cheques - authoritative_stored)
        if delta_dv < 1.0:
            print(f"  {_PASS} Derived ({_egp(derived_cheques)}) ≈ Stored ({_egp(authoritative_stored)})")
            print(f"        delta = {delta_dv:.4f} EGP — service formula is CONSISTENT with Odoo computed field.")
        else:
            print(f"  {_FLAG} ANOMALY: Derived vs Stored delta = {_egp(delta_dv)}")
            print(f"        Derived (paid - actual_paid)  : {_egp(derived_cheques)}")
            print(f"        Stored  (check_pending_amount): {_egp(authoritative_stored)}")
            print("        Service formula does NOT match the Odoo computed field.")
            print("        STOP — report to Khaled before Stage 2.")
        print()

        # ─────────────────────────────────────────────────────────────────────
        # Section 5 — PATH Recommendation
        # ─────────────────────────────────────────────────────────────────────
        print(_SEP)
        print("SECTION 5 — PATH Recommendation")
        print(_SEP)
        print()

        triggers_path_a = (late_with_checks_pct >= 10.0) or (derived_cheques >= 10_000_000.0)
        triggers_path_c = (late_with_checks_pct < 5.0) and (derived_cheques < 5_000_000.0)

        if triggers_path_a:
            recommendation = "PATH A — KEEP CHEQUES ANNOTATION"
            detail_lines = [
                "Cheques distribution on late installments is SIGNIFICANT.",
                "Stage 2 should extend KPI 2 with cheques annotation as originally planned.",
            ]
        elif triggers_path_c:
            recommendation = "PATH C (analog) — SKIP CHEQUES ANNOTATION"
            detail_lines = [
                "Cheques distribution on late installments is MINIMAL.",
                "Apply PATH C to KPI 2 as well. Skip the cheques extension.",
                "Document as Session 10 decision.",
            ]
        else:
            recommendation = "PATH MIXED — PRESENT TO KHALED"
            detail_lines = [
                "Values fall in the ambiguous zone (5–10% count and/or 5M–10M EGP).",
                "Present evidence to Khaled for a joint decision before Stage 2.",
            ]

        print("  Evidence:")
        print(f"    [1] total_late_count        : {total_late_count:,}")
        print(f"    [2] late_with_checks_count  : {late_with_checks:,}")
        print(f"    [3] late_with_checks_pct    : {late_with_checks_pct:.2f}%"
              "  (threshold: A >= 10%, C < 5%)")
        print(f"    [4] derived_cheques_in_pipe : {_egp(derived_cheques)}"
              "  (threshold: A >= 10M EGP, C < 5M EGP)")
        print(f"    [5] stored_check_pending    : {_egp(authoritative_stored)}")
        print(f"    [6] stored_vs_derived_delta : {_egp(abs(derived_cheques - authoritative_stored))}")
        print()
        print(f"  ┌─ RECOMMENDATION {'─' * 57}┐")
        print(f"  │  {recommendation:<74}│")
        print(f"  ├{'─' * 76}┤")
        for line in detail_lines:
            print(f"  │  {line:<74}│")
        print(f"  └{'─' * 76}┘")
        print()

        # ─────────────────────────────────────────────────────────────────────
        # Section 6 — Manual Cross-Check Sheet
        # ─────────────────────────────────────────────────────────────────────
        print(_SEP)
        print("SECTION 6 — Manual Cross-Check Sheet (Khaled UI Verification)")
        print(_SEP)
        print()
        print("  Open: Collections Mgmt → All Installments")
        print()
        print(f"  {'Filter':<26} {'Value'}")
        print(f"  {'─' * 26} {'─' * 40}")
        print(f"  {'State':<26} Posted")
        print(f"  {'Payment Status':<26} Unpaid + Partially Paid")
        print(f"  {'Date':<26} < {today_str}  (Date before today)")
        print(f"  {'Has Checks':<26} True")
        print()
        print("  View  : Pivot")
        print("  Measures: Paid Amount + Actual Paid Amount")
        print()
        print("  Step  : subtract Paid Amount − Actual Paid Amount")
        print(f"  Expected: {_egp(derived_cheques)} ± 1 EGP")
        print()
        print("  Note: UI snapshot may differ if new cheques were posted since script run.")
        print("  Differences > 1,000 EGP should be investigated.")
        print()

        # ─────────────────────────────────────────────────────────────────────
        # Section 7 — PHASE COMPLETE
        # ─────────────────────────────────────────────────────────────────────
        print(_SEP)
        print("SECTION 7 — PHASE COMPLETE")
        print(_SEP)
        print()
        print("  Mini-discovery complete. 5 RPCs executed. No writes performed.")
        print()
        print("  RPC budget:")
        print(f"    RPC 1  search_count(late_domain)                     → {total_late_count:,} records")
        print(f"    RPC 2  read_group(late_domain, 5 monetary fields)    → sums computed")
        print(f"    RPC 3  search_count(late_domain + has_checks=True)   → {late_with_checks:,} records")
        print(f"    RPC 4  search_read(+ has_checks=True, limit=5)       → {len(samples)} sample records")
        s_val = standalone_stored if standalone_stored is not None else 0.0
        print(f"    RPC 5  standalone read_group(check_pending_amount)   → {_egp(s_val)}")
        print()
        print(f"  PATH recommendation: {recommendation}")
        print()
        print(f"  Output teed to: {_OUTPUT_FILE}")
        print()
        print(_SEP)
        print("  KPI 2 CHEQUES MINI-DISCOVERY — PHASE COMPLETE")
        print(_SEP)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tee = _Tee(_OUTPUT_FILE)
    sys.stdout = tee
    try:
        asyncio.run(main())
    finally:
        sys.stdout = tee._stdout
        tee.close()
