"""
Playwright verification for Phase 3 dropdown fixes.
Must pass before declaring Phase 3 complete.

Prerequisites:
  pip install playwright pytest-playwright
  playwright install chromium
  uvicorn backend.main:app --reload   (separate terminal)

Run (headed so you can watch):
  pytest tests/e2e/test_phase3_dropdowns.py -v --headed
Run (headless CI):
  pytest tests/e2e/test_phase3_dropdowns.py -v
"""

import base64

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright not installed")

from playwright.sync_api import Page, expect  # noqa: E402

BASE_URL = "http://localhost:8000"
USER = "admin"
PASS = "password"


# ── Fixture ───────────────────────────────────────────────────────────────────


@pytest.fixture
def auth_page(page: Page) -> Page:
    creds = base64.b64encode(f"{USER}:{PASS}".encode()).decode()
    page.set_extra_http_headers({"Authorization": f"Basic {creds}"})
    return page


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_no_csp_eval_errors(auth_page: Page) -> None:
    """Zero 'unsafe-eval' / EvalError violations in the browser console."""
    errors: list[str] = []
    auth_page.on(
        "console",
        lambda msg: errors.append(msg.text) if msg.type == "error" else None,
    )
    auth_page.goto(f"{BASE_URL}/dashboard")
    auth_page.wait_for_timeout(2000)
    eval_errors = [e for e in errors if "unsafe-eval" in e or "EvalError" in e]
    assert not eval_errors, "CSP eval errors found:\n" + "\n".join(eval_errors[:5])


def test_dropdowns_closed_on_load(auth_page: Page) -> None:
    """Both dropdown panels must be hidden immediately after page load."""
    auth_page.goto(f"{BASE_URL}/dashboard")
    auth_page.wait_for_timeout(1000)
    theme_panel = auth_page.locator('[data-dropdown="theme"]')
    lang_panel = auth_page.locator('[data-dropdown="lang"]')
    expect(theme_panel).to_be_hidden()
    expect(lang_panel).to_be_hidden()


def test_theme_dropdown_opens_and_closes(auth_page: Page) -> None:
    """Click theme trigger → panel opens; click outside → panel closes."""
    auth_page.goto(f"{BASE_URL}/dashboard")
    auth_page.wait_for_timeout(1000)

    theme_btn = auth_page.locator('[data-dropdown-trigger="theme"]')
    theme_panel = auth_page.locator('[data-dropdown="theme"]')

    # open
    theme_btn.click()
    auth_page.wait_for_timeout(400)
    expect(theme_panel).to_be_visible()

    # close by clicking outside
    auth_page.locator("h1, main").first.click()
    auth_page.wait_for_timeout(400)
    expect(theme_panel).to_be_hidden()


def test_only_one_dropdown_open_at_a_time(auth_page: Page) -> None:
    """Opening language dropdown must close the theme dropdown."""
    auth_page.goto(f"{BASE_URL}/dashboard")
    auth_page.wait_for_timeout(1000)

    theme_btn = auth_page.locator('[data-dropdown-trigger="theme"]')
    lang_btn = auth_page.locator('[data-dropdown-trigger="lang"]')
    theme_panel = auth_page.locator('[data-dropdown="theme"]')
    lang_panel = auth_page.locator('[data-dropdown="lang"]')

    theme_btn.click()
    auth_page.wait_for_timeout(400)
    expect(theme_panel).to_be_visible()
    expect(lang_panel).to_be_hidden()

    lang_btn.click()
    auth_page.wait_for_timeout(400)
    expect(lang_panel).to_be_visible()
    expect(theme_panel).to_be_hidden()


def test_theme_dark_applies_class(auth_page: Page) -> None:
    """Click 'Dark' option → <html> gets 'dark' class; dropdown closes."""
    auth_page.goto(f"{BASE_URL}/dashboard")
    auth_page.wait_for_timeout(1000)

    # make sure we start in non-dark (clear localStorage)
    auth_page.evaluate("localStorage.removeItem('crmTheme')")
    auth_page.reload()
    auth_page.wait_for_timeout(1000)

    auth_page.locator('[data-dropdown-trigger="theme"]').click()
    auth_page.wait_for_timeout(400)

    # click "Dark" (works in both EN and AR)
    auth_page.locator('[data-dropdown="theme"] button').filter(has_text="Dark").or_(
        auth_page.locator('[data-dropdown="theme"] button').filter(has_text="داكن")
    ).first.click()
    auth_page.wait_for_timeout(500)

    html_classes = auth_page.locator("html").get_attribute("class") or ""
    assert "dark" in html_classes, f"dark class missing on <html>: '{html_classes}'"
    expect(auth_page.locator('[data-dropdown="theme"]')).to_be_hidden()


def test_language_switch_sets_cookie(auth_page: Page) -> None:
    """Click English option → lang=en cookie set → page reloads."""
    auth_page.goto(f"{BASE_URL}/dashboard")
    auth_page.wait_for_timeout(1000)

    auth_page.locator('[data-dropdown-trigger="lang"]').click()
    auth_page.wait_for_timeout(400)

    # wait for navigation triggered by setLang (window.location.reload)
    with auth_page.expect_navigation(timeout=5000):
        auth_page.locator('[data-dropdown="lang"] button').filter(has_text="English").first.click()

    cookies = auth_page.context.cookies()
    lang_cookie = next((c for c in cookies if c["name"] == "lang"), None)
    assert lang_cookie is not None, "lang cookie was not set"
    assert lang_cookie["value"] == "en", f"expected lang=en, got lang={lang_cookie['value']}"


def test_view_in_odoo_link_correct(auth_page: Page) -> None:
    """Every row's 'View in Odoo' link must point to the real Odoo deep-link."""
    auth_page.goto(f"{BASE_URL}/data-quality/missing-contact")
    auth_page.wait_for_timeout(1000)

    link = auth_page.locator('a[title="View in Odoo"], a[title="عرض في Odoo"]').first
    if link.count() == 0:
        # no rows in this environment — skip structural check
        pytest.skip("no rows returned; cannot verify link href")

    href = link.get_attribute("href") or ""
    assert "/web#id=" in href, f"expected /web#id= in href, got: {href}"
    assert "model=crm.lead" in href, f"expected model=crm.lead in href, got: {href}"
    assert "view_type=form" in href, f"expected view_type=form in href, got: {href}"
    assert link.get_attribute("target") == "_blank", "expected target=_blank"
    assert link.get_attribute("rel") == "noopener noreferrer", "expected rel=noopener noreferrer"
