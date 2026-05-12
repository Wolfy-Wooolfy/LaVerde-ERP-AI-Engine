"""
End-to-end self-verification for the chat assistant.

Run BEFORE every chat-related commit:
    python scripts/verify_chat.py

This script:
1. Spawns the FastAPI server as a subprocess on port 8090
2. Waits for /health to return 200
3. Uses httpx to POST real chat questions and inspect raw responses
4. Asserts each scenario passes
5. Kills the server
6. Exits 0 on success, non-zero on any failure

If this script fails, DO NOT commit. Fix the bug first.
"""

import asyncio
import base64
import io
import os
import re
import subprocess
import sys
import uuid

import httpx
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

# Force UTF-8 stdout so Arabic text doesn't crash on Windows cp1252 consoles
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PORT = 8090
BASE_URL = f"http://127.0.0.1:{PORT}"
USERNAME = os.environ.get("VERIFY_USERNAME", "admin")
PASSWORD = os.environ.get("VERIFY_PASSWORD", "password")

AUTH_HEADER = "Basic " + base64.b64encode(
    f"{USERNAME}:{PASSWORD}".encode()
).decode()


def log(msg: str, level: str = "INFO") -> None:
    symbol = {"INFO": "   ", "PASS": "[+]", "FAIL": "[!]", "STEP": "-->"}. get(level, "   ")
    print(f"{symbol} {msg}", flush=True)


async def wait_for_server(timeout: int = 40) -> None:
    log("Waiting for server to start...", "STEP")
    async with httpx.AsyncClient(timeout=5.0) as client:
        for _ in range(timeout):
            try:
                r = await client.get(
                    f"{BASE_URL}/api/v1/health",
                    headers={"Authorization": AUTH_HEADER},
                )
                if r.status_code == 200:
                    log("Server is up", "PASS")
                    return
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout,
                    httpx.TimeoutException, OSError):
                pass
            await asyncio.sleep(1)
    raise RuntimeError(f"Server did not start within {timeout}s")


async def chat(
    client: httpx.AsyncClient,
    message: str,
    session_id: str = "verify-session",
    lang: str = "ar",
) -> dict:
    r = await client.post(
        f"{BASE_URL}/api/v1/chat/message",
        json={"session_id": session_id, "message": message},
        headers={
            "Authorization": AUTH_HEADER,
            "Cookie": f"lang={lang}",
        },
        timeout=30.0,
    )
    r.raise_for_status()
    return r.json()


# ── Assertion helpers ─────────────────────────────────────────────────────────


def assert_no_br(content: str, context: str) -> None:
    if re.search(r"<br\s*/?>", content, re.IGNORECASE):
        log(f"<br> tag found in: {context}", "FAIL")
        log(f"  Excerpt: {content[:300]}", "INFO")
        raise AssertionError(f"<br> tag in {context}")
    log(f"No <br> tag in response ({context})", "PASS")


def assert_contains_number(content: str, context: str) -> None:
    if not re.search(r"\d", content):
        log(f"No number found in response for: {context}", "FAIL")
        log(f"  Content: {content[:300]}", "INFO")
        raise AssertionError(f"Expected a numeric answer in {context}")
    log(f"Numeric answer present ({context})", "PASS")


def assert_not_clarification(content: str, context: str) -> None:
    patterns = [
        "لا تتوفر",
        "not enough",
        "جرّب أحد هذه",
        "try one of these",
        "I don't have enough",
        "لا أعرف",
    ]
    for p in patterns:
        if p.lower() in content.lower():
            log(f"Got clarification fallback for: {context}", "FAIL")
            log(f"  Matched pattern: {p!r}", "INFO")
            log(f"  Content: {content[:300]}", "INFO")
            raise AssertionError(f"Clarification fallback in {context}")
    log(f"Real (non-clarification) answer ({context})", "PASS")


def assert_no_mandup(content: str, context: str) -> None:
    if "مندوب" in content:
        log(f"Forbidden term 'مندوب' found in: {context}", "FAIL")
        log(f"  Content: {content[:300]}", "INFO")
        raise AssertionError(f"Terminology error: 'مندوب' in {context}")
    log(f"No forbidden terminology ({context})", "PASS")


# ── Odoo ground-truth helpers (read-only, zero OpenAI) ───────────────────────

_ODOO_URL = os.environ.get("ODOO_URL", "").rstrip("/") + "/jsonrpc"
_ODOO_DB = os.environ.get("ODOO_DB", "")
_ODOO_USER = os.environ.get("ODOO_USERNAME", "")
_ODOO_KEY = os.environ.get("ODOO_API_KEY", "")

