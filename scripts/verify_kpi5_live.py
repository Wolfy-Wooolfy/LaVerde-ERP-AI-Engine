"""
Live verification for KPI 5 — Late Uncollected per project.

Usage:
    python scripts/verify_kpi5_live.py [--url http://localhost:8000]

Requires the FastAPI server to be running and reachable.
Set VERIFY_USERNAME / VERIFY_PASSWORD env vars (or .env) to override
the default admin credentials.

Exit 0  — all assertions passed
Exit 1  — at least one assertion failed or the server was unreachable

Appends one tab-separated row to logs/kpi5_verification.log on each run.
"""

import argparse
import io
import os
import sys
from datetime import date, datetime, timezone

import httpx
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

# Force UTF-8 stdout (Windows consoles default to cp1252)
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Config ────────────────────────────────────────────────────────────────────

DEFAULT_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")
USERNAME = os.environ.get("VERIFY_USERNAME", "admin")
PASSWORD = os.environ.get("VERIFY_PASSWORD", "password")
ENDPOINT = "/api/v1/collections/kpi/late-uncollected-by-project"
LOG_FILE = "logs/kpi5_verification.log"

# Discovery-run baseline (2026-05-16, identity-equal to KPI 2 standalone).
_KPI2_SESSION1_BASELINE = 318_626_200.40

# Expected project order and names (Phase 2 confirmed; _PROJECT_NAMES in kpi_service.py).
_EXPECTED_PROJECTS = [
    {"project_id": 1, "project_name": "New Capital"},
    {"project_id": 2, "project_name": "Cassette"},
    {"project_id": 3, "project_name": "La puerta"},
]

_SEP = "═" * 63
_SEP2 = "─" * 61

_PASS = "[PASS]"
_FAIL = "[FAIL]"
_INFO = "[INFO]"


def _log(prefix: str, msg: str) -> None:
    print(f"{prefix} {msg}", flush=True)


def _check(label: str, condition: bool, detail: str = "") -> bool:
    marker = _PASS if condition else _FAIL
    _log(marker, f"{label}{(' — ' + detail) if detail else ''}")
    return condition


