# API Authentication & Authorization — Security Discovery

**Status:** Discovery / read-only audit — no code changed.
**Scope:** Every HTTP route on the FastAPI app, with emphasis on the `/api/v1/*` JSON endpoints that the dashboard drill-downs fetch.
**Repo state at audit:** branch `main`, HEAD `57ba70b` == `origin/main` (clean tree).
**Date:** 2026-06-18.
**Odoo:** read-only; all probes were GETs; no create/write/unlink; no OpenAI completion triggered.

---

## 0. Headline (TL;DR for the board)

**The pre-launch fear — "the `/api/v1/*` JSON endpoints are publicly reachable without a login, except HR" — is NOT true at HEAD `57ba70b`.**

Every data endpoint is gated. An unauthenticated request to any sensitive endpoint in **every** module (CRM, HR, collections, customer-accounts, campaign-performance, marketing-attribution, AI/chat, admin settings) returns **401** before any business logic, Odoo call, or OpenAI call runs.

| Question | Answer |
|---|---|
| `/api/v1/*` endpoints registered | **55** |
| …reachable **without a login** (OPEN) | **0** |
| …**SESSION-ONLY** (login, no module check) | **4** (health ×3, metrics) |
| …**SESSION + RBAC** (login + module grant) | **45** |
| …**OTHER** (login + admin flag) | **6** (settings) |
| **SENSITIVE endpoints currently OPEN** | **0** |

The gating is already implemented the way this discovery would have recommended: a single **include-level** RBAC dependency in `backend/api/v1/router.py` (so no endpoint can be forgotten), backed by a **per-endpoint** session dependency, and locked in by a **CI regression guard** (`tests/security/test_api_auth_guard.py`) that fails the build if anyone adds an ungated route. **No code change is required to close the stated gap — it is already closed.** The remaining items are minor hardening decisions for Khaled (§7–§8), not open holes.

> Why the original premise was likely believed: it reflects an **earlier** posture (pre auth/RBAC hardening). The HR drill-downs were gated first, which is why "everything except HR is open" was the working assumption. The marketing cluster and every other module have since been brought under the same include-level guard.

---

## 1. What was read (authoritative source = code, not assumptions)

App construction & wiring:
- `backend/main.py` — middleware stack, CORS, router includes, exception handlers, public `/health`, legacy 301 shims, static mount.
- `backend/api/v1/router.py` — the aggregation: which dependency is attached at **include time** per module router.
- `backend/api/deps.py` — the dependency factories (`get_current_user`, `get_current_user_html`, `require_module_api`, `require_module_html`, `require_admin_api`, `require_admin_html`).

Every endpoint file under `backend/api/v1/endpoints/` (no sampling):
`health.py`, `metrics_endpoint.py`, `summary.py`, `followup.py`, `data_quality.py`, `dashboard_api.py`, `ai.py`, `chat.py`, `collections.py`, `customer_accounts.py`, `hr.py`, `marketing_attribution.py`, `campaign_performance.py`, `settings.py`, `auth.py`, `dashboard.py`.

Auth subsystem: `backend/auth/models.py`, `repository.py`, `seed.py`.

Tests / tooling: `tests/security/test_api_auth_guard.py`, `tests/integration/test_rbac.py`, `scripts/_lib/route_auth.py` (single source of truth for the probe + allowlists), `scripts/audit_api_auth.py`.

Frontend callers: `frontend/static/js/api.js` and every `*.js` / template issuing `fetch()` to `/api/v1/*` (`app.js`, `charts.js`, `collections.js`, `customer_accounts.js`, `drilldown.js`, `ca_drilldown.js`, `ca_refunds_panel.js`, `hr_drilldown.js`, `hr_employee_drilldown.js`, `chat.js`, `settings.html`, `dashboard.html`).

Config: `backend/core/config.py` (CORS default, AI/session settings).

---

## 2. Endpoint inventory (the core deliverable)

