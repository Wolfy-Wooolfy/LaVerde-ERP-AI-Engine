"""Guard: the manual Refresh button must reach Odoo, and only when a human asks.

WHAT THIS GUARDS
----------------
backend/main.py:233 turns ``?refresh=1`` on a GET into a process-wide
cache-bypass signal, and all seven cache read seams honour it. The frontend
never sent it. The button therefore re-fetched through the very cache the user
was trying to escape, and on the ten server-rendered pages it fetched CRM KPIs
that match nothing on screen — a capability that existed end to end on the
server and was unreachable from the UI.

Wiring it up creates a mirror-image hazard that is worse than the original bug:
if an AUTOMATIC refetch ever carries ``refresh=1``, the cache is defeated on
every tick and Odoo load returns to exactly what Phase 1 (79e9b15) was created
to reduce — silently, because everything still renders correctly. So this file
asserts both directions: manual paths must send it, automatic paths must not.

WHY STATIC CONTRACT TESTS
-------------------------
The logic under test is browser JavaScript. The e2e suite is deselected from
the bare gate (``-m 'not e2e'``) and needs a live server plus playwright, and
this repo's pytest gate has no node bridge — introducing one would make the
gate fail on any machine without node. So these tests read the shipped sources
and pin the properties that make the behaviour correct, in the same style as
tests/unit/core/test_kpi_vocabulary_consistency.py.

The behavioural half — that the helper really does preserve query parameters
and really is idempotent when applied twice — is proven by executing the code
in tests/frontend/test_refresh_url.js (``node tests/frontend/test_refresh_url.js``),
alongside the two node suites already in that directory.

WHAT THIS FILE CANNOT GUARD
---------------------------
1. That a click actually reaches the handler in a browser. That needs e2e.
2. That the reload lands on a page whose data really did change. Only Odoo
   knows that.
3. Anything about /settings or the balance sheet, both deliberately excluded:
   Settings has no Odoo data, and /api/v1/accounting/balance-sheet is
   uncached by design (accounting.py:75-78), so sending refresh=1 there would
   imply a cache that does not exist.
"""

import re

from backend.core.static_manifest import STATIC_DIR
from backend.core.templates import templates

# ── Source access ─────────────────────────────────────────────────────────────
# Both readers go through the app's own locators (the Jinja loader, the static
# manifest's STATIC_DIR) rather than hardcoded relative paths, so these tests
# follow the assets if they move instead of failing for the wrong reason.


def _template(name: str) -> str:
    source, _filename, _uptodate = templates.env.loader.get_source(templates.env, name)
    return source


def _js(name: str) -> str:
    return (STATIC_DIR / "js" / name).read_text(encoding="utf-8")


def _strip_comments(js: str) -> str:
    """Drop /* … */ and // … comments.

    Every assertion below asks "does the shipped code do X". A commented-out
    call or an example inside a docstring must not satisfy that, and the
    explanatory comments this feature carries mention the very identifiers
    being searched for.
    """
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    # The negative lookbehind keeps `http://…` inside a string literal intact;
    # without it this would silently truncate the rest of that line and the
    # assertions would fail for a reason that has nothing to do with the code.
    return re.sub(r"(?m)(?<!:)//.*$", "", js)


# The two page bundles that fetch their own KPIs client-side. The CRM dashboard
# is handled by app.js itself; the balance sheet is excluded (uncached route).
_CLIENT_FETCH_BUNDLES = ("collections.js", "customer_accounts.js")

# Pages whose data arrives with the page GET and which therefore opt in to the
# reload strategy. Hardcoded ON PURPOSE — this list IS the scope decision, and
# test_every_base_page_declares_a_refresh_strategy below proves it stays
# exhaustive, so a sixteenth page cannot quietly inherit either behaviour.
_SSR_TEMPLATES = (
    "hr/dashboard.html",
    "projects_inventory/dashboard.html",
    "projects_inventory/value_area.html",
    "projects_inventory/pricing_outliers.html",
    "projects_inventory/data_quality.html",
    "marketing_attribution/dashboard.html",
    "marketing_attribution/timeline.html",
    "campaign_performance/dashboard.html",
    "campaign_performance/timeline.html",
    "data_quality.html",
)

