"""
E2E Playwright tests for the AI Priority Queue section on the dashboard.

Requires:
  - Server running at http://localhost:8000 with AI_ENABLED=true
  - Mock OpenAI server at http://localhost:9000/v1
  - pytest-playwright installed: pip install pytest-playwright && playwright install
"""

import pytest

BASE_URL = "http://localhost:8000"
AUTH_HEADER = {"Authorization": "Basic YWRtaW46cGFzc3dvcmQ="}  # admin:password


@pytest.fixture(scope="session")
def page(browser):
    ctx = browser.new_context(http_credentials={"username": "admin", "password": "password"})
    p = ctx.new_page()
    yield p
    ctx.close()


def test_dashboard_loads(page):
    page.goto(f"{BASE_URL}/dashboard")
    assert page.title() != ""
    page.wait_for_selector("#ai-priority-section", timeout=10000)


def test_ai_section_is_present(page):
    page.goto(f"{BASE_URL}/dashboard")
    section = page.query_selector("#ai-priority-section")
    assert section is not None, "AI priority section not found in DOM"


def test_ai_skeleton_shown_initially(page):
    """Skeleton is rendered server-side and visible before JS fires."""
    page.goto(f"{BASE_URL}/dashboard")
    skeleton = page.query_selector("#ai-skeleton")
    assert skeleton is not None, "AI skeleton not in DOM"


def test_ai_leads_appear_after_load(page):
    """After JS loads AI data, leads list becomes visible."""
    page.goto(f"{BASE_URL}/dashboard")
    # Wait for either the leads list or error state
    page.wait_for_function(
        "() => !document.getElementById('ai-leads-list').classList.contains('hidden') || "
        "       !document.getElementById('ai-error-state').classList.contains('hidden')",
        timeout=15000,
    )
    # At least one of them is visible — not both hidden
    leads_hidden = page.eval_on_selector("#ai-leads-list", "el => el.classList.contains('hidden')")
    error_hidden = page.eval_on_selector("#ai-error-state", "el => el.classList.contains('hidden')")
    assert not (leads_hidden and error_hidden), "Neither leads nor error state is visible after load"


def test_budget_pill_in_topbar(page):
    page.goto(f"{BASE_URL}/dashboard")
    pill = page.query_selector("#ai-budget-pill")
    assert pill is not None, "Budget pill not found in topbar"


def test_budget_pill_shows_spend(page):
    """After page load, budget pill should show actual spend text."""
    page.goto(f"{BASE_URL}/dashboard")
    page.wait_for_function(
        "() => { var l = document.getElementById('ai-budget-label'); return l && l.textContent.includes('AI:') && !l.textContent.includes('loading'); }",
        timeout=10000,
    )
    label = page.text_content("#ai-budget-label")
    assert "AI:" in label, f"Expected 'AI:' in label, got: {label}"


def test_refresh_button_exists(page):
    page.goto(f"{BASE_URL}/dashboard")
    btn = page.query_selector("#ai-refresh-btn")
    assert btn is not None, "AI refresh button not found"


def test_budget_button_opens_modal(page):
    page.goto(f"{BASE_URL}/dashboard")
    # Wait for page to stabilize
    page.wait_for_load_state("networkidle")
    # Find the Budget button (second button in the AI section header)
    page.click("text=Budget")
    modal = page.query_selector("#budget-modal")
    assert modal is not None
    # Modal should be visible
    modal_display = page.eval_on_selector("#budget-modal", "el => el.classList.contains('flex')")
    assert modal_display is True, "Budget modal did not open"


def test_console_has_no_errors(page):
    errors = []
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    page.goto(f"{BASE_URL}/dashboard")
    page.wait_for_load_state("networkidle")
    # Filter known benign browser errors
    real_errors = [e for e in errors if "favicon" not in e.lower() and "net::ERR" not in e]
    assert real_errors == [], f"Console errors: {real_errors}"
