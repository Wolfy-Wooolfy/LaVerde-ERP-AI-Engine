"""
Live verification — KPI 7 type_breakdown (Stage 7, Deliverable 9).

Checks against live Odoo (via the FastAPI backend) that for each KPI 7 bucket:
  1. type_breakdown is present and is a list.
  2. sum(type_breakdown[].amount) == bucket.amount  (identity-equal, ±0.01 EGP).
  3. type_breakdown is sorted by amount descending (Choice 4أ).
  4. No entry has record_count == 0.
  5. Every installment_type_id resolves to a non-sentinel Arabic name.
  6. Each entry's installment_type_name_ar matches INSTALLMENT_TYPE_NAMES_AR.

Usage:
    KPI7_VERIFY_CONFIRMED=1 python scripts/verify_kpi7_breakdown_live.py
    $env:KPI7_VERIFY_CONFIRMED = "1"; python scripts/verify_kpi7_breakdown_live.py

Requires the FastAPI server to be running (Decision 6.4 ritual completed).

Exit 0  — all assertions passed
Exit 1  — at least one assertion failed or the server was unreachable
Exit 2  — ritual guard not satisfied
"""

import io
import os
import sys
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Import the mapping so we can resolve names here without re-hardcoding.
sys.path.insert(0, str(Path(__file__).parent.parent))
from backend.modules.collections.installment_type_names import (
    INSTALLMENT_TYPE_NAMES_AR, get_type_name_ar, _UNKNOWN_TYPE_AR,
)

# ── Config ────────────────────────────────────────────────────────────────────

DEFAULT_URL  = os.environ.get("BACKEND_URL", "http://localhost:8000")
USERNAME     = os.environ.get("VERIFY_USERNAME", "admin")
PASSWORD     = os.environ.get("VERIFY_PASSWORD", "password")
ENDPOINT     = "/api/v1/collections/kpi/expected-forecast"
_BUCKET_NAMES = ("this_month", "this_quarter", "this_half", "this_year")
_SEP  = "=" * 72
_SEP2 = "-" * 70
_PASS = "[PASS]"
_FAIL = "[FAIL]"
_INFO = "[INFO]"
_TOLERANCE = 0.01  # EGP identity-equal tolerance

_RITUAL = """
DECISION 6.4 RITUAL REQUIRED:
  1. Kill all python/uvicorn processes.
  2. Purge __pycache__.
  3. Start: python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
  4. Set:   KPI7_VERIFY_CONFIRMED=1
  5. Re-run this script.
"""


def _check(label: str, ok: bool, detail: str = "") -> bool:
    marker = _PASS if ok else _FAIL
    suffix = f" — {detail}" if detail else ""
    print(f"{marker} {label}{suffix}", flush=True)
    return ok


