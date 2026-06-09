# Auth + RBAC — A2 Implementation Plan
## Session-Cookie Login (replacing HTTP Basic)

**Plan date:** 2026-06-09  
**Author:** Claude Code  
**Status:** APPROVED + AMENDED — implementation in progress  
**Stage scope:** AUTHENTICATION ONLY — how a user logs in. A logged-in user still sees all modules. Per-module 403 / sidebar filtering is A3.

---

## Approved Amendments (override plan where they conflict)

**A1 — get_current_user and get_current_user_html return `str` (username), not `UserRecord`.**
Resolve UserRecord + check `is_active` internally; return `user.username`. Consequences:
- The 23 API endpoint files are NOT modified. Their `user: str` annotations stay correct and untouched.
- `_base_ctx` signature unchanged; `current_user` stays a `str`.
- Full `UserRecord` / `modules` access is introduced in A3.
- The ONLY change in `dashboard.py` is swapping its 5 HTML routes from `get_current_user` to `get_current_user_html`.

**A2 — Test DB = temp file.**
`USER_DB_PATH` set to `data/test-users.db` in `tests/conftest.py`. File is deleted at session start so seed always fires fresh. Real `data/users.db` is never touched by tests.

## Locked Q-Answers

| Q | Answer |
|---|--------|
| Q1 Staging Secure cookie | Keep `Secure=(ENVIRONMENT=="production")` only |
| Q2 Session lifetime | Keep `max_age=28800` (8 h) |
| Q3 Login rate limit | YES — `@limiter.limit("10/minute")` on POST /login |
| Q4 Test DB | Temp file `data/test-users.db` seeded from test env (amendment A2) |
| Q5 Mobile logout | YES — both desktop and mobile sidebars |
| Q6 api.js comment | YES — fix stale comment in A2 scope |

## Commit Structure

**Commit 1 (additive, non-breaking):** SESSION_SECRET config + validator, `.env.example`, `tests/conftest` (SESSION_SECRET + temp USER_DB_PATH), SessionMiddleware, new `auth.py` routes, `login.html` + i18n keys.

**Commit 2 (atomic swap):** Rewrite `deps.py` (session deps returning str), swap `dashboard.py` HTML routes, delete `core/security.py`, remove inline `/logout` from `main.py`, `base.html` logout control (both sidebars), `api.js` comment fix, AND all test/Postman rewrites — all in one commit so suite is never red.

---

## Table of Contents

