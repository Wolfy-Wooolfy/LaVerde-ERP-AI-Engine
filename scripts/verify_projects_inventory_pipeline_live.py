"""
scripts/verify_projects_inventory_pipeline_live.py — Projects Inventory CONTRACTS
PIPELINE identity-equal LIVE verification (READ-ONLY, $0 AI).

Proves get_contracts_pipeline() matches an INDEPENDENT recomputation of the pre-confirm
funnel: the same non-cancel rs.contract population grouped by stage, with the same
days-in-stage per contract. Every "SCRIPT" number is computed by THIS file from its own
raw Odoo reads; the "MODULE" numbers come from get_contracts_pipeline() (injected with
the same read-only client). NOTHING here writes to Odoo.

INDEPENDENCE RULE
    Imports CONSTANTS from domain.py only — the contract/chatter model and field names,
    PIPELINE_STAGE_STATES, PIPELINE_REVIEW_STAGES, the draft/confirm/delivered/cancel
    state literals, the tracking dotted paths. Those ARE the locked spec.
    Imports NO LOGIC: _group_contracts(), _fetch_stage_entry_dates(), _cairo_date(),
    _days_since(), _res_id_of() and _paged_search_read() are NOT imported. The chatter
    resolution, the UTC->Cairo conversion, the day arithmetic, the grouping, the sort
    and the paging are all reimplemented below.

DAYS IN STAGE, reimplemented locally: write_date is never read (a bulk edit gives most
of the live funnel one shared stamp, so it dates the edit, not the stage entry). The
stage-entry day is the LATEST mail.message whose mail.tracking.value rows record a
change to rs.contract.state, filtered SERVER-side on the TECHNICAL field name; a
contract that never changed state falls back to create_date. Odoo returns naive UTC, so
each stamp is read as UTC and converted to a Cairo calendar date before subtracting.

The module is called with THIS script's `today`, so a run straddling Cairo midnight
compares like with like instead of manufacturing an off-by-one.

What it checks:
  RAW         — the four independent fetches and the live contract-state vocabulary.
  GROUPS      — awaiting_action / under_review / confirmed / delivered counts +
                total_non_cancel, MODULE vs SCRIPT.
  CROSSCHECK  — every group count re-proved by search_count (a different RPC).
  RECONCILE   — Sigma(four groups) == total_non_cancel, on BOTH sides.
  MEMBERSHIP  — the contract_id SET of each row list is identical, not merely the size.
  PER-CONTRACT— days_in_stage, stage, stage_label, unit_id and unit_name compared row by
                row for every in-funnel contract.
  ORDER       — both row lists come back oldest-first (days desc, contract_id asc).

Method discipline: READ-ONLY (search_read / search_count only). ALLOWED_METHODS
untouched. No FastAPI. No OpenAI. AI cost = $0.00.

Usage (from project root):
    python scripts/verify_projects_inventory_pipeline_live.py
"""

import asyncio
import io
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.modules.projects_inventory.domain import (  # noqa: E402
    CONTRACT_CANCEL_STATE,
    CONTRACT_CONFIRM_STATE,
    CONTRACT_DELIVERED_STATE,
    CONTRACT_DRAFT_STATE,
    CONTRACT_MODEL,
    CONTRACT_STATE_FIELD,
    CONTRACT_UNIT_FIELD,
    MAIL_MESSAGE_DATE_FIELD,
    MAIL_MESSAGE_MODEL,
    MAIL_MESSAGE_MODEL_FIELD,
    MAIL_MESSAGE_RES_ID_FIELD,
    PIPELINE_REVIEW_STAGES,
    PIPELINE_STAGE_STATES,
    TRACKING_FIELD_MODEL_PATH,
    TRACKING_FIELD_NAME_PATH,
    TRACKING_MESSAGE_FIELD,
    TRACKING_MODEL,
)
from backend.modules.projects_inventory.services import cache as _cache  # noqa: E402
from backend.modules.projects_inventory.services.pipeline_service import (  # noqa: E402
    get_contracts_pipeline,
)
from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient  # noqa: E402

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_SEP = "=" * 100
_SEP2 = "-" * 100
_PAGE = 5000
_CAIRO = ZoneInfo("Africa/Cairo")


def _ok(flag: bool) -> str:
    return "PASS" if flag else "**FAIL**"


# ── local re-implementations (NOT imported from the service) ──────────────────


def _m2o_id(value):
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return int(value[0])
    return None


def _m2o_name(value):
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return str(value[1])
    return None