def main() -> int:
    if os.environ.get("KPI7_VERIFY_CONFIRMED") != "1":
        print(_RITUAL)
        print("REFUSED. Set KPI7_VERIFY_CONFIRMED=1 after the ritual.")
        return 2

    url = f"{DEFAULT_URL.rstrip('/')}{ENDPOINT}"
    print(_SEP)
    print("  KPI 7 type_breakdown live verification  (Stage 7)")
    print(f"  Target  : {url}")
    print(f"  Run at  : {datetime.now(timezone.utc).isoformat()}")
    print(_SEP)

    failures: list[str] = []

    def fail(label: str, detail: str = "") -> None:
        _check(label, False, detail)
        failures.append(label)

    def ok(label: str, detail: str = "") -> None:
        _check(label, True, detail)

    # ── Fetch ─────────────────────────────────────────────────────────────────
    try:
        resp = httpx.get(url, auth=(USERNAME, PASSWORD), timeout=30)
    except Exception as exc:
        print(f"[FAIL] HTTP request failed: {exc}")
        return 1

    if not _check("HTTP 200", resp.status_code == 200, f"got {resp.status_code}"):
        return 1

    data = resp.json()
    buckets = data.get("buckets", {})
    today_cairo = data.get("today_cairo", "?")
    print(f"\n{_INFO} today_cairo: {today_cairo}")

    all_pass = True

    for bname in _BUCKET_NAMES:
        b = buckets.get(bname)
        if b is None:
            fail(f"{bname}: bucket present")
            all_pass = False
            continue

        bucket_amount = float(b.get("amount") or 0.0)
        bucket_count  = int(b.get("record_count") or 0)

        print(f"\n  [{bname}]  amount={bucket_amount:,.2f}  records={bucket_count}")
        print(f"  {'-'*60}")

        tb = b.get("type_breakdown")

        # 1. type_breakdown present and is a list
        if not _check(f"  {bname}.type_breakdown is a list", isinstance(tb, list)):
            all_pass = False
            failures.append(f"{bname}.type_breakdown missing")
            continue

        # 2. identity-equal
        tb_sum = sum(float(e.get("amount") or 0.0) for e in tb)
        delta  = abs(tb_sum - bucket_amount)
        id_eq  = delta < _TOLERANCE
        if not _check(
            f"  {bname}.type_breakdown sums == bucket.amount",
            id_eq,
            f"sum={tb_sum:,.2f}  bucket={bucket_amount:,.2f}  delta={delta:.4f}",
        ):
            all_pass = False
            failures.append(f"{bname}.identity_equal  delta={delta:.4f}")

        # 3. sorted by amount descending
        amounts = [float(e.get("amount") or 0.0) for e in tb]
        sorted_ok = amounts == sorted(amounts, reverse=True)
        if not _check(f"  {bname}.type_breakdown sorted amount desc", sorted_ok):
            all_pass = False
            failures.append(f"{bname}.sort_order")

        # 4. no zero-count entries
        zero_cnt = [e for e in tb if int(e.get("record_count") or 0) == 0]
        if not _check(f"  {bname}.type_breakdown no zero-count entries", len(zero_cnt) == 0,
                      f"{len(zero_cnt)} zero-count entries found"):
            all_pass = False
            failures.append(f"{bname}.zero_count_entries")

        # 5 + 6. every type_id in mapping, name matches
        name_ok = True
        for e in tb:
            tid    = int(e.get("installment_type_id") or 0)
            name   = str(e.get("installment_type_name_ar") or "")
            expect = get_type_name_ar(tid)
            if expect == _UNKNOWN_TYPE_AR:
                fail(f"  {bname}.type_id={tid} in INSTALLMENT_TYPE_NAMES_AR", f"ID {tid} missing")
                name_ok = False
                all_pass = False
            elif name != expect:
                fail(f"  {bname}.type_id={tid} name matches mapping",
                     f"got {name!r}  expected {expect!r}")
                name_ok = False
                all_pass = False
        if name_ok:
            ok(f"  {bname}.all type_ids resolve to reviewed Arabic names")

        # Print the breakdown table
        print()
        print(f"    {'ID':>4}  {'Arabic Name':<22}  {'Amount (EGP)':>16}  {'Count':>6}")
        print(f"    {'-'*4}  {'-'*22}  {'-'*16}  {'-'*6}")
        for e in tb:
            tid    = int(e.get("installment_type_id") or 0)
            name   = str(e.get("installment_type_name_ar") or "")
            amt    = float(e.get("amount") or 0.0)
            cnt    = int(e.get("record_count") or 0)
            pct    = (amt / bucket_amount * 100) if bucket_amount else 0.0
            print(f"    {tid:>4}  {name:<22}  {amt:>16,.2f}  {cnt:>6}  ({pct:.1f}%)")
        print(f"    {'':>4}  {'TOTAL':<22}  {tb_sum:>16,.2f}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{_SEP}")
    if not failures:
        print(f"[PASS] All breakdown assertions passed.")
        print(f"       Each of the 4 bucket breakdowns is identity-equal, sorted, and")
        print(f"       has only reviewed Arabic names.")
    else:
        print(f"[FAIL] {len(failures)} assertion(s) failed:")
        for f in failures:
            print(f"       - {f}")

    print(_SEP)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
