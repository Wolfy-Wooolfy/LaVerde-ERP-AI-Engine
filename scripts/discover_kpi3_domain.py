"""
Read-only pre-implementation discovery for KPI 3 — Pending Check Exposure.

Goal: Determine the correct domain for KPI 3 by comparing the derived
formula (SUM(paid_amount) - SUM(x_studio_actual_paid_amount)) and the
stored field (SUM(check_pending_amount)) across candidate domains, then
identify which domain's derived value is closest to the 2026-05-14
baseline of 520,455,684.10 EGP.

Tests run:
  1. domain=[] (empty — as originally specified in MVP Design §3.2 KPI 3)
  2. domain=[('state','=','post')] — consistent with KPI 1 (Decision 2.4)
  3. Cross-domain delta — derived and stored comparison across both domains
  4. Per-state breakdown — read_group by state, domain=[]

This script:
  - Calls ONLY read methods (read_group — 3 RPCs total).
  - Writes nothing to Odoo.
  - Costs $0 in AI.
  - Prints no PII (no customer names, IDs, or addresses).
  - Appends one TSV row to logs/kpi3_discovery.log.
  - Exits 0 on completion regardless of findings.

Usage:
    python scripts/discover_kpi3_domain.py
"""

import asyncio
import io
import os
import sys
from datetime import datetime, timezone

from backend.shared.odoo.client import OdooClient

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_MODEL = "rs.installment"
_SEP = "═" * 76
_SEP2 = "─" * 76
_LOG_FILE = "logs/kpi3_discovery.log"

# 2026-05-14 baseline — derived from Business Context §9 All Installments snapshot:
#   SUM(paid_amount) = 3,491,180,448.95
#   SUM(x_studio_actual_paid_amount) = 2,970,724,764.85
#   Derived = 520,455,684.10 EGP
# The "All Installments" Odoo UI view uses state='post' (Decision 2.4), so this
# baseline corresponds to the state='post' domain. Test 1 vs Test 2 will confirm
# whether draft/cancel records carry non-zero paid amounts that would change the result.
_BASELINE_EGP = 520_455_684.10
_BASELINE_DATE = "2026-05-14"

# All three fields aggregated in every non-grouped read_group call.
# Decision 1.4: use derived formula (paid - actual), NOT check_pending_amount directly.
_AGG_FIELDS = ["paid_amount", "x_studio_actual_paid_amount", "check_pending_amount"]

_INFO = "[INFO]"
_PASS = "[PASS]"
_FLAG = "[FLAG]"


def _egp(v: float) -> str:
    return f"{v:>26,.2f} EGP"


def _derived(paid: float, actual: float) -> float:
    return paid - actual


def _rg_scalars(rows: list) -> tuple[float, float, float, int]:
    """Extract (paid_amount, actual_paid, check_pending, count) from the first read_group row."""
    row = rows[0] if rows else {}
    paid = float(row.get("paid_amount") or 0.0)
    actual = float(row.get("x_studio_actual_paid_amount") or 0.0)
    stored = float(row.get("check_pending_amount") or 0.0)
    count = int(row.get("__count") or 0)
    return paid, actual, stored, count


def _append_tsv(
    run_at: str,
    derived_empty: float,
    derived_post: float,
    stored_empty: float,
    stored_post: float,
    count_empty: int,
    count_post: int,
) -> None:
    os.makedirs("logs", exist_ok=True)
    log_exists = os.path.isfile(_LOG_FILE)
    with open(_LOG_FILE, "a", encoding="utf-8") as f:
        if not log_exists:
            f.write(
                "run_at\t"
                "derived_empty_egp\tderived_post_egp\t"
                "stored_empty_egp\tstored_post_egp\t"
                "delta_derived_vs_stored_empty\tdelta_derived_vs_stored_post\t"
                "delta_vs_baseline_empty\tdelta_vs_baseline_post\t"
                "count_empty\tcount_post\tbaseline_egp\n"
            )
        f.write(
            f"{run_at}\t"
            f"{derived_empty:.2f}\t{derived_post:.2f}\t"
            f"{stored_empty:.2f}\t{stored_post:.2f}\t"
            f"{derived_empty - stored_empty:.2f}\t{derived_post - stored_post:.2f}\t"
            f"{derived_empty - _BASELINE_EGP:.2f}\t{derived_post - _BASELINE_EGP:.2f}\t"
            f"{count_empty}\t{count_post}\t{_BASELINE_EGP:.2f}\n"
        )
    print(f"\n{_INFO} TSV row appended to {_LOG_FILE}")


