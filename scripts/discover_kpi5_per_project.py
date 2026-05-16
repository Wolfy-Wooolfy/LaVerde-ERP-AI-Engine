"""
Read-only pre-implementation discovery for KPI 5 — Late Uncollected per project.

Goal: Verify that read_group with groupby=['project_id'] on the validated
Candidate C Late domain returns exactly 3 projects (IDs 1, 2, 3) and that
their per-project due_amount totals sum to approximately the KPI 2 baseline
(318,626,200.40 EGP as of 2026-05-16, with drift expected from live data).

This script:
  - Calls ONLY read methods (read_group, search_count).
  - Writes nothing to Odoo.
  - Costs $0 in AI.
  - Prints no PII (no customer names, IDs, or addresses).
  - Appends a TSV row to logs/kpi5_discovery.log.
  - Exits 0 on completion regardless of findings.

Usage:
    python scripts/discover_kpi5_per_project.py
"""

import asyncio
import io
import os
import sys
from datetime import date, datetime, timezone

from backend.shared.odoo.client import OdooClient

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_MODEL = "rs.installment"
_SEP = "═" * 68
_SEP2 = "─" * 68
_LOG_FILE = "logs/kpi5_discovery.log"

# KPI 2 Session 1 baseline — used for cross-check only.
# Expected delta is non-zero due to live data movement since 2026-05-16.
_KPI2_BASELINE_EGP = 318_626_200.40
_KPI2_BASELINE_DATE = "2026-05-16"

# Known projects from Phase 2 confirmation (MODULE_2_DISCOVERY_PHASE_2.md §6).
_EXPECTED_PROJECTS = {1: "New Capital", 2: "Cassette", 3: "La puerta"}

_FLAG = "[FLAG]"
_INFO = "[INFO]"
_PASS = "[PASS]"


def _today() -> str:
    return date.today().isoformat()


def _build_late_domain(today: str) -> list:
    # Immutable — Candidate C, three-clause form validated in Phase 2 §3.
    return [
        ("state", "=", "post"),
        ("payment_state", "in", ["unpaid", "partial"]),
        ("date", "<", today),
    ]


def _egp(v: float) -> str:
    return f"{v:>22,.2f} EGP"


def _append_tsv(rows: list[dict], total_due: float, total_count: int, flags: list[str]) -> None:
    os.makedirs("logs", exist_ok=True)
    log_exists = os.path.isfile(_LOG_FILE)
    run_at = datetime.now(timezone.utc).isoformat()
    with open(_LOG_FILE, "a", encoding="utf-8") as f:
        if not log_exists:
            f.write(
                "run_at\tproject_id\tproject_name\trecord_count"
                "\tdue_amount_egp\tamount_egp\n"
            )
        for r in rows:
            f.write(
                f"{run_at}\t{r['project_id']}\t{r['project_name']}"
                f"\t{r['record_count']}\t{r['due_amount']:.2f}\t{r['amount']:.2f}\n"
            )
        # Summary row with project_id=0 as sentinel
        f.write(
            f"{run_at}\t0\tTOTAL\t{total_count}"
            f"\t{total_due:.2f}\t\n"
        )
    print(f"\n{_INFO} TSV appended to {_LOG_FILE}")
    if flags:
        print(f"{_FLAG} FLAGS raised: {flags}")


