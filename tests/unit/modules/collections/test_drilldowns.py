"""
Stage 5 unit tests — drill-down service functions and endpoint helpers.

These tests use a mocked OdooClient. They verify logic correctness:
pagination, cursor round-trips, filter clause construction, sort
ordering, envelope shape, request_id propagation, and row serialization.
They do NOT and CANNOT verify identity-equal correctness against live Odoo
data — that is the exclusive responsibility of
scripts/verify_drilldowns_live.py (D6). A green run of this file is
necessary but not sufficient for Stage 5 sign-off.
"""

import base64
import re
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.api.v1.endpoints.collections import _req_id
from backend.modules.collections.installment_type_names import INSTALLMENT_TYPE_NAMES_EN
from backend.modules.collections.services.drilldown_service import (
    _decode_cursor,
    _encode_cursor,
    _serialize_row,
    get_forecast_drilldown,
    get_late_drilldown,
    get_portfolio_drilldown,
    get_project_drilldown,
    get_trend_drilldown,
)

# ── Shared test data ──────────────────────────────────────────────────────────

_SAMPLE_ROW = {
    "id": 1001,
    "date": "2026-04-15",
    "amount": 100_000.0,
    "due_amount": 95_000.0,
    "paid_amount": 5_000.0,
    "x_studio_actual_paid_amount": 3_000.0,
    "payment_state": "partial",
    "partner_id": [42, "Test Customer"],
    "project_id": [1, "New Capital"],
}

_SAMPLE_RG_ROW = {
    "partner_id": [42, "Test Customer"],
    "project_id": [1, "New Capital"],
    "amount": 100_000.0,
    "due_amount": 95_000.0,
    "paid_amount": 5_000.0,
    "x_studio_actual_paid_amount": 3_000.0,
    "__count": 5,
}

_MOCK_CAIRO_DATE = date(2026, 5, 21)


@pytest.fixture
def mc():
    """Minimal OdooClient mock — is_read_only=True, authenticate no-ops."""
    client = MagicMock()
    client.is_read_only = True
    client.authenticate = AsyncMock()
    client.close = AsyncMock()
    return client


def _domain_from_search_count(mc: MagicMock) -> list:
    """Extract the base domain passed to the first execute_kw call (search_count)."""
    return mc.execute_kw.call_args_list[0].kwargs["args"][0]


def _order_from_search_read(mc: MagicMock) -> str:
    """Extract the order kwarg from the search_read execute_kw call."""
    return mc.execute_kw.call_args_list[1].kwargs["kwargs"]["order"]


def _patch_cairo(mock_date: date = _MOCK_CAIRO_DATE):
    """Context manager: patch datetime in drilldown_service to return mock_date."""
    m = MagicMock()
    m.now.return_value.date.return_value = mock_date
    return patch(
        "backend.modules.collections.services.drilldown_service.datetime", m
    )


# ── Section 1 — Happy-path envelope shape (5) ────────────────────────────────


async def test_late_drilldown_happy_path(mc: MagicMock) -> None:
    mc.execute_kw = AsyncMock(side_effect=[42, [_SAMPLE_ROW]])
    result = await get_late_drilldown(request_id="req-1", client=mc)

    assert result["version"] == "1.0"
    assert "data" in result and "meta" in result
    meta = result["meta"]
    assert meta["total_count"] == 42
    assert meta["page_size"] == 50
    assert meta["request_id"] == "req-1"
    assert len(result["data"]["items"]) == 1
    assert {
        "request_id", "as_of", "rpc_duration_ms", "page_size",
        "total_count", "cursor_current", "cursor_next", "has_next",
        "filters_applied", "sort_applied",
    }.issubset(meta.keys())


async def test_forecast_drilldown_happy_path(mc: MagicMock) -> None:
    mc.execute_kw = AsyncMock(side_effect=[10, [_SAMPLE_ROW]])
    result = await get_forecast_drilldown(
        request_id="req-2", bucket_url_key="month", client=mc
    )

    assert result["version"] == "1.0"
    assert result["data"]["bucket"] == "this_month"
    assert result["data"]["bucket_url_key"] == "month"
    assert len(result["data"]["items"]) == 1
    assert result["meta"]["total_count"] == 10


