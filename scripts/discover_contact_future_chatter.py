"""
scripts/discover_contact_future_chatter.py — understand the "Contact in the Future"
CRM stage by dumping structured signals + a small sample of human Chatter notes
for HUMAN reading (READ-ONLY, NO AI, $0).

Goal: are leads parked in "Contact in the Future" live prospects asking to be
contacted later, or effectively dead/parked? Cheapest possible read: (1) a
note-vs-no-note headline aggregate, (2) ~30 stratified leads with their most
recent human notes, for us to read with our own eyes.

Method discipline:
  - READ-ONLY: search / search_read / search_count / read_group only.
    ALLOWED_METHODS untouched. No writes. No FastAPI. No OpenAI. AI cost = $0.00.
  - NO AI classification — note text is fetched and printed for human reading only.
  - Population over the target stage uses context={'active_test': False}.
  - Light PII masking on note bodies (emails + 4+ digit runs) — repo convention;
    staff author names and lead ids are internal and kept intact.

Usage (from project root; uvicorn NOT required):
    python scripts/discover_contact_future_chatter.py
"""

import asyncio
import html
import io
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient  # noqa: E402

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_LEAD = "crm.lead"
_MSG = "mail.message"
_STAGE = "crm.stage"
_SEP = "=" * 100
_SEP2 = "-" * 100
_CTX_ALL = {"active_test": False}

_STAGE_NAME = "Contact in the Future"
_STAGE_ID_EXPECTED = 29

_N_ACTIVE_SAMPLE = 20
_N_ARCHIVED_SAMPLE = 10
_NOTES_PER_LEAD = 4
_BODY_CHARS = 300

_TAG = re.compile(r"<[^>]+>")


# ── text helpers ──────────────────────────────────────────────────────────────

def _plain(body) -> str:
    """Strip HTML chatter body to trimmed plain text."""
    if not body:
        return ""
    s = str(body)
    s = re.sub(r"<\s*br\s*/?\s*>", " ", s, flags=re.I)
    s = re.sub(r"</\s*p\s*>", " ", s, flags=re.I)
    s = re.sub(r"</\s*div\s*>", " ", s, flags=re.I)
    s = _TAG.sub(" ", s)
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _mask(s: str) -> str:
    """Light PII mask: emails + 4+ digit runs (phone/ID-like). Repo convention."""
    s = re.sub(r"\S+@\S+", "<email>", s)
    s = re.sub(r"\d{4,}", "<num>", s)
    return s


def _author(v) -> str:
    if isinstance(v, (list, tuple)) and len(v) == 2:
        return str(v[1])
    return "(no author)"


def _stratified(items, k):
    """Evenly-spaced pick of k items across the (already sorted) list."""
    if k <= 0 or not items:
        return []
    if len(items) <= k:
        return list(items)
    step = len(items) / k
    return [items[int(i * step)] for i in range(k)]


# ── main ──────────────────────────────────────────────────────────────────────

