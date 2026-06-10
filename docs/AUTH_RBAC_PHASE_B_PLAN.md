# Auth + RBAC — Phase B Implementation Plan
## Settings UI — Admin User Management

**Plan date:** 2026-06-10
**Author:** Claude Code
**Status:** IMPLEMENTING — Commit 1 in progress
**Stage scope:** ADMIN SETTINGS UI — manage users and their module access from the browser.
Backend JSON API + Alpine.js frontend. `is_admin` gates access; module access is NOT implied.

**CORRECTIONS APPLIED (2026-06-10):**
- **C1:** CORSMiddleware NOT widened. Settings UI is same-origin; CORS governs cross-origin only. B.D8 dropped entirely. R1 removed.
- **Q1–Q8:** All questions locked. Next-request semantics confirmed; admin toggle included; modules=[] allowed; @ in username regex; no tooltip; admin module-edit allowed; no hard-delete; unit tests sufficient for L3/L4.

---

## Table of Contents

1. [Files Read Before Planning](#1-files-read-before-planning)
2. [Guard Design + Repository Additions](#2-guard-design--repository-additions)
3. [API Surface](#3-api-surface)
4. [Settings Page UI Plan](#4-settings-page-ui-plan)
5. [Sidebar + `_base_ctx` Changes](#5-sidebar--_base_ctx-changes)
6. [Self-Lockout Rule Enforcement](#6-self-lockout-rule-enforcement)
7. [Full Test Plan](#7-full-test-plan)
8. [Proposed AUTH_RBAC_DECISIONS.md Entries](#8-proposed-auth_rbac_decisionsmd-entries)
9. [Commit Structure + Risks + Open Questions](#9-commit-structure--risks--open-questions)

---

## 1. Files Read Before Planning

| File | Key Finding |
|------|-------------|
| `docs/AUTH_RBAC_DECISIONS.md` | A1–A3 fully documented; `is_admin` reserved for Phase B; A1.D3 confirms is_admin ⊥ modules |
| `docs/AUTH_RBAC_A3_PLAN.md` | A3 implemented as planned; dual-guard pattern confirmed as the model to mirror |
| `backend/auth/repository.py` | `update_user` already handles `password_hash`, `modules`, `is_admin`, `is_active` via keyword args; `count_active_admins()` is the only missing method |
| `backend/auth/models.py` | `UserRecord` is a `@dataclass` with 7 fields; `is_admin: bool` and `is_active: bool` both present |
| `backend/api/deps.py` | `require_module_api/html` factories are the pattern; `_resolve_active_username` + dual `get_current_user` confirmed; `require_admin_*` mirrors them as plain functions (no factory needed — no parameter) |
| `backend/api/v1/endpoints/dashboard.py` | `_base_ctx` already fetches `_user_record` and returns `allowed_modules`; adding `is_admin` is one line; 5 existing HTML routes; `{% block content %}` at line 541, `{% block extra_scripts %}` at line 746 confirmed via base.html grep |
| `backend/api/v1/endpoints/auth.py` | login / logout / no_modules complete; `_PATH_MODULE_MAP` does NOT contain `/settings` — `_user_can_access_path` returns True for unknown paths (correct: admin guard enforces access at the route) |
| `backend/main.py` | `_error_response` confirmed; global `@app.exception_handler(403)` handler confirmed; **`CORSMiddleware(allow_methods=["GET", "OPTIONS"])` — must be expanded to include POST, PATCH for the settings API** |
| `frontend/templates/base.html` | `{% set am = allowed_modules %}` pattern confirmed; desktop nav ends at line ~252; mobile nav ends at line ~346; bottom section (collapse + user info + logout) is OUTSIDE the `<nav>` block — Settings link goes INSIDE `<nav>` (before closing tag), consistent with module links |
| `frontend/templates/403.html` | Standalone, confirmed working; `_t("forbidden.heading")` and `_t("forbidden.body")` used |
| `frontend/translations/en.json` | `"Settings": "Settings"` already exists; `"forbidden.*"` and `"no_modules.*"` keys confirmed |
| `frontend/translations/ar.json` | `"Settings": "الإعدادات"` already exists; all A3 keys confirmed |
| `tests/integration/conftest.py` | `_ensure_user` helper + `authed_client`, `hr_only_client`, `coll_ca_client`, `no_modules_client` all exist; `second_admin_client` is missing — needs to be added for last-admin protection tests |
| `tests/integration/test_rbac.py` | A3 tests complete and green; FIX1 envelope (`{"error":{"code":...}}`), FIX2 Accept header notes documented — mirror this pattern |
| `scripts/manage_users.py` | CLI has `add`, `list`, `set-modules`, `deactivate`; no `activate`, no `set-admin` — these gaps are filled by Phase B UI; CLI is untouched |

**`_base_ctx` pre-condition confirmed:** `_user_record` is already fetched. Adding `is_admin` is zero extra DB calls.

**CORS note (C1):** Settings UI is same-origin. Alpine `fetch()` to `/api/v1/settings/*` on the same host is not subject to CORS. `CORSMiddleware` is NOT widened.

---

## 2. Guard Design + Repository Additions

### 2.1 New Guards: `require_admin_api` and `require_admin_html`

Both added to `backend/api/deps.py`. Unlike `require_module_*` factories (parameterised by `module_id`), the admin guards are **plain dependency functions** — no factory because there is exactly one admin gate with no parameterisation.

**`require_admin_api`** — used on all `/api/v1/settings/*` routes:

```python
def require_admin_api(
    request: Request,
    username: str = Depends(get_current_user),
) -> None:
    """Allow iff is_admin. 401 handled upstream by get_current_user."""
    user = request.app.state.user_repo.get_user(username)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not user.is_admin:
        raise HTTPException(
            status_code=403,
            detail={"code": "ADMIN_ACCESS_DENIED"},
        )
```

**`require_admin_html`** — used on `/settings` HTML route:

```python
def require_admin_html(
    request: Request,
    username: str = Depends(get_current_user_html),
) -> None:
    """Allow iff is_admin. 302 to /login handled upstream by get_current_user_html."""
    user = request.app.state.user_repo.get_user(username)
    if user is None:
        raise HTTPException(
            status_code=302,
            headers={"Location": f"/login?next={request.url.path}"},
        )
    if not user.is_admin:
        raise HTTPException(status_code=403)
```

**Design notes:**
- `is_admin` is INDEPENDENT of `modules` (A1.D3). An admin with `modules=[]` passes `require_admin_api` and can reach `/settings`, but is still blocked on data routes by the module guards.
- The existing global `@app.exception_handler(403)` in `main.py` already handles `HTTPException(403)` for both API (JSON) and HTML (403.html) — **no changes to the handler**.
- FastAPI's per-request dependency caching: `get_current_user`/`get_current_user_html` run exactly once per request; the admin guard's `user_repo.get_user(username)` is one additional SQLite read — identical cost profile to `require_module_*`.

### 2.2 Repository Addition: `count_active_admins`

`update_user` already covers all field mutations. The **only new repository method** needed for lockout protection is:

**Add to `UserRepository` Protocol:**

```python
def count_active_admins(self) -> int: ...
```

**Add to `SQLiteUserRepository`:**

```python
def count_active_admins(self) -> int:
    """Count rows where is_admin=1 AND is_active=1. Used for last-admin protection."""
    with self._lock:
        cur = self._conn.execute(
            "SELECT COUNT(*) FROM users WHERE is_admin = 1 AND is_active = 1"
        )
        row = cur.fetchone()
    return row[0] if row else 0
```

**Why a dedicated method:** One SQL `COUNT(*)` instead of loading all rows and filtering in Python. For O(100) users both are equivalent, but the dedicated method is semantically clearer and avoids reading password_hashes into memory for a count query.

**Why NOT separate `update_password`, `set_active`, `set_admin` methods:** The mission spec listed these as examples. The existing `update_user(username, password_hash=..., is_active=..., is_admin=...)` already handles all these via keyword args. Adding pass-through wrappers would expand the interface for no benefit. `count_active_admins` is the only genuine addition.

### 2.3 CORSMiddleware

~~DROPPED per C1.~~ Settings UI is same-origin. CORSMiddleware is NOT widened.

---

## 3. API Surface

All routes live in the new file **`backend/api/v1/endpoints/settings.py`**. Registered in `backend/api/v1/router.py` under the prefix `/settings` with `dependencies=[Depends(require_admin_api)]` at the `include_router` level.

### 3.0 Shared Definitions in `settings.py`

```python
import re, json
from backend.auth.password import hash_password

_VALID_MODULES: frozenset[str] = frozenset({"crm", "hr", "collections", "customer_accounts", "*"})
_USERNAME_RE = re.compile(r'^[A-Za-z0-9._@\-]{2,64}$')
# @ included: seed username may be an email address (e.g. khaled.elmasry@laverde-eg.com)
```

### 3.1 `UserRow` Response Shape

Password hash is **NEVER** included in any response:

```json
{
  "username": "khaled.elmasry",
  "is_admin": true,
  "is_active": true,
  "modules": ["*"],
  "created_at": "2026-01-01T00:00:00+00:00",
  "updated_at": "2026-06-10T12:00:00+00:00"
}
```

**Success envelope** (consistent with existing API conventions):
```json
{ "ok": true, "data": { ... } }
```

**Error envelope** (uses existing `_error_response` from `main.py`):
```json
{
  "ok": false,
  "error": {
    "code": "ERROR_CODE",
    "message": "Human-readable message",
    "details": {},
    "request_id": "abc123",
    "timestamp": "2026-06-10T12:00:00+00:00"
  }
}
```

Note: `_error_response` is defined in `backend/main.py` and is not importable directly (it's a module-level function, not exported). The settings endpoints must either call `JSONResponse` directly following the same schema, or `_error_response` must be moved to a shared helper module. **Plan decision:** Move `_error_response` to `backend/core/responses.py` (new file, 1 function) and import it in both `main.py` and `settings.py`. This removes one awkward `from backend.main import _error_response` circular import risk.

### 3.2 Endpoints

---

#### `GET /api/v1/settings/users`

**Guard:** `require_admin_api` (applied at router level)
**Purpose:** List all users for the admin table.

**Response 200:**
```json
{
  "ok": true,
  "data": {
    "users": [
      { "username": "...", "is_admin": bool, "is_active": bool,
        "modules": [...], "created_at": "...", "updated_at": "..." },
      ...
    ]
  }
}
```

**No query parameters.** Full list always returned (O(100) users — no pagination needed).

**Errors:** Only 401 (unauthenticated) and 403 (non-admin), both handled by `require_admin_api`.

---

#### `POST /api/v1/settings/users`

**Guard:** `require_admin_api`
**Purpose:** Create a new user.

**Request body (JSON):**
```json
{
  "username": "string — 2–64 chars matching _USERNAME_RE",
  "password": "string — min 8 chars (plaintext; hashed before storage)",
  "modules": ["crm", "hr"] | ["*"] | [],
  "is_admin": false
}
```

**Response 201:**
```json
{ "ok": true, "data": { <UserRow> } }
```

**Errors:**

| Condition | Status | Code |
|-----------|--------|------|
| Username does not match `_USERNAME_RE` | 422 | `INVALID_USERNAME` |
| `len(password) < 8` | 422 | `PASSWORD_TOO_SHORT` |
| Any element of `modules` not in `_VALID_MODULES` | 422 | `INVALID_MODULE` |
| Username already exists (repo raises `ValueError`) | 409 | `USERNAME_EXISTS` |

**Password handling:** The endpoint calls `hash_password(body.password)` and passes the hash to `repo.create_user`. The plaintext is never stored, logged, or returned.

---

#### `PATCH /api/v1/settings/users/{username}/modules`

**Guard:** `require_admin_api`
**Purpose:** Replace a user's module list. Takes effect on the target user's next request (A3.Q4 behaviour — module changes propagate without session invalidation).

**Path param:** `username` — URL-encoded username of the target user.

**Request body (JSON):**
```json
{ "modules": ["crm", "hr"] | ["*"] | [] }
```

**Response 200:**
```json
{ "ok": true, "data": { <UserRow> } }
```

**Errors:**

| Condition | Status | Code |
|-----------|--------|------|
| Target user not found | 404 | `USER_NOT_FOUND` |
| Any element of `modules` not in `_VALID_MODULES` | 422 | `INVALID_MODULE` |

---

#### `PATCH /api/v1/settings/users/{username}/status`

**Guard:** `require_admin_api`
**Purpose:** Activate or deactivate a user. Deactivation takes effect on the target user's next request (A2.D1 — `_resolve_active_username` checks `is_active` on every request).

**Request body (JSON):**
```json
{ "is_active": true | false }
```

**Response 200:**
```json
{ "ok": true, "data": { <UserRow> } }
```

**Errors:**

| Condition | Status | Code |
|-----------|--------|------|
| Target user not found | 404 | `USER_NOT_FOUND` |
| `is_active=false` AND `requesting_username == username` | 422 | `SELF_LOCKOUT_DEACTIVATION` |
| `is_active=false` AND target `is_admin=true` AND `count_active_admins() <= 1` | 422 | `LAST_ADMIN_PROTECTION` |

---

#### `PATCH /api/v1/settings/users/{username}/admin`

**Guard:** `require_admin_api`
**Purpose:** Grant or revoke admin role. Granting takes effect on the target user's next request. The `is_admin` flag is checked via `user_repo.get_user` on every guarded request — no session invalidation needed.

**Request body (JSON):**
```json
{ "is_admin": true | false }
```

**Response 200:**
```json
{ "ok": true, "data": { <UserRow> } }
```

**Errors:**

| Condition | Status | Code |
|-----------|--------|------|
| Target user not found | 404 | `USER_NOT_FOUND` |
| `is_admin=false` AND `requesting_username == username` | 422 | `SELF_LOCKOUT_DEMOTE` |
| `is_admin=false` AND target `is_admin=true` AND `count_active_admins() <= 1` | 422 | `LAST_ADMIN_PROTECTION` |

---

#### `POST /api/v1/settings/users/{username}/reset-password`

**Guard:** `require_admin_api`
**Purpose:** Reset a user's password. An admin can reset any user's password, including other admins.

**Request body (JSON):**
```json
{ "new_password": "string — min 8 chars (plaintext; hashed before storage)" }
```

The "confirm password" check (typing it twice) is **client-side only** in Alpine.js. The API receives a single confirmed `new_password`. This is the standard pattern — server-side "confirm" adds no security value.

**Response 200:**
```json
{ "ok": true, "data": { "username": "...", "updated_at": "..." } }
```
(Only `username` and `updated_at` returned — minimal surface, no password field anywhere in response.)

**Errors:**

| Condition | Status | Code |
|-----------|--------|------|
| Target user not found | 404 | `USER_NOT_FOUND` |
| `len(new_password) < 8` | 422 | `PASSWORD_TOO_SHORT` |

---

### 3.3 Router Registration (in `backend/api/v1/router.py`)

```python
# New imports:
from backend.api.deps import require_admin_api
from backend.api.v1.endpoints.settings import router as settings_router

# New include_router block (not module-gated — admin-gated instead):
_admin = [Depends(require_admin_api)]
api_v1_router.include_router(
    settings_router,
    prefix="/settings",
    dependencies=_admin,
)
```

Full paths become `/api/v1/settings/users`, `/api/v1/settings/users/{username}/modules`, etc.

### 3.4 `_error_response` Extraction

Create **`backend/core/responses.py`** (new file, ~20 lines):

```python
from datetime import datetime, timezone
from fastapi import Request
from fastapi.responses import JSONResponse

def error_response(request: Request, status_code: int, code: str, message: str,
                   details: dict | None = None) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=status_code,
        content={
            "ok": False,
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
                "request_id": request_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        },
    )
```

`backend/main.py` updates its `_error_response` to delegate to `core.responses.error_response`.
`backend/api/v1/endpoints/settings.py` imports `from backend.core.responses import error_response`.

---

## 4. Settings Page UI Plan

### 4.1 HTML Route

Added to `backend/api/v1/endpoints/dashboard.py` (consistent with all other HTML routes):

```python
from backend.api.deps import require_admin_html  # new import

@router.get(
    "/settings",
    response_class=HTMLResponse,
    summary="Settings — User Management (admin only)",
    include_in_schema=False,
    dependencies=[Depends(require_admin_html)],
)
async def settings_page(
    request: Request,
    user: str = Depends(get_current_user_html),
) -> HTMLResponse:
    ctx = _base_ctx(request, user)
    ctx["page"] = "settings"
    return templates.TemplateResponse(request, "settings.html", ctx)
```

The page needs zero server-side data beyond `_base_ctx`. All user data is loaded client-side via Alpine.js calling `GET /api/v1/settings/users`.

### 4.2 Template File: `frontend/templates/settings.html`

**Extends `base.html`.** Uses `{% block content %}`, `{% block page_title %}`, `{% block extra_scripts %}`.

**Structure:**

```
{% extends "base.html" %}
{% block title %}{{ _t("settings.page_title") }} — LaVerde ERP AI Engine{% endblock %}
{% block page_title %}{{ _t("settings.page_title") }}{% endblock %}

{% block content %}
  <div x-data="settingsApp()" x-init="init()">

    <!-- Page header row -->
    <div class="flex items-center justify-between mb-6">
      <h1 class="text-xl font-semibold text-neutral-900 dark:text-neutral-100">
        {{ _t("settings.page_title") }}
      </h1>
      <button @click="createModal.open = true"
              class="btn btn-primary btn-sm">
        + {{ _t("settings.add_user") }}
      </button>
    </div>

    <!-- Full-page error banner (for load errors and inline action errors) -->
    <template x-if="pageError">
      <div class="mb-4 p-3 rounded-lg bg-danger-50 dark:bg-danger-950/30
                  text-danger-700 dark:text-danger-400 text-sm" x-text="pageError"></div>
    </template>

    <!-- Users table card -->
    <div class="bg-white dark:bg-neutral-900 rounded-xl border border-neutral-200
                dark:border-neutral-800 overflow-x-auto">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-neutral-100 dark:border-neutral-800">
            <th class="px-4 py-3 text-left rtl:text-right font-medium text-neutral-500">
              {{ _t("settings.table.username") }}
            </th>
            <th ...>{{ _t("settings.table.status") }}</th>
            <th ...>{{ _t("settings.table.admin") }}</th>
            <th ...>{{ _t("settings.table.modules") }}</th>
            <th ...>{{ _t("settings.table.created") }}</th>
            <th ...>{{ _t("settings.table.actions") }}</th>
          </tr>
        </thead>
        <tbody>
          <!-- Loading state -->
          <template x-if="loading">
            <tr>
              <td colspan="6" class="px-4 py-8 text-center text-neutral-400">
                {{ _t("Loading...") }}
              </td>
            </tr>
          </template>

          <!-- Empty state -->
          <template x-if="!loading && users.length === 0">
            <tr>
              <td colspan="6" class="px-4 py-8 text-center text-neutral-400">
                {{ _t("settings.no_users") }}
              </td>
            </tr>
          </template>

          <!-- User rows -->
          <template x-for="u in users" :key="u.username">
            <tr class="border-b border-neutral-50 dark:border-neutral-800/50
                       hover:bg-neutral-50 dark:hover:bg-neutral-800/30">
              <!-- username -->
              <td class="px-4 py-3 font-mono text-xs" x-text="u.username"></td>

              <!-- active status badge -->
              <td class="px-4 py-3">
                <span :class="u.is_active
                  ? 'bg-success-100 dark:bg-success-900/30 text-success-700 dark:text-success-400'
                  : 'bg-danger-100 dark:bg-danger-900/30 text-danger-700 dark:text-danger-400'"
                  class="text-xs px-2 py-0.5 rounded-full font-medium"
                  x-text="u.is_active
                    ? '{{ _t("settings.status.active") }}'
                    : '{{ _t("settings.status.inactive") }}'">
                </span>
              </td>

              <!-- admin badge -->
              <td class="px-4 py-3">
                <span x-show="u.is_admin"
                      class="text-xs px-2 py-0.5 rounded-full font-medium
                             bg-primary-100 dark:bg-primary-900/30
                             text-primary-700 dark:text-primary-400">
                  {{ _t("settings.table.admin") }}
                </span>
              </td>

              <!-- modules badges -->
              <td class="px-4 py-3">
                <div class="flex flex-wrap gap-1">
                  <template x-for="m in u.modules" :key="m">
                    <span class="text-xs px-1.5 py-0.5 rounded bg-neutral-100
                                 dark:bg-neutral-800 text-neutral-600 dark:text-neutral-400 font-mono"
                          x-text="m === '*' ? '{{ _t('settings.all_modules_badge') }}' : m">
                    </span>
                  </template>
                  <span x-show="u.modules.length === 0"
                        class="text-neutral-400 text-xs">—</span>
                </div>
              </td>

              <!-- created_at -->
              <td class="px-4 py-3 text-xs text-neutral-500"
                  x-text="fmtDate(u.created_at)"></td>

              <!-- actions -->
              <td class="px-4 py-3">
                <div class="flex flex-wrap gap-1.5">

                  <!-- Edit modules -->
                  <button @click="openEditModules(u)"
                          class="btn btn-xs btn-secondary">
                    {{ _t("settings.action.edit_modules") }}
                  </button>

                  <!-- Toggle active (inline — no modal) -->
                  <button @click="toggleStatus(u)"
                          :disabled="busy.status[u.username]"
                          class="btn btn-xs"
                          :class="u.is_active ? 'btn-warning-ghost' : 'btn-success-ghost'">
                    <span x-text="u.is_active
                      ? '{{ _t("settings.action.deactivate") }}'
                      : '{{ _t("settings.action.activate") }}'">
                    </span>
                  </button>

                  <!-- Toggle admin (inline — no modal) -->
                  <button @click="toggleAdmin(u)"
                          :disabled="busy.admin[u.username]"
                          class="btn btn-xs btn-ghost">
                    <span x-text="u.is_admin
                      ? '{{ _t("settings.action.revoke_admin") }}'
                      : '{{ _t("settings.action.make_admin") }}'">
                    </span>
                  </button>

                  <!-- Reset password -->
                  <button @click="openResetPassword(u)"
                          class="btn btn-xs btn-ghost">
                    {{ _t("settings.action.reset_password") }}
                  </button>

                </div>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>

    <!-- ── Create User Modal ──────────────────────────────────────────────── -->
    <div x-show="createModal.open"
         class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
         @keydown.escape.window="createModal.open = false">
      <div class="w-full max-w-md bg-white dark:bg-neutral-900 rounded-2xl shadow-xl border
                  border-neutral-200 dark:border-neutral-800 p-6"
           @click.outside="createModal.open = false">
        <h2 class="text-base font-semibold mb-4">{{ _t("settings.modal.create_title") }}</h2>
        <form @submit.prevent="submitCreate()" class="flex flex-col gap-3">
          <!-- Username -->
          <label class="flex flex-col gap-1 text-sm">
            {{ _t("settings.form.username") }}
            <input type="text" x-model="createModal.username" required
                   autocomplete="off" autocapitalize="none"
                   class="input-field font-mono" placeholder="e.g. john.doe">
          </label>
          <!-- Password -->
          <label class="flex flex-col gap-1 text-sm">
            {{ _t("settings.form.password") }}
            <input type="password" x-model="createModal.password" required minlength="8"
                   autocomplete="new-password" class="input-field">
          </label>
          <!-- Confirm password -->
          <label class="flex flex-col gap-1 text-sm">
            {{ _t("settings.form.confirm_password") }}
            <input type="password" x-model="createModal.confirmPassword" required
                   autocomplete="new-password" class="input-field">
          </label>
          <!-- Module checkboxes -->
          <fieldset class="flex flex-col gap-1.5">
            <legend class="text-sm font-medium mb-1">{{ _t("settings.form.modules_label") }}</legend>
            <label class="flex items-center gap-2 text-sm cursor-pointer">
              <input type="checkbox" value="crm"
                     x-model="createModal.modules" :disabled="createModal.allModules">
              CRM
            </label>
            <label class="flex items-center gap-2 text-sm cursor-pointer">
              <input type="checkbox" value="hr"
                     x-model="createModal.modules" :disabled="createModal.allModules">
              HR
            </label>
            <label class="flex items-center gap-2 text-sm cursor-pointer">
              <input type="checkbox" value="collections"
                     x-model="createModal.modules" :disabled="createModal.allModules">
              Collections
            </label>
            <label class="flex items-center gap-2 text-sm cursor-pointer">
              <input type="checkbox" value="customer_accounts"
                     x-model="createModal.modules" :disabled="createModal.allModules">
              Customer Accounts
            </label>
            <label class="flex items-center gap-2 text-sm cursor-pointer mt-1 border-t
                          border-neutral-100 dark:border-neutral-800 pt-2">
              <input type="checkbox" x-model="createModal.allModules"
                     @change="createModal.modules = []">
              {{ _t("settings.form.all_modules") }}
            </label>
          </fieldset>
          <!-- is_admin toggle -->
          <label class="flex items-center gap-2 text-sm cursor-pointer">
            <input type="checkbox" x-model="createModal.isAdmin">
            {{ _t("settings.form.is_admin") }}
          </label>
          <!-- Error -->
          <template x-if="createModal.error">
            <p class="text-danger-600 dark:text-danger-400 text-sm" x-text="createModal.error"></p>
          </template>
          <!-- Buttons -->
          <div class="flex justify-end gap-2 pt-2">
            <button type="button" @click="createModal.open = false"
                    class="btn btn-secondary btn-sm">
              {{ _t("settings.form.cancel") }}
            </button>
            <button type="submit" :disabled="createModal.loading"
                    class="btn btn-primary btn-sm">
              <span x-show="!createModal.loading">{{ _t("settings.form.submit_create") }}</span>
              <span x-show="createModal.loading">{{ _t("Loading...") }}</span>
            </button>
          </div>
        </form>
      </div>
    </div>

    <!-- ── Edit Modules Modal ─────────────────────────────────────────────── -->
    <div x-show="editModal.open"
         class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
         @keydown.escape.window="editModal.open = false">
      <div class="w-full max-w-sm bg-white dark:bg-neutral-900 rounded-2xl shadow-xl border
                  border-neutral-200 dark:border-neutral-800 p-6"
           @click.outside="editModal.open = false">
        <h2 class="text-base font-semibold mb-4">
          {{ _t("settings.modal.edit_modules_title") }}
          <span class="font-mono text-sm text-neutral-500" x-text="' — ' + editModal.username"></span>
        </h2>
        <form @submit.prevent="submitEditModules()" class="flex flex-col gap-3">
          <fieldset class="flex flex-col gap-1.5">
            <legend class="text-sm font-medium mb-1">{{ _t("settings.form.modules_label") }}</legend>
            <!-- same checkbox pattern as create modal, bound to editModal.modules / editModal.allModules -->
          </fieldset>
          <template x-if="editModal.error">
            <p class="text-danger-600 dark:text-danger-400 text-sm" x-text="editModal.error"></p>
          </template>
          <div class="flex justify-end gap-2 pt-2">
            <button type="button" @click="editModal.open = false" class="btn btn-secondary btn-sm">
              {{ _t("settings.form.cancel") }}
            </button>
            <button type="submit" :disabled="editModal.loading" class="btn btn-primary btn-sm">
              {{ _t("settings.form.submit_save") }}
            </button>
          </div>
        </form>
      </div>
    </div>

    <!-- ── Reset Password Modal ───────────────────────────────────────────── -->
    <div x-show="resetModal.open"
         class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
         @keydown.escape.window="resetModal.open = false">
      <div class="w-full max-w-sm bg-white dark:bg-neutral-900 rounded-2xl shadow-xl border
                  border-neutral-200 dark:border-neutral-800 p-6"
           @click.outside="resetModal.open = false">
        <h2 class="text-base font-semibold mb-4">
          {{ _t("settings.modal.reset_password_title") }}
          <span class="font-mono text-sm text-neutral-500" x-text="' — ' + resetModal.username"></span>
        </h2>
        <form @submit.prevent="submitResetPassword()" class="flex flex-col gap-3">
          <label class="flex flex-col gap-1 text-sm">
            {{ _t("settings.form.new_password") }}
            <input type="password" x-model="resetModal.password" required minlength="8"
                   autocomplete="new-password" class="input-field">
          </label>
          <label class="flex flex-col gap-1 text-sm">
            {{ _t("settings.form.confirm_new_password") }}
            <input type="password" x-model="resetModal.confirmPassword" required
                   autocomplete="new-password" class="input-field">
          </label>
          <template x-if="resetModal.error">
            <p class="text-danger-600 dark:text-danger-400 text-sm" x-text="resetModal.error"></p>
          </template>
          <div class="flex justify-end gap-2 pt-2">
            <button type="button" @click="resetModal.open = false" class="btn btn-secondary btn-sm">
              {{ _t("settings.form.cancel") }}
            </button>
            <button type="submit" :disabled="resetModal.loading" class="btn btn-primary btn-sm">
              {{ _t("settings.form.submit_save") }}
            </button>
          </div>
        </form>
      </div>
    </div>

  </div><!-- end x-data -->
{% endblock %}
```

### 4.3 Alpine.js Component (`{% block extra_scripts %}`)

```javascript
function settingsApp() {
  return {
    users: [],
    loading: false,
    pageError: null,
    busy: { status: {}, admin: {} },  // { username: true } while in-flight

    createModal: {
      open: false, username: '', password: '', confirmPassword: '',
      modules: [], allModules: false, isAdmin: false,
      error: null, loading: false,
    },
    editModal: {
      open: false, username: '', modules: [], allModules: false,
      error: null, loading: false,
    },
    resetModal: {
      open: false, username: '', password: '', confirmPassword: '',
      error: null, loading: false,
    },

    async init() { await this.loadUsers(); },

    async loadUsers() {
      this.loading = true;
      this.pageError = null;
      try {
        const r = await fetch('/api/v1/settings/users');
        if (!r.ok) { this.pageError = (await r.json()).error.message; return; }
        this.users = (await r.json()).data.users;
      } catch { this.pageError = '{{ _t("Error loading data") }}'; }
      finally { this.loading = false; }
    },

    fmtDate(iso) {
      if (!iso) return '—';
      return new Date(iso).toLocaleDateString('{{ lang }}');
    },

    // ── Create user ──────────────────────────────────────────────────────────
    async submitCreate() {
      this.createModal.error = null;
      if (this.createModal.password !== this.createModal.confirmPassword) {
        this.createModal.error = '{{ _t("settings.error.password_mismatch") }}'; return;
      }
      if (this.createModal.password.length < 8) {
        this.createModal.error = '{{ _t("settings.error.password_too_short") }}'; return;
      }
      this.createModal.loading = true;
      const modules = this.createModal.allModules ? ['*'] : this.createModal.modules;
      try {
        const r = await fetch('/api/v1/settings/users', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            username: this.createModal.username,
            password: this.createModal.password,
            modules,
            is_admin: this.createModal.isAdmin,
          }),
        });
        if (!r.ok) {
          const e = await r.json();
          this.createModal.error = this._mapError(e.error.code, e.error.message);
          return;
        }
        this.createModal.open = false;
        await this.loadUsers();
      } catch { this.createModal.error = '{{ _t("Error loading data") }}'; }
      finally { this.createModal.loading = false; }
    },

    // ── Edit modules ─────────────────────────────────────────────────────────
    openEditModules(u) {
      const allModules = u.modules.includes('*');
      this.editModal = {
        open: true, username: u.username,
        modules: allModules ? [] : [...u.modules],
        allModules, error: null, loading: false,
      };
    },
    async submitEditModules() {
      this.editModal.error = null;
      this.editModal.loading = true;
      const modules = this.editModal.allModules ? ['*'] : this.editModal.modules;
      try {
        const r = await fetch(
          `/api/v1/settings/users/${encodeURIComponent(this.editModal.username)}/modules`,
          { method: 'PATCH', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ modules }) }
        );
        if (!r.ok) {
          const e = await r.json();
          this.editModal.error = this._mapError(e.error.code, e.error.message);
          return;
        }
        this.editModal.open = false;
        await this.loadUsers();
      } catch { this.editModal.error = '{{ _t("Error loading data") }}'; }
      finally { this.editModal.loading = false; }
    },

    // ── Toggle status (inline) ───────────────────────────────────────────────
    async toggleStatus(u) {
      this.pageError = null;
      this.busy.status[u.username] = true;
      try {
        const r = await fetch(
          `/api/v1/settings/users/${encodeURIComponent(u.username)}/status`,
          { method: 'PATCH', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_active: !u.is_active }) }
        );
        if (!r.ok) {
          const e = await r.json();
          this.pageError = this._mapError(e.error.code, e.error.message);
          return;
        }
        await this.loadUsers();
      } catch { this.pageError = '{{ _t("Error loading data") }}'; }
      finally { delete this.busy.status[u.username]; }
    },

    // ── Toggle admin (inline) ────────────────────────────────────────────────
    async toggleAdmin(u) {
      this.pageError = null;
      this.busy.admin[u.username] = true;
      try {
        const r = await fetch(
          `/api/v1/settings/users/${encodeURIComponent(u.username)}/admin`,
          { method: 'PATCH', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_admin: !u.is_admin }) }
        );
        if (!r.ok) {
          const e = await r.json();
          this.pageError = this._mapError(e.error.code, e.error.message);
          return;
        }
        await this.loadUsers();
      } catch { this.pageError = '{{ _t("Error loading data") }}'; }
      finally { delete this.busy.admin[u.username]; }
    },

    // ── Reset password ───────────────────────────────────────────────────────
    openResetPassword(u) {
      this.resetModal = {
        open: true, username: u.username,
        password: '', confirmPassword: '', error: null, loading: false,
      };
    },
    async submitResetPassword() {
      this.resetModal.error = null;
      if (this.resetModal.password !== this.resetModal.confirmPassword) {
        this.resetModal.error = '{{ _t("settings.error.password_mismatch") }}'; return;
      }
      if (this.resetModal.password.length < 8) {
        this.resetModal.error = '{{ _t("settings.error.password_too_short") }}'; return;
      }
      this.resetModal.loading = true;
      try {
        const r = await fetch(
          `/api/v1/settings/users/${encodeURIComponent(this.resetModal.username)}/reset-password`,
          { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ new_password: this.resetModal.password }) }
        );
        if (!r.ok) {
          const e = await r.json();
          this.resetModal.error = this._mapError(e.error.code, e.error.message);
          return;
        }
        this.resetModal.open = false;
      } catch { this.resetModal.error = '{{ _t("Error loading data") }}'; }
      finally { this.resetModal.loading = false; }
    },

    // ── Error code → i18n string mapping ────────────────────────────────────
    _mapError(code, fallback) {
      const map = {
        'SELF_LOCKOUT_DEACTIVATION': '{{ _t("settings.error.self_lockout_deactivation") }}',
        'SELF_LOCKOUT_DEMOTE':       '{{ _t("settings.error.self_lockout_demote") }}',
        'LAST_ADMIN_PROTECTION':     '{{ _t("settings.error.last_admin") }}',
        'USERNAME_EXISTS':           '{{ _t("settings.error.username_exists") }}',
        'INVALID_USERNAME':          '{{ _t("settings.error.username_invalid") }}',
        'PASSWORD_TOO_SHORT':        '{{ _t("settings.error.password_too_short") }}',
        'INVALID_MODULE':            '{{ _t("settings.error.invalid_module") }}',
      };
      return map[code] || fallback;
    },
  };
}
```

### 4.4 RTL Notes

1. `settings.html` extends `base.html`. The `<html dir="rtl">` is set by base.html from the `is_rtl` context variable — no per-template direction setup needed.
2. The table header cells use `text-left rtl:text-right` (Tailwind RTL prefix, consistent with other tables in the app).
3. The modal action buttons use `flex justify-end gap-2`. In RTL, `flex-row` renders right-to-left — "Cancel" appears on the right, "Save" on the left in RTL. This is the correct Arabic convention (primary action on the right).
4. The badge/chip elements (`bg-success-100 ...`) are inline-flex — they flow naturally with RTL text.
5. `fmtDate(iso)` uses `toLocaleDateString('{{ lang }}')` where `lang` is rendered server-side by Jinja2 (`ar` or `en`). Arabic date formatting uses the browser's Intl API with locale `ar`.

### 4.5 Empty and Error States

| State | Location | Trigger |
|-------|----------|---------|
| "Loading..." row (colspan 6) | `<tbody>` | `loading === true` |
| "No users found" row (colspan 6) | `<tbody>` | `!loading && users.length === 0` |
| Full-page error banner (red) | Above table | GET /users fails; inline toggleStatus/toggleAdmin fails |
| Create modal inline error | Below form checkboxes | POST /users fails OR client validation fails |
| Edit modules modal inline error | Below checkboxes | PATCH /modules fails |
| Reset password modal inline error | Below confirm field | POST /reset-password fails OR client validation fails |

Inline action errors (toggleStatus, toggleAdmin) surface as the full-page error banner since there is no modal context. The banner clears on the next successful `loadUsers()` call.

---

## 5. Sidebar + `_base_ctx` Changes

### 5.1 `_base_ctx` — Add `is_admin`

**File:** `backend/api/v1/endpoints/dashboard.py`

Current `_base_ctx` already fetches `_user_record`. One line added:

```python
def _base_ctx(request: Request, user: str) -> dict:
    lang = detect_lang(dict(request.cookies), request.headers.get("accept-language", ""))
    _user_record = request.app.state.user_repo.get_user(user)
    allowed_modules: list[str] = _user_record.modules if _user_record else []
    is_admin: bool = _user_record.is_admin if _user_record else False  # ← ADD
    return {
        "request": request,
        "current_user": user,
        "user_display_name": _extract_first_name(user),
        "lang": lang,
        "is_rtl": lang == "ar",
        "_t": make_translator(lang),
        "allowed_modules": allowed_modules,
        "is_admin": is_admin,  # ← ADD
    }
```

**Performance:** Zero additional DB calls. `_user_record` was already fetched; `is_admin` is a field on the same object.

**Safety:** If `_user_record` is None (defensive case), `is_admin` defaults to `False` — Settings link hidden, not crashed.

**Scope:** All 5 existing HTML routes call `_base_ctx`. They all receive `is_admin` automatically. Their templates extend `base.html`. No template changes needed except `base.html` itself (for the Settings link) and `settings.html` (which reads `is_admin` is already in context via `_base_ctx`).

### 5.2 Desktop Sidebar — Settings Link

**Insert point:** At the end of `<nav class="flex-1 ...">`, after the `{% if '*' in am %}` Project Mgmt `{% endif %}` block, before `</nav>`.

```jinja2
{% if is_admin %}
<!-- Settings — admin only -->
<p x-show="!sidebarCollapsed"
   class="px-3 pb-1 pt-3 text-[10px] font-semibold text-neutral-400 dark:text-neutral-600
          uppercase tracking-widest">
  {{ _t("Settings") }}
</p>
<a href="/settings"
   class="sidebar-link {% if page == 'settings' %}active{% endif %}"
   title="{{ _t('Settings') }}">
  <svg class="sidebar-link-icon shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
          d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066
             c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924
             0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724
             0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066
             c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756
             -2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37
             .996.608 2.296.07 2.572-1.065z"/>
    <circle cx="12" cy="12" r="3"/>
  </svg>
  <span x-show="!sidebarCollapsed" class="truncate">{{ _t("Settings") }}</span>
</a>
{% endif %}
```

**`x-show="!sidebarCollapsed"`** on the section label and span — consistent with all other sidebar section labels and link text.

### 5.3 Mobile Sidebar — Settings Link

**Insert point:** Inside mobile `<nav class="flex-1 px-2 py-3 ...">`, after the `{% if 'hr' in am or '*' in am %}` HR link block, before `</nav>`.

```jinja2
{% if is_admin %}
<a href="/settings"
   class="sidebar-link {% if page == 'settings' %}active{% endif %}">
  <svg class="sidebar-link-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path ... (same gear path) .../>
    <circle cx="12" cy="12" r="3"/>
  </svg>
  {{ _t("Settings") }}
</a>
{% endif %}
```

### 5.4 Non-Admin Users

A non-admin user never sees the Settings link (Jinja2 `{% if is_admin %}` evaluates to False). Visiting `/settings` directly without admin → 403 (rendered as `403.html` by the global handler, with "Back" and "Logout" buttons). No information leakage.

---

## 6. Self-Lockout Rule Enforcement

All lockout rules enforced at the **API layer** (`settings.py` handlers). The repository enforces only data integrity (unique username, NOT NULL columns). Business invariants live in the service/API layer.

### 6.1 The Four Rules

| ID | Rule | Trigger endpoint | Test |
|----|------|-----------------|------|
| L1 | Admin cannot deactivate themselves | `PATCH /status` with `is_active=false` | `requesting_username == target_username` |
| L2 | Admin cannot revoke their own `is_admin` | `PATCH /admin` with `is_admin=false` | `requesting_username == target_username` |
| L3 | Last active admin cannot be deactivated | `PATCH /status` with `is_active=false` when target `is_admin=true` | `count_active_admins() <= 1` |
| L4 | Last active admin cannot be demoted | `PATCH /admin` with `is_admin=false` when target `is_admin=true` | `count_active_admins() <= 1` |

### 6.2 Enforcement Logic Sketch

**`PATCH /status` handler (L1 + L3):**

```python
async def patch_status(username: str, body: StatusPatch, request: Request,
                       requesting_username: str = Depends(get_current_user)) -> JSONResponse:
    # L1: self-deactivation check (no DB call needed)
    if not body.is_active and requesting_username == username:
        return error_response(request, 422, "SELF_LOCKOUT_DEACTIVATION",
                              "Cannot deactivate your own account.")

    target = repo.get_user(username)
    if target is None:
        return error_response(request, 404, "USER_NOT_FOUND", f"User '{username}' not found.")

    # L3: last-admin protection (one COUNT query)
    if not body.is_active and target.is_admin:
        if repo.count_active_admins() <= 1:
            return error_response(request, 422, "LAST_ADMIN_PROTECTION",
                                  "Cannot deactivate the last active admin.")

    repo.update_user(username, is_active=body.is_active)
    return JSONResponse(status_code=200,
                        content={"ok": True, "data": _to_user_row(repo.get_user(username))})
```

**`PATCH /admin` handler (L2 + L4):**

```python
async def patch_admin(username: str, body: AdminPatch, request: Request,
                      requesting_username: str = Depends(get_current_user)) -> JSONResponse:
    # L2: self-demote check (no DB call needed)
    if not body.is_admin and requesting_username == username:
        return error_response(request, 422, "SELF_LOCKOUT_DEMOTE",
                              "Cannot revoke your own admin role.")

    target = repo.get_user(username)
    if target is None:
        return error_response(request, 404, "USER_NOT_FOUND", f"User '{username}' not found.")

    # L4: last-admin protection (one COUNT query)
    if not body.is_admin and target.is_admin:
        if repo.count_active_admins() <= 1:
            return error_response(request, 422, "LAST_ADMIN_PROTECTION",
                                  "Cannot demote the last active admin.")

    repo.update_user(username, is_admin=body.is_admin)
    return JSONResponse(status_code=200,
                        content={"ok": True, "data": _to_user_row(repo.get_user(username))})
```

### 6.3 Count Semantics

`count_active_admins()` returns the count **before** the update. Condition is `<= 1` (not `== 1`) for safety:

- Count == 1 and we're about to remove the last → block.
- Count >= 2 → allow (at least one admin remains after the change).
- Count == 0 is theoretically impossible (the seed always creates one admin with `is_admin=True`).

**Concurrent demote race:** SQLite's per-repository `threading.Lock` serialises `count_active_admins()` and `update_user()` calls. Two concurrent demote requests will serialize; the second one sees count == 1 and is blocked. No race condition.

### 6.4 L1/L2 Take Priority Over L3/L4

L1 and L2 are checked BEFORE the `get_user(target)` call. This avoids a wasted DB read when an admin tries to self-modify. L3 and L4 are checked after `get_user` (we need to know if the target is_admin before calling count).

---

## 7. Full Test Plan

### 7.1 Unit Tests

**File: `tests/unit/auth/test_settings_guards.py`** (new)

Uses `app.dependency_overrides` and mock `user_repo` injected into `app.state`. Same pattern as A3 unit router tests.

```
class TestRequireAdminApi:
    test_admin_passes            — is_admin=True, no exception
    test_non_admin_gets_403     — is_admin=False → 403 {"error":{"code":"ADMIN_ACCESS_DENIED"}}
    test_unauthenticated_gets_401 — no session → get_current_user raises 401

class TestRequireAdminHtml:
    test_admin_passes_html          — is_admin=True, route responds
    test_non_admin_gets_403_html    — is_admin=False → 403 (Accept: text/html → 403.html)
    test_unauthenticated_302_html   — no session → 302 with Location containing /login

class TestRequireAdminVsModules:
    test_admin_with_no_modules_passes_admin_guard  — is_admin=True, modules=[] → guard passes
    test_non_admin_with_wildcard_modules_gets_403  — is_admin=False, modules=["*"] → 403
```

**File: `tests/unit/auth/test_lockout_rules.py`** (new)

Uses a lightweight TestClient with the full settings router mounted, mock `user_repo` injected.

```
class TestSelfLockoutDeactivation:
    test_cannot_deactivate_self                 — PATCH /status is_active=false on self → 422 SELF_LOCKOUT_DEACTIVATION
    test_can_deactivate_other_user              — PATCH /status is_active=false on other → 200
    test_deactivating_self_no_count_call        — L1 fires before count_active_admins is ever called (mock assertion)

class TestSelfLockoutDemote:
    test_cannot_demote_self                     — PATCH /admin is_admin=false on self → 422 SELF_LOCKOUT_DEMOTE
    test_can_demote_other_admin_if_two_exist    — two active admins → demoting one → 200
    test_demoting_self_no_count_call            — L2 fires before count_active_admins is called

class TestLastAdminProtection:
    test_cannot_deactivate_last_admin           — count_active_admins=1, deactivate that admin → 422 LAST_ADMIN_PROTECTION
    test_cannot_demote_last_admin               — count_active_admins=1, demote → 422 LAST_ADMIN_PROTECTION
    test_can_deactivate_when_two_admins         — count_active_admins=2, deactivate one → 200
    test_can_demote_when_two_admins             — count_active_admins=2, demote one → 200
    test_last_admin_self_deactivation_hits_l1   — self-deactivation of last admin → L1 fires first (SELF_LOCKOUT_DEACTIVATION, not LAST_ADMIN_PROTECTION)

class TestPasswordValidation:
    test_create_short_password                  — "abc1234" (7 chars) → 422 PASSWORD_TOO_SHORT
    test_create_exactly_8_chars_passes          — "abcd1234" → 201
    test_reset_short_password                   — same 422 for reset endpoint
```

**File: `tests/unit/auth/test_user_store.py`** (existing — add one test)

```
test_count_active_admins_counts_correctly  — create 2 admins (1 active, 1 inactive) → count == 1
```

### 7.2 Integration Tests

**File: `tests/integration/test_settings_api.py`** (new)

#### Section A — Auth matrix (401 / 403 / admin-allowed)

```python
@pytest.mark.parametrize("method,path,body", [
    ("GET",   "/api/v1/settings/users",                          None),
    ("POST",  "/api/v1/settings/users",
              {"username": "_temp_matrix_", "password": "testpass12",
               "modules": [], "is_admin": False}),
    ("PATCH", "/api/v1/settings/users/testadmin/modules",        {"modules": ["crm"]}),
    ("PATCH", "/api/v1/settings/users/testadmin/status",         {"is_active": True}),
    ("PATCH", "/api/v1/settings/users/testadmin/admin",          {"is_admin": True}),
    ("POST",  "/api/v1/settings/users/testadmin/reset-password", {"new_password": "newpass123"}),
])
class TestSettingsAuthMatrix:
    def test_admin_not_blocked(self, method, path, body, authed_client):
        """testadmin (is_admin=True) gets through the admin guard (may still get 409/422 on body issues)."""
        r = getattr(authed_client, method.lower())(path, json=body)
        assert r.status_code not in (401, 403)

    def test_non_admin_gets_403(self, method, path, body, hr_only_client):
        """hr_only (is_admin=False) → 403 ADMIN_ACCESS_DENIED on every settings endpoint."""
        r = getattr(hr_only_client, method.lower())(path, json=body)
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "ADMIN_ACCESS_DENIED"

    def test_unauthenticated_gets_401(self, method, path, body):
        """No session → 401."""
        with TestClient(app, follow_redirects=False) as c:
            r = getattr(c, method.lower())(path, json=body)
        assert r.status_code == 401
```

#### Section B — CRUD operations

```python
class TestUserCRUD:
    def test_list_users_returns_testadmin(self, authed_client):
        r = authed_client.get("/api/v1/settings/users")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        usernames = [u["username"] for u in data["data"]["users"]]
        assert "testadmin" in usernames

    def test_list_users_no_password_hash(self, authed_client):
        r = authed_client.get("/api/v1/settings/users")
        raw = r.text
        assert "password_hash" not in raw
        assert "password" not in raw

    def test_create_user_201(self, authed_client):
        r = authed_client.post("/api/v1/settings/users", json={
            "username": "b_test_create", "password": "testpass12",
            "modules": ["hr"], "is_admin": False,
        })
        assert r.status_code == 201
        u = r.json()["data"]
        assert u["username"] == "b_test_create"
        assert u["modules"] == ["hr"]
        assert u["is_admin"] is False
        assert "password" not in u

    def test_create_duplicate_409(self, authed_client):
        r = authed_client.post("/api/v1/settings/users", json={
            "username": "testadmin", "password": "testpass12",
            "modules": [], "is_admin": False,
        })
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "USERNAME_EXISTS"

    def test_create_invalid_username_422(self, authed_client):
        r = authed_client.post("/api/v1/settings/users", json={
            "username": "has space", "password": "testpass12",
            "modules": [], "is_admin": False,
        })
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "INVALID_USERNAME"

    def test_create_short_password_422(self, authed_client):
        r = authed_client.post("/api/v1/settings/users", json={
            "username": "validuser_x", "password": "short",
            "modules": [], "is_admin": False,
        })
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "PASSWORD_TOO_SHORT"

    def test_create_invalid_module_422(self, authed_client):
        r = authed_client.post("/api/v1/settings/users", json={
            "username": "validuser_y", "password": "testpass12",
            "modules": ["unknown_mod"], "is_admin": False,
        })
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "INVALID_MODULE"

    def test_update_modules_200(self, authed_client):
        r = authed_client.patch("/api/v1/settings/users/hr_only/modules",
                                json={"modules": ["hr", "crm"]})
        assert r.status_code == 200
        assert sorted(r.json()["data"]["modules"]) == ["crm", "hr"]

    def test_update_modules_wildcard(self, authed_client):
        r = authed_client.patch("/api/v1/settings/users/hr_only/modules",
                                json={"modules": ["*"]})
        assert r.status_code == 200
        assert r.json()["data"]["modules"] == ["*"]

    def test_update_modules_unknown_user_404(self, authed_client):
        r = authed_client.patch("/api/v1/settings/users/nonexistent_xyz/modules",
                                json={"modules": ["hr"]})
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "USER_NOT_FOUND"

    def test_activate_user(self, authed_client):
        r = authed_client.patch("/api/v1/settings/users/no_modules/status",
                                json={"is_active": True})
        assert r.status_code == 200
        assert r.json()["data"]["is_active"] is True

    def test_reset_password_200_no_hash_returned(self, authed_client):
        r = authed_client.post("/api/v1/settings/users/hr_only/reset-password",
                               json={"new_password": "newpass123"})
        assert r.status_code == 200
        body_str = r.text
        assert "password" not in body_str
        assert "hash" not in body_str

    def test_reset_password_short_422(self, authed_client):
        r = authed_client.post("/api/v1/settings/users/hr_only/reset-password",
                               json={"new_password": "short"})
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "PASSWORD_TOO_SHORT"
```

#### Section C — Lockout rules (integration)

```python
class TestLockoutRulesIntegration:
    def test_cannot_deactivate_self(self, authed_client):
        r = authed_client.patch("/api/v1/settings/users/testadmin/status",
                                json={"is_active": False})
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "SELF_LOCKOUT_DEACTIVATION"

    def test_cannot_demote_self(self, authed_client):
        r = authed_client.patch("/api/v1/settings/users/testadmin/admin",
                                json={"is_admin": False})
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "SELF_LOCKOUT_DEMOTE"

    def test_last_admin_cannot_be_deactivated_by_second_admin(self, second_admin_client):
        """second_admin deactivates testadmin (the only other admin if count==2 → becomes 1 if second_admin deactivates self... wait)
        Correct scenario: second_admin tries to deactivate testadmin but first we need to confirm
        that testadmin is the only active admin at this point.
        Setup: second_admin exists (is_admin=True, is_active=True).
        But testadmin is also active admin → count == 2 → deactivation of testadmin is ALLOWED.
        To test LAST_ADMIN_PROTECTION: deactivate second_admin first (using authed_client / testadmin),
        then try to deactivate testadmin using second_admin_client while it's still active... 
        Actually this test scenario requires more careful orchestration.
        Simpler: use the unit test for last-admin protection (see §7.1).
        Integration test just confirms the full path works."""
        # See open question Q8 for test orchestration note.
        # The integration test is explicitly here to document the path — unit test covers logic.
        pass  # replaced by unit test in test_lockout_rules.py

    def test_non_admin_activate_blocked(self, hr_only_client):
        """hr_only cannot reach the endpoint at all — 403 (auth matrix covers this)."""
        r = hr_only_client.patch("/api/v1/settings/users/testadmin/status",
                                 json={"is_active": False})
        assert r.status_code == 403
```

#### Section D — Sidebar visibility and settings page

```python
class TestSettingsSidebarAndPage:
    def test_admin_sees_settings_link(self, authed_client):
        r = authed_client.get("/dashboard")
        assert r.status_code == 200
        assert 'href="/settings"' in r.text

    def test_non_admin_no_settings_link(self, hr_only_client):
        r = hr_only_client.get("/hr/dashboard")
        assert r.status_code == 200
        assert 'href="/settings"' not in r.text

    def test_settings_page_200_for_admin(self, authed_client):
        r = authed_client.get("/settings")
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")

    def test_settings_page_403_for_non_admin(self, hr_only_client):
        r = hr_only_client.get("/settings", follow_redirects=False)
        assert r.status_code == 403

    def test_settings_page_302_for_unauthed(self):
        with TestClient(app, follow_redirects=False) as c:
            r = c.get("/settings")
        assert r.status_code == 302
        assert "/login" in r.headers.get("location", "")

    def test_settings_page_contains_alpine_app(self, authed_client):
        r = authed_client.get("/settings")
        assert "settingsApp()" in r.text
```

### 7.3 New Fixture Needed: `second_admin_client`

Add to `tests/integration/conftest.py`:

```python
@pytest.fixture(scope="module")
def second_admin_client():
    """TestClient authenticated as a second admin. Used for last-admin protection tests."""
    with TestClient(app) as c:
        _ensure_user("second_admin", ["*"], is_admin=True)
        r = c.post("/login",
                   data={"username": "second_admin", "password": "testpass"},
                   follow_redirects=False)
        assert r.status_code == 303, f"second_admin login failed: {r.status_code}"
        yield c
```

### 7.4 Existing Suite Green-Keeping

**A3 integration tests (`test_rbac.py`):** Unaffected. `require_admin_*` guards are independent of `require_module_*`. `testadmin` has `is_admin=True` — A3 module guards don't check `is_admin`. All 5 A3 test classes stay green.

**Unit router tests (7 files with mock_repo):** The mock `_TESTADMIN_RECORD` already has `is_admin=True`. The new `require_admin_*` guards check `user.is_admin` on the same mock. The mock returns `UserRecord(is_admin=True)` so the guard passes. All existing 401 and 200 assertions stay green. Unit tests for settings-specific routes use a separate lightweight setup (§7.1).

**Pre-existing 4 CRM unit failures:** Out of scope, not touched.

### 7.5 Full i18n Key List for Phase B

Both `frontend/translations/en.json` and `frontend/translations/ar.json` need these keys added. Keys not already present (confirmed: `"Settings"` key already exists in both files):

| Key | English | Arabic |
|-----|---------|--------|
| `settings.page_title` | `User Management` | `إدارة المستخدمين` |
| `settings.add_user` | `Add User` | `إضافة مستخدم` |
| `settings.table.username` | `Username` | `اسم المستخدم` |
| `settings.table.status` | `Status` | `الحالة` |
| `settings.table.admin` | `Admin` | `مسؤول` |
| `settings.table.modules` | `Modules` | `الوحدات` |
| `settings.table.created` | `Created` | `تاريخ الإنشاء` |
| `settings.table.actions` | `Actions` | `الإجراءات` |
| `settings.status.active` | `Active` | `نشط` |
| `settings.status.inactive` | `Inactive` | `غير نشط` |
| `settings.action.edit_modules` | `Edit Modules` | `تعديل الوحدات` |
| `settings.action.deactivate` | `Deactivate` | `تعطيل` |
| `settings.action.activate` | `Activate` | `تفعيل` |
| `settings.action.make_admin` | `Make Admin` | `جعله مسؤولاً` |
| `settings.action.revoke_admin` | `Revoke Admin` | `إلغاء صلاحية الإدارة` |
| `settings.action.reset_password` | `Reset Password` | `إعادة تعيين كلمة المرور` |
| `settings.modal.create_title` | `Create User` | `إنشاء مستخدم` |
| `settings.modal.edit_modules_title` | `Edit Modules` | `تعديل الوحدات` |
| `settings.modal.reset_password_title` | `Reset Password` | `إعادة تعيين كلمة المرور` |
| `settings.form.username` | `Username` | `اسم المستخدم` |
| `settings.form.password` | `Password` | `كلمة المرور` |
| `settings.form.confirm_password` | `Confirm Password` | `تأكيد كلمة المرور` |
| `settings.form.is_admin` | `Admin user` | `مستخدم مسؤول` |
| `settings.form.modules_label` | `Module Access` | `صلاحيات الوحدات` |
| `settings.form.all_modules` | `All modules (*)` | `جميع الوحدات (*)` |
| `settings.form.new_password` | `New Password` | `كلمة المرور الجديدة` |
| `settings.form.confirm_new_password` | `Confirm New Password` | `تأكيد كلمة المرور الجديدة` |
| `settings.form.submit_create` | `Create User` | `إنشاء المستخدم` |
| `settings.form.submit_save` | `Save` | `حفظ` |
| `settings.form.cancel` | `Cancel` | `إلغاء` |
| `settings.no_users` | `No users found.` | `لا توجد مستخدمون.` |
| `settings.all_modules_badge` | `ALL` | `الكل` |
| `settings.error.password_mismatch` | `Passwords do not match.` | `كلمتا المرور غير متطابقتين.` |
| `settings.error.password_too_short` | `Password must be at least 8 characters.` | `يجب أن تكون كلمة المرور 8 أحرف على الأقل.` |
| `settings.error.username_invalid` | `Username must be 2–64 characters (letters, digits, . _ @ -).` | `اسم المستخدم يجب أن يكون 2–64 حرفاً من الأحرف والأرقام والنقطة والشرطة والشرطة السفلية و @.` |
| `settings.error.username_exists` | `Username already exists.` | `اسم المستخدم موجود بالفعل.` |
| `settings.error.invalid_module` | `Invalid module name.` | `اسم وحدة غير صالح.` |
| `settings.error.self_lockout_deactivation` | `You cannot deactivate your own account.` | `لا يمكنك تعطيل حسابك.` |
| `settings.error.self_lockout_demote` | `You cannot remove your own admin role.` | `لا يمكنك إزالة صلاحية الإدارة من حسابك.` |
| `settings.error.last_admin` | `Cannot remove the last active admin account.` | `لا يمكن إزالة المسؤول الأخير النشط.` |

---

## 8. Proposed AUTH_RBAC_DECISIONS.md Entries

```markdown
## B — Settings UI (Admin User Management)

**Implemented:** [TBD]

### B.D1 — Admin Guard Design

Two non-factory dependencies added to `backend/api/deps.py`:
- `require_admin_api(request, username=Depends(get_current_user)) -> None`
  Raises `HTTPException(403, detail={"code": "ADMIN_ACCESS_DENIED"})` if `not user.is_admin`.
  Used on all `/api/v1/settings/*` routes via `dependencies=_admin` at `include_router` level.
- `require_admin_html(request, username=Depends(get_current_user_html)) -> None`
  Raises `HTTPException(403)` if `not user.is_admin`. The existing global 403 handler renders
  `403.html` for browser requests — no new handler needed.

Unlike `require_module_api/html` factories, these are plain dependency functions (no parameter).
One `user_repo.get_user(username)` SQLite read per guarded request — same cost profile as A3.

`is_admin` is INDEPENDENT of `modules` (A1.D3): an admin with `modules=[]` passes all admin
guards but is still blocked on module-gated data routes.

### B.D2 — Settings API

New file `backend/api/v1/endpoints/settings.py`. Registered in `router.py`:
`api_v1_router.include_router(settings_router, prefix="/settings", dependencies=_admin)`

Six endpoints:
- `GET    /api/v1/settings/users`                    — list all users (no password_hash)
- `POST   /api/v1/settings/users`                    — create user
- `PATCH  /api/v1/settings/users/{u}/modules`        — update module list
- `PATCH  /api/v1/settings/users/{u}/status`         — activate/deactivate
- `PATCH  /api/v1/settings/users/{u}/admin`          — grant/revoke is_admin
- `POST   /api/v1/settings/users/{u}/reset-password` — reset password

All responses use the project's standard envelope (`{"ok":true,"data":{...}}` or
`{"ok":false,"error":{...}}`). `_error_response` extracted to `backend/core/responses.py`
to avoid circular imports between `main.py` and `settings.py`.
Password hashes NEVER returned in any response.

### B.D3 — Self-Lockout Protection

Enforced at the API layer (`settings.py` handlers), not the repository layer.

Four rules:
- L1: Admin cannot deactivate themselves → 422 `SELF_LOCKOUT_DEACTIVATION`.
- L2: Admin cannot revoke their own `is_admin` → 422 `SELF_LOCKOUT_DEMOTE`.
- L3: Last active admin cannot be deactivated → 422 `LAST_ADMIN_PROTECTION`.
- L4: Last active admin cannot be demoted → 422 `LAST_ADMIN_PROTECTION`.

L1/L2 are pure string comparisons (no DB read). L3/L4 use `repo.count_active_admins()` (one SQL
COUNT). SQLite's `threading.Lock` serialises concurrent demote attempts — no race condition.

### B.D4 — Password Rules

Minimum 8 characters. Server-side validation → 422 `PASSWORD_TOO_SHORT`.
"Confirm password" (type twice) is Alpine.js client-side only; the API receives a single
`new_password`. No password is returned in any response or written to logs.

### B.D5 — Settings Page

Route: `GET /settings` in `dashboard.py`, gated by `require_admin_html`.
Template: `frontend/templates/settings.html` (extends `base.html`, uses `{% block content %}`
and `{% block extra_scripts %}`).
Alpine.js component `settingsApp()` loads user data from `GET /api/v1/settings/users` on init.
Inline actions (toggle status, toggle admin) need no modal.
Modal actions: create user, edit modules, reset password.
Zero server-side rendering of user data — all from the JSON API.

### B.D6 — Sidebar and `_base_ctx`

`_base_ctx` adds `"is_admin": bool` to Jinja2 context. Zero additional DB calls — `_user_record`
was already fetched by existing code; `is_admin` is another field on the same object.
Both desktop and mobile sidebars in `base.html` wrap the Settings link in `{% if is_admin %}`.
The Settings label uses the existing `"Settings"` i18n key (already in en.json and ar.json).

### B.D7 — Repository Addition

`count_active_admins() -> int` added to both `UserRepository` Protocol and `SQLiteUserRepository`.
Executes `SELECT COUNT(*) FROM users WHERE is_admin=1 AND is_active=1`.
This is the ONLY new repository method. `update_user` already covers all field mutations via
keyword args; no separate `update_password`, `set_active`, or `set_admin` methods are needed.

### ~~B.D8 — CORSMiddleware~~ (DROPPED per C1 — same-origin; no CORS widening needed)

### B.D9 — Module Whitelist and Username Validation

`_VALID_MODULES = frozenset({"crm","hr","collections","customer_accounts","*"})` in `settings.py`.
Any unknown module in a POST/PATCH body → 422 `INVALID_MODULE`.
Username regex: `^[A-Za-z0-9._@\-]{2,64}$` — allows `@` (seed username may be an email address).
Violation → 422 `INVALID_USERNAME`.

### B.D10 — `_error_response` Extraction

`backend/core/responses.py` (new file) contains `error_response(request, status_code, code,
message, details=None) -> JSONResponse`. Imported by `main.py` (replaces the private
`_error_response` function there) and by `settings.py`. Eliminates the circular-import risk of
importing `_error_response` from `main.py` into `settings.py`.
```

---

## 9. Commit Structure + Risks + Open Questions

### 9.1 Commit Structure

All commits must be independently green. Implementation and the unit tests that make it testable
are NEVER split across commits (A3.D8 pattern).

---

**Commit 1 — Infrastructure (guards, repo, context, sidebar, i18n, stub route)**

Files changed:
- `backend/core/responses.py` — NEW: `error_response` helper extracted from `main.py`
- `backend/main.py` — import from `core.responses` (no CORS change per C1)
- `backend/auth/repository.py` — add `count_active_admins()` to Protocol + `SQLiteUserRepository`
- `backend/api/deps.py` — add `require_admin_api`, `require_admin_html`
- `backend/api/v1/endpoints/dashboard.py` — add `is_admin` to `_base_ctx`; add `/settings` route (renders `settings.html`)
- `frontend/templates/base.html` — add Settings link in desktop + mobile `<nav>` (both `{% if is_admin %}`)
- `frontend/templates/settings.html` — NEW: stub that extends `base.html`, `{% block content %}` with placeholder text (full Alpine component in Commit 3)
- `frontend/translations/en.json` — add all `settings.*` keys from §7.5
- `frontend/translations/ar.json` — same keys in Arabic
- `tests/unit/auth/test_settings_guards.py` — NEW: 8 guard unit tests
- `tests/unit/auth/test_user_store.py` — add `test_count_active_admins_*` tests

Green after Commit 1: existing tests pass; `/settings` returns 200 for admin (stub page);
403 for non-admin; 302 for unauth; Settings link visible only to admin in sidebar.

Message: `feat(auth): B1 — admin guards, count_active_admins, _base_ctx is_admin, settings route stub, sidebar link, i18n keys`

---

**Commit 2 — Settings API + lockout logic**

Files changed:
- `backend/api/v1/endpoints/settings.py` — NEW: 6 endpoints with lockout enforcement
- `backend/api/v1/router.py` — add `settings_router` include with `_admin` dependency list
- `tests/unit/auth/test_lockout_rules.py` — NEW: 11 lockout unit tests
- `tests/integration/conftest.py` — add `second_admin_client` fixture; add `_ensure_user("b_test_create", ...)` cleanup if needed
- `tests/integration/test_settings_api.py` — NEW: Sections A + B + C + D (auth matrix, CRUD, lockout, sidebar)

Green after Commit 2: full API tested; all lockout rules covered; auth matrix green.

Message: `feat(auth): B2 — settings API endpoints (list/create/modules/status/admin/reset-password), lockout protection; full integration tests`

---

**Commit 3 — Full settings page Alpine.js template**

Files changed:
- `frontend/templates/settings.html` — replace stub with full Alpine.js component (§4.2 + §4.3)

Green after Commit 3: Full Phase B complete. All tests stay green (integration tests that assert `"settingsApp()" in r.text` now pass).

Message: `feat(auth): B3 — settings page Alpine.js UI; user management fully browser-driven`

---

### 9.2 Risks

| # | Risk | Severity | Mitigation |
|---|------|----------|------------|
| R2 | **`_error_response` extraction circular import** — If `main.py` imports from `core.responses` and `settings.py` also imports from `core.responses`, and `core.responses` imports anything from `main.py` — circular. The extraction must be clean (only stdlib + FastAPI in `core/responses.py`). | LOW | `core/responses.py` imports only `fastapi.Request`, `fastapi.responses.JSONResponse`, `datetime`, `typing`. No circular dependency possible. |
| R3 | **`_base_ctx` `is_admin` in templates that don't check it** — Adding `is_admin` to the context dict is purely additive. Jinja2 templates that don't reference `is_admin` ignore it silently. No existing template uses `is_admin` today. | LOW | No breakage. All 5 existing HTML routes call `_base_ctx`; their templates extend `base.html` which now uses `{% if is_admin %}` for the Settings link. Works correctly — non-admin users get `is_admin=False`. |
| R4 | **`second_admin_client` fixture lifecycle and test isolation** — The `second_admin_client` creates `second_admin` in the test DB. If `tests/conftest.py` wipes the DB at module import time before this fixture runs, the user won't exist. Session-scoped `_ensure_user` calls in each fixture's `with TestClient(app) as c:` block (triggered after lifespan) handle ordering. | LOW | Mirror the `hr_only_client` fixture pattern exactly (call `_ensure_user` inside the `with TestClient(app) as c:` block, which runs lifespan first). Verify on first run with `pytest tests/integration/test_settings_api.py -v`. |
| R5 | **No hard-delete endpoint — potential surprise** — An admin who wants to fully remove a test user cannot do so from the browser. They must deactivate. The CLI also lacks a delete subcommand. | LOW | Document in B.D2: deactivation is the soft-delete path. Hard-delete requires direct DB access or a future CLI extension (`manage_users.py delete`). See Q7 for whether to add a hard-delete endpoint. |
| R6 | **`fmtDate` locale fallback** — `new Date(iso).toLocaleDateString('ar')` requires the browser to support the `ar` locale for `Intl.DateTimeFormat`. Most modern browsers do. Older embedded browsers on intranet kiosks may not. Graceful fallback: `toLocaleDateString()` without locale arg. | LOW | Acceptable for this use case. Date display is informational only. Add a try/catch fallback if Khaled reports date rendering issues on specific browsers. |
| R7 | **Alpine.js error code mapping may grow stale** — The `_mapError` function in the Alpine component maps backend error codes to i18n keys. If a new error code is added to the backend without updating the frontend map, the fallback (`b.error.message`) is shown — which is an English string. | LOW | The backend error messages are short and clear in English. The fallback is acceptable. Document that any new error code added to `settings.py` must also be added to `_mapError`. |
| R8 | **PATCH `/status` on an already-active user** — Sending `is_active=true` on a user who is already active is idempotent (`update_user` executes the SQL UPDATE with the same value). No error thrown. This is correct behaviour but could be confusing if the UI's `loadUsers()` call shows a brief stale state. The `loadUsers()` call after each action reloads fresh state. | LOW | Idempotency is a feature, not a bug. The Alpine state always reflects server state after `loadUsers()`. |

### 9.3 Open Questions for Khaled

**Q1 — Deactivation: "on next request" vs. immediate session revocation**
Phase B enforces deactivation on the target's next request (A2.D1 — `_resolve_active_username`
checks `is_active` on every request). This matches the A3 module-change behavior (A3.Q4).
For a user who is currently logged in and browsing, they will be kicked out on the next page
load or API call after deactivation.
**If immediate revocation is required** (force logout right now): the simplest approach is a
server-side session blocklist table in the users.db (`blocked_sessions` with `username + blocked_at`).
This is a significant architectural change. Confirm whether "on next request" is acceptable.

**Q2 — Toggle admin flag from the UI**
The locked decisions (decision 3) explicitly include `is_admin` in the users table display but do
not explicitly list "toggle admin" as a settings capability. The plan includes `PATCH /admin` and
a "Make Admin"/"Revoke Admin" button because:
(a) `Create user` has an `is_admin` toggle — it would be inconsistent to set it only at creation.
(b) The spirit of the mission is to replace `manage_users.py` entirely.
Confirm: should the `PATCH /admin` endpoint and toggle buttons be included?

**Q3 — Create user with `modules=[]`**
An admin can create a user with no modules assigned. That user will land on `/no-modules` after
login. The plan allows this. If you prefer to enforce "at least one module OR `*`" at creation,
add a `422 NO_MODULES_REQUIRED` error for `modules=[]` and `is_admin=False` on the create
endpoint. Proposed: allow `modules=[]` — the `/no-modules` page handles it gracefully and the
admin can assign modules later.

**Q4 — Username regex: `@` character**
The current seed username may be an email address (e.g. `khaled.elmasry@laverde-eg.com`).
The plan uses `^[A-Za-z0-9._@\-]{2,64}$` to allow `@`. For new users created via the settings UI,
should usernames be restricted to non-email format (alphanumeric + `.` `_` `-` only, no `@`),
while the existing email-format admin username continues to work?
Proposed: allow `@` in the regex so the validation is consistent for existing and new users.

**Q5 — Module change UX: show "next request" notice**
When an admin edits a user's modules and saves, the change takes effect on the target's next
request. Should the Edit Modules modal show a tooltip or notice: "Changes take effect on the
user's next page load"? Adds one i18n key (`settings.info.modules_next_request`). Proposed:
omit the notice (it adds complexity; the behavior is acceptable and consistent with A3).

**Q6 — Admin editing another admin's modules**
An admin CAN change another admin's module list (the lockout rules only protect `is_active` and
`is_admin` status, not `modules`). An admin with `modules=[]` still passes `require_admin_*`
guards — `is_admin` is independent of `modules` (A1.D3). Is this the correct behavior, or should
the "Edit Modules" button be hidden for users where `is_admin=true`?
Proposed: allow module editing for admin users — consistent with A1.D3 separation of concerns.

**Q7 — Hard-delete endpoint**
The plan intentionally omits `DELETE /api/v1/settings/users/{username}`. The only removal path
is deactivation (soft-delete). If you need a hard-delete for cleanup purposes, it can be added in
Commit 2 with a confirmation body (`{"confirm": true}`). The `delete_user` method already exists
in the repository. Proposed: exclude from Phase B; add if explicitly requested.

**Q8 — Last-admin integration test orchestration**
The last-admin protection rule (L3/L4) is fully covered by unit tests (§7.1). The integration test
(§7.2 Section C) is awkward to write because `testadmin` is always the "first" admin and tests
run in sequence. The cleanest approach: the integration test creates a user, makes them admin,
then tries to demote testadmin — at that point count == 2, so demotion is ALLOWED. To test the
LAST_ADMIN_PROTECTION path in integration: create a second admin, then deactivate testadmin using
the `second_admin_client` — but that leaves the test DB in a broken state for subsequent tests.
Proposed: rely on unit tests for the last-admin boundary condition; the integration test just
confirms the L1/L2 self-lockout rules (which are simpler to test without state mutation).
Confirm if a full last-admin integration test is required or if unit coverage is sufficient.
```