async def test_portfolio_drilldown_happy_path(mc: MagicMock) -> None:
    mc.execute_kw = AsyncMock(return_value=[_SAMPLE_RG_ROW])
    result = await get_portfolio_drilldown(request_id="req-3", client=mc)

    assert result["version"] == "1.0"
    assert "customers" in result["data"]
    assert len(result["data"]["customers"]) == 1
    c = result["data"]["customers"][0]
    assert c["customer_id"] == 42
    assert c["total_amount"] == 100_000.0
    # Decision 14.13: data_quality absent when all rows have valid project
    assert result["meta"].get("data_quality") is None


async def test_project_drilldown_happy_path(mc: MagicMock) -> None:
    mc.execute_kw = AsyncMock(side_effect=[
        42,
        [{"due_amount": 90_000.0, "__count": 42}],
        [_SAMPLE_ROW],
    ])
    result = await get_project_drilldown(
        request_id="req-4", project_id=1, client=mc
    )

    assert result["version"] == "1.0"
    assert result["data"]["project_id"] == 1
    assert result["data"]["total_late_uncollected"] == 90_000.0
    assert result["data"]["total_record_count"] == 42
    assert len(result["data"]["items"]) == 1


async def test_trend_drilldown_happy_path(mc: MagicMock) -> None:
    mc.execute_kw = AsyncMock(side_effect=[5, [_SAMPLE_ROW]])
    with _patch_cairo():
        result = await get_trend_drilldown(
            request_id="req-5", month="2026-05", client=mc
        )

    assert result["version"] == "1.0"
    assert result["data"]["month"] == "2026-05"
    assert len(result["data"]["items"]) == 1
    assert result["meta"]["total_count"] == 5


# ── Section 1b — Row serialization (4) + Decision 14.12 assertion (1) ────────


def test_serialize_row_computes_late_amount() -> None:
    row = {**_SAMPLE_ROW, "amount": 100_000.0, "x_studio_actual_paid_amount": 30_000.0}
    assert _serialize_row(row)["late_amount"] == pytest.approx(70_000.0)


def test_serialize_row_late_amount_when_actual_paid_zero() -> None:
    row = {**_SAMPLE_ROW, "amount": 100_000.0, "x_studio_actual_paid_amount": 0.0}
    assert _serialize_row(row)["late_amount"] == pytest.approx(100_000.0)


def test_serialize_row_computes_pending_cheque() -> None:
    row = {**_SAMPLE_ROW, "paid_amount": 50_000.0, "x_studio_actual_paid_amount": 30_000.0}
    assert _serialize_row(row)["pending_cheque"] == pytest.approx(20_000.0)


def test_serialize_row_pending_cheque_clamped_to_zero() -> None:
    # paid < actual → max(..., 0) must clamp to 0, never negative (Decision 9.1)
    row = {**_SAMPLE_ROW, "paid_amount": 20_000.0, "x_studio_actual_paid_amount": 30_000.0}
    assert _serialize_row(row)["pending_cheque"] == pytest.approx(0.0)


# D-1 — _serialize_row must include installment_type_name_en


def test_serialize_row_has_installment_type_name_en_field() -> None:
    row = {**_SAMPLE_ROW, "installment_type_id": [3, "Regular"]}
    result = _serialize_row(row)
    assert "installment_type_name_en" in result


def test_serialize_row_type_name_en_resolved_from_mapping() -> None:
    row = {**_SAMPLE_ROW, "installment_type_id": [3, "Regular"]}
    assert _serialize_row(row)["installment_type_name_en"] == "Regular"


def test_serialize_row_type_name_en_for_garage() -> None:
    row = {**_SAMPLE_ROW, "installment_type_id": [7, "Garage"]}
    assert _serialize_row(row)["installment_type_name_en"] == "Garage"


def test_serialize_row_ar_and_en_names_coexist() -> None:
    row = {**_SAMPLE_ROW, "installment_type_id": [2, "Down Payment"]}
    result = _serialize_row(row)
    assert result["installment_type_name_ar"] == "المقدمة"
    assert result["installment_type_name_en"] == "Down Payment"


def test_serialize_row_en_name_all_13_ids_resolvable() -> None:
    for tid in range(1, 14):
        row = {**_SAMPLE_ROW, "installment_type_id": [tid, "dummy"]}
        result = _serialize_row(row)
        assert result["installment_type_name_en"] == INSTALLMENT_TYPE_NAMES_EN[tid], (
            f"ID {tid}: expected {INSTALLMENT_TYPE_NAMES_EN[tid]!r}, "
            f"got {result['installment_type_name_en']!r}"
        )


