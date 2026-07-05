"""
Route tests for GET /accounting/balance-sheet — the HTML shell (Module 4 · Phase 2).

The route serves the Jinja2 shell ONLY: no Odoo call, no service call — all
figures arrive client-side from /api/v1/accounting/balance-sheet. These tests
therefore need no service patch at all; they mirror the Phase-1 pattern in
test_routes.py (dependency override + mocked user_repo) but override
get_current_user_html (the HTML session dependency), and pin the house HTML
auth behavior: 302 → /login?next=... unauthenticated, 403 without the module.
"""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_current_user_html
from backend.auth.models import UserRecord
from backend.main import app

_TESTADMIN_RECORD = UserRecord(
    username="testadmin", password_hash="", modules=["*"],
    is_admin=True, is_active=True,
    created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
)

# A non-admin user WITHOUT the "accounting" module — exercises the HTML gate.
_NO_ACCOUNTING_RECORD = UserRecord(
    username="collections_only", password_hash="", modules=["collections"],
    is_admin=False, is_active=True,
    created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
)

_URL = "/accounting/balance-sheet"


def _client_for(record: UserRecord) -> TestClient:
    app.dependency_overrides[get_current_user_html] = lambda: record.username
    mock_repo = MagicMock()
    mock_repo.get_user.return_value = record
    app.state.user_repo = mock_repo
    return TestClient(app, raise_server_exceptions=True)


def _teardown() -> None:
    app.dependency_overrides.pop(get_current_user_html, None)
    if hasattr(app.state, "user_repo"):
        del app.state.user_repo


@pytest.fixture
def client() -> TestClient:
    c = _client_for(_TESTADMIN_RECORD)
    yield c
    _teardown()


@pytest.fixture
def client_without_accounting() -> TestClient:
    c = _client_for(_NO_ACCOUNTING_RECORD)
    yield c
    _teardown()


# ── Test 1 — unauthenticated → 302 to /login with next ───────────────────────


def test_unauthenticated_redirects_302_to_login_with_next() -> None:
    """Same behavior as every collections/hr HTML route: no session → 302 to
    /login?next=<path>, never 401/403."""
    c = TestClient(app, raise_server_exceptions=True)
    r = c.get(_URL, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == f"/login?next={_URL}"


# ── Test 2 — authenticated without the module → 403 ──────────────────────────


def test_403_when_accounting_module_not_granted(client_without_accounting: TestClient) -> None:
    r = client_without_accounting.get(_URL, headers={"Accept": "text/html"})
    assert r.status_code == 403


# ── Test 3 — admin ("*") → 200 text/html shell ────────────────────────────────


def test_admin_gets_200_html_shell(client: TestClient) -> None:
    r = client.get(_URL)
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


# ── Test 4 — the rendered shell carries the mount markers ─────────────────────


def test_shell_contains_title_and_alpine_mount_markers(client: TestClient) -> None:
    """The shell must ship: the page title, the Alpine component mount, and
    the page's JS module — everything the client needs to fetch and render
    the statement. No figures are asserted: the shell contains none."""
    r = client.get(_URL)
    body = r.text
    assert 'id="bs-page-title"' in body
    assert "Balance Sheet" in body  # default lang is en (no cookie / header)
    assert 'x-data="balanceSheetPage()"' in body
    assert "/static/js/accounting_balance_sheet.js" in body
    # The shell fetches the Phase-1 API — the URL must be referenced in the JS,
    # never inlined figures in the HTML.
    assert "/api/v1/accounting/balance-sheet" not in body


def test_shell_renders_arabic_title_under_ar_locale(client: TestClient) -> None:
    r = client.get(_URL, headers={"accept-language": "ar"})
    assert r.status_code == 200
    assert "الميزانية العمومية" in r.text
    assert 'dir="rtl"' in r.text


# ── Test 5 — sidebar: admin sees the accounting link, active on this page ─────


def test_sidebar_link_present_and_active_for_admin(client: TestClient) -> None:
    r = client.get(_URL)
    body = r.text
    assert 'href="/accounting/balance-sheet"' in body
    # Active-state on this page (house sidebar convention): the Jinja
    # conditional renders exactly this class pair, and only for the page
    # whose ctx["page"] matches — i.e. only the accounting link here.
    assert 'class="sidebar-link active"' in body
