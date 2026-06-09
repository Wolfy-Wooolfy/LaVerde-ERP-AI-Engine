# Auth + RBAC — A3 Implementation Plan
## Module-Based Authorization (RBAC Enforcement)

**Plan date:** 2026-06-09
**Author:** Claude Code
**Status:** IMPLEMENTED — Commit 1 green (2026-06-09)
**Stage scope:** AUTHORIZATION ONLY — which modules an authenticated user may reach. A3 gates on `modules` only; `is_admin` is reserved for Phase B Settings UI.

---

## Table of Contents

1. [Files Read Before Planning](#1-files-read-before-planning)
2. [require_module Design — API + HTML Variants](#2-require_module-design--api--html-variants)
3. [_base_ctx + base.html Sidebar Filtering](#3-_base_ctx--basehtml-sidebar-filtering)
4. [Post-Login Landing + No-Modules Page + 403 Page](#4-post-login-landing--no-modules-page--403-page)
5. [Full Test Plan](#5-full-test-plan)
6. [Proposed AUTH_RBAC_DECISIONS.md Entries](#6-proposed-auth_rbac_decisionsmd-entries)
7. [Risks + Open Questions for Khaled](#7-risks--open-questions-for-khaled)
8. [File Change Summary + Commit Structure](#8-file-change-summary--commit-structure)

---

## 1. Files Read Before Planning

| File | Key Finding |
|------|-------------|
| `docs/AUTH_RBAC_DECISIONS.md` | A1–A2 locked decisions; modules = JSON list, `["*"]` = wildcard, `is_admin` reserved for Phase B |
| `docs/AUTH_RBAC_A2_PLAN.md` | Amendment A1: `get_current_user` / `get_current_user_html` return `str`, not `UserRecord` |
| `docs/AUTH_RBAC_DISCOVERY.md` | Authoritative module→route mapping; 4 active modules confirmed |
| `backend/api/deps.py` | `_resolve_active_username → str\|None`; both deps return `str`; no UserRecord exposed |
| `backend/api/v1/router.py` | Clean `include_router` calls — ideal insertion point for router-level module guards |
| `backend/api/v1/endpoints/auth.py` | `login_submit` redirects to `_sanitize_next(next)` → A3 must replace this with module-aware landing |
| `backend/api/v1/endpoints/dashboard.py` | `_base_ctx(request, user: str) → dict`; `user` param is username string; 5 HTML routes |
| `backend/api/v1/endpoints/collections.py` | All 13 endpoints: `_user: str = Depends(get_current_user)` — already have auth |
| `backend/api/v1/endpoints/hr.py` | All 6 endpoints: `_user: str = Depends(get_current_user)` — already have auth |
| `backend/api/v1/endpoints/customer_accounts.py` | All 7 endpoints: `_user: str = Depends(get_current_user)` — already have auth |
| `backend/api/v1/endpoints/summary.py` | CRM router; no prefix, tag="crm" — needs module guard via include_router |
| `backend/auth/models.py` | `UserRecord.modules: list[str]`; `is_admin: bool`; `is_active: bool` |
| `backend/main.py` | Existing exception handlers; `_error_response` helper; no existing 403 handler for HTTPException |
| `frontend/templates/base.html` | Desktop + mobile sidebars hardcoded; active modules: crm, hr, collections, customer_accounts; stubs always shown |
| `tests/conftest.py` | Wipes test DB at session start; seeds testadmin via lifespan; `SESSION_SECRET` set |
| `tests/integration/conftest.py` | `authed_client` = TestClient + POST /login as testadmin (modules=["*"]) |
| `tests/unit/modules/hr/test_router_headcount.py` | `client` fixture: `app.dependency_overrides[get_current_user] = lambda: "testadmin"` + `TestClient(app)` WITHOUT lifespan context manager |
| `tests/unit/auth/test_auth_routes.py` | Auth route unit test pattern for reference |

**Critical pre-condition confirmed:** All 26 API endpoints that should be protected already have `Depends(get_current_user)` (the hotfix `bdadb46` + A2 wiring). A3 adds module guards on top — it does NOT need to re-add authentication.

---

## 2. `require_module` Design — API + HTML Variants

### 2.1 What the Guards Must Do

**Logic (identical for both variants):** Given `module_id`, resolve the session user → `UserRecord` → allow iff `("*" in user.modules) or (module_id in user.modules)` → else 403.

**Two variants mirror A2's dual pattern:**
- `require_module_api(module_id)` — chains off `get_current_user`; 401 is already handled upstream; raises `HTTPException(403)` with JSON detail. Used on all API routes.
- `require_module_html(module_id)` — chains off `get_current_user_html`; 302 to /login is already handled upstream; raises `HTTPException(403)` which the new global handler (§2.3) converts to `403.html`. Used on HTML routes.

### 2.2 Full Implementation in `backend/api/deps.py`

```python
# backend/api/deps.py — ADDITIONS only (existing code untouched)

def require_module_api(module_id: str):
    """
    Factory: dependency that enforces module access for API routes.
    Chains off get_current_user — 401 is already handled if session is absent/inactive.
    Raises HTTP 403 JSON if the user lacks the module.
    """
    def _guard(request: Request, username: str = Depends(get_current_user)) -> None:
        user = request.app.state.user_repo.get_user(username)
        # user is expected to be non-None (get_current_user confirmed session validity).
        # Guard against the race where the user was deleted between the two calls.
        if user is None:
            raise HTTPException(status_code=401, detail="Not authenticated")
        if "*" not in user.modules and module_id not in user.modules:
            raise HTTPException(
                status_code=403,
                detail={"code": "MODULE_ACCESS_DENIED", "module": module_id},
            )
    return _guard


def require_module_html(module_id: str):
    """
    Factory: dependency that enforces module access for HTML routes.
    Chains off get_current_user_html — 302 to /login is already handled if session is absent/inactive.
    Raises HTTP 403 which the global 403 handler renders as 403.html.
    """
    def _guard(request: Request, username: str = Depends(get_current_user_html)) -> None:
        user = request.app.state.user_repo.get_user(username)
        if user is None:
            # Race: user deleted mid-session. Re-authenticate.
            raise HTTPException(
                status_code=302,
                headers={"Location": f"/login?next={request.url.path}"},
            )
        if "*" not in user.modules and module_id not in user.modules:
            raise HTTPException(status_code=403)
    return _guard
```

**Why chaining works without double-calling `get_current_user`:**
FastAPI caches dependency results per request. The endpoint's existing `_user: str = Depends(get_current_user)` and `require_module_api`'s inner `_guard(username: str = Depends(get_current_user))` share the same cached result. `get_current_user` is called exactly once per request. `user_repo.get_user(username)` is one additional SQLite read — sub-millisecond at this scale.

**Import addition required in `deps.py`:**
```python
from fastapi import Depends, HTTPException, Request
```
(`Depends` and `HTTPException` may already be imported; confirm and add only what is missing.)

### 2.3 Global 403 Exception Handler in `backend/main.py`

`require_module_html` raises `HTTPException(403)`. The default FastAPI handler would return plain JSON for that. Instead, add a handler that serves `403.html` to browsers and JSON to API clients — distinguished by the `Accept` header.

```python
# backend/main.py — new handler, alongside existing @app.exception_handler blocks

from fastapi.templating import Jinja2Templates as _Jinja2Templates

_err_templates = _Jinja2Templates(directory="frontend/templates")


@app.exception_handler(403)
async def module_forbidden_handler(request: Request, exc: Exception) -> Response:
    """Serve 403.html for browser requests; JSON for API requests."""
    from backend.core.i18n import detect_lang, make_translator
    if "text/html" in request.headers.get("accept", ""):
        lang = detect_lang(dict(request.cookies), request.headers.get("accept-language", ""))
        ctx = {
            "request": request,
            "lang": lang,
            "is_rtl": lang == "ar",
            "_t": make_translator(lang),
        }
        return _err_templates.TemplateResponse("403.html", ctx, status_code=403)
    return _error_response(
        request, 403, "MODULE_ACCESS_DENIED",
        "You do not have access to this module.",
    )
```

**Coexistence with `ReadOnlyViolationError` handler:** The existing `readonly_violation_handler` handles `ReadOnlyViolationError` (a custom exception class) and returns a `JSONResponse(status_code=403)` directly — it does NOT raise `HTTPException(403)`. Therefore the new `@app.exception_handler(403)` never intercepts read-only violation errors. No conflict.

**Scope of the new handler:** It intercepts ALL `HTTPException(403)` — not only RBAC denials. If any other code raises `HTTPException(403)` in the future, it will receive the same formatted response. This is acceptable since 403 always means forbidden.

### 2.4 Gating Points — API Routes (`backend/api/v1/router.py`)

The **only file changed** for all API module guards. Add `dependencies=[...]` to each `include_router` call. Zero endpoint body changes across the 26 protected endpoints.

```python
# backend/api/v1/router.py — complete replacement

from fastapi import APIRouter, Depends

from backend.api.deps import require_module_api
from backend.api.v1.endpoints import data_quality, followup, health, summary
from backend.api.v1.endpoints.ai import router as ai_router
from backend.api.v1.endpoints.chat import router as chat_router
from backend.api.v1.endpoints.collections import router as collections_router
from backend.api.v1.endpoints.customer_accounts import router as customer_accounts_router
from backend.api.v1.endpoints.dashboard_api import router as dashboard_api_router
from backend.api.v1.endpoints.hr import router as hr_router
from backend.api.v1.endpoints.metrics_endpoint import router as metrics_router

api_v1_router = APIRouter()

# ── Not module-gated (authenticated-only) ────────────────────────────────────
api_v1_router.include_router(health.router)    # /api/v1/health, /health/odoo, /health/deep
api_v1_router.include_router(metrics_router)   # /api/v1/metrics

# ── crm — 6 endpoint files, one shared dependency list ────────────────────────
_crm = [Depends(require_module_api("crm"))]
api_v1_router.include_router(summary.router,       dependencies=_crm)   # /summary
api_v1_router.include_router(followup.router,      dependencies=_crm)   # /followup-risk
api_v1_router.include_router(data_quality.router,  dependencies=_crm)   # /data-quality/*
api_v1_router.include_router(dashboard_api_router, dependencies=_crm)   # /dashboard/*
api_v1_router.include_router(ai_router,            dependencies=_crm)   # /ai/*
api_v1_router.include_router(chat_router,          dependencies=_crm)   # /chat/*

# ── collections — 13 endpoints ────────────────────────────────────────────────
api_v1_router.include_router(
    collections_router,
    dependencies=[Depends(require_module_api("collections"))],
)

# ── customer_accounts — 7 endpoints ──────────────────────────────────────────
api_v1_router.include_router(
    customer_accounts_router,
    dependencies=[Depends(require_module_api("customer_accounts"))],
)

# ── hr — 6 endpoints ──────────────────────────────────────────────────────────
api_v1_router.include_router(
    hr_router,
    dependencies=[Depends(require_module_api("hr"))],
)
```

**What changes from current `router.py`:** The `include_router` calls for the 4 module groups each gain a `dependencies=[...]` argument. The two non-module-gated routers (`health`, `metrics`) are left as-is. The file's import block gains `from fastapi import APIRouter, Depends` and `from backend.api.deps import require_module_api`.

**Endpoint body impact:** None. The `_user: str = Depends(get_current_user)` parameter in every endpoint function stays exactly as written. The module guard is an additional dependency stacked at the router inclusion level.

### 2.5 Gating Points — HTML Routes (`backend/api/v1/endpoints/dashboard.py`)

The 5 HTML routes cannot share a single router-level guard because they belong to different modules. Add `dependencies=[Depends(require_module_html(module_id))]` directly to each `@router.get(...)` decorator. Zero function body changes.

```python
# dashboard.py — decorator changes only, function signatures and bodies UNCHANGED
# Add to imports: from backend.api.deps import ..., require_module_html

@router.get(
    "/dashboard",
    response_class=HTMLResponse,
    summary="CRM dashboard (HTML)",
    dependencies=[Depends(require_module_html("crm"))],          # ← ADD
)
async def dashboard(request, user, service): ...

@router.get(
    "/data-quality/missing-contact",
    response_class=HTMLResponse,
    summary="Missing contact details page (HTML)",
    dependencies=[Depends(require_module_html("crm"))],          # ← ADD
)
async def missing_contact_page(...): ...

@router.get(
    "/collections/dashboard",
    response_class=HTMLResponse,
    summary="Collections dashboard (HTML)",
    dependencies=[Depends(require_module_html("collections"))],  # ← ADD
)
async def collections_dashboard(request, user): ...

@router.get(
    "/customer-accounts/dashboard",
    response_class=HTMLResponse,
    summary="Customer Accounts dashboard (HTML)",
    dependencies=[Depends(require_module_html("customer_accounts"))],  # ← ADD
)
async def customer_accounts_dashboard(request, user): ...

@router.get(
    "/hr/dashboard",
    response_class=HTMLResponse,
    summary="HR overview dashboard (HTML)",
    dependencies=[Depends(require_module_html("hr"))],           # ← ADD
)
async def hr_dashboard(request, user): ...
```

**Dependency execution:** `require_module_html("crm")` depends on `get_current_user_html`. The route function itself also depends on `get_current_user_html` (already in its parameter list). FastAPI's per-request caching ensures `get_current_user_html` runs once. The guard's `_guard` function receives the cached username, looks up the UserRecord, and checks modules — all before the function body runs.

---

## 3. `_base_ctx` + `base.html` Sidebar Filtering

### 3.1 `_base_ctx` Change (`dashboard.py`)

One targeted change to `_base_ctx`: resolve the `UserRecord` to extract `modules` and add `allowed_modules` to the Jinja2 context. The `user: str` parameter signature is **unchanged** (no endpoint body modifications required).

```python
def _base_ctx(request: Request, user: str) -> dict:
    """Common context injected into every page."""
    lang = detect_lang(dict(request.cookies), request.headers.get("accept-language", ""))
    # Resolve UserRecord to pass allowed_modules to the template.
    # By the time _base_ctx runs, require_module_html has already confirmed the session is
    # valid, so user_repo.get_user will return a non-None record.
    _user_record = request.app.state.user_repo.get_user(user)
    allowed_modules: list[str] = _user_record.modules if _user_record else []
    return {
        "request": request,
        "current_user": user,
        "user_display_name": _extract_first_name(user),
        "lang": lang,
        "is_rtl": lang == "ar",
        "_t": make_translator(lang),
        "allowed_modules": allowed_modules,   # NEW — raw list, e.g. ["hr"] or ["*"]
    }
```

**Performance note:** This is one additional `user_repo.get_user(username)` call per HTML page load. It is the third call per request (after `_resolve_active_username` inside `get_current_user_html` and the call inside `require_module_html`). All three are single-row SQLite SELECTs. Negligible for a small-team ERP dashboard. If it ever becomes a concern, the result can be cached in `request.state` at the dependency level in Phase B.

### 3.2 `base.html` — Jinja2 Conditionals

Both sidebars (desktop `<aside>` lines 71–268 and mobile `<aside>` lines 271–330) need the same conditional wrappers. The change is **additive only** — existing HTML is wrapped, not replaced.

**Jinja2 shorthand** used throughout — define once at the top of each `<nav>` block:

```jinja2
{% set am = allowed_modules %}
```

Then the conditional pattern is:
```jinja2
{% if 'module_id' in am or '*' in am %} ... {% endif %}
```

#### Desktop sidebar `<nav>` block — annotated changes

```jinja2
<nav class="flex-1 px-2 py-3 overflow-y-auto ...">

  {# ── CRM: Overview + Data Quality sections ────────────────────────── #}
  {% set am = allowed_modules %}
  {% if 'crm' in am or '*' in am %}
  <p x-show="!sidebarCollapsed" class="px-3 pb-1 pt-2 text-[10px] ...">
    {{ _t("Overview") }}
  </p>
  <a href="/dashboard" class="sidebar-link {% if page == 'dashboard' %}active{% endif %}" title="Dashboard">
    ...
  </a>
  <p x-show="!sidebarCollapsed" class="px-3 pb-1 pt-3 text-[10px] ...">{{ _t("Data Quality") }}</p>
  <a href="/data-quality/missing-contact" class="sidebar-link {% if page == 'missing_contact' %}active{% endif %}" ...>
    ...
  </a>
  {% endif %}

  {# ── Modules section header — shown only if user has any active module #}
  {% if 'crm' in am or 'hr' in am or 'collections' in am or 'customer_accounts' in am or '*' in am %}
  <p x-show="!sidebarCollapsed" class="px-3 pb-1 pt-3 text-[10px] ...">{{ _t("Modules") }}</p>
  {% endif %}

  {# CRM module link #}
  {% if 'crm' in am or '*' in am %}
  <a href="/dashboard" class="sidebar-link active" title="CRM">
    ... CRM ...
  </a>
  {% endif %}

  {# Customer Service — Coming Soon: visible to ['*'] users only (Q2 locked answer) #}
  {% if '*' in am %}
  <div class="sidebar-link opacity-40 cursor-not-allowed ...">...Customer Service...</div>
  {% endif %}

  {# HR module link #}
  {% if 'hr' in am or '*' in am %}
  <a href="/hr/dashboard" class="sidebar-link {% if page == 'hr_dashboard' %}active{% endif %}" ...>
    ... HR ...
  </a>
  {% endif %}

  {# Contracts — Coming Soon: visible to ['*'] users only (Q2 locked answer) #}
  {% if '*' in am %}
  <div class="sidebar-link opacity-40 ...">...Contracts...</div>
  {% endif %}

  {# Collections module link #}
  {% if 'collections' in am or '*' in am %}
  <a href="/collections/dashboard" class="sidebar-link {% if page == 'collections_dashboard' %}active{% endif %}" ...>
    ... Collections ...
  </a>
  {% endif %}

  {# Customer Accounts module link #}
  {% if 'customer_accounts' in am or '*' in am %}
  <a href="/customer-accounts/dashboard" class="sidebar-link {% if page == 'customer_accounts_dashboard' %}active{% endif %}" ...>
    ... Customer Accounts ...
  </a>
  {% endif %}

  {# Accounting + Project Mgmt — Coming Soon: visible to ['*'] users only (Q2 locked answer) #}
  {% if '*' in am %}
  <div ...>...Accounting...</div>
  <div ...>...Project Mgmt...</div>
  {% endif %}

</nav>
```

#### Mobile sidebar `<nav>` block — identical conditional pattern

The mobile `<nav>` block (lines ~297–317) currently lists: Dashboard, Missing Contacts, Collections, Customer Accounts, HR. Apply the same `{% if 'module_id' in am or '*' in am %}` wrappers around each link.

**Stub modules decision (Q2 locked):** "Coming Soon" entries (Customer Service, Contracts, Accounting, Project Mgmt) are **visible only to `["*"]` users** — wrapped in `{% if '*' in am %}`. Restricted users see a clean sidebar with only their actual modules. Admin users see the roadmap stubs unchanged.

**A `["*"]` user** sees all links — identical to today's behavior. No regression.

---

## 4. Post-Login Landing + No-Modules Page + 403 Page

### 4.1 Post-Login Landing Logic (`auth.py`)

**Current behavior:** `login_submit` redirects to `_sanitize_next(next)`, which defaults to `/dashboard`. For a non-CRM user, this means a 302 to `/dashboard` → hit `require_module_html("crm")` → 403. Broken UX.

**New behavior:** After setting `request.session["username"]`, check the `next` param against a module map; fall back to the user's first allowed module dashboard; if no modules, redirect to `/no-modules`.

**New helpers to add to `backend/api/v1/endpoints/auth.py`:**

```python
# Ordered list: first match wins for users with partial module access.
# Order reflects sidebar priority (CRM → HR → Collections → Customer Accounts).
_ORDERED_MODULE_DASHBOARDS: list[tuple[str, str]] = [
    ("crm",               "/dashboard"),
    ("hr",                "/hr/dashboard"),
    ("collections",       "/collections/dashboard"),
    ("customer_accounts", "/customer-accounts/dashboard"),
]

# Maps URL path prefixes to the module required for access.
# Used to validate the `next` parameter against user permissions.
_PATH_MODULE_MAP: dict[str, str] = {
    "/dashboard":          "crm",
    "/data-quality":       "crm",
    "/hr":                 "hr",
    "/collections":        "collections",
    "/customer-accounts":  "customer_accounts",
}


def _user_can_access_path(user_modules: list[str], path: str) -> bool:
    """Return True if user's module list permits navigating to `path`."""
    if "*" in user_modules:
        return True
    for prefix, module_id in _PATH_MODULE_MAP.items():
        if path.startswith(prefix):
            return module_id in user_modules
    # Path not in the module map (e.g. /metrics, /no-modules) — allow.
    return True


def _first_allowed_dashboard(user_modules: list[str]) -> str | None:
    """Return the URL of the first module dashboard the user may reach, or None."""
    if "*" in user_modules:
        return "/dashboard"
    for module_id, url in _ORDERED_MODULE_DASHBOARDS:
        if module_id in user_modules:
            return url
    return None
```

**Updated redirect block in `login_submit`** (replace the current last two lines):

```python
    # Before: return RedirectResponse(url=_sanitize_next(next), status_code=303)
    # After:

    request.session["username"] = username
    safe_next = _sanitize_next(next)

    # Determine landing page:
    # 1. If next was explicitly set and user can access it → go there.
    # 2. Else → first allowed module dashboard.
    # 3. No modules → /no-modules.
    if safe_next != "/dashboard" and _user_can_access_path(user.modules, safe_next):
        target = safe_next
    else:
        landing = _first_allowed_dashboard(user.modules)
        target = landing if landing is not None else "/no-modules"

    return RedirectResponse(url=target, status_code=303)
```

**Why `safe_next != "/dashboard"` as the explicit-next check:** The login form's hidden `<input name="next">` defaults to `/dashboard`. When no redirect was pending, `next` is `/dashboard` — this is not an "explicit" user intent, it's the form default. So a user whose default landing should be `/hr/dashboard` is not incorrectly sent to `/dashboard`. When `next` differs from `/dashboard` (meaning the user was redirected from a real page they tried to access), honor their intent if they have access.

**Edge cases:**
- Admin (modules=["*"]): `safe_next="/dashboard"` → condition fails → `_first_allowed_dashboard(["*"])` returns `/dashboard`. Correct.
- HR-only user with `next=/hr/dashboard` (valid redirect): `safe_next="/hr/dashboard" != "/dashboard"` AND `_user_can_access_path(["hr"], "/hr/dashboard") = True` → goes to `/hr/dashboard`. Correct.
- HR-only user with `next=/collections/dashboard` (denied): cannot access → falls back to `/hr/dashboard`. Correct.
- No-modules user: `_first_allowed_dashboard([])` returns None → `/no-modules`. Correct.

### 4.2 `/no-modules` Route (`auth.py`)

```python
from backend.api.deps import get_current_user_html  # already imported

@router.get("/no-modules", response_class=HTMLResponse, include_in_schema=False)
async def no_modules_page(
    request: Request,
    user: str = Depends(get_current_user_html),
) -> HTMLResponse:
    """Landing page for authenticated users with no modules assigned."""
    lang = detect_lang(dict(request.cookies), request.headers.get("accept-language", ""))
    ctx = {
        "request": request,
        "current_user": user,
        "lang": lang,
        "is_rtl": lang == "ar",
        "_t": make_translator(lang),
    }
    return templates.TemplateResponse(request, "no_modules.html", ctx)
```

Protected by `get_current_user_html` — unauthenticated visitor → redirect to /login. No module guard (the user truly has no modules — gating them would be circular).

### 4.3 `403.html` — Standalone Forbidden Page

**File:** `frontend/templates/403.html` — does NOT extend `base.html`.

**Rationale for standalone:** The user who reaches 403 may have restricted modules. If `base.html` tries to render `allowed_modules` and it is not in the context (because `_base_ctx` was never called), the template would error. A standalone page is safe regardless of what context was prepared.

**Structure (mirrors `login.html` aesthetically):**

```
403.html
├── <head>  — same CSS (app.css, fonts.css, favicon), theme-flash-prevention script
├── <body>
│   └── centered card (max-w-sm, dark mode, rounded-2xl, shadow-sm)
│       ├── Red/danger icon or lock SVG
│       ├── Heading: _t("forbidden.heading")  → "Access Denied"
│       ├── Body:    _t("forbidden.body")     → "You don't have access to this section."
│       ├── <a href="javascript:history.back()">← Back</a>
│       └── <a href="/logout" class="btn btn-secondary">{{ _t("Logout") }}</a>
```

### 4.4 `no_modules.html` — Standalone No-Modules Page

**File:** `frontend/templates/no_modules.html` — does NOT extend `base.html`.

```
no_modules.html
├── <head>  — same CSS, theme script
├── <body>
│   └── centered card
│       ├── Information icon (neutral color)
│       ├── Heading: _t("no_modules.heading") → "No Modules Assigned"
│       ├── Body:    _t("no_modules.body")    → "Your account has no modules assigned. Contact an administrator."
│       └── <a href="/logout" class="btn btn-primary">{{ _t("Logout") }}</a>
```

### 4.5 i18n Keys to Add (`en.json` + `ar.json`)

| Key | English | Arabic |
|-----|---------|--------|
| `forbidden.heading` | `Access Denied` | `تم رفض الوصول` |
| `forbidden.body` | `You don't have access to this section.` | `ليس لديك صلاحية الوصول إلى هذا القسم.` |
| `no_modules.heading` | `No Modules Assigned` | `لا توجد وحدات مُعيَّنة` |
| `no_modules.body` | `Your account has no modules assigned. Contact an administrator.` | `ليس لحسابك أي وحدات مُعيَّنة. تواصل مع المسؤول.` |

---

## 5. Full Test Plan

### 5.1 Restricted User Setup

**How restricted users are created:** A session-scoped `autouse` fixture in `tests/integration/conftest.py` directly instantiates `SQLiteUserRepository` (same as the app uses) and creates three restricted test users. This runs after `tests/conftest.py` wipes the DB file and before any TestClient lifespan fires (which seeds testadmin). The three users coexist with testadmin in the same `data/test-users.db`.

```python
# tests/integration/conftest.py — ADDITIONS

import pytest
from fastapi.testclient import TestClient

from backend.auth.repository import SQLiteUserRepository
from backend.core.config import settings
from backend.main import app


@pytest.fixture(scope="session", autouse=True)
def _seed_rbac_test_users():
    """Create restricted users in the test DB before any integration test fixture runs."""
    repo = SQLiteUserRepository(settings.USER_DB_PATH)
    from backend.auth.password import hash_password
    _TESTPASS_HASH = hash_password("testpass")
    for username, modules in [
        ("hr_only",    ["hr"]),
        ("coll_ca",    ["collections", "customer_accounts"]),
        ("no_modules", []),
    ]:
        try:
            repo.create_user(
                username=username,
                password_hash=_TESTPASS_HASH,
                modules=modules,
                is_admin=False,
                is_active=True,
            )
        except ValueError:
            pass  # user already exists (idempotent)


@pytest.fixture(scope="module")
def hr_only_client():
    """TestClient authenticated as hr_only user (modules=['hr'])."""
    with TestClient(app) as c:
        r = c.post(
            "/login",
            data={"username": "hr_only", "password": "testpass", "next": "/dashboard"},
            follow_redirects=False,
        )
        assert r.status_code == 303, f"Login failed: {r.status_code}"
        yield c


@pytest.fixture(scope="module")
def coll_ca_client():
    """TestClient authenticated as coll_ca user (modules=['collections','customer_accounts'])."""
    with TestClient(app) as c:
        r = c.post(
            "/login",
            data={"username": "coll_ca", "password": "testpass", "next": "/dashboard"},
            follow_redirects=False,
        )
        assert r.status_code == 303, f"Login failed: {r.status_code}"
        yield c


@pytest.fixture(scope="module")
def no_modules_client():
    """TestClient authenticated as no_modules user (modules=[])."""
    with TestClient(app) as c:
        r = c.post(
            "/login",
            data={"username": "no_modules", "password": "testpass", "next": "/dashboard"},
            follow_redirects=False,
        )
        assert r.status_code == 303, f"Login failed: {r.status_code}"
        yield c
```

### 5.2 New Test File: `tests/integration/test_rbac.py`

#### Section A — 403/200 Matrix, API Routes

```python
import pytest
from fastapi.testclient import TestClient


class TestApiModuleGating:
    """403/200 matrix: hr_only and coll_ca against every module-gated API route."""

    @pytest.mark.parametrize("path,hr_expect,ca_expect", [
        # ── CRM routes (hr_only → 403, coll_ca → 403) ────────────────────────
        ("/api/v1/summary",                              403, 403),
        ("/api/v1/followup-risk",                        403, 403),
        ("/api/v1/data-quality/missing-contact",         403, 403),
        ("/api/v1/dashboard/kpis",                       403, 403),
        ("/api/v1/dashboard/sparkline",                  403, 403),
        ("/api/v1/dashboard/heatmap",                    403, 403),
        ("/api/v1/ai/health",                            403, 403),
        ("/api/v1/chat/suggested-questions",             403, 403),
        # ── HR routes (hr_only → 200, coll_ca → 403) ─────────────────────────
        ("/api/v1/hr/kpi/headcount",                     200, 403),
        ("/api/v1/hr/kpi/tenure-distribution",           200, 403),
        ("/api/v1/hr/kpi/payroll-risk-dashboard",        200, 403),
        ("/api/v1/hr/kpi/department-cost",               200, 403),
        # ── Collections routes (hr_only → 403, coll_ca → 200) ────────────────
        ("/api/v1/collections/kpi/late-uncollected",     403, 200),
        ("/api/v1/collections/kpi/collection-rate",      403, 200),
        ("/api/v1/collections/drilldown/portfolio",      403, 200),
        # ── Customer Accounts routes (hr_only → 403, coll_ca → 200) ──────────
        ("/api/v1/customer-accounts/kpi/total-receivables",    403, 200),
        ("/api/v1/customer-accounts/kpi/top-overdue-customers",403, 200),
    ])
    def test_api_module_gating_matrix(
        self, path, hr_expect, ca_expect, hr_only_client, coll_ca_client
    ):
        r_hr = hr_only_client.get(path)
        assert r_hr.status_code == hr_expect, (
            f"hr_only on {path}: expected {hr_expect}, got {r_hr.status_code}"
        )
        if hr_expect == 403:
            body = r_hr.json()
            assert body.get("detail", {}).get("code") == "MODULE_ACCESS_DENIED"
            assert "module" in body.get("detail", {})

        r_ca = coll_ca_client.get(path)
        assert r_ca.status_code == ca_expect, (
            f"coll_ca on {path}: expected {ca_expect}, got {r_ca.status_code}"
        )

    def test_admin_wildcard_never_gets_403(self, authed_client):
        """testadmin (modules=['*']) must not get 403 on any module-gated route."""
        for path in [
            "/api/v1/hr/kpi/headcount",
            "/api/v1/collections/kpi/late-uncollected",
            "/api/v1/customer-accounts/kpi/total-receivables",
        ]:
            r = authed_client.get(path)
            assert r.status_code != 403, f"Admin should not get 403 on {path}"

    def test_unauthed_gets_401_not_403_on_module_routes(self):
        """Unauthenticated requests get 401 from get_current_user before module check runs."""
        with TestClient(app, follow_redirects=False) as c:
            for path in ["/api/v1/hr/kpi/headcount", "/api/v1/collections/kpi/late-uncollected"]:
                r = c.get(path)
                assert r.status_code == 401, f"Expected 401, got {r.status_code} on {path}"

    def test_non_module_gated_routes_accessible_to_all(self, hr_only_client, coll_ca_client):
        """Routes not module-gated (/metrics, /health/*) must be reachable by any authenticated user."""
        for client, name in [(hr_only_client, "hr_only"), (coll_ca_client, "coll_ca")]:
            r = client.get("/api/v1/health")
            assert r.status_code == 200, f"{name} should access /api/v1/health, got {r.status_code}"
```

#### Section B — 403/200 Matrix, HTML Routes

```python
class TestHtmlModuleGating:
    def test_crm_html_dashboard_forbidden_for_hr_only(self, hr_only_client):
        r = hr_only_client.get("/dashboard", follow_redirects=False)
        assert r.status_code == 403
        assert "text/html" in r.headers.get("content-type", "")

    def test_crm_html_data_quality_forbidden_for_hr_only(self, hr_only_client):
        r = hr_only_client.get("/data-quality/missing-contact", follow_redirects=False)
        assert r.status_code == 403

    def test_hr_html_allowed_for_hr_only(self, hr_only_client):
        r = hr_only_client.get("/hr/dashboard", follow_redirects=False)
        assert r.status_code == 200

    def test_collections_html_forbidden_for_hr_only(self, hr_only_client):
        r = hr_only_client.get("/collections/dashboard", follow_redirects=False)
        assert r.status_code == 403

    def test_collections_html_allowed_for_coll_ca(self, coll_ca_client):
        r = coll_ca_client.get("/collections/dashboard", follow_redirects=False)
        assert r.status_code == 200

    def test_customer_accounts_html_allowed_for_coll_ca(self, coll_ca_client):
        r = coll_ca_client.get("/customer-accounts/dashboard", follow_redirects=False)
        assert r.status_code == 200

    def test_hr_html_forbidden_for_coll_ca(self, coll_ca_client):
        r = coll_ca_client.get("/hr/dashboard", follow_redirects=False)
        assert r.status_code == 403

    def test_admin_accesses_all_html_modules(self, authed_client):
        for path in ["/dashboard", "/hr/dashboard", "/collections/dashboard",
                     "/customer-accounts/dashboard", "/data-quality/missing-contact"]:
            r = authed_client.get(path, follow_redirects=False)
            assert r.status_code == 200, f"Admin should access {path}, got {r.status_code}"

    def test_unauthenticated_html_gets_302_not_403(self):
        """Unauthenticated HTML requests get 302 to /login, not 403."""
        with TestClient(app, follow_redirects=False) as c:
            r = c.get("/hr/dashboard")
        assert r.status_code == 302
        assert "/login" in r.headers.get("location", "")
```

#### Section C — Sidebar Filtering

```python
class TestSidebarFiltering:
    def test_hr_only_sidebar_shows_hr_hides_crm(self, hr_only_client):
        r = hr_only_client.get("/hr/dashboard")
        assert r.status_code == 200
        body = r.text
        assert 'href="/hr/dashboard"' in body
        # CRM-specific sidebar link is hidden
        assert ">CRM<" not in body
        assert 'href="/dashboard"' not in body or "hr/dashboard" in body  # no CRM link

    def test_hr_only_sidebar_hides_collections_and_ca(self, hr_only_client):
        r = hr_only_client.get("/hr/dashboard")
        body = r.text
        assert 'href="/collections/dashboard"' not in body
        assert 'href="/customer-accounts/dashboard"' not in body

    def test_coll_ca_sidebar_shows_collections_and_ca(self, coll_ca_client):
        r = coll_ca_client.get("/collections/dashboard")
        body = r.text
        assert 'href="/collections/dashboard"' in body
        assert 'href="/customer-accounts/dashboard"' in body

    def test_coll_ca_sidebar_hides_hr(self, coll_ca_client):
        r = coll_ca_client.get("/collections/dashboard")
        assert 'href="/hr/dashboard"' not in r.text

    def test_admin_sidebar_shows_all_modules(self, authed_client):
        r = authed_client.get("/dashboard")
        assert r.status_code == 200
        body = r.text
        assert 'href="/hr/dashboard"' in body
        assert 'href="/collections/dashboard"' in body
        assert 'href="/customer-accounts/dashboard"' in body
```

#### Section D — Post-Login Landing

```python
class TestPostLoginLanding:
    def test_hr_only_lands_on_hr_dashboard(self):
        """hr_only: next=/dashboard (inaccessible) → redirected to /hr/dashboard."""
        with TestClient(app, follow_redirects=False) as c:
            r = c.post("/login",
                       data={"username": "hr_only", "password": "testpass", "next": "/dashboard"})
        assert r.status_code == 303
        assert r.headers["location"] == "/hr/dashboard"

    def test_coll_ca_lands_on_collections_dashboard(self):
        """coll_ca: no crm → first allowed module is collections."""
        with TestClient(app, follow_redirects=False) as c:
            r = c.post("/login",
                       data={"username": "coll_ca", "password": "testpass", "next": "/dashboard"})
        assert r.status_code == 303
        assert r.headers["location"] == "/collections/dashboard"

    def test_admin_lands_on_dashboard(self):
        """Admin (modules=['*']) → /dashboard (CRM is first in ordered list)."""
        with TestClient(app, follow_redirects=False) as c:
            r = c.post("/login",
                       data={"username": "testadmin", "password": "testpass", "next": "/dashboard"})
        assert r.status_code == 303
        assert r.headers["location"] == "/dashboard"

    def test_hr_only_with_valid_next_honoured(self):
        """hr_only explicitly navigated to /hr/dashboard → that next param is honoured."""
        with TestClient(app, follow_redirects=False) as c:
            r = c.post("/login",
                       data={"username": "hr_only", "password": "testpass", "next": "/hr/dashboard"})
        assert r.status_code == 303
        assert r.headers["location"] == "/hr/dashboard"

    def test_hr_only_with_inaccessible_next_falls_back(self):
        """hr_only with next=/collections/dashboard (denied) → falls back to /hr/dashboard."""
        with TestClient(app, follow_redirects=False) as c:
            r = c.post("/login",
                       data={"username": "hr_only", "password": "testpass",
                             "next": "/collections/dashboard"})
        assert r.status_code == 303
        assert r.headers["location"] == "/hr/dashboard"
```

#### Section E — No-Modules Case

```python
class TestNoModules:
    def test_no_modules_user_redirected_to_no_modules_page(self):
        with TestClient(app, follow_redirects=False) as c:
            r = c.post("/login",
                       data={"username": "no_modules", "password": "testpass",
                             "next": "/dashboard"})
        assert r.status_code == 303
        assert r.headers["location"] == "/no-modules"

    def test_no_modules_page_renders_html(self, no_modules_client):
        r = no_modules_client.get("/no-modules")
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")

    def test_no_modules_page_unauthenticated_redirects_to_login(self):
        with TestClient(app, follow_redirects=False) as c:
            r = c.get("/no-modules")
        assert r.status_code == 302
        assert "/login" in r.headers.get("location", "")

    def test_no_modules_user_gets_403_on_all_module_api_routes(self, no_modules_client):
        for path in [
            "/api/v1/hr/kpi/headcount",
            "/api/v1/collections/kpi/late-uncollected",
            "/api/v1/customer-accounts/kpi/total-receivables",
        ]:
            r = no_modules_client.get(path)
            assert r.status_code == 403, f"no_modules user should get 403 on {path}"
```

### 5.3 Unit Router Tests — Green-Keeping

**Root cause of the incompatibility:** After A3, `require_module_api("hr")` is added to the HR router's include_router call. Its inner `_guard` calls `request.app.state.user_repo.get_user("testadmin")`. In current unit tests, `app.state.user_repo` is never initialized (the TestClient is created WITHOUT the lifespan context manager `with TestClient(app) as c`). This causes `AttributeError`.

**Fix:** Change the `client` fixture in all 8 unit router test files to inject a mock `user_repo` into `app.state`. No lifespan required, no bcrypt, fast.

**New `client` fixture pattern (drop-in replacement):**

```python
from unittest.mock import MagicMock
from backend.auth.models import UserRecord
from backend.api.deps import get_current_user
from backend.main import app

_TESTADMIN_RECORD = UserRecord(
    username="testadmin",
    password_hash="",
    modules=["*"],        # wildcard — satisfies all module guards
    is_admin=True,
    is_active=True,
    created_at="2026-01-01T00:00:00",
    updated_at="2026-01-01T00:00:00",
)


@pytest.fixture
def client() -> TestClient:
    app.dependency_overrides[get_current_user] = lambda: "testadmin"
    mock_repo = MagicMock()
    mock_repo.get_user.return_value = _TESTADMIN_RECORD
    app.state.user_repo = mock_repo

    c = TestClient(app, raise_server_exceptions=True)
    yield c

    app.dependency_overrides.pop(get_current_user, None)
    if hasattr(app.state, "user_repo"):
        del app.state.user_repo
```

**Why this keeps all existing tests green:**
- `get_current_user` is overridden → returns "testadmin" (no session lookup)
- `require_module_api` calls `user_repo.get_user("testadmin")` → mock returns `_TESTADMIN_RECORD` with `modules=["*"]` → guard passes
- All 200/503/500 test assertions are unchanged; the module guard adds zero test failures for a `["*"]` user

**Existing `test_401_when_no_auth` tests are unaffected:** When no session is present, `get_current_user` raises 401 before `require_module_api._guard` is even invoked. The mock `user_repo` is never accessed in this path.

**Files requiring the fixture update (7 total):**

| File | Change |
|------|--------|
| `tests/unit/modules/hr/test_router_headcount.py` | Replace `client` fixture with mock_repo pattern |
| `tests/unit/modules/hr/test_router_payroll_risk.py` | Same |
| `tests/unit/modules/hr/test_router_department_cost.py` | Same |
| `tests/unit/modules/hr/test_router_department_staff.py` | Same |
| `tests/unit/modules/hr/test_router_employee_profile.py` | Same |
| `tests/unit/modules/hr/test_router_tenure.py` | Same |
| `tests/unit/modules/collections/test_routes.py` | Same |

`tests/unit/modules/customer_accounts/test_routes.py` does NOT need changes — it only contains `test_401_when_no_auth` with a plain `TestClient(app)` and no `dependency_overrides`. The 401 path raises before `require_module_api._guard` is ever invoked, so `user_repo` is never accessed.

**No other test files are modified.** Integration tests use `authed_client` (testadmin, modules=["*"]) — they satisfy all module guards automatically. Auth route unit tests are unaffected (they test /login and /logout which are public routes with no module guard).

---

## 6. Proposed AUTH_RBAC_DECISIONS.md Entries

```markdown
## A3 — RBAC Enforcement

### A3.D1 — require_module Design

Two dependency factories in `backend/api/deps.py`:
- `require_module_api(module_id: str)` — chains off `get_current_user` (which handles 401).
  Raises `HTTPException(403, detail={"code": "MODULE_ACCESS_DENIED", "module": module_id})`
  if `"*" not in user.modules and module_id not in user.modules`.
- `require_module_html(module_id: str)` — chains off `get_current_user_html` (which handles 302).
  Raises `HTTPException(403)` (no detail body; the global handler renders 403.html for browsers).

FastAPI's per-request dependency caching means `get_current_user` / `get_current_user_html` run
exactly once even when both the endpoint's own parameter and the module guard depend on them.
One additional `user_repo.get_user(username)` SQLite read per guarded request.

### A3.D2 — Gating Points

API routes: `include_router(router, dependencies=[Depends(require_module_api(module_id))])` in
`backend/api/v1/router.py` only. Zero endpoint body changes. CRM spans 6 files — all share a
single `_crm = [Depends(require_module_api("crm"))]` list. `/api/v1/metrics` and
`/api/v1/health/*` are intentionally NOT module-gated (authenticated-only).

HTML routes: `dependencies=[Depends(require_module_html(module_id))]` added to each
`@router.get(...)` decorator in `dashboard.py`. Five decorators; zero function body changes.

### A3.D3 — Global 403 Exception Handler

`@app.exception_handler(403)` in `backend/main.py`. Dispatches by Accept header:
- `Accept: text/html` present → renders `frontend/templates/403.html` (standalone, no base.html)
- Otherwise → returns `_error_response(request, 403, "MODULE_ACCESS_DENIED", ...)`

Does not intercept `ReadOnlyViolationError` responses — that handler returns a JSONResponse
directly (not via HTTPException), so it is unaffected.

### A3.D4 — Sidebar Filtering

`_base_ctx` in `dashboard.py` resolves `UserRecord` from `user_repo` and adds
`allowed_modules` (the raw `user.modules` list, e.g. `["hr"]` or `["*"]`) to context.
Both the desktop and mobile sidebars in `base.html` wrap each active module link in
`{% if 'module_id' in allowed_modules or '*' in allowed_modules %}`.
"Coming Soon" stub entries remain unconditionally visible.

### A3.D5 — Post-Login Landing

`login_submit` checks the `next` param against `_PATH_MODULE_MAP`. If the user can access
the `next` path → redirect there. Else → redirect to the user's first allowed module dashboard
per `_ORDERED_MODULE_DASHBOARDS` (order: crm → hr → collections → customer_accounts).
If no modules → redirect to `/no-modules`.

### A3.D6 — No-Modules and Forbidden Pages

`/no-modules` — new route in `auth.py`, protected by `get_current_user_html` (no module guard),
renders `no_modules.html`. Reached only when login lands a user with `modules=[]`.
`403.html` — standalone template. Rendered by the global 403 handler for browser requests.
Neither template extends `base.html` (avoids context dependency on `allowed_modules`).

### A3.D7 — Test Strategy

New file `tests/integration/test_rbac.py` with 5 test classes:
API 403/200 matrix (parametrized), HTML 403/200 matrix, sidebar content, post-login landing,
no-modules case.

Three restricted fixtures: `hr_only_client` (modules=["hr"]), `coll_ca_client`
(modules=["collections","customer_accounts"]), `no_modules_client` (modules=[]).

Unit router tests (8 files): replace `client` fixture to inject a mock `user_repo` into
`app.state` so `require_module_api._guard` can call `get_user("testadmin")` and receive a
`UserRecord(modules=["*"])`. No lifespan required. All existing assertions unchanged.
```

---

## 7. Risks + Open Questions for Khaled

### 7.1 Risks

| # | Risk | Severity | Mitigation |
|---|------|----------|------------|
| R1 | **Three `get_user` DB calls per HTML page load** — `_resolve_active_username` inside `get_current_user_html`, then the guard's `_guard` inside `require_module_html`, then `_base_ctx`. All three call `user_repo.get_user(username)`. Three SQLite reads per page. | LOW | Sub-millisecond locally. Acceptable for O(10) concurrent users. If it becomes a bottleneck in Phase B, store `UserRecord` in `request.state` once and reuse. |
| R2 | **Unit test `app.state.user_repo` teardown** — the mock_repo is set on `app.state` and deleted in fixture cleanup. If a test crashes before cleanup (e.g., unhandled exception in the test body), `app.state.user_repo` may persist into the next test that does NOT use the fixture. Tests that create their own raw `TestClient` (e.g., `test_401_when_no_auth`) would then find a stale mock_repo. | LOW | 401 path does not access `user_repo`. Stale mock_repo is harmless there. If a future test explicitly checks `user_repo` behavior, add `autouse` cleanup or use `try/finally`. |
| R3 | **`_seed_rbac_test_users` and the test DB wipe race** — `tests/conftest.py` wipes `data/test-users.db` at module import time (top-level code, before any fixture). The `SQLiteUserRepository(settings.USER_DB_PATH)` call in `_seed_rbac_test_users` will create the `users` table if missing (idempotent init). But the three restricted users must be present before `authed_client`/`hr_only_client` etc. open their TestClient contexts (which seed testadmin via lifespan). Session-scoped `autouse` on `_seed_rbac_test_users` ensures it runs before any module-scoped client fixture. Confirm pytest fixture ordering holds. | MEDIUM | Verify by running `pytest tests/integration/test_rbac.py -v` and checking that testadmin + restricted users all exist. Add an assertion to `_seed_rbac_test_users` that reads the users back if needed. |
| R4 | **`_user_can_access_path` and future routes** — the `_PATH_MODULE_MAP` in `auth.py` is a static dict. If a new HTML route is added under a new prefix (e.g., `/accounting/dashboard`) without updating the map, `_user_can_access_path` returns `True` (path not in map → allow). This could allow post-login landing on a route the user isn't supposed to access. | LOW | The guard at the route level (`require_module_html`) is the authoritative check. The `_user_can_access_path` function only determines the post-login redirect — landing on an inaccessible page just results in a 403, which is not a security breach. Document the map must be updated when new module routes are added. |
| R5 | **`@app.exception_handler(403)` scope** — the handler intercepts ALL `HTTPException(403)` regardless of origin. If any future code raises `HTTPException(403)` for a non-RBAC reason (e.g., rate limiting, input validation), browser requests would receive `403.html` with the generic "Access Denied" message. This may be confusing if the actual reason is rate limiting. | LOW | For A3 scope, the only source of `HTTPException(403)` is the module guards. Document that any non-RBAC 403 should include an `X-Reason` header or use a custom exception type so the handler can tailor the message. |

### 7.2 Open Questions for Khaled

**Q1 — Module priority order for post-login landing**
The plan uses `crm → hr → collections → customer_accounts` as the landing-page priority order (matching the sidebar order). A user with `modules=["hr", "collections"]` lands on `/hr/dashboard`. Is this the correct priority? Should collections come before hr (primary financial ops module)?

**Q2 — "Coming Soon" stubs visibility for restricted users**
The plan always shows Customer Service, Contracts, Accounting, and Project Mgmt stubs in the sidebar regardless of `allowed_modules`. Option A (current plan): always show — provides roadmap context. Option B: hide stubs for non-admin / non-wildcard users — cleaner sidebar but removes roadmap visibility. Which is preferred?

**Q3 — The 403 page "Go back" link**
The 403 page plan includes `<a href="javascript:history.back()">← Back</a>`. For a user who typed the URL directly (no history), this button does nothing. Alternative: omit the back link entirely and only show the Logout button, plus a "← Go to your modules" link that routes to `_first_allowed_dashboard`. This requires the 403 handler to resolve the session user — one extra DB call per 403 render. Tradeoff: slightly better UX vs extra complexity. Confirm which approach is preferred.

**Q4 — Module change takes effect on next request**
`user_repo.get_user(username)` is called on every request. If a user's `modules` list is changed via direct DB edit while they are logged in, the change takes effect on their next request — not their current one. This is the correct and intended behavior (mirrors `is_active` enforcement from A2.D1). Confirm this is acceptable.

**Q5 — `/no-modules` vs. login-form error**
The plan redirects `modules=[]` users to a dedicated `/no-modules` page after login. Alternative: show an inline error on the login form ("Your account has no modules assigned. Contact an administrator."), preventing login entirely for these users. The argument for the dedicated page: the user IS authenticated — blocking login conflates authentication failure with authorization failure. The argument for the form error: simpler implementation, no new route needed. Confirm the dedicated `/no-modules` page approach is preferred.

**Q6 — Sidebar `allowed_modules` context in `base.html` vs the `_base_ctx` DB read**
Currently `_base_ctx` does one extra `get_user` call to get `allowed_modules`. An alternative is to store the `UserRecord` in `request.state` inside `get_current_user_html` (or `require_module_html`) so that `_base_ctx` can read `request.state.user_record.modules` without another DB hit. This reduces calls from 3 to 2. Worth implementing in A3, or defer to Phase B when the overhead is proven to matter?

---

## 8. File Change Summary + Commit Structure

### New Files

| File | Purpose |
|------|---------|
| `frontend/templates/403.html` | Standalone styled 403 Forbidden page (no sidebar) |
| `frontend/templates/no_modules.html` | Standalone "No Modules Assigned" page (no sidebar) |
| `scripts/manage_users.py` | CLI: `add`, `list`, `set-modules`, `deactivate` commands |
| `tests/integration/test_rbac.py` | Full A3 integration test suite (5 test classes, ~35 assertions) |

### Modified Files

| File | Nature of Change |
|------|-----------------|
| `backend/api/deps.py` | Add `require_module_api(module_id)` and `require_module_html(module_id)` factory functions |
| `backend/api/v1/router.py` | Add `dependencies=[Depends(require_module_api(...))]` to all module `include_router` calls; add import |
| `backend/api/v1/endpoints/dashboard.py` | Add `dependencies=[Depends(require_module_html(...))]` to 5 `@router.get` decorators; add `allowed_modules` to `_base_ctx`; add `require_module_html` import |
| `backend/api/v1/endpoints/auth.py` | Add `_ORDERED_MODULE_DASHBOARDS`, `_PATH_MODULE_MAP`, `_user_can_access_path`, `_first_allowed_dashboard`; update `login_submit` redirect; add `/no-modules` route |
| `backend/main.py` | Add `@app.exception_handler(403)` with Accept-based dispatch; add `_err_templates` local instance |
| `frontend/templates/base.html` | Wrap active module links with `{% if ... in allowed_modules %}` conditionals in both desktop and mobile sidebars; add `{% set am = allowed_modules %}` alias |
| `frontend/translations/en.json` | Add 4 keys: `forbidden.heading`, `forbidden.body`, `no_modules.heading`, `no_modules.body` |
| `frontend/translations/ar.json` | Same 4 keys in Arabic |
| `tests/integration/conftest.py` | Add `_seed_rbac_test_users` (session autouse), `hr_only_client`, `coll_ca_client`, `no_modules_client` fixtures |
| `tests/unit/modules/hr/test_router_headcount.py` | Replace `client` fixture with mock_repo pattern |
| `tests/unit/modules/hr/test_router_payroll_risk.py` | Same |
| `tests/unit/modules/hr/test_router_department_cost.py` | Same |
| `tests/unit/modules/hr/test_router_department_staff.py` | Same |
| `tests/unit/modules/hr/test_router_employee_profile.py` | Same |
| `tests/unit/modules/hr/test_router_tenure.py` | Same |
| `tests/unit/modules/collections/test_routes.py` | Same |

**Files NOT touched:** All 26 API endpoint files (`collections.py`, `hr.py`, `customer_accounts.py`, `summary.py`, `followup.py`, `data_quality.py`, `dashboard_api.py`, `ai.py`, `chat.py`, `health.py`, `metrics_endpoint.py`). Their endpoint bodies, signatures, and dependencies are unchanged.

### Commit Structure

**Commit 1 — RBAC implementation + unit fixture updates (must be green together):**
`deps.py` (new factories) → `router.py` (include_router dependencies) → `dashboard.py` (decorator dependencies + `_base_ctx`) → `auth.py` (landing logic + `/no-modules` route) → `main.py` (403 handler) → `403.html` + `no_modules.html` → `base.html` (sidebar conditionals) → both translation files → `scripts/manage_users.py` (new CLI) → 7 unit router test fixtures (mock_repo pattern).

The unit fixture changes are part of Commit 1 — they must be green before the commit lands. Never split implementation from the unit fixes that make the implementation testable.

`feat(auth): A3 RBAC — require_module guards, sidebar filtering, module-aware landing, 403/no-modules pages, manage_users CLI; unit fixtures updated`

**Commit 2 — Integration test suite:**
`tests/integration/test_rbac.py` (new) → `tests/integration/conftest.py` (new fixtures: `_seed_rbac_test_users`, `hr_only_client`, `coll_ca_client`, `no_modules_client`).

Full integration suite must be green at end of Commit 2.

`test(auth): A3 RBAC integration matrix + restricted-user fixtures`
