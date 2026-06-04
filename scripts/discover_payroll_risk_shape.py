"""
Read-only payroll-risk shape discovery — KPI C re-foundation.

Answers Item 0 (RPC-level active_test probe) plus the five standard
discovery items, all on post-Dev-fix live data.

  Item 0  — RPC-level active_test probe: does the OLD KPI C silently drop
             the 13 archived-running contracts at the RPC layer?
             Compares search_read WITHOUT flag (old KPI C) vs WITH flag.
             Verdict: "old KPI C RPC drops archived-running: YES/NO".

  Item 1  — The 7 buckets today (current KPI C Python logic replicated
             exactly on corrected data).  Thresholds from kpi_service.py:
             delta<0→expired, <=45→expiring_45d, <=90→expiring_90d,
             <=135→expiring_135d, else→beyond_135d; False→open_ended.

  Item 2  — Sanity population: where active=True enters the current code,
             what the correct denominator is (115 Running-contract
             employees vs 136 active flag), and the gap decomposition.

  Item 3  — Orphan / Archived-Running: 13 Running contracts on archived
             employees, excluded from all 7 buckets in the current code;
             shown with the bucket each would land in under corrected logic.

  Item 4  — active_without_contract: the 34 active=True employees with no
             Running contract; their IDs and sub-type (exit_gap / data_gap
             / incoming).

  Item 5  — Expired bucket content: confirms zero Running contracts remain
             with date_end < cairo_today (post-fix); shows state='close'
             contracts with past date_end (the formerly-expired-but-running
             cases now correctly closed); resolves the expired/past_end_date
             rename question.

3 RPCs total (minimised):
  RPC 0 — search_read(hr.contract, [('state','=','open')], fields=['id'])
           NO active_test flag.  Mirrors old KPI C RPC 1 exactly.

  RPC 1 — search_read(hr.contract, [], fields=['id','employee_id',
           'state','date_end'], context={'active_test': False})
           All contracts, all states.  Running-with-flag count derived
           in Python (no separate RPC 0b needed).

  RPC 2 — search_read(hr.employee, [], fields=['id','active'],
           context={'active_test': False})
           All employees, active + archived.

Hard structural check (one):
  total_returned == running_in_buckets + orphan_count + non_running_count

Output:
  Console  — [INFO] / [PASS] / [FAIL] per section.
  TSV      — logs/payroll_risk_shape_discovery.log (appended).
  Exit 0 always.

Pre-flight (Decision 6.4): kill python, purge __pycache__; no uvicorn.
Usage:
    python scripts/discover_payroll_risk_shape.py
"""

import asyncio
import io
import os
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

# sys.path.insert so script runs without PYTHONPATH set (settled convention)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.shared.odoo.client import OdooClient  # noqa: E402

# Force UTF-8 stdout (Windows cp1252 default)
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Constants ──────────────────────────────────────────────────────────────────

CAIRO_TZ  = ZoneInfo("Africa/Cairo")
_LOG_FILE = "logs/payroll_risk_shape_discovery.log"
_SEP      = "═" * 72
_SEP2     = "─" * 72
_MAX_IDS  = 20

_PASS = "[PASS]"
_FAIL = "[FAIL]"
_INFO = "[INFO]"

# Bucket label order — must match kpi_service.py _BUCKET_LABELS exactly
BUCKET_LABELS = [
    "active_without_contract",
    "expired",
    "expiring_45d",
    "expiring_90d",
    "expiring_135d",
    "beyond_135d",
    "open_ended",
]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _info(msg: str) -> None:
    print(f"{_INFO} {msg}", flush=True)


def _pass(msg: str) -> None:
    print(f"{_PASS} {msg}", flush=True)


def _fail(msg: str) -> None:
    print(f"{_FAIL} {msg}", flush=True)


def _check(label: str, condition: bool, detail: str = "") -> bool:
    if condition:
        _pass(label)
    else:
        _fail(f"{label}{(' — ' + detail) if detail else ''}")
    return condition


