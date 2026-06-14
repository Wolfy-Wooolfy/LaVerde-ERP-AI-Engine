"""
Live verification for the N5 segment-aware forecast drill-down (Session 21).

The KPI 7 v2 "Dues & Collections — Current Periods" cards each show 4 buckets and,
within each, 3 money segments. N5 adds a per-installment drill-down for ANY one of
the 12 (bucket, segment) combinations. This script proves, for EACH of the 12,
TRIPLE AGREEMENT (< 1.0 EGP; exact preferred):

  (a) CARD     — the segment figure on the aggregate card
                 GET /api/v1/collections/kpi/expected-forecast
                   cleared   → buckets[b].collected_cleared_egp
                   pending   → buckets[b].cheques_pending_egp
                   remaining → buckets[b].remaining_egp
  (b) LIST     — the drill-down's full-set segment-metric total
                 GET /api/v1/collections/drilldown/forecast/{bucket}/{segment}
                   → data.segment_total_egp
  (c) DIRECT   — an independent Odoo recomputation over the SAME proven domain:
                   cleared   : read_group SUM(actual_paid) over base + actual>0
                   remaining : read_group SUM(due_amount) over base + due!=0
                   pending   : superset base + paid>0, keep paid−actual>0, sum it

Both auth (session-cookie, Decision 18.1) and the direct RPCs are READ-ONLY:
GET requests + read_group/search_read only (ALLOWED_METHODS enforced by
OdooClient). No create/write/unlink. No OpenAI. AI cost = $0.00.

Also asserts, per combo:
  - page-1 pagination sanity: total_count >= 0, len(items) == min(page_size,
    total_count), has_next == (page_size < total_count), cursor_next present iff
    has_next.
  - row metric present: when items is non-empty, items[0].segment_metric is
    numeric and items[0].segment == the requested segment.

Usage:
    python scripts/verify_forecast_drilldown_live.py [--url http://localhost:8000]

Requires the FastAPI server running. Set VERIFY_USERNAME / VERIFY_PASSWORD to
override the default admin credentials.

Exit 0  — all assertions passed
Exit 1  — at least one assertion failed or the server was unreachable
Exit 2  — Decision 6.4 ritual not confirmed (FCDD_VERIFY_CONFIRMED != "1")

NOTE — Decision 6.4 restart ritual REQUIRED before running (scripts/start_server.bat
encodes it): kill all python, confirm port 8000 free, purge __pycache__, start
uvicorn WITHOUT --reload, then set FCDD_VERIFY_CONFIRMED=1 and run this script.
"""

import argparse
import asyncio
import io
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

# Run from the PROJECT ROOT. Both the repo root (backend.*) and scripts/ (_lib.*)
# go on sys.path so imports work either way.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _lib.api_session import ApiLoginError, login as api_login  # noqa: E402
from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient  # noqa: E402

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Decision 6.4 ritual enforcement ──────────────────────────────────────────

_RITUAL = """
┌─────────────────────────────────────────────────────────────────┐
│  Decision 6.4 — Pre-Verification Ritual (run scripts/start_server.bat)  │
├─────────────────────────────────────────────────────────────────┤
│  1. Kill all python.exe                                          │
│  2. Confirm port 8000 is free                                    │
│  3. Purge every __pycache__                                      │
│  4. Start uvicorn WITHOUT --reload                               │
│  5. Set environment: FCDD_VERIFY_CONFIRMED = "1"                 │
│  6. Re-run: python scripts/verify_forecast_drilldown_live.py     │
└─────────────────────────────────────────────────────────────────┘
"""

# ── Config ────────────────────────────────────────────────────────────────────

DEFAULT_URL  = os.environ.get("BACKEND_URL", "http://localhost:8000")
CARD_ENDPOINT = "/api/v1/collections/kpi/expected-forecast"
DD_ENDPOINT   = "/api/v1/collections/drilldown/forecast/{bucket}/{segment}"

_MODEL = "rs.installment"
_BUCKET_NAMES = ("this_month", "this_quarter", "this_half", "this_year")
_SEGMENTS = ("cleared", "pending", "remaining")

# Card field exposing each segment's aggregate figure.
_CARD_FIELD = {
    "cleared":   "collected_cleared_egp",
    "pending":   "cheques_pending_egp",
    "remaining": "remaining_egp",
}

_EPS = 1.0          # triple-agreement tolerance (EGP)
_PAGE_SIZE = 50

_SEP  = "═" * 100
_SEP2 = "─" * 100
_PASS = "[PASS]"
_FAIL = "[FAIL]"
_INFO = "[INFO]"
_WARN = "[WARN]"


def _log(prefix: str, msg: str) -> None:
    print(f"{prefix} {msg}", flush=True)


def _check(label: str, condition: bool, detail: str = "") -> bool:
    marker = _PASS if condition else _FAIL
    _log(marker, f"{label}{(' — ' + detail) if detail else ''}")
    return bool(condition)


