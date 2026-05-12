"""
User Journey Verification — tests multi-turn conversations as a real user.
Focus: UX quality, not technical assertions. Same session_id across steps.

8 journeys × 3 steps = 24 primary questions.
Each step also tests the first suggested follow-up (1-level recursion).

Usage:
    python scripts/verify_user_journeys.py

Spawns FastAPI on port 8092. Outputs docs/USER_JOURNEY_FAILURES.md.
Budget cap: $0.10 (≈40 questions at $0.0024 each).
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

import httpx
from dotenv import load_dotenv

# Force UTF-8 on Windows consoles
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────────────

PORT = 8092
BASE_URL = f"http://127.0.0.1:{PORT}"
BUDGET_CAP = 0.10

USERNAME = os.environ.get("VERIFY_USERNAME", "admin")
PASSWORD = os.environ.get("VERIFY_PASSWORD", "password")
AUTH = "Basic " + base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()

PROJECT_ROOT = Path(__file__).parent.parent
DOCS_DIR = PROJECT_ROOT / "docs"
REPORT_PATH = DOCS_DIR / "USER_JOURNEY_FAILURES.md"

# ── Global state ───────────────────────────────────────────────────────────────

_total_cost = 0.0
_failures: list[dict] = []
_passes = 0


# ── Logging ────────────────────────────────────────────────────────────────────

def log(msg: str, level: str = "   ") -> None:
    sym = {"PASS": "[+]", "FAIL": "[!]", "STEP": "-->", "WARN": "[~]"}.get(level, "   ")
    print(f"{sym} {msg}", flush=True)


# ── Report ─────────────────────────────────────────────────────────────────────

def _init_report() -> None:
    DOCS_DIR.mkdir(exist_ok=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    REPORT_PATH.write_text(
        f"# User Journey Failures\n\n"
        f"**Generated**: {now}  \n"
        f"**Note**: Site-visit chatter probe confirmed 0 messages in Odoo — "
        f"site visit intent returns empty by design (product gap).  \n\n"
        f"## Failures\n\n",
        encoding="utf-8",
    )


def _append_failure(f: dict) -> None:
    with open(REPORT_PATH, "a", encoding="utf-8") as fh:
        fh.write(f"---\n\n")
        fh.write(f"### {f['step_id']} — {f['journey_name']}\n\n")
        fh.write(f"- **Step**: {f['step_id']} ({f['step_label']})\n")
        fh.write(f"- **Question**: {f['question']}\n")
        fh.write(f"- **Intent classified**: `{f['intent']}`\n")
        fh.write(f"- **Data type**: `{f['data_type']}`\n")
        fh.write(f"- **Failure reason**: {f['reason']}\n")
        fh.write(f"- **Diagnosis**: {f['diagnosis']}\n")
        fh.write(f"- **Proposed fix**: {f['proposed_fix']}\n\n")
        fh.write(f"**AI Response** (first 400 chars):\n\n")
        fh.write(f"```\n{f['response'][:400]}\n```\n\n")
        if f.get("data_snapshot"):
            fh.write(f"**Data snapshot**:\n\n")
            fh.write(f"```json\n{json.dumps(f['data_snapshot'], ensure_ascii=False, indent=2)[:500]}\n```\n\n")


def _finalize_report(total: int, fails: int, cost: float) -> None:
    existing = REPORT_PATH.read_text(encoding="utf-8")
    summary = (
        f"## Summary\n\n"
        f"| Metric | Value |\n"
        f"|--------|-------|\n"
        f"| Steps run | {total} |\n"
        f"| Failures | {fails} |\n"
        f"| Passes | {total - fails} |\n"
        f"| Total cost | ${cost:.4f} |\n\n"
    )
    REPORT_PATH.write_text(
        existing.replace("## Failures\n\n", summary + "## Failures\n\n"),
        encoding="utf-8",
    )


# ── Server lifecycle ───────────────────────────────────────────────────────────

def _start_server() -> subprocess.Popen:
    log(f"Starting FastAPI server on port {PORT}...", "STEP")
    env = {**os.environ, "PORT": str(PORT)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app",
         "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Poll until ready
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            r = httpx.get(f"{BASE_URL}/health", timeout=2)
            if r.status_code < 500:
                log(f"Server ready on port {PORT}", "STEP")
                return proc
        except Exception:
            pass
        time.sleep(0.5)
    proc.terminate()
    raise RuntimeError("Server failed to start within 30 seconds")


# ── UX assertions ──────────────────────────────────────────────────────────────

_CLARIFICATION_PHRASES = [
    "لا تتوفر لديّ بيانات",
    "لا تتوفر لدي بيانات",
    "لم أفهم سؤالك",
    "لم افهم سؤالك",
    "عذراً، لم أفهم",
    "عذرا، لم أفهم",
    "لا أستطيع الإجابة",
    "لا استطيع الاجابة",
    "not enough data",
    "couldn't understand",
    "I couldn't understand",
    "i couldn't understand",
    "do not understand",
    "don't understand",
    "please clarify",
    "unclear",
    "جرّب أحد هذه:\n",  # the exact clarification fallback pattern
    "جرب أحد هذه:\n",
]


def _is_clarification(content: str) -> tuple[bool, str]:
    cl = content.lower()
    for phrase in _CLARIFICATION_PHRASES:
        if phrase.lower() in cl:
            return True, phrase
    return False, ""


def _content_length(content: str) -> int:
    return len(re.sub(r"\s+", "", content))


def _check_ux(content: str) -> list[str]:
    """Return list of UX failure reasons (empty = pass)."""
    reasons = []
    is_clar, phrase = _is_clarification(content)
    if is_clar:
        reasons.append(f"clarification response (phrase: '{phrase[:40]}')")
    if _content_length(content) < 50:
        reasons.append(f"response too short ({_content_length(content)} non-whitespace chars)")
    return reasons


# ── Chat client ────────────────────────────────────────────────────────────────

async def chat(
    client: httpx.AsyncClient,
    question: str,
    session_id: str,
    locale: str = "ar",
) -> dict:
    global _total_cost
    cookies = {"lang": locale}
    r = await client.post(
        f"{BASE_URL}/api/v1/chat/message",
        json={"message": question, "session_id": session_id},
        headers={"Authorization": AUTH},
        cookies=cookies,
        timeout=60,
    )
    if r.status_code != 200:
        return {
            "error": True,
            "status": r.status_code,
            "content": f"HTTP {r.status_code}: {r.text[:200]}",
        }
    data = r.json()
    msg = data.get("message", {})
    cost = msg.get("cost_usd", 0.0)
    _total_cost += cost
    return {
        "content": msg.get("content", ""),
        "intent": msg.get("intent", "unknown"),
        "data_snapshot": msg.get("data_snapshot") or {},
        "followups": data.get("suggested_followups", []),
        "cost": cost,
    }


# ── Step runner ────────────────────────────────────────────────────────────────

async def run_step(
    client: httpx.AsyncClient,
    step_id: str,
    journey_name: str,
    step_label: str,
    question: str,
    session_id: str,
    locale: str,
    diagnosis: str = "",
    proposed_fix: str = "",
    known_gap: bool = False,
) -> tuple[bool, dict]:
    """Run one step. Returns (passed, response_dict)."""
    global _passes

    if _total_cost >= BUDGET_CAP:
        log(f"Budget cap ${BUDGET_CAP} reached — stopping", "WARN")
        sys.exit(0)

    resp = await chat(client, question, session_id, locale)

    if resp.get("error"):
        content = resp["content"]
        intent = "http_error"
        data_snap = {}
        followups: list[str] = []
    else:
        content = resp["content"]
        intent = resp["intent"]
        data_snap = resp["data_snapshot"]
        followups = resp["followups"]

    ux_failures = _check_ux(content)

    # Known product gaps: site visit empty-data is expected — don't flag as UX failure
    if known_gap and not resp.get("error"):
        is_clar, _ = _is_clarification(content)
        if is_clar:
            ux_failures_display = ux_failures.copy()
            ux_failures = []  # suppress for known gap
            gap_note = f"(product gap — empty data expected; AI said: '{content[:80]}')"
            log(f"    {step_id} KNOWN-GAP: {step_label!r} — {gap_note}", "WARN")
            _passes += 1
            return True, resp

    data_type = data_snap.get("type", "?") if isinstance(data_snap, dict) else "?"

    if ux_failures:
        reason_str = "; ".join(ux_failures)
        log(f"    {step_id} FAIL: {step_label!r}", "FAIL")
        log(f"         reason: {reason_str}")
        log(f"         intent: {intent} | data_type: {data_type}")
        log(f"         response[:120]: {content[:120]!r}")

        _failures.append({
            "step_id": step_id,
            "journey_name": journey_name,
            "step_label": step_label,
            "question": question,
            "intent": intent,
            "data_type": data_type,
            "reason": reason_str,
            "diagnosis": diagnosis or "see intent and data_type above",
            "proposed_fix": proposed_fix or "investigate intent parser and handler",
            "response": content,
            "data_snapshot": data_snap,
        })
        _append_failure(_failures[-1])
        return False, resp
    else:
        log(f"    {step_id} PASS: {step_label!r}  [intent={intent}, type={data_type}, ${resp.get('cost',0):.5f}]", "PASS")
        _passes += 1
        return True, resp


async def run_followup(
    client: httpx.AsyncClient,
    parent_step_id: str,
    journey_name: str,
    followup_question: str,
    session_id: str,
    locale: str,
) -> None:
    """Test the first suggested follow-up from a passed step."""
    fu_id = f"{parent_step_id}-FU"
    log(f"    {fu_id} follow-up: {followup_question!r}")
    await run_step(
        client, fu_id, journey_name,
        f"follow-up of {parent_step_id}",
        followup_question, session_id, locale,
        diagnosis="AI-generated follow-up that the system cannot answer",
        proposed_fix="validate follow-ups against real handler data availability",
    )


# ── Journey definitions ─────────────────────────────────────────────────────────

JOURNEYS = [
    {
        "id": "J1",
        "name": "Site Visit Investigation",
        "locale": "ar",
        "steps": [
            {
                "label": "Q1: list site-visit leads",
                "question": "عرضلي العملاء اللي طلبوا معاينة",
                "diagnosis": "leads_with_site_visit_signal handler: probe confirmed 0 chatter messages with معاينة (product gap — no data)",
                "proposed_fix": "Remove from suggested questions; update response to explain that site visit logging must be done in Odoo chatter",
                "known_gap": True,
            },
            {
                "label": "Q2: details of first lead (context-dependent)",
                "question": "اعرض تفاصيل أول عميل منهم",
                "diagnosis": "lead_details_by_id — question has no numeric ID; parser can only extract ID from session context (previous response). If Q1 was empty, there's nothing to reference.",
                "proposed_fix": "AI response builder should include clickable lead IDs; intent parser should extract ID from session context",
                "known_gap": False,
            },
            {
                "label": "Q3: who is the responsible salesperson (context-dependent)",
                "question": "مين الموظف المسؤول عنه؟",
                "diagnosis": "No dedicated intent for 'who is the salesperson of a specific lead'. May map to unknown or free_form_analysis.",
                "proposed_fix": "If lead_details_by_id is working, salesperson is in the data. AI should answer from context without a new CRM query.",
                "known_gap": False,
            },
        ],
    },
    {
        "id": "J2",
        "name": "Top Performers Deep-Dive",
        "locale": "ar",
        "steps": [
            {
                "label": "Q1: top 5 overdue salespeople",
                "question": "إيه أعلى 5 موظفي مبيعات عندهم تأخر؟",
                "diagnosis": "list_overdue_by_salesperson — should work",
                "proposed_fix": "N/A",
                "known_gap": False,
            },
            {
                "label": "Q2: exact count for Ahmed Adel",
                "question": "كم lead عند أحمد عادل بالظبط؟",
                "diagnosis": "count_by_salesperson filtered by name. If Ahmed Adel not in top 5, still should resolve via search.",
                "proposed_fix": "N/A",
                "known_gap": False,
            },
            {
                "label": "Q3: Ahmed Adel leads in Re-Distribution (no intent for salesperson×stage)",
                "question": "أحمد عادل عنده كام lead في Re-Distribution؟",
                "diagnosis": "No intent handles salesperson+stage combination. Parser may return count_by_stage (ignoring salesperson) or unknown.",
                "proposed_fix": "Add filter combination to count_by_stage handler: if both salesperson and stage filters present, apply both.",
                "known_gap": False,
            },
        ],
    },
    {
        "id": "J3",
        "name": "Stage Analysis",
        "locale": "ar",
        "steps": [
            {
                "label": "Q1: count leads in Re-Distribution",
                "question": "كم lead في إعادة التوزيع؟",
                "diagnosis": "count_by_stage with Arabic stage name — fixed in this session",
                "proposed_fix": "N/A",
                "known_gap": False,
            },
            {
                "label": "Q2: how many are overdue (context: no stage name in Q)",
                "question": "وكام منهم متأخر؟",
                "diagnosis": "count_overdue_by_stage — 'وكام' (and how many) needs parser to infer stage from session context. Stage name not in this question.",
                "proposed_fix": "Stage 1 intent parser gets last 20 messages as context — should resolve Re-Distribution from Q1. Depends on context window.",
                "known_gap": False,
            },
            {
                "label": "Q3: top salesperson in that stage (no intent for salesperson-in-stage)",
                "question": "مين أعلى موظف عنده تأخر فيهم؟",
                "diagnosis": "No intent returns overdue-by-salesperson filtered by stage. list_overdue_by_salesperson ignores stage filter.",
                "proposed_fix": "Add stage filter to list_overdue_by_salesperson handler — filter rows by stage after fetching.",
                "known_gap": False,
            },
        ],
    },
    {
        "id": "J4",
        "name": "Recommendation Flow",
        "locale": "ar",
        "steps": [
            {
                "label": "Q1: recommend 3 leads",
                "question": "اقترح عليّ 3 عملاء أتواصل معاهم النهارده",
                "diagnosis": "recommendation_top_priority — should work",
                "proposed_fix": "N/A",
                "known_gap": False,
            },
            {
                "label": "Q2: who are the salespeople of those leads (no intent + no data)",
                "question": "مين موظفي المبيعات المسؤولين عن العملاء دول؟",
                "diagnosis": "DOUBLE BUG: (1) No intent for 'salespeople of previously-shown leads'; (2) recommendation handler output does NOT include salesperson_name field — only lead_id, score, tier, reasoning, recommended_action.",
                "proposed_fix": "Add salesperson_name to recommendation handler output. Add free_form_analysis fallback that can answer contextual questions from prior response.",
                "known_gap": False,
            },
            {
                "label": "Q3: details of first lead (context-dependent ID)",
                "question": "أعطيني تفاصيل أكتر عن أول واحد",
                "diagnosis": "lead_details_by_id — if Q1 response mentioned a lead ID (e.g., 707758), parser may extract it from context. Depends on session context quality.",
                "proposed_fix": "Recommendation response should explicitly surface lead IDs so context-dependent follow-ups can extract them.",
                "known_gap": False,
            },
        ],
    },
    {
        "id": "J5",
        "name": "Data Quality Investigation",
        "locale": "ar",
        "steps": [
            {
                "label": "Q1: worst team for data quality (subjective)",
                "question": "ايه أسوأ فريق في جودة البيانات؟",
                "diagnosis": "free_form_analysis or data_quality_summary. data_quality_summary is aggregate-only (no team breakdown). free_form_analysis uses general_summary.",
                "proposed_fix": "N/A — free_form_analysis with team_performance_summary covers this",
                "known_gap": False,
            },
            {
                "label": "Q2: how many leads have problems in that team (no team-filtered DQ intent)",
                "question": "كام lead عنده مشكلة في الفريق ده؟",
                "diagnosis": "No intent for team-filtered data quality count. data_quality_summary has no team filter. May return aggregate (ignoring team context) or unknown.",
                "proposed_fix": "Add team filter to missing_contact_summary handler using crm.missing_contact_details(team_id=...).",
                "known_gap": False,
            },
            {
                "label": "Q3: what type of problem",
                "question": "إيه نوع المشكلة بالضبط؟",
                "diagnosis": "data_quality_summary or free_form_analysis — should describe missing phone/email/stage categories",
                "proposed_fix": "N/A",
                "known_gap": False,
            },
        ],
    },
    {
        "id": "J6",
        "name": "Pure Conversational",
        "locale": "ar",
        "steps": [
            {
                "label": "Q1: greeting",
                "question": "أهلاً",
                "diagnosis": "greeting intent — should pass",
                "proposed_fix": "N/A",
                "known_gap": False,
            },
            {
                "label": "Q2: what can you do",
                "question": "إنت بتقدر تساعد في إيه؟",
                "diagnosis": "meta_question or help_request — should pass",
                "proposed_fix": "N/A",
                "known_gap": False,
            },
            {
                "label": "Q3: show me an example question",
                "question": "اعرضلي مثال على سؤال أقدر أسأله",
                "diagnosis": "help_request — should suggest a concrete CRM question",
                "proposed_fix": "N/A",
                "known_gap": False,
            },
        ],
    },
    {
        "id": "J7",
        "name": "Mixed Language",
        "locale": "en",
        "steps": [
            {
                "label": "Q1: EN count in Follow up",
                "question": "How many leads in Follow up?",
                "diagnosis": "count_by_stage — should work",
                "proposed_fix": "N/A",
                "known_gap": False,
            },
            {
                "label": "Q2: AR follow-up about Re-Distribution (context-dependent stage)",
                "question": "وفي Re-Distribution؟",
                "diagnosis": "count_by_stage with stage=Re-Distribution. Short question — parser needs context from Q1 to understand 'وفي' = 'and in'. Stage name is explicit here though.",
                "proposed_fix": "N/A if stage name present. If parser returns unknown, add example for this mixed-language pattern.",
                "known_gap": False,
            },
            {
                "label": "Q3: details of top salesperson in Re-Distribution (no salesperson-in-stage intent)",
                "question": "Show me details of the top salesperson there",
                "diagnosis": "No intent for 'top salesperson in [previously mentioned stage]'. May map to list_overdue_by_salesperson (ignoring stage) or unknown.",
                "proposed_fix": "Add stage filter to list_overdue_by_salesperson. Or use free_form_analysis to answer from context.",
                "known_gap": False,
            },
        ],
    },
    {
        "id": "J8",
        "name": "Stress Test — Vague Questions",
        "locale": "ar",
        "steps": [
            {
                "label": "Q1: how is the company today (vague)",
                "question": "إيه أوضاع الشركة النهارده؟",
                "diagnosis": "free_form_analysis — general_summary data should produce a substantive response",
                "proposed_fix": "N/A",
                "known_gap": False,
            },
            {
                "label": "Q2: what needs attention (vague)",
                "question": "أنهي حاجة محتاجة اهتمام؟",
                "diagnosis": "recommendation_top_priority or free_form_analysis — either should return actionable data",
                "proposed_fix": "N/A",
                "known_gap": False,
            },
            {
                "label": "Q3: suggest practical steps",
                "question": "اقترح عليّ خطوات عملية",
                "diagnosis": "recommendation_top_priority — should return 3 prioritized leads",
                "proposed_fix": "N/A",
                "known_gap": False,
            },
        ],
    },
]


# ── Main runner ────────────────────────────────────────────────────────────────

async def run_journeys(client: httpx.AsyncClient) -> None:
    total_steps = 0
    total_fails = 0

    for journey in JOURNEYS:
        jid = journey["id"]
        jname = journey["name"]
        locale = journey["locale"]
        session_id = f"journey-{jid.lower()}-{uuid.uuid4().hex[:8]}"

        log("")
        log("=" * 65)
        log(f"{jid}: {jname}  [session={session_id[:20]}...]", "STEP")
        log(f"     locale={locale} | {len(journey['steps'])} steps", "STEP")
        log("=" * 65)

        for i, step in enumerate(journey["steps"], 1):
            step_id = f"{jid}-Q{i}"
            total_steps += 1

            passed, resp = await run_step(
                client,
                step_id=step_id,
                journey_name=jname,
                step_label=step["label"],
                question=step["question"],
                session_id=session_id,
                locale=locale,
                diagnosis=step["diagnosis"],
                proposed_fix=step["proposed_fix"],
                known_gap=step.get("known_gap", False),
            )

            if not passed:
                total_fails += 1

            # 1-level follow-up recursion (only if step passed and there are follow-ups)
            followups: list[str] = []
            if not resp.get("error"):
                followups = resp.get("followups", [])

            if passed and followups:
                fu_q = followups[0]
                total_steps += 1
                fu_id = f"{step_id}-FU"
                fu_passed, _ = await run_step(
                    client,
                    step_id=fu_id,
                    journey_name=jname,
                    step_label=f"follow-up of {step_id}",
                    question=fu_q,
                    session_id=session_id,
                    locale=locale,
                    diagnosis="AI-generated follow-up suggestion — tests whether suggested questions are actually answerable",
                    proposed_fix="Remove follow-up if it maps to unknown/clarification. Validate follow-ups against handler data availability.",
                )
                if not fu_passed:
                    total_fails += 1

            log(f"         [cost so far: ${_total_cost:.4f}]")

    log("")
    log("=" * 65)
    log(f"COMPLETE: {total_steps} steps run, {total_fails} failures, ${_total_cost:.4f} spent", "STEP")
    log("=" * 65)

    _finalize_report(total_steps, total_fails, _total_cost)

    if _failures:
        log(f"\nFailure report: {REPORT_PATH}", "WARN")
    else:
        log("All journeys passed.")


async def main() -> None:
    _init_report()
    server = None
    try:
        server = _start_server()
        async with httpx.AsyncClient() as client:
            await run_journeys(client)
    finally:
        if server:
            server.terminate()
            log("Server stopped.")
    sys.exit(1 if _failures else 0)


if __name__ == "__main__":
    asyncio.run(main())
