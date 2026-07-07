"""
Live verification for N4 — CRM data-quality detail lists
(missing-stage / missing-salesperson), Session 20.

For EACH of the two data-quality issues, TRIPLE AGREEMENT:
  (a) the dashboard card count — GET /api/v1/summary →
      data_quality.missing_stage_count / missing_salesperson_count
      (the exact value rendered on the /dashboard cards), vs
  (b) the new list endpoint total — GET
      /api/v1/data-quality/missing-{stage,salesperson} →
      pagination.total, vs
  (c) a direct Odoo search_count over the SAME domain via OdooClient,
      built by backend.modules.crm.domain.build_missing_stage_domain() /
      build_missing_salesperson_domain() — the single source the server
      itself uses for BOTH (a) and (b), so identity holds by construction;
      this run proves it holds live.

All three must be EQUAL (run back-to-back). Tiny drift (≤ 2 records or
≤ 0.5%) → FLAG with values (intraday data movement); anything larger →
FAIL (structural). missing-salesperson == 0 is a valid PASS path (clean
empty envelope asserted).

Plus:
  - HTTP 200 on both JSON endpoints AND both hub HTML pages
    (/data-quality?tab=stage, /data-quality?tab=salesperson — the legacy
    /data-quality/missing-{stage,salesperson} URLs now 302 to these).
  - Page-1 pagination sanity: page == 1, page_size echoed, total_pages ==
    ceil(total / page_size), has_prev == False, has_next == (total >
    page_size), len(data) == min(page_size, total), row shape keys.

READ-ONLY: GET requests + search_count direct RPCs only (ALLOWED_METHODS
enforced by OdooClient). No create/write/unlink. No OpenAI. AI cost = $0.00.

Usage:
    python scripts/verify_dq_details_live.py [--url http://localhost:8000]

Requires the FastAPI server to be running and reachable.
Set VERIFY_USERNAME / VERIFY_PASSWORD env vars to override the default
admin credentials. ONE login per process (limiter 10/minute).

Exit 0  — all assertions passed (FLAGs allowed)
Exit 1  — at least one assertion failed or the server was unreachable
Exit 2  — Decision 6.4 ritual not confirmed (DQ_VERIFY_CONFIRMED != "1")

Appends one tab-separated row to logs/dq_details_verification.log per run.

NOTE — Decision 6.4 restart ritual REQUIRED before running:
    1. Kill any uvicorn --reload server (and all python processes)
    2. Confirm port 8000 is free
    3. Purge __pycache__ everywhere
    4. Start clean: python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
       (scripts/start_server.bat encodes steps 1-4)
    5. Run this script immediately (no warm-up call needed)
"""

import argparse
import asyncio
import io
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

# Run from the PROJECT ROOT (python scripts/verify_dq_details_live.py): backend
# Settings resolves .env relative to CWD. Both the repo root (backend.*) and
# scripts/ (_lib.*) go on sys.path so imports work either way.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _lib.api_session import ApiLoginError, login as api_login
from backend.modules.crm.domain import (
    build_missing_salesperson_domain,
    build_missing_stage_domain,
)
from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Decision 6.4 ritual enforcement ──────────────────────────────────────────

_RITUAL = """
┌─────────────────────────────────────────────────────────────────┐
│  Decision 6.4 — Pre-Verification Ritual (Windows PowerShell)    │
├─────────────────────────────────────────────────────────────────┤
│  1. Get-Process -Name python -EA SilentlyContinue |             │
│       Stop-Process -Force                                       │
│  2. Get-ChildItem -Path . -Filter __pycache__ -Recurse          │
│       -Directory | Remove-Item -Recurse -Force                  │
│  3. python -m uvicorn backend.main:app --host 0.0.0.0           │
│       --port 8000        (NO --reload flag)                     │
│  4. Set environment: $env:DQ_VERIFY_CONFIRMED = "1"             │
│  5. Re-run: python scripts/verify_dq_details_live.py            │
└─────────────────────────────────────────────────────────────────┘
"""

# ── Config ────────────────────────────────────────────────────────────────────

DEFAULT_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")
LOG_FILE = "logs/dq_details_verification.log"

_PAGE_SIZE = 50

_ISSUES = (
    {
        "key": "missing_stage",
        "card_field": "missing_stage_count",
        "endpoint": "/api/v1/data-quality/missing-stage",
        "page": "/data-quality?tab=stage",
        "domain_builder": build_missing_stage_domain,
    },
    {
        "key": "missing_salesperson",
        "card_field": "missing_salesperson_count",
        "endpoint": "/api/v1/data-quality/missing-salesperson",
        "page": "/data-quality?tab=salesperson",
        "domain_builder": build_missing_salesperson_domain,
    },
)

_ROW_KEYS = (
    "lead_id", "opportunity_name", "contact_name", "salesperson_id",
    "salesperson_name", "team_id", "team_name", "stage_id", "stage_name",
    "source_id", "source_name", "create_date",
)

