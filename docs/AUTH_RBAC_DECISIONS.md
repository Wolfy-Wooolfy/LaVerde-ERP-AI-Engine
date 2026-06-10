# Auth + RBAC Decision Log

> Append-only. Each stage records its decisions here so future stages have the full context.

---

## A1 — User Store (Storage Foundation)

### A1.D1 — Store Choice: SQLite via stdlib `sqlite3`

SQLite chosen over PostgreSQL/Redis/in-memory dict:
- Zero new infrastructure — runs anywhere the app runs.
- Survives process restarts (persistent on disk).
- Standard library driver (`sqlite3`) — no extra dep beyond bcrypt.
- Adequate for O(100) users (small internal tool).
- DB path is configurable via `USER_DB_PATH` env var (default `data/users.db`).
- The `.db` file is gitignored (`data/*.db`) and is never committed.

### A1.D2 — Schema

Table `users`:

| Column | Type | Constraint |
|---|---|---|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT |
| `username` | TEXT | NOT NULL UNIQUE |
| `password_hash` | TEXT | NOT NULL |
| `modules` | TEXT | NOT NULL (JSON array) |
| `is_admin` | INTEGER | NOT NULL DEFAULT 0 (bool as 0/1) |
| `is_active` | INTEGER | NOT NULL DEFAULT 1 (bool as 0/1) |
| `created_at` | TEXT | NOT NULL (ISO-8601 UTC) |
| `updated_at` | TEXT | NOT NULL (ISO-8601 UTC) |

`modules` stores a JSON list: `["crm","hr"]` or `["*"]` (wildcard = all modules).

### A1.D3 — `is_admin` vs `modules` Separation

`is_admin` and `modules` are **independent** axes:

- `modules` controls **data visibility** (which module KPIs/dashboards are accessible).
  `["*"]` is a sentinel meaning "all current and future modules."
- `is_admin` controls **user management** and the Settings panel **only**.
  It does not grant broader data access.

Example: a Chairman has `modules=["*"], is_admin=False` — sees all data, cannot manage
users. An IT admin has `modules=["hr"], is_admin=True` — manages users, sees only HR data.

### A1.D4 — Password Hashing: `bcrypt`

- `bcrypt` package added to `requirements.txt` (lightweight C extension, ~thin wrapper).
  `passlib` was not chosen — heavier dep not needed at this stage.
  `hashlib.scrypt` (stdlib) was available but bcrypt was specified by design.
- Default work factor: bcrypt gensalt default (12 rounds). Tunable in future via
  `bcrypt.gensalt(rounds=N)` if cost needs adjusting.
- `bcrypt.checkpw` is inherently constant-time — no additional compare_digest needed.
- `verify_password` on the repository is a **pure hash check**. It does not enforce
  `is_active` — that is an A3 (RBAC enforcement) concern, not a storage concern.

### A1.D5 — Seed Bootstrap

- On `lifespan` startup (not in the auth path): if `users` table is empty, seed one
  admin user from `BASIC_AUTH_USERNAME` + hashed `BASIC_AUTH_PASSWORD`, with
  `modules=["*"]` and `is_admin=True`.
- Idempotent: if any user already exists, the seed is skipped unconditionally.
- The existing `verify_credentials` / `get_current_user` HTTP Basic Auth path is
  **untouched**. App behavior is identical to pre-A1 at runtime.

### A1.D6 — `UserRepository` Interface

- Defined as `typing.Protocol` (structural typing). The `SQLiteUserRepository` satisfies
  it without inheriting from it — allows a future backend swap (PostgreSQL, etc.)
  with zero changes to callers.
- Methods: `create_user`, `get_user`, `list_users`, `update_user`, `delete_user`,
  `verify_password`.
- `update_user` uses keyword-only arguments with `None` defaults; raises `KeyError` if
  the username is not found, `ValueError` if called with no fields to update.
- `create_user` raises `ValueError` if the username already exists (wraps
  `sqlite3.IntegrityError`).

---

## A2 — Session-Cookie Authentication

### A2.D1 — SessionMiddleware

Starlette's built-in `SessionMiddleware` (zero extra deps — Starlette is already a FastAPI
transitive dependency). Session payload: `{"username": <str>}` only. Full `UserRecord`
is resolved from `user_repo` on every authenticated request to propagate deactivation
immediately without session invalidation.

