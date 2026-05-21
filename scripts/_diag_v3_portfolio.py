"""
One-time diagnostic for V3 portfolio identity gap (6,500,203 EGP delta).

Answers Q1-Q4 from the D6 failure analysis:
  Q1  Orphan partner_id — are there read_group rows with partner_id=False?
  Q2  Total group count — does the raw read_group count match 1,272 customers?
  Q3  Sum reconciliation — where does the SUM(amount) diverge?
  Q4  Cursor / page-boundary — do offset pages drop rows?

Run from project root AFTER Decision 6.4 ritual:
    python scripts/_diag_v3_portfolio.py

Delete after use. Results are printed to stdout; no log file written.
"""

import asyncio
import io
import sys
from pathlib import Path

# Ensure project root is importable (needed when run as a script)
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from backend.shared.odoo.client import OdooClient  # noqa: E402

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_MODEL  = "rs.installment"
_DOMAIN = [("state", "=", "post")]

_SEP  = "═" * 72
_SEP2 = "─" * 70

KPI1_VALUE   = 6_121_816_265.23   # from D6 run — identity target
DD_VALUE     = 6_115_316_062.23   # from D6 run — what the drilldown returned
DELTA_KNOWN  = KPI1_VALUE - DD_VALUE   # 6,500,203.00


