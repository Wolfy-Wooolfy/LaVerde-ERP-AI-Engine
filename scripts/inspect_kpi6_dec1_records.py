"""One-shot inspection: read records around the Dec 1 boundary,
report their raw .date values to understand Odoo storage format."""

import asyncio
import sys

from backend.shared.odoo.client import OdooClient

_MODEL = "rs.account.payment.installment"


async def main() -> None:
    async with OdooClient() as client:
        # 1. The specific record Khaled identified (amount=99,114, state=post)
        print("=== Search by amount=99114, state=post ===")
        records = await client.execute_kw(
            _MODEL,
            "search_read",
            args=[
                [("amount", "=", 99114), ("state", "=", "post")],
                ["id", "date", "amount", "state"],
            ],
            kwargs={"limit": 5},
        )
        if not records:
            print("  (no records matched amount=99114 + state=post)")
        for r in records:
            print(f"  id={r['id']}")
            print(f"    date (raw from Odoo) = {r['date']!r}")
            print(f"    type                 = {type(r['date']).__name__}")
            print(f"    amount               = {r['amount']}")
            print(f"    state                = {r['state']}")

        print()

        # 2. First 5 records ascending by date in Dec 2025 with NAIVE boundaries
        print("=== First 5 records: naive >= '2025-12-01' ===")
        naive = await client.execute_kw(
            _MODEL,
            "search_read",
            args=[
                [("state", "=", "post"), ("date", ">=", "2025-12-01"), ("date", "<=", "2025-12-01 23:59:59")],
                ["id", "date", "amount"],
            ],
            kwargs={"limit": 5, "order": "date asc"},
        )
        print(f"  count in naive Dec-1 window: {len(naive)}")
        for r in naive:
            print(f"    id={r['id']}  date={r['date']!r}  amount={r['amount']}")

        print()

        # 3. First 5 records with UTC-shifted boundary (2025-11-30 22:00:00)
        print("=== First 5 records: UTC-shifted >= '2025-11-30 22:00:00' ===")
        utc_shifted = await client.execute_kw(
            _MODEL,
            "search_read",
            args=[
                [("state", "=", "post"), ("date", ">=", "2025-11-30 22:00:00"), ("date", "<=", "2025-12-01 21:59:59")],
                ["id", "date", "amount"],
            ],
            kwargs={"limit": 5, "order": "date asc"},
        )
        print(f"  count in UTC-shifted Dec-1 window: {len(utc_shifted)}")
        for r in utc_shifted:
            print(f"    id={r['id']}  date={r['date']!r}  amount={r['amount']}")

        print()

        # 4. Records just BEFORE the naive boundary (2025-11-30 22:00 to midnight)
        print("=== Records between '2025-11-30 22:00:00' and '2025-11-30 23:59:59' ===")
        pre_naive = await client.execute_kw(
            _MODEL,
            "search_read",
            args=[
                [("state", "=", "post"), ("date", ">=", "2025-11-30 22:00:00"), ("date", "<", "2025-12-01")],
                ["id", "date", "amount"],
            ],
            kwargs={"limit": 10, "order": "date asc"},
        )
        print(f"  count: {len(pre_naive)}")
        for r in pre_naive:
            print(f"    id={r['id']}  date={r['date']!r}  amount={r['amount']}")

        print()

        # 5. Count for full December with naive vs shifted
        print("=== December 2025 total counts ===")
        c_naive = await client.execute_kw(
            _MODEL, "search_count",
            args=[[("state", "=", "post"), ("date", ">=", "2025-12-01"), ("date", "<=", "2025-12-31 23:59:59")]],
        )
        c_shifted = await client.execute_kw(
            _MODEL, "search_count",
            args=[[("state", "=", "post"), ("date", ">=", "2025-11-30 22:00:00"), ("date", "<=", "2025-12-31 21:59:59")]],
        )
        print(f"  naive filter:       {c_naive} records")
        print(f"  UTC-shifted filter: {c_shifted} records")
        print(f"  delta:              {c_shifted - c_naive} records")


if __name__ == "__main__":
    asyncio.run(main())
