"""Guard: every KPI card's metric name must be reachable in the /kpis payload.

WHAT THIS GUARDS
----------------
frontend/templates/components/_kpi_card.html feeds ONE macro argument,
`sparkline_metric`, into FOUR attributes — data-sparkline-metric (:41),
data-kpi-value (:76), data-kpi-trend (:80) and data-sparkline (:88). Two
consumers then read that single vocabulary with two different expectations:

  * app.js:146 matches ``[data-kpi-value="<key>"]`` against the KEYS OF THE
    /api/v1/dashboard/kpis PAYLOAD.
  * _METRIC_MAP in dashboard_api.py and charts.js (:239 :246 :250 :273) read
    the SHORT NAMES — "critical", "overdue", "missing_contact", ...

The two vocabularies diverged in 9286a7b, the commit that created both files,
and stayed divergent. Only `total_leads` and `followups_today` were spelled the
same on both sides, so five of the seven KPI cards — Critical Overdue, Overdue
Follow-ups, Missing Contact Info, Missing Salesperson and Data Quality Issues —
were never once updated by a refresh, from that commit until the short-name
aliases were added. It was never a regression: it shipped broken and the suite
stayed green the whole time, because the first server-rendered paint is always
correct and nothing compared the two vocabularies.

THIS TEST WOULD HAVE FAILED ON 9286a7b. That is its entire purpose.

WHAT THIS TEST CANNOT GUARD
---------------------------
1. charts.js:273 — ``['critical','overdue','missing_contact','data_quality']``
   is a bare literal array inside the JS function that picks the sparkline
   stroke colour. It is unreachable from Python, and tests/frontend/*.js are
   run by hand with `node` (never collected by pytest), so NOTHING in the
   pytest suite can see it. Renaming a short name without editing that array
   silently turns four red sparklines indigo, and no test anywhere fails.
2. Whether the rendered ``data-kpi-value`` attribute actually reaches the right
   DOM element, and whether crmRefresh() then animates it. That needs a
   browser. The e2e suite is skipped (playwright deliberately not installed)
   and only counts cards in any case.
3. app.js:146 itself. This asserts the two NAME SETS are compatible; it cannot
   assert the JavaScript consuming them is correct.

So: this guards the vocabulary contract. Not the rendering, not the JS.
"""

import re
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.deps import get_crm_service, get_current_user
from backend.api.v1.endpoints.dashboard_api import _METRIC_MAP
from backend.api.v1.endpoints.dashboard_api import router as dashboard_api_router
from backend.core.templates import templates
from backend.modules.crm.schemas import (
    ActivitySummary,
    DataQuality,
    FollowupRisk,
    SummaryResponse,
)

# Every field gets a DISTINCT value so that an alias wired to the wrong model
# attribute (the copy-paste failure mode) is detectable, not just a missing key.
_TOTAL_LEADS = 101
_FOLLOWUPS_TODAY = 102
_OVERDUE_FOLLOWUPS = 103
_PLANNED_FOLLOWUPS = 104
_NO_ACTIVITY_LEADS = 105
_CRITICAL_OVERDUE = 106
_MISSING_STAGE = 108
_MISSING_CONTACT = 109
_MISSING_SALESPERSON = 110
_TOTAL_DQ_ISSUES = 111


def _mock_summary() -> SummaryResponse:
    return SummaryResponse(
        mode="read_only",
        scope="resolved_opportunities_only",
        summary=ActivitySummary(
            total_leads=_TOTAL_LEADS,
            followups_today=_FOLLOWUPS_TODAY,
            overdue_followups=_OVERDUE_FOLLOWUPS,
            planned_followups=_PLANNED_FOLLOWUPS,
            no_activity_leads=_NO_ACTIVITY_LEADS,
            critical_overdue=_CRITICAL_OVERDUE,
            data_quality_issues=107,
        ),
        data_quality=DataQuality(
            missing_stage_count=_MISSING_STAGE,
            missing_contact_count=_MISSING_CONTACT,
            missing_salesperson_count=_MISSING_SALESPERSON,
            total_data_quality_issues=_TOTAL_DQ_ISSUES,
        ),
        followup_risk=FollowupRisk(
            overdue_by_salesperson=[],
            overdue_by_team=[],
            overdue_by_stage=[],
            overdue_matrix_by_team_salesperson_stage=[],
        ),
    )


# Mini app, no lifespan: the dashboard router is included on its own, so the
# module gate applied in backend/api/v1/router.py never runs, no user DB is
# touched and no Odoo connection is needed. Same isolation pattern as
# tests/unit/auth/test_settings_guards.py.
_probe_app = FastAPI()
_probe_app.include_router(dashboard_api_router)
_probe_app.dependency_overrides[get_current_user] = lambda: "kpi-vocabulary-probe"
_probe_app.dependency_overrides[get_crm_service] = lambda: _mock_crm_service()


def _mock_crm_service() -> MagicMock:
    svc = MagicMock()
    svc.summary = AsyncMock(return_value=_mock_summary())
    return svc