def _append_log_row(
    run_at: str,
    np_late: "float | str",
    cs_late: "float | str",
    lp_late: "float | str",
    total: "float | str",
    total_records: "int | str",
    cache_status: str,
    rpc_ms: "int | str",
    odoo_ui_total: str = "",
) -> None:
    os.makedirs("logs", exist_ok=True)
    log_exists = os.path.isfile(LOG_FILE)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        if not log_exists:
            f.write(
                "run_at\tnp_late\tcs_late\tlp_late\ttotal\t"
                "total_records\tcache_status\trpc_ms\todoo_ui_total\n"
            )
        f.write(
            f"{run_at}\t{np_late}\t{cs_late}\t{lp_late}\t{total}\t"
            f"{total_records}\t{cache_status}\t{rpc_ms}\t{odoo_ui_total}\n"
        )


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_URL, help="Backend base URL")
    args = parser.parse_args()
    base_url: str = args.url.rstrip("/")

    url = f"{base_url}{ENDPOINT}"
    run_at = datetime.now(timezone.utc).isoformat()

    _log(_INFO, f"Target: GET {url}")
    _log(_INFO, f"Auth user: {USERNAME}")

    failures: list[str] = []

    # ── Step 1: GET endpoint ──────────────────────────────────────────────────
    try:
        with httpx.Client(timeout=60) as client:
            r = client.get(url, auth=(USERNAME, PASSWORD))
    except httpx.ConnectError as exc:
        msg = f"Cannot reach {base_url} — is the server running? ({exc})"
        _log(_FAIL, msg)
        _append_log_row(run_at, "", "", "", "", "", "", "", msg)
        return 1

    # ── Step 2: HTTP 200 ──────────────────────────────────────────────────────
    if not _check("HTTP 200", r.status_code == 200, f"got {r.status_code}"):
        _log(_INFO, f"Response body: {r.text[:500]}")
        _append_log_row(run_at, "", "", "", "", "", "", "", f"HTTP {r.status_code}")
        return 1

    body: dict = r.json()
    _log(_INFO, f"Response body (top-level keys): {list(body.keys())}")

    # ── Step 3: Required top-level keys ───────────────────────────────────────
    required_keys = (
        "projects", "total_late_uncollected", "total_record_count",
        "currency", "as_of", "cache_status", "rpc_duration_ms", "domain",
    )
    for k in required_keys:
        if not _check(f"key '{k}' present", k in body):
            failures.append(f"missing_key_{k}")

    if failures:
        _append_log_row(run_at, "", "", "", "", "", "", "", f"missing keys: {failures}")
        return 1

    # ── Step 4: Extract values ────────────────────────────────────────────────
    projects: list = body["projects"]
    total_late: float = float(body["total_late_uncollected"])
    total_count: int = int(body["total_record_count"])
    cache_status: str = body["cache_status"]
    rpc_ms: int = int(body["rpc_duration_ms"])
    domain: list = body.get("domain", [])

    # ── Step 5: projects array structure ─────────────────────────────────────
    if not _check("projects array has exactly 3 entries", len(projects) == 3, f"got {len(projects)}"):
        failures.append("projects_count_wrong")

    per_project_keys = {"project_id", "project_name", "late_uncollected", "record_count"}
    for i, proj in enumerate(projects):
        missing = per_project_keys - set(proj.keys())
        if not _check(f"projects[{i}] has all 4 keys", not missing, f"missing: {missing}"):
            failures.append(f"project_{i}_missing_keys")

    # ── Step 6: Project IDs and names ─────────────────────────────────────────
    if len(projects) == 3:
        for i, (actual, expected) in enumerate(zip(projects, _EXPECTED_PROJECTS)):
            if not _check(
                f"projects[{i}].project_id == {expected['project_id']}",
                actual.get("project_id") == expected["project_id"],
                f"got {actual.get('project_id')!r}",
            ):
                failures.append(f"project_{i}_wrong_id")
            if not _check(
                f"projects[{i}].project_name == {expected['project_name']!r}",
                actual.get("project_name") == expected["project_name"],
                f"got {actual.get('project_name')!r}",
            ):
                failures.append(f"project_{i}_wrong_name")

    # ── Step 7: Total consistency ─────────────────────────────────────────────
    if len(projects) == 3:
        computed_total = sum(float(p.get("late_uncollected", 0)) for p in projects)
        if not _check(
            "total_late_uncollected == sum of per-project values",
            abs(total_late - computed_total) < 0.01,
            f"total={total_late:.2f}, sum={computed_total:.2f}, delta={abs(total_late - computed_total):.2f}",
        ):
            failures.append("total_inconsistency")

        computed_count = sum(int(p.get("record_count", 0)) for p in projects)
        if not _check(
            "total_record_count == sum of per-project counts",
            total_count == computed_count,
            f"total={total_count}, sum={computed_count}",
        ):
            failures.append("count_inconsistency")

    # ── Step 8: currency ──────────────────────────────────────────────────────
    if not _check("currency == 'EGP'", body.get("currency") == "EGP", f"got {body.get('currency')!r}"):
        failures.append("wrong_currency")

    if not _check(
        "cache_status in {fresh, cached}",
        cache_status in {"fresh", "cached"},
        f"got {cache_status!r}",
    ):
        failures.append("bad_cache_status")

    # ── Step 9: Response headers ──────────────────────────────────────────────
    cc = r.headers.get("cache-control", "")
    _check("Cache-Control: private", "private" in cc, f"header: {cc!r}")
    _check("Cache-Control: max-age=60", "max-age=60" in cc, f"header: {cc!r}")
    xcs = r.headers.get("x-cache-status", "")
    _check("X-Cache-Status header present", bool(xcs), f"got {xcs!r}")

    # ── Step 10: Domain shape — Candidate C three-clause ──────────────────────
    if _check("domain has 3 clauses", len(domain) == 3, f"got {len(domain)}"):
        _check("domain[0] == state=post", domain[0] == ["state", "=", "post"])
        _check(
            "domain[1] == payment_state in [unpaid,partial]",
            domain[1] == ["payment_state", "in", ["unpaid", "partial"]],
        )
        _check("domain[2][0] == date", domain[2][0] == "date")
        _check("domain[2][1] == <", domain[2][1] == "<")
        date_str = domain[2][2]
        try:
            parsed_date = date.fromisoformat(date_str)
            delta_days = abs((parsed_date - date.today()).days)
            if not _check(
                "domain[2][2] is a valid recent ISO date",
                delta_days <= 1,
                f"got {date_str!r}",
            ):
                failures.append("domain_date_stale")
        except ValueError as exc:
            _log(_FAIL, f"domain[2][2] not a valid ISO date — got {date_str!r}: {exc}")
            failures.append("domain_date_invalid")
    else:
        failures.append("domain_shape")

    # ── Step 11: Second request — cache hit ───────────────────────────────────
    _log(_INFO, "Issuing second request to verify cache hit ...")
    with httpx.Client(timeout=30) as client:
        r2 = client.get(url, auth=(USERNAME, PASSWORD))
    body2: dict = r2.json()
    if not _check(
        "second call cache_status == 'cached'",
        body2.get("cache_status") == "cached",
        f"got {body2.get('cache_status')!r}",
    ):
        failures.append("cache_not_hit_on_second_call")
    if not _check(
        "second call rpc_duration_ms == 0",
        int(body2.get("rpc_duration_ms", -1)) == 0,
        f"got {body2.get('rpc_duration_ms')}",
    ):
        failures.append("cache_rpc_ms_nonzero")

    # ── Structured output ─────────────────────────────────────────────────────
    print()
    print(_SEP)
    print("KPI 5 — Late Uncollected by Project Verification")
    print(f"Run timestamp     : {run_at}")
    print(_SEP)
    print(f"{'Project':<20} {'Late Uncollected':>24}  {'Records':>8}")
    print(_SEP2)
    np_late = cs_late = lp_late = 0.0
    lp_count = 0
    for proj in projects:
        pid = proj.get("project_id")
        name = proj.get("project_name", "?")
        late = float(proj.get("late_uncollected", 0))
        cnt = int(proj.get("record_count", 0))
        label = f"{name} ({pid})"
        print(f"{label:<20} {late:>22,.2f} EGP  {cnt:>8,}")
        if pid == 1:
            np_late = late
        elif pid == 2:
            cs_late = late
        elif pid == 3:
            lp_late = late
            lp_count = cnt
    print(_SEP2)
    print(f"{'TOTAL':<20} {total_late:>22,.2f} EGP  {total_count:>8,}")
    print(_SEP)
    print()

    # ── KPI 2 cross-check block ───────────────────────────────────────────────
    kpi2_delta = total_late - _KPI2_SESSION1_BASELINE
    kpi2_delta_sign = "+" if kpi2_delta >= 0 else ""
    print("KPI 2 cross-check:")
    print(f"  KPI 5 total       : {total_late:>22,.2f} EGP")
    print(f"  KPI 2 standalone  : {_KPI2_SESSION1_BASELINE:>22,.2f} EGP (Session 1 baseline)")
    print(f"  Delta             : {kpi2_delta_sign}{kpi2_delta:>21,.2f} EGP (drift expected)")
    print()

    # ── Board insight note (informational, not an assertion) ──────────────────
    if lp_late > 0:
        _log(
            _INFO,
            f"La puerta: {lp_late:,.2f} EGP across {lp_count} records — "
            "due_amount equals amount (zero actual paid). Board visibility item, not a code concern.",
        )

    print()
    print("Next step (manual):")
    print("  1. Open Odoo -> Collections Mgmt -> Late Installments tab")
    print("  2. Group by Project (or filter per project one at a time)")
    print("  3. Compare each project's Due Amount aggregate to the value above")
    print("  4. Identity-equal match expected (same Candidate C domain as KPI 2)")
    print()

    # ── Result ────────────────────────────────────────────────────────────────
    _append_log_row(
        run_at=run_at,
        np_late=f"{np_late:.2f}",
        cs_late=f"{cs_late:.2f}",
        lp_late=f"{lp_late:.2f}",
        total=f"{total_late:.2f}",
        total_records=total_count,
        cache_status=cache_status,
        rpc_ms=rpc_ms,
    )

    if failures:
        _log(_FAIL, f"Verification FAILED — {len(failures)} assertion(s): {failures}")
        return 1

    _log(_PASS, "All assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