_BASE_DOMAIN = [
    ["type", "=", "opportunity"],
    ["opportunity_status", "=", "resolved"],
]


def _rpc_sync(service, method, args):
    import urllib.request
    import json as _json
    payload = _json.dumps({
        "jsonrpc": "2.0", "method": "call", "id": str(uuid.uuid4()),
        "params": {"service": service, "method": method, "args": args},
    }).encode()
    req = urllib.request.Request(_ODOO_URL, data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = _json.loads(r.read())
    if "error" in data:
        raise RuntimeError(f"Odoo RPC error: {data['error']}")
    return data["result"]


def _get_odoo_stage_count(stage_name: str) -> int:
    """Exact case-insensitive match — same logic as CrmService.count_leads_by_stage."""
    uid = _rpc_sync("common", "authenticate", [_ODOO_DB, _ODOO_USER, _ODOO_KEY, {}])
    stages = _rpc_sync("object", "execute_kw",
                       [_ODOO_DB, uid, _ODOO_KEY, "crm.stage", "search_read",
                        [[]], {"fields": ["id", "name"], "limit": 200}])
    target = stage_name.strip().lower()
    matched_ids = [s["id"] for s in stages if s["name"].strip().lower() == target]
    if not matched_ids:
        return 0
    domain = _BASE_DOMAIN + [["stage_id", "in", matched_ids]]
    rows = _rpc_sync("object", "execute_kw",
                     [_ODOO_DB, uid, _ODOO_KEY, "crm.lead", "read_group",
                      [domain, ["__count"], []], {}])
    return rows[0].get("__count", 0) if rows else 0


# ── Scenarios ─────────────────────────────────────────────────────────────────


async def run_scenarios() -> int:
    failures = 0
    async with httpx.AsyncClient() as client:

        # ── S1: Stage count question (Reservation — real stage in live Odoo) ───
        log("=" * 60)
        log("Scenario 1: كم lead في مرحلة Reservation؟", "STEP")
        log("=" * 60)
        try:
            resp = await chat(client, "كم lead في مرحلة Reservation؟", session_id="s1")
            content = resp["message"]["content"]
            intent = resp["message"].get("intent", "?")
            log(f"Intent:   {intent}")
            log(f"Response: {content[:250]}")
            assert_no_br(content, "S1 Reservation")
            assert_not_clarification(content, "S1 Reservation")
            assert_contains_number(content, "S1 Reservation")
        except Exception as e:
            log(f"Scenario 1 FAILED: {e}", "FAIL")
            failures += 1

        # ── S2: Arabic stage name alias ────────────────────────────────────────
        log("=" * 60)
        log("Scenario 2: Arabic stage names (متابعة / الحجز)", "STEP")
        log("=" * 60)
        for question in ["كم عميل في متابعة؟", "كم lead في الحجز؟"]:
            try:
                resp = await chat(client, question, session_id=f"s2-{hash(question)}")
                content = resp["message"]["content"]
                intent = resp["message"].get("intent", "?")
                log(f"Q: {question}")
                log(f"Intent:   {intent}")
                log(f"Response: {content[:200]}")
                assert_no_br(content, f"S2 {question}")
                assert_not_clarification(content, f"S2 {question}")
                assert_contains_number(content, f"S2 {question}")
            except Exception as e:
                log(f"Scenario 2 FAILED for '{question}': {e}", "FAIL")
                failures += 1

        # ── S3: Suggested questions / follow-ups are answerable ────────────────
        log("=" * 60)
        log("Scenario 3: Suggested questions are answerable", "STEP")
        log("=" * 60)
        try:
            r = await client.get(
                f"{BASE_URL}/api/v1/chat/suggested-questions",
                headers={"Authorization": AUTH_HEADER, "Cookie": "lang=ar"},
            )
            r.raise_for_status()
            data = r.json()
            questions = data if isinstance(data, list) else data.get("questions", [])
            log(f"Got {len(questions)} suggested questions")
            for q in questions[:3]:
                log(f"  Testing: {q}", "STEP")
                resp = await chat(client, q, session_id=f"s3-{hash(q)}")
                content = resp["message"]["content"]
                assert_no_br(content, f"S3 follow-up: {q}")
                assert_not_clarification(content, f"S3 follow-up: {q}")
                assert_no_mandup(content, f"S3 follow-up: {q}")
            log(f"All {min(3, len(questions))} suggested questions answered successfully", "PASS")
        except Exception as e:
            log(f"Scenario 3 FAILED: {e}", "FAIL")
            failures += 1

        # ── S4: Conversational intents bypass CRM, no crash ───────────────────
        log("=" * 60)
        log("Scenario 4: Conversational messages (greeting / thanks)", "STEP")
        log("=" * 60)
        for conv_msg in ["أهلاً", "شكراً", "مرحبا"]:
            try:
                resp = await chat(client, conv_msg, session_id=f"s4-{hash(conv_msg)}")
                content = resp["message"]["content"]
                intent = resp["message"].get("intent", "?")
                log(f"Q: {conv_msg}  Intent: {intent}  Response: {content[:100]}")
                assert_no_br(content, f"S4 {conv_msg}")
            except Exception as e:
                log(f"Scenario 4 FAILED for '{conv_msg}': {e}", "FAIL")
                failures += 1

        # ── S5: No <br> in any real data response ─────────────────────────────
        log("=" * 60)
        log("Scenario 5: No <br> tags in data responses", "STEP")
        log("=" * 60)
        data_questions = [
            ("إيه أعلى 5 موظفي مبيعات عندهم تأخر؟", "ar"),
            ("How many leads are overdue?", "en"),
            ("Show me team performance", "en"),
        ]
        for q, lang in data_questions:
            try:
                resp = await chat(client, q, session_id=f"s5-{hash(q)}", lang=lang)
                content = resp["message"]["content"]
                followups = resp.get("suggested_followups", [])
                log(f"Q: {q}")
                log(f"  Response (first 150): {content[:150]}")
                log(f"  Follow-ups: {followups}")
                assert_no_br(content, f"S5 {q}")
                for fu in followups:
                    assert_no_br(fu, f"S5 follow-up: {fu}")
            except Exception as e:
                log(f"Scenario 5 FAILED for '{q}': {e}", "FAIL")
                failures += 1

        # ── S6: Terminology — no "مندوب" anywhere ─────────────────────────────
        log("=" * 60)
        log("Scenario 6: Terminology check — no 'مندوب' in responses", "STEP")
        log("=" * 60)
        terminology_questions = [
            "إيه أعلى موظفي المبيعات عندهم تأخر؟",
            "اعطيني قائمة موظفي المبيعات",
        ]
        for q in terminology_questions:
            try:
                resp = await chat(client, q, session_id=f"s6-{hash(q)}")
                content = resp["message"]["content"]
                followups = resp.get("suggested_followups", [])
                log(f"Q: {q}")
                log(f"  Response: {content[:200]}")
                assert_no_mandup(content, f"S6 {q}")
                for fu in followups:
                    assert_no_mandup(fu, f"S6 follow-up: {fu}")
            except Exception as e:
                log(f"Scenario 6 FAILED for '{q}': {e}", "FAIL")
                failures += 1

        # ── S7: Dashboard uses DISPLAY_NAME ───────────────────────────────────
        log("=" * 60)
        log("Scenario 7: Dashboard greeting", "STEP")
        log("=" * 60)
        try:
            r = await client.get(
                f"{BASE_URL}/dashboard",
                headers={"Authorization": AUTH_HEADER},
            )
            html = r.text
            display_name = os.environ.get("DISPLAY_NAME", "")
            if display_name:
                if display_name in html:
                    log(f"Dashboard contains DISPLAY_NAME='{display_name}'", "PASS")
                else:
                    log(f"DISPLAY_NAME='{display_name}' NOT found in dashboard HTML", "FAIL")
                    failures += 1
            else:
                log("DISPLAY_NAME not set — skipping name assertion", "INFO")
                log("(Set DISPLAY_NAME env var to test greeting)", "INFO")
        except Exception as e:
            log(f"Scenario 7 FAILED: {e}", "FAIL")
            failures += 1

        # ── S8: Stage count accuracy vs Odoo ground truth ─────────────────────
        log("=" * 60)
        log("Scenario 8: Stage count accuracy — AI must match Odoo", "STEP")
        log("=" * 60)

        s8_cases = [
            ("New",        "كم lead في مرحلة New؟",            "ar"),
            ("Follow up",  "كم lead في مرحلة Follow up؟",      "ar"),
            ("Interested", "كم lead في مرحلة Interested؟",     "ar"),
            ("Reservation","How many leads in Reservation?",    "en"),
            ("New",        "How many leads in New stage?",      "en"),
        ]

        for stage_name, question, lang in s8_cases:
            try:
                # Ground truth from Odoo (read-only, no OpenAI)
                odoo_count = _get_odoo_stage_count(stage_name)
                log(f"Odoo ground truth — '{stage_name}': {odoo_count} leads")

                resp = await chat(client, question,
                                  session_id=f"s8-{stage_name.replace(' ', '_')}",
                                  lang=lang)
                content = resp["message"]["content"]
                intent = resp["message"].get("intent", "?")
                log(f"  Q: {question}")
                log(f"  Intent: {intent}  Response: {content[:200]}")

                assert_no_br(content, f"S8 {stage_name}")

                # Extract first number from AI response
                nums = re.findall(r"\d[\d,]*", content)
                if not nums:
                    log(f"  No number found in AI response for '{stage_name}'", "FAIL")
                    raise AssertionError(f"No number in S8 response for '{stage_name}'")

                # Strip commas (Arabic formatting sometimes adds them)
                ai_count = int(nums[0].replace(",", ""))

                if ai_count == odoo_count:
                    log(f"  '{stage_name}': AI={ai_count} == Odoo={odoo_count}", "PASS")
                else:
                    log(f"  '{stage_name}': AI={ai_count} != Odoo={odoo_count} "
                        f"(diff={abs(ai_count - odoo_count)})", "FAIL")
                    raise AssertionError(
                        f"Count mismatch for '{stage_name}': AI={ai_count}, Odoo={odoo_count}"
                    )
            except AssertionError:
                failures += 1
            except Exception as e:
                log(f"Scenario 8 FAILED for '{stage_name}': {e}", "FAIL")
                failures += 1

        # ── S9: AI-generated follow-ups must be answerable ───────────────────────
        log("=" * 60)
        log("Scenario 9: AI-generated follow-ups must not return clarification", "STEP")
        log("=" * 60)

        _CLARIFICATION_SIGNALS = [
            "لم أفهم",
            "لم أجد مرحلة",
            "لا تتوفر",
            "جرّب أحد هذه",
            "couldn't find a stage",
            "I'm not sure I understood",
            "not enough",
            "try one of these",
        ]

        s9_seed_questions = [
            ("كم lead في مرحلة Follow up؟", "ar"),
            ("Show me team performance", "en"),
        ]

        for seed_q, seed_lang in s9_seed_questions:
            try:
                seed_resp = await chat(client, seed_q,
                                       session_id=f"s9-{abs(hash(seed_q))}",
                                       lang=seed_lang)
                followups = seed_resp.get("suggested_followups", [])
                log(f"Seed: {seed_q!r} → {len(followups)} follow-up(s)")

                if not followups:
                    log("  No follow-ups returned — skipping", "INFO")
                    continue

                for fu in followups:
                    log(f"  Testing follow-up: {fu}", "STEP")
                    fu_resp = await chat(client, fu,
                                        session_id=f"s9-fu-{abs(hash(fu))}",
                                        lang=seed_lang)
                    fu_content = fu_resp["message"]["content"]
                    bad = next((p for p in _CLARIFICATION_SIGNALS
                                if p.lower() in fu_content.lower()), None)
                    if bad:
                        log(f"  Follow-up returned clarification (matched {bad!r}): {fu!r}", "FAIL")
                        log(f"  Response: {fu_content[:200]}", "INFO")
                        failures += 1
                    else:
                        log(f"  Follow-up answered correctly: {fu!r}", "PASS")

            except Exception as e:
                log(f"Scenario 9 FAILED for seed '{seed_q}': {e}", "FAIL")
                failures += 1

    log("=" * 60)
    if failures == 0:
        log("ALL SCENARIOS PASSED", "PASS")
    else:
        log(f"{failures} SCENARIO(S) FAILED", "FAIL")
    return failures


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> int:
    import pathlib

    project_root = pathlib.Path(__file__).parent.parent
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    # Make sure the project root is on PYTHONPATH
    env["PYTHONPATH"] = str(project_root) + os.pathsep + env.get("PYTHONPATH", "")

    log(f"Starting server on port {PORT} (cwd={project_root})...", "STEP")
    server_log = open(project_root / "logs" / "verify_server.log", "w", encoding="utf-8")
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "backend.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(PORT),
            "--log-level",
            "info",
        ],
        env=env,
        stdout=server_log,
        stderr=server_log,
        cwd=str(project_root),
    )

    try:
        asyncio.run(wait_for_server())
        failures = asyncio.run(run_scenarios())
        return 1 if failures > 0 else 0
    except Exception as e:
        import traceback
        log(f"Verification crashed: {e!r}", "FAIL")
        traceback.print_exc()
        # Print last lines of server log for diagnosis
        server_log.flush()
        try:
            with open(project_root / "logs" / "verify_server.log", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            log("Last 20 lines of server log:", "INFO")
            for line in lines[-20:]:
                print("  " + line.rstrip(), flush=True)
        except Exception:
            pass
        return 2
    finally:
        server_log.close()
        log("Stopping server...", "STEP")
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        log("Server stopped")


if __name__ == "__main__":
    sys.exit(main())
