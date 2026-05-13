"""
Unit tests for OdooClient — read-only enforcement + async HTTP calls.
"""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from backend.core.exceptions import (
    OdooAuthenticationError,
    OdooConnectionError,
    OdooQueryError,
    ReadOnlyViolationError,
)
from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient, _ensure_read_only

# ── _ensure_read_only ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("method", ["create", "write", "unlink", "copy", "sql_execute"])
def test_ensure_read_only_raises_for_write_methods(method: str) -> None:
    with pytest.raises(ReadOnlyViolationError):
        _ensure_read_only(method)


@pytest.mark.parametrize("method", list(ALLOWED_METHODS))
def test_ensure_read_only_passes_for_allowed_methods(method: str) -> None:
    _ensure_read_only(method)  # must not raise


# ── OdooClient helpers ────────────────────────────────────────────────────────


def _make_client() -> OdooClient:
    """Return an OdooClient with a mocked httpx.AsyncClient."""
    client = OdooClient.__new__(OdooClient)
    client._url = "http://mock/jsonrpc"
    client._db = "testdb"
    client._username = "u"
    client._api_key = "k"
    client._uid = 42  # pre-authenticated
    client._http = MagicMock()
    return client


def _mock_http_response(result: object) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"jsonrpc": "2.0", "id": "1", "result": result}
    return resp


# ── Read-only enforcement (sync — fires before any network call) ──────────────


@pytest.mark.parametrize("write_method", ["create", "write", "unlink"])
async def test_execute_kw_blocks_write_operations(write_method: str) -> None:
    client = _make_client()
    with pytest.raises(ReadOnlyViolationError):
        await client.execute_kw("crm.lead", write_method, [[{"name": "test"}]])


# ── Async HTTP calls ──────────────────────────────────────────────────────────


async def test_execute_kw_allows_search_read() -> None:
    client = _make_client()
    mock_response = _mock_http_response([{"id": 1, "name": "Test Lead"}])
    client._http.post = AsyncMock(return_value=mock_response)

    result = await client.execute_kw("crm.lead", "search_read", [[]], {"fields": ["id", "name"]})
    assert isinstance(result, list)
    assert result[0]["id"] == 1


async def test_execute_kw_allows_read_group() -> None:
    client = _make_client()
    mock_response = _mock_http_response([{"__count": 5}])
    client._http.post = AsyncMock(return_value=mock_response)

    result = await client.execute_kw("crm.lead", "read_group", [[], ["__count"], []])
    assert result == [{"__count": 5}]


async def test_authenticate_returns_uid() -> None:
    client = _make_client()
    client._uid = None  # reset so authenticate actually runs
    mock_response = _mock_http_response(99)
    client._http.post = AsyncMock(return_value=mock_response)

    uid = await client.authenticate()
    assert uid == 99
    assert client._uid == 99


async def test_authenticate_caches_uid() -> None:
    """Second call must NOT hit the network."""
    client = _make_client()  # _uid is already 42
    client._http.post = AsyncMock()

    uid = await client.authenticate()
    assert uid == 42
    client._http.post.assert_not_called()


async def test_authenticate_raises_on_false_uid() -> None:
    client = _make_client()
    client._uid = None
    mock_response = _mock_http_response(False)
    client._http.post = AsyncMock(return_value=mock_response)

    with pytest.raises(OdooAuthenticationError):
        await client.authenticate()


async def test_call_raises_odoo_query_error_on_rpc_error() -> None:
    client = _make_client()
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "jsonrpc": "2.0",
        "id": "1",
        "error": {"message": "Model not found"},
    }
    client._http.post = AsyncMock(return_value=resp)

    with pytest.raises(OdooQueryError):
        await client._call("object", "execute_kw", [])


async def test_call_raises_odoo_auth_error_on_access_denied() -> None:
    client = _make_client()
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "jsonrpc": "2.0",
        "id": "1",
        "error": {"message": "Access denied"},
    }
    client._http.post = AsyncMock(return_value=resp)

    with pytest.raises(OdooAuthenticationError):
        await client._call("object", "execute_kw", [])


async def test_call_raises_connection_error_on_http_status_error() -> None:
    client = _make_client()
    resp = MagicMock()
    resp.status_code = 500
    mock_exc = httpx.HTTPStatusError("500", request=MagicMock(), response=resp)
    resp.raise_for_status.side_effect = mock_exc
    client._http.post = AsyncMock(return_value=resp)

    with pytest.raises(OdooConnectionError):
        await client._call("object", "execute_kw", [])


# ── Async context manager ─────────────────────────────────────────────────────


async def test_async_context_manager_calls_close() -> None:
    client = _make_client()
    client._http.aclose = AsyncMock()

    async with client:
        pass

    client._http.aclose.assert_called_once()
