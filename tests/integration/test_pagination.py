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

_AUTH = ("testadmin", "testpass")


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


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_default_pagination(client: TestClient) -> None:
    r = client.get("/api/v1/data-quality/missing-contact", auth=_AUTH)
    assert r.status_code == 200
    body = r.json()
    p = body["pagination"]
    assert p["page"] == 1
    assert p["page_size"] == 50


def test_page_2_has_prev(client: TestClient) -> None:
    r = client.get("/api/v1/data-quality/missing-contact?page=2&page_size=50", auth=_AUTH)
    assert r.status_code == 200
    p = r.json()["pagination"]
    assert p["has_prev"] is True
    assert p["page"] == 2


def test_last_page_has_no_next(client: TestClient) -> None:
    # 234 total, page_size=50 → 5 pages, last page = 5
    r = client.get("/api/v1/data-quality/missing-contact?page=5&page_size=50", auth=_AUTH)
    assert r.status_code == 200
    p = r.json()["pagination"]
    assert p["has_next"] is False


def test_page_size_max_200(client: TestClient) -> None:
    r = client.get("/api/v1/data-quality/missing-contact?page_size=200", auth=_AUTH)
    assert r.status_code == 200


def test_page_size_over_200_rejected(client: TestClient) -> None:
    r = client.get("/api/v1/data-quality/missing-contact?page_size=201", auth=_AUTH)
    assert r.status_code == 422