Placement in middleware stack (outermost → innermost):
```
request_id_middleware → security_headers_middleware → SessionMiddleware → CORSMiddleware → routes
```
CORS still handles OPTIONS before session parsing runs.

### A2.D2 — Cookie Flags

`HttpOnly=True` (Starlette default), `SameSite=lax`, `Secure=True` iff
`ENVIRONMENT=="production"`, `max_age=28800` (8 h — one work session).
`SameSite=lax` chosen over `strict` to allow intranet link-following without forcing
re-login. Sub-resource cross-origin requests (AJAX) don't carry lax cookies → adequate
CSRF protection for the API surface.

### A2.D3 — Dual Unauthenticated Behaviour

Two FastAPI dependencies, both returning `str` (username) on success:

- `get_current_user(request)` → `str` or raises HTTP 401.  
  Used on all `/api/v1/*` routes. Keeps the hotfix-era 401 guarantees intact.
- `get_current_user_html(request)` → `str` or raises HTTP 302 to `/login?next=<path>`.  
  Used on all HTML page routes in `dashboard.py`.

The 23 API endpoint files' `user: str = Depends(get_current_user)` annotations are
**unchanged** — Amendment A1. Full `UserRecord` / `modules` exposure is deferred to A3.

No global auth middleware — per-route `Depends` pattern retained from A1.

### A2.D4 — /login, /logout Route Placement

New file `backend/api/v1/endpoints/auth.py`, included via `app.include_router(auth_router)`
with no prefix. Routes are `GET /login`, `POST /login` (rate-limited to 10/min per IP),
`GET /logout`. The old inline `/logout` stub in `main.py` (401 + WWW-Authenticate) is
removed in Commit 2. Both `/login` and `/logout` are standalone HTML routes (not under
`/api/v1`).

### A2.D5 — SESSION_SECRET Handling

`SESSION_SECRET: str = ""` in `Settings`. Empty string allowed in `development`/`staging`
with a logger.warning (dev default passed to middleware). Required + ≥ 32 chars in
`production` — fail-fast at `Settings` instantiation so the process never starts.
Never committed; `.env.example` has an empty placeholder with a generation command.

### A2.D6 — Basic Auth Retirement

`backend/core/security.py` deleted in Commit 2. `HTTPBasic` and `verify_credentials`
removed from `backend/api/deps.py`. `BASIC_AUTH_USERNAME` / `BASIC_AUTH_PASSWORD` retained
in `Settings` as the A1 seed source only — they are no longer runtime auth credentials.
`WWW-Authenticate: Basic` header no longer sent on 401 responses.

### A2.D7 — Test Auth Strategy

Integration tests: shared `authed_client` fixture in `tests/integration/conftest.py`
that logs in via `POST /login` and carries the session cookie (`scope="module"` to
amortise bcrypt). Service mocking (dependency_overrides for `get_crm_service`) continues
per-file as before; it composes with the session cookie independently.

Unit router tests: `app.dependency_overrides[get_current_user] = lambda: "testadmin"`
pattern — faster, isolated, no DB dependency.

E2E (Playwright): login-form flow replacing Authorization header injection.

Test DB: `USER_DB_PATH=data/test-users.db` (dedicated temp file, deleted at session
start in `tests/conftest.py` so the seed always fires fresh). Real `data/users.db`
is never touched by the test suite. Not `:memory:` — the repository uses a single
persistent connection so `:memory:` would work, but a file gives a cleaner failure
message if something goes wrong.

---

## A3 — RBAC Enforcement

**Implemented:** 2026-06-09

### A3.D1 — require_module Design

Two dependency factories in `backend/api/deps.py`:
- `require_module_api(module_id: str)` — chains off `get_current_user` (which handles 401).
  Raises `HTTPException(403, detail={"code": "MODULE_ACCESS_DENIED", "module": module_id})`
  if `"*" not in user.modules and module_id not in user.modules`.
- `require_module_html(module_id: str)` — chains off `get_current_user_html` (which handles 302).
  Raises `HTTPException(403)` (no detail body; the global handler renders `403.html` for browsers).

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

Does not intercept `ReadOnlyViolationError` responses — that handler returns a `JSONResponse`
directly (not via `HTTPException`), so it is unaffected.