# Client-fetch pages: they refresh in place, so they must NOT opt in to reload.
_CLIENT_FETCH_TEMPLATES = (
    "dashboard.html",
    "collections/dashboard.html",
    "customer_accounts/dashboard.html",
    "accounting/balance_sheet.html",
)

# No Odoo data at all — excluded from the feature by the approved scope.
_NO_DATA_TEMPLATES = ("settings.html",)


# ── T2. the URL-merge helper ──────────────────────────────────────────────────


def test_url_helper_exists_on_one_seam_only() -> None:
    """T2(a). Exactly one definition of the helper, and it lives in api.js.

    A second copy is the realistic failure mode: a page bundle grows its own
    `addRefresh()` and drifts. The whole point of a single seam is that the
    manual-only rule has one place to be enforced and one place to be audited.
    """
    api = _strip_comments(_js("api.js"))
    assert re.search(r"window\.crmWithRefresh\s*=\s*function", api), (
        "crmWithRefresh is not defined in api.js — the URL seam the manual "
        "refresh depends on is missing or moved"
    )

    defined_in = [
        name
        for name in ("app.js", "collections.js", "customer_accounts.js")
        if re.search(r"crmWithRefresh\s*=\s*function", _strip_comments(_js(name)))
    ]
    assert not defined_in, (
        f"crmWithRefresh is redefined in {defined_in}. One seam, one definition: "
        f"a second copy can drift out of the manual-only rule unnoticed."
    )


def test_url_helper_uses_searchparams_set_not_string_concatenation() -> None:
    """T2(b). The single implementation property that makes the helper both
    param-preserving AND idempotent.

    `URLSearchParams.set` replaces an existing key and leaves every other key
    alone. `append` would stack `refresh=1&refresh=1` on a second application;
    `url + '?refresh=1'` would corrupt any URL that already has a query string
    — which is every SSR page that carries window / tab / months / campaign_id.

    The behavioural proof that this actually holds lives in
    tests/frontend/test_refresh_url.js.
    """
    api = _strip_comments(_js("api.js"))
    helper = _helper_body(api)

    assert "new URL(" in helper, (
        "the helper does not parse the URL — string surgery cannot preserve an "
        "existing query string correctly"
    )
    assert re.search(r"searchParams\.set\(\s*['\"]refresh['\"]\s*,\s*['\"]1['\"]\s*\)", helper), (
        "the helper does not call searchParams.set('refresh', '1')"
    )
    assert "searchParams.append" not in helper, (
        "the helper uses append(): applying it twice yields refresh=1&refresh=1"
    )
    assert not re.search(r"['\"][?&]refresh=", helper), (
        "the helper concatenates a literal ?refresh= / &refresh= — that corrupts "
        "URLs that already carry a query string (window, tab, months, campaign_id)"
    )


def test_url_helper_is_a_no_op_without_the_manual_flag() -> None:
    """T2(c). The helper's own guard clause: no `manual`, no parameter.

    This is the last line of defence behind the call-site rules in T3. If the
    helper ever stamped refresh=1 unconditionally, every automatic refetch
    would bypass the cache and no test asserting call sites would notice.
    """
    helper = _helper_body(_strip_comments(_js("api.js")))
    assert re.search(r"if\s*\(\s*!\s*manual\s*\)\s*return\s+url\s*;", helper), (
        "crmWithRefresh has no `if (!manual) return url;` guard — it can no "
        "longer be trusted to leave automatic refetches alone"
    )


def _helper_body(api_js: str) -> str:
    """Isolate the crmWithRefresh function body, so the assertions above cannot
    be satisfied by unrelated code elsewhere in api.js."""
    start = api_js.index("window.crmWithRefresh")
    depth = 0
    for i in range(start, len(api_js)):
        if api_js[i] == "{":
            depth += 1
        elif api_js[i] == "}":
            depth -= 1
            if depth == 0:
                return api_js[start : i + 1]
    raise AssertionError("crmWithRefresh body is unbalanced — could not isolate it")


