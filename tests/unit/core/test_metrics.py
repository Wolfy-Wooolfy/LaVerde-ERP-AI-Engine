"""Unit tests for in-memory metrics store."""

import pytest

from backend.core.metrics import _Metrics


@pytest.fixture
def m() -> _Metrics:
    instance = _Metrics()
    return instance


def test_initial_snapshot_zeros(m: _Metrics) -> None:
    snap = m.snapshot()
    assert snap["odoo"]["total_calls"] == 0
    assert snap["cache"]["hits"] == 0
    assert snap["api"]["total_requests"] == 0


def test_record_odoo_call(m: _Metrics) -> None:
    m.record_odoo_call("search_read", 120.5)
    m.record_odoo_call("read_group", 80.0)
    snap = m.snapshot()
    assert snap["odoo"]["total_calls"] == 2
    assert snap["odoo"]["by_method"]["search_read"] == 1
    assert snap["odoo"]["avg_duration_ms"] == pytest.approx(100.25, abs=0.1)


def test_record_odoo_error(m: _Metrics) -> None:
    m.record_odoo_call("search_read", 50.0, error=True)
    snap = m.snapshot()
    assert snap["odoo"]["errors"] == 1


def test_cache_hit_rate(m: _Metrics) -> None:
    m.record_cache_hit()
    m.record_cache_hit()
    m.record_cache_hit()
    m.record_cache_miss()
    snap = m.snapshot()
    assert snap["cache"]["hits"] == 3
    assert snap["cache"]["misses"] == 1
    assert snap["cache"]["hit_rate"] == pytest.approx(0.75)


def test_api_request_4xx(m: _Metrics) -> None:
    m.record_api_request(50.0, 401)
    snap = m.snapshot()
    assert snap["api"]["errors_4xx"] == 1
    assert snap["api"]["errors_5xx"] == 0


def test_api_request_5xx(m: _Metrics) -> None:
    m.record_api_request(50.0, 500)
    snap = m.snapshot()
    assert snap["api"]["errors_5xx"] == 1


def test_uptime_is_positive(m: _Metrics) -> None:
    snap = m.snapshot()
    assert snap["uptime_seconds"] >= 0


def test_reset_clears_counters(m: _Metrics) -> None:
    m.record_odoo_call("read_group", 100.0)
    m.record_cache_hit()
    m.reset()
    snap = m.snapshot()
    assert snap["odoo"]["total_calls"] == 0
    assert snap["cache"]["hits"] == 0
