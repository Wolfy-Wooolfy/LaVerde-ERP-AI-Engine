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
