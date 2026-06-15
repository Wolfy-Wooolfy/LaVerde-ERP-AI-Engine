"""CI regression guard — every route must be unreachable without authentication.

The one-shot ``scripts/audit_api_auth.py`` proved the current posture (0 EXPOSED).
This test makes that posture PERMANENT: if anyone later adds a route and forgets to
gate it, the suite goes red here and the PR cannot merge silently.

It reuses the SINGLE SOURCE OF TRUTH in ``scripts/_lib/route_auth.py`` — the same
route enumeration, unauthenticated probe, classifier, and the two explicit allowlists
the audit uses — so the guard and the audit can never disagree about what "gated" or
"intentionally public" means.

No lifespan / $0: the probe uses TestClient WITHOUT its context manager, exactly as the
audit does. ``get_current_user`` / ``get_current_user_html`` reject the unauthenticated
request (they read ``request.session``) before any ``app.state`` / Odoo / OpenAI
dependency resolves, so this test needs no live backend, makes no Odoo RPC, and spends
no OpenAI budget.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException

# Expose the shared `_lib` core (under scripts/) on sys.path, mirroring how the
# verify_*/audit scripts bootstrap it. The core is TEST/TOOLING ONLY and is never
# imported by the running app.
_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from _lib.route_auth import (  # noqa: E402
    PUBLIC_ALLOWLIST,
    REDIRECT_SHIM_ALLOWLIST,
    classify,
    find_auth_violations,
    iter_probeable_routes,
    probe_unauthenticated,
    unauth_client,
    verify_redirect_shim,
)

from backend.main import app  # noqa: E402


# ── (a) No route reachable without auth ───────────────────────────────────────


def test_no_route_reachable_without_auth():
    """Probe every route on the real app unauthenticated; the violation list must be
    EMPTY. A violation = any route that is NOT (401/403, OR a 3xx → /login redirect,
    OR in PUBLIC_ALLOWLIST, OR a verified 301 REDIRECT_SHIM).

    On failure the message enumerates EVERY offending route at once as METHOD PATH
    [STATUS], so a developer sees the full gap list in one run.
    """
    probed, violations = find_auth_violations(app)

    # Sanity: the guard must actually be probing the real surface, not an empty app.
    assert probed > 0, "guard enumerated zero routes — enumeration is broken"

    assert not violations, (
        f"{len(violations)} route(s) are reachable WITHOUT authentication "
        f"(probed {probed} routes):\n"
        + "\n".join(f"  - {v}" for v in violations)
        + "\n\nEvery route must be gated (require_module_api/html + "
        "get_current_user[_html]) OR explicitly added to PUBLIC_ALLOWLIST in "
        "scripts/_lib/route_auth.py with a documented reason."
    )


def test_redirect_shims_still_redirect_to_api_v1():
    """Each allow-listed legacy shim must STILL be a live 301 → its documented
    /api/v1/* target, so a shim can never silently become a data route."""
    client = unauth_client(app)
    by_path = {t.path: t for t in iter_probeable_routes(app)}

    for shim_path, target in REDIRECT_SHIM_ALLOWLIST.items():
        assert shim_path in by_path, (
            f"REDIRECT_SHIM_ALLOWLIST entry {shim_path!r} is not a route on the app — "
            "stale allowlist entry."
        )
        result = probe_unauthenticated(client, by_path[shim_path])
        assert result.status == 301, (
            f"{shim_path} should 301-redirect, got status {result.status}"
        )
        assert result.location == target, (
            f"{shim_path} should redirect to {target}, got {result.location!r}"
        )
        assert target.startswith("/api/v1/"), (
            f"shim {shim_path} target {target!r} is not an /api/v1/* path"
        )
        assert verify_redirect_shim(shim_path, result.status, result.location), (
            f"{shim_path} failed shim verification"
        )


# ── (b) Guard is not vacuous ──────────────────────────────────────────────────


def test_guard_is_not_vacuous():
    """Build a THROWAWAY in-memory app with two non-allowlisted routes — one ungated
    data route and one gated route — and run the SAME classifier over it. The ungated
    route must be flagged EXPOSED and the gated route GATED. This proves the guard
    actually catches an auth gap, without touching the real app."""
    mini = FastAPI()

    def _raise_401() -> None:
        # Stand-in for require_module_api/get_current_user: rejects unauthenticated.
        raise HTTPException(status_code=401, detail="auth required")

    @mini.get("/throwaway/ungated-data")
    def _ungated_data() -> dict:
        return {"secret": "reachable without auth"}

    @mini.get("/throwaway/gated-data", dependencies=[Depends(_raise_401)])
    def _gated_data() -> dict:
        return {"secret": "should never be reached"}

    client = unauth_client(mini)
    classes: dict[str, str] = {}
    for target in iter_probeable_routes(mini):
        result = probe_unauthenticated(client, target)
        klass, _ = classify(target.path, result.status, result.location)
        classes[target.path] = klass

    # Neither throwaway path is in the real allowlists, so the classifier alone decides.
    assert "/throwaway/ungated-data" not in PUBLIC_ALLOWLIST
    assert "/throwaway/gated-data" not in PUBLIC_ALLOWLIST

    assert classes.get("/throwaway/ungated-data") == "EXPOSED", (
        "guard FAILED to flag an ungated data route — it would not catch a real gap. "
        f"got {classes.get('/throwaway/ungated-data')!r}"
    )
    assert classes.get("/throwaway/gated-data") == "GATED", (
        "guard misclassified a gated route. "
        f"got {classes.get('/throwaway/gated-data')!r}"
    )

    # And the end-to-end violation finder must flag exactly the ungated route.
    _, violations = find_auth_violations(mini)
    offending = {v.path for v in violations}
    assert "/throwaway/ungated-data" in offending
    assert "/throwaway/gated-data" not in offending


# ── (c) Public allowlist is minimal ───────────────────────────────────────────


def test_public_allowlist_is_minimal():
    """Every path in PUBLIC_ALLOWLIST must actually exist on the app. A stale entry
    is dangerous: it would silently mask a FUTURE data route added at the same path."""
    live_paths = {t.path for t in iter_probeable_routes(app)}
    stale = sorted(p for p in PUBLIC_ALLOWLIST if p not in live_paths)
    assert not stale, (
        "PUBLIC_ALLOWLIST contains path(s) that no longer exist on the app: "
        f"{stale}. Remove them — a stale public entry could mask a future data route "
        "added at the same path."
    )
