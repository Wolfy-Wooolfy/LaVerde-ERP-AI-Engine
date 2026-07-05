"""
Live verification for Module 4 Phase 1 — Balance Sheet (opening-balance phase).

Usage:
    python scripts/verify_balance_sheet.py

Calls the SERVICE directly against live Odoo (no HTTP server required) via the
shared read-only OdooClient. Requires .env with ODOO_URL / ODOO_DB /
ODOO_USERNAME / ODOO_API_KEY.

Prints:
  1. total posted debit vs total posted credit (must match to the piaster)
  2. the three section totals + unallocated_result + difference + balanced
  3. excluded_off_balance
  4. top 10 accounts by |balance| per section (code, name, balance)
  5. distinct account_type values per displayed group (payload + full chart)

Exit 0 — debit == credit, equation balanced, no fail-loud condition
Exit 1 — unbalanced or debit != credit
Exit 2 — fail-loud condition (BalanceSheetIntegrityError) or Odoo error

NO hardcoded expected totals: figures shift while finance edits the opening
balance in place (Decision M4.3).

READ-ONLY: only the read-only service plus two read_group probes, all through
the same ALLOWED_METHODS-enforced client. No create/write/unlink by any path.
"""

import asyncio
import io
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from backend.core.exceptions import OdooQueryError  # noqa: E402
from backend.modules.accounting.services.balance_sheet_service import (  # noqa: E402
    BalanceSheetIntegrityError,
    get_balance_sheet,
)
from backend.shared.odoo.client import OdooClient  # noqa: E402

_SEP = "═" * 72
_SEP2 = "─" * 72
_PASS = "[PASS]"
_FAIL = "[FAIL]"
_INFO = "[INFO]"

_PIASTER = 0.01


def _fmt(x: float) -> str:
    return f"{x:,.2f}"


async def main() -> int:
    failures = 0

    client = OdooClient()
    try:
        # Probe 1 — raw posted totals (identity check: debit == credit).
        totals_rows = await client.execute_kw(
            "account.move.line",
            "read_group",
            args=[[("parent_state", "=", "posted")], ["debit", "credit"], []],
            kwargs={"lazy": False},
        )
        # Probe 2 — full chart pairs (distinct account_type per group,
        # including types whose all-zero subgroups the payload omits).
        pairs = await client.execute_kw(
            "account.account",
            "read_group",
            args=[[], ["id"], ["internal_group", "account_type"]],
            kwargs={"lazy": False},
        )
        # The actual service under test (shares the same client/connection).
        data = await get_balance_sheet(client=client)
    finally:
        await client.close()

    row = (totals_rows or [{}])[0]
    total_debit = float(row.get("debit") or 0.0)
    total_credit = float(row.get("credit") or 0.0)
    delta = total_debit - total_credit

    print(_SEP)
    print("Module 4 Phase 1 — Balance Sheet LIVE verification (service-direct)")
    print(f"generated_at: {data['generated_at']}   rpc_duration_ms: {data['rpc_duration_ms']}")
    print(_SEP)

    print(_SEP2)
    print("1. Posted totals — account.move.line (parent_state='posted')")
    print(_SEP2)
    print(f"   total debit : {_fmt(total_debit)} EGP")
    print(f"   total credit: {_fmt(total_credit)} EGP")
    print(f"   delta       : {delta:,.6f} EGP")
    if abs(delta) < _PIASTER:
        print(f"{_PASS} total posted debit == total posted credit (to the piaster)")
    else:
        print(f"{_FAIL} posted debit != credit — journal out of balance at source")
        failures += 1

    totals = data["totals"]
    print(_SEP2)
    print("2. Accounting equation")
    print(_SEP2)
    print(f"   assets                              : {_fmt(totals['assets'])} EGP")
    print(f"   liabilities                         : {_fmt(totals['liabilities'])} EGP")
    print(f"   equity                              : {_fmt(totals['equity'])} EGP")
    print(f"   unallocated_result (income+expense) : {_fmt(totals['unallocated_result'])} EGP")
    print(f"   liabilities + equity + result       : {_fmt(totals['liabilities_plus_equity_plus_result'])} EGP")
    print(f"   difference                          : {_fmt(totals['difference'])} EGP")
    print(f"   balanced                            : {totals['balanced']}")
    if totals["balanced"]:
        print(f"{_PASS} equation balanced: assets == liabilities + equity + unallocated_result")
    else:
        print(f"{_FAIL} equation NOT balanced (difference {_fmt(totals['difference'])} EGP)")
        failures += 1

    excluded = data["excluded_off_balance"]
    print(_SEP2)
    print("3. excluded_off_balance")
    print(_SEP2)
    print(f"   count: {excluded['count']}   total: {_fmt(excluded['total'])} EGP")
    print(f"{_INFO} zero expected while the chart has no off-balance internal_group")

    print(_SEP2)
    print("4. Top 10 accounts by |balance| per section")
    print(_SEP2)
    for section in data["sections"]:
        accounts = [a for sg in section["subgroups"] for a in sg["accounts"]]
        accounts.sort(key=lambda a: -abs(a["balance"]))
        print(f"   ── {section['group']} ({section['label_ar']}) — total {_fmt(section['total'])} EGP")
        if not accounts:
            print("      (no non-zero accounts)")
        for account in accounts[:10]:
            print(f"      {account['code']:<12} {_fmt(account['balance']):>22}  {account['name']}")

    print(_SEP2)
    print("5. Distinct account_type values per displayed group")
    print(_SEP2)
    chart_types: dict[str, set[str]] = {}
    for pair in pairs or []:
        chart_types.setdefault(str(pair.get("internal_group")), set()).add(str(pair.get("account_type")))
    for section in data["sections"]:
        group = section["group"]
        payload_types = sorted(sg["account_type"] for sg in section["subgroups"])
        all_types = sorted(chart_types.get(group, set()))
        print(f"   {group}:")
        print(f"      in payload (non-zero subgroups): {payload_types}")
        print(f"      in full chart                  : {all_types}")

    print(_SEP)
    if failures:
        print(f"{_FAIL} VERIFICATION FAILED — {failures} check(s) failed")
    else:
        print(f"{_PASS} ALL CHECKS PASSED")
    print(_SEP)
    return 1 if failures else 0


def run() -> int:
    try:
        return asyncio.run(main())
    except BalanceSheetIntegrityError as exc:
        print(f"{_FAIL} FAIL-LOUD integrity condition triggered:\n       {exc}")
        return 2
    except OdooQueryError as exc:
        print(f"{_FAIL} Odoo query failed:\n       {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(run())
