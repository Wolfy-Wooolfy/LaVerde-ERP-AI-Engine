"""route_auth.py — SINGLE SOURCE OF TRUTH for the unauthenticated route-auth probe.

This is the reusable core extracted from ``scripts/audit_api_auth.py``. It is shared
by BOTH:
  * ``scripts/audit_api_auth.py``      — the human-readable, one-shot audit report.
  * ``tests/security/test_api_auth_guard.py`` — the permanent CI regression guard.

There is exactly ONE definition of the route enumeration, the unauthenticated probe,
the classifier, and the two explicit allowlists — so the audit and the guard can never
drift apart.

TEST / TOOLING ONLY. This module lives under ``scripts/`` and is NEVER imported by the
running app (nothing in ``backend/`` imports it). It is also app-agnostic: every entry
point takes the FastAPI ``app`` as an argument and this module never imports
``backend.main`` — that lets the guard run the very same classifier over a throwaway
in-memory app to prove it is not vacuous.

Why no lifespan / why $0 (inherited from the audit):
  ``TestClient(app)`` is used WITHOUT its context manager, so the app's startup
  (CrmService, OpenAI client, user-DB seed) never runs. Unauthenticated requests are
  rejected by ``get_current_user`` / ``get_current_user_html`` (they read
  ``request.session``) BEFORE any ``app.state`` / Odoo / OpenAI dependency resolves, so
  the probe touches no live backend. No Odoo RPC, no OpenAI calls.

READ-ONLY: changes no app code, routers, or Odoo. Discovery only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from starlette.routing import Mount
from starlette.testclient import TestClient

# Methods that never carry auth-bearing intent for this probe.
_SKIP_METHODS = {"HEAD", "OPTIONS"}

# Statuses that may carry a redirect Location header.
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}

_PARAM_RE = re.compile(r"\{[^}]+\}")


# ── EXPLICIT, DOCUMENTED ALLOWLISTS ───────────────────────────────────────────
# These two collections ARE the reviewable "intentionally public" surface. A new
# route is allowed to be reachable without a session ONLY if it is gated
# (require_module_api/html + get_current_user[_html]) OR it appears below with a
# documented reason. Adding an entry here is a deliberate, reviewable act.

# The 8 routes (7 distinct paths; /login covers both GET form and POST submit) that
# are PUBLIC BY DESIGN — the liveness probe, the auth entry/exit points, and the
# interactive API docs / schema. None of these return business data.
PUBLIC_ALLOWLIST = {
    "/health",                 # root liveness probe (main.py) — public by design, returns only status/uptime
    "/login",                  # GET renders the login form; POST submits credentials — the auth entry point
    "/logout",                 # clears the session, 303 → /login; returns no data
    "/docs",                   # Swagger UI — interactive API docs, no data
    "/redoc",                  # ReDoc UI — interactive API docs, no data
    "/openapi.json",           # OpenAPI schema document — route shapes only, no data
    "/docs/oauth2-redirect",   # Swagger OAuth2 redirect helper page — no data
}

# The 3 legacy 301 redirect-shims (main.py): old /crm/* URLs that permanently redirect
# to their gated /api/v1/* equivalents. Each is allow-listed ONLY together with the
# exact /api/v1/* target it must redirect to — verify_redirect_shim() asserts the live
# 301 still points there, so a shim can never silently mutate into a data route.
REDIRECT_SHIM_ALLOWLIST = {
    "/crm/summary":                       "/api/v1/summary",                       # legacy → gated CRM summary
    "/crm/followup-risk":                 "/api/v1/followup-risk",                 # legacy → gated follow-up risk
    "/crm/data-quality/missing-contact":  "/api/v1/data-quality/missing-contact",  # legacy → gated data-quality view
}


# ── Probe data structures ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProbeTarget:
    """One (method, route) pair to probe. Carries the live route object so callers
    that render reports (the audit) can introspect dependencies without re-walking
    ``app.routes``."""

    method: str
    path: str       # template path, e.g. /api/v1/hr/employee/{employee_id}
    concrete: str   # param-substituted path actually requested, e.g. /api/v1/hr/employee/1
    name: str
    route: object


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of one unauthenticated request. ``status`` is the int status code, or
    the string ``"ERR"`` when the handler raised before returning a response (which
    means auth was bypassed — a reached handler)."""

    status: object       # int, or "ERR"
    location: str
    error: str | None    # exception type name when the handler raised, else None


@dataclass(frozen=True)
class Violation:
    """A route reachable without authentication that is not allow-listed."""

    method: str
    path: str
    status: object

    def __str__(self) -> str:
        return f"{self.method} {self.path} [{self.status}]"


# ── Route enumeration ─────────────────────────────────────────────────────────


def iter_probeable_routes(app: object):
    """Yield one ProbeTarget per (method, route), skipping Mount/static/WebSocket
    routes and the HEAD/OPTIONS methods. Path params are substituted with "1"."""
    for route in app.routes:  # type: ignore[attr-defined]
        if isinstance(route, Mount):
            continue  # static mount / sub-app
        methods = getattr(route, "methods", None)
        if not methods:
            continue  # WebSocket / lifespan / non-HTTP
        path = getattr(route, "path", None)
        if not path:
            continue
        name = getattr(route, "name", None) or getattr(
            getattr(route, "endpoint", None), "__name__", "?"
        )
        concrete = _PARAM_RE.sub("1", path)
        for method in sorted(m for m in methods if m not in _SKIP_METHODS):
            yield ProbeTarget(method, path, concrete, name, route)


