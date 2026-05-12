"""
Comprehensive chat verification — Phase 5 Bug Hunt.

Live data, all 17 intents, depth-first follow-up chains, data accuracy checks.

Usage:
    python scripts/verify_chat_comprehensive.py --section-a   # baseline only, zero AI cost
    python scripts/verify_chat_comprehensive.py --full        # all sections (~$0.12)

Section A runs direct Odoo RPC (zero AI cost, no server needed).
Sections B-E spin up FastAPI on port 8091 and fire real chat requests.

Failures are written to docs/PHASE_5_COMPREHENSIVE_BUG_HUNT.md incrementally.
Cost ceiling: $0.50 — script halts and asks before exceeding.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

# Force UTF-8 on Windows consoles
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

load_dotenv(dotenv_path=".env")

# ── Config ────────────────────────────────────────────────────────────────────

PORT = 8091  # Different from verify_chat.py (8090)
BASE_URL = f"http://127.0.0.1:{PORT}"
COST_CEILING = 0.50
COST_PRINT_EVERY = 5  # print running cost every N tests

USERNAME = os.environ.get("VERIFY_USERNAME", "admin")
PASSWORD = os.environ.get("VERIFY_PASSWORD", "password")
AUTH_HEADER = "Basic " + base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()

ODOO_URL = os.environ.get("ODOO_URL", "").rstrip("/") + "/jsonrpc"
ODOO_DB = os.environ.get("ODOO_DB", "")
ODOO_USER = os.environ.get("ODOO_USERNAME", "")
ODOO_KEY = os.environ.get("ODOO_API_KEY", "")

BASE_DOMAIN: list = [
    ["type", "=", "opportunity"],
    ["opportunity_status", "=", "resolved"],
]

PROJECT_ROOT = Path(__file__).parent.parent
DOCS_DIR = PROJECT_ROOT / "docs"
REPORT_PATH = DOCS_DIR / "PHASE_5_COMPREHENSIVE_BUG_HUNT.md"

# ── Global state ──────────────────────────────────────────────────────────────

_test_counter = 0
_total_tests_estimate = 110  # updated once we know exact count
_total_cost = 0.0
_failures: list[dict] = []
_passes = 0
_baseline: dict = {}


# ── Logging ───────────────────────────────────────────────────────────────────

def log(msg: str, level: str = "INFO") -> None:
    syms = {"INFO": "   ", "PASS": "[+]", "FAIL": "[!]", "STEP": "-->", "WARN": "[~]"}
    print(f"{syms.get(level, '   ')} {msg}", flush=True)


# ── Odoo direct RPC (Section A only, no AI) ───────────────────────────────────

def _rpc(service: str, method: str, args: list) -> Any:
    import urllib.request
    payload = json.dumps({
        "jsonrpc": "2.0", "method": "call", "id": str(uuid.uuid4()),
        "params": {"service": service, "method": method, "args": args},
    }).encode()
    req = urllib.request.Request(
        ODOO_URL, data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    if "error" in data:
        raise RuntimeError(f"Odoo RPC error: {data['error']}")
    return data["result"]


def odoo_uid() -> int:
    return _rpc("common", "authenticate", [ODOO_DB, ODOO_USER, ODOO_KEY, {}])


def odoo_call(uid: int, model: str, method: str, args: list, kwargs: dict | None = None) -> Any:
    return _rpc("object", "execute_kw",
                [ODOO_DB, uid, ODOO_KEY, model, method, args, kwargs or {}])


# ── Report writer ─────────────────────────────────────────────────────────────

def _init_report() -> None:
    DOCS_DIR.mkdir(exist_ok=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    REPORT_PATH.write_text(
        f"# Phase 5: Comprehensive Bug Hunt Report\n\n"
        f"**Generated**: {now}  \n"
        f"**Status**: In progress...\n\n"
        f"## Summary\n\n"
        f"| Metric | Value |\n"
        f"|--------|-------|\n"
        f"| Tests run | 0 |\n"
        f"| Failures | 0 |\n"
        f"| Total cost | $0.000000 |\n\n"
        f"## Failures\n\n",
        encoding="utf-8",
    )


def _append_failure_to_report(failure: dict) -> None:
    fid = failure["test_id"]
    with open(REPORT_PATH, "a", encoding="utf-8") as f:
        f.write(f"---\n\n")
        f.write(f"### {fid}: {failure['intent']} — {failure['reason'][:80]}\n\n")
        f.write(f"- **Test ID**: {fid}\n")
        f.write(f"- **Section**: {failure['section']}\n")
        f.write(f"- **Intent (expected)**: `{failure['intent']}`\n")
        f.write(f"- **Intent (classified)**: `{failure['intent_classified']}`\n")
        f.write(f"- **Language**: {failure['lang']}\n")
        f.write(f"- **Question sent**: {failure['question']}\n")
        f.write(f"- **Failure reason**: {failure['reason']}\n\n")
        f.write(f"**Full AI response:**\n```\n{failure['response']}\n```\n\n")
        f.write(f"**Suggested follow-ups returned:**\n")
        for fu in failure.get("followups", []):
            f.write(f"- {fu}\n")
        f.write(f"\n")
        f.write(f"**Data snapshot from handler:**\n```json\n")
        f.write(json.dumps(failure.get("data_snapshot", {}), ensure_ascii=False, indent=2))
        f.write(f"\n```\n\n")
        f.write(f"**Root cause category**: {failure.get('root_cause', 'TBD')}\n\n")


def _finalize_report() -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    content = REPORT_PATH.read_text(encoding="utf-8")
    # Update summary table in header
    content = re.sub(r"\| Tests run \| \d+ \|", f"| Tests run | {_test_counter} |", content)
    content = re.sub(r"\| Failures \| \d+ \|", f"| Failures | {len(_failures)} |", content)
    content = re.sub(r"\| Total cost \| \$[\d.]+ \|", f"| Total cost | ${_total_cost:.6f} |", content)
    content = content.replace("**Status**: In progress...", f"**Status**: Complete ({now})")
    REPORT_PATH.write_text(content, encoding="utf-8")


# ── Cost tracking ─────────────────────────────────────────────────────────────

def _add_cost(amount: float) -> None:
    global _total_cost
    _total_cost += amount
    if _total_cost >= COST_CEILING:
        log(f"COST CEILING REACHED: ${_total_cost:.4f} >= ${COST_CEILING:.2f}", "FAIL")
        log("Halting to protect budget.", "FAIL")
        _finalize_report()
        sys.exit(2)


def _print_cost_update() -> None:
    remaining = COST_CEILING - _total_cost
    log(f"[{_test_counter}/{_total_tests_estimate} tests] "
        f"Spent so far: ${_total_cost:.4f} | Remaining budget: ${remaining:.4f}")


# ── HTTP helpers ──────────────────────────────────────────────────────────────

async def wait_for_server(timeout: int = 45) -> None:
    log("Waiting for server to start...", "STEP")
    async with httpx.AsyncClient(timeout=5.0) as client:
        for _ in range(timeout):
            try:
                r = await client.get(
                    f"{BASE_URL}/api/v1/health",
                    headers={"Authorization": AUTH_HEADER},
                )
                if r.status_code == 200:
                    log("Server ready", "PASS")
                    return
            except Exception:
                pass
            await asyncio.sleep(1)
    raise RuntimeError(f"Server did not start within {timeout}s")


async def chat(
    client: httpx.AsyncClient,
    message: str,
    session_id: str,
    lang: str = "ar",
) -> dict:
    r = await client.post(
        f"{BASE_URL}/api/v1/chat/message",
        json={"session_id": session_id, "message": message},
        headers={"Authorization": AUTH_HEADER, "Cookie": f"lang={lang}"},
        timeout=60.0,
    )
    r.raise_for_status()
    return r.json()


# ── Test runner ───────────────────────────────────────────────────────────────

async def run_test(
    client: httpx.AsyncClient,
    test_id: str,
    section: str,
    intent: str,
    question: str,
    lang: str,
    session_id: str,
    checks: list[tuple[str, Any]],  # [(check_name, check_arg), ...]
    root_cause_hint: str = "",
) -> tuple[bool, dict]:
    """
    Run one chat test. Returns (passed, response_data).
    checks is a list of (check_type, arg) where check_type is one of:
      "not_clarification", "contains_number", "no_br", "no_mandup",
      "intent_is", "has_followups", "data_type_is", "not_empty_data"
    """
    global _test_counter, _passes

    _test_counter += 1

    try:
        resp = await chat(client, question, session_id=session_id, lang=lang)
    except httpx.HTTPStatusError as exc:
        _record_failure(
            test_id=test_id, section=section, intent=intent, question=question, lang=lang,
            intent_classified="HTTP_ERROR", response=str(exc),
            followups=[], data_snapshot={},
            reason=f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            root_cause="infrastructure",
        )
        if _test_counter % COST_PRINT_EVERY == 0:
            _print_cost_update()
        return False, {}
    except Exception as exc:
        _record_failure(
            test_id=test_id, section=section, intent=intent, question=question, lang=lang,
            intent_classified="EXCEPTION", response=str(exc),
            followups=[], data_snapshot={},
            reason=f"Exception: {exc}",
            root_cause="infrastructure",
        )
        if _test_counter % COST_PRINT_EVERY == 0:
            _print_cost_update()
        return False, {}

    msg = resp.get("message", {})
    content = msg.get("content", "")
    intent_classified = msg.get("intent", "unknown")
    data_snapshot = msg.get("data_snapshot") or {}
    followups = resp.get("suggested_followups", [])
    cost = msg.get("cost_usd", 0.0)

    _add_cost(cost)

    # Run all checks
    failures_this_test: list[str] = []
    for check_type, check_arg in checks:
        fail_reason = _run_check(check_type, check_arg, content, followups, intent_classified, data_snapshot)
        if fail_reason:
            failures_this_test.append(fail_reason)

    if _test_counter % COST_PRINT_EVERY == 0:
        _print_cost_update()

    if failures_this_test:
        reason = "; ".join(failures_this_test)
        _record_failure(
            test_id=test_id, section=section, intent=intent, question=question, lang=lang,
            intent_classified=intent_classified, response=content,
            followups=followups, data_snapshot=data_snapshot,
            reason=reason,
            root_cause=root_cause_hint or "TBD",
        )
        log(f"FAIL [{test_id}] {intent} — {reason[:100]}", "FAIL")
        return False, resp
    else:
        log(f"PASS [{test_id}] {intent} | intent={intent_classified} | cost=${cost:.5f}", "PASS")
        _passes += 1
        return True, resp


def _run_check(
    check_type: str,
    check_arg: Any,
    content: str,
    followups: list[str],
    intent_classified: str,
    data_snapshot: dict,
) -> str | None:
    """Return failure message string, or None if check passes."""
    if check_type == "not_clarification":
        patterns = [
            "لا تتوفر", "not enough", "جرّب أحد هذه", "try one of these",
            "I don't have enough", "لا أعرف", "لم أفهم", "couldn't find a stage",
            "I'm not sure I understood", "عذراً، لم أفهم",
        ]
        for p in patterns:
            if p.lower() in content.lower():
                return f"Clarification fallback detected (matched {p!r})"
        return None

    elif check_type == "contains_number":
        if not re.search(r"\d", content):
            return "No numeric value in response (expected a count)"
        return None

    elif check_type == "no_br":
        all_text = content + " ".join(followups)
        if re.search(r"<br\s*/?>", all_text, re.IGNORECASE):
            return "<br> HTML tag found in response or follow-ups"
        return None

    elif check_type == "no_mandup":
        all_text = content + " ".join(followups)
        if "مندوب" in all_text:
            return "Forbidden term 'مندوب' found (should be 'موظف مبيعات')"
        return None

    elif check_type == "intent_is":
        expected = check_arg
        if intent_classified != expected:
            return f"Intent mismatch: expected '{expected}', got '{intent_classified}'"
        return None

    elif check_type == "has_followups":
        min_count = check_arg if isinstance(check_arg, int) else 1
        if len(followups) < min_count:
            return f"Too few follow-ups: got {len(followups)}, expected >= {min_count}"
        return None

    elif check_type == "data_type_is":
        dt = data_snapshot.get("type", "")
        if dt != check_arg:
            return f"Data type mismatch: expected '{check_arg}', got '{dt}'"
        return None

    elif check_type == "not_empty_data":
        dt = data_snapshot.get("type", "")
        if dt in ("clarification_needed", "error", "unavailable", "not_found"):
            return f"Handler returned empty/error data type: '{dt}'"
        rows = data_snapshot.get("rows", [])
        leads = data_snapshot.get("leads", [])
        count = data_snapshot.get("count")
        if not rows and not leads and count is None and dt not in (
            "stage_count", "data_quality", "data_quality_full",
            "team_performance", "salesperson_performance", "general_summary",
            "recommendations", "lead_detail",
        ):
            return f"Handler returned empty data (type='{dt}', rows=[], count=None)"
        return None

    elif check_type == "count_matches_odoo":
        # check_arg = (stage_name, odoo_count)
        stage_name, odoo_count = check_arg
        nums = re.findall(r"\d[\d,]*", content)
        if not nums:
            return f"No number in AI response for stage '{stage_name}' (Odoo count={odoo_count})"
        ai_count = int(nums[0].replace(",", ""))
        if ai_count != odoo_count:
            return f"Count mismatch for '{stage_name}': AI={ai_count}, Odoo={odoo_count}"
        return None

    return None  # unknown check type — skip


def _record_failure(**kwargs: Any) -> None:
    _failures.append(kwargs)
    _append_failure_to_report(kwargs)


# ── Section A ─────────────────────────────────────────────────────────────────

def run_section_a() -> dict:
    """Fetch live Odoo data. Zero AI cost. Returns baseline dict for test parameterisation."""
    log("=" * 70)
    log("SECTION A: Live Odoo Baseline Fetch (zero AI cost, no server)", "STEP")
    log("=" * 70)

    uid = odoo_uid()
    log(f"Authenticated to Odoo (uid={uid})")

    # A1: All stages + total lead counts
    log("Fetching all pipeline stages with lead counts...", "STEP")
    stages_raw = odoo_call(uid, "crm.stage", "search_read", [[]], {"fields": ["id", "name"], "limit": 200})
    stage_counts = []
    for s in stages_raw:
        domain = BASE_DOMAIN + [["stage_id", "=", s["id"]]]
        rows = odoo_call(uid, "crm.lead", "read_group", [domain, ["__count"], []], {})
        count = rows[0].get("__count", 0) if rows else 0
        stage_counts.append({"id": s["id"], "name": s["name"], "lead_count": count})
    stage_counts.sort(key=lambda x: x["lead_count"], reverse=True)
    log(f"  Found {len(stage_counts)} stages")

    # A2: Overdue counts per stage
    log("Fetching overdue counts per stage...", "STEP")
    ov_domain = BASE_DOMAIN + [["activity_state", "=", "overdue"]]
    ov_stage_rows = odoo_call(uid, "crm.lead", "read_group",
                               [ov_domain, ["stage_id"], ["stage_id"]], {"orderby": "stage_id"})
    overdue_by_stage: dict[str, int] = {}
    for row in ov_stage_rows:
        st = row.get("stage_id")
        if st:
            overdue_by_stage[st[1]] = row.get("stage_id_count", 0)

    # A3: All teams + lead counts
    log("Fetching all sales teams with lead counts...", "STEP")
    teams_raw = odoo_call(uid, "crm.team", "search_read", [[]], {"fields": ["id", "name"], "limit": 100})
    team_counts = []
    for t in teams_raw:
        domain = BASE_DOMAIN + [["team_id", "=", t["id"]]]
        rows = odoo_call(uid, "crm.lead", "read_group", [domain, ["__count"], []], {})
        count = rows[0].get("__count", 0) if rows else 0
        team_counts.append({"id": t["id"], "name": t["name"], "lead_count": count})
    team_counts.sort(key=lambda x: x["lead_count"], reverse=True)
    log(f"  Found {len(team_counts)} teams")

    # A4: Overdue counts per team
    ov_team_rows = odoo_call(uid, "crm.lead", "read_group",
                              [ov_domain, ["team_id"], ["team_id"]], {"orderby": "team_id"})
    overdue_by_team: dict[str, int] = {}
    for row in ov_team_rows:
        t = row.get("team_id")
        if t:
            overdue_by_team[t[1]] = row.get("team_id_count", 0)

    # A5: Top 20 salespeople by total lead count
    log("Fetching top 20 salespeople...", "STEP")
    sp_rows = odoo_call(uid, "crm.lead", "read_group",
                        [BASE_DOMAIN, ["user_id"], ["user_id"]], {"orderby": "user_id"})
    salespeople = []
    for row in sp_rows:
        user = row.get("user_id")
        if user:
            salespeople.append({
                "id": user[0], "name": user[1],
                "lead_count": row.get("user_id_count", 0),
            })
    salespeople.sort(key=lambda x: x["lead_count"], reverse=True)
    salespeople = salespeople[:20]
    log(f"  Found {len(salespeople)} salespeople (top 20 by lead count)")

    # A6: Overdue counts per salesperson
    ov_sp_rows = odoo_call(uid, "crm.lead", "read_group",
                            [ov_domain, ["user_id"], ["user_id"]], {"orderby": "user_id"})
    overdue_by_sp: dict[str, int] = {}
    for row in ov_sp_rows:
        u = row.get("user_id")
        if u:
            overdue_by_sp[u[1]] = row.get("user_id_count", 0)

    # A7: Sample overdue lead IDs for lead_details_by_id tests
    log("Fetching sample overdue lead IDs...", "STEP")
    ov_leads = odoo_call(uid, "crm.lead", "search_read",
                          [ov_domain],
                          {"fields": ["id", "name", "stage_id", "user_id"], "limit": 10})
    sample_leads = []
    for lv in ov_leads[:5]:
        st = lv.get("stage_id")
        sp = lv.get("user_id")
        sample_leads.append({
            "id": lv["id"],
            "name": lv.get("name", ""),
            "stage": st[1] if st else "",
            "salesperson": sp[1] if sp else "",
        })
    log(f"  Got {len(sample_leads)} sample overdue leads")

    # A8: Grand totals
    total_rows = odoo_call(uid, "crm.lead", "read_group", [BASE_DOMAIN, ["__count"], []], {})
    total_leads = total_rows[0].get("__count", 0) if total_rows else 0
    ov_total_rows = odoo_call(uid, "crm.lead", "read_group", [ov_domain, ["__count"], []], {})
    total_overdue = ov_total_rows[0].get("__count", 0) if ov_total_rows else 0

    baseline = {
        "stages": stage_counts,
        "overdue_by_stage": overdue_by_stage,
        "teams": team_counts,
        "overdue_by_team": overdue_by_team,
        "salespeople": salespeople,
        "overdue_by_sp": overdue_by_sp,
        "sample_leads": sample_leads,
        "total_leads": total_leads,
        "total_overdue": total_overdue,
        "fetched_at": datetime.now().isoformat(),
    }

    _print_baseline(baseline)
    return baseline


def _print_baseline(b: dict) -> None:
    log("")
    log("=" * 70)
    log("SECTION A — BASELINE DATA")
    log("=" * 70)
    log(f"Total resolved opportunities : {b['total_leads']}")
    log(f"Total currently overdue      : {b['total_overdue']}")
    log("")

    log("PIPELINE STAGES (sorted by total lead count):")
    log(f"  {'#':>3}  {'Stage name':<45}  {'Total':>6}  {'Overdue':>7}")
    log(f"  {'-'*3}  {'-'*45}  {'-'*6}  {'-'*7}")
    for i, s in enumerate(b["stages"], 1):
        ov = b["overdue_by_stage"].get(s["name"], 0)
        log(f"  {i:>3}. {s['name']:<45}  {s['lead_count']:>6}  {ov:>7}")
    log("")

    log("SALES TEAMS (sorted by total lead count):")
    log(f"  {'#':>3}  {'Team name':<40}  {'Total':>6}  {'Overdue':>7}")
    log(f"  {'-'*3}  {'-'*40}  {'-'*6}  {'-'*7}")
    for i, t in enumerate(b["teams"], 1):
        ov = b["overdue_by_team"].get(t["name"], 0)
        log(f"  {i:>3}. {t['name']:<40}  {t['lead_count']:>6}  {ov:>7}")
    log("")

    log("TOP 20 SALESPEOPLE (sorted by total lead count):")
    log(f"  {'#':>3}  {'Name':<40}  {'Total':>6}  {'Overdue':>7}")
    log(f"  {'-'*3}  {'-'*40}  {'-'*6}  {'-'*7}")
    for i, sp in enumerate(b["salespeople"], 1):
        ov = b["overdue_by_sp"].get(sp["name"], 0)
        log(f"  {i:>3}. {sp['name']:<40}  {sp['lead_count']:>6}  {ov:>7}")
    log("")

    log("SAMPLE OVERDUE LEADS (for lead_details_by_id tests):")
    for lv in b["sample_leads"]:
        log(f"  ID={lv['id']:8d}  stage={lv['stage']:<25}  sp={lv['salesperson'][:30]}")
        log(f"            name: {lv['name'][:60]}")
    log("")
    log("=" * 70)
    log("END OF SECTION A")
    log("=" * 70)


# ── Section B: Per-intent test matrix ─────────────────────────────────────────

async def section_b(client: httpx.AsyncClient) -> int:
    """
    Test all 17 data intents.
    V1: Direct question with valid filter
    V2: Same intent, conversational phrasing
    V3: Edge case (empty filter, typo, plural, partial name)
    Recommendation intents: V1 only (cost control).
    """
    b = _baseline
    stages = b["stages"]
    teams = b["teams"]
    sps = b["salespeople"]
    sample_leads = b["sample_leads"]

    # Pick representative entities for parameterised questions
    # Use stages with significant lead counts
    stage_top = stages[0]["name"] if stages else "New"
    stage_2nd = stages[1]["name"] if len(stages) > 1 else "Follow up"
    stage_mid = stages[len(stages)//2]["name"] if len(stages) > 2 else "Interested"
    # Pick a stage that definitely has some leads
    stage_with_leads = next((s["name"] for s in stages if s["lead_count"] > 0), "New")

    team_top = teams[0]["name"] if teams else "Sales Team"
    team_2nd = teams[1]["name"] if len(teams) > 1 else "Sales Team"

    sp_top = sps[0]["name"] if sps else "Admin"
    sp_2nd = sps[1]["name"] if len(sps) > 1 else "Admin"
    # first name only for partial-match edge case
    sp_top_first = sp_top.split()[0] if sp_top else "Admin"

    lead_id_1 = sample_leads[0]["id"] if sample_leads else 1
    lead_id_bad = 99999999  # almost certainly doesn't exist

    # Re-Distribution specific variables (Bug I — highest-impact stage)
    rd_stage = next((s for s in stages if s["name"] == "Re-Distribution"), None)
    rd_total = rd_stage["lead_count"] if rd_stage else 2437
    rd_overdue = b["overdue_by_stage"].get("Re-Distribution", 243)

    # Define test matrix: (test_id, intent, question, lang, session_suffix, checks, root_cause_hint)
    tests = [
        # ── 1. list_overdue_by_salesperson ────────────────────────────────────
        ("B-01-V1", "list_overdue_by_salesperson",
         "إيه أعلى 5 موظفي مبيعات عندهم تأخر؟", "ar", "b01v1",
         [("not_clarification", None), ("no_br", None), ("no_mandup", None), ("has_followups", 2)],
         ""),

        ("B-01-V2", "list_overdue_by_salesperson",
         "محتاج أعرف مين المسؤولين عن أكتر العملاء المتأخرين", "ar", "b01v2",
         [("not_clarification", None), ("no_br", None), ("no_mandup", None)],
         "intent_parser_robustness"),

        ("B-01-V3", "list_overdue_by_salesperson",
         "Show me all salesperson overdue leads", "en", "b01v3",
         [("not_clarification", None), ("no_br", None), ("has_followups", 1)],
         ""),

        # ── 2. list_overdue_by_team ───────────────────────────────────────────
        ("B-02-V1", "list_overdue_by_team",
         "إيه الفرق اللي عندها أكتر تأخر؟", "ar", "b02v1",
         [("not_clarification", None), ("no_br", None), ("has_followups", 2)],
         ""),

        ("B-02-V2", "list_overdue_by_team",
         "أنهي فرق المبيعات متأخرة أكتر؟", "ar", "b02v2",
         [("not_clarification", None), ("no_br", None)],
         "intent_parser_robustness"),

        ("B-02-V3", "list_overdue_by_team",
         "Which teams are falling behind on follow-ups?", "en", "b02v3",
         [("not_clarification", None), ("no_br", None)],
         "intent_parser_robustness"),

        # ── 3. list_overdue_by_stage ──────────────────────────────────────────
        ("B-03-V1", "list_overdue_by_stage",
         "في أنهي مرحلة أكتر عملاء متأخرين؟", "ar", "b03v1",
         [("not_clarification", None), ("no_br", None), ("has_followups", 2)],
         ""),

        ("B-03-V2", "list_overdue_by_stage",
         "وزع التأخرات على المراحل المختلفة", "ar", "b03v2",
         [("not_clarification", None), ("no_br", None)],
         "intent_parser_robustness"),

        ("B-03-V3", "list_overdue_by_stage",
         "overdue leads per stage breakdown", "en", "b03v3",
         [("not_clarification", None), ("no_br", None)],
         "intent_parser_robustness"),

        # ── 4. count_by_stage ─────────────────────────────────────────────────
        ("B-04-V1", "count_by_stage",
         f"كم lead في مرحلة {stage_top}؟", "ar", "b04v1",
         [("not_clarification", None), ("contains_number", None), ("no_br", None)],
         ""),

        ("B-04-V2", "count_by_stage",
         f"أنا محتاج أعرف عدد العملاء اللي في {stage_2nd}", "ar", "b04v2",
         [("not_clarification", None), ("contains_number", None), ("no_br", None)],
         "intent_parser_robustness"),

        ("B-04-V3", "count_by_stage",
         "كم عميل في متابعة؟", "ar", "b04v3",  # Arabic alias for "Follow up"
         [("not_clarification", None), ("contains_number", None), ("no_br", None)],
         "stage_name_normalisation"),

        # ── 5. count_overdue_by_stage ─────────────────────────────────────────
        ("B-05-V1", "count_overdue_by_stage",
         f"كم lead متأخر في مرحلة {stage_top}؟", "ar", "b05v1",
         [("not_clarification", None), ("contains_number", None), ("no_br", None)],
         ""),

        ("B-05-V2", "count_overdue_by_stage",
         f"How many overdue leads are in {stage_2nd}?", "en", "b05v2",
         [("not_clarification", None), ("contains_number", None), ("no_br", None)],
         ""),

        ("B-05-V3", "count_overdue_by_stage",
         f"كام lead في مرحلة {stage_mid} محتاج متابعة عاجلة؟", "ar", "b05v3",
         [("not_clarification", None), ("contains_number", None), ("no_br", None)],
         "intent_parser_total_vs_overdue"),

        # ── 6. count_by_team ──────────────────────────────────────────────────
        # NOTE: handler returns OVERDUE count, not total — this is Bug D
        ("B-06-V1", "count_by_team",
         f"كام lead عند فريق {team_top}؟", "ar", "b06v1",
         [("not_clarification", None), ("contains_number", None), ("no_br", None)],
         "count_by_team_returns_overdue_not_total"),

        ("B-06-V2", "count_by_team",
         f"How many leads does {team_top} team have?", "en", "b06v2",
         [("not_clarification", None), ("contains_number", None), ("no_br", None)],
         "count_by_team_returns_overdue_not_total"),

        ("B-06-V3", "count_by_team",
         "كام lead في الفرق كلها؟", "ar", "b06v3",  # no specific team filter
         [("not_clarification", None), ("contains_number", None), ("no_br", None)],
         ""),

        # ── 7. count_by_salesperson ───────────────────────────────────────────
        # NOTE: handler returns OVERDUE count, not total — this is Bug D
        ("B-07-V1", "count_by_salesperson",
         f"كام lead عند {sp_top}؟", "ar", "b07v1",
         [("not_clarification", None), ("contains_number", None), ("no_br", None)],
         "count_by_sp_returns_overdue_not_total"),

        ("B-07-V2", "count_by_salesperson",
         f"عرضلي leads {sp_2nd}", "ar", "b07v2",
         [("not_clarification", None), ("contains_number", None), ("no_br", None)],
         "intent_parser_robustness"),

        ("B-07-V3", "count_by_salesperson",
         f"show me {sp_top_first} leads", "en", "b07v3",  # partial name
         [("not_clarification", None), ("contains_number", None), ("no_br", None)],
         "partial_name_matching"),

        # ── 8. lead_details_by_id ─────────────────────────────────────────────
        ("B-08-V1", "lead_details_by_id",
         f"عرضلي تفاصيل العميل رقم {lead_id_1}", "ar", "b08v1",
         [("not_clarification", None), ("no_br", None)],
         "lead_details_overdue_only"),

        ("B-08-V2", "lead_details_by_id",
         f"Show me details for lead ID {lead_id_1}", "en", "b08v2",
         [("not_clarification", None), ("no_br", None)],
         "lead_details_overdue_only"),

        ("B-08-V3", "lead_details_by_id",
         f"تفاصيل lead رقم {lead_id_bad}", "ar", "b08v3",  # non-existent ID
         [("no_br", None)],  # should NOT crash; not_found response is acceptable
         "lead_not_found_handling"),

        # ── 9. leads_with_site_visit_signal ───────────────────────────────────
        # Bug A: returns "not enough data" when no overdue leads have site_visit chatter
        ("B-09-V1", "leads_with_site_visit_signal",
         "عرضلي العملاء اللي طلبوا معاينة", "ar", "b09v1",
         [("not_clarification", None), ("no_br", None)],
         "site_visit_signal_empty_overdue_leads"),

        ("B-09-V2", "leads_with_site_visit_signal",
         "مين العملاء اللي عندهم اهتمام بمعاينة الموقع؟", "ar", "b09v2",
         [("not_clarification", None), ("no_br", None)],
         "site_visit_signal_empty_overdue_leads"),

        ("B-09-V3", "leads_with_site_visit_signal",
         "Show me clients who want to visit the property", "en", "b09v3",
         [("not_clarification", None), ("no_br", None)],
         "site_visit_signal_empty_overdue_leads"),

        # ── 10. leads_with_phone_attempt_signal ───────────────────────────────
        ("B-10-V1", "leads_with_phone_attempt_signal",
         "عرضلي العملاء اللي اتصلنا بيهم وما ردوش", "ar", "b10v1",
         [("not_clarification", None), ("no_br", None)],
         "phone_signal_empty_overdue_leads"),

        ("B-10-V2", "leads_with_phone_attempt_signal",
         "العملاء اللي حاولنا نكلمهم بس مردوش", "ar", "b10v2",
         [("not_clarification", None), ("no_br", None)],
         "phone_signal_empty_overdue_leads"),

        ("B-10-V3", "leads_with_phone_attempt_signal",
         "leads with failed phone contact attempts", "en", "b10v3",
         [("not_clarification", None), ("no_br", None)],
         "phone_signal_empty_overdue_leads"),

        # ── 11. missing_contact_summary ───────────────────────────────────────
        ("B-11-V1", "missing_contact_summary",
         "كام lead عنده بيانات ناقصة؟", "ar", "b11v1",
         [("not_clarification", None), ("contains_number", None), ("no_br", None)],
         ""),

        ("B-11-V2", "missing_contact_summary",
         "عملاء بدون أرقام تليفون", "ar", "b11v2",
         [("not_clarification", None), ("contains_number", None), ("no_br", None)],
         "intent_parser_robustness"),

        ("B-11-V3", "missing_contact_summary",
         "how many leads are missing phone numbers?", "en", "b11v3",
         [("not_clarification", None), ("contains_number", None), ("no_br", None)],
         ""),

        # ── 12. data_quality_summary ──────────────────────────────────────────
        ("B-12-V1", "data_quality_summary",
         "عرضلي تقرير جودة البيانات الكامل", "ar", "b12v1",
         [("not_clarification", None), ("no_br", None), ("has_followups", 1)],
         ""),

        ("B-12-V2", "data_quality_summary",
         "ما هي مشاكل البيانات الموجودة في النظام؟", "ar", "b12v2",
         [("not_clarification", None), ("no_br", None)],
         "intent_parser_robustness"),

        ("B-12-V3", "data_quality_summary",
         "data quality audit full report", "en", "b12v3",
         [("not_clarification", None), ("no_br", None)],
         "intent_parser_robustness"),

        # ── 13. team_performance_summary ──────────────────────────────────────
        ("B-13-V1", "team_performance_summary",
         "عرضلي أداء الفرق", "ar", "b13v1",
         [("not_clarification", None), ("no_br", None), ("has_followups", 2)],
         ""),

        ("B-13-V2", "team_performance_summary",
         "مقارنة أداء فرق المبيعات", "ar", "b13v2",
         [("not_clarification", None), ("no_br", None)],
         "intent_parser_robustness"),

        ("B-13-V3", "team_performance_summary",
         "Show me how each team is performing", "en", "b13v3",
         [("not_clarification", None), ("no_br", None)],
         "intent_parser_robustness"),

        # ── 14. salesperson_performance_summary ───────────────────────────────
        ("B-14-V1", "salesperson_performance_summary",
         "عرضلي أداء موظفي المبيعات", "ar", "b14v1",
         [("not_clarification", None), ("no_br", None), ("no_mandup", None), ("has_followups", 2)],
         ""),

        ("B-14-V2", "salesperson_performance_summary",
         "الأداء العام لموظفي المبيعات عندي", "ar", "b14v2",
         [("not_clarification", None), ("no_br", None), ("no_mandup", None)],
         "intent_parser_robustness"),

        ("B-14-V3", "salesperson_performance_summary",
         "overall sales employee performance overview", "en", "b14v3",
         [("not_clarification", None), ("no_br", None)],
         "intent_parser_robustness"),

        # ── 15. recommendation_top_priority (1 variation — cost control) ──────
        ("B-15-V1", "recommendation_top_priority",
         "اقترح عليّ 3 عملاء أتواصل معاهم النهارده", "ar", "b15v1",
         [("not_clarification", None), ("no_br", None), ("has_followups", 1)],
         ""),

        # ── 16. recommendation_for_salesperson (1 variation — Bug E confirmed) ──
        # Bug E: sp_filter is not applied — returns ALL top leads regardless
        ("B-16-V1", "recommendation_for_salesperson",
         f"اقترح عملاء لـ {sp_top} يتصل بيهم", "ar", "b16v1",
         [("not_clarification", None), ("no_br", None)],
         "recommendation_for_sp_filter_ignored"),

        # ── 17. free_form_analysis ────────────────────────────────────────────
        ("B-17-V1", "free_form_analysis",
         "مين أفضل موظف مبيعات عندي؟", "ar", "b17v1",
         [("not_clarification", None), ("no_br", None), ("no_mandup", None)],
         ""),

        ("B-17-V2", "free_form_analysis",
         "Who is the worst performing sales team?", "en", "b17v2",
         [("not_clarification", None), ("no_br", None)],
         ""),

        ("B-17-V3", "free_form_analysis",
         "حلل لي وضع الـ pipeline الحالي وقولي إيه أهم مشاكله", "ar", "b17v3",
         [("not_clarification", None), ("no_br", None)],
         "intent_parser_robustness"),

        # ── Bug I: Re-Distribution — most overdue stage, no Arabic alias ──────
        # B-RD-01: English stage name in Arabic question → should work (exact match)
        ("B-RD-01", "count_by_stage",
         "كم lead في مرحلة Re-Distribution؟", "ar", "brd01",
         [("not_clarification", None), ("contains_number", None), ("no_br", None),
          ("count_matches_odoo", ("Re-Distribution", rd_total))],
         "re_distribution_Bug_I"),

        # B-RD-02: Overdue count → should return 243 exactly
        ("B-RD-02", "count_overdue_by_stage",
         "كم lead متأخر في Re-Distribution؟", "ar", "brd02",
         [("not_clarification", None), ("contains_number", None), ("no_br", None),
          ("count_matches_odoo", ("Re-Distribution", rd_overdue))],
         "re_distribution_Bug_I"),

        # B-RD-03: Full Arabic phrasing — no alias in STAGE_AR_TO_EN → expected to fail gracefully
        # "إعادة التوزيع" is not in the normaliser. Should return stage_not_found or clarification.
        # We only assert no crash + no <br>. The failure IS the point — proves Bug I.
        ("B-RD-03", "count_by_stage",
         "كم lead في إعادة التوزيع؟", "ar", "brd03",
         [("no_br", None)],
         "re_distribution_arabic_no_alias_Bug_I"),
    ]

    log("")
    log("=" * 70)
    log(f"SECTION B: Per-intent matrix — {len(tests)} tests across 17 intents", "STEP")
    log("=" * 70)

    section_fails = 0
    for (tid, intent, question, lang, sess_sfx, checks, rc_hint) in tests:
        sess = f"b-{sess_sfx}-{uuid.uuid4().hex[:6]}"
        passed, _ = await run_test(
            client, tid, "B", intent, question, lang, sess, checks, rc_hint,
        )
        if not passed:
            section_fails += 1

    log(f"Section B complete: {len(tests) - section_fails}/{len(tests)} passed", "STEP")
    return section_fails


# ── Section C: Edge cases ─────────────────────────────────────────────────────

async def section_c(client: httpx.AsyncClient) -> int:
    b = _baseline
    stages = b["stages"]
    sps = b["salespeople"]
    stage_top = stages[0]["name"] if stages else "New"
    sp_top = sps[0]["name"] if sps else "Admin"

    tests = [
        # C1: Very long message (boundary of max_length=500)
        ("C-01", "free_form_analysis",
         "أ" * 499, "ar", "c01",
         [("no_br", None)],
         "message_length_boundary"),

        # C2: Mixed Arabic/English in same sentence
        ("C-02", "count_by_stage",
         f"Show me التأخرات في stage {stage_top}", "ar", "c02",
         [("not_clarification", None), ("no_br", None)],
         "mixed_language_parsing"),

        # C3: Emojis in input
        ("C-03", "free_form_analysis",
         "🤔 إيه أحسن lead عندي؟ 📊", "ar", "c03",
         [("no_br", None)],
         "emoji_in_input"),

        # C4: Stage typo — "Negotation" (missing i)
        ("C-04", "count_by_stage",
         "كم lead في مرحلة Negotation؟", "ar", "c04",
         [("no_br", None)],
         "stage_typo_handling"),

        # C5: Stage typo — "follo up"
        ("C-05", "count_by_stage",
         "كم lead في مرحلة follo up؟", "ar", "c05",
         [("no_br", None)],
         "stage_typo_handling"),

        # C6: Stage name ALL CAPS
        ("C-06", "count_by_stage",
         "How many leads in RESERVATION stage?", "en", "c06",
         [("not_clarification", None), ("contains_number", None), ("no_br", None)],
         "stage_case_sensitivity"),

        # C7: Stage name lowercase
        ("C-07", "count_by_stage",
         "how many leads are in new?", "en", "c07",
         [("not_clarification", None), ("contains_number", None), ("no_br", None)],
         "stage_case_sensitivity"),

        # C8: Salesperson first name only (partial match)
        ("C-08", "count_by_salesperson",
         f"كام lead عند {sp_top.split()[0]}؟", "ar", "c08",
         [("not_clarification", None), ("contains_number", None), ("no_br", None)],
         "partial_name_matching"),

        # C9: Subjective — "best" (should go to free_form_analysis)
        ("C-09", "free_form_analysis",
         "أحسن موظف عندي بالنسبة للإنتاجية", "ar", "c09",
         [("not_clarification", None), ("no_br", None), ("no_mandup", None)],
         "subjective_question"),

        # C10: Subjective — "worst team"
        ("C-10", "free_form_analysis",
         "What's the worst team in terms of overdue leads?", "en", "c10",
         [("not_clarification", None), ("no_br", None)],
         "subjective_question"),

        # C11: Out-of-scope — weather
        ("C-11", "unknown",
         "What's the weather in Cairo today?", "en", "c11",
         [("no_br", None)],  # should NOT crash; should return clarification
         "out_of_scope"),

        # C12: Out-of-scope — joke
        ("C-12", "unknown",
         "قولي نكتة", "ar", "c12",
         [("no_br", None)],
         "out_of_scope"),

        # C13: Conversational — greeting
        ("C-13", "greeting",
         "أهلاً وسهلاً", "ar", "c13",
         [("no_br", None)],
         "conversational"),

        # C14: Conversational — thanks
        ("C-14", "thanks",
         "شكراً جزيلاً على المساعدة", "ar", "c14",
         [("no_br", None)],
         "conversational"),

        # C15: Conversational — meta question
        ("C-15", "meta_question",
         "إنت AI ولا بشر؟", "ar", "c15",
         [("no_br", None)],
         "conversational"),

        # C16: Arabic stage alias — التفاوض → Negotiation
        # "Negotiation" does NOT exist in live Odoo — expect stage_not_found, NOT a crash.
        # Assertion is softened: just verify no <br> and no crash. Failure=graceful handling.
        ("C-16", "count_by_stage",
         "كم lead في التفاوض؟", "ar", "c16",
         [("no_br", None)],
         "arabic_stage_alias_Negotiation_not_in_Odoo_Bug_H"),

        # C17: Arabic stage alias — الحجز → Reservation
        ("C-17", "count_by_stage",
         "عدد العملاء في الحجز", "ar", "c17",
         [("not_clarification", None), ("contains_number", None), ("no_br", None)],
         "arabic_stage_alias"),

        # C18: Multi-turn — ask about salesperson, then ask "وفريقه؟"
        # Two separate chat calls on same session to test context
        ("C-18a", "salesperson_performance_summary",
         f"عرضلي أداء {sp_top}", "ar", "c18",
         [("not_clarification", None), ("no_br", None)],
         "multi_turn_context"),

        ("C-18b", "free_form_analysis",
         "وإيه اسم فريقه؟", "ar", "c18",  # same session — tests context window
         [("no_br", None)],
         "multi_turn_context"),

        # C19: count_by_stage for a stage that doesn't exist (should get stage_not_found)
        ("C-19", "count_by_stage",
         "كم lead في مرحلة مؤهل؟", "ar", "c19",  # invented stage name
         [("no_br", None)],
         "invented_stage_name"),

        # C20: No follow-up should use invented stage names
        ("C-20", "count_by_stage",
         f"كم lead في مرحلة {stage_top}؟", "ar", "c20",
         [("no_br", None), ("has_followups", 1)],
         "followup_stage_validation"),
    ]

    log("")
    log("=" * 70)
    log(f"SECTION C: Edge cases — {len(tests)} tests", "STEP")
    log("=" * 70)

    section_fails = 0
    for (tid, intent, question, lang, sess_sfx, checks, rc_hint) in tests:
        sess = f"c-{sess_sfx}-{uuid.uuid4().hex[:6]}"
        passed, _ = await run_test(
            client, tid, "C", intent, question, lang, sess, checks, rc_hint,
        )
        if not passed:
            section_fails += 1

    log(f"Section C complete: {len(tests) - section_fails}/{len(tests)} passed", "STEP")
    return section_fails


# ── Section D: Follow-up chains (depth-first, 3 levels) ──────────────────────

async def section_d(client: httpx.AsyncClient) -> int:
    """
    For 5 seed questions, follow all suggested follow-ups depth-first:
    - Depth 1: test all follow-ups of the seed
    - Depth 2: test the first follow-up of each depth-1 result
    (Depth 3 would be too expensive; 2 levels is enough to expose the pattern.)
    """
    b = _baseline
    stages = b["stages"]
    stage_top = stages[0]["name"] if stages else "New"

    seeds = [
        ("D-SEED1", "إيه أعلى 5 موظفي مبيعات عندهم تأخر؟", "ar"),
        ("D-SEED2", f"كم lead في مرحلة {stage_top}؟", "ar"),
        ("D-SEED3", "عرضلي العملاء اللي طلبوا معاينة", "ar"),  # Bug A seed
        ("D-SEED4", "اقترح عليّ 3 عملاء أتواصل معاهم النهارده", "ar"),
        ("D-SEED5", "عرضلي تقرير جودة البيانات الكامل", "ar"),
    ]

    log("")
    log("=" * 70)
    log(f"SECTION D: Follow-up chains — {len(seeds)} seeds, depth 2", "STEP")
    log("=" * 70)

    clarification_signals = [
        "لا تتوفر", "not enough", "جرّب أحد هذه", "try one of these",
        "I don't have enough", "لا أعرف", "لم أفهم", "couldn't find a stage",
        "I'm not sure I understood", "عذراً، لم أفهم",
    ]

    section_fails = 0
    d_test_count = 0

    for (seed_id, seed_q, seed_lang) in seeds:
        log(f"  Seed: [{seed_id}] {seed_q}", "STEP")
        sess_seed = f"d-{seed_id.lower()}-{uuid.uuid4().hex[:6]}"

        # Depth 0: send seed
        try:
            seed_resp = await chat(client, seed_q, session_id=sess_seed, lang=seed_lang)
        except Exception as exc:
            log(f"  Seed {seed_id} HTTP error: {exc}", "FAIL")
            section_fails += 1
            continue

        seed_msg = seed_resp.get("message", {})
        _add_cost(seed_msg.get("cost_usd", 0.0))
        d_test_count += 1
        global _test_counter
        _test_counter += 1
        if _test_counter % COST_PRINT_EVERY == 0:
            _print_cost_update()

        depth1_followups = seed_resp.get("suggested_followups", [])
        log(f"    → {len(depth1_followups)} follow-up(s): {depth1_followups}")

        # Depth 1: test each follow-up of the seed
        for fi, fu1 in enumerate(depth1_followups):
            tid = f"D-{seed_id[-1]}-D1-{fi+1}"
            sess_d1 = f"d-{seed_id.lower()}-d1-{fi}-{uuid.uuid4().hex[:6]}"

            log(f"    Depth 1 [{tid}]: {fu1[:80]}", "STEP")

            try:
                d1_resp = await chat(client, fu1, session_id=sess_d1, lang=seed_lang)
            except Exception as exc:
                log(f"    FAIL [{tid}] HTTP error: {exc}", "FAIL")
                _record_failure(
                    test_id=tid, section="D", intent="(follow-up)", question=fu1, lang=seed_lang,
                    intent_classified="HTTP_ERROR", response=str(exc),
                    followups=[], data_snapshot={},
                    reason=f"Follow-up HTTP error: {exc}",
                    root_cause="followup_chain_http_error",
                )
                section_fails += 1
                d_test_count += 1
                _test_counter += 1
                continue

            d1_msg = d1_resp.get("message", {})
            d1_content = d1_msg.get("content", "")
            d1_intent = d1_msg.get("intent", "unknown")
            d1_snap = d1_msg.get("data_snapshot") or {}
            d1_followups = d1_resp.get("suggested_followups", [])
            _add_cost(d1_msg.get("cost_usd", 0.0))
            d_test_count += 1
            _test_counter += 1
            if _test_counter % COST_PRINT_EVERY == 0:
                _print_cost_update()

            # Check: follow-up should NOT return clarification
            bad_signal = next(
                (p for p in clarification_signals if p.lower() in d1_content.lower()), None
            )
            has_br = bool(re.search(r"<br\s*/?>", d1_content, re.IGNORECASE))

            if bad_signal or has_br:
                reason_parts = []
                if bad_signal:
                    reason_parts.append(f"Depth-1 follow-up returned clarification (matched '{bad_signal}')")
                if has_br:
                    reason_parts.append("<br> in depth-1 response")
                reason = "; ".join(reason_parts)
                log(f"    FAIL [{tid}] {reason}", "FAIL")
                _record_failure(
                    test_id=tid, section="D", intent="(follow-up-depth-1)",
                    question=fu1, lang=seed_lang,
                    intent_classified=d1_intent, response=d1_content,
                    followups=d1_followups, data_snapshot=d1_snap,
                    reason=reason,
                    root_cause="followup_unanswerable_depth1",
                )
                section_fails += 1
            else:
                log(f"    PASS [{tid}] intent={d1_intent}", "PASS")
                global _passes
                _passes += 1

            # Depth 2: test only the FIRST follow-up of each depth-1 result
            if d1_followups:
                fu2 = d1_followups[0]
                tid2 = f"D-{seed_id[-1]}-D2-{fi+1}-1"
                sess_d2 = f"d-{seed_id.lower()}-d2-{fi}-{uuid.uuid4().hex[:6]}"

                log(f"      Depth 2 [{tid2}]: {fu2[:80]}", "STEP")

                try:
                    d2_resp = await chat(client, fu2, session_id=sess_d2, lang=seed_lang)
                except Exception as exc:
                    log(f"      FAIL [{tid2}] HTTP error: {exc}", "FAIL")
                    _record_failure(
                        test_id=tid2, section="D", intent="(follow-up-depth-2)",
                        question=fu2, lang=seed_lang,
                        intent_classified="HTTP_ERROR", response=str(exc),
                        followups=[], data_snapshot={},
                        reason=f"Depth-2 HTTP error: {exc}",
                        root_cause="followup_chain_http_error",
                    )
                    section_fails += 1
                    d_test_count += 1
                    _test_counter += 1
                    continue

                d2_msg = d2_resp.get("message", {})
                d2_content = d2_msg.get("content", "")
                d2_intent = d2_msg.get("intent", "unknown")
                d2_snap = d2_msg.get("data_snapshot") or {}
                d2_followups = d2_resp.get("suggested_followups", [])
                _add_cost(d2_msg.get("cost_usd", 0.0))
                d_test_count += 1
                _test_counter += 1
                if _test_counter % COST_PRINT_EVERY == 0:
                    _print_cost_update()

                bad2 = next(
                    (p for p in clarification_signals if p.lower() in d2_content.lower()), None
                )
                has_br2 = bool(re.search(r"<br\s*/?>", d2_content, re.IGNORECASE))

                if bad2 or has_br2:
                    r2_parts = []
                    if bad2:
                        r2_parts.append(f"Depth-2 follow-up returned clarification (matched '{bad2}')")
                    if has_br2:
                        r2_parts.append("<br> in depth-2 response")
                    reason2 = "; ".join(r2_parts)
                    log(f"      FAIL [{tid2}] {reason2}", "FAIL")
                    _record_failure(
                        test_id=tid2, section="D", intent="(follow-up-depth-2)",
                        question=fu2, lang=seed_lang,
                        intent_classified=d2_intent, response=d2_content,
                        followups=d2_followups, data_snapshot=d2_snap,
                        reason=reason2,
                        root_cause="followup_unanswerable_depth2",
                    )
                    section_fails += 1
                else:
                    log(f"      PASS [{tid2}] intent={d2_intent}", "PASS")
                    _passes += 1

    log(f"Section D complete: {d_test_count} traversal steps, {section_fails} failures", "STEP")
    return section_fails


# ── Section E: Data accuracy ──────────────────────────────────────────────────

def _fetch_fresh_stage_count(uid: int, stage_id: int) -> int:
    """Re-fetch stage lead count from Odoo right now (avoid stale baseline race condition)."""
    domain = BASE_DOMAIN + [["stage_id", "=", stage_id]]
    rows = odoo_call(uid, "crm.lead", "read_group", [domain, ["__count"], []], {})
    return rows[0].get("__count", 0) if rows else 0


def _fetch_fresh_team_overdue(uid: int, team_id: int) -> int:
    """Re-fetch team overdue count from Odoo right now."""
    domain = BASE_DOMAIN + [["activity_state", "=", "overdue"], ["team_id", "=", team_id]]
    rows = odoo_call(uid, "crm.lead", "read_group", [domain, ["__count"], []], {})
    return rows[0].get("__count", 0) if rows else 0


async def section_e(client: httpx.AsyncClient) -> int:
    """
    For top 5 stages and top 3 teams, ask AI for the count and compare
    against direct Odoo RPC. Re-fetches ground truth immediately before each
    test to avoid stale-baseline race conditions.
    """
    b = _baseline
    stages = b["stages"]
    teams = b["teams"]

    # Pick stages with non-zero lead counts for meaningful accuracy tests
    test_stages = [s for s in stages if s["lead_count"] > 0][:5]
    test_teams = [t for t in teams if t["lead_count"] > 0][:3]

    log("")
    log("=" * 70)
    log(f"SECTION E: Data accuracy — {len(test_stages)} stages + {len(test_teams)} teams", "STEP")
    log("=" * 70)

    uid = odoo_uid()  # authenticate once for all Section E RPC calls
    section_fails = 0

    # Stage count accuracy
    for s in test_stages:
        stage_name = s["name"]
        # Re-fetch immediately before the AI test to get the freshest truth
        odoo_count = _fetch_fresh_stage_count(uid, s["id"])
        tid = f"E-STG-{stage_name[:15].replace(' ', '_')}"
        question = f"كم lead في مرحلة {stage_name}؟"
        sess = f"e-stg-{uuid.uuid4().hex[:6]}"

        log(f"  Stage '{stage_name}': Odoo truth={odoo_count}", "STEP")

        passed, resp = await run_test(
            client, tid, "E", "count_by_stage", question, "ar", sess,
            [
                ("not_clarification", None),
                ("contains_number", None),
                ("no_br", None),
                ("count_matches_odoo", (stage_name, odoo_count)),
            ],
            root_cause_hint="data_accuracy_stage_count",
        )
        if not passed:
            section_fails += 1

    # Team overdue count accuracy — count_by_team returns overdue, not total
    for t in test_teams:
        team_name = t["name"]
        odoo_overdue = _fetch_fresh_team_overdue(uid, t["id"])
        if odoo_overdue == 0:
            log(f"  Skipping team '{team_name}' — 0 overdue leads (no AI count to verify)", "INFO")
            continue

        tid = f"E-TM-{team_name[:15].replace(' ', '_')}"
        # Ask in a way that gets the overdue count (matching what handler actually returns)
        question = f"كام lead متأخر في فريق {team_name}؟"
        sess = f"e-tm-{uuid.uuid4().hex[:6]}"

        log(f"  Team '{team_name}': Odoo overdue truth={odoo_overdue}", "STEP")

        passed, resp = await run_test(
            client, tid, "E", "count_by_team", question, "ar", sess,
            [
                ("not_clarification", None),
                ("contains_number", None),
                ("no_br", None),
                ("count_matches_odoo", (team_name, odoo_overdue)),
            ],
            root_cause_hint="data_accuracy_team_overdue_count",
        )
        if not passed:
            section_fails += 1

    log(f"Section E complete: {section_fails} failures", "STEP")
    return section_fails


# ── Final report ──────────────────────────────────────────────────────────────

def print_final_summary() -> None:
    total = _passes + len(_failures)
    log("")
    log("=" * 70)
    log("COMPREHENSIVE VERIFICATION COMPLETE")
    log("=" * 70)
    log(f"Total tests run   : {total}")
    log(f"Passed            : {_passes}")
    log(f"Failed            : {len(_failures)}")
    log(f"Pass rate         : {_passes/total*100:.1f}%" if total else "N/A")
    log(f"Total AI cost     : ${_total_cost:.6f}")
    log("")
    if _failures:
        log("FAILURES SUMMARY:")
        by_rc: dict[str, list] = {}
        for f in _failures:
            rc = f.get("root_cause", "TBD")
            by_rc.setdefault(rc, []).append(f["test_id"])
        for rc, ids in sorted(by_rc.items()):
            log(f"  [{len(ids):2d}x] {rc}: {', '.join(ids)}")
    log("")
    log(f"Full report saved to: {REPORT_PATH}", "STEP")
    log("=" * 70)


# ── Server lifecycle ──────────────────────────────────────────────────────────

def start_server() -> subprocess.Popen:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    log_path = PROJECT_ROOT / "logs" / "verify_comprehensive_server.log"
    log_path.parent.mkdir(exist_ok=True)
    server_log = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app",
         "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "info"],
        env=env, stdout=server_log, stderr=server_log, cwd=str(PROJECT_ROOT),
    )
    return proc


# ── Entry point ───────────────────────────────────────────────────────────────

async def run_full(baseline: dict) -> int:
    global _baseline, _total_tests_estimate
    _baseline = baseline
    _total_tests_estimate = 50 + 20 + 35 + 10  # B(47+3 Re-Distribution) + C + D + E (approximate)

    _init_report()
    log(f"Initialised report at: {REPORT_PATH}")
    log(f"Cost ceiling: ${COST_CEILING:.2f} | Estimated cost: ~$0.12")
    log("")

    proc = start_server()
    total_fails = 0
    try:
        await wait_for_server()

        async with httpx.AsyncClient() as client:
            total_fails += await section_b(client)
            total_fails += await section_c(client)
            total_fails += await section_d(client)
            total_fails += await section_e(client)

    finally:
        log("Stopping server...", "STEP")
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log("Server stopped")

    _finalize_report()
    print_final_summary()
    return total_fails


def main() -> int:
    args = sys.argv[1:]
    section_a_only = "--section-a" in args
    full = "--full" in args

    if not section_a_only and not full:
        print("Usage:")
        print("  python scripts/verify_chat_comprehensive.py --section-a   # baseline, zero AI cost")
        print("  python scripts/verify_chat_comprehensive.py --full        # all sections (~$0.12)")
        return 1

    baseline = run_section_a()

    if section_a_only:
        # Save baseline to a JSON file so --full can reuse it without re-fetching
        baseline_path = PROJECT_ROOT / "logs" / "verify_baseline.json"
        baseline_path.parent.mkdir(exist_ok=True)
        baseline_path.write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"Baseline saved to {baseline_path}")
        log("Pausing for your review. Run with --full when ready.", "STEP")
        return 0

    # --full: run everything
    fails = asyncio.run(run_full(baseline))
    return 1 if fails > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
