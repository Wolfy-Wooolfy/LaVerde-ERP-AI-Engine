"""
Site-visit chatter probe — zero AI cost.
Tells us if Bug A (site visit returns empty) is Risk A (timeout),
Risk B (no data), or Risk C (code bug).

Run:
    python scripts/probe_site_visit.py
"""
import io
import os
import sys
import time
import uuid

import httpx
from dotenv import load_dotenv

# Force UTF-8 on Windows consoles
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

load_dotenv()

ODOO_URL = os.environ["ODOO_URL"].rstrip("/") + "/jsonrpc"
DB = os.environ["ODOO_DB"]
USER = os.environ["ODOO_USERNAME"]
KEY = os.environ["ODOO_API_KEY"]

BASE_DOMAIN = [
    ["type", "=", "opportunity"],
    ["opportunity_status", "=", "resolved"],
]


def rpc(c: httpx.Client, service: str, method: str, args: list):
    r = c.post(
        ODOO_URL,
        json={
            "jsonrpc": "2.0",
            "method": "call",
            "id": str(uuid.uuid4()),
            "params": {"service": service, "method": method, "args": args},
        },
        timeout=90,
    )
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(data["error"])
    return data["result"]


def odoo(c, model, method, domain, kwargs=None):
    return rpc(c, "object", "execute_kw", [DB, uid, KEY, model, method, [domain], kwargs or {}])


def _or_domain(conditions: list) -> list:
    """Build valid Odoo Polish-notation OR domain."""
    if not conditions:
        return []
    if len(conditions) == 1:
        return list(conditions)
    result: list = ["|"] * (len(conditions) - 1)
    result.extend(conditions)
    return result


with httpx.Client() as c:
    uid = rpc(c, "common", "authenticate", [DB, USER, KEY, {}])
    print(f"Authenticated (uid={uid})\n")

    # ── Part 1: Total mail.message count for CRM leads ────────────────────────
    print("=" * 55)
    print("PART 1: Total chatter messages for CRM leads")
    print("=" * 55)
    t0 = time.time()
    total_msgs = odoo(c, "mail.message", "search_count", [
        ["model", "=", "crm.lead"],
        ["message_type", "in", ["comment", "email"]],
    ])
    print(f"Total comment/email messages on crm.lead: {total_msgs:,}  ({time.time()-t0:.2f}s)\n")

    # ── Part 2: Individual keyword counts ─────────────────────────────────────
    print("=" * 55)
    print("PART 2: Per-keyword search_count on mail.message")
    print("        (crm.lead records only, comment/email type)")
    print("=" * 55)

    SITE_VISIT_KWS = [
        "معاينة",
        "زيارة",
        "شاف الموقع",
        "site visit",
        "visited",
        "viewing",
        "tour",
        "موعد معاينة",
        "موعد",
        "تم التواصل",
    ]

    PHONE_KWS = [
        "مردش",
        "مرد",
        "اتصلت",
        "كلمته",
        "didn't answer",
        "no response",
        "called",
        "no answer",
    ]

    print(f"\n{'Keyword':<28} {'msg matches':>12}  {'timing':>8}")
    print("-" * 55)
    for kw in SITE_VISIT_KWS + ["---phone---"] + PHONE_KWS:
        if kw == "---phone---":
            print(f"\n  — phone attempt keywords —")
            continue
        t0 = time.time()
        try:
            count = odoo(c, "mail.message", "search_count", [
                ["model", "=", "crm.lead"],
                ["message_type", "in", ["comment", "email"]],
                ["body", "ilike", kw],
            ])
            print(f"  {kw:<26} {count:>12,}  {time.time()-t0:>7.2f}s")
        except Exception as e:
            print(f"  {kw:<26} {'ERROR':>12}  {time.time()-t0:>7.2f}s  ({e})")

    # ── Part 3: Full OR query timing ──────────────────────────────────────────
    print("\n" + "=" * 55)
    print("PART 3: Full OR domain timing (all 7 site-visit keywords)")
    print("=" * 55)

    leaf_conditions = [["body", "ilike", kw] for kw in SITE_VISIT_KWS[:7]]
    full_domain = (
        _or_domain(leaf_conditions)
        + [["model", "=", "crm.lead"], ["message_type", "in", ["comment", "email"]]]
    )

    t0 = time.time()
    try:
        count = odoo(c, "mail.message", "search_count", full_domain)
        elapsed = time.time() - t0
        print(f"Full OR query: {count:,} matches in {elapsed:.2f}s")
        if elapsed > 5:
            print("  ⚠  > 5 seconds — Risk A (timeout) likely on production load")
        elif count == 0:
            print("  ⚠  Zero results — Risk B (no chatter data for site visits)")
        else:
            print(f"  ✓  {count:,} messages found — chatter data exists")
    except Exception as e:
        elapsed = time.time() - t0
        print(f"Full OR query FAILED after {elapsed:.2f}s: {e}")
        print("  ⚠  Risk A (query error / timeout) confirmed")

    # ── Part 4: Sample messages for matching keyword ──────────────────────────
    print("\n" + "=" * 55)
    print("PART 4: Sample messages for 'معاينة' keyword (if any)")
    print("=" * 55)
    try:
        samples = odoo(c, "mail.message", "search_read", [
            ["model", "=", "crm.lead"],
            ["message_type", "in", ["comment", "email"]],
            ["body", "ilike", "معاينة"],
        ], {"fields": ["res_id", "body", "author_id", "date"], "limit": 5, "order": "date desc"})

        if not samples:
            print("  No messages found for 'معاينة'")
        else:
            for m in samples:
                author = m["author_id"][1] if m.get("author_id") else "Unknown"
                body_snippet = (m.get("body") or "")[:120].replace("\n", " ").strip()
                print(f"\n  Lead #{m['res_id']} | {m.get('date','')} | {author}")
                print(f"  Body: {body_snippet}")
    except Exception as e:
        print(f"  Sample fetch failed: {e}")

    # ── Part 5: Cross-check — do matching leads exist in BASE_DOMAIN? ─────────
    print("\n" + "=" * 55)
    print("PART 5: Cross-check — do 'معاينة' leads exist as resolved opportunities?")
    print("=" * 55)
    try:
        msgs = odoo(c, "mail.message", "search_read", [
            ["model", "=", "crm.lead"],
            ["message_type", "in", ["comment", "email"]],
            ["body", "ilike", "معاينة"],
        ], {"fields": ["res_id"], "limit": 200})
        lead_ids = list({m["res_id"] for m in msgs if m.get("res_id")})
        print(f"  Lead IDs from معاينة chatter: {len(lead_ids)} unique leads")
        if lead_ids:
            in_scope = odoo(c, "crm.lead", "search_count",
                            BASE_DOMAIN + [["id", "in", lead_ids]])
            print(f"  Of those, in BASE_DOMAIN (resolved opps): {in_scope}")
            if in_scope == 0:
                print("  ⚠  All matching leads are NOT resolved opportunities — BASE_DOMAIN filters them out")
            else:
                print(f"  ✓  {in_scope} leads are in scope and should be returned by the handler")
    except Exception as e:
        print(f"  Cross-check failed: {e}")

print("\nProbe complete.")