### A3.D4 — Sidebar Filtering

`_base_ctx` in `dashboard.py` resolves `UserRecord` from `user_repo` and adds
`allowed_modules` (the raw `user.modules` list, e.g. `["hr"]` or `["*"]`) to context.
Both the desktop and mobile sidebars in `base.html` wrap each active module link in
`{% if 'module_id' in allowed_modules or '*' in allowed_modules %}`.
"Coming Soon" stub entries (Customer Service, Contracts, Accounting, Project Mgmt) are
wrapped in `{% if '*' in allowed_modules %}` — visible only to wildcard (`["*"]`) users.

### A3.D5 — Post-Login Landing

`login_submit` checks the `next` param against `_PATH_MODULE_MAP`. If `next` differs from the
form default `/dashboard` and the user can access the path → redirect there. Else → redirect
to the user's first allowed module dashboard per `_ORDERED_MODULE_DASHBOARDS` (order:
crm → hr → collections → customer_accounts). If no modules → redirect to `/no-modules`.

### A3.D6 — No-Modules and Forbidden Pages

`/no-modules` — new route in `auth.py`, protected by `get_current_user_html` (no module guard),
renders `no_modules.html`. Reached only when login lands a user with `modules=[]`.
`403.html` — standalone template. Rendered by the global 403 handler for browser requests.
Neither template extends `base.html` (avoids context dependency on `allowed_modules`).
The 403 page has `← Previous` (history.back()) and Logout buttons (Q3 answer).

### A3.D7 — User Management CLI

`scripts/manage_users.py` — argparse CLI for managing users without direct DB access:
- `add <username> <password> [--modules m1,m2] [--admin]`
- `list` — tabular output of all users
- `set-modules <username> m1,m2`
- `deactivate <username>`
CLI calls `hash_password()` before passing to `create_user` (repository takes a hash, not plaintext).

### A3.D8 — Test Strategy

Commit 1 (implementation) includes unit fixture updates — never split implementation from the
unit fixes that make it testable. The 7 affected unit router test files (6 HR + 1 collections)
have their `client` fixture updated to inject a `MagicMock` `user_repo` into `app.state` with
`get_user.return_value = UserRecord(modules=["*"])`. The 401 path (unauthenticated) is unaffected.

`tests/unit/modules/customer_accounts/test_routes.py` did NOT need changes — it only has
`test_401_when_no_auth` with plain `TestClient(app)`. The 401 path raises before the module
guard runs, so `user_repo` is never accessed.

Commit 2 adds `tests/integration/test_rbac.py` with 5 test classes (API matrix, HTML matrix,
sidebar filtering, post-login landing, no-modules) and restricted-user fixtures in
`tests/integration/conftest.py`. The `_seed_rbac_test_users` fixture uses `hash_password()`
to create users — `create_user` takes a hash, never plaintext.

---

## B — Settings UI (Admin User Management)

**Implementing:** 2026-06-10

### B.D1 — Admin Guard Design

Two plain dependency functions (not factories) added to `backend/api/deps.py`:

- `require_admin_api(request, username=Depends(get_current_user)) -> None`
  Raises `HTTPException(403, detail={"code": "ADMIN_ACCESS_DENIED"})` if `not user.is_admin`.
  Used on all `/api/v1/settings/*` routes via `dependencies=_admin` at `include_router` level.

- `require_admin_html(request, username=Depends(get_current_user_html)) -> None`
  Raises `HTTPException(403)` if `not user.is_admin`. The existing global `@app.exception_handler(403)`
  renders `403.html` for browser requests — no new handler needed.

Unlike `require_module_api/html` factories (parameterised by `module_id`), these are plain
dependency functions — no parameter, no factory. One `user_repo.get_user(username)` SQLite read
per guarded request; same cost profile as A3.

`is_admin` is INDEPENDENT of `modules` (A1.D3). An admin with `modules=[]` passes all admin
guards but is still blocked on module-gated data routes.

### B.D2 — Settings API

New file `backend/api/v1/endpoints/settings.py`. Registered in `backend/api/v1/router.py`:
`api_v1_router.include_router(settings_router, prefix="/settings", dependencies=_admin)`