# ── T3. manual vs automatic ───────────────────────────────────────────────────


def test_crm_refresh_routes_its_url_through_the_helper() -> None:
    """T3(a). The CRM KPI fetch must pass its `manual` argument to the helper.

    Hardcoding `crmWithRefresh(url, true)` here would be the bug this whole
    file exists to prevent, so the argument is asserted to be the parameter.
    """
    app = _strip_comments(_js("app.js"))
    assert re.search(
        r"crmApi\.get\(\s*crmWithRefresh\(\s*['\"]/api/v1/dashboard/kpis['\"]\s*,\s*manual\s*\)",
        app,
    ), "crmRefresh no longer routes /api/v1/dashboard/kpis through crmWithRefresh(url, manual)"


def test_automatic_refresh_paths_pass_no_manual_flag() -> None:
    """T3(b). The timers and the visibility handler must call the fetch
    functions with ZERO arguments, so `manual` lands as undefined.

    These are the paths that run unattended, once an hour, on every open tab.
    A `true` here is invisible in the UI and doubles as a permanent cache
    bypass — the exact regression Phase 1 was written to undo.
    """
    app = _strip_comments(_js("app.js"))
    # Anchored to the autoRefresh body: a bare crmRefresh() anywhere in the file
    # would satisfy a looser pattern while the hourly timer passed `true`.
    assert re.search(
        r"typeof\s+crmRefresh\s*===\s*['\"]function['\"]\s*\)\s*await\s+crmRefresh\(\s*\)", app
    ), "app.js's hourly autoRefresh no longer calls crmRefresh() with no argument"

    for name in _CLIENT_FETCH_BUNDLES:
        src = _strip_comments(_js(name))
        assert re.search(r"setInterval\(\s*fetchAllKPIs\s*,", src), (
            f"{name}: the hourly timer no longer passes fetchAllKPIs as a bare "
            f"reference — check it is not wrapped in a call that supplies `true`"
        )
        # Property, not shape. This assertion used to pin the literal text
        # `fetchAllKPIs().then(startAutoRefresh)`. On 2026-08-20 every caller was
        # rerouted through restartTimersAfter() so that a failed fetch could no
        # longer kill both timers, which changed that text at all three call
        # sites. The PROPERTY this test exists to guard — automatic paths never
        # request a cache bypass — is unaffected, so it is now asserted directly
        # and will survive the next reshaping too.
        #
        # Every call must pass either nothing (automatic) or the bare identifier
        # `manual` (forwarding). A literal `true` anywhere here is the
        # regression: it would bypass the cache on an unattended hourly tick.
        calls = re.findall(r"(?<!function )fetchAllKPIs\(([^)]*)\)", src)
        assert calls, f"{name}: extracted no fetchAllKPIs call sites — the file moved"
        bad = [a.strip() for a in calls if a.strip() not in ("", "manual")]
        assert not bad, (
            f"{name}: fetchAllKPIs called with {bad} — an automatic path must pass "
            f"nothing and a forwarding path must pass the `manual` parameter, never "
            f"a literal. A hardcoded `true` bypasses the cache on every hourly tick."
        )
        assert any(a.strip() == "" for a in calls), (
            f"{name}: no argument-less fetchAllKPIs call left — the automatic paths "
            f"(initial load, visibilitychange) have gone or now pass a flag"
        )
        assert any(a.strip() == "manual" for a in calls), (
            f"{name}: nothing forwards `manual` to fetchAllKPIs, so the manual "
            f"refresh can no longer bypass the cache"
        )


