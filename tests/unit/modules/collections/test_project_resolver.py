"""
Unit tests for the dynamic project-name resolver — get_project_name_map.

Stage 1 of the dynamic-project-resolution refactor. The resolver sources live
project display names from rs.structure.project.code (active=True, id-asc) and
caches them for 1 hour. It is DORMANT (not yet wired into any KPI/drill-down);
these tests exercise it in isolation.

OdooClient is fully mocked; no live Odoo connection is made (consistent with the
rest of the Collections unit suite). Live verification is out of scope here.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.core.exceptions import OdooQueryError, ReadOnlyViolationError
from backend.modules.collections.services import cache as _cache
from backend.modules.collections.services.kpi_service import (
    _CACHE_KEY_PREFIX_PROJECT_MASTER,
    _CACHE_TTL_PROJECT_MASTER,
    _PROJECT_MASTER_MODEL,
    get_project_name_map,
)

# Mirrors the live read-only probe (2026-06-30): exactly 3 active projects,
# clean `code` values with no "Project#" prefix.
_MOCK_ROWS = [
    {"id": 1, "code": "New Capital"},
    {"id": 2, "code": "Cassette"},
    {"id": 3, "code": "La puerta"},
]


@pytest.fixture(autouse=True)
def fresh_cache() -> None:
    _cache.clear()
    yield
    _cache.clear()


@pytest.fixture
def mock_client() -> MagicMock:
    client = MagicMock()
    client.execute_kw = AsyncMock(return_value=_MOCK_ROWS)
    return client


# ── Test 1 — Happy path: id→name map in id order ─────────────────────────────


async def test_happy_path_returns_id_name_map(mock_client: MagicMock) -> None:
    result = await get_project_name_map(client=mock_client)

    assert result == {1: "New Capital", 2: "Cassette", 3: "La puerta"}
    # Dict iteration order must be id-ascending.
    assert list(result.keys()) == [1, 2, 3]
    # Keys are ints, names are clean (no "Project#" prefix).
    assert all(isinstance(k, int) for k in result)
    assert all(not v.startswith("Project#") for v in result.values())


# ── Test 2 — Correct read-only Odoo call (model / method / domain / fields) ──


async def test_issues_correct_search_read_call(mock_client: MagicMock) -> None:
    await get_project_name_map(client=mock_client)

    call = mock_client.execute_kw.call_args
    assert call.args[0] == _PROJECT_MASTER_MODEL
    assert call.args[0] == "rs.structure.project"
    assert call.args[1] == "search_read", (
        f"Resolver must use search_read (read-only), got {call.args[1]!r}"
    )

    domain = call.kwargs["args"][0]
    fields = call.kwargs["args"][1]
    assert domain == [("active", "=", True)], (
        f"Domain must be the active-only guard verbatim, got {domain!r}"
    )
    assert fields == ["id", "code"], (
        f"Fields must be exactly ['id', 'code'] verbatim, got {fields!r}"
    )


# ── Test 3 — order='id asc' kwarg is sent to Odoo ────────────────────────────


async def test_sends_order_id_asc_kwarg(mock_client: MagicMock) -> None:
    await get_project_name_map(client=mock_client)

    call = mock_client.execute_kw.call_args
    assert call.kwargs["kwargs"]["order"] == "id asc", (
        f"Resolver must request order='id asc', got {call.kwargs.get('kwargs')!r}"
    )


# ── Test 4 — Cache hit: second call does NOT touch Odoo ──────────────────────


async def test_second_call_is_served_from_cache(mock_client: MagicMock) -> None:
    first = await get_project_name_map(client=mock_client)
    second = await get_project_name_map(client=mock_client)

    assert mock_client.execute_kw.call_count == 1, (
        "Second call must be served from cache — Odoo hit exactly once."
    )
    assert second == first == {1: "New Capital", 2: "Cassette", 3: "La puerta"}


# ── Test 5 — Cached with the longer 1-hour TTL (distinct from 60s default) ───


async def test_caches_with_one_hour_ttl(mock_client: MagicMock) -> None:
    with patch.object(_cache, "set", wraps=_cache.set) as spy_set:
        await get_project_name_map(client=mock_client)

    spy_set.assert_called_once()
    ttl = spy_set.call_args.kwargs["ttl"]
    assert ttl == _CACHE_TTL_PROJECT_MASTER
    assert ttl == 3600, f"Master list must be cached for 1 hour, got ttl={ttl}"
    assert ttl > _cache._TTL_SECONDS, (
        f"Resolver TTL ({ttl}) must exceed the 60s KPI default "
        f"({_cache._TTL_SECONDS})."
    )


# ── Test 6 — Blank/falsy code falls back to a safe placeholder ───────────────


async def test_blank_code_falls_back_to_placeholder(mock_client: MagicMock) -> None:
    mock_client.execute_kw = AsyncMock(return_value=[
        {"id": 1, "code": "New Capital"},
        {"id": 4, "code": False},   # falsy code — Odoo returns False for empty
        {"id": 5, "code": None},
        {"id": 6, "code": ""},
    ])

    result = await get_project_name_map(client=mock_client)

    assert result == {
        1: "New Capital",
        4: "Project 4",
        5: "Project 5",
        6: "Project 6",
    }
    # Never emit a blank string.
    assert all(name for name in result.values())


# ── Test 7 — Empty result set returns {} without raising ─────────────────────


async def test_empty_result_returns_empty_dict(mock_client: MagicMock) -> None:
    mock_client.execute_kw = AsyncMock(return_value=[])

    result = await get_project_name_map(client=mock_client)

    assert result == {}


# ── Test 8 — Read-only guard: contaminated ALLOWED_METHODS raises pre-RPC ────


async def test_contaminated_allowed_methods_raises_before_any_rpc(
    monkeypatch: pytest.MonkeyPatch,
    mock_client: MagicMock,
) -> None:
    monkeypatch.setattr(
        "backend.modules.collections.services.kpi_service.ALLOWED_METHODS",
        frozenset({"search_read", "write"}),  # contaminated
    )

    with pytest.raises(ReadOnlyViolationError):
        await get_project_name_map(client=mock_client)

    mock_client.execute_kw.assert_not_called()


async def test_clean_allowed_methods_does_not_raise(mock_client: MagicMock) -> None:
    # Baseline: the production ALLOWED_METHODS must never trigger the assertion.
    result = await get_project_name_map(client=mock_client)
    assert result == {1: "New Capital", 2: "Cassette", 3: "La puerta"}


# ── Test 9 — Ordering: out-of-order Odoo rows still yield id-ascending dict ──


async def test_orders_ascending_regardless_of_odoo_order(mock_client: MagicMock) -> None:
    mock_client.execute_kw = AsyncMock(return_value=[
        {"id": 3, "code": "La puerta"},
        {"id": 1, "code": "New Capital"},
        {"id": 2, "code": "Cassette"},
    ])

    result = await get_project_name_map(client=mock_client)

    assert list(result.keys()) == [1, 2, 3], (
        f"Dict must be id-ascending regardless of Odoo row order, got {list(result.keys())}"
    )
    assert result == {1: "New Capital", 2: "Cassette", 3: "La puerta"}


# ── Test 10 — RPC failure surfaces as OdooQueryError (suite convention) ──────


async def test_rpc_failure_raises_odoo_query_error(mock_client: MagicMock) -> None:
    mock_client.execute_kw.side_effect = RuntimeError("connection refused")

    with pytest.raises(OdooQueryError):
        await get_project_name_map(client=mock_client)


async def test_rpc_failure_writes_no_cache_entry(mock_client: MagicMock) -> None:
    mock_client.execute_kw.side_effect = RuntimeError("timeout")

    with pytest.raises(OdooQueryError):
        await get_project_name_map(client=mock_client)

    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_PROJECT_MASTER)
    assert _cache.get(cache_key) is None, "A failed RPC must not leave a cache entry."