Six endpoints:
- `GET    /api/v1/settings/users`                    — list all users (no password_hash in response)
- `POST   /api/v1/settings/users`                    — create user
- `PATCH  /api/v1/settings/users/{u}/modules`        — replace module list
- `PATCH  /api/v1/settings/users/{u}/status`         — activate/deactivate
- `PATCH  /api/v1/settings/users/{u}/admin`          — grant/revoke is_admin
- `POST   /api/v1/settings/users/{u}/reset-password` — reset password

All responses use the project standard envelope (`{"ok":true,"data":{...}}` or
`{"ok":false,"error":{...}}`). `_error_response` extracted to `backend/core/responses.py`
to avoid circular imports between `main.py` and `settings.py`.
Password hashes NEVER returned in any response. No hard-delete endpoint — deactivation is the
soft-delete path. Hard-delete requires direct DB access or a future CLI extension.

### B.D3 — Self-Lockout Protection

Enforced at the API layer (`settings.py` handlers), not the repository layer.

Four rules:
- L1: Admin cannot deactivate themselves → 422 `SELF_LOCKOUT_DEACTIVATION`.
- L2: Admin cannot revoke their own `is_admin` → 422 `SELF_LOCKOUT_DEMOTE`.
- L3: Last active admin cannot be deactivated → 422 `LAST_ADMIN_PROTECTION`.
- L4: Last active admin cannot be demoted → 422 `LAST_ADMIN_PROTECTION`.

L1/L2 are pure string comparisons (no DB read). L3/L4 use `repo.count_active_admins()` (one SQL
COUNT). SQLite's `threading.Lock` serialises concurrent demote attempts — no race condition.
L3/L4 boundary covered by unit tests; integration suite covers L1/L2.

### B.D4 — Password Rules

Minimum 8 characters. Server-side validation → 422 `PASSWORD_TOO_SHORT`.
"Confirm password" (type twice) is Alpine.js client-side only; the API receives a single
`new_password`. No password is returned in any response or written to logs at any point.

### B.D5 — Settings Page

Route: `GET /settings` in `dashboard.py`, gated by `require_admin_html`.
Template: `frontend/templates/settings.html` (extends `base.html`, uses `{% block content %}`
and `{% block extra_scripts %}`).
Alpine.js component `settingsApp()` loads user data from `GET /api/v1/settings/users` on init.
Inline actions (toggle status, toggle admin) need no modal.
Modal actions: create user, edit modules, reset password.

### B.D6 — Sidebar and `_base_ctx`

`_base_ctx` adds `"is_admin": bool` to Jinja2 context. Zero additional DB calls — `_user_record`
was already fetched by existing code; `is_admin` is another field on the same object.
Both desktop and mobile sidebars in `base.html` wrap the Settings link in `{% if is_admin %}`.
The Settings label uses the existing `"Settings"` i18n key (already in en.json and ar.json).
`page == 'settings'` marks the Settings link active.

### B.D7 — Repository Addition

`count_active_admins() -> int` added to both `UserRepository` Protocol and `SQLiteUserRepository`.
Executes `SELECT COUNT(*) FROM users WHERE is_admin = 1 AND is_active = 1`.
This is the ONLY new repository method. `update_user` already covers all field mutations via
keyword args; no separate `update_password`, `set_active`, or `set_admin` methods are needed.

### B.D8 — CORSMiddleware (DROPPED per C1)

Settings UI is same-origin (Alpine `fetch()` to the same host). CORS governs cross-origin
requests only. `CORSMiddleware allow_methods` is NOT widened. POST /login already works under
GET-only CORS today — same principle applies to the settings API.

### B.D9 — Module Whitelist and Username Validation

`_VALID_MODULES = frozenset({"crm","hr","collections","customer_accounts","*"})` in `settings.py`.
Any unknown module in a POST/PATCH body → 422 `INVALID_MODULE`.
Username regex: `^[A-Za-z0-9._@\-]{2,64}$` — allows `@` (seed username may be email-format).
Violation → 422 `INVALID_USERNAME`.

### B.D10 — `_error_response` Extraction

`backend/core/responses.py` (new file) exposes `error_response(request, status_code, code,
message, details=None) -> JSONResponse`. Imported by `main.py` (via private `_error_response`
wrapper that delegates to it) and directly by `settings.py`. Eliminates the circular-import
risk of importing the private `_error_response` from `main.py` into `settings.py`.
