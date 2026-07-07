"""
Integration tests for the v1 API endpoints.
Uses FastAPI's TestClient with dependency overrides — no real Odoo connection.
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
    FollowupRiskResponse,
    PaginatedMissingContactResponse,
    Pagination,
    SummaryResponse,
)

# ── Mock service factory ──────────────────────────────────────────────────────


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


def _build_mock_paginated() -> PaginatedMissingContactResponse:
    return PaginatedMissingContactResponse(
        ok=True,
        data=[],
        pagination=Pagination(
            page=1, page_size=50, total=0, total_pages=0, has_next=False, has_prev=False
        ),
    )


@pytest.fixture(autouse=True)
def override_crm_service() -> None:
    mock_svc = MagicMock()
    mock_svc.summary = AsyncMock(return_value=_build_mock_summary())
    mock_svc.followup_risk_response = AsyncMock(
        return_value=FollowupRiskResponse(
            mode="read_only",
            scope="resolved_opportunities_only",
            followup_risk=FollowupRisk(
                overdue_by_salesperson=[],
                overdue_by_team=[],
                overdue_by_stage=[],
                overdue_matrix_by_team_salesperson_stage=[],
            ),
        )
    )
    mock_svc.missing_contact_response = AsyncMock(return_value=_build_mock_paginated())
    mock_svc.missing_stage_response = AsyncMock(return_value=_build_mock_paginated())
    mock_svc.missing_salesperson_response = AsyncMock(return_value=_build_mock_paginated())
    mock_svc.missing_linked_contact_response = AsyncMock(return_value=_build_mock_paginated())
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
    body = r.json()
    assert body["status"] == "ok"
    assert "uptime_seconds" in body


# ── /api/v1/health ────────────────────────────────────────────────────────────


def test_v1_health_requires_auth(client: TestClient) -> None:
    r = client.get("/api/v1/health")
    assert r.status_code == 401


def test_v1_health_with_auth(authed_client: TestClient) -> None:
    r = authed_client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "uptime_seconds" in body
    assert "components" in body


# ── /api/v1/summary ───────────────────────────────────────────────────────────


def test_summary_requires_auth(client: TestClient) -> None:
    r = client.get("/api/v1/summary")
    assert r.status_code == 401


def test_summary_with_auth(authed_client: TestClient) -> None:
    r = authed_client.get("/api/v1/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "read_only"
    assert body["summary"]["total_leads"] == 50


def test_summary_unauthenticated(client: TestClient) -> None:
    """Basic auth is no longer accepted; only session cookies work."""
    r = client.get("/api/v1/summary")
    assert r.status_code == 401


# ── /api/v1/followup-risk ─────────────────────────────────────────────────────


def test_followup_risk_with_auth(authed_client: TestClient) -> None:
    r = authed_client.get("/api/v1/followup-risk")
    assert r.status_code == 200
    assert "followup_risk" in r.json()


# ── /api/v1/data-quality/missing-contact (paginated) ─────────────────────────


def test_missing_contact_with_auth(authed_client: TestClient) -> None:
    r = authed_client.get("/api/v1/data-quality/missing-contact")
    assert r.status_code == 200
    body = r.json()
    assert "data" in body
    assert "pagination" in body


def test_missing_contact_pagination_params(authed_client: TestClient) -> None:
    r = authed_client.get("/api/v1/data-quality/missing-contact?page=2&page_size=25")
    assert r.status_code == 200


def test_missing_contact_invalid_page_size(authed_client: TestClient) -> None:
    r = authed_client.get("/api/v1/data-quality/missing-contact?page_size=999")
    assert r.status_code == 422  # Pydantic validation


# ── /api/v1/data-quality/missing-stage (paginated, N4) ───────────────────────


def test_missing_stage_requires_auth(client: TestClient) -> None:
    r = client.get("/api/v1/data-quality/missing-stage")
    assert r.status_code == 401


def test_missing_stage_with_auth(authed_client: TestClient) -> None:
    r = authed_client.get("/api/v1/data-quality/missing-stage")
    assert r.status_code == 200
    body = r.json()
    assert "data" in body
    assert "pagination" in body


def test_missing_stage_pagination_params(authed_client: TestClient) -> None:
    r = authed_client.get("/api/v1/data-quality/missing-stage?page=2&page_size=25")
    assert r.status_code == 200


def test_missing_stage_invalid_page_size(authed_client: TestClient) -> None:
    r = authed_client.get("/api/v1/data-quality/missing-stage?page_size=999")
    assert r.status_code == 422  # Pydantic validation


# ── /api/v1/data-quality/missing-salesperson (paginated, N4) ─────────────────


def test_missing_salesperson_requires_auth(client: TestClient) -> None:
    r = client.get("/api/v1/data-quality/missing-salesperson")
    assert r.status_code == 401


def test_missing_salesperson_with_auth(authed_client: TestClient) -> None:
    r = authed_client.get("/api/v1/data-quality/missing-salesperson")
    assert r.status_code == 200
    body = r.json()
    assert "data" in body
    assert "pagination" in body


def test_missing_salesperson_pagination_params(authed_client: TestClient) -> None:
    r = authed_client.get("/api/v1/data-quality/missing-salesperson?page=2&page_size=25")
    assert r.status_code == 200


def test_missing_salesperson_invalid_page_size(authed_client: TestClient) -> None:
    r = authed_client.get("/api/v1/data-quality/missing-salesperson?page_size=999")
    assert r.status_code == 422  # Pydantic validation


# ── /api/v1/data-quality/missing-linked-contact (paginated, hub Tab 1) ───────


def test_missing_linked_contact_requires_auth(client: TestClient) -> None:
    r = client.get("/api/v1/data-quality/missing-linked-contact")
    assert r.status_code == 401


def test_missing_linked_contact_with_auth(authed_client: TestClient) -> None:
    r = authed_client.get("/api/v1/data-quality/missing-linked-contact")
    assert r.status_code == 200
    body = r.json()
    assert "data" in body
    assert "pagination" in body


def test_missing_linked_contact_pagination_params(authed_client: TestClient) -> None:
    r = authed_client.get("/api/v1/data-quality/missing-linked-contact?page=2&page_size=25")
    assert r.status_code == 200


def test_missing_linked_contact_invalid_page_size(authed_client: TestClient) -> None:
    r = authed_client.get("/api/v1/data-quality/missing-linked-contact?page_size=999")
    assert r.status_code == 422  # Pydantic validation


# ── /api/v1/metrics ───────────────────────────────────────────────────────────


def test_metrics_endpoint_with_auth(authed_client: TestClient) -> None:
    r = authed_client.get("/api/v1/metrics")
    assert r.status_code == 200
    body = r.json()
    assert "odoo" in body
    assert "cache" in body
    assert "api" in body
    assert "uptime_seconds" in body


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
    r = client.get("/dashboard", follow_redirects=False)
    assert r.status_code == 302
    assert "/login" in r.headers.get("location", "")


def test_dashboard_html_with_auth(authed_client: TestClient) -> None:
    r = authed_client.get("/dashboard")
    assert r.status_code == 200
    assert "LaVerde ERP AI Engine" in r.text


# ── Security headers ──────────────────────────────────────────────────────────


def test_security_headers_present(client: TestClient) -> None:
    r = client.get("/health")
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "DENY"
    assert "referrer-policy" in r.headers
    assert "content-security-policy" in r.headers


def test_request_id_header_present(client: TestClient) -> None:
    r = client.get("/health")
    assert "x-request-id" in r.headers
    assert "x-response-time" in r.headers


# ── Error response format ─────────────────────────────────────────────────────


def test_error_response_structure_on_401(client: TestClient) -> None:
    r = client.get("/api/v1/summary")
    # 401 from session check (no session) — verify it's 401
    assert r.status_code == 401
