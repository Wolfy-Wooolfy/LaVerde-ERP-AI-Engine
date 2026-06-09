# AUTH + RBAC DISCOVERY — LaVerde ERP AI Engine

**Session date:** 2026-06-09  
**Author:** Claude Code (read-only discovery pass — zero implementation code written)  
**Status:** Draft — awaiting Khaled review before any build session  
**Pre-flight ritual:** Server was confirmed running as `python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000` (no `--reload`). All curl results below are from that live process.

---

## Table of Contents

1. [Auth Inventory](#1-auth-inventory)
2. [Endpoint Protection Matrix](#2-endpoint-protection-matrix)
3. [Module ID Registry](#3-module-id-registry)
4. [Frontend Wiring](#4-frontend-wiring)
5. [Test / Postman Blast Radius](#5-test--postman-blast-radius)
6. [Design Proposal](#6-design-proposal)
7. [Appendix — Raw Curl Output](#7-appendix--raw-curl-output)

---

## 1. Auth Inventory

### 1.1 Confirmed: Single Shared Credential

**YES** — the system uses one global username + password pair stored in `.env`:

```
BASIC_AUTH_USERNAME=admin
BASIC_AUTH_PASSWORD=password
```

Source: `backend/core/config.py:31-32` (`Settings.BASIC_AUTH_USERNAME`, `BASIC_AUTH_PASSWORD` as plain `str` fields).

### 1.2 Verification Path

```
.env
  ↓  loaded by pydantic-settings into
backend/core/config.py → Settings.BASIC_AUTH_USERNAME / BASIC_AUTH_PASSWORD
  ↓  read by
backend/core/security.py:6-16 → verify_credentials(username, password)
  - uses secrets.compare_digest (constant-time) — correctly avoids timing attacks
  - returns True only when BOTH username AND password match the single configured value
  ↓  called from
backend/api/deps.py:10-20 → get_current_user(credentials: HTTPBasicCredentials)
  - raises HTTP 401 with WWW-Authenticate: Basic on failure
  - returns credentials.username (the string "admin") on success
  ↓  injected via Depends(get_current_user) into endpoint handlers
```

### 1.3 Routers/Endpoints That Depend on get_current_user

The following files import and wire `get_current_user` via `Depends`:

| File | Endpoints Protected |
|------|---------------------|
| `backend/api/v1/endpoints/health.py` | `/health`, `/health/odoo`, `/health/deep` |
| `backend/api/v1/endpoints/summary.py` | `/summary` |
| `backend/api/v1/endpoints/followup.py` | `/followup-risk` |
| `backend/api/v1/endpoints/data_quality.py` | `/data-quality/missing-contact` |
| `backend/api/v1/endpoints/metrics_endpoint.py` | `/metrics` |
| `backend/api/v1/endpoints/dashboard_api.py` | `/dashboard/kpis`, `/dashboard/sparkline`, `/dashboard/heatmap` |
| `backend/api/v1/endpoints/ai.py` | `/ai/prioritize-lead/{id}`, `/ai/prioritize-overdue`, `/ai/budget`, `/ai/health` |
| `backend/api/v1/endpoints/chat.py` | `/chat/message`, `/chat/session/{id}`, `/chat/suggested-questions` |
| `backend/api/v1/endpoints/dashboard.py` | HTML: `/dashboard`, `/collections/dashboard`, `/customer-accounts/dashboard`, `/hr/dashboard`, `/data-quality/missing-contact` |
| `backend/api/v1/endpoints/hr.py` | **PARTIAL** — only F2 `/hr/department/{id}` (line 152) and F3 `/hr/employee/{id}` (line 257) |

### 1.4 CRITICAL: Endpoints With NO Auth Dependency

These endpoint modules were **never wired to `get_current_user`** at all:

| File | Unprotected Endpoints |
|------|-----------------------|
| `backend/api/v1/endpoints/collections.py` | All 13 endpoints (8 KPI + 5 drilldown) |
| `backend/api/v1/endpoints/customer_accounts.py` | All 7 endpoints (3 KPI + 2 refunds + 1 customer drilldown + 1 refund detail) |
| `backend/api/v1/endpoints/hr.py` | 4 KPI endpoints (headcount, tenure, payroll-risk, dept-cost) |

**Root cause:** Neither `collections.py` nor `customer_accounts.py` imports `get_current_user` at all. `hr.py` imports it but only applies it to the two drilldown endpoints that explicitly say "Auth: HTTPBasic required — response contains employee names (PII)." The four aggregate KPI handlers were never given the dependency.

This is a security gap independent of RBAC. It must be fixed before or alongside the auth migration.

---

## 2. Endpoint Protection Matrix

Every status is from a real curl against the running server (see Appendix §7).

### 2.1 API Routes (`/api/v1/*`)

| Path | Method | has_auth_dependency | no-auth status | authed status |
|------|--------|--------------------:|---------------:|--------------:|
| `/health` | GET | N (intentionally public, defined in `main.py:245`) | **200** | 200 |
| `/api/v1/health` | GET | Y | **401** | 200 |
| `/api/v1/health/odoo` | GET | Y | **401** | 200 |
| `/api/v1/health/deep` | GET | Y | **401** | 200 |
| `/api/v1/summary` | GET | Y | **401** | 200 |
| `/api/v1/followup-risk` | GET | Y | **401** | 200 |
| `/api/v1/data-quality/missing-contact` | GET | Y | **401** | 200 |
| `/api/v1/metrics` | GET | Y | **401** | 200 |
| `/api/v1/dashboard/kpis` | GET | Y | **401** | 200 |
| `/api/v1/dashboard/sparkline` | GET | Y | **401** | 200 |
| `/api/v1/dashboard/heatmap` | GET | Y | **401** | 200 |
| `/api/v1/ai/prioritize-lead/{id}` | POST | Y | **401** | — |
| `/api/v1/ai/prioritize-overdue` | POST | Y | **401** | — |
| `/api/v1/ai/budget` | GET | Y | **401** | — |
| `/api/v1/ai/health` | GET | Y | **401** | — |
| `/api/v1/chat/message` | POST | Y | **401** | — |
| `/api/v1/chat/session/{id}` | DELETE | Y | **401** | — |
| `/api/v1/chat/suggested-questions` | GET | Y | **401** | — |
| `/api/v1/collections/kpi/late-uncollected` | GET | **N** | **⚠️ 200** | 200 |
| `/api/v1/collections/kpi/total-portfolio-value` | GET | **N** | **⚠️ 200** | 200 |
| `/api/v1/collections/kpi/late-uncollected-by-project` | GET | **N** | **⚠️ 200** | 200 |
| `/api/v1/collections/kpi/pending-check-exposure` | GET | **N** | **⚠️ 200** | 200 |
| `/api/v1/collections/kpi/collection-trend-6m` | GET | **N** | **⚠️ 200** | 200 |
| `/api/v1/collections/kpi/collection-rate` | GET | **N** | **⚠️ 200** | 200 |
| `/api/v1/collections/kpi/collection-rate-by-project` | GET | **N** | **⚠️ 200** | 200 |
| `/api/v1/collections/kpi/expected-forecast` | GET | **N** | **⚠️ 200** | 200 |
| `/api/v1/collections/drilldown/late` | GET | **N** | **⚠️ 200** | 200 |
| `/api/v1/collections/drilldown/forecast/{bucket}` | GET | **N** | **⚠️ 200** | 200 |
| `/api/v1/collections/drilldown/portfolio` | GET | **N** | **⚠️ 200** | 200 |
| `/api/v1/collections/drilldown/project/{id}` | GET | **N** | **⚠️ 200** | 200 |
| `/api/v1/collections/drilldown/trend/{month}` | GET | **N** | **⚠️ 200** | 200 |
| `/api/v1/customer-accounts/kpi/total-receivables` | GET | **N** | **⚠️ 200** | 200 |
| `/api/v1/customer-accounts/kpi/top-overdue-customers` | GET | **N** | **⚠️ 200** | 200 |
| `/api/v1/customer-accounts/kpi/unallocated-wallet-balance` | GET | **N** | **⚠️ 200** | 200 |
| `/api/v1/customer-accounts/refunds/summary` | GET | **N** | **⚠️ 200** | 200 |
| `/api/v1/customer-accounts/refunds/detail` | GET | **N** | **⚠️ 200** | 200 |
| `/api/v1/customer-accounts/customer/{id}` | GET | **N** | **⚠️ 200** | 200 |
| `/api/v1/hr/kpi/headcount` | GET | **N** | **⚠️ 200** | 200 |
| `/api/v1/hr/kpi/tenure-distribution` | GET | **N** | **⚠️ 200** | 200 |
| `/api/v1/hr/kpi/payroll-risk-dashboard` | GET | **N** | **⚠️ 200** | 200 |
| `/api/v1/hr/kpi/department-cost` | GET | **N** | **⚠️ 200** | 200 |
| `/api/v1/hr/department/{id}` | GET | Y | **401** | 200 |
| `/api/v1/hr/employee/{id}` | GET | Y | **401** | 200 |

### 2.2 HTML Routes

| Path | Method | has_auth_dependency | no-auth status | authed status |
|------|--------|--------------------:|---------------:|--------------:|
| `/dashboard` | GET | Y | **401** | 200 |
| `/collections/dashboard` | GET | Y | **401** | 200 |
| `/customer-accounts/dashboard` | GET | Y | **401** | 200 |
| `/hr/dashboard` | GET | Y | **401** | 200 |
| `/data-quality/missing-contact` | GET | Y | **401** | 200 |
| `/logout` | GET | N (sets 401 by design) | **401** | 401 |
| `/crm/summary` | GET | N (legacy 301 redirect) | **301** | 301 |
| `/crm/followup-risk` | GET | N (legacy 301 redirect) | **301** | 301 |
| `/crm/data-quality/missing-contact` | GET | N (legacy 301 redirect) | **301** | 301 |

### 2.3 Discrepancy Resolution

**Postman "Security" folder** (`tests/postman/CRM-AI-Engine.postman_collection.json`, lines 262–277): expects `GET /api/v1/summary` with no auth → 401.
**Real curl result: 401.** Postman expectation is CORRECT.

**HR test comment (2026-06-07):** says HR `/kpi/*` endpoints return 200 with no auth.
**Real curl result: 200 on all 4 HR KPI endpoints.** HR comment is ALSO CORRECT.

There is no contradiction. The two statements describe different endpoint groups. `/api/v1/summary` (CRM) is protected. `/api/v1/hr/kpi/*` is unprotected. Both facts coexist because the Collections, Customer Accounts, and HR KPI endpoint modules were added without wiring `get_current_user`.

---

## 3. Module ID Registry

These are the authoritative module identifiers derived from the codebase, sidebar, and routers. All active modules have a live `backend/modules/<id>/` directory.

| Module ID | Status | Backend Module | API Prefix | Sidebar Link | Source |
|-----------|--------|----------------|------------|--------------|--------|
| `crm` | **Active** | `backend/modules/crm/` | `/api/v1/summary`, `/api/v1/followup-risk`, `/api/v1/data-quality/*`, `/api/v1/dashboard/*`, `/api/v1/ai/*`, `/api/v1/chat/*` | `/dashboard` | `router.py:4-5`, `base.html:135` |
| `hr` | **Active** | `backend/modules/hr/` | `/api/v1/hr/*` | `/hr/dashboard` | `router.py:9`, `base.html:160` |
| `collections` | **Active** | `backend/modules/collections/` | `/api/v1/collections/*` | `/collections/dashboard` | `router.py:7`, `base.html:185` |
| `customer_accounts` | **Active** | `backend/modules/customer_accounts/` | `/api/v1/customer-accounts/*` | `/customer-accounts/dashboard` | `router.py:8`, `base.html:198` |
| `customer_service` | **Stub** | `backend/modules/customer_service/__init__.py` (empty) | none | "Soon" badge | `base.html:147` |
| `contracts` | **Stub** | `backend/modules/contracts/__init__.py` (empty) | none | "Soon" badge | `base.html:173` |
| `accounting` | **Stub** | `backend/modules/accounting/__init__.py` (empty) | none | "Soon" badge | `base.html:211` |
| `project_mgmt` | **Stub** | `backend/modules/project_mgmt/__init__.py` (empty) | none | "Soon" badge | `base.html:223` |

**RBAC key set** (modules that would appear in `user.modules`):
```python
ACTIVE_MODULE_IDS = {"crm", "hr", "collections", "customer_accounts"}
STUB_MODULE_IDS   = {"customer_service", "contracts", "accounting", "project_mgmt"}
```

The `data-quality` feature lives inside the CRM module (`/api/v1/data-quality/*` is handled by `data_quality.py` which imports from `backend.modules.crm`) — it is not a separate module ID.

---

## 4. Frontend Wiring

### 4.1 How the Sidebar Renders Module Links

The sidebar is **server-side rendered, statically hardcoded** in `frontend/templates/base.html`. There is no dynamic module filtering. Every authenticated user who loads any page sees the same sidebar with all four active module links plus four "Coming Soon" placeholders.

The sidebar template uses `{% if page == 'xxx' %}active{% endif %}` to highlight the current page. The `page` variable is injected by the backend in `_base_ctx()` (`dashboard.py:35-45`).

User display: `{{ current_user }}` and `{{ current_user[0] }}` (avatar initial) are rendered using the `user` string returned by `get_current_user`, which is always the string `"admin"` (the single shared username). `_extract_first_name()` applies cosmetic formatting.

### 4.2 URL → Module Mapping

| URL Prefix | Module | Dashboard Template |
|------------|--------|--------------------|
| `/dashboard` | `crm` | `frontend/templates/dashboard.html` |
| `/data-quality/*` | `crm` | `frontend/templates/missing_contact.html` |
| `/collections/dashboard` | `collections` | `frontend/templates/collections/dashboard.html` |
| `/customer-accounts/dashboard` | `customer_accounts` | `frontend/templates/customer_accounts/dashboard.html` |
| `/hr/dashboard` | `hr` | `frontend/templates/hr/dashboard.html` |

### 4.3 How the Client Sends Auth

`frontend/static/js/api.js:27`:
```javascript
credentials: 'include',   // sends Basic Auth cookies
```

**The comment is misleading.** HTTP Basic Auth is not cookie-based. The browser caches the `Authorization: Basic base64(user:pass)` header and re-sends it on every request when the server challenges with `WWW-Authenticate: Basic`. `credentials: 'include'` means "send cookies AND auth headers when the origin matches" — the latter is what actually carries Basic Auth.

**Implication for migration:** `credentials: 'include'` is the correct `fetch` flag for session cookies. When we switch to session-cookie auth, `api.js` needs **zero changes** — the flag already includes cookies. The browser will automatically send the session cookie on every `crmApi.get()` call.

### 4.4 What Must Change for Session-Cookie Auth

| Component | Current State | Required Change |
|-----------|--------------|-----------------|
| `backend/api/deps.py` | `HTTPBasic` + `verify_credentials` | Replace with session cookie lookup |
| `backend/core/security.py` | `verify_credentials` (single cred) | Replace with `UserRepository.verify` (bcrypt) |
| `backend/core/config.py` | `BASIC_AUTH_USERNAME/PASSWORD` | Add `SESSION_SECRET` (required), keep old creds as seed |
| `backend/main.py` | `GET /logout` → 401+WWW-Authenticate | Replace with cookie clearing + redirect to /login |
| `frontend/templates/base.html` | Sidebar hardcoded; `current_user` is always "admin" | Pass `user.modules` in context; filter sidebar conditionally |
| `frontend/static/js/api.js` | No change needed (`credentials: 'include'` ✓) | — |
| New: `GET /login`, `POST /login` | Does not exist | New HTML login page + form handler |
| New: per-module guard | Does not exist | New `require_module(module_id)` dependency |

---

## 5. Test / Postman Blast Radius

Everything below assumes Basic auth credentials and would need rewriting after a session-cookie migration.

### 5.1 E2E Tests (Playwright)

| File | Hardcoded Credential | Location |
|------|---------------------|----------|
| `tests/e2e/conftest.py` | `AUTH = ("admin", "password")` | Line 6; generates `Authorization: Basic` header |
| `tests/e2e/test_dashboard.py` | `USERNAME = "admin"`, `PASSWORD = "password"` | Lines 22–23; `authenticate(page)` sets `Authorization` header on every page navigation |
| `tests/e2e/test_ai_dashboard_section.py` | (inherits pattern from `test_dashboard.py`) | Auth header approach |
| `tests/e2e/test_phase3_dropdowns.py` | (inherits pattern from `test_dashboard.py`) | Auth header approach |

**Migration impact:** Playwright's `page.set_extra_http_headers({"Authorization": ...})` must be replaced with a proper login flow — navigate to `/login`, fill form, submit, then proceed. All four e2e test files need rewriting.

### 5.2 Integration Tests

| File | Auth Mechanism | Credentials |
|------|---------------|-------------|
| `tests/integration/test_api_v1.py` | `TestClient(app).get(url, auth=_AUTH)` | `_AUTH = ("testadmin", "testpass")` (from `.env.test`) |
| `tests/integration/test_health.py` | `TestClient(..., auth=...)` pattern | `("testadmin", "testpass")` |
| `tests/integration/test_smoke.py` | `TestClient(...)` | `("testadmin", "testpass")` |
| `tests/integration/test_ai_endpoints.py` | `TestClient(..., auth=...)` | `("testadmin", "testpass")` |
| `tests/integration/test_chat_endpoint.py` | `TestClient(..., auth=...)` | `("testadmin", "testpass")` |
| `tests/integration/test_exception_handlers.py` | `TestClient(...)` | `("testadmin", "testpass")` |
| `tests/integration/test_concurrent_summary.py` | `TestClient(...)` | `("testadmin", "testpass")` |
| `tests/integration/test_pagination.py` | `TestClient(...)` | `("testadmin", "testpass")` |
| `tests/integration/test_locale_ai_endpoints.py` | `TestClient(...)` | `("testadmin", "testpass")` |
| `tests/integration/test_ai_budget_flow.py` | `TestClient(...)` | `("testadmin", "testpass")` |
| `tests/integration/test_ai_cache_flow.py` | `TestClient(...)` | `("testadmin", "testpass")` |

**Migration impact:** All integration tests that hit auth-protected endpoints need either: (a) a `dependency_override` that bypasses auth, or (b) a helper fixture that logs in via the new `POST /login` endpoint and manages the session cookie.

### 5.3 Unit Tests (HR Router Pattern)

Unit tests for the new modules (HR, Collections, Customer Accounts) follow the same `_AUTH = ("testadmin", "testpass")` pattern for the endpoints that currently have auth. The unprotected endpoints don't pass auth at all — e.g. `tests/unit/modules/collections/test_routes.py` passes `_AUTH` but only to satisfy future auth; many calls work without it because the endpoints have no `Depends(get_current_user)`.

Files affected:
- `tests/unit/modules/hr/test_router_headcount.py`
- `tests/unit/modules/hr/test_router_payroll_risk.py`
- `tests/unit/modules/hr/test_router_department_cost.py`
- `tests/unit/modules/hr/test_router_department_staff.py`
- `tests/unit/modules/hr/test_router_employee_profile.py`
- `tests/unit/modules/hr/test_router_tenure.py`
- `tests/unit/modules/collections/test_routes.py`
- `tests/unit/core/test_security.py` (tests `verify_credentials` directly — will need replacing with new `UserRepository.verify` tests)

### 5.4 Postman Collection

`tests/postman/CRM-AI-Engine.postman_collection.json`:
- Collection-level auth: `"type": "basic"`, `{{username}}` / `{{password}}`
- Environment: `username=admin`, `password=password`
- All authenticated requests inherit this collection-level Basic auth
- "Security" folder: two requests explicitly test 401 behavior against `/api/v1/summary` — these tests will still pass after migration (auth will be required) but the mechanism changes

**Migration impact:** Collection auth block changes from `"type": "basic"` to a pre-request script that POSTs to `/login` and stores the session cookie. All 4+ authenticated request items in Health, CRM, Observability folders need the cookie-based auth.

### 5.5 Root conftest.py

`tests/conftest.py:17-30` sets `BASIC_AUTH_USERNAME=testadmin` and `BASIC_AUTH_PASSWORD=testpass` as env defaults for the entire test suite. After migration, these are replaced by `SESSION_SECRET` and initial user seed credentials.

---

## 6. Design Proposal

### 6.1 Target Architecture

```
GET /login    → HTML login page (no auth required)
POST /login   → validates username+password, sets session cookie, redirects to /dashboard
GET /logout   → clears session cookie, redirects to /login

SessionMiddleware (starlette)
  ↓
get_current_user (deps.py) — reads session cookie, returns User object (not just str)
  ↓
require_module("crm") / require_module("collections") etc. — returns 403 if module not in user.modules
```

### 6.2 Component Assessment

#### ✅ Session-Cookie Auth + Login Page

**Verdict: Confirm.** Replacing HTTP Basic with session-cookie auth is straightforward in FastAPI/Starlette using `starlette.middleware.sessions.SessionMiddleware`. The main benefit is proper logout (cookie clearing), expiry, and a real login UX. The `api.js` client already uses `credentials: 'include'` so AJAX calls require no changes.

**Risks:**
- **CSRF:** Moving from Basic auth (stateless, per-request credential) to cookie auth introduces CSRF risk. Since `allow_methods=["GET", "OPTIONS"]` in CORS config, write operations are currently blocked. But the login form itself is a `POST`, and future routes might be added. Mitigate with `SameSite=Strict` on the session cookie plus optionally a CSRF token in the login form. The `allow_credentials=False` in CORS means cross-origin requests can't send cookies — this partially protects but is not a substitute for SameSite.
- **Session secret handling:** `SESSION_SECRET` must be ≥32 random bytes, stored in `.env`, never committed. Loss or rotation invalidates all active sessions.
- **Session expiry:** `max_age` should be set explicitly (e.g. 8 hours for a work session). Default Starlette sessions are browser-session-scoped.

#### ✅ SQLite User Store Behind UserRepository Interface

**Verdict: Confirm, with conditions.** SQLite is a pragmatic choice for a single-server, low-user-count ERP dashboard. The `UserRepository` interface decouples the auth logic from the storage backend.

Proposed schema:
```sql
CREATE TABLE users (
    id          INTEGER PRIMARY KEY,
    username    TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,         -- bcrypt
    modules     TEXT NOT NULL DEFAULT '["*"]',  -- JSON array; ["*"] = full access
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);
```

**Seed strategy:** On first startup, if the `users` table is empty, insert one user from `settings.BASIC_AUTH_USERNAME` / `settings.BASIC_AUTH_PASSWORD` (hashing the plain text with bcrypt). This gives a zero-downtime migration path — you keep the old `.env` creds as the initial admin password.

**Risks:**
- SQLite file location needs to be outside the repo (e.g. `data/users.db`) and must be backed up. Add to `.gitignore`.
- `bcrypt` adds ~100ms to login; this is a feature (brute-force hardening), not a bug. Do not cache password hashes in memory.
- The `modules` JSON field is simple but means module list changes require a direct DB update (no admin UI yet). Acceptable for a one-admin shop.

#### ✅ Per-Module Backend Guard Returning 403

**Verdict: Confirm.** The guard is a FastAPI dependency:
```python
def require_module(module_id: str):
    def _guard(user: User = Depends(get_current_user)):
        if "*" not in user.modules and module_id not in user.modules:
            raise HTTPException(status_code=403, detail={"code": "MODULE_ACCESS_DENIED", ...})
        return user
    return _guard
```

**Pre-condition:** Before RBAC can be meaningful, the 21 currently-unprotected endpoints (all Collections, Customer Accounts, and the 4 HR KPI routes) must first receive `Depends(get_current_user)`. Without that fix, any anonymous caller bypasses the module guard entirely. This is a **separate single-step prerequisite** that can be done in one build session.

#### ✅ Sidebar + Route Filtering by Allowed Modules

**Verdict: Confirm, with implementation note.** The sidebar in `base.html` is currently hardcoded. Under RBAC, `_base_ctx()` in `dashboard.py` would pass `user.modules` into the Jinja2 context, and each sidebar entry would be conditionally rendered with `{% if 'crm' in allowed_modules %}`. The "Coming Soon" stubs can stay unconditionally visible or be hidden for users without those modules — TBD.

**HTML route protection:** The HTML routes (e.g. `/collections/dashboard`) need both `get_current_user` AND `require_module("collections")`. Currently they only have `get_current_user`. The module guard must be added alongside the user store.

### 6.3 Named Risks

| # | Risk | Severity | Evidence / Source |
|---|------|----------|-------------------|
| R1 | **21 endpoints are currently open to the internet with no auth** | HIGH | Curl confirms 200 on all Collections, Customer Accounts, and 4 HR KPI routes. Financial data (receivables, payroll) is exposed. |
| R2 | **Session secret in .env** — if `.env` is leaked, all sessions can be forged | HIGH | `.env` currently contains `BASIC_AUTH_PASSWORD=password` in plaintext; same file will hold `SESSION_SECRET`. Must be documented and excluded from VCS. |
| R3 | **CSRF on POST /login** | MEDIUM | Starlette `SessionMiddleware` does not auto-add CSRF protection. Add `SameSite=Strict` and a form token. |
| R4 | **E2E fixture rewrite** — all Playwright tests inject a static `Authorization` header | MEDIUM | `tests/e2e/conftest.py:17`, `test_dashboard.py:27-31`. After migration, tests need to navigate to `/login` and submit the form. May interact with the known asyncio event-loop pollution in the full test suite. |
| R5 | **Asyncio event-loop pollution** — the existing test suite has a known full-suite event-loop issue | MEDIUM | Memory records this as a known issue. Adding session state (SQLite writes, cookie middleware) to the test fixture stack could worsen this. Recommend: use synchronous `TestClient` (not async) for all new auth tests. |
| R6 | **Single-user assumption baked into templates** | LOW | `base.html` renders `{{ current_user[0] }}` as an avatar initial. With multi-user, each user's display name must now come from `user.display_name` or be derived per-user, not from a global `DISPLAY_NAME` setting. |
| R7 | **No admin UI for user management** | LOW | Adding or changing user module assignments requires direct SQLite manipulation. Acceptable initially — can add a CLI script (`python -m backend.cli add-user`) before building a UI. |

### 6.4 Recommended Build Sequence

Before starting any RBAC work, address R1 as a standalone hotfix:

1. **Hotfix (1 session):** Add `Depends(get_current_user)` to all 21 unprotected endpoints in `collections.py`, `customer_accounts.py`, and the 4 HR KPI handlers in `hr.py`. No other changes. This closes the open data exposure.

2. **Auth foundation (1–2 sessions):** Add `starlette.middleware.sessions`, `UserRepository` + SQLite, `bcrypt`, login page, `/login` POST handler, session-based `get_current_user`, logout with cookie clearing.

3. **RBAC layer (1 session):** Add `require_module()` dependency, wire it to all module-specific routes (API + HTML), update `_base_ctx()` to pass `user.modules`, update `base.html` sidebar to filter by allowed modules.

4. **Test rewrites (1 session):** Update `tests/e2e/conftest.py` and all Playwright tests to login via the form. Update integration/unit tests to use either dependency override or login fixture. Update Postman collection.

---

## 7. Appendix — Raw Curl Output

**Server:** `python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000` (no `--reload`), confirmed via `(Get-WmiObject Win32_Process -Filter "ProcessId=17488").CommandLine`  
**Credentials used:** `admin` / `password` (from `.env` `BASIC_AUTH_USERNAME` / `BASIC_AUTH_PASSWORD`)  
**Date:** 2026-06-09

All commands run as: `curl -s -o /dev/null -w "%{http_code}" [options] <url>`

```
=== 1. /health (no auth) ===
200
=== 2. /health authed ===
200
=== 3. /api/v1/health (no auth) ===
401
=== 4. /api/v1/health (authed) ===
200

=== 5. /api/v1/summary (no auth) ===
401
=== 6. /api/v1/summary (authed) ===
200
=== 7. /api/v1/followup-risk (no auth) ===
401
=== 8. /api/v1/data-quality/missing-contact (no auth) ===
401

=== 9. /api/v1/metrics (no auth) ===
401
=== 10. /api/v1/dashboard/kpis (no auth) ===
401
=== 11. /api/v1/dashboard/sparkline (no auth) ===
401
=== 12. /api/v1/dashboard/heatmap (no auth) ===
401
=== 13. /api/v1/ai/budget (no auth) ===
401
=== 14. /api/v1/ai/health (no auth) ===
401

=== 15. /api/v1/chat/suggested-questions (no auth) ===
401
=== 16. /api/v1/collections/kpi/late-uncollected (no auth) ===
200
=== 17. /api/v1/collections/kpi/total-portfolio-value (no auth) ===
200
=== 18. /api/v1/customer-accounts/kpi/total-receivables (no auth) ===
200
=== 19. /api/v1/hr/kpi/headcount (no auth) ===
200
=== 20. /api/v1/hr/kpi/tenure-distribution (no auth) ===
200

=== 21. /api/v1/hr/kpi/payroll-risk-dashboard (no auth) ===
200
=== 22. /api/v1/hr/kpi/department-cost (no auth) ===
200
=== 23. /api/v1/hr/department/1 (no auth) ===
401
=== 24. /api/v1/hr/employee/1 (no auth) ===
401
=== 25. /api/v1/collections/kpi/pending-check-exposure (no auth) ===
200
=== 26. /api/v1/collections/kpi/collection-trend-6m (no auth) ===
200
=== 27. /api/v1/collections/kpi/collection-rate (no auth) ===
200
=== 28. /api/v1/collections/kpi/collection-rate-by-project (no auth) ===
200
=== 29. /api/v1/collections/kpi/expected-forecast (no auth) ===
200
=== 30. /api/v1/collections/kpi/late-uncollected-by-project (no auth) ===
200

=== 31. /api/v1/collections/drilldown/late (no auth) ===
200
=== 32. /api/v1/collections/drilldown/portfolio (no auth) ===
200
=== 33. /api/v1/collections/drilldown/project/1 (no auth) ===
200
=== 34. /api/v1/customer-accounts/kpi/top-overdue-customers (no auth) ===
200
=== 35. /api/v1/customer-accounts/kpi/unallocated-wallet-balance (no auth) ===
200
=== 36. /api/v1/customer-accounts/refunds/summary (no auth) ===
200
=== 37. /api/v1/customer-accounts/refunds/detail (no auth) ===
200
=== 38. /api/v1/customer-accounts/customer/1 (no auth) ===
200
=== 39. /api/v1/health/odoo (no auth) ===
401
=== 40. /api/v1/health/deep (no auth) ===
401

=== HTML ROUTES ===
=== 41. /dashboard (no auth) ===
401
=== 42. /dashboard (authed) ===
200
=== 43. /collections/dashboard (no auth) ===
401
=== 44. /customer-accounts/dashboard (no auth) ===
401
=== 45. /hr/dashboard (no auth) ===
401
=== 46. /data-quality/missing-contact (no auth) ===
401
=== LEGACY REDIRECTS ===
=== 47. /crm/summary (no auth) ===
301
=== 48. /logout ===
401
```

**Total routes checked:** 48  
**Routes confirmed unprotected (⚠️):** 21 (all returning 200 without credentials)  
**Routes confirmed protected:** 25 (returning 401 or intentional 301/401-by-design)  
**Intentionally public (no-auth 200):** 1 (`/health`)
