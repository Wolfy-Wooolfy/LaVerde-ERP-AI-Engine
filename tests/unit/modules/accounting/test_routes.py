"""
Endpoint tests for GET /api/v1/accounting/balance-sheet (Module 4 · Phase 1).

FastAPI TestClient with get_balance_sheet patched — no Odoo connection.
Mirrors the HR router-test pattern (dependency override + mocked user_repo).
Amounts in _MOCK_DATA are illustrative fixtures, NOT live baselines — the
live figures shift as finance edits the opening balance (M4.3).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_current_user
from backend.auth.models import UserRecord
from backend.core.exceptions import OdooQueryError
from backend.main import app
from backend.modules.accounting.services.balance_sheet_service import (
    BalanceSheetIntegrityError,
)

_TESTADMIN_RECORD = UserRecord(
    username="testadmin", password_hash="", modules=["*"],
    is_admin=True, is_active=True,
    created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
)

# A non-admin user WITHOUT the "accounting" module — exercises the gate.
_NO_ACCOUNTING_RECORD = UserRecord(
    username="collections_only", password_hash="", modules=["collections"],
    is_admin=False, is_active=True,
    created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
)

_URL = "/api/v1/accounting/balance-sheet"
_PATCH_TARGET = "backend.api.v1.endpoints.accounting.get_balance_sheet"

_MOCK_DATA = {
    "generated_at": "2026-07-05T14:03:22+03:00",
    "currency": "EGP",
    "banner_ar": "أرصدة افتتاحية — بيانات تحت الإدخال",
    "totals": {
        "assets": 1000.0,
        "liabilities": 300.0,
        "equity": 630.0,
        "unallocated_result": 70.0,
        "liabilities_plus_equity_plus_result": 1000.0,
        "difference": 0.0,
        "balanced": True,
    },
    "excluded_off_balance": {"count": 0, "total": 0.0},
    "sections": [
        {
            "group": "asset",
            "label_ar": "الأصول",
            "total": 1000.0,
            "subgroups": [
                {
                    "account_type": "asset_current",
                    "label_ar": "أصول متداولة",
                    "total": 1000.0,
                    "accounts": [
                        {"code": "20002000", "name": "حساب اختبار", "balance": 1000.0},
                    ],
                },
            ],
        },
        {"group": "liability", "label_ar": "الخصوم", "total": 300.0, "subgroups": []},
        {"group": "equity", "label_ar": "حقوق الملكية", "total": 630.0, "subgroups": []},
    ],
    "rpc_duration_ms": 250,
}


def _client_for(record: UserRecord) -> TestClient:
    app.dependency_overrides[get_current_user] = lambda: record.username
    mock_repo = MagicMock()
    mock_repo.get_user.return_value = record
    app.state.user_repo = mock_repo
    return TestClient(app, raise_server_exceptions=True)


def _teardown() -> None:
    app.dependency_overrides.pop(get_current_user, None)
    if hasattr(app.state, "user_repo"):
        del app.state.user_repo


@pytest.fixture
def client() -> TestClient:
    c = _client_for(_TESTADMIN_RECORD)
    yield c
    _teardown()


@pytest.fixture
def client_without_accounting() -> TestClient:
    c = _client_for(_NO_ACCOUNTING_RECORD)
    yield c
    _teardown()


# ── Test 1 — 200 + contract keys ──────────────────────────────────────────────


def test_returns_200_and_contract_keys(client: TestClient) -> None:
    with patch(_PATCH_TARGET, new=AsyncMock(return_value=_MOCK_DATA)):
        r = client.get(_URL)

    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {
        "generated_at", "currency", "banner_ar", "totals",
        "excluded_off_balance", "sections", "rpc_duration_ms",
    }
    assert "cache_status" not in body  # uncached route (M4.3)
    assert body["currency"] == "EGP"
    assert body["totals"]["balanced"] is True
    assert body["totals"]["unallocated_result"] == 70.0


# ── Test 2 — no-store, no X-Cache-Status ──────────────────────────────────────


def test_cache_control_no_store_and_no_cache_status_header(client: TestClient) -> None:
    with patch(_PATCH_TARGET, new=AsyncMock(return_value=_MOCK_DATA)):
        r = client.get(_URL)

    assert r.status_code == 200
    assert r.headers.get("cache-control") == "no-store"
    assert "x-cache-status" not in r.headers


# ── Test 3 — nested sections serialized intact through response_model ─────────


def test_nested_sections_serialized_intact(client: TestClient) -> None:
    with patch(_PATCH_TARGET, new=AsyncMock(return_value=_MOCK_DATA)):
        r = client.get(_URL)

    sections = r.json()["sections"]
    assert [s["group"] for s in sections] == ["asset", "liability", "equity"]
    subgroup = sections[0]["subgroups"][0]
    assert subgroup["account_type"] == "asset_current"
    assert subgroup["label_ar"] == "أصول متداولة"
    account = subgroup["accounts"][0]
    assert set(account.keys()) == {"code", "name", "balance"}
    assert account["code"] == "20002000"


# ── Test 4 — OdooQueryError → 503 ─────────────────────────────────────────────


def test_odoo_query_error_returns_503(client: TestClient) -> None:
    with patch(_PATCH_TARGET, new=AsyncMock(side_effect=OdooQueryError("boom"))):
        r = client.get(_URL)

    assert r.status_code == 503
    assert r.json()["error"]["code"] == "odoo_unavailable"


# ── Test 5 — BalanceSheetIntegrityError → 500 ─────────────────────────────────


def test_integrity_error_returns_500(client: TestClient) -> None:
    with patch(
        _PATCH_TARGET,
        new=AsyncMock(side_effect=BalanceSheetIntegrityError("unmapped: asset_weird")),
    ):
        r = client.get(_URL)

    assert r.status_code == 500
    assert r.json()["error"]["code"] == "internal_error"


# ── Test 6 — unexpected exception → 500 ───────────────────────────────────────


def test_unexpected_exception_returns_500(client: TestClient) -> None:
    with patch(_PATCH_TARGET, new=AsyncMock(side_effect=RuntimeError("unexpected"))):
        r = client.get(_URL)

    assert r.status_code == 500
    assert r.json()["error"]["code"] == "internal_error"


# ── Test 7 — 401 unauthenticated ──────────────────────────────────────────────


def test_401_when_no_auth() -> None:
    """No session → 401 before the handler body runs (no service patch needed)."""
    c = TestClient(app, raise_server_exceptions=True)
    r = c.get(_URL)  # no session
    assert r.status_code == 401, (
        f"Expected 401 for unauthenticated accounting request, got {r.status_code}"
    )


# ── Test 8 — 403 when module not granted ──────────────────────────────────────


def test_403_when_accounting_module_not_granted(client_without_accounting: TestClient) -> None:
    """Authenticated user WITHOUT 'accounting' (and without '*') → 403 from the
    include-time require_module_api gate (rendered as the project's JSON
    envelope by the app-level 403 handler); the service is never called."""
    with patch(_PATCH_TARGET, new=AsyncMock(return_value=_MOCK_DATA)) as mock_service:
        r = client_without_accounting.get(_URL)

    assert r.status_code == 403
    assert r.json()["error"]["code"] == "MODULE_ACCESS_DENIED"
    mock_service.assert_not_awaited()


# ── Test 9 — settings grant validation knows the module ───────────────────────


def test_accounting_registered_in_valid_modules() -> None:
    """Admins must be able to grant 'accounting' via the Settings API."""
    from backend.api.v1.endpoints.settings import _VALID_MODULES

    assert "accounting" in _VALID_MODULES