async def run() -> None:
    today = _today()
    domain = _build_late_domain(today)
    run_at = datetime.now(timezone.utc).isoformat()

    print(_SEP)
    print("KPI 5 — Late Uncollected Per-Project Discovery")
    print(f"Run timestamp : {run_at}")
    print(f"Today (domain): {today}")
    print(_SEP)
    print()
    print(f"{_INFO} Late domain: {domain}")
    print(f"{_INFO} Expected projects: {_EXPECTED_PROJECTS}")
    print(f"{_INFO} KPI 2 baseline (2026-05-16): {_egp(_KPI2_BASELINE_EGP)}")
    print()

    flags: list[str] = []

    async with OdooClient() as client:

        # ── Section 1: read_group by project_id — SUM(due_amount) ────────────
        print(_SEP2)
        print("[1] read_group(domain=Candidate C, ['due_amount'], groupby=['project_id'], lazy=False)")
        print(_SEP2)

        rows_due: list[dict] = await client.execute_kw(
            _MODEL,
            "read_group",
            args=[domain, ["due_amount", "amount"], ["project_id"]],
            kwargs={"lazy": False},
        )

        print(f"    Raw row count returned by Odoo: {len(rows_due)}")
        print()

        # ── Section 2: Parse and validate ─────────────────────────────────────
        print(_SEP2)
        print("[2] Per-project breakdown")
        print(_SEP2)
        print(f"    {'Project':<30} {'ID':>4}  {'Records':>8}  {'Due Amount (EGP)':>24}  {'Amount (EGP)':>24}")
        print(f"    {'-'*30} {'-'*4}  {'-'*8}  {'-'*24}  {'-'*24}")

        parsed: list[dict] = []
        seen_ids: set[int] = set()

        for row in rows_due:
            proj_raw = row.get("project_id")
            # many2one fields in read_group return [id, display_name]
            if isinstance(proj_raw, (list, tuple)) and len(proj_raw) == 2:
                proj_id = int(proj_raw[0])
                proj_name = str(proj_raw[1])
            elif proj_raw is False or proj_raw is None:
                proj_id = 0
                proj_name = "(none)"
            else:
                proj_id = int(proj_raw)
                proj_name = f"id={proj_raw}"

            due_amount = float(row.get("due_amount") or 0.0)
            amount = float(row.get("amount") or 0.0)
            count = int(row.get("__count") or 0)

            seen_ids.add(proj_id)

            if proj_id not in _EXPECTED_PROJECTS:
                flags.append(f"unexpected_project_id_{proj_id}")
                print(f"    {_FLAG} Unexpected project_id={proj_id} name={proj_name!r}")

            parsed.append({
                "project_id": proj_id,
                "project_name": proj_name,
                "due_amount": due_amount,
                "amount": amount,
                "record_count": count,
            })

            print(
                f"    {proj_name:<30} {proj_id:>4}  {count:>8,}  "
                f"{due_amount:>24,.2f}  {amount:>24,.2f}"
            )

        # Check for missing expected projects
        for eid in _EXPECTED_PROJECTS:
            if eid not in seen_ids:
                flags.append(f"missing_project_id_{eid}")
                print(f"    {_FLAG} Expected project_id={eid} ({_EXPECTED_PROJECTS[eid]}) NOT returned")
                print(f"    {_INFO} This means zero late records for this project — zero-padding will apply in service")
                # Add a zero entry so the summary math is complete
                parsed.append({
                    "project_id": eid,
                    "project_name": _EXPECTED_PROJECTS[eid],
                    "due_amount": 0.0,
                    "amount": 0.0,
                    "record_count": 0,
                })

        # Sort by project_id for consistent display
        parsed.sort(key=lambda r: r["project_id"])

        total_due = sum(r["due_amount"] for r in parsed if r["project_id"] in _EXPECTED_PROJECTS)
        total_count = sum(r["record_count"] for r in parsed if r["project_id"] in _EXPECTED_PROJECTS)

        print(f"    {'-'*30} {'-'*4}  {'-'*8}  {'-'*24}  {'-'*24}")
        print(
            f"    {'TOTAL':<30} {'':>4}  {total_count:>8,}  "
            f"{total_due:>24,.2f}  {'':>24}"
        )

        # ── Section 3: Project ID validation ──────────────────────────────────
        print()
        print(_SEP2)
        print("[3] Project ID validation")
        print(_SEP2)

        expected_ids = set(_EXPECTED_PROJECTS.keys())
        actual_expected_ids = seen_ids & expected_ids
        unexpected_ids = seen_ids - expected_ids

        if len(seen_ids) == 3 and seen_ids == expected_ids:
            print(f"    {_PASS} Exactly 3 projects returned: IDs {sorted(seen_ids)}")
        else:
            print(f"    {_FLAG} Expected IDs {{1,2,3}}, got {sorted(seen_ids)}")

        for pid, pname in _EXPECTED_PROJECTS.items():
            proj_row = next((r for r in parsed if r["project_id"] == pid), None)
            if proj_row:
                expected_name = pname
                actual_name = proj_row["project_name"]
                name_match = expected_name.lower() in actual_name.lower() or actual_name.lower() in expected_name.lower()
                status = _PASS if name_match else _FLAG
                if not name_match:
                    flags.append(f"name_mismatch_id_{pid}")
                print(f"    {status} project_id={pid}: expected={expected_name!r}, got={actual_name!r}")
            else:
                print(f"    {_FLAG} project_id={pid} ({pname}): NOT in results")

        if unexpected_ids:
            for uid in sorted(unexpected_ids):
                print(f"    {_FLAG} Unexpected project_id={uid} appeared in results")

        # ── Section 4: KPI 2 cross-check ──────────────────────────────────────
        print()
        print(_SEP2)
        print("[4] KPI 2 cross-check (sum of per-project Late due_amount vs KPI 2 baseline)")
        print(_SEP2)

        delta = total_due - _KPI2_BASELINE_EGP
        delta_sign = "+" if delta >= 0 else ""
        delta_pct = (abs(delta) / _KPI2_BASELINE_EGP * 100) if _KPI2_BASELINE_EGP else 0.0

        print(f"    Per-project total  : {_egp(total_due)}")
        print(f"    KPI 2 baseline     : {_egp(_KPI2_BASELINE_EGP)} ({_KPI2_BASELINE_DATE})")
        print(f"    Delta              : {delta_sign}{delta:>20,.2f} EGP ({delta_pct:.3f}%)")
        print()

        # Material discrepancy = >5% delta (arbitrary but generous given ongoing data entry)
        if delta_pct > 5.0:
            flags.append(f"material_delta_vs_kpi2_baseline_{delta_pct:.1f}pct")
            print(f"    {_FLAG} Delta exceeds 5% threshold — investigate before implementing service code")
        else:
            print(f"    {_INFO} Delta within drift tolerance (<=5%) — consistent with ongoing data entry")

        # ── Section 5: Detailed per-project summary ────────────────────────────
        print()
        print(_SEP)
        print("DISCOVERY SUMMARY")
        print(_SEP)
        print()
        print(f"{'Project':<25} {'ID':>4}  {'Records':>8}  {'Due Amount (EGP)':>24}  {'Amount (EGP)':>24}")
        print(f"{'-'*25} {'-'*4}  {'-'*8}  {'-'*24}  {'-'*24}")
        for r in sorted(parsed, key=lambda x: x["project_id"]):
            if r["project_id"] in _EXPECTED_PROJECTS:
                print(
                    f"{r['project_name']:<25} {r['project_id']:>4}  {r['record_count']:>8,}  "
                    f"{r['due_amount']:>24,.2f}  {r['amount']:>24,.2f}"
                )
        print(f"{'-'*25} {'-'*4}  {'-'*8}  {'-'*24}  {'-'*24}")
        print(
            f"{'TOTAL':<25} {'':>4}  {total_count:>8,}  "
            f"{total_due:>24,.2f}  {'':>24}"
        )
        print()

        if flags:
            print(f"{_FLAG} FLAGS raised ({len(flags)}): {flags}")
            print(f"{_FLAG} DO NOT proceed to D1 until all flags are resolved by Khaled.")
        else:
            print(f"{_PASS} No flags raised.")
            print()
            print("Next step — Khaled manual cross-check (REQUIRED before D1):")
            print("  1. Open Odoo → Collections Mgmt → Late Installments tab")
            print("  2. Group by Project (or filter per project one at a time)")
            print("  3. Compare each project's Due Amount to the values above")
            print("  4. Confirm the three values sum to the TOTAL above (within drift)")
            print("  DO NOT proceed to D1 until Khaled confirms all three values match.")

        print(_SEP)

    _append_tsv(
        rows=[r for r in parsed if r["project_id"] in _EXPECTED_PROJECTS],
        total_due=total_due,
        total_count=total_count,
        flags=flags,
    )


if __name__ == "__main__":
    asyncio.run(run())