def _base(start_str: str, end_str: str) -> list:
    """Full-period v2 base domain — NO payment_state filter (Decision 19.1)."""
    return [
        ("state", "=", "post"),
        ("date", ">=", start_str),
        ("date", "<=", end_str),
    ]


async def _direct_segment_totals(windows: "dict[str, tuple[str, str]]") -> "dict[tuple, float]":
    """Independent Odoo recomputation per (bucket, segment) over the proven domains.

    cleared   : read_group SUM(x_studio_actual_paid_amount) over base + actual>0
    remaining : read_group SUM(due_amount) over base + due!=0
    pending   : search_read base + paid>0, keep paid−actual>0, sum the difference
    READ-ONLY (read_group / search_read).
    """
    out: dict[tuple, float] = {}
    async with OdooClient() as client:
        for bname, (start_str, end_str) in windows.items():
            base = _base(start_str, end_str)

            rg_c = await client.execute_kw(
                _MODEL, "read_group",
                args=[base + [("x_studio_actual_paid_amount", ">", 0)],
                      ["x_studio_actual_paid_amount"], []],
                kwargs={"lazy": False},
            )
            out[(bname, "cleared")] = float(
                (rg_c[0] if rg_c else {}).get("x_studio_actual_paid_amount") or 0.0
            )

            rg_r = await client.execute_kw(
                _MODEL, "read_group",
                args=[base + [("due_amount", "!=", 0)], ["due_amount"], []],
                kwargs={"lazy": False},
            )
            out[(bname, "remaining")] = float(
                (rg_r[0] if rg_r else {}).get("due_amount") or 0.0
            )

            rows = await client.execute_kw(
                _MODEL, "search_read",
                args=[base + [("paid_amount", ">", 0)],
                      ["paid_amount", "x_studio_actual_paid_amount"]],
                kwargs={},
            )
            pend = 0.0
            for r in rows:
                d = float(r.get("paid_amount") or 0.0) - float(r.get("x_studio_actual_paid_amount") or 0.0)
                if d > 0:
                    pend += d
            out[(bname, "pending")] = pend
    return out