_TINY_DRIFT_ABS = 2
_TINY_DRIFT_PCT = 0.5

_SEP = "═" * 72
_PASS = "[PASS]"
_FAIL = "[FAIL]"
_INFO = "[INFO]"
_FLAG = "[FLAG]"


def _log(prefix: str, msg: str) -> None:
    print(f"{prefix} {msg}", flush=True)


def _check(label: str, condition: bool, detail: str = "") -> bool:
    marker = _PASS if condition else _FAIL
    _log(marker, f"{label}{(' — ' + detail) if detail else ''}")
    return condition


async def _direct_counts() -> dict:
    """Direct Odoo cross-check: one search_count per issue over the SAME
    domain the server uses (single-source builders). 2 RPCs. READ-ONLY."""
    out: dict = {}
    async with OdooClient() as client:
        for issue in _ISSUES:
            dom = issue["domain_builder"]()
            count = await client.execute_kw("crm.lead", "search_count", args=[dom])
            out[issue["key"]] = {"count": int(count), "domain": dom}
    return out


def _append_log(run_at: str, results: dict, failures: list) -> None:
    os.makedirs("logs", exist_ok=True)
    log_exists = os.path.isfile(LOG_FILE)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        if not log_exists:
            f.write(
                "run_at\tstage_card\tstage_list\tstage_direct\t"
                "sp_card\tsp_list\tsp_direct\tfailures\n"
            )
        st = results.get("missing_stage", {})
        sp = results.get("missing_salesperson", {})
        f.write(
            f"{run_at}\t{st.get('card', '')}\t{st.get('list', '')}\t{st.get('direct', '')}\t"
            f"{sp.get('card', '')}\t{sp.get('list', '')}\t{sp.get('direct', '')}\t"
            f"{','.join(failures) if failures else 'none'}\n"
        )


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    # ── Decision 6.4 ritual guard ─────────────────────────────────────────────
    if os.environ.get("DQ_VERIFY_CONFIRMED") != "1":
        print(_RITUAL)
        print("REFUSED. Set DQ_VERIFY_CONFIRMED=1 after completing")
        print("the ritual above, then re-run this script.")
        sys.exit(2)

    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_URL)
    args = parser.parse_args()
    base_url: str = args.url.rstrip("/")
    run_at = datetime.now(timezone.utc).isoformat()

    _log(_INFO, f"Target : {base_url}  (N4 — data-quality detail lists, Session 20)")
    _log(_INFO, f"Auth   : session-cookie (Decision 18.1) — "
                f"user {os.environ.get('VERIFY_USERNAME', 'admin')!r}")
    _log(_INFO, f"ALLOWED_METHODS : {sorted(ALLOWED_METHODS)}  (read-only direct RPC)")

    failures: list = []
    results: dict = {}

    # ── Step 1: ONE login per process (limiter 10/minute) ────────────────────
    try:
        client = api_login(base_url)
    except ApiLoginError as exc:
        _log(_FAIL, f"Session login failed: {exc}")
        _append_log(run_at, {}, ["login_failed"])
        return 1
    except httpx.ConnectError as exc:
        _log(_FAIL, f"Cannot reach {base_url} — run scripts/start_server.bat "
                    f"(Decision 6.4 ritual) first. ({exc})")
        _append_log(run_at, {}, ["connect_error"])
        return 1
    _log(_INFO, "Session cookie acquired — client reused for every request.")

    try:
        # ── Step 2: (a) dashboard card counts from /api/v1/summary ───────────
        r = client.get("/api/v1/summary", timeout=60)
        if not _check("GET /api/v1/summary → HTTP 200", r.status_code == 200,
                      f"got {r.status_code}"):
            _append_log(run_at, {}, [f"summary_http_{r.status_code}"])
            return 1
        dq = r.json().get("data_quality", {})
        _log(_INFO, f"data_quality block: {dq}")
        for issue in _ISSUES:
            card = dq.get(issue["card_field"])
            if not _check(f"summary.data_quality.{issue['card_field']} is int >= 0",
                          isinstance(card, int) and card >= 0, f"got {card!r}"):
                failures.append(f"{issue['key']}_card_invalid")
            results[issue["key"]] = {"card": card}

        # ── Step 3: (b) list endpoints — 200 + envelope + page-1 sanity ──────
        for issue in _ISSUES:
            key = issue["key"]
            print()
            _log(_INFO, f"── {key} — list endpoint {issue['endpoint']} ──")
            r = client.get(f"{issue['endpoint']}?page=1&page_size={_PAGE_SIZE}", timeout=60)
            if not _check(f"{key}: HTTP 200", r.status_code == 200, f"got {r.status_code}"):
                failures.append(f"{key}_http_{r.status_code}")
                continue
            body = r.json()
            for k in ("ok", "data", "pagination"):
                if not _check(f"{key}: envelope key '{k}' present", k in body):
                    failures.append(f"{key}_missing_{k}")
            pag = body.get("pagination", {})
            data = body.get("data", [])
            total = pag.get("total")
            results[key]["list"] = total
            if not _check(f"{key}: pagination.total is int >= 0",
                          isinstance(total, int) and total >= 0, f"got {total!r}"):
                failures.append(f"{key}_total_invalid")
                continue
            exp_pages = math.ceil(total / _PAGE_SIZE)
            checks = (
                ("pagination.page == 1", pag.get("page") == 1, repr(pag.get("page"))),
                (f"pagination.page_size == {_PAGE_SIZE}", pag.get("page_size") == _PAGE_SIZE,
                 repr(pag.get("page_size"))),
                (f"pagination.total_pages == ceil(total/{_PAGE_SIZE}) == {exp_pages}",
                 pag.get("total_pages") == exp_pages, repr(pag.get("total_pages"))),
                ("pagination.has_prev == False", pag.get("has_prev") is False,
                 repr(pag.get("has_prev"))),
                (f"pagination.has_next == (total > {_PAGE_SIZE}) == {total > _PAGE_SIZE}",
                 pag.get("has_next") == (total > _PAGE_SIZE), repr(pag.get("has_next"))),
                (f"len(data) == min({_PAGE_SIZE}, total) == {min(_PAGE_SIZE, total)}",
                 len(data) == min(_PAGE_SIZE, total), f"got {len(data)}"),
            )
            for label, ok, detail in checks:
                if not _check(f"{key}: {label}", ok, detail):
                    failures.append(f"{key}_pagination_sanity")
            if data:
                row = data[0]
                missing = [k for k in _ROW_KEYS if k not in row]
                if not _check(f"{key}: page-1 row has all {len(_ROW_KEYS)} keys",
                              not missing, f"missing {missing}"):
                    failures.append(f"{key}_row_shape")
            else:
                _log(_INFO, f"{key}: data == [] (total == {total}) — "
                            "empty envelope is clean (valid PASS path).")

            # HTML page
            rp = client.get(issue["page"], timeout=60)
            if not _check(f"{key}: HTML page {issue['page']} → HTTP 200",
                          rp.status_code == 200, f"got {rp.status_code}"):
                failures.append(f"{key}_page_http_{rp.status_code}")

        # ── Step 4: (c) direct Odoo search_count over the same domains ───────
        print()
        _log(_INFO, "Direct Odoo cross-check (search_count over the single-source domains):")
        direct = asyncio.run(_direct_counts())
        for issue in _ISSUES:
            key = issue["key"]
            results[key]["direct"] = direct[key]["count"]
            _log(_INFO, f"{key}: domain = {direct[key]['domain']}")

        # ── Step 5: TRIPLE AGREEMENT ──────────────────────────────────────────
        print()
        _log(_INFO, "Triple agreement — card (a) vs list total (b) vs search_count (c):")
        for issue in _ISSUES:
            key = issue["key"]
            a = results[key].get("card")
            b = results[key].get("list")
            c = results[key].get("direct")
            vals = [v for v in (a, b, c) if isinstance(v, int)]
            detail = f"card={a} list={b} direct={c}"
            if len(vals) != 3:
                _check(f"{key}: all three values collected", False, detail)
                failures.append(f"{key}_triple_incomplete")
                continue
            spread = max(vals) - min(vals)
            if spread == 0:
                _check(f"{key}: TRIPLE AGREEMENT (exact)", True, detail)
                if max(vals) == 0:
                    _log(_INFO, f"{key}: count is 0 — zero is a valid PASS "
                                "(clean empty state).")
            else:
                base = max(vals)
                pct = (spread / base * 100) if base else 0.0
                tiny = spread <= _TINY_DRIFT_ABS or pct <= _TINY_DRIFT_PCT
                if tiny:
                    _log(_FLAG, f"{key}: tiny drift — {detail} "
                                f"(spread {spread}, {pct:.3f}%) — intraday data "
                                "movement between back-to-back calls; review values.")
                else:
                    _check(f"{key}: TRIPLE AGREEMENT", False,
                           f"{detail} (spread {spread}, {pct:.3f}%) — STRUCTURAL")
                    failures.append(f"{key}_triple_mismatch")
    finally:
        client.close()

    # ── Structured output ─────────────────────────────────────────────────────
    print()
    print(_SEP)
    print("N4 — Data-Quality Detail Lists — Live Verification (Session 20)")
    print(f"Run timestamp : {run_at}")
    print(_SEP)
    print(f"  {'Issue':<22} {'Card (a)':>10} {'List (b)':>10} {'Direct (c)':>10}")
    print(f"  {'─' * 56}")
    for issue in _ISSUES:
        rkey = results.get(issue["key"], {})
        print(f"  {issue['key']:<22} {str(rkey.get('card', '—')):>10} "
              f"{str(rkey.get('list', '—')):>10} {str(rkey.get('direct', '—')):>10}")
    print(_SEP)

    _append_log(run_at, results, failures)

    if failures:
        _log(_FAIL, f"Verification FAILED — {len(failures)} assertion(s): {failures}")
        return 1

    _log(_PASS, "All assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
