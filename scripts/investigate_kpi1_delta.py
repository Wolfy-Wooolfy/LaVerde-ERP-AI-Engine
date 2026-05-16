"""
One-off read-only investigation: why does SUM(rs.installment.amount)
with domain=[] return ~6.27B EGP while Odoo "All Installments" shows
~6.12B EGP (the 2026-05-14 baseline)?

Hypotheses tested in order:
  A. The model has an 'active' field and the view hides inactive records
  B. The Odoo UI filters by state (e.g. excludes draft / cancel)
  C. A company_id or other auto-filter field is hiding records

This script:
  - Calls ONLY read methods (search_count, read_group, fields_get).
  - Writes nothing to Odoo.
  - Costs $0 in AI.
  - Prints no PII (no customer names, IDs, or addresses).
  - Exits 0 on completion regardless of findings.

Usage:
    python scripts/investigate_kpi1_delta.py
"""

import asyncio
import io
import sys

from backend.shared.odoo.client import OdooClient

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_MODEL = "rs.installment"
_SEP = "═" * 66
_SEP2 = "─" * 66
_BASELINE = 6_123_549_625.23   # Odoo UI "All Installments → Amount" as of 2026-05-14
_BACKEND = 6_266_498_967.23    # what domain=[] returned 2026-05-16


def _egp(v: float) -> str:
    return f"{v:>22,.2f} EGP"


def _rg_first(rows: list, field: str) -> tuple[float, int]:
    """Return (SUM, count) from the first row of a read_group result."""
    row = rows[0] if rows else {}
    return float(row.get(field) or 0.0), int(row.get("__count") or 0)


