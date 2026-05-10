"""
Integration tests for the v1 API endpoints.
Uses FastAPI's TestClient with dependency overrides — no real Odoo connection.
"""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_crm_service
from backend.main import app
from backend.modules.crm.schemas import (
    ActivitySummary,
    DataQuality,
    DataQualityMissingContactResponse,
    FollowupRisk,
    FollowupRiskResponse,
    SummaryResponse,
)

_AUTH = ("testadmin", "testpass")

# ── Mock service fixture ──────────────────────────────────────────────────────


def _build_mock_summary() -> SummaryResponse:
    return SummaryResponse(
        mode="read_only",
        scope="resolved_opportunities_only",
        summary=ActivitySummary(
            total_leads=50,
            followups_today=3,
            overdue_followups=10,
            planned_followups=15,
            no_activity_leads=22,
            critical_overdue=5,
            data_quality_issues=4,
        ),
        data_quality=DataQuality(
            new_x_count=2,
            missing_stage_count=2,
            missing_contact_count=0,
            missing_salesperson_count=0,
            total_data_quality_issues=4,
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
    mock_svc.summary.return_value = _build_mock_summary()
    mock_svc.followup_risk_response.return_value = FollowupRiskResponse(
        mode="read_only",
        scope="resolved_opportunities_only",
        followup_risk=FollowupRisk(
            overdue_by_salesperson=[],
            overdue_by_team=[],
            overdue_by_stage=[],
            overdue_matrix_by_team_salesperson_stage=[],
        ),
    )
    mock_svc.missing_contact_response.return_value = DataQualityMissingContactResponse(
        mode="read_only",
        scope="resolved_opportunities_only",
        missing_contact_details=[],
    )
    app.dependency_overrides[get_crm_service] = lambda: mock_svc
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=True)


# ── /health (no auth) ─────────────────────────────────────────────────────────


def test_liveness_no_auth(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ── /api/v1/health ────────────────────────────────────────────────────────────


def test_v1_health_requires_auth(client: TestClient) -> None:
    r = client.get("/api/v1/health")
    assert r.status_code == 401


def test_v1_health_with_auth(client: TestClient) -> None:
    r = client.get("/api/v1/health", auth=_AUTH)
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ── /api/v1/summary ───────────────────────────────────────────────────────────


def test_summary_requires_auth(client: TestClient) -> None:
    r = client.get("/api/v1/summary")
    assert r.status_code == 401


def test_summary_with_auth(client: TestClient) -> None:
    r = client.get("/api/v1/summary", auth=_AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "read_only"
    assert body["summary"]["total_leads"] == 50


def test_summary_wrong_password(client: TestClient) -> None:
    r = client.get("/api/v1/summary", auth=("testadmin", "wrong"))
    assert r.status_code == 401


# ── /api/v1/followup-risk ─────────────────────────────────────────────────────


def test_followup_risk_with_auth(client: TestClient) -> None:
    r = client.get("/api/v1/followup-risk", auth=_AUTH)
    assert r.status_code == 200
    assert "followup_risk" in r.json()


# ── /api/v1/data-quality/missing-contact ─────────────────────────────────────


def test_missing_contact_with_auth(client: TestClient) -> None:
    r = client.get("/api/v1/data-quality/missing-contact", auth=_AUTH)
    assert r.status_code == 200
    assert "missing_contact_details" in r.json()


# ── Legacy redirects ──────────────────────────────────────────────────────────


def test_legacy_summary_redirects(client: TestClient) -> None:
    r = client.get("/crm/summary", follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == "/api/v1/summary"


def test_legacy_followup_redirects(client: TestClient) -> None:
    r = client.get("/crm/followup-risk", follow_redirects=False)
    assert r.status_code == 301


def test_legacy_missing_contact_redirects(client: TestClient) -> None:
    r = client.get("/crm/data-quality/missing-contact", follow_redirects=False)
    assert r.status_code == 301


# ── HTML pages ────────────────────────────────────────────────────────────────


def test_dashboard_html_requires_auth(client: TestClient) -> None:
    r = client.get("/dashboard")
    assert r.status_code == 401


def test_dashboard_html_with_auth(client: TestClient) -> None:
    r = client.get("/dashboard", auth=_AUTH)
    assert r.status_code == 200
    assert "CRM Sales Health Dashboard" in r.text


# ── Response headers ──────────────────────────────────────────────────────────


def test_request_id_header_present(client: TestClient) -> None:
    r = client.get("/health")
    assert "x-request-id" in r.headers
    assert "x-response-time" in r.headers