def test_manual_entry_points_pass_true() -> None:
    """T3(c). Every human-initiated entry point opts in explicitly."""
    app = _strip_comments(_js("app.js"))
    assert re.search(r"crmManualRefresh\(\s*\)", app), (
        "app.js no longer exposes/uses crmManualRefresh() — the single manual entry point"
    )
    assert re.search(r"crmRefresh\(\s*true\s*\)", app), (
        "no manual crmRefresh(true) call site left in app.js"
    )

    for name, fn in (
        ("collections.js", "collectionsRefresh"),
        ("customer_accounts.js", "customerAccountsRefresh"),
    ):
        src = _strip_comments(_js(name))
        assert re.search(rf"{fn}\(\s*true\s*\)", src), (
            f"{name}: the topbar rebinding no longer calls {fn}(true), so a manual "
            f"refresh on this page still reads through the cache"
        )
        assert re.search(r"fetch\(\s*crmWithRefresh\(\s*url\s*,\s*manual\s*\)", src), (
            f"{name}: fetchAllKPIs no longer routes its URLs through "
            f"crmWithRefresh(url, manual)"
        )


def test_refresh_button_is_never_bound_by_bare_function_reference() -> None:
    """T3(d). `btn.onclick = collectionsRefresh` must never come back.

    A bare reference makes the browser pass the MouseEvent as the first
    argument, so `manual` is an object — truthy. The button would appear to
    work, for entirely the wrong reason, and the same function called from
    anywhere else (a timer, a test, another module) would silently take the
    automatic path. Assigning a wrapper keeps the flag explicit at both ends.
    """
    for name, fn in (
        ("collections.js", "collectionsRefresh"),
        ("customer_accounts.js", "customerAccountsRefresh"),
    ):
        src = _strip_comments(_js(name))
        bare = re.search(rf"onclick\s*=\s*(?:window\.)?{fn}\s*;", src)
        assert not bare, (
            f"{name}: #refresh-btn is bound to a bare {fn} reference. The click "
            f"event object then arrives as `manual` and is truthy by accident — "
            f"wrap it: onclick = function () {{ {fn}(true); }};"
        )


# ── T4. the honest toast (097b48d) must survive the rewiring ──────────────────


def test_toast_success_is_gated_on_matched_kpi_selectors() -> None:
    """T4. 097b48d stopped the button claiming success on pages it cannot
    update. crmRefresh is being rewritten around it, so pin the gate.

    Only `dashboard.html` renders [data-kpi-value] (via _kpi_card.html), so on
    every other page matchedKpis is 0 and the neutral message is the only
    truthful one available.
    """
    app = _strip_comments(_js("app.js"))
    assert re.search(r"if\s*\(\s*matchedKpis\s*>\s*0\s*\)", app), (
        "the matchedKpis > 0 gate is gone — 'Data refreshed' can now be shown on "
        "a page where nothing was refreshed (the bug 097b48d fixed)"
    )
    assert "'Data refreshed'" in app or '"Data refreshed"' in app
    assert "'Nothing to refresh on this page'" in app or '"Nothing to refresh on this page"' in app

    success_branch = app[app.index("if (matchedKpis > 0)") :]
    else_at = success_branch.index("} else {")
    assert "Data refreshed" in success_branch[:else_at], (
        "'Data refreshed' is no longer inside the matchedKpis > 0 branch"
    )
    assert "Nothing to refresh on this page" in success_branch[else_at:], (
        "the neutral message is no longer in the else branch"
    )
    # The element LOOKUP belongs outside the branch (it decides timestampFound);
    # it is the WRITE that must stay inside it.
    assert "lu.textContent" in success_branch[:else_at], (
        "the timestamp write escaped the success branch — a clock that ticks on "
        "a page that did not refresh is the same false signal in another form"
    )
    assert "lu.textContent" not in success_branch[else_at:], (
        "the timestamp is written on the 'nothing refreshed' path too"
    )


# ── T5. strip refresh=1 after an SSR reload ───────────────────────────────────


