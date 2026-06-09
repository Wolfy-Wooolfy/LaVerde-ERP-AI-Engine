"""
Playwright end-to-end tests for the CRM AI Engine dashboard.

Prerequisites:
  pip install playwright pytest-playwright
  playwright install chromium

Run:
  pytest tests/e2e/ --base-url http://localhost:8000 -v
  (requires running server: uvicorn backend.main:app --reload)
"""

import re

import pytest

# Skip entire module if playwright is not installed
pytest.importorskip("playwright.sync_api", reason="playwright not installed")

from playwright.sync_api import Page, expect  # noqa: E402

BASE_URL = "http://localhost:8000"
USERNAME = "admin"
PASSWORD = "password"


def authenticate(page: "Page") -> None:
    """Log in via the /login form to obtain a session cookie."""
    page.goto(f"{BASE_URL}/login")
    page.fill("input[name='username']", USERNAME)
    page.fill("input[name='password']", PASSWORD)
    page.click("button[type='submit']")
    page.wait_for_load_state("networkidle")


# ── Dashboard loads ───────────────────────────────────────────────────────────


@pytest.mark.e2e
def test_dashboard_loads(page: "Page") -> None:
    authenticate(page)
    page.goto(f"{BASE_URL}/dashboard")
    expect(page).to_have_title(re.compile("Dashboard"))
    expect(page.locator("h1")).to_be_visible()


@pytest.mark.e2e
def test_kpi_cards_visible(page: "Page") -> None:
    authenticate(page)
    page.goto(f"{BASE_URL}/dashboard")
    # There should be at least 5 KPI cards
    cards = page.locator(".kpi-card")
    expect(cards.first).to_be_visible()
    count = cards.count()
    assert count >= 5, f"Expected >= 5 KPI cards, got {count}"


@pytest.mark.e2e
def test_charts_render(page: "Page") -> None:
    authenticate(page)
    page.goto(f"{BASE_URL}/dashboard")
    page.wait_for_timeout(1500)  # allow Chart.js to render
    activity_canvas = page.locator("#activityChart")
    expect(activity_canvas).to_be_visible()


@pytest.mark.e2e
def test_heatmap_visible(page: "Page") -> None:
    authenticate(page)
    page.goto(f"{BASE_URL}/dashboard")
    heatmap = page.locator("table").first
    expect(heatmap).to_be_visible()


@pytest.mark.e2e
def test_tabs_work(page: "Page") -> None:
    authenticate(page)
    page.goto(f"{BASE_URL}/dashboard")
    # Click "By Team" tab
    team_tab = page.get_by_role("button", name="By Team")
    if team_tab.is_visible():
        team_tab.click()
        page.wait_for_timeout(200)
        # teamTable should now be in DOM
        assert page.locator("#teamTable").count() > 0


# ── Missing Contacts page ─────────────────────────────────────────────────────


@pytest.mark.e2e
def test_missing_contacts_page_loads(page: "Page") -> None:
    authenticate(page)
    page.goto(f"{BASE_URL}/data-quality/missing-contact")
    expect(page).to_have_title(re.compile("Missing"))
    expect(page.locator("h1")).to_be_visible()


@pytest.mark.e2e
def test_missing_contacts_table_visible(page: "Page") -> None:
    authenticate(page)
    page.goto(f"{BASE_URL}/data-quality/missing-contact")
    table = page.locator("#contactsTable")
    if table.count() > 0:
        expect(table).to_be_visible()
    else:
        # Empty state should be shown
        expect(page.locator(".flex-col.items-center")).to_be_visible()


# ── Theme toggle ──────────────────────────────────────────────────────────────


@pytest.mark.e2e
def test_dark_mode_toggle(page: "Page") -> None:
    authenticate(page)
    page.goto(f"{BASE_URL}/dashboard")
    # Set dark mode via localStorage
    page.evaluate("localStorage.setItem('crmTheme', 'dark')")
    page.reload()
    # html element should have class 'dark'
    html_class = page.locator("html").get_attribute("class") or ""
    assert "dark" in html_class


@pytest.mark.e2e
def test_light_mode_toggle(page: "Page") -> None:
    authenticate(page)
    page.goto(f"{BASE_URL}/dashboard")
    page.evaluate("localStorage.setItem('crmTheme', 'light')")
    page.reload()
    html_class = page.locator("html").get_attribute("class") or ""
    assert "dark" not in html_class


# ── Language toggle ───────────────────────────────────────────────────────────


@pytest.mark.e2e
def test_rtl_mode(page: "Page") -> None:
    authenticate(page)
    page.goto(f"{BASE_URL}/dashboard")
    # Set language to Arabic
    page.evaluate("document.cookie = 'lang=ar;path=/'")
    page.reload()
    html_dir = page.locator("html").get_attribute("dir") or "ltr"
    assert html_dir == "rtl"


# ── Authentication ────────────────────────────────────────────────────────────


@pytest.mark.e2e
def test_unauthenticated_redirect(page: "Page") -> None:
    # No login — unauthenticated dashboard access should redirect to /login
    page.goto(f"{BASE_URL}/dashboard")
    assert "/login" in page.url


# ── Mobile responsive ─────────────────────────────────────────────────────────


@pytest.mark.e2e
def test_mobile_layout(page: "Page") -> None:
    authenticate(page)
    page.set_viewport_size({"width": 390, "height": 844})  # iPhone 14
    page.goto(f"{BASE_URL}/dashboard")
    expect(page.locator("h1")).to_be_visible()
    # Sidebar should be hidden on mobile (use hamburger)
    sidebar = page.locator("aside.sidebar")
    # On mobile, sidebar is hidden (lg:flex) so not visible
    assert not sidebar.is_visible() or page.viewport_size["width"] >= 1024


# ── Security headers ──────────────────────────────────────────────────────────


@pytest.mark.e2e
def test_security_headers(page: "Page") -> None:
    response = page.goto(f"{BASE_URL}/health")
    assert response is not None
    headers = response.headers
    assert headers.get("x-content-type-options") == "nosniff"
    assert headers.get("x-frame-options") == "DENY"
    assert "x-request-id" in headers