async def run() -> None:
    run_at = datetime.now(timezone.utc).isoformat()

    print(_SEP)
    print("KPI 3 — Pending Check Exposure: Domain Discovery")
    print(f"Run timestamp  : {run_at}")
    print(f"Baseline       : {_egp(_BASELINE_EGP)} ({_BASELINE_DATE})")
    print(f"Formula        : SUM(paid_amount) - SUM(x_studio_actual_paid_amount)")
    print(f"NOT used       : SUM(check_pending_amount) — differs by ~2.47M EGP (Phase 2 §4)")
    print(f"RPCs planned   : 3 (Test 1: 1, Test 2: 1, Test 4: 1)")
    print(_SEP)

    async with OdooClient() as client:

        # ── Test 1 — Empty domain ─────────────────────────────────────────────
        print("\n[TEST 1] domain=[] (no state filter — as in MVP Design §3.2 KPI 3)")
        print(_SEP2)
        rows_a = await client.execute_kw(
            _MODEL, "read_group",
            args=[[], _AGG_FIELDS, []],
            kwargs={"lazy": False},
        )
        paid_a, actual_a, stored_a, count_a = _rg_scalars(rows_a)
        derived_a = _derived(paid_a, actual_a)
        delta_baseline_a = derived_a - _BASELINE_EGP
        delta_stored_a = derived_a - stored_a

        print(f"    Record count                     : {count_a:>12,}")
        print(f"    SUM(paid_amount)                 : {_egp(paid_a)}")
        print(f"    SUM(x_studio_actual_paid_amount) : {_egp(actual_a)}")
        print(f"    Derived (paid - actual)          : {_egp(derived_a)}")
        print(f"    SUM(check_pending_amount)        : {_egp(stored_a)}")
        print(f"    Delta: derived vs stored         : {delta_stored_a:>+26,.2f} EGP")
        print(f"    Delta: derived vs baseline       : {delta_baseline_a:>+26,.2f} EGP")

        # ── Test 2 — state='post' domain ─────────────────────────────────────
        print("\n[TEST 2] domain=[('state','=','post')] — consistent with KPI 1 (Decision 2.4)")
        print(_SEP2)
        rows_b = await client.execute_kw(
            _MODEL, "read_group",
            args=[[("state", "=", "post")], _AGG_FIELDS, []],
            kwargs={"lazy": False},
        )
        paid_b, actual_b, stored_b, count_b = _rg_scalars(rows_b)
        derived_b = _derived(paid_b, actual_b)
        delta_baseline_b = derived_b - _BASELINE_EGP
        delta_stored_b = derived_b - stored_b

        print(f"    Record count                     : {count_b:>12,}")
        print(f"    SUM(paid_amount)                 : {_egp(paid_b)}")
        print(f"    SUM(x_studio_actual_paid_amount) : {_egp(actual_b)}")
        print(f"    Derived (paid - actual)          : {_egp(derived_b)}")
        print(f"    SUM(check_pending_amount)        : {_egp(stored_b)}")
        print(f"    Delta: derived vs stored         : {delta_stored_b:>+26,.2f} EGP")
        print(f"    Delta: derived vs baseline       : {delta_baseline_b:>+26,.2f} EGP")

        # ── Test 3 — Cross-domain comparison (pure Python, no RPC) ────────────
        print("\n[TEST 3] Cross-domain delta: domain=[] minus domain=state='post'")
        print(_SEP2)
        diff_count = count_a - count_b
        diff_paid = paid_a - paid_b
        diff_actual = actual_a - actual_b
        diff_derived = derived_a - derived_b
        diff_stored = stored_a - stored_b
        print(f"    Additional records in domain=[]  : {diff_count:>+12,}")
        print(f"    Delta paid_amount                : {diff_paid:>+26,.2f} EGP")
        print(f"    Delta actual_paid                : {diff_actual:>+26,.2f} EGP")
        print(f"    Delta derived                    : {diff_derived:>+26,.2f} EGP")
        print(f"    Delta check_pending_amount       : {diff_stored:>+26,.2f} EGP")
        print()
        if abs(diff_derived) < 1.0:
            print(f"    {_PASS} |Delta derived| < 1 EGP — draft/cancel records carry")
            print(f"           no paid amounts; domain choice is immaterial for KPI 3.")
        elif abs(diff_derived) < 1_000_000:
            print(f"    {_FLAG} |Delta derived| = {abs(diff_derived):,.2f} EGP — small but non-zero.")
            print(f"           Draft/cancel records carry some paid amounts. Khaled to decide.")
        else:
            print(f"    {_FLAG} |Delta derived| = {abs(diff_derived):,.2f} EGP — domain matters.")
            print(f"           Significant paid amounts in non-post records. Investigate before D1.")

        # ── Test 4 — Per-state breakdown ──────────────────────────────────────
        print("\n[TEST 4] Per-state breakdown: read_group(domain=[], groupby=['state'])")
        print(_SEP2)
        rows_states = await client.execute_kw(
            _MODEL, "read_group",
            args=[[], _AGG_FIELDS, ["state"]],
            kwargs={"lazy": False},
        )
        col = 20
        print(
            f"    {'State':<10} {'Count':>8}  "
            f"{'paid_amount':>{col}}  {'actual_paid':>{col}}  "
            f"{'Derived':>{col}}  {'check_pending':>{col}}"
        )
        print(f"    {'-'*10} {'-'*8}  {'-'*col}  {'-'*col}  {'-'*col}  {'-'*col}")

        for row in rows_states:
            state = row.get("state", "?")
            cnt = int(row.get("__count") or 0)
            paid = float(row.get("paid_amount") or 0.0)
            actual = float(row.get("x_studio_actual_paid_amount") or 0.0)
            stored = float(row.get("check_pending_amount") or 0.0)
            drv = _derived(paid, actual)
            print(
                f"    {state!r:<10} {cnt:>8,}  "
                f"{paid:>{col},.2f}  {actual:>{col},.2f}  "
                f"{drv:>{col},.2f}  {stored:>{col},.2f}"
            )

        # ── Final summary ─────────────────────────────────────────────────────
        abs_a = abs(delta_baseline_a)
        abs_b = abs(delta_baseline_b)
        closer = "[]" if abs_a <= abs_b else "[('state','=','post')]"

        print(f"\n{_SEP}")
        print("DISCOVERY SUMMARY")
        print(_SEP2)
        print(f"  Baseline ({_BASELINE_DATE})                      : {_egp(_BASELINE_EGP)}")
        print(f"  (Baseline source: Odoo 'All Installments' UI, state='post' confirmed by Decision 2.4)")
        print()
        print(f"  domain=[]:")
        print(f"    Records          : {count_a:>12,}")
        print(f"    Derived          : {_egp(derived_a)}")
        print(f"    |Delta baseline| : {_egp(abs_a)}")
        print(f"    |Delta stored|   : {_egp(abs(delta_stored_a))}")
        print()
        print(f"  domain=[('state','=','post')]:")
        print(f"    Records          : {count_b:>12,}")
        print(f"    Derived          : {_egp(derived_b)}")
        print(f"    |Delta baseline| : {_egp(abs_b)}")
        print(f"    |Delta stored|   : {_egp(abs(delta_stored_b))}")
        print()
        print(f"  Numerically closest to 2026-05-14 baseline : domain={closer}")
        print(_SEP)
        print()
        print("CHECKPOINT 1 — STOP. Do not proceed to D1 until Khaled:")
        print("  1. Reviews the per-domain derived values above.")
        print("  2. Opens Odoo → Collections Mgmt → All Installments.")
        print("     Sets Measures to: Paid Amount AND Actual Paid Amount.")
        print("     Manually computes: (Paid Amount total) − (Actual Paid Amount total).")
        print("     Compares to the state='post' derived value above.")
        print("  3. Decides which domain to use for KPI 3 (Decision 4.1).")
        print("  4. Gives explicit approval before D1 begins.")
        print(_SEP)

    _append_tsv(
        run_at=run_at,
        derived_empty=derived_a,
        derived_post=derived_b,
        stored_empty=stored_a,
        stored_post=stored_b,
        count_empty=count_a,
        count_post=count_b,
    )


if __name__ == "__main__":
    asyncio.run(run())
