#!/usr/bin/env python
"""audit_api_auth.py — READ-ONLY API auth-posture audit (human report).

Goal: establish the TRUE auth posture of every HTTP route on the FastAPI app and
list the endpoints reachable WITHOUT a logged-in session.

The reusable core — route enumeration, the unauthenticated probe, the classifier,
and the PUBLIC / REDIRECT-SHIM allowlists — lives in ``scripts/_lib/route_auth.py``
(the SINGLE SOURCE OF TRUTH it shares with the CI guard,
``tests/security/test_api_auth_guard.py``). This script is now just the human-readable
report built on top of that core; the dependency introspection below is report-only
presentation.

What it does:
  * Imports the FastAPI `app` object.
  * Enumerates every route via route_auth.iter_probeable_routes (skips Mount/static/
    WebSocket routes and the HEAD/OPTIONS methods; path params → dummy "1").
  * Sends ONE UNAUTHENTICATED request per (method, route) via route_auth.probe_*.
    Records the raw status code only. Response BODIES ARE NEVER PRINTED (no data
    leakage). The Location header is read solely to detect login redirects.
  * Classifies each route (route_auth.classify) and prints a table + an EXPOSED list.

Why no lifespan / why $0:
  TestClient(app) is used WITHOUT its context manager, so the app's startup
  (CrmService, OpenAI client, user-DB seed) never runs. Unauthenticated requests
  are rejected by get_current_user / get_current_user_html (they read
  request.session) BEFORE any app.state / Odoo / OpenAI dependency is resolved,
  so the audit touches no live backend. No Odoo RPC, no OpenAI calls.

READ-ONLY: changes no app code, routers, or Odoo. Discovery only.

Run:
    python scripts/audit_api_auth.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make `python scripts/audit_api_auth.py` work regardless of CWD, and expose the
# `_lib` package (shared core) on sys.path exactly like the other verify_* scripts.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from _lib.route_auth import (  # noqa: E402
    classify,
    iter_probeable_routes,
    probe_unauthenticated,
    unauth_client,
)

from backend.main import app  # noqa: E402


# ── Dependency introspection (best-effort, report-only) ───────────────────────


def _fmt_dep(call: object) -> str:
    """Human-readable name for a dependency callable.

    Factory closures (require_module_api/html return a nested `_guard`) carry a
    useless `__name__` of '_guard'; we recover the factory from __qualname__ and,
    where possible, the captured module id from the closure → "require_module_api('crm')".
    """
    qn = getattr(call, "__qualname__", None) or getattr(call, "__name__", None) or repr(call)
    if "<locals>" in qn:
        factory = qn.split(".<locals>")[0].rsplit(".", 1)[-1]
        try:
            freevars = call.__code__.co_freevars  # type: ignore[attr-defined]
            cells = call.__closure__ or ()  # type: ignore[attr-defined]
            captured = {name: c.cell_contents for name, c in zip(freevars, cells)}
            mod = captured.get("module_id")
            if mod is not None:
                return f"{factory}('{mod}')"
        except Exception:
            pass
        return factory
    return qn.rsplit(".", 1)[-1] if "." in qn else qn


def _collect_deps(dependant: object, acc: list[str]) -> None:
    for sub in getattr(dependant, "dependencies", []):
        call = getattr(sub, "call", None)
        if call is None:
            continue
        name = _fmt_dep(call)
        if name not in acc:
            acc.append(name)
        _collect_deps(sub, acc)  # recurse to surface nested get_current_user(_html)


def _dep_summary(route: object) -> str:
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return "—"
    acc: list[str] = []
    _collect_deps(dependant, acc)
    return ", ".join(acc) if acc else "—"


# Sort order for the report: gaps first, certainties last.
_CLASS_ORDER = {
    "EXPOSED": 0,
    "REVIEW": 1,
    "INCONCLUSIVE": 2,
    "REDIRECT": 3,
    "GATED": 4,
    "PUBLIC-OK": 5,
}


def main() -> int:
    client = unauth_client(app)

    rows: list[dict] = []
    for target in iter_probeable_routes(app):
        deps = _dep_summary(target.route)
        result = probe_unauthenticated(client, target)
        if result.error is not None:  # handler blew up before returning a response
            rows.append(
                {
                    "method": target.method, "path": target.path, "status": "ERR",
                    "klass": "EXPOSED", "reason": f"raised {result.error}",
                    "name": target.name, "deps": deps,
                }
            )
            continue
        klass, reason = classify(target.path, result.status, result.location)
        rows.append(
            {
                "method": target.method, "path": target.path, "status": str(result.status),
                "klass": klass, "reason": reason, "name": target.name, "deps": deps,
            }
        )

    rows.sort(key=lambda r: (_CLASS_ORDER.get(r["klass"], 9), r["path"], r["method"]))

    # ── (a) Full table ────────────────────────────────────────────────────────
    w_method = max(6, *(len(r["method"]) for r in rows))
    w_path = max(4, *(len(r["path"]) for r in rows))
    w_status = max(6, *(len(r["status"]) for r in rows))
    w_class = max(5, *(len(r["klass"]) for r in rows))

    header = (
        f"{'METHOD':<{w_method}}  {'PATH':<{w_path}}  "
        f"{'STATUS':<{w_status}}  {'CLASS':<{w_class}}  DEPENDENCIES"
    )
    print("=" * len(header))
    print("API AUTH AUDIT — unauthenticated probe of every route (read-only)")
    print("=" * len(header))
    print(header)
    print("-" * len(header))
    for r in rows:
        print(
            f"{r['method']:<{w_method}}  {r['path']:<{w_path}}  "
            f"{r['status']:<{w_status}}  {r['klass']:<{w_class}}  {r['deps']}"
        )

    # ── Class tally ───────────────────────────────────────────────────────────
    tally: dict[str, int] = {}
    for r in rows:
        tally[r["klass"]] = tally.get(r["klass"], 0) + 1
    print("-" * len(header))
    print("Totals: " + " | ".join(f"{k}={tally[k]}" for k in sorted(tally)))

    # ── (b) EXPOSED list ──────────────────────────────────────────────────────
    exposed = [r for r in rows if r["klass"] == "EXPOSED"]
    print()
    print("=" * len(header))
    print("EXPOSED (reachable without login)")
    print("=" * len(header))
    if not exposed:
        print("None — no data endpoints were reachable without authentication.")
    else:
        for i, r in enumerate(exposed, 1):
            print(f"{i:>2}. {r['method']:<6} {r['path']}   [{r['status']}]")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