# D-6 — installment_type_id field extraction and base-field preservation
# Migrated from backend/modules/collections/tests/test_stage7.py (legacy path).
# These complement the D-1 EN-name tests above: D-1 verified the EN name field
# exists and resolves correctly; these verify the type_id integer and that adding
# the type-name fields does not corrupt the pre-existing serialised fields.


def test_serialize_row_type_id_extracted_from_list() -> None:
    row = {**_SAMPLE_ROW, "installment_type_id": [3, "Regular"]}
    assert _serialize_row(row)["installment_type_id"] == 3


def test_serialize_row_type_id_from_plain_int() -> None:
    row = {**_SAMPLE_ROW, "installment_type_id": 7}
    assert _serialize_row(row)["installment_type_id"] == 7


def test_serialize_row_type_id_zero_when_false() -> None:
    row = {**_SAMPLE_ROW, "installment_type_id": False}
    assert _serialize_row(row)["installment_type_id"] == 0


def test_serialize_row_base_fields_unchanged_after_type_fields_added() -> None:
    # _SAMPLE_ROW: amount=100_000, x_studio_actual_paid_amount=3_000
    # → late_amount = 100_000 − 3_000 = 97_000
    row = {**_SAMPLE_ROW, "installment_type_id": [3, "Regular"]}
    result = _serialize_row(row)
    assert result["record_id"] == 1001
    assert result["payment_state"] == "partial"
    assert result["amount"] == pytest.approx(100_000.0)
    assert result["late_amount"] == pytest.approx(97_000.0)


def test_serialize_row_all_required_fields_present() -> None:
    # Includes installment_type_name_en: added by D-1, not in original Stage 7 spec.
    row = {**_SAMPLE_ROW, "installment_type_id": [3, "Regular"]}
    result = _serialize_row(row)
    required = {
        "record_id", "customer_name", "project_id", "project_name_ar",
        "project_name_en", "installment_type_id", "installment_type_name_ar",
        "installment_type_name_en", "date", "amount", "due_amount",
        "paid_amount", "actual_paid_amount", "pending_cheque",
        "payment_state", "late_amount",
    }
    assert required.issubset(result.keys())


async def test_portfolio_drilldown_uses_read_group_not_search_read(mc: MagicMock) -> None:
    """Decision 14.12: portfolio must aggregate via read_group, not pull raw rows.
    Regression guard — if changed to search_read the 42K-row transfer problem returns.
    """
    mc.execute_kw = AsyncMock(return_value=[_SAMPLE_RG_ROW])
    await get_portfolio_drilldown(request_id="r", client=mc)

    assert mc.execute_kw.call_count == 1
    call = mc.execute_kw.call_args_list[0]
    method = call.args[1]
    assert method == "read_group", (
        f"Portfolio must call read_group, got {method!r} — "
        "search_read would transfer all 42K installment rows."
    )
    groupby = call.kwargs["args"][2]
    assert "partner_id" in groupby


async def test_portfolio_drilldown_includes_unassigned_project(mc: MagicMock) -> None:
    """Decision 14.13: project_id=False rows appear under 'بدون مشروع', included in totals.

    Before the fix these rows were silently dropped (6.5M EGP gap in D6).
    """
    mc.execute_kw = AsyncMock(return_value=[
        # Same customer, assigned project
        {
            "partner_id": [101, "Customer A"],
            "project_id": [1, "New Capital"],
            "amount": 100_000.0,
            "due_amount": 80_000.0,
            "paid_amount": 20_000.0,
            "x_studio_actual_paid_amount": 20_000.0,
            "__count": 2,
        },
        # Same customer, no project assigned → was silently dropped before Decision 14.13
        {
            "partner_id": [101, "Customer A"],
            "project_id": False,
            "amount": 50_000.0,
            "due_amount": 50_000.0,
            "paid_amount": 0.0,
            "x_studio_actual_paid_amount": 0.0,
            "__count": 1,
        },
    ])
    result = await get_portfolio_drilldown(request_id="r1", client=mc)

    customers = result["data"]["customers"]
    assert len(customers) == 1, "Both rows belong to the same customer — must collapse to 1"

    cust = customers[0]
    assert cust["customer_id"] == 101
    assert cust["total_amount"] == pytest.approx(150_000.0), (
        "total_amount must include both assigned and unassigned project rows"
    )
    assert cust["record_count"] == 3

    breakdown = cust["project_breakdown"]
    assert len(breakdown) == 2

    no_proj = next((b for b in breakdown if b["project_id"] is None), None)
    assert no_proj is not None, "project_breakdown must contain a None-project entry"
    assert no_proj["project_name_ar"] == "بدون مشروع"
    assert no_proj["project_name_en"] == "No Project Assigned"
    assert no_proj["amount"] == pytest.approx(50_000.0)
    assert no_proj["record_count"] == 1

    known_proj = next(b for b in breakdown if b["project_id"] == 1)
    assert known_proj["project_name_en"] == "New Capital"
    assert known_proj["amount"] == pytest.approx(100_000.0)