@pytest.fixture(scope="module")
def kpis_payload() -> dict:
    """The `kpis` sub-dict of a real /kpis response, served from a mocked service."""
    r = TestClient(_probe_app, raise_server_exceptions=True).get("/dashboard/kpis")
    assert r.status_code == 200, f"probe request failed: {r.status_code} — {r.text[:200]}"
    return r.json()["kpis"]


def _dashboard_template_source() -> str:
    """Read dashboard.html through the app's own Jinja loader, so this test
    follows the template if it ever moves rather than pinning a filesystem path."""
    source, _filename, _uptodate = templates.env.loader.get_source(templates.env, "dashboard.html")
    return source


def _card_metrics() -> list[str]:
    """Extract every sparkline_metric literal from dashboard.html.

    Extracted, never hardcoded: a hardcoded list of the seven known names would
    still pass on the day an eighth card is added under a name that matches
    nothing — which is precisely the failure this file exists to prevent.
    """
    return re.findall(r"""sparkline_metric\s*=\s*["']([^"']+)["']""", _dashboard_template_source())


def _card_invocation_count() -> int:
    """Count kpi_card(...) calls. The `{% from ... import kpi_card %}` line
    carries no parenthesis, so it is not counted."""
    return len(re.findall(r"kpi_card\s*\(", _dashboard_template_source()))


def test_every_card_metric_is_reachable_in_kpis_payload(kpis_payload: dict) -> None:
    """(i) Requirement B3(i). Fails on the pre-alias payload with five names
    missing: critical, overdue, missing_contact, missing_salesperson,
    data_quality — the five cards that never refreshed."""
    unreachable = sorted(set(_card_metrics()) - set(kpis_payload))
    assert not unreachable, (
        f"KPI cards whose data-kpi-value matches no /kpis key: {unreachable}. "
        f"app.js:146 will silently skip them and the card will never refresh. "
        f"Payload keys available: {sorted(kpis_payload)}"
    )


def test_every_card_metric_is_a_known_sparkline_metric() -> None:
    """(ii) Requirement B3(ii). The same literal is also the ?metric= query
    value; an unknown one makes /sparkline return HTTP 200 with ok:false, which
    charts.js:247 discards silently — blank canvas, blank trend badge, no error."""
    unknown = sorted(set(_card_metrics()) - set(_METRIC_MAP))
    assert not unknown, (
        f"KPI cards whose sparkline_metric is not in _METRIC_MAP: {unknown}. "
        f"Known metrics: {sorted(_METRIC_MAP)}"
    )


def test_alias_and_canonical_key_carry_the_same_value(kpis_payload: dict) -> None:
    """An alias that exists but reports the wrong number is worse than a missing
    one: the card would animate to a confidently wrong figure. Every mock field
    holds a distinct value, so a mis-wired alias cannot pass by coincidence."""
    for alias, canonical in (
        ("critical", "critical_overdue"),
        ("overdue", "overdue_followups"),
        ("missing_contact", "missing_contact_count"),
        ("missing_salesperson", "missing_salesperson_count"),
        ("data_quality", "data_quality_issues"),
    ):
        assert kpis_payload[alias] == kpis_payload[canonical], (
            f"alias {alias!r}={kpis_payload[alias]} disagrees with "
            f"{canonical!r}={kpis_payload[canonical]} — the alias reads the wrong field"
        )


def test_every_kpi_card_declares_a_sparkline_metric() -> None:
    """Anti-vacuity, and the card-#8 guard.

    Both subset assertions above pass trivially against an empty set, so the
    extraction must be proven non-empty. Equality with the kpi_card() call count
    additionally catches a new card added with no sparkline_metric at all: the
    macro renders data-kpi-value="" for it, which matches nothing and no-ops —
    the same silent failure, arrived at from the other direction.
    """
    metrics = _card_metrics()
    assert metrics, "extracted no sparkline_metric literals — the regex or the template moved"
    assert len(metrics) == _card_invocation_count(), (
        f"{_card_invocation_count()} kpi_card() calls but {len(metrics)} sparkline_metric "
        f"literals: {metrics}. A card without one renders data-kpi-value=\"\" and never refreshes."
    )


def test_short_name_aliases_are_purely_additive(kpis_payload: dict) -> None:
    """The aliases were chosen (option f) precisely BECAUSE they rename and
    remove nothing — external consumers of /kpis could not be enumerated by
    static inspection, so dropping a key was not acceptable.

    This list IS hardcoded on purpose, unlike the extracted card metrics above:
    its job is to pin the historical contract, so a future "tidy up" that
    deletes the now-duplicated long keys fails here loudly.
    """
    for key in (
        "total_leads",
        "critical_overdue",
        "overdue_followups",
        "followups_today",
        "planned_followups",
        "no_activity_leads",
        "data_quality_issues",
        "missing_contact_count",
        "missing_salesperson_count",
        "missing_stage_count",
    ):
        assert key in kpis_payload, (
            f"pre-existing /kpis key {key!r} was removed. The alias block is additive by "
            f"design — see the comment above it in dashboard_api.py before changing this."
        )