async def main() -> None:
    async with OdooClient() as odoo:

        # ── Q3 (first) — ground-truth total from a flat read_group ───────────
        print(_SEP)
        print("Q3 — Sum reconciliation: flat read_group (no groupby)")
        print(_SEP2)
        flat = await odoo.execute_kw(
            _MODEL, "read_group",
            args=[_DOMAIN, ["amount"], []],
            kwargs={"lazy": False},
        )
        flat_row   = flat[0] if flat else {}
        flat_total = float(flat_row.get("amount") or 0.0)
        flat_count = int(flat_row.get("__count") or 0)
        print(f"  Flat SUM(amount)   : EGP {flat_total:>22,.2f}")
        print(f"  Flat __count       : {flat_count:>26,}")
        print(f"  KPI 1 (from D6)    : EGP {KPI1_VALUE:>22,.2f}")
        print(f"  Delta flat vs KPI1 : EGP {abs(flat_total - KPI1_VALUE):>22,.4f}")
        if abs(flat_total - KPI1_VALUE) < 1.0:
            print("  CONCLUSION: flat read_group matches KPI 1 → loss is in aggregation/pagination layer")
        else:
            print("  CONCLUSION: flat read_group DISAGREES with KPI 1 → investigate kpi_service caching")
        print()

        # ── Q1 — Orphan partner_id check ─────────────────────────────────────
        print(_SEP)
        print("Q1 — Orphan partner_id: read_group groupby=['partner_id']")
        print(_SEP2)
        rg_partner = await odoo.execute_kw(
            _MODEL, "read_group",
            args=[_DOMAIN, ["amount"], ["partner_id"]],
            kwargs={"lazy": False},
        )
        total_groups_partner = len(rg_partner)
        orphan_amount  = 0.0
        orphan_count   = 0
        non_orphan_sum = 0.0
        print(f"  Total partner_id groups: {total_groups_partner:,}")
        print()
        for row in rg_partner:
            raw = row.get("partner_id")
            amt = float(row.get("amount") or 0.0)
            cnt = int(row.get("__count") or 0)
            is_falsy = not raw   # False, None, 0, [] all caught
            if is_falsy:
                orphan_amount += amt
                orphan_count  += cnt
                print(f"  ORPHAN GROUP: partner_id={raw!r}  amount=EGP {amt:,.2f}  __count={cnt}")
            else:
                non_orphan_sum += amt
        if orphan_amount == 0.0:
            print("  No orphan partner_id groups found.")
        print()
        print(f"  SUM(amount) for orphan groups  : EGP {orphan_amount:>22,.2f}")
        print(f"  SUM(amount) for non-orphan     : EGP {non_orphan_sum:>22,.2f}")
        print(f"  Total (orphan + non-orphan)    : EGP {orphan_amount + non_orphan_sum:>22,.2f}")
        print(f"  Known delta (D6)               : EGP {DELTA_KNOWN:>22,.2f}")
        if abs(orphan_amount - DELTA_KNOWN) < 1.0:
            print("  MATCH: orphan_amount explains the entire delta → Q1 is the root cause")
        else:
            print(f"  PARTIAL or NO match: orphan accounts for {orphan_amount:,.2f} of {DELTA_KNOWN:,.2f}")
        print()

        # ── Q1b — Orphan project_id check ────────────────────────────────────
        print(_SEP)
        print("Q1b — Orphan project_id: read_group groupby=['project_id']")
        print(_SEP2)
        rg_project = await odoo.execute_kw(
            _MODEL, "read_group",
            args=[_DOMAIN, ["amount"], ["project_id"]],
            kwargs={"lazy": False},
        )
        total_groups_project = len(rg_project)
        orphan_proj_amount = 0.0
        orphan_proj_count  = 0
        print(f"  Total project_id groups: {total_groups_project:,}")
        print()
        for row in rg_project:
            raw = row.get("project_id")
            amt = float(row.get("amount") or 0.0)
            cnt = int(row.get("__count") or 0)
            if not raw:
                orphan_proj_amount += amt
                orphan_proj_count  += cnt
                print(f"  ORPHAN GROUP: project_id={raw!r}  amount=EGP {amt:,.2f}  __count={cnt}")
        if orphan_proj_amount == 0.0:
            print("  No orphan project_id groups found.")
        print(f"  SUM(amount) orphan project_id groups: EGP {orphan_proj_amount:>22,.2f}")
        print()

        # ── Q2 — Total group count in the drilldown's actual groupby ─────────
        print(_SEP)
        print("Q2 — Group count: read_group groupby=['partner_id','project_id']")
        print("     (this is the exact call get_portfolio_drilldown() makes)")
        print(_SEP2)
        rg_both = await odoo.execute_kw(
            _MODEL, "read_group",
            args=[_DOMAIN, ["amount", "due_amount", "paid_amount", "x_studio_actual_paid_amount"],
                  ["partner_id", "project_id"]],
            kwargs={"lazy": False},
        )
        total_groups_both = len(rg_both)

        # Split into falsy-key and non-falsy rows (mirrors the drilldown's `continue` logic)
        kept_rows    = []
        skipped_rows = []
        for row in rg_both:
            partner_raw = row.get("partner_id")
            project_raw = row.get("project_id")
            if not partner_raw or not project_raw:
                skipped_rows.append(row)
            else:
                kept_rows.append(row)

        kept_amount    = sum(float(r.get("amount") or 0.0) for r in kept_rows)
        skipped_amount = sum(float(r.get("amount") or 0.0) for r in skipped_rows)

        # Unique customers after the drilldown's collapse
        cust_ids = set()
        for row in kept_rows:
            pr = row.get("partner_id")
            cid = int(pr[0]) if isinstance(pr, (list, tuple)) else int(pr)
            cust_ids.add(cid)
        unique_customers = len(cust_ids)

        print(f"  Total read_group rows (raw)    : {total_groups_both:>26,}")
        print(f"  Rows KEPT   (partner+project ok): {len(kept_rows):>26,}")
        print(f"  Rows SKIPPED (falsy partner/proj): {len(skipped_rows):>25,}")
        print(f"  Unique customer IDs after keep : {unique_customers:>26,}")
        print(f"  D6 paginated customer count    : {'1,272':>26}")
        print()
        print(f"  SUM(amount) KEPT rows          : EGP {kept_amount:>22,.2f}")
        print(f"  SUM(amount) SKIPPED rows       : EGP {skipped_amount:>22,.2f}")
        print(f"  KEPT + SKIPPED                 : EGP {kept_amount + skipped_amount:>22,.2f}")
        print(f"  KPI 1                          : EGP {KPI1_VALUE:>22,.2f}")
        print()

        if len(skipped_rows) > 0:
            print("  SKIPPED rows (partner_id, project_id, amount):")
            for row in skipped_rows[:20]:  # cap at 20 to avoid flooding
                pr  = row.get("partner_id")
                pj  = row.get("project_id")
                amt = float(row.get("amount") or 0.0)
                cnt = int(row.get("__count") or 0)
                print(f"    partner_id={pr!r}  project_id={pj!r}  "
                      f"amount=EGP {amt:,.2f}  __count={cnt}")
            if len(skipped_rows) > 20:
                print(f"    ... and {len(skipped_rows) - 20} more skipped rows")
        print()

        # ── Q4 — Cursor / page-boundary walk ─────────────────────────────────
        print(_SEP)
        print("Q4 — Offset cursor boundary: walk all_customers via Python-side pagination")
        print("     (simulates walk_all_pages page_size=50, no HTTP — direct Python logic)")
        print(_SEP2)

        # Replicate the exact drilldown aggregation + sort (no HTTP involved)
        customer_map: dict[int, dict] = {}
        for row in rg_both:
            partner_raw = row.get("partner_id")
            project_raw = row.get("project_id")
            if not partner_raw or not project_raw:
                continue
            cust_id = int(partner_raw[0]) if isinstance(partner_raw, (list, tuple)) else int(partner_raw)
            amount  = float(row.get("amount") or 0.0)
            due     = float(row.get("due_amount") or 0.0)
            paid    = float(row.get("paid_amount") or 0.0)
            actual  = float(row.get("x_studio_actual_paid_amount") or 0.0)
            count   = int(row.get("__count") or 0)
            if cust_id not in customer_map:
                customer_map[cust_id] = {"total_amount": 0.0, "count": 0}
            customer_map[cust_id]["total_amount"] += amount
            customer_map[cust_id]["count"]        += count

        all_customers = sorted(
            customer_map.items(),
            key=lambda kv: (-kv[1]["total_amount"], kv[0]),
        )

        PAGE_SIZE   = 50
        total_pages = 0
        total_seen  = 0
        page_sums: list[float] = []

        for offset in range(0, len(all_customers), PAGE_SIZE):
            page = all_customers[offset: offset + PAGE_SIZE]
            page_total = sum(c[1]["total_amount"] for c in page)
            page_sums.append(page_total)
            total_seen += len(page)
            total_pages += 1

        grand_sum = sum(page_sums)
        print(f"  Total customers (after drilldown keep filter): {len(all_customers):,}")
        print(f"  Total pages at page_size=50: {total_pages}")
        print(f"  Total customers across all pages: {total_seen:,}")
        print(f"  SUM(total_amount) across all pages: EGP {grand_sum:>22,.2f}")
        print(f"  Delta vs D6 drilldown value: EGP {abs(grand_sum - DD_VALUE):>22,.4f}")
        print(f"  Delta vs KPI 1             : EGP {abs(grand_sum - KPI1_VALUE):>22,.4f}")
        print()
        if total_seen == len(all_customers):
            print("  CONCLUSION: No customers dropped by pagination. Offset cursor is correct.")
        else:
            print(f"  WARNING: {len(all_customers) - total_seen} customers DROPPED by pagination")
        print()

        # ── Summary ───────────────────────────────────────────────────────────
        print(_SEP)
        print("DIAGNOSTIC SUMMARY")
        print(_SEP2)
        print(f"  KPI 1 value (ground truth)       : EGP {KPI1_VALUE:>22,.2f}")
        print(f"  D6 drilldown total               : EGP {DD_VALUE:>22,.2f}")
        print(f"  Known delta                      : EGP {DELTA_KNOWN:>22,.2f}")
        print()
        print(f"  Q3: flat read_group total        : EGP {flat_total:>22,.2f}")
        print(f"      flat == KPI 1?                 {'YES' if abs(flat_total - KPI1_VALUE) < 1.0 else 'NO'}")
        print()
        print(f"  Q1: orphan partner_id amount     : EGP {orphan_amount:>22,.2f}")
        print(f"  Q1b: orphan project_id amount    : EGP {orphan_proj_amount:>22,.2f}")
        print(f"  Q2: rows skipped by `if not partner_raw or not project_raw`: {len(skipped_rows)}")
        print(f"      skipped amount               : EGP {skipped_amount:>22,.2f}")
        print(f"      skipped == known delta?        {'YES' if abs(skipped_amount - DELTA_KNOWN) < 1.0 else 'NO — partial or different cause'}")
        print()
        print(f"  Q4: pagination drop count        : {len(all_customers) - total_seen}")
        print(f"      pagination is the bug?         {'YES' if len(all_customers) != total_seen else 'NO — pagination is clean'}")
        print()

        if abs(skipped_amount - DELTA_KNOWN) < 1.0:
            print("  ROOT CAUSE CONFIRMED: `if not partner_raw or not project_raw: continue`")
            print("  in get_portfolio_drilldown() skips installments with no customer/project")
            print("  link. KPI 1 includes them; the drill-down silently excludes them.")
            print("  Fix: accumulate these rows under an 'Unknown' customer bucket, OR")
            print("  report them as a separate data-quality entry. Do NOT silently drop them.")
        elif abs(orphan_amount - DELTA_KNOWN) < 1.0:
            print("  ROOT CAUSE CONFIRMED (Q1): orphan partner_id=False rows account for the delta.")
        elif len(all_customers) != total_seen:
            print("  ROOT CAUSE: cursor / pagination is dropping customers (Q4).")
        else:
            print("  ROOT CAUSE UNCLEAR — check the numbers above and re-run with extra logging.")
        print(_SEP)


if __name__ == "__main__":
    asyncio.run(main())