# ── Unauthenticated probe ─────────────────────────────────────────────────────


def unauth_client(app: object) -> TestClient:
    """A TestClient bound to ``app`` WITHOUT entering its lifespan context (no
    startup), follow_redirects=False so callers see raw status + Location."""
    return TestClient(app, follow_redirects=False)  # type: ignore[arg-type]


def probe_unauthenticated(client: TestClient, target: ProbeTarget) -> ProbeResult:
    """Send ONE unauthenticated request for ``target``. No session cookie. POST/PUT/PATCH
    send an empty JSON body. Response BODIES ARE NEVER READ (no data leakage); only the
    status code and Location header are recorded. A raised exception means the handler
    was reached (auth bypassed) and is reported as status "ERR"."""
    json_body = {} if target.method in {"POST", "PUT", "PATCH"} else None
    try:
        resp = client.request(
            target.method, target.concrete, json=json_body, follow_redirects=False
        )
    except Exception as exc:  # handler blew up before returning a response
        return ProbeResult("ERR", "", type(exc).__name__)
    return ProbeResult(resp.status_code, resp.headers.get("location", ""), None)


# ── Classification (used by the audit report) ─────────────────────────────────


def classify(path: str, status: int, location: str) -> tuple[str, str]:
    """Return (CLASS, reason) for one unauthenticated request result."""
    if path in PUBLIC_ALLOWLIST or path.startswith("/static"):
        return "PUBLIC-OK", "expected public (health/login/docs/static)"
    if status in (401, 403):
        return "GATED", f"{status} auth/role required"
    if status in (302, 303, 307, 308):
        if "/login" in location:
            return "GATED", f"{status} → login redirect"
        return "REDIRECT", f"{status} → {location or '?'} (no data)"
    if status == 429:
        return "INCONCLUSIVE", "429 rate-limited — re-run"
    if 200 <= status < 300:
        return "EXPOSED", f"{status} handler reached (data returned)"
    if status in (402, 422, 500, 502, 503):
        # 422 = body validation reached (auth passed); 5xx/402 = handler reached.
        return "EXPOSED", f"{status} reached handler/validation (auth bypassed)"
    if status == 404:
        return "INCONCLUSIVE", "404 — dummy path param may not resolve"
    if status == 405:
        return "INCONCLUSIVE", "405 method not allowed"
    return "REVIEW", f"unexpected {status}"


# ── Guard predicate (used by the CI regression guard) ─────────────────────────


def is_login_redirect(status: object, location: str) -> bool:
    """True iff this is an HTML auth gate: a redirect whose target is the login page."""
    return status in _REDIRECT_STATUSES and "/login" in (location or "")


def verify_redirect_shim(path: str, status: object, location: str) -> bool:
    """True iff ``path`` is an allow-listed legacy shim that is STILL a real 301
    redirect to its documented /api/v1/* target. If the live redirect ever changes
    status, target, or stops pointing at /api/v1/*, this returns False and the shim is
    treated as a violation — so a shim can never silently become a data route."""
    target = REDIRECT_SHIM_ALLOWLIST.get(path)
    if target is None:
        return False
    return status == 301 and location == target and target.startswith("/api/v1/")


def is_acceptable_unauthenticated(target: ProbeTarget, result: ProbeResult) -> bool:
    """The guard's safe-set predicate. Returns True when an unauthenticated probe of
    ``target`` produced an acceptable outcome; False marks ``target`` a violation.

    Acceptable == the route is NOT reachable without auth, i.e. it is one of:
      * 401 / 403            — auth or role required;
      * 3xx → /login         — HTML auth gate;
      * in PUBLIC_ALLOWLIST  — intentionally public, documented;
      * a verified 301 shim  — legacy redirect to its documented /api/v1/* target.
    Anything else (2xx, 422, 5xx, an ERR raise, a bare redirect, a 404/405/429
    inconclusive) is a violation: it must be investigated and either gated or
    explicitly allow-listed."""
    if result.status in (401, 403):
        return True
    if is_login_redirect(result.status, result.location):
        return True
    if target.path in PUBLIC_ALLOWLIST:
        return True
    if verify_redirect_shim(target.path, result.status, result.location):
        return True
    return False


def find_auth_violations(app: object) -> tuple[int, list[Violation]]:
    """Probe EVERY enumerable route on ``app`` unauthenticated and collect violations.

    Returns ``(probed_count, violations)`` — the number of (method, route) pairs probed
    and the list of those not in the safe set. An empty list means no route is reachable
    without authentication. This is the single function the CI guard asserts on."""
    client = unauth_client(app)
    probed = 0
    violations: list[Violation] = []
    for target in iter_probeable_routes(app):
        probed += 1
        result = probe_unauthenticated(client, target)
        if not is_acceptable_unauthenticated(target, result):
            violations.append(Violation(target.method, target.path, result.status))
    return probed, violations
