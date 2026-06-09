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