def test_refresh_param_is_stripped_from_the_url_after_load() -> None:
    """T5. ``refresh=1`` must not survive in the address bar.

    The SSR strategy reloads the page with the parameter attached, which leaves
    it in the URL. Left there it is inherited by F5, by a bookmark and by any
    link the user copies and shares — turning a one-off manual bypass into a
    permanent one for every recipient, against a cache whose entire purpose is
    to keep Odoo load survivable.

    history.replaceState (not pushState) is required: pushState would add a
    history entry, so Back would return to the refreshing URL and re-arm it.
    """
    app = _strip_comments(_js("app.js"))
    assert re.search(r"searchParams\.delete\(\s*['\"]refresh['\"]\s*\)", app), (
        "nothing removes the refresh parameter from the URL after load"
    )
    assert "history.replaceState" in app, (
        "the refresh parameter is not removed via history.replaceState"
    )
    assert "history.pushState" not in app, (
        "pushState adds a history entry, so Back returns to the ?refresh=1 URL "
        "and re-arms the bypass — replaceState is required here"
    )


# ── the SSR opt-in contract ───────────────────────────────────────────────────


def test_base_renders_the_refresh_mode_attribute_from_a_block() -> None:
    """The attribute exists in exactly one DOM location, with an explicit
    default, so a page opts IN by overriding a block and never by accident."""
    base = _template("base.html")
    assert re.search(
        r"data-refresh-mode=\"\{%\s*block\s+refresh_mode\s*%\}fetch\{%\s*endblock\s*%\}\"", base
    ), (
        "base.html no longer renders data-refresh-mode from a refresh_mode block "
        "defaulting to 'fetch' — the opt-in contract is broken"
    )


def test_every_ssr_page_opts_in_to_reload() -> None:
    for name in _SSR_TEMPLATES:
        src = _template(name)
        assert re.search(r"\{%\s*block\s+refresh_mode\s*%\}\s*reload\s*\{%\s*endblock\s*%\}", src), (
            f"{name} is server-rendered but does not declare "
            f"{{% block refresh_mode %}}reload{{% endblock %}} — its Refresh button "
            f"would fetch CRM KPIs and update nothing on the page"
        )


def test_client_fetch_pages_do_not_opt_in_to_reload() -> None:
    """A client-fetch page that reloaded would throw away the in-place update
    it already does correctly — and on the balance sheet it would attach
    refresh=1 to a route that is uncached by design."""
    for name in _CLIENT_FETCH_TEMPLATES + _NO_DATA_TEMPLATES:
        src = _template(name)
        assert "refresh_mode" not in src, (
            f"{name} refreshes in place (or carries no Odoo data) but declares a "
            f"refresh_mode override"
        )


def test_every_base_page_declares_a_refresh_strategy() -> None:
    """Anti-vacuity, and the page-#16 guard.

    The two lists above are hardcoded, so both preceding tests would keep
    passing on the day a new page is added and silently inherits whichever
    default it happens to get. This compares them against what is actually on
    disk: every template extending base.html renders the shared Refresh button
    (base.html:684 sits outside every block), so every one of them must have
    been assigned a strategy on purpose.
    """
    root = STATIC_DIR.parent / "templates"
    on_disk = {
        p.relative_to(root).as_posix()
        for p in root.rglob("*.html")
        if re.search(r"\{%\s*extends\s+[\"']base\.html[\"']\s*%\}", p.read_text(encoding="utf-8"))
    }
    assert on_disk, "found no templates extending base.html — the templates directory moved"

    classified = set(_SSR_TEMPLATES) | set(_CLIENT_FETCH_TEMPLATES) | set(_NO_DATA_TEMPLATES)
    unclassified = sorted(on_disk - classified)
    stale = sorted(classified - on_disk)

    assert not unclassified, (
        f"these pages render the shared Refresh button but are in no list: "
        f"{unclassified}. Add each to _SSR_TEMPLATES (data arrives with the page "
        f"GET), _CLIENT_FETCH_TEMPLATES (refreshes in place) or _NO_DATA_TEMPLATES."
    )
    assert not stale, f"listed templates that no longer exist: {stale}"


# ── a failed fetch must not kill the page's timers ────────────────────────────


def _restart_helper_body(src: str) -> str:
    """Isolate restartTimersAfter's body, brace-balanced from its declaration."""
    start = src.index("function restartTimersAfter")
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
    raise AssertionError("restartTimersAfter body is unbalanced — could not isolate it")


