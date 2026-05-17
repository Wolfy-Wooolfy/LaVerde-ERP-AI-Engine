"""
D0 Part 2 — KPI 6 field semantics deep discovery.

Answers four questions definitively before service code is written:

  A: What does rs.account.payment.installment.date represent?
     Sample 10 headers (state=post), compare date vs create_date vs
     write_date to determine if it is a system timestamp or a user-set date.

  B: Does HEADER.amount == SUM(LINE.amount)?
     For the same 10 sampled headers, compare HEADER.amount against the
     sum of all child line amounts. If they diverge, HEADER.amount has
     different semantics from the LINE model's amount.

  C: All monetary fields on the LINE model.
     fields_get + 5 sanitized sample records showing every monetary field's
     value. Goal: find a field clearly labeled "collected" or "paid".

  D: Alternative architecture — rs.installment + write_date:month.
     Test whether read_group on rs.installment with groupby=['write_date:month']
     and domain state=post, actual_paid>0, write_date>=6m_ago yields non-zero
     data across multiple months. Also sample 5 high-paid installments to
     check if write_date semantics are reasonable.

This script:
  - Calls ONLY read methods (search_read, search_count, read_group, fields_get).
  - Writes nothing to Odoo.
  - Costs $0 in AI.
  - Prints no PII (IDs only — no customer names, addresses, or contract refs).
  - Appends a summary row to logs/kpi6_discovery_part2.log.
  - Exits 0 on completion regardless of findings.

Usage:
    python -m scripts.discover_kpi6_field_semantics
"""

import asyncio
import io
import os
import sys
from datetime import date, datetime, timezone

from backend.shared.odoo.client import OdooClient

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Constants ─────────────────────────────────────────────────────────────────

_HEADER_MODEL = "rs.account.payment.installment"
_LINE_MODEL   = "rs.account.payment.installment.line"
_INST_MODEL   = "rs.installment"

_SEP  = "═" * 80
_SEP2 = "─" * 80
_LOG_FILE = "logs/kpi6_discovery_part2.log"

_INFO = "[INFO]"
_PASS = "[PASS]"
_FLAG = "[FLAG]"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _period_start(today: date) -> date:
    sm = today.month - 5
    sy = today.year
    if sm <= 0:
        sm += 12
        sy -= 1
    return date(sy, sm, 1)


def _egp(v: float) -> str:
    return f"{v:>24,.2f} EGP"


def _date_part(dt_str: str) -> str:
    return dt_str[:10] if dt_str else ""


def _parse_groupby_key(row: dict, *candidates: str) -> str:
    for c in candidates:
        v = row.get(c)
        if v is not None and v is not False:
            return str(v)
    return "(unknown)"


# ── TSV log ───────────────────────────────────────────────────────────────────