Classification key:
- **OPEN** — no auth dependency; reachable without a session.
- **SESSION-ONLY** — `get_current_user` only (login required, no module check).
- **SESSION+RBAC** — login **and** a per-module grant (`require_module_api("<mod>")`, which itself chains `get_current_user`).
- **OTHER (admin)** — login **and** the `is_admin` flag (`require_admin_api`); not module-based.

The "Auth dependency" column shows the dependency chain as FastAPI actually resolved it (recovered programmatically from each route's dependant tree — see §2.1). Every module router has its RBAC dependency attached **once at `include_router(...)`** in `router.py`; each endpoint **additionally** declares `get_current_user` in its own signature (defense-in-depth).

### 2.A — CRM cluster — `require_module_api("crm")` at include time → **SESSION+RBAC** (15)

| Path | Method | Source file | Auth dependency (resolved) | Module | Class |
|---|---|---|---|---|---|
| `/api/v1/summary` | GET | `summary.py` | require_module_api('crm'), get_current_user | crm | SESSION+RBAC |
| `/api/v1/followup-risk` | GET | `followup.py` | require_module_api('crm'), get_current_user | crm | SESSION+RBAC |
| `/api/v1/data-quality/missing-contact` | GET | `data_quality.py` | require_module_api('crm'), get_current_user | crm | SESSION+RBAC |
| `/api/v1/data-quality/missing-stage` | GET | `data_quality.py` | require_module_api('crm'), get_current_user | crm | SESSION+RBAC |
| `/api/v1/data-quality/missing-salesperson` | GET | `data_quality.py` | require_module_api('crm'), get_current_user | crm | SESSION+RBAC |
| `/api/v1/dashboard/kpis` | GET | `dashboard_api.py` | require_module_api('crm'), get_current_user | crm | SESSION+RBAC |
| `/api/v1/dashboard/sparkline` | GET | `dashboard_api.py` | require_module_api('crm'), get_current_user | crm | SESSION+RBAC |
| `/api/v1/dashboard/heatmap` | GET | `dashboard_api.py` | require_module_api('crm'), get_current_user | crm | SESSION+RBAC |
| `/api/v1/ai/prioritize-lead/{lead_id}` | POST | `ai.py` | require_module_api('crm'), get_current_user | crm | SESSION+RBAC |
| `/api/v1/ai/prioritize-overdue` | POST | `ai.py` | require_module_api('crm'), get_current_user | crm | SESSION+RBAC |
| `/api/v1/ai/budget` | GET | `ai.py` | require_module_api('crm'), get_current_user | crm | SESSION+RBAC |
| `/api/v1/ai/health` | GET | `ai.py` | require_module_api('crm'), get_current_user | crm | SESSION+RBAC |
| `/api/v1/chat/message` | POST | `chat.py` | require_module_api('crm'), get_current_user | crm | SESSION+RBAC |
| `/api/v1/chat/session/{session_id}` | DELETE | `chat.py` | require_module_api('crm'), get_current_user | crm | SESSION+RBAC |
| `/api/v1/chat/suggested-questions` | GET | `chat.py` | require_module_api('crm'), get_current_user | crm | SESSION+RBAC |

### 2.B — Collections — `require_module_api("collections")` → **SESSION+RBAC** (13)

| Path | Method | Source file | Module | Class |
|---|---|---|---|---|
| `/api/v1/collections/kpi/late-uncollected` | GET | `collections.py` | collections | SESSION+RBAC |
| `/api/v1/collections/kpi/total-portfolio-value` | GET | `collections.py` | collections | SESSION+RBAC |
| `/api/v1/collections/kpi/late-uncollected-by-project` | GET | `collections.py` | collections | SESSION+RBAC |
| `/api/v1/collections/kpi/pending-check-exposure` | GET | `collections.py` | collections | SESSION+RBAC |
| `/api/v1/collections/kpi/collection-trend-6m` | GET | `collections.py` | collections | SESSION+RBAC |
| `/api/v1/collections/kpi/collection-rate` | GET | `collections.py` | collections | SESSION+RBAC |
| `/api/v1/collections/kpi/collection-rate-by-project` | GET | `collections.py` | collections | SESSION+RBAC |
| `/api/v1/collections/kpi/expected-forecast` | GET | `collections.py` | collections | SESSION+RBAC |
| `/api/v1/collections/drilldown/late` | GET | `collections.py` | collections | SESSION+RBAC |
| `/api/v1/collections/drilldown/portfolio` | GET | `collections.py` | collections | SESSION+RBAC |
| `/api/v1/collections/drilldown/project/{project_id}` | GET | `collections.py` | collections | SESSION+RBAC |
| `/api/v1/collections/drilldown/trend/{month}` | GET | `collections.py` | collections | SESSION+RBAC |
| `/api/v1/collections/drilldown/forecast/{bucket}/{segment}` | GET | `collections.py` | collections | SESSION+RBAC |

### 2.C — Customer-accounts — `require_module_api("customer_accounts")` → **SESSION+RBAC** (6)

| Path | Method | Source file | Module | Class |
|---|---|---|---|---|
| `/api/v1/customer-accounts/kpi/total-receivables` | GET | `customer_accounts.py` | customer_accounts | SESSION+RBAC |
| `/api/v1/customer-accounts/kpi/top-overdue-customers` | GET | `customer_accounts.py` | customer_accounts | SESSION+RBAC |
| `/api/v1/customer-accounts/kpi/unallocated-wallet-balance` | GET | `customer_accounts.py` | customer_accounts | SESSION+RBAC |
| `/api/v1/customer-accounts/refunds/summary` | GET | `customer_accounts.py` | customer_accounts | SESSION+RBAC |
| `/api/v1/customer-accounts/refunds/detail` | GET | `customer_accounts.py` | customer_accounts | SESSION+RBAC |
| `/api/v1/customer-accounts/customer/{partner_id}` | GET | `customer_accounts.py` | customer_accounts | SESSION+RBAC |

### 2.D — HR — `require_module_api("hr")` → **SESSION+RBAC** (6)

| Path | Method | Source file | Module | Class |
|---|---|---|---|---|
| `/api/v1/hr/kpi/headcount` | GET | `hr.py` | hr | SESSION+RBAC |
| `/api/v1/hr/kpi/tenure-distribution` | GET | `hr.py` | hr | SESSION+RBAC |
| `/api/v1/hr/kpi/payroll-risk-dashboard` | GET | `hr.py` | hr | SESSION+RBAC |
| `/api/v1/hr/kpi/department-cost` | GET | `hr.py` | hr | SESSION+RBAC |
| `/api/v1/hr/department/{department_id}` | GET | `hr.py` | hr | SESSION+RBAC |
| `/api/v1/hr/employee/{employee_id}` | GET | `hr.py` | hr | SESSION+RBAC |

> Note: HR endpoint **docstrings** still say "HTTPBasic required". That is **stale wording** — the real mechanism is the session cookie (`get_current_user`) + the module grant. Documentation drift only; not a security gap. (Same stale wording in `hr_drilldown.js`, `hr_employee_drilldown.js`, `_hr_dept_panel.html`, `_hr_profile_panel.html`.)

### 2.E — Campaign-performance — `require_module_api("campaign_performance")` → **SESSION+RBAC** (3)

| Path | Method | Source file | Module | Class |
|---|---|---|---|---|
| `/api/v1/campaign-performance/overview` | GET | `campaign_performance.py` | campaign_performance | SESSION+RBAC |
| `/api/v1/campaign-performance/windowed` | GET | `campaign_performance.py` | campaign_performance | SESSION+RBAC |
| `/api/v1/campaign-performance/timeline` | GET | `campaign_performance.py` | campaign_performance | SESSION+RBAC |

### 2.F — Marketing-attribution — `require_module_api("marketing_attribution")` → **SESSION+RBAC** (2)

| Path | Method | Source file | Module | Class |
|---|---|---|---|---|
| `/api/v1/marketing-attribution/overview` | GET | `marketing_attribution.py` | marketing_attribution | SESSION+RBAC |
| `/api/v1/marketing-attribution/windowed` | GET | `marketing_attribution.py` | marketing_attribution | SESSION+RBAC |

### 2.G — Health & metrics — no module gate → **SESSION-ONLY** (4)

| Path | Method | Source file | Auth dependency | Class |
|---|---|---|---|---|
| `/api/v1/health` | GET | `health.py` | get_current_user | SESSION-ONLY |
| `/api/v1/health/odoo` | GET | `health.py` | get_current_user, get_crm_service | SESSION-ONLY |
| `/api/v1/health/deep` | GET | `health.py` | get_current_user, get_crm_service | SESSION-ONLY |
| `/api/v1/metrics` | GET | `metrics_endpoint.py` | get_current_user | SESSION-ONLY |

> These four are intentionally **not** module-gated (any authenticated user may read them — see `test_rbac.py::test_non_module_gated_routes_accessible_to_all`). They return operational data only: uptime, Odoo connectivity, cache size, request metrics, and AI budget/spend. **No board financial / CRM / HR data.** `/api/v1/metrics` does surface **AI cost-to-date** — the only mildly sensitive field here (see §7-A).

### 2.H — Admin settings — `require_admin_api` (include `prefix="/settings"`) → **OTHER (admin)** (6)

| Path | Method | Source file | Auth dependency | Class |
|---|---|---|---|---|
| `/api/v1/settings/users` | GET | `settings.py` | require_admin_api, get_current_user | OTHER (admin) |
| `/api/v1/settings/users` | POST | `settings.py` | require_admin_api, get_current_user | OTHER (admin) |
| `/api/v1/settings/users/{username}/modules` | PATCH | `settings.py` | require_admin_api, get_current_user | OTHER (admin) |
| `/api/v1/settings/users/{username}/status` | PATCH | `settings.py` | require_admin_api, get_current_user | OTHER (admin) |
| `/api/v1/settings/users/{username}/admin` | PATCH | `settings.py` | require_admin_api, get_current_user | OTHER (admin) |
| `/api/v1/settings/users/{username}/reset-password` | POST | `settings.py` | require_admin_api, get_current_user | OTHER (admin) |

> These write to the **local SQLite user store** (`backend/auth/repository.py`) — **not Odoo**. Read-only-Odoo is preserved. Passwords are never returned or logged.

### `/api/v1/*` totals

**55 endpoints: 0 OPEN · 4 SESSION-ONLY · 45 SESSION+RBAC · 6 OTHER (admin).**
**Sensitive endpoints OPEN: 0.**

### 2.1 — Completeness proof (reconciled against FastAPI's own route table)

The inventory was not hand-built — it was generated by enumerating `app.routes` programmatically (`scripts/_lib/route_auth.iter_probeable_routes`, used by both the audit and the CI guard) and probing every `(method, route)` pair. The full registered surface:

| Class (whole app) | Count |
|---|---|
| GATED (401, or 302→/login) | **67** |
| PUBLIC-OK (allowlisted, no data) | **8** |
| REVIEW (legacy 301 shims) | **3** |
| **Total (method, route) pairs probed** | **78** |

Reconciliation: **GATED 67 = 55 `/api/v1/*` endpoints + 12 gated HTML pages.** The 12 HTML pages (`/dashboard`, `/hr/dashboard`, `/collections/dashboard`, `/customer-accounts/dashboard`, `/marketing-attribution/dashboard`, `/campaign-performance/dashboard`, `/campaign-performance/timeline`, the three `/data-quality/*` pages, `/settings`, `/no-modules`) each carry `require_module_html`/`require_admin_html`/`get_current_user_html` and answer an unauthenticated request with **302 → /login**. PUBLIC-OK 8 and REVIEW 3 are detailed in §6. The arithmetic closes with **zero unexplained routes** — the inventory is provably complete.

---

## 3. Mechanism write-up (plain terms)

### (a) How the HTML routes enforce session + RBAC
Each HTML page in `backend/api/v1/endpoints/dashboard.py` declares `dependencies=[Depends(require_module_html("<mod>"))]` and takes `user = Depends(get_current_user_html)`.
- `get_current_user_html(request)` reads `request.session["username"]` (the signed cookie via Starlette `SessionMiddleware`), looks the user up in the SQLite store, checks `is_active`. No/invalid session → raises **302** with `Location: /login?next=<path>`.
- `require_module_html("<mod>")` chains off `get_current_user_html`, then checks `"*" in user.modules or "<mod>" in user.modules`. Missing grant → **403** (rendered as `403.html` for browsers).
- `require_admin_html` is the same shape but checks `user.is_admin` (used by `/settings`).

### (b) How the protected JSON endpoints are gated — the reusable dependency
Two layers, both already in place:
1. **Include-level RBAC (the important one).** In `backend/api/v1/router.py`, every module router is mounted with its guard attached **once**, e.g.
   `api_v1_router.include_router(hr_router, dependencies=[Depends(require_module_api("hr"))])`.
   Because the dependency is on the **include**, it applies to **every** route in that router automatically — a new endpoint added to `hr.py` is gated the moment it is registered, with no per-endpoint action. The CRM group shares one `_crm = [Depends(require_module_api("crm"))]` list across its six routers; settings uses `_admin = [Depends(require_admin_api)]`.
2. **Per-endpoint session (defense-in-depth).** Each endpoint **also** declares `_user / user: str = Depends(get_current_user)` in its signature. `get_current_user` resolves the session exactly like the HTML variant but raises **401 "Not authenticated"** (no redirect) when there is no valid session.
   `require_module_api("<mod>")` itself chains `username = Depends(get_current_user)`, so **401 (no login) fires before 403 (no module)** — confirmed by `test_rbac.py::test_unauthed_gets_401_not_403_on_module_routes`.

The reusable primitives, if anything new ever needs gating: `require_module_api("<mod>")` (login + module), `require_admin_api` (login + admin), or bare `get_current_user` (login only). Prefer attaching them at `include_router(...)` level.

### (c) What `test_api_auth_guard.py` does — and does NOT — cover
**Does:**
- `test_no_route_reachable_without_auth` — enumerates **every** route on the real `app`, probes each **unauthenticated**, and asserts the violation list is **empty**. "Acceptable" = `401`/`403`, **or** `3xx→/login`, **or** in `PUBLIC_ALLOWLIST`, **or** a verified `301` shim. **Anything else — including `2xx`, `422`, `5xx`, or a handler that raises — is a violation.** (So even "auth passed but body-validation reached" would fail the build.) This is the safety net that makes "no endpoint can be forgotten" permanent: add an ungated route and CI goes red.
- `test_redirect_shims_still_redirect_to_api_v1` — the 3 legacy `/crm/*` shims must remain live `301`s to their documented `/api/v1/*` targets (a shim can never silently become a data route).
- `test_guard_is_not_vacuous` — builds a throwaway app with one ungated + one gated route and proves the classifier flags the ungated one `EXPOSED` and the gated one `GATED`.
- `test_public_allowlist_is_minimal` — every `PUBLIC_ALLOWLIST` path must still exist on the app (no stale entry that could mask a future data route).

**Does NOT cover (by design — these are other tests' or out of scope):**
- It does **not** verify the *correctness* of RBAC per role (which user may see which module) — that is `tests/integration/test_rbac.py` (the 403/allow matrix for `hr_only`, `coll_ca`, `mktattr_only`, admin, no-modules).
- It does **not** test authenticated behaviour, response bodies, rate-limit thresholds, session expiry/rotation, password strength, or CSRF.
- It uses `TestClient` **without** lifespan, so it does not exercise the live ASGI server, real Odoo, or real OpenAI (intentional — $0, no side effects). The §5 live-server probe complements this.

### (d) The gap between (a) and the OPEN JSON endpoints
**There is no gap.** At HEAD `57ba70b` the count of OPEN JSON data endpoints is **0**. The original concern described a state that no longer exists. The "lowest-risk remediation" this discovery would otherwise recommend — *attach the guard at the router/include level so nothing is forgotten* — is **already the implemented design**, and a CI guard prevents regression.

---

## 4. Frontend coupling — would a session guard break the drill-downs?

**Verdict: No. Every drill-down already sends the session cookie, and the gate is already live while the drill-downs work.** There is **no caller that would break**; there is nothing to change on the frontend.

Why, by caller:
- **All requests are same-origin.** Every fetch URL is a root-relative `/api/v1/...` path; CSP `connect-src 'self'`. No cross-origin calls exist, so cookie delivery is never subject to CORS credential rules.
- **`window.crmApi.get()`** (`frontend/static/js/api.js`) sets **`credentials: 'include'`** explicitly. Used by: `app.js` (`/dashboard/kpis`), `charts.js` (`/dashboard/sparkline`), `drilldown.js` (collections drill-downs), `ca_drilldown.js` (customer drill-down), `ca_refunds_panel.js` (`/refunds/detail`).
- **`hr_drilldown.js`, `hr_employee_drilldown.js`, `settings.html`** set **`credentials: 'same-origin'`** explicitly.
- **`collections.js`, `customer_accounts.js`** (KPI-card fetches) and **`dashboard.html`** (AI `prioritize-overdue` / `budget`) call `fetch()` with only an `Accept` header and **no** `credentials` option. On a **same-origin** request the Fetch API default is `credentials: 'same-origin'`, so the session cookie **is** sent regardless. These survive a guard unchanged.
- **`chat.js`** (`/chat/*`) — same-origin fetch, cookie sent by default.

Empirical confirmation (§5): with no cookie every endpoint returned 401; after `POST /login` set the session cookie, the **same** endpoints returned 200 with real data. The gate is the only blocker and the cookie passes it.

---

## 5. Empirical proof (read-only, $0)

Two independent, complementary probes. Neither triggered an OpenAI completion (the unauthenticated path 401s before any AI/budget dependency resolves) and Odoo stayed read-only (GETs only).

### 5.1 Full-surface unauthenticated probe — `scripts/audit_api_auth.py`
Enumerates **all 78** `(method, route)` pairs and sends one unauthenticated request each (bodies never read). Result:

```
Totals: GATED=67 | PUBLIC-OK=8 | REVIEW=3
EXPOSED (reachable without login): None — no data endpoints were reachable without authentication.
```

### 5.2 Live `uvicorn` no-cookie probe (real socket, full middleware stack)
Started `uvicorn backend.main:app` on `127.0.0.1:8137` **without `--reload`**; issued no-cookie GETs to one representative sensitive endpoint per module, plus controls:

| Request (no session cookie) | Status | Body (first bytes) |
|---|---|---|
| GET `/api/v1/collections/kpi/late-uncollected` | **401** | `{"detail":"Not authenticated"}` |
| GET `/api/v1/collections/kpi/total-portfolio-value` | **401** | `{"detail":"Not authenticated"}` |
| GET `/api/v1/customer-accounts/kpi/total-receivables` | **401** | `{"detail":"Not authenticated"}` |
| GET `/api/v1/customer-accounts/customer/1` | **401** | `{"detail":"Not authenticated"}` |
| GET `/api/v1/summary` | **401** | `{"detail":"Not authenticated"}` |
| GET `/api/v1/dashboard/kpis` | **401** | `{"detail":"Not authenticated"}` |
| GET `/api/v1/campaign-performance/overview` | **401** | `{"detail":"Not authenticated"}` |
| GET `/api/v1/campaign-performance/windowed` (new) | **401** | `{"detail":"Not authenticated"}` |
| GET `/api/v1/marketing-attribution/overview` | **401** | `{"detail":"Not authenticated"}` |
| GET `/api/v1/marketing-attribution/windowed` (new) | **401** | `{"detail":"Not authenticated"}` |
| GET `/api/v1/hr/kpi/headcount` | **401** | `{"detail":"Not authenticated"}` |
| GET `/api/v1/hr/employee/1` | **401** | `{"detail":"Not authenticated"}` |
| GET `/api/v1/settings/users` | **401** | `{"detail":"Not authenticated"}` |
| GET `/api/v1/metrics` | **401** | `{"detail":"Not authenticated"}` |
| — controls — | | |
| GET `/health` (root liveness) | **200** | `{"status":"ok", ... "uptime_seconds":...}` |
| GET `/api/v1/health` (authed health) | **401** | `{"detail":"Not authenticated"}` |
| GET `/login` | **200** | login HTML form |
| GET `/dashboard` (HTML host page) | **302** | `Location: /login?next=/dashboard` |
| GET `/hr/dashboard` (HTML host page) | **302** | `Location: /login?next=/hr/dashboard` |

### 5.3 Authenticated round-trip (confirms the gate is the only blocker)
`POST /login` (seeded admin, `modules=['*']`) → **303** + session cookie set. Re-probing **with** the cookie:

| Request (with session cookie) | Status | Body (first bytes) |
|---|---|---|
| GET `/api/v1/metrics` | **200** | `{"uptime_seconds":..., "odoo":{...}}` |
| GET `/api/v1/settings/users` | **200** | `{"ok":true,"data":{"users":[{"username":"admin",...` |
| GET `/api/v1/hr/kpi/headcount` | **200** | `{"headcount":115, "by_department":[...]}` (live read-only Odoo) |

The server was then stopped and the cookie jar/temp files removed. (The HR call returned real Odoo data — a read-only GET — and triggered **no** OpenAI call.)

**Conclusion:** unauthenticated = 401 everywhere sensitive; authenticated = 200 + data. The session gate is real, enforced over the live socket, and is the sole barrier.

---

## 6. Endpoints that MUST stay open (explicit allowlist + justification)

These are the only routes intentionally reachable without a session. They return **no business data** and are documented in `scripts/_lib/route_auth.py` (`PUBLIC_ALLOWLIST` / `REDIRECT_SHIM_ALLOWLIST`), so each is a reviewable, deliberate entry.

| Path | Why it must stay open |
|---|---|
| `/health` (root, `main.py`) | Liveness/readiness probe for load balancer / orchestrator. Returns only `status`, `service`, `version`, `uptime`. **No data.** |
| `/login` (GET) | Renders the login form — the auth entry point. |
| `/login` (POST) | Submits credentials (rate-limited `10/min`). The only way to obtain a session. |
| `/logout` | Clears the session, `303 → /login`. Returns no data. |
| `/static/*` | CSS/JS/vendor assets (mounted, not a data route). Public by nature. |
| `/docs`, `/redoc`, `/openapi.json`, `/docs/oauth2-redirect` | Interactive API docs + schema. **Route shapes only, no data.** ⚠️ See §7-B — consider disabling in production. |
| `/crm/summary`, `/crm/followup-risk`, `/crm/data-quality/missing-contact` | Legacy **301** redirect shims → their **gated** `/api/v1/*` equivalents. They return no data themselves; the CI guard verifies they stay 301s pointing at `/api/v1/*`. |

> **i18n / translations:** there is **no** translation API endpoint. Translations are loaded server-side (`load_translations()`) and rendered into templates. Nothing to allowlist here.

---

## 7. Decisions left for Khaled (genuine either/or — all low-stakes)

**A. The 4 SESSION-ONLY endpoints (`/api/v1/health`, `/health/odoo`, `/health/deep`, `/api/v1/metrics`).**
Today any authenticated user (any role) can read them. They expose operational data only — uptime, Odoo connectivity, cache size, request metrics, and **AI spend-to-date** (`/api/v1/metrics`).
- Option 1 (keep): leave as authenticated-only. All accounts are board-trusted; this is the simplest and matches the current test contract.
- Option 2 (tighten `/metrics`): gate `/api/v1/metrics` behind `require_admin_api` if AI cost should be admin-only. One-line dependency change; would need the RBAC test updated.
*Recommendation: Option 1 — not sensitive enough to justify churn before launch.*

**B. Swagger/ReDoc in production (`/docs`, `/redoc`, `/openapi.json`).**
They expose the full route map (no data, but reveals the attack surface and route shapes).
- Option 1 (keep): convenient for the team; low risk since every data route is gated anyway.
- Option 2 (disable in prod): pass `docs_url=None, redoc_url=None, openapi_url=None` to `FastAPI(...)` when `ENVIRONMENT == "production"`.
*Recommendation: Option 2 for production hardening — but this is a deployment-time toggle, not a gate fix.*

**C. CORS origins in production.**
`CORS_ORIGINS` defaults to `["*"]` when unset (`main.py`). This is **not** an exfiltration path today because `allow_credentials=False` and methods are limited to `GET/OPTIONS` — a browser will not attach the session cookie to a cross-origin request the server won't accept credentials for, and the response would be unreadable cross-origin. Still, set `CORS_ORIGINS` explicitly to the dashboard origin in production as hygiene.
*Recommendation: set explicit origins in the production `.env`; no code change.*

---

## 8. Risk / edge-case notes

- **AI chat endpoint (`POST /api/v1/chat/message`)** — gated (`crm` module + session). An unauthenticated call 401s **before** the budget/AI dependencies resolve, so there is **no unauthenticated path to an OpenAI charge**. Confirmed by code order and the §5 probe (401). No streaming (single JSON response — nothing to leak mid-stream).
- **Manual refresh** — the dashboard's refresh uses `/api/v1/dashboard/kpis` (gated). There is no separate unauthenticated refresh endpoint.
- **Settings writes** — `POST/PATCH /api/v1/settings/*` mutate the **local SQLite** user store only (never Odoo); admin-gated; passwords never returned/logged; includes self-lockout and last-admin protections.
- **Rate-limit interplay** — endpoints carry `@limiter.limit(...)`. A flood could yield `429`; the CI guard deliberately treats `429` as a **violation** (forces investigation rather than masking a route), so a rate-limited route can't hide an auth gap.
- **401 vs 403 ordering** — `get_current_user` resolves before the module check, so unauthenticated → 401, authenticated-but-unauthorized → 403 (JSON) / 403 page (HTML). Verified by `test_rbac.py`.
- **Stale "HTTPBasic" docstrings** in `hr.py` and the HR JS/templates — cosmetic documentation drift; the real mechanism is the session cookie. Worth a doc cleanup later (out of scope this session — no code changed).
- **Session secret** — `SESSION_SECRET` is validated as required & ≥32 chars only in `production`; in dev it falls back to an insecure default with a warning. Ensure it is set in the production `.env` (the config validator already enforces this at boot).

---

## 9. Recommended remediation approach

**Primary recommendation: ship as-is for the stated gate — no code change is required.** The board-data exposure risk that motivated this discovery does not exist at HEAD `57ba70b`:

1. **The gate is already implemented at the lowest-risk layer.** RBAC is attached at `include_router(...)` in `backend/api/v1/router.py`, so it covers every current and future endpoint of each module automatically — exactly the "so no endpoint can be forgotten" property requested. Per-endpoint `get_current_user` adds defense-in-depth.
2. **It is regression-proof.** `tests/security/test_api_auth_guard.py` fails CI if any new route is reachable unauthenticated, and `tests/integration/test_rbac.py` locks the per-role allow/deny matrix. The allowlist of intentionally-public routes is small, explicit, and self-validating.
3. **It is empirically verified** both with the project's audit tool and a live-server no-cookie probe (§5): 0 sensitive endpoints open.

**Optional hardening (deployment-time, not gate fixes), in priority order:** (B) disable `/docs` `/redoc` `/openapi.json` in production; (C) set explicit `CORS_ORIGINS`; ensure `SESSION_SECRET` is set in prod; (A) optionally admin-gate `/api/v1/metrics`. None of these are blockers and none touch the routing/auth design.

**Do not** add a *third*, app-level auth layer (e.g. a global middleware that 401s everything): it would collide with the public allowlist (`/health`, `/login`, `/static`, `/docs`) and risks breaking the working login flow and drill-downs for no security gain over the existing include-level guard.

---

### Appendix — how to reproduce (read-only, $0)
- Full audit: `python scripts/audit_api_auth.py` (TestClient, no lifespan, no Odoo/OpenAI).
- CI guard: `pytest tests/security/test_api_auth_guard.py tests/integration/test_rbac.py`.
- Live probe: `python -m uvicorn backend.main:app --host 127.0.0.1 --port <port>` (no `--reload`), then unauthenticated `curl` of any `/api/v1/*` path → expect `401`.