async def test_portfolio_drilldown_meta_reports_unassigned(mc: MagicMock) -> None:
    """Decision 14.13: data_quality block populated when project_id=False rows exist;
    absent (None) when all rows have a valid project.
    """
    # Case A: rows with project_id=False → data_quality populated
    mc.execute_kw = AsyncMock(return_value=[
        {
            "partner_id": [101, "Customer A"],
            "project_id": False,
            "amount": 6_500_203.0,
            "due_amount": 6_500_203.0,
            "paid_amount": 0.0,
            "x_studio_actual_paid_amount": 0.0,
            "__count": 208,
        },
    ])
    result = await get_portfolio_drilldown(request_id="r2", client=mc)
    dq = result["meta"].get("data_quality")
    assert dq is not None, "data_quality must be present when project_id=False rows exist"
    assert dq["unassigned_project_installments"] == 208
    assert abs(dq["unassigned_project_amount"] - 6_500_203.0) < 0.01
    assert "note_ar" in dq and "بدون مشروع" in dq["note_ar"]
    assert "note_en" in dq and "No Project Assigned" in dq["note_en"]

    # Case B: all rows have valid project_id → data_quality absent
    mc.execute_kw = AsyncMock(return_value=[_SAMPLE_RG_ROW])
    result_clean = await get_portfolio_drilldown(request_id="r3", client=mc)
    assert result_clean["meta"].get("data_quality") is None, (
        "data_quality must be None when all rows have a valid project"
    )


# ── Section 2 — Tri-state has_pending_cheque (9 + 1 trend) ───────────────────


async def test_late_drilldown_cheque_filter_none_returns_all(mc: MagicMock) -> None:
    mc.execute_kw = AsyncMock(side_effect=[0, []])
    await get_late_drilldown(request_id="r", has_pending_cheque=None, client=mc)
    domain = _domain_from_search_count(mc)
    assert not any(
        isinstance(c, tuple) and c[0] == "check_pending_amount"
        for c in domain
    )


async def test_late_drilldown_cheque_filter_true_returns_only_pending(mc: MagicMock) -> None:
    mc.execute_kw = AsyncMock(side_effect=[0, []])
    await get_late_drilldown(request_id="r", has_pending_cheque=True, client=mc)
    domain = _domain_from_search_count(mc)
    assert ("check_pending_amount", ">", 0) in domain


async def test_late_drilldown_cheque_filter_false_returns_only_non_pending(mc: MagicMock) -> None:
    mc.execute_kw = AsyncMock(side_effect=[0, []])
    await get_late_drilldown(request_id="r", has_pending_cheque=False, client=mc)
    domain = _domain_from_search_count(mc)
    assert ("check_pending_amount", "=", 0) in domain


async def test_forecast_drilldown_cheque_filter_none_returns_all(mc: MagicMock) -> None:
    mc.execute_kw = AsyncMock(side_effect=[0, []])
    await get_forecast_drilldown(
        request_id="r", bucket_url_key="year", has_pending_cheque=None, client=mc
    )
    domain = _domain_from_search_count(mc)
    assert not any(
        isinstance(c, tuple) and c[0] == "check_pending_amount"
        for c in domain
    )


async def test_forecast_drilldown_cheque_filter_true_returns_only_pending(mc: MagicMock) -> None:
    mc.execute_kw = AsyncMock(side_effect=[0, []])
    await get_forecast_drilldown(
        request_id="r", bucket_url_key="year", has_pending_cheque=True, client=mc
    )
    domain = _domain_from_search_count(mc)
    assert ("check_pending_amount", ">", 0) in domain