def main() -> int:
    if os.environ.get("FCDD_VERIFY_CONFIRMED") != "1":
        print(_RITUAL)
        print("REFUSED. Set FCDD_VERIFY_CONFIRMED=1 after completing the ritual above,")
        print("then re-run this script.")
        sys.exit(2)

    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_URL)
    args = parser.parse_args()
    base_url: str = args.url.rstrip("/")
    run_at = datetime.now(timezone.utc).isoformat()

    _log(_INFO, f"Card  : GET {base_url}{CARD_ENDPOINT}")
    _log(_INFO, f"Drill : GET {base_url}{DD_ENDPOINT}")
    _log(_INFO, f"Auth  : session-cookie (Decision 18.1) — user "
                f"{os.environ.get('VERIFY_USERNAME', 'admin')!r}")
    _log(_INFO, f"ALLOWED_METHODS : {sorted(ALLOWED_METHODS)}  (read-only direct RPC)")
    print()

    failures: list[str] = []

    # ── ONE login per process (limiter 10/minute) ────────────────────────────
    try:
        client = api_login(base_url)
    except ApiLoginError as exc:
        _log(_FAIL, f"Session login failed: {exc}")
        return 1
    except httpx.ConnectError as exc:
        _log(_FAIL, f"Cannot reach {base_url} — run scripts/start_server.bat first. ({exc})")
        return 1
    _log(_INFO, "Session cookie acquired — client reused for every request.")

    summary: list[dict] = []
    try:
        # ── Card endpoint — segment figures + period windows ─────────────────
        rc = client.get(CARD_ENDPOINT, timeout=60)
        if not _check("card HTTP 200", rc.status_code == 200, f"got {rc.status_code}"):
            _log(_INFO, f"Body: {rc.text[:400]}")
            return 1
        card = rc.json()
        buckets = card.get("buckets", {})
        today_cairo = card.get("today_cairo")
        _log(_INFO, f"today_cairo = {today_cairo}")

        windows = {
            b: (buckets[b]["period_start"], buckets[b]["period_end"])
            for b in _BUCKET_NAMES if b in buckets
        }
        if not _check("all 4 bucket windows present", len(windows) == 4,
                      f"got {sorted(windows)}"):
            return 1

        # ── Direct Odoo recomputation (12 figures) ───────────────────────────
        _log(_INFO, "Recomputing 12 segment totals directly from Odoo (read-only) ...")
        direct = asyncio.run(_direct_segment_totals(windows))
        print()

        # ── Per-combo triple agreement + pagination sanity ───────────────────
        for b in _BUCKET_NAMES:
            for seg in _SEGMENTS:
                card_val = float(buckets[b].get(_CARD_FIELD[seg]) or 0.0)
                direct_val = float(direct[(b, seg)])

                path = DD_ENDPOINT.format(bucket=b, segment=seg)
                rd = client.get(f"{path}?page_size={_PAGE_SIZE}", timeout=120)
                ok_200 = _check(f"{b}/{seg}: drill HTTP 200",
                                rd.status_code == 200, f"got {rd.status_code}")
                if not ok_200:
                    failures.append(f"{b}_{seg}_http_{rd.status_code}")
                    summary.append({"bucket": b, "segment": seg, "card": card_val,
                                    "list": float("nan"), "direct": direct_val,
                                    "delta": float("nan"), "ok": False,
                                    "total": -1, "page": -1})
                    continue

                env = rd.json()
                data = env.get("data", {})
                meta = env.get("meta", {})
                list_val = float(data.get("segment_total_egp") or 0.0)
                items = data.get("items", [])
                total = int(meta.get("total_count") or 0)

                # Triple agreement (card == list == direct, all within _EPS).
                d_cl = abs(card_val - list_val)
                d_cd = abs(card_val - direct_val)
                d_ld = abs(list_val - direct_val)
                worst = max(d_cl, d_cd, d_ld)
                agree = worst < _EPS
                if not _check(f"{b}/{seg}: triple agreement (< {_EPS} EGP)", agree,
                              f"card {card_val:,.2f} | list {list_val:,.2f} | "
                              f"direct {direct_val:,.2f} | worst Δ {worst:,.4f}"):
                    failures.append(f"{b}_{seg}_triple_mismatch")

                # data echoes the requested bucket/segment.
                if not _check(f"{b}/{seg}: data echoes bucket+segment",
                              data.get("bucket") == b and data.get("segment") == seg,
                              f"got {data.get('bucket')!r}/{data.get('segment')!r}"):
                    failures.append(f"{b}_{seg}_echo_mismatch")

                # Pagination sanity.
                exp_items = min(_PAGE_SIZE, total)
                if not _check(f"{b}/{seg}: page-1 len == min(page_size, total)",
                              len(items) == exp_items,
                              f"len {len(items)} vs expected {exp_items} (total {total})"):
                    failures.append(f"{b}_{seg}_page_len")
                exp_has_next = _PAGE_SIZE < total
                if not _check(f"{b}/{seg}: has_next == (page_size < total)",
                              bool(meta.get("has_next")) == exp_has_next,
                              f"has_next {meta.get('has_next')} total {total}"):
                    failures.append(f"{b}_{seg}_has_next")
                if not _check(f"{b}/{seg}: cursor_next present iff has_next",
                              (meta.get("cursor_next") is not None) == bool(meta.get("has_next")),
                              f"cursor_next {meta.get('cursor_next')!r} has_next {meta.get('has_next')}"):
                    failures.append(f"{b}_{seg}_cursor")

                # Row metric present & labelled.
                if items:
                    row0 = items[0]
                    if not _check(f"{b}/{seg}: row segment_metric numeric",
                                  isinstance(row0.get("segment_metric"), (int, float)),
                                  f"got {row0.get('segment_metric')!r}"):
                        failures.append(f"{b}_{seg}_row_metric_missing")
                    if not _check(f"{b}/{seg}: row.segment == {seg}",
                                  row0.get("segment") == seg,
                                  f"got {row0.get('segment')!r}"):
                        failures.append(f"{b}_{seg}_row_segment_mismatch")
                else:
                    _log(_WARN, f"  {b}/{seg}: 0 rows (empty segment) — metric-field check skipped")

                summary.append({"bucket": b, "segment": seg, "card": card_val,
                                "list": list_val, "direct": direct_val,
                                "delta": worst, "ok": agree,
                                "total": total, "page": len(items)})
                print()
    finally:
        client.close()

    # ── 12-row summary table ───────────────────────────────────────────────────
    print(_SEP)
    print("N5 SEGMENT-AWARE FORECAST DRILL-DOWN — 12-COMBO TRIPLE AGREEMENT")
    print(f"Run timestamp : {run_at}")
    print(_SEP)
    print(f"  {'bucket':<13} {'segment':<10} {'card (EGP)':>18} {'list (EGP)':>18} "
          f"{'direct (EGP)':>18} {'maxΔ':>10} {'rows':>7} {'pg':>4}  result")
    print(f"  {_SEP2}")
    for r in summary:
        result = _PASS if r["ok"] else _FAIL
        print(f"  {r['bucket']:<13} {r['segment']:<10} {r['card']:>18,.2f} "
              f"{r['list']:>18,.2f} {r['direct']:>18,.2f} {r['delta']:>10,.4f} "
              f"{r['total']:>7,} {r['page']:>4}  {result}")
    print(f"  {_SEP2}")
    print()

    if failures:
        _log(_FAIL, f"Verification FAILED — {len(failures)} assertion(s): {failures}")
        return 1

    _log(_PASS, "All 12 combos agree (card == list == direct) and pagination is sane.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