def test_every_fetch_caller_routes_through_the_timer_restart_helper() -> None:
    """No caller may consume fetchAllKPIs's promise directly.

    fetchAllKPIs re-throws after showing the error banner, so `.then(restart)`
    skips the restart on failure AND leaves an unhandled rejection. Both
    dashboards had that shape at all three call sites: the manual button, the
    visibilitychange re-fetch, and the initial page load.

    The initial load was the severe one. Its timers had never started, so a
    single failed first fetch left the page with no auto-refresh and no
    heartbeat permanently — with no manual click available to recover, because
    the user had not interacted with the page at all.
    """
    for name in _CLIENT_FETCH_BUNDLES:
        src = _strip_comments(_js(name))

        assert "function restartTimersAfter" in src, (
            f"{name}: restartTimersAfter is gone — the one place that guarantees "
            f"both timers restart whatever the fetch did"
        )
        assert not re.search(r"fetchAllKPIs\([^)]*\)\s*\.then\(", src), (
            f"{name}: a caller chains .then() straight onto fetchAllKPIs again. "
            f"That skips the restart on failure and leaves an unhandled rejection "
            f"— route it through restartTimersAfter() instead."
        )
        assert not re.search(r"fetchAll\(\s*\)\s*\.then\(", src), (
            f"{name}: a caller chains .then() onto the exposed fetchAll() — same "
            f"defect reached through the dashboard object"
        )

        callers = re.findall(r"(?<!function )restartTimersAfter\(", src)
        assert len(callers) >= 3, (
            f"{name}: only {len(callers)} restartTimersAfter call site(s); all three "
            f"(manual refresh, visibilitychange, initial load) must go through it"
        )


def test_the_restart_helper_restarts_both_timers_on_every_outcome() -> None:
    """The rejection is swallowed so ONE restart path serves both outcomes.

    Swallowing is what makes a single `.then()` reachable after a failure; the
    alternative — .then(onOk, onErr) — duplicates the restart body and lets the
    two copies drift.
    """
    for name in _CLIENT_FETCH_BUNDLES:
        body = _restart_helper_body(_strip_comments(_js(name)))

        assert ".catch(" in body, (
            f"{name}: restartTimersAfter no longer catches. The rejection escapes "
            f"and the restart never runs on a failed fetch."
        )
        assert ".then(" in body, f"{name}: restartTimersAfter no longer chains a restart"

        catch_at, then_at = body.index(".catch("), body.rindex(".then(")
        assert catch_at < then_at, (
            f"{name}: the .then() restart runs BEFORE the .catch(), so it is still "
            f"skipped when the fetch rejects"
        )
        restart = body[then_at:]
        for fn in ("startAutoRefresh()", "startHeartbeat()"):
            assert fn in restart, (
                f"{name}: {fn} is not in the post-catch restart, so a failed fetch "
                f"still leaves that timer dead for the life of the page"
            )


def test_the_swallowing_catch_is_never_silent() -> None:
    """A swallowed rejection must still reach the console.

    fetchAllKPIs wraps the render functions, not just the network calls. A
    genuine coding error inside renderSection1..4 / renderKpiA..C is caught
    there, mis-reported to the user as a connection error, and re-thrown — so
    without a log here it would be swallowed with no trace in any surface a
    developer looks at.
    """
    for name, prefix in (
        ("collections.js", "[Collections]"),
        ("customer_accounts.js", "[CustomerAccounts]"),
    ):
        body = _restart_helper_body(_strip_comments(_js(name)))
        catch_body = body[body.index(".catch(") : body.rindex(".then(")]

        assert "console.error(" in catch_body, (
            f"{name}: restartTimersAfter swallows the rejection without logging it. "
            f"A real bug in the render path would disappear entirely."
        )
        assert prefix in catch_body, (
            f"{name}: the swallow log carries no {prefix} module prefix, so it "
            f"cannot be attributed in a console shared by every page script"
        )
        assert re.search(r"console\.error\([^)]*,\s*err\s*\)", catch_body), (
            f"{name}: the swallow log drops the error object — a message with no "
            f"stack is barely better than silence"
        )