async def test_forecast_drilldown_cheque_filter_false_returns_only_non_pending(mc: MagicMock) -> None:
    mc.execute_kw = AsyncMock(side_effect=[0, []])
    await get_forecast_drilldown(
        request_id="r", bucket_url_key="year", has_pending_cheque=False, client=mc
    )
    domain = _domain_from_search_count(mc)
    assert ("check_pending_amount", "=", 0) in domain


async def test_project_drilldown_cheque_filter_none_returns_all(mc: MagicMock) -> None:
    mc.execute_kw = AsyncMock(side_effect=[0, [], []])
    await get_project_drilldown(
        request_id="r", project_id=1, has_pending_cheque=None, client=mc
    )
    domain = _domain_from_search_count(mc)
    assert not any(
        isinstance(c, tuple) and c[0] == "check_pending_amount"
        for c in domain
    )


async def test_project_drilldown_cheque_filter_true_returns_only_pending(mc: MagicMock) -> None:
    mc.execute_kw = AsyncMock(side_effect=[0, [], []])
    await get_project_drilldown(
        request_id="r", project_id=1, has_pending_cheque=True, client=mc
    )
    domain = _domain_from_search_count(mc)
    assert ("check_pending_amount", ">", 0) in domain


async def test_project_drilldown_cheque_filter_false_returns_only_non_pending(mc: MagicMock) -> None:
    mc.execute_kw = AsyncMock(side_effect=[0, [], []])
    await get_project_drilldown(
        request_id="r", project_id=1, has_pending_cheque=False, client=mc
    )
    domain = _domain_from_search_count(mc)
    assert ("check_pending_amount", "=", 0) in domain


async def test_trend_drilldown_cheque_filter_true_adds_clause(mc: MagicMock) -> None:
    mc.execute_kw = AsyncMock(side_effect=[0, []])
    with _patch_cairo():
        await get_trend_drilldown(
            request_id="r", month="2026-05", has_pending_cheque=True, client=mc
        )
    domain = _domain_from_search_count(mc)
    assert ("check_pending_amount", ">", 0) in domain


# ── Section 3 — Cursor round-trips (6) ───────────────────────────────────────


def test_cursor_encode_decode_roundtrip() -> None:
    payload = {"sv": "2026-04-15", "id": 42, "sb": "date", "sd": "asc"}
    encoded = _encode_cursor(payload)
    assert isinstance(encoded, str)
    decoded = _decode_cursor(encoded)
    assert decoded == payload


def test_cursor_malformed_decode_returns_empty_dict() -> None:
    result = _decode_cursor("this_is_not_valid_base64!!!")
    assert result == {}


def test_cursor_tampered_base64_decode_returns_empty_dict() -> None:
    # Valid base64 encoding, but the decoded bytes are not valid JSON.
    tampered = base64.urlsafe_b64encode(b"not-json-at-all").decode()
    result = _decode_cursor(tampered)
    assert result == {}


async def test_cursor_keyset_clause_applied_to_domain(mc: MagicMock) -> None:
    cursor = _encode_cursor({"sv": "2026-04-15", "id": 100, "sb": "date", "sd": "asc"})
    mc.execute_kw = AsyncMock(side_effect=[42, [_SAMPLE_ROW]])
    await get_late_drilldown(
        request_id="r", cursor=cursor, sort_by="date", sort_dir="asc", client=mc
    )
    # search_read (call index 1) receives page_domain with the keyset clause appended.
    page_domain = mc.execute_kw.call_args_list[1].kwargs["args"][0]
    # ASC keyset: ["|", ("date",">","2026-04-15"), "&", ("date","=","2026-04-15"), ("id",">",100)]
    assert "|" in page_domain
    assert ("date", ">", "2026-04-15") in page_domain
    assert ("date", "=", "2026-04-15") in page_domain
    assert ("id", ">", 100) in page_domain


async def test_last_page_returns_cursor_next_none_and_has_next_false(mc: MagicMock) -> None:
    # Exactly page_size rows → no next page.
    mc.execute_kw = AsyncMock(side_effect=[3, [_SAMPLE_ROW] * 3])
    result = await get_late_drilldown(request_id="r", page_size=3, client=mc)
    assert result["meta"]["has_next"] is False
    assert result["meta"]["cursor_next"] is None
    assert len(result["data"]["items"]) == 3