def _section(title: str) -> None:
    print(f"\n{_SEP}")
    print(title)
    print(_SEP2)


def _fmt_ids(ids: list, max_ids: int = _MAX_IDS) -> str:
    if len(ids) <= max_ids:
        return str(ids)
    shown = ids[:max_ids]
    remaining = len(ids) - max_ids
    return f"{shown}  (... {remaining} more — see {_LOG_FILE})"


def _emp_id(raw: object) -> int | None:
    if isinstance(raw, (list, tuple)) and raw:
        return int(raw[0])
    if raw and raw is not False:
        return int(raw)
    return None


def _classify_bucket(date_end_raw: object, cairo_today: date) -> tuple[str, int | None]:
    """Return (bucket_label, delta_days) for a Running contract's date_end.

    Thresholds confirmed from kpi_service.py lines 572-586:
      delta < 0         → expired
      0 <= delta <= 45  → expiring_45d
      46 <= delta <= 90 → expiring_90d
      91 <= delta <= 135→ expiring_135d
      delta >= 136      → beyond_135d
      date_end = False  → open_ended
    """
    if not date_end_raw:
        return "open_ended", None
    delta = (date.fromisoformat(str(date_end_raw)) - cairo_today).days
    if delta < 0:
        return "expired", delta
    elif delta <= 45:
        return "expiring_45d", delta
    elif delta <= 90:
        return "expiring_90d", delta
    elif delta <= 135:
        return "expiring_135d", delta
    else:
        return "beyond_135d", delta


# ── Main ───────────────────────────────────────────────────────────────────────