async def main():
    run_at = datetime.now(timezone.utc)
    print(_SEP)
    print("  'CONTACT IN THE FUTURE' CHATTER SAMPLE (READ-ONLY, NO AI, $0)")
    print(f"  Run at (UTC)    : {run_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  ALLOWED_METHODS : {sorted(ALLOWED_METHODS)}")
    print("  READ-ONLY. Direct JSON-RPC. No FastAPI. No OpenAI. AI cost = $0.00")
    print("  Note bodies: HTML stripped; emails + 4+ digit runs masked (human-read only).")
    print(_SEP)
    print()

    async with OdooClient() as client:
        # ── confirm stage id by name ──────────────────────────────────────────
        stages = await client.execute_kw(
            _STAGE, "search_read",
            args=[[("name", "=", _STAGE_NAME)]],
            kwargs={"fields": ["id", "name"]},
        )
        if not stages:
            print(f"  FATAL: no crm.stage named {_STAGE_NAME!r}. Stop.")
            return
        stage_id = stages[0]["id"]
        match = "MATCHES" if stage_id == _STAGE_ID_EXPECTED else "DIFFERS from"
        print(f"  STAGE CONFIRM: name {_STAGE_NAME!r} -> id={stage_id}  "
              f"({match} prior-doc expected id={_STAGE_ID_EXPECTED})")
        if len(stages) > 1:
            print(f"  !! {len(stages)} stages share this name: "
                  f"{[(s['id']) for s in stages]} — using first.")
        print()

        stage_domain = [("stage_id", "=", stage_id)]

        # ── fetch the full stage population (id, active, create_date) ─────────
        leads = await client.execute_kw(
            _LEAD, "search_read",
            args=[stage_domain],
            kwargs={"fields": ["id", "active", "create_date"],
                    "context": _CTX_ALL, "order": "id"},
        )
        total = len(leads)
        lead_ids = [r["id"] for r in leads]
        active_flag = {r["id"]: bool(r.get("active")) for r in leads}
        create_date = {r["id"]: r.get("create_date") for r in leads}

        # ── note counts per lead (human comments only) via read_group ─────────
        note_domain = [("model", "=", _LEAD), ("res_id", "in", lead_ids),
                       ("message_type", "=", "comment")]
        rg = await client.execute_kw(
            _MSG, "read_group",
            args=[note_domain, ["res_id"], ["res_id"]],
            kwargs={"lazy": False},
        )
        note_count = {}
        for r in rg:
            rid = r.get("res_id")
            if isinstance(rid, (list, tuple)):
                rid = rid[0]
            note_count[rid] = r.get("__count") or 0

        # ── aggregate: note vs no-note, split active/archived ─────────────────
        a_note = a_none = ar_note = ar_none = 0
        for lid in lead_ids:
            has = note_count.get(lid, 0) > 0
            if active_flag[lid]:
                if has:
                    a_note += 1
                else:
                    a_none += 1
            else:
                if has:
                    ar_note += 1
                else:
                    ar_none += 1
        with_note = a_note + ar_note
        no_note = a_none + ar_none

        # ── deliverable 3: ONE-LINE headline at the very top ──────────────────
        print(f"  HEADLINE: {total:,} leads in '{_STAGE_NAME}' — "
              f"{with_note:,} ({100*with_note/max(total,1):.1f}%) have >=1 human note; "
              f"{no_note:,} ({100*no_note/max(total,1):.1f}%) are untouched (0 notes).")
        print()

        # ── deliverable 1: aggregate table ────────────────────────────────────
        print(_SEP)
        print("  DELIVERABLE 1 — NOTE-vs-NO-NOTE AGGREGATE")
        print(f"  stage domain  = {stage_domain}   context={_CTX_ALL}")
        print(f"  note domain   = [('model','=','crm.lead'),('res_id','in',<all stage leads>),"
              f"('message_type','=','comment')]")
        print(_SEP)
        print(f"  {'segment':<14} | {'>=1 note':>10} | {'0 notes':>10} | {'total':>10} | {'%w/note':>8}")
        print(f"  {'-'*14}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}-+-{'-'*8}")
        for seg, n_note, n_none in (
            ("active", a_note, a_none),
            ("archived", ar_note, ar_none),
        ):
            tot = n_note + n_none
            print(f"  {seg:<14} | {n_note:>10,} | {n_none:>10,} | {tot:>10,} | "
                  f"{100*n_note/max(tot,1):>7.1f}%")
        print(f"  {'-'*14}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}-+-{'-'*8}")
        print(f"  {'TOTAL':<14} | {with_note:>10,} | {no_note:>10,} | {total:>10,} | "
              f"{100*with_note/max(total,1):>7.1f}%")
        print()

        # ── deliverable 2: stratified sample dump (note-bearing leads) ────────
        # Sample is drawn from leads that HAVE >=1 human note (untouched leads
        # have nothing to read; their share is already in the headline above).
        active_with = sorted(l for l in lead_ids if active_flag[l] and note_count.get(l, 0) > 0)
        arch_with = sorted(l for l in lead_ids if not active_flag[l] and note_count.get(l, 0) > 0)
        sample = (_stratified(active_with, _N_ACTIVE_SAMPLE)
                  + _stratified(arch_with, _N_ARCHIVED_SAMPLE))

        print(_SEP)
        print(f"  DELIVERABLE 2 — SAMPLE DUMP ({len(sample)} leads: "
              f"{min(_N_ACTIVE_SAMPLE, len(active_with))} active + "
              f"{min(_N_ARCHIVED_SAMPLE, len(arch_with))} archived; stratified across id range,")
        print(f"  drawn from note-bearing leads; up to {_NOTES_PER_LEAD} most-recent human notes each)")
        print(_SEP)
        print()

        for lid in sample:
            notes = await client.execute_kw(
                _MSG, "search_read",
                args=[[("model", "=", _LEAD), ("res_id", "=", lid),
                       ("message_type", "=", "comment")]],
                kwargs={"fields": ["date", "author_id", "body"],
                        "order": "date desc", "limit": _NOTES_PER_LEAD},
            )
            most_recent = notes[0]["date"] if notes else "-"
            flag = "active" if active_flag[lid] else "ARCHIVED"
            print(_SEP2)
            print(f"  lead id={lid}  [{flag}]  created={create_date.get(lid)}  "
                  f"last_note={most_recent}  human_notes={note_count.get(lid,0)}")
            if not notes:
                print("     (no human notes retrievable)")
            for m in notes:
                body = _mask(_plain(m.get("body")))
                if len(body) > _BODY_CHARS:
                    body = body[:_BODY_CHARS] + "…"
                if not body:
                    body = "(empty after strip)"
                print(f"     {m.get('date')} — {_author(m.get('author_id'))}:")
                print(f"        {body}")
            print()

    print(_SEP)
    print("  DONE — aggregate + sample only. No AI, no classification, no module, no decision.")
    print(_SEP)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except SystemExit:
        raise
    except Exception as exc:
        print(f"\n  FATAL ERROR: {exc}")
        import traceback
        traceback.print_exc()
        raise SystemExit(1)