async def test_page_size_plus_one_trick_sets_has_next_without_extra_row(mc: MagicMock) -> None:
    # page_size+1 rows fetched → has_next=True, but items truncated to page_size.
    mc.execute_kw = AsyncMock(side_effect=[100, [_SAMPLE_ROW] * 4])
    result = await get_late_drilldown(request_id="r", page_size=3, client=mc)
    assert result["meta"]["has_next"] is True
    assert len(result["data"]["items"]) == 3


# ── Section 4 — Sort ordering (3) ────────────────────────────────────────────


async def test_late_drilldown_sort_by_date_sends_date_order(mc: MagicMock) -> None:
    mc.execute_kw = AsyncMock(side_effect=[0, []])
    await get_late_drilldown(
        request_id="r", sort_by="date", sort_dir="desc", client=mc
    )
    assert _order_from_search_read(mc) == "date desc, id desc"


async def test_late_drilldown_sort_by_amount_sends_amount_order(mc: MagicMock) -> None:
    mc.execute_kw = AsyncMock(side_effect=[0, []])
    await get_late_drilldown(
        request_id="r", sort_by="amount", sort_dir="asc", client=mc
    )
    assert _order_from_search_read(mc) == "amount asc, id asc"


async def test_late_drilldown_sort_by_due_amount_sends_due_amount_order(mc: MagicMock) -> None:
    mc.execute_kw = AsyncMock(side_effect=[0, []])
    await get_late_drilldown(
        request_id="r", sort_by="due_amount", sort_dir="desc", client=mc
    )
    assert _order_from_search_read(mc) == "due_amount desc, id desc"


# ── Section 5 — Error / validation (7) ───────────────────────────────────────


async def test_forecast_drilldown_invalid_bucket_raises_value_error(mc: MagicMock) -> None:
    mc.execute_kw = AsyncMock()
    with pytest.raises(ValueError, match="Unknown bucket key"):
        await get_forecast_drilldown(
            request_id="r", bucket_url_key="century", client=mc
        )
    mc.execute_kw.assert_not_called()


async def test_project_drilldown_invalid_project_id_raises_value_error(mc: MagicMock) -> None:
    mc.execute_kw = AsyncMock()
    with pytest.raises(ValueError, match="Invalid project_id"):
        await get_project_drilldown(request_id="r", project_id=99, client=mc)
    mc.execute_kw.assert_not_called()


async def test_trend_drilldown_invalid_month_format_raises(mc: MagicMock) -> None:
    # "2026-13" is syntactically YYYY-MM but month 13 is not a valid date.
    mc.execute_kw = AsyncMock()
    with pytest.raises(ValueError, match="Invalid month format"):
        await get_trend_drilldown(request_id="r", month="2026-13", client=mc)
    mc.execute_kw.assert_not_called()


async def test_trend_drilldown_out_of_range_month_raises(mc: MagicMock) -> None:
    # Valid format + valid month, but 76 months before the mocked Cairo date.
    mc.execute_kw = AsyncMock()
    with _patch_cairo():
        with pytest.raises(ValueError, match="out of range"):
            await get_trend_drilldown(request_id="r", month="2020-01", client=mc)
    mc.execute_kw.assert_not_called()


async def test_trend_drilldown_accepts_month_at_range_boundary(mc: MagicMock) -> None:
    """Year-wrap boundary: today = 2026-01-15 (Cairo).
    Valid trailing-6 range is 2025-08 through 2026-01.
    months_behind math: (2026-2025)*12 + (1-8)=5 → accepted (boundary).
                        (2026-2025)*12 + (1-7)=6 → rejected (one past boundary).
    """
    mc.execute_kw = AsyncMock(side_effect=[0, []])
    with _patch_cairo(date(2026, 1, 15)):
        # 2025-08 is exactly 5 months back — the oldest valid month.
        result = await get_trend_drilldown(
            request_id="r", month="2025-08", client=mc
        )
    assert result["version"] == "1.0"

    # 2025-07 is 6 months back — one past the boundary, must be rejected.
    with _patch_cairo(date(2026, 1, 15)):
        with pytest.raises(ValueError, match="out of range"):
            await get_trend_drilldown(request_id="r", month="2025-07", client=mc)