def _write_log(flags: list[str], notes: str) -> None:
    os.makedirs("logs", exist_ok=True)
    run_at = datetime.now(timezone.utc).isoformat()
    with open(_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n{'='*80}\n")
        f.write(f"run_at : {run_at}\n")
        f.write(f"flags  : {flags}\n")
        f.write(f"notes  : {notes}\n")
        f.write(f"{'='*80}\n")
    print(f"\n{_INFO} Summary appended to {_LOG_FILE}")


# ── Main ──────────────────────────────────────────────────────────────────────

async def run() -> None:
    today         = date.today()
    ps            = _period_start(today)
    run_at        = datetime.now(timezone.utc).isoformat()
    flags: list[str] = []
    notes: list[str] = []

    print(_SEP)
    print("KPI 6 — Field Semantics Deep Discovery  (D0 Part 2)")
    print(f"Run timestamp  : {run_at}")
    print(f"Today          : {today}")
    print(f"6-month window : {ps}  →  {today}")
    print(_SEP)

    async with OdooClient() as client:

        # ══════════════════════════════════════════════════════════════════════
        # SECTION A — What does HEADER.date represent?
        # ══════════════════════════════════════════════════════════════════════
        print()
        print(_SEP2)
        print("[A] HEADER.date semantics")
        print(f"    Model : {_HEADER_MODEL}  |  filter: state='post'  |  sample: 10 (most-recent by id)")
        print("    Compare HEADER.date vs create_date vs write_date")
        print(_SEP2)

        headers: list[dict] = await client.execute_kw(
            _HEADER_MODEL,
            "search_read",
            args=[[("state", "=", "post")]],
            kwargs={
                "fields": ["id", "date", "create_date", "write_date", "state", "amount"],
                "limit": 10,
                "order": "id desc",
            },
        )

        if not headers:
            print(f"    {_FLAG} Zero post-state headers found — cannot proceed.")
            flags.append("A_no_post_headers")
        else:
            print(f"    Sampled {len(headers)} records.")
            print()
            # Table
            W = {"id": 7, "date": 24, "cre": 24, "wri": 24, "amt": 20}
            print(
                f"    {'id':>{W['id']}}  {'HEADER.date':^{W['date']}}  "
                f"{'create_date':^{W['cre']}}  {'write_date':^{W['wri']}}  "
                f"{'HEADER.amount':>{W['amt']}}  match?"
            )
            print(
                f"    {'-'*W['id']}  {'-'*W['date']}  "
                f"{'-'*W['cre']}  {'-'*W['wri']}  {'-'*W['amt']}  -------"
            )

            match_cre_n = 0
            match_wri_n = 0
            for h in headers:
                hid  = h["id"]
                hdt  = str(h.get("date") or "")
                hcre = str(h.get("create_date") or "")
                hwri = str(h.get("write_date") or "")
                hamt = float(h.get("amount") or 0.0)

                mc = (_date_part(hdt) == _date_part(hcre)) if hdt and hcre else False
                mw = (_date_part(hdt) == _date_part(hwri)) if hdt and hwri else False
                if mc:
                    match_cre_n += 1
                if mw:
                    match_wri_n += 1

                tag = []
                if mc:
                    tag.append("=cre")
                if mw:
                    tag.append("=wri")
                match_str = ",".join(tag) if tag else "distinct"

                print(
                    f"    {hid:>{W['id']}}  {hdt[:W['date']]:^{W['date']}}  "
                    f"{hcre[:W['cre']]:^{W['cre']}}  {hwri[:W['wri']]:^{W['wri']}}  "
                    f"{hamt:>{W['amt']},.2f}  {match_str}"
                )

            n = len(headers)
            pct_c = match_cre_n / n * 100
            pct_w = match_wri_n / n * 100
            print()
            print(f"    date == create_date (date-part): {match_cre_n}/{n}  ({pct_c:.0f}%)")
            print(f"    date == write_date  (date-part): {match_wri_n}/{n}  ({pct_w:.0f}%)")
            print()

            if pct_c >= 80:
                msg = "HEADER.date matches create_date in ≥80% of cases — likely auto-populated system timestamp."
                print(f"    {_FLAG} {msg}")
                flags.append("A_date_matches_create_date")
                notes.append(msg)
            elif pct_w >= 80:
                msg = "HEADER.date matches write_date in ≥80% of cases — set on last edit."
                print(f"    {_FLAG} {msg}")
                flags.append("A_date_matches_write_date")
                notes.append(msg)
            else:
                msg = "HEADER.date is DISTINCT from both create_date and write_date — user-entered field."
                print(f"    {_PASS} {msg}")
                notes.append(msg)

        # ══════════════════════════════════════════════════════════════════════
        # SECTION B — HEADER.amount == SUM(LINE.amount)?
        # ══════════════════════════════════════════════════════════════════════
        print()
        print(_SEP2)
        print("[B] HEADER.amount  vs  SUM(LINE.amount)  —  same 10 sampled headers")
        print(_SEP2)

        if not headers:
            print(f"    {_FLAG} Skipped — no headers from Section A.")
        else:
            header_ids = [h["id"] for h in headers]

            # One batched read_group to get SUM(line.amount) per payment_id
            line_sums_raw: list[dict] = await client.execute_kw(
                _LINE_MODEL,
                "read_group",
                args=[
                    [("payment_id", "in", header_ids)],
                    ["amount:sum"],
                    ["payment_id"],
                ],
                kwargs={"lazy": False},
            )

            line_by_hid: dict[int, dict] = {}
            for row in line_sums_raw:
                pid_raw = row.get("payment_id")
                pid = int(pid_raw[0]) if isinstance(pid_raw, (list, tuple)) else int(pid_raw or 0)
                line_by_hid[pid] = {
                    "sum":   float(row.get("amount") or 0.0),
                    "count": int(row.get("__count") or 0),
                }

            print(
                f"    {'id':>7}  {'HEADER.amount':>22}  {'SUM(LINE.amount)':>22}  "
                f"{'Delta':>14}  {'Lines':>6}  match?"
            )
            print(
                f"    {'-'*7}  {'-'*22}  {'-'*22}  "
                f"{'-'*14}  {'-'*6}  ------"
            )

            all_match = True
            zero_line_headers = 0
            for h in headers:
                hid  = h["id"]
                hamt = float(h.get("amount") or 0.0)
                ls   = line_by_hid.get(hid, {"sum": 0.0, "count": 0})
                lsum = ls["sum"]
                lcnt = ls["count"]
                delta = hamt - lsum
                match = abs(delta) < 0.01
                if not match:
                    all_match = False
                if lcnt == 0:
                    zero_line_headers += 1
                tag = _PASS if match else _FLAG
                print(
                    f"    {hid:>7}  {hamt:>22,.2f}  {lsum:>22,.2f}  "
                    f"{delta:>+14,.2f}  {lcnt:>6}  {tag}"
                )

            print()
            if all_match:
                msg = "HEADER.amount == SUM(LINE.amount) for all sampled records — they are the same metric."
                print(f"    {_PASS} {msg}")
                notes.append("B: " + msg)
            else:
                msg = "HEADER.amount ≠ SUM(LINE.amount) for one or more records — different field semantics."
                print(f"    {_FLAG} {msg}")
                flags.append("B_header_amount_ne_sum_line_amount")
                notes.append("B: " + msg)

            if zero_line_headers > 0:
                print(f"    {_INFO} {zero_line_headers} header(s) have no child line records.")

        # ══════════════════════════════════════════════════════════════════════
        # SECTION C — All monetary fields on the LINE model
        # ══════════════════════════════════════════════════════════════════════
        print()
        print(_SEP2)
        print("[C] All monetary/float fields on the LINE model")
        print(f"    Model: {_LINE_MODEL}")
        print(_SEP2)

        all_line_fields: dict = await client.execute_kw(
            _LINE_MODEL,
            "fields_get",
            args=[],
            kwargs={"attributes": ["string", "type"]},
        )

        monetary_fields = sorted(
            [(fn, fi) for fn, fi in all_line_fields.items() if fi.get("type") in ("monetary", "float")],
            key=lambda x: x[0],
        )
        print(f"    Found {len(monetary_fields)} monetary/float fields:")
        print()
        print(f"    {'Field name':<42} {'Type':<10}  String label")
        print(f"    {'-'*42} {'-'*10}  {'-'*35}")
        for fname, finfo in monetary_fields:
            print(f"    {fname:<42} {finfo.get('type',''):<10}  {finfo.get('string','')}")

        # Sample 5 LINE records with all monetary fields
        print()
        print("    Sample 5 LINE records (sanitized — IDs only):")
        print()
        mon_field_names = [fn for fn, _ in monetary_fields]
        fetch_fields = ["id", "payment_id", "installment_id"] + mon_field_names
        line_samples: list[dict] = await client.execute_kw(
            _LINE_MODEL,
            "search_read",
            args=[[]],
            kwargs={"fields": fetch_fields, "limit": 5, "order": "id desc"},
        )

        if not line_samples:
            print("    (no records)")
        else:
            for row in line_samples:
                lid  = row.get("id")
                pid  = row.get("payment_id")
                iid  = row.get("installment_id")
                pid_id = pid[0] if isinstance(pid, (list, tuple)) else pid
                iid_id = iid[0] if isinstance(iid, (list, tuple)) else iid
                print(f"    line_id={lid}  payment_id={pid_id}  installment_id={iid_id}")
                for fname in mon_field_names:
                    val = row.get(fname)
                    if val is not None and val is not False and float(val) != 0.0:
                        fstr = next((fi.get("string", "") for fn, fi in monetary_fields if fn == fname), "")
                        print(f"      {fname:<42} {float(val):>20,.2f}   # {fstr}")
                print()

        # ══════════════════════════════════════════════════════════════════════
        # SECTION D — rs.installment + write_date:month architecture
        # ══════════════════════════════════════════════════════════════════════
        print()
        print(_SEP2)
        print("[D] Alternative architecture: rs.installment + write_date:month")
        print("    Hypothesis: write_date proxies for 'when actual_paid_amount was recorded'")
        print(_SEP2)

        ps_str = ps.isoformat()
        domain_d = [
            ("state", "=", "post"),
            ("x_studio_actual_paid_amount", ">", 0),
            ("write_date", ">=", ps_str),
        ]
        print(f"    Domain : state=post, x_studio_actual_paid_amount>0, write_date>={ps_str}")
        print()

        # D1: read_group by write_date:month
        print("    [D1] read_group grouped by write_date:month")
        print()
        try:
            rg_d: list[dict] = await client.execute_kw(
                _INST_MODEL,
                "read_group",
                args=[domain_d, ["x_studio_actual_paid_amount:sum"], ["write_date:month"]],
                kwargs={"lazy": False},
            )
            print(f"    {_PASS} read_group SUCCEEDED — {len(rg_d)} month group(s) returned")

            if rg_d:
                print()
                print("    Raw keys from first row (key-format documentation):")
                for k, v in rg_d[0].items():
                    print(f"      {k!r}: {v!r}")
                print()

            print(f"    {'Month (Odoo key)':<32} {'Records':>10}  {'SUM(actual_paid_amount)':>28}")
            print(f"    {'-'*32} {'-'*10}  {'-'*28}")
            total_d = 0.0
            total_d_cnt = 0
            for row in rg_d:
                mk    = _parse_groupby_key(row, "write_date:month", "write_date")
                amt   = float(row.get("x_studio_actual_paid_amount") or 0.0)
                cnt   = int(row.get("__count") or 0)
                total_d     += amt
                total_d_cnt += cnt
                print(f"    {mk:<32} {cnt:>10,}  {amt:>26,.2f} EGP")

            print(f"    {'-'*32} {'-'*10}  {'-'*28}")
            print(f"    {'TOTAL':<32} {total_d_cnt:>10,}  {total_d:>26,.2f} EGP")
            print()

            n_months = len(rg_d)
            if total_d <= 0:
                msg = "write_date:month approach yields ZERO total — not viable."
                print(f"    {_FLAG} {msg}")
                flags.append("D_write_date_zero_total")
                notes.append("D: " + msg)
            elif n_months < 3:
                msg = f"write_date:month yields only {n_months} month(s) — sparse coverage, may not be reliable."
                print(f"    {_FLAG} {msg}")
                flags.append(f"D_write_date_sparse_{n_months}_months")
                notes.append("D: " + msg)
            else:
                msg = f"write_date:month yields {n_months} month(s), total={total_d:,.2f} EGP — viable architecture candidate."
                print(f"    {_PASS} {msg}")
                notes.append("D: " + msg)

        except Exception as exc:
            print(f"    {_FLAG} read_group with write_date:month FAILED: {exc}")
            flags.append("D_write_date_groupby_failed")
            notes.append(f"D: write_date:month groupby failed — {type(exc).__name__}")

        # D2: Sample 5 high-actual-paid installments → check write_date semantics
        print()
        print("    [D2] Sample 5 rs.installment records with highest x_studio_actual_paid_amount")
        print("         (check whether write_date looks like a payment receipt date)")
        print()

        high_paid: list[dict] = await client.execute_kw(
            _INST_MODEL,
            "search_read",
            args=[[("state", "=", "post"), ("x_studio_actual_paid_amount", ">", 0)]],
            kwargs={
                "fields": [
                    "id", "date", "write_date", "create_date",
                    "x_studio_actual_paid_amount", "due_amount",
                    "payment_state",
                ],
                "limit": 5,
                "order": "x_studio_actual_paid_amount desc",
            },
        )

        if not high_paid:
            print("    (no records found)")
        else:
            W2 = {"id": 8, "dt": 12, "wri": 22, "cre": 22, "ap": 22, "due": 22, "ps": 12}
            print(
                f"    {'id':>{W2['id']}}  {'inst.date':^{W2['dt']}}  "
                f"{'write_date':^{W2['wri']}}  {'create_date':^{W2['cre']}}  "
                f"{'actual_paid':>{W2['ap']}}  {'due_amount':>{W2['due']}}  "
                f"{'pay_state':>{W2['ps']}}"
            )
            print(
                f"    {'-'*W2['id']}  {'-'*W2['dt']}  "
                f"{'-'*W2['wri']}  {'-'*W2['cre']}  "
                f"{'-'*W2['ap']}  {'-'*W2['due']}  {'-'*W2['ps']}"
            )
            for row in high_paid:
                rid  = row.get("id")
                rdt  = str(row.get("date") or "")[:W2["dt"]]
                rwri = str(row.get("write_date") or "")[:W2["wri"]]
                rcre = str(row.get("create_date") or "")[:W2["cre"]]
                rap  = float(row.get("x_studio_actual_paid_amount") or 0.0)
                rdue = float(row.get("due_amount") or 0.0)
                rps  = str(row.get("payment_state") or "")[:W2["ps"]]
                print(
                    f"    {rid:>{W2['id']}}  {rdt:^{W2['dt']}}  "
                    f"{rwri:^{W2['wri']}}  {rcre:^{W2['cre']}}  "
                    f"{rap:>{W2['ap']},.2f}  {rdue:>{W2['due']},.2f}  "
                    f"{rps:>{W2['ps']}}"
                )

        # ══════════════════════════════════════════════════════════════════════
        # SUMMARY
        # ══════════════════════════════════════════════════════════════════════
        print()
        print(_SEP)
        print("D0 PART 2 — FIELD SEMANTICS SUMMARY")
        print(_SEP)
        print()
        if flags:
            print(f"  Flags raised ({len(flags)}):")
            for fl in flags:
                print(f"    - {fl}")
        else:
            print(f"  {_PASS} No flags raised.")
        print()
        if notes:
            print("  Key findings:")
            for n in notes:
                print(f"    {n}")
        print()
        print("  Decisions required before D1:")
        print("  A: Is HEADER.date a valid cash-receipt axis for KPI 6?")
        print("     (see date vs create_date vs write_date comparison above)")
        print("  B: Is the LINE model amount field consistent with HEADER.amount?")
        print("     (if HEADER.amount == SUM(LINE.amount), they represent the same payment)")
        print("  C: Is there a LINE field better labeled as 'collected' vs 'Amount to Pay'?")
        print("     (review label list above — installment_paid_amount is the candidate)")
        print("  D: Does the rs.installment + write_date:month approach give")
        print("     better month coverage than the HEADER model approach?")
        print("     (If yes, this is the recommended D1 architecture)")
        print()
        print(_SEP)

    _write_log(flags, " | ".join(notes))


if __name__ == "__main__":
    asyncio.run(run())
