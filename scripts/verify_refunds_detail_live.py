"""
Verification script — Refunds Detail endpoint (M3-S8).

Identity-equal check: total_amount from /refunds/detail must match
total_refunds from /refunds/summary (same domain, same records).

Usage:
    python scripts/verify_refunds_detail_live.py

Requires: server running on localhost:8000, admin credentials in env/.env.
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
from loguru import logger

_BASE = "http://localhost:8000"
_DETAIL_URL  = f"{_BASE}/api/v1/customer-accounts/refunds/detail"
_SUMMARY_URL = f"{_BASE}/api/v1/customer-accounts/refunds/summary"

# Credentials from env
_USER = os.getenv("ODOO_USERNAME", "admin")
_PASS = os.getenv("ODOO_PASSWORD", "admin")
_AUTH = (_USER, _PASS)

_PASS_COUNT = 0
_FAIL_COUNT = 0


def _pass(msg: str) -> None:
    global _PASS_COUNT
    _PASS_COUNT += 1
    print(f"[PASS] {msg}")


def _fail(msg: str) -> None:
    global _FAIL_COUNT
    _FAIL_COUNT += 1
    print(f"[FAIL] {msg}")


def _info(msg: str) -> None:
    print(f"[INFO] {msg}")


async def main() -> int:
    async with httpx.AsyncClient(auth=_AUTH, timeout=30) as client:

        # ── Call /refunds/detail ──────────────────────────────────────────────
        _info(f"Target: GET {_DETAIL_URL}")
        r_detail = await client.get(_DETAIL_URL)

        if r_detail.status_code == 200:
            _pass(f"HTTP 200 — /refunds/detail")
        else:
            _fail(f"HTTP {r_detail.status_code} — expected 200")
            return 1

        detail = r_detail.json()

        # ── Call /refunds/summary ─────────────────────────────────────────────
        _info(f"Target: GET {_SUMMARY_URL}")
        r_summary = await client.get(_SUMMARY_URL)

        if r_summary.status_code == 200:
            _pass(f"HTTP 200 — /refunds/summary")
        else:
            _fail(f"HTTP {r_summary.status_code} — expected 200")
            return 1

        summary = r_summary.json()

        # ── Basic shape checks ────────────────────────────────────────────────
        for key in ("items", "total_amount", "record_count", "currency",
                    "as_of", "cache_status", "rpc_duration_ms", "domain"):
            if key in detail:
                _pass(f"detail key '{key}' present")
            else:
                _fail(f"detail key '{key}' MISSING")

        # ── domain check ──────────────────────────────────────────────────────
        domain = detail.get("domain", [])
        if len(domain) == 2:
            _pass("domain has 2 clauses")
        else:
            _fail(f"domain expected 2 clauses, got {len(domain)}")

        if domain and domain[0] == ["state", "=", "post"]:
            _pass("domain[0] == state=post")
        else:
            _fail(f"domain[0] unexpected: {domain[0] if domain else 'empty'}")

        if len(domain) > 1 and domain[1] == ["amount", "<", 0]:
            _pass("domain[1] == amount<0")
        else:
            _fail(f"domain[1] unexpected: {domain[1] if len(domain) > 1 else 'missing'}")

        # ── Items shape ───────────────────────────────────────────────────────
        items = detail.get("items", [])
        _info(f"Records returned: {len(items)}")

        if items:
            row0 = items[0]
            for fld in ("record_id", "customer_id", "customer_name", "amount", "date"):
                if fld in row0:
                    _pass(f"items[0].{fld} present")
                else:
                    _fail(f"items[0].{fld} MISSING")

            all_negative = all(i["amount"] < 0 for i in items)
            if all_negative:
                _pass("all item amounts are negative (refunds)")
            else:
                pos = [i for i in items if i["amount"] >= 0]
                _fail(f"{len(pos)} item(s) have non-negative amount — unexpected for refunds")

        # ── record_count consistency ──────────────────────────────────────────
        if detail.get("record_count") == len(items):
            _pass(f"record_count == len(items) == {len(items)}")
        else:
            _fail(f"record_count {detail.get('record_count')} != len(items) {len(items)}")

        # ── total_amount == sum of item amounts ───────────────────────────────
        computed_total = sum(i["amount"] for i in items)
        reported_total = detail.get("total_amount", None)
        delta_items = abs((reported_total or 0) - computed_total)

        _info(f"Detail total_amount  : {reported_total:,.2f} EGP")
        _info(f"Sum of item amounts  : {computed_total:,.2f} EGP")
        _info(f"Delta (items vs total): {delta_items:.4f} EGP")

        if delta_items < 0.01:
            _pass(f"|total_amount - sum(items)| <= 0.01 EGP — delta={delta_items:.4f}")
        else:
            _fail(f"|total_amount - sum(items)| = {delta_items:.4f} EGP — expected < 0.01")

        # ── IDENTITY CHECK: detail.total_amount == summary.total_refunds ──────
        summary_total  = summary.get("total_refunds", None)
        summary_count  = summary.get("refund_count", None)
        detail_total   = detail.get("total_amount", None)
        detail_count   = detail.get("record_count", None)

        _info("")
        _info("=" * 60)
        _info("IDENTITY CHECK — detail vs summary")
        _info("=" * 60)
        _info(f"Detail  total_amount : {detail_total:,.2f} EGP")
        _info(f"Summary total_refunds: {summary_total:,.2f} EGP")

        delta = abs((detail_total or 0) - (summary_total or 0))
        _info(f"Delta                : {delta:.4f} EGP")

        if delta < 0.01:
            _pass(f"IDENTITY: |detail.total_amount - summary.total_refunds| <= 0.01 EGP — delta={delta:.4f}")
        else:
            _fail(f"IDENTITY MISMATCH: delta={delta:.4f} EGP — endpoints are inconsistent")

        _info(f"Detail  record_count : {detail_count}")
        _info(f"Summary refund_count : {summary_count}")

        if detail_count == summary_count:
            _pass(f"record counts match: detail={detail_count} == summary={summary_count}")
        else:
            _fail(f"record count MISMATCH: detail={detail_count} != summary={summary_count}")

        # ── Cache check ───────────────────────────────────────────────────────
        if detail.get("cache_status") == "fresh":
            _pass("cache_status == 'fresh' (first call, no cache)")
        else:
            _info(f"cache_status = {detail.get('cache_status')!r} (not fresh — server may have been running)")

        # ── Summary ───────────────────────────────────────────────────────────
        _info("")
        _info("=" * 60)
        _info(f"Results: {_PASS_COUNT} PASS, {_FAIL_COUNT} FAIL")
        _info("=" * 60)

        if _FAIL_COUNT == 0:
            print("\n[PASS] All assertions passed.")
        else:
            print(f"\n[FAIL] {_FAIL_COUNT} assertion(s) failed.")

        # ── Manual cross-check prompt ─────────────────────────────────────────
        print(
            "\nManual cross-check prompt (for Khaled):\n"
            "  Open Odoo -> Reconcile Payments (rs.account.payment.reconcile)\n"
            "  Filter: State = Posted, Amount < 0\n"
            "  The record count should match 'detail record_count' above.\n"
            "  The sum of Amount should match 'detail total_amount' above (negative).\n"
            "  Expected: identity-equal or < 0.01 EGP drift.\n"
        )

    return 1 if _FAIL_COUNT > 0 else 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