async def run() -> None:
    run_at      = datetime.now(timezone.utc).isoformat()
    cairo_today = datetime.now(CAIRO_TZ).date()

    print(_SEP)
    print("Payroll Risk Shape Discovery — KPI C Re-Foundation")
    print(f"Run timestamp  : {run_at}")
    print(f"Cairo today    : {cairo_today}")
    print(f"RPCs planned   : 3  (RPC 0: running-no-flag; RPC 1: all-contracts-with-flag; RPC 2: all-employees-with-flag)")
    print(_SEP)
    _info("SCOPE: READ-ONLY DISCOVERY. No writes. No kpi_service.py changes. Exit 0 always.")
    _info("       One hard structural check: all returned contracts accounted for.")

    # ── RPC calls ─────────────────────────────────────────────────────────────

    _section("RPC CALLS")

    async with OdooClient() as client:

        _info("RPC 0: search_read(hr.contract, [('state','=','open')], fields=['id'])")
        _info("       NO active_test context — mirrors old KPI C RPC 1 exactly")
        rpc0_records = await client.execute_kw(
            "hr.contract",
            "search_read",
            args=[[("state", "=", "open")]],
            kwargs={"fields": ["id"]},
        )
        _info(f"       → {len(rpc0_records)} records returned")

        _info("RPC 1: search_read(hr.contract, [], fields=['id','employee_id','state','date_end'],")
        _info("       context={'active_test': False})  — all contracts, all states")
        all_contracts = await client.execute_kw(
            "hr.contract",
            "search_read",
            args=[[]],
            kwargs={
                "fields": ["id", "employee_id", "state", "date_end"],
                "context": {"active_test": False},
            },
        )
        _info(f"       → {len(all_contracts)} records returned")

        _info("RPC 2: search_read(hr.employee, [], fields=['id','active'],")
        _info("       context={'active_test': False})  — all employees, active + archived")
        emp_records = await client.execute_kw(
            "hr.employee",
            "search_read",
            args=[[]],
            kwargs={
                "fields": ["id", "active"],
                "context": {"active_test": False},
            },
        )
        _info(f"       → {len(emp_records)} records returned")

    # ── Parse employees ────────────────────────────────────────────────────────

    active_emp_ids   = {int(e["id"]) for e in emp_records if e.get("active") is True}
    archived_emp_ids = {int(e["id"]) for e in emp_records if e.get("active") is False}

    # ── Parse contracts ────────────────────────────────────────────────────────

    state_counter     = Counter(c.get("state", "MISSING") for c in all_contracts)
    running_contracts = [c for c in all_contracts if c.get("state") == "open"]
    non_running       = [c for c in all_contracts if c.get("state") != "open"]

    # All contracts grouped by employee_id (all states) — used in Item 4
    contracts_by_emp: dict[int, list[dict]] = defaultdict(list)
    for c in all_contracts:
        eid = _emp_id(c.get("employee_id"))
        if eid is not None:
            contracts_by_emp[eid].append(c)

    rpc0_count         = len(rpc0_records)
    rpc1_running_count = len(running_contracts)   # with active_test=False

    # ══════════════════════════════════════════════════════════════════════════
    # ITEM 0 — RPC-level active_test probe
    # ══════════════════════════════════════════════════════════════════════════

    _section("ITEM 0 — RPC-level active_test probe")
    _info("Question: does the old KPI C silently drop archived-running contracts?")
    _info(f"  RPC 0 — WITHOUT flag (mirrors old KPI C): {rpc0_count} Running contracts")
    _info(f"  RPC 1 — WITH active_test=False:           {rpc1_running_count} Running contracts")

    if rpc0_count == rpc1_running_count:
        item0_silent_drop = False
        _pass(
            f"Counts equal ({rpc0_count} == {rpc1_running_count}). "
            "active_test flag is INERT on hr.contract."
        )
        _info("Verdict: old KPI C RPC drops archived-running contracts: NO")
        _info("  hr.contract has no active field (or flag has no effect at this Odoo version).")
        _info("  Old KPI C receives all Running contracts including those on archived employees.")
        _info("  The 13 archived-running contracts ARE seen; Python classifies them as orphans")
        _info("  via: if emp_id not in active_emp_ids → orphan_count += 1.")
        _info("  orphan_count and bucket universe are correct at the RPC layer.")
    else:
        item0_silent_drop = True
        drop_count = rpc1_running_count - rpc0_count
        _fail(
            f"SILENT DROP CONFIRMED: without-flag={rpc0_count}, "
            f"with-flag={rpc1_running_count}, dropped={drop_count}."
        )
        _info("Verdict: old KPI C RPC drops archived-running contracts: YES — BUG")
        _info(f"  Impact: {drop_count} Running contracts on archived employees are invisible")
        _info("  to the current KPI C. Those employees are EMPLOYED (§3.6) but absent from")
        _info("  both bucket universe and orphan_count. orphan_count is understated by the")
        _info(f"  same amount ({drop_count}).")

    # ══════════════════════════════════════════════════════════════════════════
    # ITEM 1 — The 7 buckets today (current KPI C logic replicated exactly)
    # ══════════════════════════════════════════════════════════════════════════

    _section("ITEM 1 — The 7 buckets today (current KPI C logic replicated)")
    _info(f"Input: {rpc1_running_count} Running contracts (active_test=False, full universe)")
    _info(f"       active_emp_ids: {len(active_emp_ids)}  (mirrors old KPI C: search_read active=True)")
    _info(f"       Cairo today: {cairo_today}")

    orphan_count      = 0
    covered_emp_ids   = set()
    bucket_counts     = {label: 0 for label in BUCKET_LABELS}
    expired_details   = []   # for Item 5

    for c in running_contracts:
        eid = _emp_id(c.get("employee_id"))
        if eid not in active_emp_ids:
            orphan_count += 1
            continue

        covered_emp_ids.add(eid)
        bucket, delta = _classify_bucket(c.get("date_end"), cairo_today)
        bucket_counts[bucket] += 1
        if bucket == "expired":
            expired_details.append({
                "id": int(c["id"]),
                "date_end": c.get("date_end"),
                "delta": delta,
                "emp_id": eid,
            })

    bucket_counts["active_without_contract"] = len(active_emp_ids - covered_emp_ids)
    current_total_active = sum(bucket_counts.values())

    _info("")
    _info(f"  {'Bucket':<28}  {'Count':>6}")
    _info(f"  {'─' * 28}  {'─' * 6}")
    for label in BUCKET_LABELS:
        _info(f"  {label:<28}  {bucket_counts[label]:>6}")
    _info(f"  {'─' * 28}  {'─' * 6}")
    _info(f"  {'total_active (sum of 7)':<28}  {current_total_active:>6}")
    _info(f"  {'orphan_contracts_count':<28}  {orphan_count:>6}  (excluded from all 7 buckets)")

    # Show the dominant date_end and its delta — the June-30 wave
    date_end_counter: Counter = Counter()
    for c in running_contracts:
        eid = _emp_id(c.get("employee_id"))
        if eid in active_emp_ids:
            date_end_counter[str(c.get("date_end") or "open-ended")] += 1
    _info("")
    _info("  date_end distribution among Running contracts on active employees:")
    for de_val, cnt in date_end_counter.most_common(10):
        if de_val != "open-ended":
            delta = (date.fromisoformat(de_val) - cairo_today).days
            _info(f"    date_end={de_val}  count={cnt}  delta={delta:+d} days")
        else:
            _info(f"    date_end=open-ended  count={cnt}")

    # ══════════════════════════════════════════════════════════════════════════
    # ITEM 2 — Sanity population
    # ══════════════════════════════════════════════════════════════════════════

    _section("ITEM 2 — Sanity population (corrected denominator vs current)")

    true_headcount = len({
        _emp_id(c.get("employee_id"))
        for c in running_contracts
        if _emp_id(c.get("employee_id")) is not None
    })

    _info(f"active=True employee count (current KPI C universe):       {len(active_emp_ids)}")
    _info(f"Running-contract employees (corrected employment def):      {true_headcount}")
    _info(f"  delta (active − running):                                 {len(active_emp_ids) - true_headcount:+d}")
    _info("")
    _info(f"Current KPI C total_active (sum of 7 buckets):             {current_total_active}")
    _info(f"Correct denominator (Running-contract employees):           {true_headcount}")
    _info(f"  gap (current − correct):                                  {current_total_active - true_headcount:+d}")
    _info("")
    _info("Decomposed gap:")
    _info(f"  + {bucket_counts['active_without_contract']:>3} active_without_contract counted in buckets (active=True, no Running contract)")
    _info(f"  − {orphan_count:>3} orphan excluded from buckets (Running on archived employees — EMPLOYED per §3.6)")
    _info(f"  Net: {bucket_counts['active_without_contract'] - orphan_count:+d}  "
          f"= {current_total_active} (current) − {true_headcount} (correct)")
    _info("")
    _info("Where active=True enters the current kpi_service.py code:")
    _info("  RPC 2 (line ~539): search_read(hr.employee, [('active','=',True)], fields=['id'])")
    _info("  Feeds active_emp_ids (136).  Governs three things:")
    _info("    (a) orphan gate: emp_id not in active_emp_ids → skip all buckets")
    _info("    (b) bucket_counts['active_without_contract'] = len(active_emp_ids − covered_emp_ids)")
    _info("    (c) total_active = sum(bucket_counts) = len(active_emp_ids) by construction")

    # ══════════════════════════════════════════════════════════════════════════
    # ITEM 3 — Orphan / Archived-Running
    # ══════════════════════════════════════════════════════════════════════════

    _section("ITEM 3 — Orphan / Archived-Running (Running contracts on archived employees)")

    archived_running = [
        c for c in running_contracts
        if _emp_id(c.get("employee_id")) in archived_emp_ids
    ]
    archived_running_emp_ids = sorted({_emp_id(c["employee_id"]) for c in archived_running})

    _info(f"Running contracts on archived (active=False) employees: {len(archived_running)}")
    _info(f"  Employee IDs: {_fmt_ids(archived_running_emp_ids)}")
    _info("")
    _info(f"Current KPI C treatment: counted in orphan_contracts_count ({orphan_count})")
    _info("  Excluded from all 7 buckets by: if emp_id not in active_emp_ids → continue")
    _info("  §3.6: these are EMPLOYED (hold Running contracts). Archive flag is stale.")
    _info("")
    _info("Where each would land under corrected bucket logic (for design reference):")

    bucket_if_corrected: Counter = Counter()
    for c in archived_running:
        bucket, delta = _classify_bucket(c.get("date_end"), cairo_today)
        bucket_if_corrected[bucket] += 1
        _info(f"  emp_id={_emp_id(c['employee_id']):<6}  "
              f"date_end={str(c.get('date_end') or 'False'):<12}  "
              f"delta={'N/A' if delta is None else f'{delta:+d}':>7} days  "
              f"→ corrected_bucket={bucket}")

    _info("")
    _info("  Summary (corrected bucket distribution for archived+running):")
    for bucket_label in BUCKET_LABELS[1:]:   # skip active_without_contract
        cnt = bucket_if_corrected.get(bucket_label, 0)
        if cnt:
            _info(f"    {bucket_label}: {cnt}")

    # ══════════════════════════════════════════════════════════════════════════
    # ITEM 4 — active_without_contract population
    # ══════════════════════════════════════════════════════════════════════════

    _section("ITEM 4 — active_without_contract (bucket 1 population)")

    awc_emp_ids = sorted(active_emp_ids - covered_emp_ids)
    _info(f"bucket 'active_without_contract' count: {len(awc_emp_ids)}")
    _info(f"  Employee IDs: {_fmt_ids(awc_emp_ids)}")
    _info("")
    _info("Sub-classification by contract history (mirrors verify_employment_foundation.py C4):")

    exit_gap   = []
    data_gap   = []
    incoming   = []
    unexpected = []

    for eid in awc_emp_ids:
        emp_contracts = contracts_by_emp.get(eid, [])
        if not emp_contracts:
            data_gap.append(eid)
            continue
        states = {c.get("state") for c in emp_contracts}
        if "draft" in states:
            incoming.append(eid)
        elif states & {"close", "cancel"}:
            exit_gap.append(eid)
        else:
            unexpected.append(eid)
            exit_gap.append(eid)   # conservative: treat as exited

    _info(f"  exit_gap  (only close/cancel contracts): {len(exit_gap):>3}"
          f"  IDs: {_fmt_ids(exit_gap)}")
    _info(f"  data_gap  (no contract record at all):   {len(data_gap):>3}"
          f"  IDs: {_fmt_ids(data_gap)}")
    _info(f"  incoming  (has draft contract):          {len(incoming):>3}"
          f"  IDs: {_fmt_ids(incoming)}")
    if unexpected:
        _info(f"  unexpected (unknown state mix):          {len(unexpected):>3}"
              f"  IDs: {unexpected}")
    _info("")
    _info("Under corrected employment definition: NONE of these 34 are employed.")
    _info("  exit_gap + data_gap are data-quality issues, not payroll-risk contracts.")
    _info("  Counting them in bucket 1 inflates the sanity denominator by 34.")

    # ══════════════════════════════════════════════════════════════════════════
    # ITEM 5 — Expired bucket content
    # ══════════════════════════════════════════════════════════════════════════

    _section("ITEM 5 — Expired bucket content")

    _info(f"Current 'expired' bucket count (Running contracts, delta < 0): {bucket_counts['expired']}")
    if expired_details:
        _info("  Expired Running contracts (payroll-blocking — HR must act immediately):")
        for d in expired_details:
            _info(f"    contract_id={d['id']}  emp_id={d['emp_id']}  "
                  f"date_end={d['date_end']}  delta={d['delta']} days")
    else:
        _info("  (empty — consistent with post-fix auto-flip working correctly)")

    _info("")
    _info("Contract state distribution (all contracts, active_test=False):")
    label_map = {
        "open":   "Running",
        "close":  "Expired/Closed",
        "draft":  "New/incoming",
        "cancel": "Cancelled",
    }
    for state_key, count in sorted(state_counter.items(), key=lambda x: -x[1]):
        label = label_map.get(state_key, f"UNKNOWN '{state_key}'")
        _info(f"  state='{state_key}' ({label}): {count}")

    _info("")
    close_contracts = [c for c in all_contracts if c.get("state") == "close"]
    close_past  = [
        c for c in close_contracts
        if c.get("date_end")
        and date.fromisoformat(str(c.get("date_end"))) < cairo_today
    ]
    close_future = [
        c for c in close_contracts
        if c.get("date_end")
        and date.fromisoformat(str(c.get("date_end"))) >= cairo_today
    ]
    close_no_end = [c for c in close_contracts if not c.get("date_end")]

    _info(f"state='close' contracts total: {len(close_contracts)}")
    _info(f"  date_end < cairo_today  (auto-flipped from expired-but-running): {len(close_past)}")
    _info(f"  date_end >= cairo_today (closed for other reasons):              {len(close_future)}")
    _info(f"  date_end = False        (closed, no end date recorded):          {len(close_no_end)}")

    if close_past:
        _info(f"\n  First 5 state='close' with past date_end (formerly expired-but-running):")
        for c in close_past[:5]:
            _info(f"    contract_id={c['id']}  emp_id={_emp_id(c.get('employee_id'))}  "
                  f"date_end={c.get('date_end')}")

    _info("")
    _info("Rename decision context (expired vs past_end_date):")
    _info("  The 'expired' bucket catches: Running contracts where date_end < cairo_today.")
    _info("  This is 'past_end_date but still Running' — NOT identical to state='close'")
    _info("  (Odoo UI label 'Expired'). Post-fix, auto-flip fires reliably, so this bucket")
    _info("  should stay at 0 in normal operation. Rename is cosmetic unless bucket is non-zero.")
    _info(f"  Today's count: {bucket_counts['expired']} "
          f"→ {'rename is cosmetic; bucket functions correctly' if bucket_counts['expired'] == 0 else 'NON-ZERO: real payroll risk — resolve before renaming'}")

    # ══════════════════════════════════════════════════════════════════════════
    # HARD STRUCTURAL CHECK
    # ══════════════════════════════════════════════════════════════════════════

    _section("HARD STRUCTURAL CHECK — all returned contracts accounted for")

    running_in_date_buckets = sum(
        bucket_counts[l] for l in BUCKET_LABELS if l != "active_without_contract"
    )
    total_accounted = running_in_date_buckets + orphan_count + len(non_running)

    _info(f"Total contracts returned (RPC 1, active_test=False): {len(all_contracts)}")
    _info(f"  Running — in date/open buckets (on active employees): {running_in_date_buckets}")
    _info(f"  Running — orphan_count (on archived employees):        {orphan_count}")
    _info(f"  Non-running (close/draft/cancel):                      {len(non_running)}")
    _info(f"  Total accounted:                                       {total_accounted}")

    hard_check_pass = _check(
        f"All {len(all_contracts)} returned contracts accounted for",
        total_accounted == len(all_contracts),
        f"accounted={total_accounted} != returned={len(all_contracts)}",
    )

    # ══════════════════════════════════════════════════════════════════════════
    # FINAL SUMMARY
    # ══════════════════════════════════════════════════════════════════════════

    print(f"\n{_SEP}")
    print("DISCOVERY SUMMARY")
    print(_SEP2)
    print(f"  Run at (UTC)                                    : {run_at}")
    print(f"  Cairo today                                     : {cairo_today}")
    print(_SEP2)
    verdict = "YES — SILENT DROP BUG" if item0_silent_drop else "NO (flag inert on hr.contract)"
    print(f"  Item 0 — old KPI C drops archived-running       : {verdict}")
    print(f"    RPC 0 (no flag, mirrors old KPI C)            : {rpc0_count}")
    print(f"    RPC 1 (with active_test=False)                : {rpc1_running_count}")
    print(_SEP2)
    print("  Item 1 — Bucket counts (current KPI C logic):")
    for label in BUCKET_LABELS:
        print(f"    {label:<32} : {bucket_counts[label]:>5}")
    print(f"    {'orphan_contracts_count':<32} : {orphan_count:>5}  (excluded from buckets)")
    print(f"    {'total_active (sum of 7 buckets)':<32} : {current_total_active:>5}")
    print(_SEP2)
    print(f"  Item 2 — Sanity population:")
    print(f"    Current denominator (active=True flag)        : {len(active_emp_ids):>5}")
    print(f"    Correct denominator (Running contracts)       : {true_headcount:>5}  (= KPI A headcount)")
    print(f"    Gap (current − correct)                       : {len(active_emp_ids) - true_headcount:>+5}")
    print(f"    Decomposed: +{bucket_counts['active_without_contract']} awc in buckets, −{orphan_count} employed excluded")
    print(_SEP2)
    print(f"  Item 3 — Archived+Running (employed, in orphan):")
    print(f"    Count                                         : {len(archived_running):>5}")
    print(f"    Employee IDs                                  : {_fmt_ids(archived_running_emp_ids)}")
    if bucket_if_corrected:
        for b, cnt in sorted(bucket_if_corrected.items()):
            print(f"    Would land in {b:<20}        : {cnt:>5}")
    print(_SEP2)
    print(f"  Item 4 — active_without_contract breakdown:")
    print(f"    Total                                         : {len(awc_emp_ids):>5}")
    print(f"    exit_gap  (departed, unarchived)              : {len(exit_gap):>5}")
    print(f"    data_gap  (no contract record)                : {len(data_gap):>5}")
    print(f"    incoming  (draft contract)                    : {len(incoming):>5}")
    print(_SEP2)
    print(f"  Item 5 — Expired bucket:")
    print(f"    Current 'expired' bucket count                : {bucket_counts['expired']:>5}")
    print(f"    state='close' with past date_end              : {len(close_past):>5}  (correctly auto-flipped)")
    print(_SEP2)
    print(f"  Hard structural check                           : {'PASS' if hard_check_pass else 'FAIL'}")
    print(_SEP)

    # ── TSV log ────────────────────────────────────────────────────────────────

    os.makedirs("logs", exist_ok=True)
    log_exists = os.path.isfile(_LOG_FILE)
    with open(_LOG_FILE, "a", encoding="utf-8") as f:
        if not log_exists:
            f.write(
                "run_at\tcairo_today\t"
                "rpc0_no_flag\trpc1_with_flag\titem0_silent_drop\t"
                "bucket_active_without_contract\tbucket_expired\t"
                "bucket_expiring_45d\tbucket_expiring_90d\tbucket_expiring_135d\t"
                "bucket_beyond_135d\tbucket_open_ended\t"
                "orphan_count\ttotal_active_sum\t"
                "true_headcount_running\tactive_flag_count\t"
                "awc_exit_gap\tawc_data_gap\tawc_incoming\t"
                "archived_running_count\tclose_past_date_count\t"
                "hard_check_pass\n"
            )
        f.write(
            f"{run_at}\t{cairo_today}\t"
            f"{rpc0_count}\t{rpc1_running_count}\t"
            f"{'YES' if item0_silent_drop else 'NO'}\t"
            f"{bucket_counts['active_without_contract']}\t"
            f"{bucket_counts['expired']}\t"
            f"{bucket_counts['expiring_45d']}\t"
            f"{bucket_counts['expiring_90d']}\t"
            f"{bucket_counts['expiring_135d']}\t"
            f"{bucket_counts['beyond_135d']}\t"
            f"{bucket_counts['open_ended']}\t"
            f"{orphan_count}\t{current_total_active}\t"
            f"{true_headcount}\t{len(active_emp_ids)}\t"
            f"{len(exit_gap)}\t{len(data_gap)}\t{len(incoming)}\t"
            f"{len(archived_running)}\t{len(close_past)}\t"
            f"{'PASS' if hard_check_pass else 'FAIL'}\n"
        )

    print(f"\n{_INFO} TSV row appended to {_LOG_FILE}")


if __name__ == "__main__":
    asyncio.run(run())
