"""Integration tests for missing-contact pagination."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_crm_service
from backend.main import app
from backend.modules.crm.schemas import (
    PaginatedMissingContactResponse,
    Pagination,
)


def _paginated(page: int, page_size: int, total: int) -> PaginatedMissingContactResponse:
    total_pages = max(1, -(-total // page_size))  # ceiling division
    return PaginatedMissingContactResponse(
        ok=True,
        data=[],
        pagination=Pagination(
            page=page,
            page_size=page_size,
            total=total,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_prev=page > 1,
        ),
    )


@pytest.fixture(autouse=True)
def override_service() -> None:
    mock_svc = MagicMock()
    mock_svc.missing_contact_response = AsyncMock(
        side_effect=lambda page=1, page_size=50, **kw: _paginated(page, page_size, 234)
    )
    app.dependency_overrides[get_crm_service] = lambda: mock_svc
    yield
    app.dependency_overrides.clear()


def test_default_pagination(authed_client: TestClient) -> None:
    r = authed_client.get("/api/v1/data-quality/missing-contact")
    assert r.status_code == 200
    body = r.json()
    p = body["pagination"]
    assert p["page"] == 1
    assert p["page_size"] == 50


def test_page_2_has_prev(authed_client: TestClient) -> None:
    r = authed_client.get("/api/v1/data-quality/missing-contact?page=2&page_size=50")
    assert r.status_code == 200
    p = r.json()["pagination"]
    assert p["has_prev"] is True
    assert p["page"] == 2


def test_last_page_has_no_next(authed_client: TestClient) -> None:
    # 234 total, page_size=50 → 5 pages, last page = 5
    r = authed_client.get("/api/v1/data-quality/missing-contact?page=5&page_size=50")
    assert r.status_code == 200
    p = r.json()["pagination"]
    assert p["has_next"] is False


def test_page_size_max_200(authed_client: TestClient) -> None:
    r = authed_client.get("/api/v1/data-quality/missing-contact?page_size=200")
    assert r.status_code == 200


def test_page_size_over_200_rejected(authed_client: TestClient) -> None:
    r = authed_client.get("/api/v1/data-quality/missing-contact?page_size=201")
    assert r.status_code == 422
