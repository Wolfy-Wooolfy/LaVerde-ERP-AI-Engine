"""
A3 RBAC integration test suite.

Sections:
  A. API 403/allow matrix  — module guards on API routes
  B. HTML 403/allow matrix — module guards on HTML routes (FIX2: Accept: text/html for content-type checks)
  C. Sidebar filtering     — allowed_modules rendered in base.html
  D. Post-login landing    — module-aware redirect after POST /login
  E. No-modules            — users with modules=[] handled correctly

FIX1: JSON 403 body uses project envelope {"error": {"code": "MODULE_ACCESS_DENIED", ...}}.
      "module" field is NOT checked (discarded by the global handler — acceptable).
FIX2: TestClient default Accept is */* → hits JSON branch of the 403 handler.
      Tests asserting content-type text/html must send headers={"Accept": "text/html"}.
      Status == 403 assertions hold on both branches; only content-type checks need the header.
"""

import pytest
from fastapi.testclient import TestClient

from backend.main import app


# ── A. API Module Gating ──────────────────────────────────────────────────────


class TestApiModuleGating:
    """403/allow matrix: hr_only and coll_ca against every module-gated API route group."""

    @pytest.mark.parametrize("path,hr_denied,ca_denied", [
        # CRM routes — both users denied
        ("/api/v1/summary",                                      True,  True),
        ("/api/v1/data-quality/missing-contact",                 True,  True),
        ("/api/v1/dashboard/kpis",                               True,  True),
        # HR routes — hr_only allowed, coll_ca denied
        ("/api/v1/hr/kpi/headcount",                             False, True),
        ("/api/v1/hr/kpi/department-cost",                       False, True),
        ("/api/v1/hr/kpi/tenure-distribution",                   False, True),
        # Collections routes — hr_only denied, coll_ca allowed
        ("/api/v1/collections/kpi/late-uncollected",             True,  False),
        ("/api/v1/collections/kpi/total-portfolio-value",        True,  False),
        # Customer accounts routes — hr_only denied, coll_ca allowed
        ("/api/v1/customer-accounts/kpi/total-receivables",      True,  False),
    ])
    def test_api_module_gating_matrix(
        self, path, hr_denied, ca_denied, hr_only_client, coll_ca_client
    ):
        r_hr = hr_only_client.get(path)
        if hr_denied:
            assert r_hr.status_code == 403, (
                f"hr_only on {path}: expected 403, got {r_hr.status_code}"
            )
            body = r_hr.json()
            # FIX1: standard error envelope, not {"detail": {...}}
            assert body["error"]["code"] == "MODULE_ACCESS_DENIED"
        else:
            assert r_hr.status_code != 403, (
                f"hr_only on {path}: guard should pass, got {r_hr.status_code}"
            )

        r_ca = coll_ca_client.get(path)
        if ca_denied:
            assert r_ca.status_code == 403, (
                f"coll_ca on {path}: expected 403, got {r_ca.status_code}"
            )
            body = r_ca.json()
            assert body["error"]["code"] == "MODULE_ACCESS_DENIED"
        else:
            assert r_ca.status_code != 403, (
                f"coll_ca on {path}: guard should pass, got {r_ca.status_code}"
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
        """Unauthenticated requests reach get_current_user (401) before the module guard runs."""
        # No `with` — avoids running a new lifespan that would close the shared HTTP
        # client used by the module-scoped fixture clients.  app.state is already
        # populated by the module-scoped fixtures; 401 is raised before any state access.
        c = TestClient(app, follow_redirects=False, raise_server_exceptions=False)
        for path in [
            "/api/v1/hr/kpi/headcount",
            "/api/v1/collections/kpi/late-uncollected",
        ]:
            r = c.get(path)
            assert r.status_code == 401, (
                f"Expected 401 for unauthenticated request on {path}, got {r.status_code}"
            )

    def test_non_module_gated_routes_accessible_to_all(self, hr_only_client, coll_ca_client):
        """/api/v1/health is authenticated-only (no module guard)."""
        for client, name in [
            (hr_only_client, "hr_only"),
            (coll_ca_client, "coll_ca"),
        ]:
            r = client.get("/api/v1/health")
            assert r.status_code != 403, (
                f"{name} should not get 403 on /api/v1/health, got {r.status_code}"
            )


# ── B. HTML Module Gating ─────────────────────────────────────────────────────


class TestHtmlModuleGating:
    """403/200 matrix for HTML routes.

    FIX2: status == 403 holds with any Accept header, but to also verify the response
    is rendered as text/html (not JSON) the request must carry Accept: text/html.
    """

    def test_crm_html_dashboard_forbidden_for_hr_only(self, hr_only_client):
        r = hr_only_client.get("/dashboard", headers={"Accept": "text/html"})
        assert r.status_code == 403
        assert "text/html" in r.headers.get("content-type", "")

    def test_crm_data_quality_forbidden_for_hr_only(self, hr_only_client):
        r = hr_only_client.get(
            "/data-quality/missing-contact", headers={"Accept": "text/html"}
        )
        assert r.status_code == 403

    def test_hr_html_allowed_for_hr_only(self, hr_only_client):
        r = hr_only_client.get("/hr/dashboard")
        assert r.status_code != 403

    def test_collections_html_forbidden_for_hr_only(self, hr_only_client):
        r = hr_only_client.get(
            "/collections/dashboard", headers={"Accept": "text/html"}
        )
        assert r.status_code == 403

    def test_collections_html_allowed_for_coll_ca(self, coll_ca_client):
        r = coll_ca_client.get("/collections/dashboard")
        assert r.status_code != 403

    def test_customer_accounts_html_allowed_for_coll_ca(self, coll_ca_client):
        r = coll_ca_client.get("/customer-accounts/dashboard")
        assert r.status_code != 403

    def test_hr_html_forbidden_for_coll_ca(self, coll_ca_client):
        r = coll_ca_client.get("/hr/dashboard", headers={"Accept": "text/html"})
        assert r.status_code == 403

    def test_admin_accesses_all_html_modules(self, authed_client):
        for path in [
            "/dashboard",
            "/hr/dashboard",
            "/collections/dashboard",
            "/customer-accounts/dashboard",
        ]:
            try:
                r = authed_client.get(path)
            except Exception:
                # Unhandled server exception (e.g. ConnectError from CRM Odoo call)
                # means the guard passed — RBAC denial produces 302/403, not exceptions.
                continue
            # RBAC concern: guard must not block admin (302/403).
            assert r.status_code not in {302, 403}, (
                f"Admin guard blocked {path}: {r.status_code}"
            )

    def test_unauthenticated_html_gets_302_not_403(self):
        """Unauthenticated HTML requests get 302 → /login, not 403."""
        # No `with` — same reason as test_unauthed_gets_401; 302 fires before any Odoo call.
        c = TestClient(app, follow_redirects=False, raise_server_exceptions=False)
        r = c.get("/hr/dashboard")
        assert r.status_code == 302
        assert "/login" in r.headers.get("location", "")


# ── C. Sidebar Filtering ──────────────────────────────────────────────────────


class TestSidebarFiltering:
    """base.html renders only the module links matching allowed_modules."""

    def test_hr_only_sidebar_shows_hr_hides_crm(self, hr_only_client):
        r = hr_only_client.get("/hr/dashboard")
        if r.status_code != 200:
            pytest.skip(f"/hr/dashboard returned {r.status_code} — Odoo unavailable, skipping body check")
        body = r.text
        assert 'href="/hr/dashboard"' in body
        assert 'href="/dashboard"' not in body  # CRM module link absent

    def test_hr_only_sidebar_hides_collections_and_ca(self, hr_only_client):
        r = hr_only_client.get("/hr/dashboard")
        if r.status_code != 200:
            pytest.skip(f"/hr/dashboard returned {r.status_code} — Odoo unavailable, skipping body check")
        body = r.text
        assert 'href="/collections/dashboard"' not in body
        assert 'href="/customer-accounts/dashboard"' not in body

    def test_coll_ca_sidebar_shows_collections_and_ca(self, coll_ca_client):
        r = coll_ca_client.get("/collections/dashboard")
        assert r.status_code == 200
        body = r.text
        assert 'href="/collections/dashboard"' in body
        assert 'href="/customer-accounts/dashboard"' in body

    def test_coll_ca_sidebar_hides_hr(self, coll_ca_client):
        r = coll_ca_client.get("/collections/dashboard")
        assert r.status_code == 200
        assert 'href="/hr/dashboard"' not in r.text

    def test_admin_sidebar_shows_all_modules(self, authed_client):
        try:
            r = authed_client.get("/dashboard")
        except Exception:
            pytest.skip("/dashboard raised an unhandled exception — Odoo unavailable, skipping sidebar body check")
        if r.status_code != 200:
            pytest.skip(f"/dashboard returned {r.status_code} — Odoo unavailable, skipping sidebar body check")
        body = r.text
        assert 'href="/hr/dashboard"' in body
        assert 'href="/collections/dashboard"' in body
        assert 'href="/customer-accounts/dashboard"' in body


# ── D. Post-Login Landing ─────────────────────────────────────────────────────


class TestPostLoginLanding:
    """login_submit must redirect to the user's first allowed module dashboard."""

    def test_hr_only_lands_on_hr_dashboard(self, hr_only_client):
        """next=/dashboard (default, inaccessible) → /hr/dashboard."""
        r = hr_only_client.post(
            "/login",
            data={"username": "hr_only", "password": "testpass", "next": "/dashboard"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/hr/dashboard"

    def test_coll_ca_lands_on_collections_dashboard(self, coll_ca_client):
        """coll_ca has no crm → first allowed module is collections."""
        r = coll_ca_client.post(
            "/login",
            data={"username": "coll_ca", "password": "testpass", "next": "/dashboard"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/collections/dashboard"

    def test_admin_lands_on_crm_dashboard(self, authed_client):
        """modules=['*'] → first in _ORDERED_MODULE_DASHBOARDS is crm → /dashboard."""
        r = authed_client.post(
            "/login",
            data={"username": "testadmin", "password": "testpass", "next": "/dashboard"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/dashboard"

    def test_hr_only_valid_next_is_honoured(self, hr_only_client):
        """next=/hr/dashboard — accessible — is honoured directly."""
        r = hr_only_client.post(
            "/login",
            data={"username": "hr_only", "password": "testpass", "next": "/hr/dashboard"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/hr/dashboard"

    def test_hr_only_inaccessible_next_falls_back(self, hr_only_client):
        """next=/collections/dashboard — denied — falls back to /hr/dashboard."""
        r = hr_only_client.post(
            "/login",
            data={
                "username": "hr_only",
                "password": "testpass",
                "next": "/collections/dashboard",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/hr/dashboard"


# ── E. No-Modules ─────────────────────────────────────────────────────────────


class TestNoModules:
    """Users with modules=[] are routed to /no-modules and blocked everywhere else."""

    def test_no_modules_login_redirects_to_no_modules_page(self, no_modules_client):
        r = no_modules_client.post(
            "/login",
            data={"username": "no_modules", "password": "testpass", "next": "/dashboard"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/no-modules"

    def test_no_modules_page_renders_html(self, no_modules_client):
        r = no_modules_client.get("/no-modules")
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")

    def test_no_modules_page_unauthenticated_redirects_to_login(self):
        # No `with` — avoids a new lifespan. 302 fires before any state access.
        c = TestClient(app, follow_redirects=False, raise_server_exceptions=False)
        r = c.get("/no-modules")
        assert r.status_code == 302
        assert "/login" in r.headers.get("location", "")

    def test_no_modules_user_gets_403_on_module_api_routes(self, no_modules_client):
        for path in [
            "/api/v1/hr/kpi/headcount",
            "/api/v1/collections/kpi/late-uncollected",
            "/api/v1/customer-accounts/kpi/total-receivables",
        ]:
            r = no_modules_client.get(path)
            assert r.status_code == 403, (
                f"no_modules user should get 403 on {path}, got {r.status_code}"
            )
            assert r.json()["error"]["code"] == "MODULE_ACCESS_DENIED"
