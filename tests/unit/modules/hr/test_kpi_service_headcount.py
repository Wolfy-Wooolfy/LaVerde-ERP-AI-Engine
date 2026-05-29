"""
Unit tests for HR KPI service — get_headcount (KPI A).

OdooClient is fully mocked; no live Odoo connection is made.
Live verification: scripts/verify_kpi_a_headcount_live.py (D4).

Discovery baselines (canonical run 2026-05-28T13:43:49Z):
  total_active   == 136  (S3: employee.active_true)
  total_inactive == 24   (S3: employee.active_false)
  by_department: 24 groups (includes null-dept bucket, S3.3)
  by_job:        67 groups (includes null-job bucket, S3.4)
  null-dept employees (active+inactive): 4  (S3.2)
  null-job  employees (active+inactive): 3  (S3.2)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.core.exceptions import OdooQueryError, ReadOnlyViolationError
from backend.modules.hr.services import cache as _cache
from backend.modules.hr.services.kpi_service import (
    _CACHE_KEY_PREFIX_HEADCOUNT,
    _MODEL,
    _NO_DEPT_DISPLAY,
    _NO_JOB_DISPLAY,
    get_headcount,
)

# ── Shared mock data (happy path) ─────────────────────────────────────────────

_ACTIVE_COUNT   = 10
_INACTIVE_COUNT = 2

# dept rows — sum of __count == _ACTIVE_COUNT (10)
_DEPT_ROWS = [
    {"department_id": [10, "Sales"],   "__count": 5},
    {"department_id": [11, "Finance"], "__count": 3},
    {"department_id": False,            "__count": 2},   # null-dept bucket
]

# job rows — sum of __count == _ACTIVE_COUNT (10)
_JOB_ROWS = [
    {"job_id": [20, "Manager"], "__count": 7},
    {"job_id": False,            "__count": 3},           # null-job bucket
]


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def fresh_cache():
    _cache.clear()
    yield
    _cache.clear()


@pytest.fixture
def mock_client():
    """4-RPC mock: search_count(active), search_count(inactive),
    read_group(dept), read_group(job) — in that order."""
    client = MagicMock()
    client.execute_kw = AsyncMock(
        side_effect=[_ACTIVE_COUNT, _INACTIVE_COUNT, _DEPT_ROWS, _JOB_ROWS]
    )
    return client


def _make_client(active, inactive, dept_rows, job_rows):
    client = MagicMock()
    client.execute_kw = AsyncMock(
        side_effect=[active, inactive, dept_rows, job_rows]
    )
    return client


# ── Test 1 — Happy path: return shape ────────────────────────────────────────


async def test_happy_path_return_shape(mock_client):
    result = await get_headcount(client=mock_client)

    assert set(result.keys()) == {
        "total_active", "total_inactive", "by_department",
        "by_job", "as_of", "cache_status", "rpc_duration_ms",
    }
    assert result["total_active"] == _ACTIVE_COUNT
    assert result["total_inactive"] == _INACTIVE_COUNT
    assert isinstance(result["by_department"], list)
    assert isinstance(result["by_job"], list)
    assert result["cache_status"] == "fresh"
    assert isinstance(result["as_of"], str)
    assert result["rpc_duration_ms"] >= 0


# ── Test 2 — Happy path: department rows parsed correctly ─────────────────────


async def test_happy_path_department_rows_parsed(mock_client):
    result = await get_headcount(client=mock_client)

    by_dept = result["by_department"]
    assert len(by_dept) == len(_DEPT_ROWS)

    # Top entry: highest count (Sales = 5)
    assert by_dept[0]["department_id"] == 10
    assert by_dept[0]["department_name"] == "Sales"
    assert by_dept[0]["count"] == 5

    # Null bucket is present
    null_rows = [r for r in by_dept if r["department_id"] is None]
    assert len(null_rows) == 1
    assert null_rows[0]["department_name"] == _NO_DEPT_DISPLAY
    assert null_rows[0]["count"] == 2


# ── Test 3 — department_id=False → null bucket with correct label ─────────────


async def test_null_department_becomes_no_dept_bucket():
    client = _make_client(4, 0, [{"department_id": False, "__count": 4}], [])

    result = await get_headcount(client=client)

    assert len(result["by_department"]) == 1
    row = result["by_department"][0]
    assert row["department_id"] is None
    assert row["department_name"] == _NO_DEPT_DISPLAY
    assert row["count"] == 4


# ── Test 4 — job_id=False → null bucket with correct label ───────────────────


async def test_null_job_becomes_no_job_bucket():
    client = _make_client(3, 0, [], [{"job_id": False, "__count": 3}])

    result = await get_headcount(client=client)

    assert len(result["by_job"]) == 1
    row = result["by_job"][0]
    assert row["job_id"] is None
    assert row["job_name"] == _NO_JOB_DISPLAY
    assert row["count"] == 3


# ── Test 5 — All-zero edge case ───────────────────────────────────────────────


async def test_all_zero_edge_case():
    client = _make_client(0, 0, [], [])

    result = await get_headcount(client=client)

    assert result["total_active"] == 0
    assert result["total_inactive"] == 0
    assert result["by_department"] == []
    assert result["by_job"] == []
    assert result["cache_status"] == "fresh"


# ── Test 6 — Sorting: count DESC, name ASC on equal counts ───────────────────


async def test_sorting_stability_equal_count():
    dept_rows = [
        {"department_id": [30, "Zebra Dept"],  "__count": 10},
        {"department_id": [31, "Alpha Dept"],  "__count": 10},
        {"department_id": [32, "Middle Dept"], "__count":  5},
    ]
    client = _make_client(25, 0, dept_rows, [])

    result = await get_headcount(client=client)

    by_dept = result["by_department"]
    # count=10 tie broken alphabetically ASC
    assert by_dept[0]["department_name"] == "Alpha Dept"
    assert by_dept[0]["count"] == 10
    assert by_dept[1]["department_name"] == "Zebra Dept"
    assert by_dept[1]["count"] == 10
    # lower count comes last regardless of name
    assert by_dept[2]["department_name"] == "Middle Dept"
    assert by_dept[2]["count"] == 5


# ── Test 7 — Second call served from cache ────────────────────────────────────


async def test_second_call_served_from_cache(mock_client):
    result1 = await get_headcount(client=mock_client)
    result2 = await get_headcount(client=mock_client)

    # Only the first call fires RPCs (4); second hits cache
    assert mock_client.execute_kw.call_count == 4, (
        "execute_kw must fire exactly 4 times on the first call; "
        "the second call must be served from cache"
    )
    assert result1["cache_status"] == "fresh"
    assert result2["cache_status"] == "cached"
    assert result2["rpc_duration_ms"] == 0
    assert result2["total_active"] == result1["total_active"]
    assert result2["total_inactive"] == result1["total_inactive"]


# ── Test 8 — Read-only guard fires before any RPC ─────────────────────────────


async def test_read_only_guard_raises_before_rpc(monkeypatch, mock_client):
    monkeypatch.setattr(
        "backend.modules.hr.services.kpi_service.ALLOWED_METHODS",
        frozenset({"read_group", "write"}),
    )

    with pytest.raises(ReadOnlyViolationError):
        await get_headcount(client=mock_client)

    mock_client.execute_kw.assert_not_called()


# ── Test 9 — RPC failure raises OdooQueryError ───────────────────────────────


async def test_rpc_failure_raises_odoo_query_error(mock_client):
    mock_client.execute_kw.side_effect = RuntimeError("connection refused")

    with pytest.raises(OdooQueryError):
        await get_headcount(client=mock_client)


# ── Test 10 — RPC failure writes no cache entry ──────────────────────────────


async def test_rpc_failure_writes_no_cache_entry(mock_client):
    mock_client.execute_kw.side_effect = RuntimeError("timeout")

    with pytest.raises(OdooQueryError):
        await get_headcount(client=mock_client)

    cache_key = _cache.make_key(_CACHE_KEY_PREFIX_HEADCOUNT)
    assert _cache.get(cache_key) is None, (
        "A failed RPC must not leave a cache entry"
    )


# ── Test 11 — by_department sum equals total_active ──────────────────────────


async def test_department_sum_equals_total_active(mock_client):
    result = await get_headcount(client=mock_client)

    dept_sum = sum(r["count"] for r in result["by_department"])
    assert dept_sum == result["total_active"], (
        f"by_department counts must sum to total_active={result['total_active']}, "
        f"got {dept_sum}. Null-dept employees must be in the breakdown, not dropped."
    )


# ── Test 12 — by_job sum equals total_active ─────────────────────────────────


async def test_job_sum_equals_total_active(mock_client):
    result = await get_headcount(client=mock_client)

    job_sum = sum(r["count"] for r in result["by_job"])
    assert job_sum == result["total_active"], (
        f"by_job counts must sum to total_active={result['total_active']}, "
        f"got {job_sum}. Null-job employees must be in the breakdown, not dropped."
    )