1. [Files Read Before Planning](#1-files-read-before-planning)
2. [SessionMiddleware Wiring in main.py](#2-sessionmiddleware-wiring-in-mainpy)
3. [New and Changed Backend Files](#3-new-and-changed-backend-files)
4. [Two Dependencies: JSON-401 vs HTML-Redirect](#4-two-dependencies-json-401-vs-html-redirect)
5. [Route Signatures: /login and /logout](#5-route-signatures-login-and-logout)
6. [Login Template Plan](#6-login-template-plan)
7. [Frontend Changes — Login UI + Logout Control](#7-frontend-changes--login-ui--logout-control)
8. [SESSION_SECRET Config Plan](#8-session_secret-config-plan)
9. [Removal of verify_credentials / HTTPBasic](#9-removal-of-verify_credentials--httpbasic)
10. [Full Test-Rewrite Inventory](#10-full-test-rewrite-inventory)
11. [Proposed AUTH_RBAC_DECISIONS.md Entries](#11-proposed-auth_rbac_decisionsmd-entries)
12. [Risks + Open Questions for Khaled](#12-risks--open-questions-for-khaled)

---

## 1. Files Read Before Planning

| File | Purpose |
|------|---------|
| `docs/AUTH_RBAC_DISCOVERY.md` | Full auth inventory, endpoint matrix, test blast radius |
| `docs/AUTH_RBAC_DECISIONS.md` | A1 decisions (SQLite, schema, bcrypt, seed, Protocol) |
| `backend/core/security.py` | `verify_credentials` — to be retired in A2 |
| `backend/api/deps.py` | Current `get_current_user` (HTTPBasic) — to be replaced |
| `backend/main.py` | Middleware stack, lifespan (user_repo already wired), current `/logout` stub |
| `backend/core/config.py` | `Settings` class — where `SESSION_SECRET` is added |
| `backend/api/v1/endpoints/dashboard.py` | All 5 HTML routes and `_base_ctx` — type change needed |
| `backend/auth/models.py` | `UserRecord` dataclass |
| `backend/auth/repository.py` | `UserRepository` Protocol + `SQLiteUserRepository` |
| `backend/auth/seed.py` | Idempotent seed from env vars |
| `frontend/static/js/api.js` | `credentials: 'include'` already set — confirmed no changes needed |
| `frontend/templates/base.html` | Sidebar + topbar structure — logout control target |
| `frontend/translations/en.json` | Translation keys — "Logout" already present |
| `tests/conftest.py` | Root conftest defaults — `SESSION_SECRET` must be added |
| `tests/e2e/conftest.py` | `AUTH = ("admin", "password")` + `auth_headers` fixture |
| `tests/integration/test_api_v1.py` | Representative integration test with `_AUTH` pattern |
| `tests/integration/test_health.py` | Representative integration test with `_AUTH` pattern |
| `tests/unit/core/test_security.py` | Tests `verify_credentials` — to be replaced |
| `tests/unit/modules/hr/test_router_headcount.py` | Representative unit router test with `_AUTH` |
| `.env.example` | To receive `SESSION_SECRET` placeholder |

---

## 2. SessionMiddleware Wiring in main.py

### 2.1 Import

Add to `backend/main.py` imports:

```python
from starlette.middleware.sessions import SessionMiddleware
```

### 2.2 Middleware Registration

Insert immediately after the existing `CORSMiddleware` block (before the `@app.middleware("http")` decorators):

```python
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SESSION_SECRET or "dev-insecure-change-me-in-env",
    max_age=settings.SESSION_COOKIE_MAX_AGE,
    https_only=(settings.ENVIRONMENT == "production"),
    same_site="lax",
)
```

**Stack ordering note:** `add_middleware` prepends to the stack (last-added = outermost). The current stack from outermost to innermost is:

```
request_id_middleware (@app.middleware — added last, runs first)
security_headers_middleware (@app.middleware)
CORSMiddleware (add_middleware — added first among add_middleware calls)
Route handlers
```

Inserting `SessionMiddleware` *after* the `CORSMiddleware` call (as a second `add_middleware` call) makes it wrap CORS, ensuring sessions are available to all route handlers. The resulting stack:

```
request_id_middleware (outermost)
security_headers_middleware
SessionMiddleware  ← new
CORSMiddleware
Route handlers (innermost)
```

This is correct: CORS preflight OPTIONS are handled by CORSMiddleware before session parsing even runs; all downstream handlers get `request.session` populated by SessionMiddleware.

### 2.3 Remove Inline /logout

The current inline `/logout` stub in `main.py` (lines 283–290) issues `401 + WWW-Authenticate: Basic`. **Delete it entirely.** The real `/logout` will be registered via the new `auth_router` (see §3).

### 2.4 Include auth_router

Add after the existing `include_router` calls:

```python
from backend.api.v1.endpoints.auth import router as auth_router
app.include_router(auth_router)   # no prefix — HTML routes at /login, /logout
```

---

## 3. New and Changed Backend Files

### 3.1 NEW: `backend/api/v1/endpoints/auth.py`

Owns the three auth HTML routes: `GET /login`, `POST /login`, `GET /logout`.

Responsibilities:
- Render `frontend/templates/login.html` with i18n context
- Authenticate via `user_repo.verify_password(username, password)` + explicit `user.is_active` check
- Write/clear `request.session["username"]`
- Handle the `?next=` redirect parameter (sanitized — see §5)

This file does **not** touch `Depends(get_current_user)` — the login/logout routes are intentionally public (no auth dependency).

### 3.2 MODIFIED: `backend/api/deps.py`

**Remove:**
- `from fastapi.security import HTTPBasic, HTTPBasicCredentials`
- `from backend.core.security import verify_credentials`
- `_http_basic = HTTPBasic()`
- Entire current `get_current_user` body

**Add:**
- `_get_user_from_session(request: Request) -> UserRecord | None` — private helper
- `get_current_user(request: Request) -> UserRecord` — JSON API dependency (raises 401)
- `get_current_user_html(request: Request) -> UserRecord` — HTML dependency (raises 302)

Full signatures described in §4.

### 3.3 MODIFIED: `backend/core/config.py`

Add `SESSION_SECRET` and `SESSION_COOKIE_MAX_AGE` fields plus a fail-fast validator. See §8.

### 3.4 MODIFIED: `backend/core/security.py`

**Delete the entire file.** Its sole function `verify_credentials` (single-user plaintext compare) is replaced by `UserRepository.verify_password` (bcrypt). No new content belongs here in A2; if A3 adds security utilities, the file can be recreated at that point.

Downstream: `backend/api/deps.py` currently imports from it — that import is removed as part of the deps.py rewrite.

### 3.5 MODIFIED: `backend/api/v1/endpoints/dashboard.py`

Two targeted changes only:

**a) Import type:** add `from backend.auth.models import UserRecord`

**b) `_base_ctx` signature and body:**

```python
# Before
def _base_ctx(request: Request, user: str) -> dict:
    ...
    "current_user": user,
    "user_display_name": _extract_first_name(user),

# After
def _base_ctx(request: Request, user: UserRecord) -> dict:
    ...
    "current_user": user.username,           # still a str for template {{ current_user[0] }}
    "user_display_name": _extract_first_name(user.username),
```

**c) All five HTML route signatures:** change `user: str = Depends(get_current_user)` to `user: UserRecord = Depends(get_current_user_html)`.

```python
# Before
user: str = Depends(get_current_user)

# After
user: UserRecord = Depends(get_current_user_html)
```

This is the only functional change to these routes: they now redirect to `/login` instead of returning 401 when unauthenticated.

### 3.6 MODIFIED: All API endpoint files that use `Depends(get_current_user)`

These files need only a **type annotation update** — no functional change. `user: str` becomes `user: UserRecord`. The guard behaviour (401 on no session) is unchanged.

Affected files:
- `backend/api/v1/endpoints/health.py`
- `backend/api/v1/endpoints/summary.py`
- `backend/api/v1/endpoints/followup.py`
- `backend/api/v1/endpoints/data_quality.py`
- `backend/api/v1/endpoints/metrics_endpoint.py`
- `backend/api/v1/endpoints/dashboard_api.py`
- `backend/api/v1/endpoints/ai.py`
- `backend/api/v1/endpoints/chat.py`
- `backend/api/v1/endpoints/hr.py` (the 2 drilldown endpoints + the 4 KPI endpoints added by the hotfix)
- `backend/api/v1/endpoints/collections.py` (all 13 endpoints added by the hotfix)
- `backend/api/v1/endpoints/customer_accounts.py` (all 7 endpoints added by the hotfix)

In every file: add `from backend.auth.models import UserRecord` and change the `user: str` annotation to `user: UserRecord`. No other edits.

---

## 4. Two Dependencies: JSON-401 vs HTML-Redirect

### 4.1 Full Implementation in `backend/api/deps.py`

```python
from fastapi import Depends, HTTPException, Request, status
from backend.auth.models import UserRecord
from backend.modules.crm.service import CrmService


def _get_user_from_session(request: Request) -> UserRecord | None:
    username = request.session.get("username")
    if not username:
        return None
    user_repo = request.app.state.user_repo
    return user_repo.get_user(username)   # None if user was deleted after login


def get_current_user(request: Request) -> UserRecord:
    """JSON API routes: 401 if no valid session or user is inactive."""
    user = _get_user_from_session(request)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    return user


def get_current_user_html(request: Request) -> UserRecord:
    """HTML page routes: 302 to /login?next=<path> if no valid session."""
    user = _get_user_from_session(request)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_302_FOUND,
            headers={"Location": f"/login?next={request.url.path}"},
        )
    return user


def get_crm_service(request: Request) -> CrmService:
    return request.app.state.crm_service
```

**Notes:**
- `HTTPException(302, headers={"Location": ...})` works correctly: FastAPI's default exception handler passes the `headers` dict through to the `Response`, producing a valid HTTP 302 with a `Location` header.
- `request.url.path` (just the path, no query string) is used for the `next` parameter — simple, safe, avoids encoding issues.
- `is_active` is checked on every request so deactivation takes effect immediately without session invalidation (Decision 1).
- The `WWW-Authenticate` header is intentionally absent — no longer issuing a Basic challenge.

### 4.2 Which Routes Use Which Dependency

| Route file | Dependency | Behaviour when unauthenticated |
|------------|-----------|-------------------------------|
| `dashboard.py` — all 5 HTML routes | `get_current_user_html` | 302 → `/login?next=<path>` |
| `health.py` | `get_current_user` | 401 JSON |
| `summary.py` | `get_current_user` | 401 JSON |
| `followup.py` | `get_current_user` | 401 JSON |
| `data_quality.py` | `get_current_user` | 401 JSON |
| `metrics_endpoint.py` | `get_current_user` | 401 JSON |
| `dashboard_api.py` | `get_current_user` | 401 JSON |
| `ai.py` | `get_current_user` | 401 JSON |
| `chat.py` | `get_current_user` | 401 JSON |
| `hr.py` (all 6 endpoints) | `get_current_user` | 401 JSON |
| `collections.py` (all 13) | `get_current_user` | 401 JSON |
| `customer_accounts.py` (all 7) | `get_current_user` | 401 JSON |
| `/health` (public liveness) | none | always 200 |
| `/login`, `/logout` | none | public routes |

**Hotfix 401 tests remain valid:** the hotfix (`bdadb46`) tests that unprotected endpoints return 401. After A2, those same endpoints still return 401 for unauthenticated requests — the mechanism changes (session cookie instead of Basic) but the observable behaviour (401) does not change.

---

## 5. Route Signatures: /login and /logout

### 5.1 GET /login

```python
@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page(
    request: Request,
    next: str = Query(default="/dashboard"),
    error: str = Query(default=""),
) -> HTMLResponse:
    lang = detect_lang(dict(request.cookies), request.headers.get("accept-language", ""))
    ctx = {
        "request": request,
        "next": _sanitize_next(next),
        "error": error,
        "lang": lang,
        "is_rtl": lang == "ar",
        "_t": make_translator(lang),
    }
    return templates.TemplateResponse(request, "login.html", ctx)
```

**Note:** If the user already has a valid session, a redirect to `/dashboard` before rendering is a nice-to-have but not required in A2.

### 5.2 POST /login

```python
@router.post("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form(default="/dashboard"),
) -> Response:
    user_repo = request.app.state.user_repo
    user = user_repo.get_user(username)

    # Check password hash first (constant-time via bcrypt.checkpw)
    # Then check is_active to give same-latency response regardless of cause
    authed = user_repo.verify_password(username, password) if user else False
    active = user.is_active if user else False

    if not authed or not active:
        error_key = "login.error.inactive" if (authed and not active) else "login.error.invalid"
        lang = detect_lang(dict(request.cookies), request.headers.get("accept-language", ""))
        ctx = {
            "request": request,
            "next": _sanitize_next(next),
            "error": error_key,
            "lang": lang,
            "is_rtl": lang == "ar",
            "_t": make_translator(lang),
        }
        return templates.TemplateResponse(request, "login.html", ctx, status_code=401)

    request.session["username"] = username
    return RedirectResponse(url=_sanitize_next(next), status_code=303)
```

**`_sanitize_next` helper** (private, same file):

```python
def _sanitize_next(next_url: str) -> str:
    """Allow only relative paths starting with /. Reject open-redirect attempts."""
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return "/dashboard"
```

**Security note:** `POST /login` uses `status_code=303 See Other` (not 302) for the redirect after success. This is the correct HTTP idiom for POST→redirect to prevent form resubmission on back-navigation.

**`verify_password` when user is None:** The repo's `verify_password` returns `False` if user is not found (no exception). The `if user else False` guard prevents the bcrypt call for non-existent users — this is optional since the repo already handles it, but makes the intent explicit.

### 5.3 GET /logout

```python
@router.get("/logout", include_in_schema=False)
def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)
```

**Note:** `session.clear()` removes the `"username"` key and all other session data (future-safe). The signed cookie is replaced by the middleware with an empty session on the next response. The browser discards it when `max_age` expires; `session.clear()` effectively expires it immediately server-side.

---

## 6. Login Template Plan

### 6.1 File

`frontend/templates/login.html` — **standalone page, does NOT extend base.html** (no sidebar, no topbar, no chat drawer).

### 6.2 Structure

```
login.html
├── <head>  — same CSS links as base.html (app.css, fonts.css, favicon)
│            — same theme-flash-prevention script (localStorage check)
├── <body>
│   └── centered card (max-w-sm, mx-auto)
│       ├── LaVerde logo mark (primary-600 rounded square + SVG icon)
│       ├── "LaVerde ERP AI Engine" title
│       ├── Subtitle: _t("login.subtitle")  →  "Sign in to your account"
│       ├── [if error] error banner (_t(error) key resolved server-side)
│       ├── <form action="/login" method="POST">
│       │   ├── <input type="hidden" name="next" value="{{ next }}">
│       │   ├── Username field  (label: _t("login.username_label"))
│       │   ├── Password field  (label: _t("login.password_label"))
│       │   └── Submit button   (_t("login.submit"))
│       └── Language toggle (EN / AR — simple `<a>` links that set `lang` cookie)
```

### 6.3 Aesthetic Requirements

- Tailwind dark-mode aware (`dark:bg-neutral-950` body, `dark:bg-neutral-900` card)
- Same border-radius, shadow, and color tokens as existing cards (`rounded-2xl`, `shadow-sm`)
- Primary-600 button (matches existing `btn btn-primary`)
- RTL-safe: if `is_rtl`, `dir="rtl"` on `<html>`, text inputs reverse, label alignment flips via Tailwind `rtl:` variants
- No Alpine.js required — plain HTML form (no JavaScript needed for login)

### 6.4 i18n Keys to Add

Translation files: `frontend/translations/en.json` and `frontend/translations/ar.json`.

| Key | English | Arabic |
|-----|---------|--------|
| `login.title` | `Sign In` | `تسجيل الدخول` |
| `login.subtitle` | `Sign in to your account` | `سجّل دخولك إلى حسابك` |
| `login.username_label` | `Username` | `اسم المستخدم` |
| `login.password_label` | `Password` | `كلمة المرور` |
| `login.submit` | `Sign In` | `دخول` |
| `login.error.invalid` | `Invalid username or password.` | `اسم المستخدم أو كلمة المرور غير صحيحة.` |
| `login.error.inactive` | `Your account is deactivated. Contact an administrator.` | `تم تعطيل حسابك. تواصل مع المسؤول.` |

**Already present (no change needed):** `"Logout"` key exists in both translation files.

---

## 7. Frontend Changes — Login UI + Logout Control

### 7.1 api.js — NO CHANGES

`credentials: 'include'` (line 27) already instructs the browser to send cookies with every `fetch()`. When the session cookie is set after login, it will be sent automatically on every `crmApi.get()` call. The comment `// sends Basic Auth cookies` is stale (it never actually described Basic Auth correctly — see discovery doc §4.3) and should be updated to `// sends session cookie`, but this is a cosmetic fix that can be bundled with the template work.

### 7.2 Logout Control in base.html

**Location:** The sidebar footer (currently lines ~319–321):

```html
<!-- Current -->
<div class="p-3 border-t border-neutral-100 dark:border-neutral-800 text-sm text-neutral-500">
  {{ current_user }}
</div>
```

**Replacement — user identity + logout button:**

```html
<div class="p-3 border-t border-neutral-100 dark:border-neutral-800 shrink-0">
  <div class="flex items-center justify-between gap-2">
    <!-- Avatar + display name -->
    <div class="flex items-center gap-2 min-w-0">
      <div class="w-7 h-7 rounded-full bg-primary-100 dark:bg-primary-900/40
                  flex items-center justify-center shrink-0
                  text-primary-700 dark:text-primary-300 text-xs font-semibold uppercase">
        {{ current_user[0] }}
      </div>
      <span x-show="!sidebarCollapsed"
            class="text-sm text-neutral-700 dark:text-neutral-300 truncate">
        {{ user_display_name }}
      </span>
    </div>
    <!-- Logout -->
    <a x-show="!sidebarCollapsed"
       href="/logout"
       class="btn-ghost btn btn-icon shrink-0"
       title="{{ _t('Logout') }}"
       aria-label="{{ _t('Logout') }}">
      <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
              d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"/>
      </svg>
    </a>
  </div>
</div>
```

**Note:** The mobile sidebar (`x-show="mobileOpen"`) has a duplicate nav structure in base.html (the second simpler sidebar from ~line 280). The same sidebar-footer change must be applied to that duplicate block too.

### 7.3 Context variables unchanged

`_base_ctx` already passes `current_user` (str) and `user_display_name` (str). After A2, these are derived from `user.username` instead of the raw string passed in. Template variables are identical — zero template logic changes beyond adding the logout button.

---

## 8. SESSION_SECRET Config Plan

### 8.1 New Fields in `backend/core/config.py`

```python
# ── Session ────────────────────────────────────────────────────────────────────
SESSION_SECRET: str = ""
SESSION_COOKIE_MAX_AGE: int = 28800   # 8 hours in seconds
```

### 8.2 Fail-Fast Validator

Add a second `@model_validator(mode="after")` method (alongside the existing `validate_ai_config`):

```python
@model_validator(mode="after")
def validate_session_secret(self) -> "Settings":
    if self.ENVIRONMENT == "production":
        if not self.SESSION_SECRET:
            raise ValueError(
                "SESSION_SECRET is required in production. "
                "Generate with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        if len(self.SESSION_SECRET) < 32:
            raise ValueError(
                "SESSION_SECRET must be at least 32 characters in production."
            )
    elif not self.SESSION_SECRET:
        logger.warning(
            "SESSION_SECRET is not set — sessions will use an insecure dev default. "
            "Set SESSION_SECRET in .env before deploying."
        )
    return self
```

**Effect:** In `development` / `staging` with `SESSION_SECRET=""`, the app starts with a warning (not an error). In `production`, missing or short secret causes the process to refuse to start at import time.

**In `main.py`:** The `SessionMiddleware` call uses `settings.SESSION_SECRET or "dev-insecure-change-me"` so that in dev/test the middleware gets a non-empty string even if the field is blank.

### 8.3 `.env.example` Update

Add to the `# ─── Authentication` section:

```env
# Session secret — REQUIRED in production. Must be >= 32 random characters.
# Generate: python -c "import secrets; print(secrets.token_hex(32))"
# Never commit the real value.
SESSION_SECRET=
```

Update the existing comment block header from `HTTP Basic Auth credentials` to `Authentication` (since Basic is now only the seed source).

### 8.4 Tests — `tests/conftest.py`

Add to the `_defaults` dict:

```python
"SESSION_SECRET": "test-session-secret-exactly-32ch!",
```

This satisfies the `len >= 32` requirement when `ENVIRONMENT=development` is already set in test defaults (the production validator does not run in development).

---

## 9. Removal of verify_credentials / HTTPBasic

### 9.1 `backend/core/security.py`

**Delete the entire file.** It contains only `verify_credentials`, which compares plaintext credentials against env-var values. After A2, authentication goes through `UserRepository.verify_password` (bcrypt). There is no other content to preserve.

### 9.2 Import Cleanup in `backend/api/deps.py`

Remove:
```python
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from backend.core.security import verify_credentials
_http_basic = HTTPBasic()
```

These are fully replaced by the session-based implementation (§4.1).

### 9.3 `BASIC_AUTH_USERNAME` / `BASIC_AUTH_PASSWORD` in config.py

**Keep both fields.** They are the seed source for the admin user (`backend/auth/seed.py` reads them). Their semantic meaning changes: they are no longer runtime credentials for HTTP Basic; they are bootstrap credentials used exactly once at first startup. The `.env.example` comment should be updated to reflect this.

### 9.4 `tests/unit/core/test_security.py`

**Delete this file.** Its five test functions (`test_correct_credentials`, `test_wrong_password`, `test_wrong_username`, `test_both_wrong`, `test_empty_credentials`) all test `verify_credentials` directly. That function is being deleted.

**Replace with:** `tests/unit/auth/test_auth_routes.py` — unit tests for the new `auth.py` routes (GET /login renders form, POST /login success, POST /login wrong password, POST /login inactive user, GET /logout clears session). These tests use `TestClient` with `app.dependency_overrides` where needed and a `:memory:` SQLite DB.

Note: `tests/unit/auth/test_user_store.py` already exists (from A1) and covers `UserRepository`. The new `test_auth_routes.py` focuses on the route layer, not the repository.

---

## 10. Full Test-Rewrite Inventory

### 10.1 Shared `authed_client` Fixture Design

A single fixture, placed in `tests/integration/conftest.py` (new file), provides a `TestClient` that has already completed the `POST /login` flow and carries a valid session cookie.

```python
# tests/integration/conftest.py
import pytest
from fastapi.testclient import TestClient
from backend.main import app

@pytest.fixture(scope="module")
def authed_client() -> TestClient:
    """TestClient with a live session cookie from POST /login."""
    with TestClient(app, follow_redirects=False) as client:
        # The lifespan runs here: user_repo is initialised and seed fires.
        # BASIC_AUTH_USERNAME=testadmin, BASIC_AUTH_PASSWORD=testpass
        # are set by tests/conftest.py, so the seed creates that user.
        resp = client.post(
            "/login",
            data={"username": "testadmin", "password": "testpass", "next": "/dashboard"},
        )
        assert resp.status_code == 303, f"Login failed: {resp.status_code}"
        # session cookie is now in client.cookies — carried on all subsequent requests
        yield client
```

**Why `scope="module"`:** The login POST is ~100ms (bcrypt). One login per test module is fast enough and avoids repeated cost. Session state is server-side stateless (the cookie is self-contained), so there is no cross-test contamination from sharing the client.

**Why `with TestClient(...) as client`:** The context manager runs the lifespan (`startup` event), which calls `seed_initial_user`. Without the context manager, `app.state.user_repo` is not initialised and the session lookup fails.

**Why `follow_redirects=False`:** The `POST /login` returns `303 See Other`. With `follow_redirects=True` (default), the client would follow into `/dashboard` which requires a running Odoo. Stopping at the 303 lets us assert login success and start using the cookie.

### 10.2 Integration Tests — Files and Changes Required

| File | Current auth | Change |
|------|-------------|--------|
| `tests/integration/test_api_v1.py` | `auth=_AUTH` on all `TestClient` calls | Replace `_AUTH` fixture with `authed_client`; remove `_AUTH` constant; replace `TestClient(app)` with `authed_client` |
| `tests/integration/test_health.py` | `auth=_AUTH` | Same pattern |
| `tests/integration/test_smoke.py` | `_AUTH` | Same pattern |
| `tests/integration/test_ai_endpoints.py` | `_AUTH` | Same pattern |
| `tests/integration/test_chat_endpoint.py` | `_AUTH` | Same pattern |
| `tests/integration/test_exception_handlers.py` | `_AUTH` | Same pattern |
| `tests/integration/test_concurrent_summary.py` | `_AUTH` | Same pattern |
| `tests/integration/test_pagination.py` | `_AUTH` | Same pattern |
| `tests/integration/test_locale_ai_endpoints.py` | `_AUTH` | Same pattern |
| `tests/integration/test_ai_budget_flow.py` | `_AUTH` | Same pattern |
| `tests/integration/test_ai_cache_flow.py` | `_AUTH` | Same pattern |

**Pattern for each file:** Remove the `_AUTH = (...)` constant and the `auth=_AUTH` kwarg from `TestClient(...)` calls. Replace with `authed_client` fixture injection. Example:

```python
# Before
_AUTH = ("testadmin", "testpass")

@pytest.fixture
def client() -> TestClient:
    return TestClient(app)

def test_foo(client):
    resp = client.get("/api/v1/summary", auth=_AUTH)

# After
def test_foo(authed_client):
    resp = authed_client.get("/api/v1/summary")
```

**Exception — tests that explicitly test unauthenticated 401 behavior:** These tests must NOT use `authed_client`. They use a plain `TestClient(app)` with no auth. Example (from test_exception_handlers.py or security tests):

```python
def test_unauthenticated_returns_401():
    with TestClient(app, follow_redirects=False) as client:
        resp = client.get("/api/v1/summary")
    assert resp.status_code == 401
```

This continues to work after A2 because `get_current_user` still raises 401 for API routes with no session.

### 10.3 Unit Router Tests — Files and Changes Required

These tests use `TestClient(app)` with `_AUTH` to hit router-level endpoints. After A2, the `_AUTH` pattern breaks because HTTPBasic is removed.

**Recommended approach for unit router tests:** `app.dependency_overrides` to inject a mock `UserRecord`. This is better than the login fixture for unit tests because it is faster (no bcrypt), more isolated, and does not require a seeded DB.

```python
# tests/unit/modules/hr/conftest.py  (new shared fixture file)
import pytest
from fastapi.testclient import TestClient
from backend.auth.models import UserRecord
from backend.api.deps import get_current_user
from backend.main import app

_MOCK_USER = UserRecord(
    username="testadmin",
    password_hash="",
    modules=["*"],
    is_admin=True,
    is_active=True,
    created_at="2026-01-01T00:00:00+00:00",
    updated_at="2026-01-01T00:00:00+00:00",
)

@pytest.fixture
def client():
    app.dependency_overrides[get_current_user] = lambda: _MOCK_USER
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()
```

This fixture replaces the per-file `client()` fixture (which currently returns `TestClient(app)` without auth). The `_AUTH` constant is deleted from each file.

**Files requiring this change:**

| File | Change |
|------|--------|
| `tests/unit/modules/hr/test_router_headcount.py` | Delete `_AUTH`; replace `client` fixture with override pattern |
| `tests/unit/modules/hr/test_router_payroll_risk.py` | Same |
| `tests/unit/modules/hr/test_router_department_cost.py` | Same |
| `tests/unit/modules/hr/test_router_department_staff.py` | Same |
| `tests/unit/modules/hr/test_router_employee_profile.py` | Same |
| `tests/unit/modules/hr/test_router_tenure.py` | Same |
| `tests/unit/modules/collections/test_routes.py` | Same |
| `tests/unit/modules/customer_accounts/test_routes.py` | Same |

**Note:** The `conftest.py` fixture approach is preferred over duplicating the override boilerplate in every test file. Create `tests/unit/modules/hr/conftest.py`, `tests/unit/modules/collections/conftest.py`, and `tests/unit/modules/customer_accounts/conftest.py` with the same pattern.

### 10.4 Unit Core Tests

| File | Change |
|------|--------|
| `tests/unit/core/test_security.py` | **Delete entirely** (see §9.4) |
| `tests/unit/auth/test_auth_routes.py` | **New file** — route-level tests for GET/POST /login and GET /logout |

`tests/unit/core/test_config.py` may need a new test case for `SESSION_SECRET` validation (fail-fast in production, warning in dev). Add — do not replace existing tests.

### 10.5 E2E Tests (Playwright)

All four Playwright test files currently inject `Authorization: Basic` headers. This mechanism stops working after A2.

| File | Change |
|------|--------|
| `tests/e2e/conftest.py` | Replace `AUTH` + `auth_headers` fixture with a `login_page` fixture that navigates to `/login`, fills the form, and submits |
| `tests/e2e/test_dashboard.py` | Replace `page.set_extra_http_headers(auth_headers)` with `login_page` fixture call |
| `tests/e2e/test_ai_dashboard_section.py` | Same |
| `tests/e2e/test_phase3_dropdowns.py` | Same |

**New `conftest.py` pattern:**

```python
BASE_URL = "http://localhost:8000"

@pytest.fixture(scope="session")
def base_url() -> str:
    return BASE_URL

@pytest.fixture(scope="session")
def logged_in_page(playwright):
    browser = playwright.chromium.launch()
    context = browser.new_context()
    page = context.new_page()
    page.goto(f"{BASE_URL}/login")
    page.fill("input[name=username]", "admin")
    page.fill("input[name=password]", "password")
    page.click("button[type=submit]")
    page.wait_for_url(f"{BASE_URL}/dashboard")
    yield page
    context.close()
    browser.close()
```

**Note on asyncio event-loop pollution (Risk R5):** Use synchronous Playwright API (not `async def`) to avoid compounding the known event-loop issue in the test suite.

### 10.6 Postman Collection

File: `tests/postman/CRM-AI-Engine.postman_collection.json`

**Changes:**

1. **Collection-level auth block:** Change from `"type": "basic"` to `"type": "noauth"`.

2. **Collection pre-request script:** Add a script that checks if `pm.collectionVariables.get("session_cookie")` is already set; if not, fires a synchronous `pm.sendRequest` to `POST {{base_url}}/login` with `username={{username}}&password={{password}}` (form-urlencoded), extracts the `Set-Cookie` header, and stores the session cookie value in `pm.collectionVariables`.

3. **All authenticated request items:** Add a header `Cookie: session={{session_cookie}}` (using the stored collection variable) OR configure the Postman environment to use a cookie jar with the login cookie.

4. **Environment variables:** Keep `username` and `password` variables. Add `base_url` (e.g. `http://localhost:8000`) if not already present.

5. **"Security" folder tests:** The two requests that test `GET /api/v1/summary → 401` must be modified to send NO `Cookie` header (removing any inherited session cookie). They still test the correct behaviour: unauthenticated API requests return 401.

### 10.7 Root `tests/conftest.py`

Add `SESSION_SECRET` to `_defaults`:

```python
"SESSION_SECRET": "test-session-secret-exactly-32ch!",
```

Remove `BASIC_AUTH_USERNAME` and `BASIC_AUTH_PASSWORD` from `_defaults`? **No — keep them.** The seed logic (`seed.py`) still reads these to create the initial user in the test DB. They are needed for the `authed_client` login to work (the seeded user has username=testadmin, password=testpass). Only their role description changes (seed source, not runtime auth).

### 10.8 Green-Keeping Order of Operations

Execute in this exact order to ensure the suite never goes red mid-stage:

1. **Add `SESSION_SECRET` to conftest defaults and config.py** (non-breaking — old Basic auth still in place).
2. **Create `backend/api/v1/endpoints/auth.py`** (new file — no existing routes clash).
3. **Create `frontend/templates/login.html`** (new file — no existing template affected).
4. **Add i18n keys** to `frontend/translations/en.json` and `ar.json` (additive — no existing key changed).
5. **Add `SESSION_SECRET` + `SESSION_COOKIE_MAX_AGE` to `config.py`** with fail-fast validator.
6. **Add `SessionMiddleware` to `main.py`** and include `auth_router` — still non-breaking because `get_current_user` still uses Basic auth at this point.
7. **Rewrite `backend/api/deps.py`**: replace `get_current_user` with session-based version, add `get_current_user_html`. **This is the breaking change.** All tests using `auth=_AUTH` break here.
8. **Immediately (same commit):** Update `dashboard.py` routes to `get_current_user_html`; update all API endpoint type annotations from `str` to `UserRecord`.
9. **Delete `backend/core/security.py`** and its import in `deps.py`.
10. **Remove inline `/logout` from `main.py`**.
11. **Add logout control to `frontend/templates/base.html`**.
12. **Add `SESSION_SECRET` to `.env.example`**.
13. **Rewrite tests:** integration conftest, all integration test files, unit router conftest files, delete `test_security.py`, add `test_auth_routes.py`, rewrite e2e conftest and test files, update Postman collection.
14. **Run full test suite** — confirm green before closing the stage.

**Key insight:** Steps 7–12 should be a single atomic commit so the suite is never broken between them. Steps 1–6 can be separate commits (they are non-breaking). Step 13 is the final commit.

---

## 11. Proposed AUTH_RBAC_DECISIONS.md Entries

```markdown
## A2 — Session-Cookie Authentication

### A2.D1 — Session Middleware: Starlette SessionMiddleware

Starlette's built-in `SessionMiddleware` chosen (already a dependency via FastAPI).
- Signed cookie using `itsdangerous` (Starlette's built-in); no server-side session store needed.
- Session payload: `{"username": <str>}` only. Full `UserRecord` resolved from `user_repo`
  on every authenticated request — ensures deactivation takes effect without session invalidation.
- Placement: after `CORSMiddleware` in the `add_middleware` call order (wraps CORS, runs before
  route handlers; CORS still handles OPTIONS without touching session state).

### A2.D2 — Cookie Flags

`HttpOnly=True` (Starlette default), `SameSite=Lax`, `Secure=True` iff `ENVIRONMENT=="production"`.
`max_age=28800` (8 hours) — one work session.
`SameSite=Lax` chosen over `Strict` to allow link-following from external pages (intranet links)
without forcing re-login. CSRF risk on non-navigation POSTs is mitigated by SameSite=Lax itself
(subresource requests from cross-origin don't carry the cookie).

### A2.D3 — Dual Unauthenticated Behaviour

Two separate FastAPI dependencies:
- `get_current_user(request)` → `UserRecord` or raises `HTTP 401`. Used on all `/api/v1/*` routes.
  Keeps the hotfix-era 401 guarantees intact.
- `get_current_user_html(request)` → `UserRecord` or raises `HTTP 302` to `/login?next=<path>`.
  Used on all HTML page routes in `dashboard.py`.
No global auth middleware — the per-route `Depends` pattern is preserved from A1.

### A2.D4 — /login, /logout Route Placement

New file `backend/api/v1/endpoints/auth.py`, included via `app.include_router(auth_router)`
(no prefix). Routes are `GET /login`, `POST /login`, `GET /logout`.
These are HTML routes, not JSON API routes, so they are not under `/api/v1`.
The old inline `/logout` stub in `main.py` (401 + WWW-Authenticate) is removed.

### A2.D5 — SESSION_SECRET Handling

`SESSION_SECRET: str` in `Settings`. Empty string is allowed in `development`/`staging` with
a warning (dev default passed to middleware). Required and ≥32 chars in `production` (fail-fast
at Settings instantiation — process never starts). Never committed; `.env.example` has an empty
placeholder with a generation command.

### A2.D6 — Basic Auth Retirement

`backend/core/security.py` deleted. `HTTPBasic` and `verify_credentials` removed from
`backend/api/deps.py`. `BASIC_AUTH_USERNAME` / `BASIC_AUTH_PASSWORD` retained in `Settings`
as the A1 seed source only — they are no longer runtime authentication credentials.
`WWW-Authenticate: Basic` header is no longer sent on 401 responses.

### A2.D7 — Test Auth Strategy

Integration tests: one shared `authed_client` fixture in `tests/integration/conftest.py`
that logs in via `POST /login` and carries the session cookie (scope=module to amortise bcrypt).
Unit router tests: `app.dependency_overrides[get_current_user]` injecting a mock `UserRecord`
(faster, isolated, no DB dependency).
E2E (Playwright): login form flow replacing Authorization header injection.
Rationale: `POST /login` fixture tests the real auth path; override pattern is valid for
unit tests where auth behaviour is not under test.
```

---

## 12. Risks + Open Questions for Khaled

### 12.1 Risks

| # | Risk | Severity | Mitigation |
|---|------|----------|------------|
| R1 | **`itsdangerous` signature key rotation** — if `SESSION_SECRET` is rotated in production, all existing sessions are invalidated and users are forced to log in. | LOW | Acceptable for a small-team ERP tool. Document in ops runbook. |
| R2 | **`next` redirect parameter** — a malicious link like `/login?next=//evil.com` could redirect post-login to an external site. | MEDIUM | The `_sanitize_next` helper rejects paths not starting with `/` or starting with `//`. This blocks the open-redirect pattern. |
| R3 | **`TestClient` lifespan and the test DB** — `authed_client` depends on the lifespan running `seed_initial_user`, which writes to the SQLite DB at `USER_DB_PATH`. In CI, if `data/users.db` persists between runs (e.g. cached volume), the seed skips and `testadmin` may be absent or have a stale password hash. | MEDIUM | Ensure `USER_DB_PATH=:memory:` or `data/test-users.db` is set in the test environment. The existing `tests/conftest.py` does not set `USER_DB_PATH`; it should be added to `_defaults`. |
| R4 | **asyncio event-loop pollution in full test suite** — existing known issue (memory record). Adding session middleware + SQLite writes to the TestClient fixture stack may worsen it if any test uses `async def` with the default pytest-asyncio mode. | MEDIUM | Use synchronous `TestClient` for all new auth fixtures (not `AsyncClient`). Confirm the existing `@pytest.mark.asyncio` tests are not affected by the new middleware layer. |
| R5 | **DISPLAY_NAME setting becomes per-user** — `_extract_first_name` currently falls back to `settings.DISPLAY_NAME` for the display name. In a multi-user world, `DISPLAY_NAME` is a global setting that would show the same name for all users. After A2, each user's display name should come from `user.username` (or a future `display_name` column). The `settings.DISPLAY_NAME` fallback should be removed or made user-specific. | LOW | For A2, Khaled is the only user (`is_admin=True, modules=["*"]`). The current fallback is harmless. Defer the clean-up to A3 or when a second user is added. |
| R6 | **`POST /login` is not rate-limited** — the existing `limiter` (slowapi) applies per-IP rate limits to API endpoints. The new `/login` POST should be rate-limited to prevent brute-force attacks. | MEDIUM | Add `@limiter.limit("10/minute")` to the `login_submit` handler. This requires importing `limiter` from `backend.core.limiter` in `auth.py`. |

### 12.2 Open Questions for Khaled

**Q1 — Cookie domain / HTTPS in staging**  
`Secure=True` only fires when `ENVIRONMENT=="production"`. Is `staging` also served over HTTPS? If so, should `Secure` also apply to `staging`? Current plan: only production. Update `https_only` condition in `SessionMiddleware` if staging needs it.

**Q2 — Session expiry on browser close**  
`max_age=28800` (8 hours) keeps the session cookie alive until expiry even if the browser is closed and reopened. This is deliberate (one work-day session). If Khaled prefers a session cookie that dies when the browser closes, set `max_age=None` (Starlette default = browser-session). Confirm preference.

**Q3 — Rate limiting on /login (R6 above)**  
Brute-force protection on the login form. The plan proposes `10/minute` per IP. Is this the right threshold for the team (e.g. could it trigger on a VPN that shares one exit IP)? Alternative: `5/minute` with a longer lockout, or rely on fail2ban / infrastructure-level protection.

**Q4 — `USER_DB_PATH` in test environment**  
Should `tests/conftest.py` set `USER_DB_PATH=:memory:` so each test process gets a fresh in-memory DB? Or should it use a fixed path like `data/test-users.db` that is cleaned up between runs? The `:memory:` approach is cleanest for CI but means the seed runs on every TestClient instantiation. Confirm preference before implementing §10.

**Q5 — Mobile sidebar logout**  
The mobile sidebar (the `x-show="mobileOpen"` panel in base.html) has its own duplicate nav structure. The plan updates both the desktop sidebar and the mobile sidebar footer. Confirm this is correct, or should the mobile sidebar have a different logout UI (e.g. hidden logout, logout only accessible from desktop)?

**Q6 — Stale `// sends Basic Auth cookies` comment in api.js**  
The comment on line 27 of `api.js` is factually wrong (it never sent Basic auth via cookies) and will be doubly confusing after A2. The plan says update it to `// sends session cookie`. This is a one-line edit — is it in scope for A2, or deferred?