async def run() -> None:
    print(_SEP)
    print("KPI 1 Delta Investigation — 2026-05-16")
    print(_SEP)
    print(f"  Backend (domain=[])  : {_egp(_BACKEND)}")
    print(f"  Odoo UI (All Inst.)  : {_egp(_BASELINE)}")
    print(f"  Delta to explain     : {_egp(_BACKEND - _BASELINE)}")
    print(_SEP)

    async with OdooClient() as client:

        # ── Section 1: Confirm baseline ──────────────────────────────────────
        print("\n[1] Confirm domain=[] SUM(amount)")
        rows = await client.execute_kw(
            _MODEL, "read_group",
            args=[[], ["amount"], []], kwargs={"lazy": False},
        )
        v0, c0 = _rg_first(rows, "amount")
        print(f"    SUM(amount) = {_egp(v0)}")
        print(f"    count       = {c0:,}")

        # ── Section 2: fields_get — look for active and other suspects ───────
        print("\n[2] fields_get — scanning for auto-filter fields")
        all_fields: dict = await client.execute_kw(
            _MODEL, "fields_get",
            args=[], kwargs={"attributes": ["string", "type", "default"]},
        )
        print(f"    Total fields on {_MODEL}: {len(all_fields)}")

        SUSPECTS = ("active", "company_id", "display_type", "archive",
                    "state", "payment_state")
        for fname in SUSPECTS:
            fmeta = all_fields.get(fname)
            if fmeta:
                print(f"    [FOUND]  {fname:20s} "
                      f"type={fmeta.get('type')!r:15}  "
                      f"string={fmeta.get('string')!r:25}  "
                      f"default={fmeta.get('default')!r}")
            else:
                print(f"    [ABSENT] {fname}")

        active_exists = "active" in all_fields

        # ── Section 3: active=True filter ─────────────────────────────────────
        print("\n[3] domain=[('active','=',True)]")
        try:
            rows_at = await client.execute_kw(
                _MODEL, "read_group",
                args=[[("active", "=", True)], ["amount"], []], kwargs={"lazy": False},
            )
            v_at, c_at = _rg_first(rows_at, "amount")
            print(f"    SUM(amount) = {_egp(v_at)}")
            print(f"    count       = {c_at:,}")
            print(f"    delta vs UI = {v_at - _BASELINE:+,.2f} EGP")
            if abs(v_at - _BASELINE) < 1.0:
                print("    >>> HYPOTHESIS A CONFIRMED: active=True matches Odoo UI")
        except Exception as exc:
            print(f"    ERROR: {exc}")

        # ── Section 4: active=False filter ────────────────────────────────────
        print("\n[4] domain=[('active','=',False)]")
        try:
            rows_af = await client.execute_kw(
                _MODEL, "read_group",
                args=[[("active", "=", False)], ["amount"], []], kwargs={"lazy": False},
            )
            v_af, c_af = _rg_first(rows_af, "amount")
            print(f"    SUM(amount) = {_egp(v_af)}")
            print(f"    count       = {c_af:,}")
        except Exception as exc:
            print(f"    ERROR: {exc}")

        # ── Section 5: Group by state (no domain) ────────────────────────────
        print("\n[5] read_group grouped by state, domain=[]")
        rows_s = await client.execute_kw(
            _MODEL, "read_group",
            args=[[], ["amount"], ["state"]], kwargs={"lazy": False},
        )
        state_total_v = 0.0
        state_total_c = 0
        for row in rows_s:
            s = row.get("state", "?")
            v = float(row.get("amount") or 0.0)
            c = int(row.get("__count") or 0)
            state_total_v += v
            state_total_c += c
            print(f"    state={s!r:10}  count={c:>6,}  SUM(amount)={_egp(v)}")
        print(f"    {'TOTAL':16}  count={state_total_c:>6,}  SUM(amount)={_egp(state_total_v)}")

        # ── Section 6: state='post' only ─────────────────────────────────────
        print("\n[6] domain=[('state','=','post')]")
        rows_post = await client.execute_kw(
            _MODEL, "read_group",
            args=[[("state", "=", "post")], ["amount"], []], kwargs={"lazy": False},
        )
        v_post, c_post = _rg_first(rows_post, "amount")
        print(f"    SUM(amount) = {_egp(v_post)}")
        print(f"    count       = {c_post:,}")
        print(f"    delta vs UI = {v_post - _BASELINE:+,.2f} EGP")
        if abs(v_post - _BASELINE) < 1.0:
            print("    >>> HYPOTHESIS B CONFIRMED: state='post' matches Odoo UI exactly")
        elif abs(v_post - _BASELINE) < 100_000:
            print("    >>> NEAR MATCH (within 100K EGP)")

        # ── Section 7: state != 'cancel' ─────────────────────────────────────
        print("\n[7] domain=[('state','!=','cancel')]")
        rows_nc = await client.execute_kw(
            _MODEL, "read_group",
            args=[[("state", "!=", "cancel")], ["amount"], []], kwargs={"lazy": False},
        )
        v_nc, c_nc = _rg_first(rows_nc, "amount")
        print(f"    SUM(amount) = {_egp(v_nc)}")
        print(f"    count       = {c_nc:,}")
        print(f"    delta vs UI = {v_nc - _BASELINE:+,.2f} EGP")
        if abs(v_nc - _BASELINE) < 1.0:
            print("    >>> state!='cancel' matches Odoo UI exactly")

        # ── Section 8: state not in draft or cancel ───────────────────────────
        print("\n[8] domain=[('state','not in',['draft','cancel'])]")
        rows_nd = await client.execute_kw(
            _MODEL, "read_group",
            args=[[("state", "not in", ["draft", "cancel"])], ["amount"], []],
            kwargs={"lazy": False},
        )
        v_nd, c_nd = _rg_first(rows_nd, "amount")
        print(f"    SUM(amount) = {_egp(v_nd)}")
        print(f"    count       = {c_nd:,}")
        print(f"    delta vs UI = {v_nd - _BASELINE:+,.2f} EGP")
        if abs(v_nd - _BASELINE) < 1.0:
            print("    >>> state not in [draft,cancel] matches Odoo UI exactly")

        # ── Section 9: Draft + cancel isolation ──────────────────────────────
        print("\n[9] Isolate draft records — domain=[('state','=','draft')]")
        rows_dr = await client.execute_kw(
            _MODEL, "read_group",
            args=[[("state", "=", "draft")], ["amount"], []], kwargs={"lazy": False},
        )
        v_dr, c_dr = _rg_first(rows_dr, "amount")
        print(f"    SUM(amount) = {_egp(v_dr)}")
        print(f"    count       = {c_dr:,}")

        print("\n[10] Isolate cancel records — domain=[('state','=','cancel')]")
        rows_ca = await client.execute_kw(
            _MODEL, "read_group",
            args=[[("state", "=", "cancel")], ["amount"], []], kwargs={"lazy": False},
        )
        v_ca, c_ca = _rg_first(rows_ca, "amount")
        print(f"    SUM(amount) = {_egp(v_ca)}")
        print(f"    count       = {c_ca:,}")
        print(f"    draft + cancel SUM = {_egp(v_dr + v_ca)}")
        print(f"    draft + cancel count = {c_dr + c_ca:,}")
        print(f"    domain=[] minus (draft+cancel) = {_egp(v0 - v_dr - v_ca)}")
        print(f"    vs Odoo UI baseline             = {_egp(_BASELINE)}")
        diff_excl = (v0 - v_dr - v_ca) - _BASELINE
        print(f"    remaining delta                 = {diff_excl:+,.2f} EGP")

        # ── Summary ───────────────────────────────────────────────────────────
        print(f"\n{_SEP}")
        print("SUMMARY")
        print(_SEP2)
        print(f"  active field present on {_MODEL}: {active_exists}")
        print(f"  domain=[] SUM:              {_egp(v0)}")
        print(f"  active=True SUM:            {_egp(v_at) if 'v_at' in dir() else 'ERROR'}")
        print(f"  state='post' SUM:           {_egp(v_post)}")
        print(f"  state!='cancel' SUM:        {_egp(v_nc)}")
        print(f"  state not in[d,c] SUM:      {_egp(v_nd)}")
        print(f"  Odoo UI baseline:           {_egp(_BASELINE)}")
        print(_SEP)


if __name__ == "__main__":
    asyncio.run(run())
