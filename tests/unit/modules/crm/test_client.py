"""
Unit tests for OdooClient — focuses on read-only enforcement.
HTTP calls are not made; the guard fires before any network activity.
"""

from unittest.mock import MagicMock

import pytest

from backend.core.exceptions import ReadOnlyViolationError
from backend.modules.crm.client import ALLOWED_METHODS, OdooClient, _ensure_read_only

# ── _ensure_read_only ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("method", ["create", "write", "unlink", "copy", "sql_execute"])
def test_ensure_read_only_raises_for_write_methods(method: str) -> None:
    with pytest.raises(ReadOnlyViolationError):
        _ensure_read_only(method)


@pytest.mark.parametrize("method", list(ALLOWED_METHODS))
def test_ensure_read_only_passes_for_allowed_methods(method: str) -> None:
    _ensure_read_only(method)  # must not raise


# ── OdooClient.execute_kw ────────────────────────────────────────────────────


def _make_client() -> OdooClient:
    """Return an OdooClient with a mocked httpx.Client."""
    client = OdooClient.__new__(OdooClient)
    client._url = "http://mock/jsonrpc"
    client._db = "testdb"
    client._username = "u"
    client._api_key = "k"
    client._uid = 42  # pre-authenticated
    client._http = MagicMock()
    return client


@pytest.mark.parametrize("write_method", ["create", "write", "unlink"])
def test_execute_kw_blocks_write_operations(write_method: str) -> None:
    client = _make_client()
    with pytest.raises(ReadOnlyViolationError):
        client.execute_kw("crm.lead", write_method, [[{"name": "test"}]])


def test_execute_kw_allows_search_read() -> None:
    client = _make_client()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "jsonrpc": "2.0",
        "id": "1",
        "result": [{"id": 1, "name": "Test Lead"}],
    }
    client._http.post.return_value = mock_response

    result = client.execute_kw("crm.lead", "search_read", [[]], {"fields": ["id", "name"]})
    assert isinstance(result, list)
    assert result[0]["id"] == 1


def test_execute_kw_allows_read_group() -> None:
    client = _make_client()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "jsonrpc": "2.0",
        "id": "1",
        "result": [{"__count": 5}],
    }
    client._http.post.return_value = mock_response

    result = client.execute_kw("crm.lead", "read_group", [[], ["__count"], []])
    assert result == [{"__count": 5}]


# ── Context manager ───────────────────────────────────────────────────────────


def test_context_manager_calls_close() -> None:
    client = _make_client()
    client._http = MagicMock()

    with client:
        pass

    client._http.close.assert_called_once()