async def test_trend_drilldown_in_range_empty_month_returns_empty_page(mc: MagicMock) -> None:
    # Zero data for a valid in-range month → 200 OK with empty items, NOT a 404.
    mc.execute_kw = AsyncMock(side_effect=[0, []])
    with _patch_cairo():
        result = await get_trend_drilldown(
            request_id="r", month="2026-05", client=mc
        )
    assert result["version"] == "1.0"
    assert result["data"]["items"] == []
    assert result["meta"]["total_count"] == 0


async def test_late_drilldown_malformed_cursor_treated_as_first_page(mc: MagicMock) -> None:
    # Malformed cursor → _decode_cursor returns {} → silently ignored (first page).
    mc.execute_kw = AsyncMock(side_effect=[10, [_SAMPLE_ROW]])
    result = await get_late_drilldown(
        request_id="r", cursor="!!not-valid-base64!!", client=mc
    )
    assert result["version"] == "1.0"
    assert result["meta"]["cursor_current"] == "!!not-valid-base64!!"


async def test_page_size_above_max_clamped_to_200(mc: MagicMock) -> None:
    mc.execute_kw = AsyncMock(side_effect=[0, []])
    result = await get_late_drilldown(request_id="r", page_size=9999, client=mc)
    assert result["meta"]["page_size"] == 200


# ── Section 6 — Read-only assertion (5) ──────────────────────────────────────


async def test_late_drilldown_read_only_assertion_fires_when_violated(mc: MagicMock) -> None:
    mc.is_read_only = False
    with pytest.raises(AssertionError):
        await get_late_drilldown(request_id="r", client=mc)
    mc.execute_kw.assert_not_called()


async def test_forecast_drilldown_read_only_assertion_fires_when_violated(mc: MagicMock) -> None:
    mc.is_read_only = False
    with pytest.raises(AssertionError):
        await get_forecast_drilldown(
            request_id="r", bucket_url_key="month", client=mc
        )
    mc.execute_kw.assert_not_called()


async def test_portfolio_drilldown_read_only_assertion_fires_when_violated(mc: MagicMock) -> None:
    mc.is_read_only = False
    with pytest.raises(AssertionError):
        await get_portfolio_drilldown(request_id="r", client=mc)
    mc.execute_kw.assert_not_called()


async def test_project_drilldown_read_only_assertion_fires_when_violated(mc: MagicMock) -> None:
    mc.is_read_only = False
    with pytest.raises(AssertionError):
        await get_project_drilldown(request_id="r", project_id=1, client=mc)
    mc.execute_kw.assert_not_called()


async def test_trend_drilldown_read_only_assertion_fires_when_violated(mc: MagicMock) -> None:
    mc.is_read_only = False
    # Patch datetime so range validation passes before the assertion fires.
    with _patch_cairo():
        with pytest.raises(AssertionError):
            await get_trend_drilldown(
                request_id="r", month="2026-05", client=mc
            )
    mc.execute_kw.assert_not_called()


# ── Section 7 — Request ID (3) ───────────────────────────────────────────────


async def test_late_drilldown_request_id_echoed_in_meta(mc: MagicMock) -> None:
    mc.execute_kw = AsyncMock(side_effect=[0, []])
    result = await get_late_drilldown(request_id="trace-abc123", client=mc)
    assert result["meta"]["request_id"] == "trace-abc123"


def test_req_id_helper_returns_state_value_when_present() -> None:
    # _req_id() now reads from request.state.request_id (set by middleware before
    # the endpoint runs). The middleware is the single source of truth per request.
    mock_request = MagicMock()
    mock_request.state.request_id = "my-trace-id"
    result = _req_id(mock_request)
    assert result == "my-trace-id"


def test_req_id_helper_generates_uuid4_hex_when_state_absent() -> None:
    # When request.state has no request_id (e.g. tests bypassing middleware),
    # _req_id() falls back to a fresh 32-char hex UUID.
    mock_request = MagicMock(spec=["state"])
    mock_request.state = MagicMock(spec=[])  # no request_id attribute
    result = _req_id(mock_request)
    assert re.fullmatch(r"[0-9a-f]{32}", result), (
        f"Expected 32-char lowercase hex UUID4, got {result!r}"
    )
