"""
Unit tests for StageResolver — async OdooClient is mocked.
"""

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.modules.crm.stage_resolver import _CACHE_TTL, StageResolver


@pytest.fixture
def mock_client() -> MagicMock:
    client = MagicMock()
    client.execute_kw = AsyncMock(
        return_value=[
            {"id": 28, "name": "New Lead"},
            {"id": 34, "name": "Qualified"},
            {"id": 26, "name": "Closed Won"},
        ]
    )
    return client


async def test_get_name_returns_correct_stage(mock_client: MagicMock) -> None:
    resolver = StageResolver(mock_client)
    assert await resolver.get_name(28) == "New Lead"
    assert await resolver.get_name(34) == "Qualified"
    assert await resolver.get_name(26) == "Closed Won"


async def test_get_name_fallback_for_unknown_id(mock_client: MagicMock) -> None:
    resolver = StageResolver(mock_client)
    assert await resolver.get_name(999) == "Stage 999"


async def test_get_name_returns_no_stage_for_none(mock_client: MagicMock) -> None:
    resolver = StageResolver(mock_client)
    assert await resolver.get_name(None) == "No Stage"


async def test_stage_data_is_cached(mock_client: MagicMock) -> None:
    resolver = StageResolver(mock_client)
    await resolver.get_name(28)
    await resolver.get_name(34)
    # execute_kw should only have been called once (first access triggers refresh)
    assert mock_client.execute_kw.call_count == 1


async def test_cache_refreshes_after_ttl(mock_client: MagicMock) -> None:
    resolver = StageResolver(mock_client)
    await resolver.get_name(28)

    resolver._loaded_at = time.monotonic() - (_CACHE_TTL + 1)
    await resolver.get_name(28)

    assert mock_client.execute_kw.call_count == 2


async def test_get_all_returns_full_map(mock_client: MagicMock) -> None:
    resolver = StageResolver(mock_client)
    all_stages = await resolver.get_all()
    assert all_stages == {28: "New Lead", 34: "Qualified", 26: "Closed Won"}


def test_cache_info_does_not_refresh(mock_client: MagicMock) -> None:
    resolver = StageResolver(mock_client)
    info = resolver.cache_info()
    assert info["cached_stages"] == 0
    assert info["is_stale"] is True
    mock_client.execute_kw.assert_not_called()
