"""
Smoke tests for HTML pages — assert that every main route returns 200
and contains expected landmarks. These catch template errors and missing
context variables before a browser even opens.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_crm_service
from backend.main import app
from backend.modules.crm.schemas import (
    ActivitySummary,
    DataQuality,
    FollowupRisk,
    SummaryResponse,
)


def _mock_summary() -> SummaryResponse:
    return SummaryResponse(
        mode="read_only",
        scope="resolved_opportunities_only",
        summary=ActivitySummary(
            total_leads=42,
            followups_today=5,
            overdue_followups=8,
            planned_followups=12,
            no_activity_leads=17,
            critical_overdue=3,
            data_quality_issues=6,
        ),
        data_quality=DataQuality(
            new_x_count=1,
            missing_stage_count=2,
            missing_contact_count=3,
            missing_salesperson_count=2,
            total_data_quality_issues=6,
        ),
        followup_risk=FollowupRisk(
            overdue_by_salesperson=[],
            overdue_by_team=[],
            overdue_by_stage=[],
            overdue_matrix_by_team_salesperson_stage=[],
        ),
    )


@pytest.fixture(autouse=True)
def override_crm_service() -> None:
    mock_svc = MagicMock()
    mock_svc.summary = AsyncMock(return_value=_mock_summary())
    mock_svc.missing_contact_details = AsyncMock(return_value=([], 0))
    app.dependency_overrides[get_crm_service] = lambda: mock_svc
    yield
    app.dependency_overrides.clear()


# ── Dashboard page ─────────────────────────────────────────────────────────────


def test_dashboard_loads(authed_client: TestClient) -> None:
    r = authed_client.get("/dashboard")
    assert r.status_code == 200
    assert b"LaVerde ERP AI Engine" in r.content


def test_dashboard_has_kpi_section(authed_client: TestClient) -> None:
    r = authed_client.get("/dashboard")
    assert r.status_code == 200
    assert b"kpi-card" in r.content


def test_dashboard_has_chart_canvases(authed_client: TestClient) -> None:
    r = authed_client.get("/dashboard")
    assert r.status_code == 200
    assert b"activityChart" in r.content
    assert b"salespersonChart" in r.content
    assert b"stageChart" in r.content


def test_dashboard_greeting_uses_username(authed_client: TestClient) -> None:
    r = authed_client.get("/dashboard")
    assert r.status_code == 200
    # The greeting span should carry translated data attributes
    assert b"data-morning=" in r.content


def test_dashboard_no_cdn_references(authed_client: TestClient) -> None:
    r = authed_client.get("/dashboard")
    body = r.text
    # No CDN URLs in the rendered HTML
    assert "cdn.jsdelivr.net" not in body
    assert "cdn.datatables.net" not in body
    assert "fonts.googleapis.com" not in body


def test_dashboard_vendor_scripts_referenced(authed_client: TestClient) -> None:
    r = authed_client.get("/dashboard")
    body = r.text
    assert "/static/vendor/alpine.min.js" in body
    assert "/static/vendor/chart.umd.min.js" in body
    assert "/static/vendor/jquery.min.js" in body


# ── Missing contacts page ──────────────────────────────────────────────────────


def test_missing_contact_loads(authed_client: TestClient) -> None:
    r = authed_client.get("/data-quality/missing-contact")
    assert r.status_code == 200


def test_missing_contact_has_title(authed_client: TestClient) -> None:
    r = authed_client.get("/data-quality/missing-contact")
    assert r.status_code == 200
    assert b"Missing" in r.content


def test_missing_contact_empty_state(authed_client: TestClient) -> None:
    # With 0 rows, should show the empty state, not an error
    r = authed_client.get("/data-quality/missing-contact")
    assert r.status_code == 200
    assert b"No records found" in r.content


def test_missing_contact_no_cdn_references(authed_client: TestClient) -> None:
    r = authed_client.get("/data-quality/missing-contact")
    body = r.text
    assert "cdn.jsdelivr.net" not in body
    assert "cdn.datatables.net" not in body
    assert "fonts.googleapis.com" not in body


def test_missing_contact_pagination_params(authed_client: TestClient) -> None:
    r = authed_client.get("/data-quality/missing-contact?page=2&page_size=25")
    assert r.status_code == 200


# ── CSP header ────────────────────────────────────────────────────────────────


def test_csp_header_present(authed_client: TestClient) -> None:
    r = authed_client.get("/dashboard")
    csp = r.headers.get("content-security-policy", "")
    assert "default-src" in csp


def test_csp_no_external_domains(authed_client: TestClient) -> None:
    r = authed_client.get("/dashboard")
    csp = r.headers.get("content-security-policy", "")
    # The CSP must NOT allow external CDN domains
    assert "cdn.jsdelivr.net" not in csp
    assert "fonts.googleapis.com" not in csp


# ── Bug 2: DISPLAY_NAME greeting fallback chain ───────────────────────────────


def test_extract_first_name_display_name_takes_precedence(monkeypatch):
    """DISPLAY_NAME must override username-derived name everywhere."""
    from backend.api.v1.endpoints.dashboard import _extract_first_name
    from backend.core.config import settings

    monkeypatch.setattr(settings, "DISPLAY_NAME", "La Verde")
    assert _extract_first_name("khaled@laverde-eg.com") == "La Verde"
    assert _extract_first_name("admin") == "La Verde"


def test_extract_first_name_email_fallback(monkeypatch):
    from backend.api.v1.endpoints.dashboard import _extract_first_name
    from backend.core.config import settings

    monkeypatch.setattr(settings, "DISPLAY_NAME", "")
    assert _extract_first_name("khaled.elmasry@laverde-eg.com") == "Khaled"


def test_extract_first_name_plain_username_fallback(monkeypatch):
    from backend.api.v1.endpoints.dashboard import _extract_first_name
    from backend.core.config import settings

    monkeypatch.setattr(settings, "DISPLAY_NAME", "")
    assert _extract_first_name("admin") == "Admin"


def test_dashboard_renders_display_name(authed_client: TestClient, monkeypatch) -> None:
    """When DISPLAY_NAME is set, the rendered dashboard must include it."""
    from backend.core.config import settings

    monkeypatch.setattr(settings, "DISPLAY_NAME", "La Verde")
    r = authed_client.get("/dashboard")
    assert r.status_code == 200
    assert b"La Verde" in r.content