def _plain_id(value):
    """mail.message.res_id is a many2one_reference — a plain int. Tolerate an m2o pair."""
    if isinstance(value, (list, tuple)):
        return _m2o_id(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _cairo_day(stamp):
    """Naive-UTC 'YYYY-MM-DD HH:MM:SS' -> Cairo calendar date. Local copy."""
    if not stamp:
        return None
    try:
        naive = datetime.strptime(str(stamp)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return naive.replace(tzinfo=timezone.utc).astimezone(_CAIRO).date()


def _days(stage_day, today: date) -> int:
    if stage_day is None:
        return 0
    return max(0, (today - stage_day).days)


async def _read_all(client, model, domain, fields) -> list[dict]:
    """This script's OWN paged search_read."""
    rows, offset = [], 0
    while True:
        page = await client.execute_kw(
            model, "search_read", args=[domain],
            kwargs={"fields": fields, "order": "id", "limit": _PAGE, "offset": offset},
        )
        rows.extend(page)
        if len(page) < _PAGE:
            break
        offset += _PAGE
    return rows


async def _count(client, model, domain) -> int:
    return await client.execute_kw(model, "search_count", args=[domain])


def _fmt_entry(e: dict) -> str:
    stage = e.get("stage") or "-"
    return (f"{e['contract_id']:>6} {str(e['name'])[:14]:<14} {str(e['unit_name'])[:22]:<22} "
            f"{stage:<12} {e['days_in_stage']:>5}d")


async def main():
    cairo_today = datetime.now(_CAIRO).date()

    print(_SEP)
    print("  PROJECTS INVENTORY — CONTRACTS PIPELINE IDENTITY-EQUAL LIVE VERIFICATION")
    print("  (READ-ONLY, $0). SCRIPT numbers are recomputed here from raw reads; no")
    print("  grouping / chatter / day-arithmetic code is imported from the service.")
    print(f"  ALLOWED_METHODS : {sorted(ALLOWED_METHODS)}")
    print(f"  Models          : {CONTRACT_MODEL} / {MAIL_MESSAGE_MODEL} / {TRACKING_MODEL}")
    print(f"  Funnel states   : {list(PIPELINE_STAGE_STATES)}")
    print(f"  Review desks    : {PIPELINE_REVIEW_STAGES}")
    print(f"  Cairo today     : {cairo_today.isoformat()}")
    print(_SEP)
    print()

    fail = 0
    _cache.clear()

    async with OdooClient() as client:
        # ── INDEPENDENT raw fetches ────────────────────────────────────────────
        contracts = await _read_all(
            client, CONTRACT_MODEL,
            [(CONTRACT_STATE_FIELD, "!=", CONTRACT_CANCEL_STATE)],
            ["id", "name", CONTRACT_STATE_FIELD, CONTRACT_UNIT_FIELD, "create_date"],
        )
        funnel_ids = [
            c["id"] for c in contracts
            if c.get(CONTRACT_STATE_FIELD) in PIPELINE_STAGE_STATES
        ]

        messages = []
        tracking = []
        if funnel_ids:
            messages = await _read_all(
                client, MAIL_MESSAGE_MODEL,
                [(MAIL_MESSAGE_MODEL_FIELD, "=", CONTRACT_MODEL),
                 (MAIL_MESSAGE_RES_ID_FIELD, "in", funnel_ids)],
                ["id", MAIL_MESSAGE_RES_ID_FIELD, MAIL_MESSAGE_DATE_FIELD],
            )
            if messages:
                tracking = await _read_all(
                    client, TRACKING_MODEL,
                    [(TRACKING_MESSAGE_FIELD, "in", [m["id"] for m in messages]),
                     (TRACKING_FIELD_NAME_PATH, "=", CONTRACT_STATE_FIELD),
                     (TRACKING_FIELD_MODEL_PATH, "=", CONTRACT_MODEL)],
                    [TRACKING_MESSAGE_FIELD],
                )

        seen_states = sorted({str(c.get(CONTRACT_STATE_FIELD)) for c in contracts})
        print(_SEP2)
        print("  RAW (this script's own reads)")
        print(f"    non-cancel contracts     : {len(contracts):>8,}")
        print(f"    in-funnel (stage) ids    : {len(funnel_ids):>8,}")
        print(f"    chatter messages         : {len(messages):>8,}")
        print(f"    state-change tracking    : {len(tracking):>8,}")
        print(f"    contract states seen     : {seen_states}")
        print()

        # ── the stage-entry day, recomputed locally ────────────────────────────
        state_msg_ids = set()
        for t in tracking:
            mid = t.get(TRACKING_MESSAGE_FIELD)
            mid = _m2o_id(mid) if isinstance(mid, (list, tuple)) else mid
            if isinstance(mid, int) and not isinstance(mid, bool):
                state_msg_ids.add(mid)

        latest: dict[int, str] = {}
        for m in messages:
            if m["id"] not in state_msg_ids:
                continue
            raw = m.get(MAIL_MESSAGE_DATE_FIELD)
            if not raw:
                continue
            rid = _plain_id(m.get(MAIL_MESSAGE_RES_ID_FIELD))
            if rid is None:
                continue
            cur = latest.get(rid)
            if cur is None or str(raw) > cur:
                latest[rid] = str(raw)
        stage_day = {rid: d for rid, d in
                     ((r, _cairo_day(v)) for r, v in latest.items()) if d is not None}
        print(f"    stage date from chatter for {len(stage_day):,} of {len(funnel_ids):,} "
              f"in-funnel contracts; the rest fall back to create_date")
        print()

        # ── INDEPENDENT grouping ───────────────────────────────────────────────
        s_awaiting, s_review = [], []
        s_confirmed = s_delivered = 0
        unplaceable: dict[str, int] = {}
        for c in contracts:
            state = c.get(CONTRACT_STATE_FIELD)
            if state == CONTRACT_CONFIRM_STATE:
                s_confirmed += 1
                continue
            if state == CONTRACT_DELIVERED_STATE:
                s_delivered += 1
                continue
            if state not in PIPELINE_STAGE_STATES:
                unplaceable[str(state)] = unplaceable.get(str(state), 0) + 1
                continue
            day = stage_day.get(c["id"]) or _cairo_day(c.get("create_date"))
            uid = _m2o_id(c.get(CONTRACT_UNIT_FIELD))
            entry = {
                "contract_id": c["id"],
                "name": c.get("name") or "",
                "unit_id": uid if uid is not None else 0,
                "unit_name": _m2o_name(c.get(CONTRACT_UNIT_FIELD)) or "—",
                "days_in_stage": _days(day, cairo_today),
                "stage": None if state == CONTRACT_DRAFT_STATE else state,
                "stage_label": None if state == CONTRACT_DRAFT_STATE
                else PIPELINE_REVIEW_STAGES[state],
            }
            (s_awaiting if state == CONTRACT_DRAFT_STATE else s_review).append(entry)

        if unplaceable:
            print(f"  ** {CONTRACT_MODEL} carries non-cancel state(s) outside the funnel "
                  f"vocabulary: {unplaceable}. STOP.")
            return 1

        s_awaiting.sort(key=lambda e: (-e["days_in_stage"], e["contract_id"]))
        s_review.sort(key=lambda e: (-e["days_in_stage"], e["contract_id"]))
        s_total = len(contracts)

        # ── the MODULE, measured against the SAME `today` ──────────────────────
        mod = await get_contracts_pipeline(client=client, today=cairo_today)

        # ── GROUP COUNTS ───────────────────────────────────────────────────────
        print(_SEP)
        print("  GROUP COUNTS — MODULE vs SCRIPT vs an independent search_count")
        print(_SEP)
        print(f"  {'group':<20} | {'MODULE':>9} | {'SCRIPT':>9} | {'COUNT RPC':>9} | result")
        print(f"  {'-'*20}-+-{'-'*9}-+-{'-'*9}-+-{'-'*9}-+-{'-'*8}")

        x_await = await _count(client, CONTRACT_MODEL,
                               [(CONTRACT_STATE_FIELD, "=", CONTRACT_DRAFT_STATE)])
        x_review = await _count(client, CONTRACT_MODEL,
                                [(CONTRACT_STATE_FIELD, "in", sorted(PIPELINE_REVIEW_STAGES))])
        x_conf = await _count(client, CONTRACT_MODEL,
                              [(CONTRACT_STATE_FIELD, "=", CONTRACT_CONFIRM_STATE)])
        x_deliv = await _count(client, CONTRACT_MODEL,
                               [(CONTRACT_STATE_FIELD, "=", CONTRACT_DELIVERED_STATE)])
        x_total = await _count(client, CONTRACT_MODEL,
                               [(CONTRACT_STATE_FIELD, "!=", CONTRACT_CANCEL_STATE)])

        for label, m, s, x in (
            ("awaiting_action", mod["awaiting_action_count"], len(s_awaiting), x_await),
            ("under_review", mod["under_review_count"], len(s_review), x_review),
            ("confirmed", mod["confirmed_count"], s_confirmed, x_conf),
            ("delivered", mod["delivered_count"], s_delivered, x_deliv),
            ("total_non_cancel", mod["total_non_cancel"], s_total, x_total),
        ):
            g_ok = m == s == x
            fail += 0 if g_ok else 1
            print(f"  {label:<20} | {m:>9,} | {s:>9,} | {x:>9,} | {_ok(g_ok)}")

        m_sigma = (mod["awaiting_action_count"] + mod["under_review_count"]
                   + mod["confirmed_count"] + mod["delivered_count"])
        s_sigma = len(s_awaiting) + len(s_review) + s_confirmed + s_delivered
        r_ok = m_sigma == mod["total_non_cancel"] and s_sigma == s_total
        fail += 0 if r_ok else 1
        print()
        print(f"  RECONCILE  Sigma(four groups) == total_non_cancel")
        print(f"    MODULE: {m_sigma:,} == {mod['total_non_cancel']:,}  {_ok(m_sigma == mod['total_non_cancel'])}")
        print(f"    SCRIPT: {s_sigma:,} == {s_total:,}  {_ok(s_sigma == s_total)}")
        rd_ok = mod["reference_date"] == cairo_today.isoformat()
        fail += 0 if rd_ok else 1
        print(f"  reference_date  MODULE={mod['reference_date']}  SCRIPT={cairo_today.isoformat()}  {_ok(rd_ok)}")
        print()

        # ── MEMBERSHIP + PER-CONTRACT parity, for BOTH row lists ───────────────
        for label, m_rows, s_rows in (
            ("AWAITING ACTION", mod["awaiting_action"], s_awaiting),
            ("UNDER REVIEW", mod["under_review"], s_review),
        ):
            print(_SEP)
            print(f"  {label} — row-by-row parity ({len(m_rows)} row(s))")
            print(_SEP)

            m_by = {e["contract_id"]: e for e in m_rows}
            s_by = {e["contract_id"]: e for e in s_rows}
            set_ok = set(m_by) == set(s_by)
            fail += 0 if set_ok else 1
            print(f"  MEMBERSHIP  contract_id sets identical  {_ok(set_ok)}")
            if not set_ok:
                print(f"    only in MODULE: {sorted(set(m_by) - set(s_by))[:20]}")
                print(f"    only in SCRIPT: {sorted(set(s_by) - set(m_by))[:20]}")

            bad = []
            for cid, me in m_by.items():
                se = s_by.get(cid)
                if se is None:
                    continue
                for field in ("name", "unit_id", "unit_name", "days_in_stage",
                              "stage", "stage_label"):
                    if me.get(field) != se.get(field):
                        bad.append((cid, field, me.get(field), se.get(field)))
            per_ok = not bad
            fail += 0 if per_ok else 1
            print(f"  PER-CONTRACT  name+unit+days_in_stage+stage+stage_label identical  {_ok(per_ok)}")
            for cid, field, mv, sv in bad[:15]:
                print(f"    contract {cid}: {field} MODULE={mv!r} SCRIPT={sv!r}")

            expected = [e["contract_id"] for e in s_rows]
            got = [e["contract_id"] for e in m_rows]
            ord_ok = got == expected
            fail += 0 if ord_ok else 1
            print(f"  ORDER  oldest first (days desc, contract_id asc)  {_ok(ord_ok)}")
            print()

            if m_rows:
                print(f"  {'   id':>6} {'name':<14} {'unit':<22} {'stage':<12} {'days':>6}")
                print(f"  {'-'*6} {'-'*14} {'-'*22} {'-'*12} {'-'*6}")
                for e in m_rows:
                    print(f"  {_fmt_entry(e)}")
                oldest = m_rows[0]["days_in_stage"]
                newest = m_rows[-1]["days_in_stage"]
                print(f"  (days_in_stage spans {newest}–{oldest})")
            else:
                print("  (no rows in this group)")
            print()

    print(_SEP)
    if fail == 0:
        print("  VERIFICATION COMPLETE — ALL CHECKS PASSED.")
    else:
        print(f"  VERIFICATION COMPLETE — {fail} CHECK(S) FAILED. STOP and report.")
    print(_SEP)
    return 1 if fail else 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except SystemExit:
        raise
    except Exception as exc:
        print(f"\n  FATAL ERROR: {exc}")
        import traceback
        traceback.print_exc()
        raise SystemExit(1)
