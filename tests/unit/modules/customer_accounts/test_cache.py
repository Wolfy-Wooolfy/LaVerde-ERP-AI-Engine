"""
Unit tests for customer_accounts/services/cache.py — today_str() timezone behaviour.

Mirrors tests/unit/modules/collections/test_cache.py exactly — both modules
must behave identically (Decision 5.9 / D-2 fix).
"""

from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from backend.modules.customer_accounts.services import cache as ca_cache


CAIRO_TZ = ZoneInfo("Africa/Cairo")


def test_today_str_format():
    """today_str() returns a YYYY-MM-DD string."""
    result = ca_cache.today_str()
    assert len(result) == 10
    parts = result.split("-")
    assert len(parts) == 3
    year, month, day = parts
    assert len(year) == 4 and year.isdigit()
    assert len(month) == 2 and month.isdigit()
    assert len(day) == 2 and day.isdigit()


def test_today_str_matches_cairo():
    """today_str() returns the Cairo-local date, not a UTC or arbitrary date."""
    result = ca_cache.today_str()
    expected = datetime.now(CAIRO_TZ).date().isoformat()
    assert result == expected


def test_today_str_timezone_stable():
    """today_str() returns Cairo date even when datetime.now is mocked to a UTC time.

    Scenario: it is 23:30 UTC on day D, which is 01:30 Cairo time on day D+1
    (UTC+2 in winter). today_str() must return D+1, not D.
    """
    utc_time = datetime(2026, 1, 14, 23, 30, 0, tzinfo=timezone.utc)
    cairo_date_expected = "2026-01-15"

    with patch(
        "backend.modules.customer_accounts.services.cache.datetime"
    ) as mock_dt:
        mock_dt.now.return_value = utc_time.astimezone(CAIRO_TZ)
        result = ca_cache.today_str()

    assert result == cairo_date_expected, (
        f"Expected Cairo date {cairo_date_expected!r}, got {result!r}"
    )


def test_today_str_dst_boundary():
    """today_str() returns correct Cairo date across DST boundary (UTC+3 in summer).

    Scenario: 21:45 UTC on day D in July (DST active, Cairo = UTC+3) →
    00:45 Cairo on day D+1. today_str() must return D+1.
    """
    utc_time = datetime(2026, 7, 20, 21, 45, 0, tzinfo=timezone.utc)
    cairo_date_expected = "2026-07-21"

    with patch(
        "backend.modules.customer_accounts.services.cache.datetime"
    ) as mock_dt:
        mock_dt.now.return_value = utc_time.astimezone(CAIRO_TZ)
        result = ca_cache.today_str()

    assert result == cairo_date_expected, (
        f"Expected Cairo date {cairo_date_expected!r}, got {result!r}"
    )


def test_make_key_includes_cairo_date():
    """make_key() embeds the Cairo-local date in the key."""
    with patch(
        "backend.modules.customer_accounts.services.cache.today_str",
        return_value="2026-05-24",
    ):
        key = ca_cache.make_key("kpib_test")
    assert key == "kpib_test:2026-05-24"
