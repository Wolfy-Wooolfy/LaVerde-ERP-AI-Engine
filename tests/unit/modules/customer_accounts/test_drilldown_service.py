"""
Unit tests for Customer Accounts drill-down service — M3-S6.

OdooClient is fully mocked; no live Odoo connection is made.
Live verification: scripts/verify_m3s6_drilldown_live.py.

Coverage:
  1.  test_late_timing             — installment date < today → timing='late'
  2.  test_future_timing           — installment date >= today → timing='future'
  3.  test_assertion_holds         — late+future == all_due, no error
  4.  test_assertion_fires         — delta >= 1.0 EGP raises AssertionError
  5.  test_payment_ratio_uses_actual_paid_not_paid_amount  ← KEY test (DR1)
  6.  test_pagination_has_next     — n+1 rows → has_next=True, cursor_next set
  7.  test_pagination_exact_page   — exactly n rows → has_next=False
  8.  test_cursor_roundtrip        — encode/decode preserves payload
  9.  test_read_only_assertion     — is_read_only=False raises AssertionError
  10. test_rpc_failure             — OdooQueryError on execute_kw exception
  11. test_response_shape          — all required top-level keys present
  12. test_wallet_zero_on_no_data  — empty wallet rows → wallet_balance=0.0
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.core.exceptions import OdooQueryError
from backend.modules.customer_accounts.services.drilldown_service import (
    _decode_cursor,
    _encode_cursor,
    _serialize_installment_row,
    get_customer_drilldown,
)

# ── Constants ─────────────────────────────────────────────────────────────────

_TODAY = "2026-05-23"
_PARTNER_ID = 999


# ── Helpers ───────────────────────────────────────────────────────────────────

def _patch_today(today: str = _TODAY):
    return patch(
        "backend.modules.customer_accounts.services.drilldown_service._cache"
        ".today_str",
        return_value=today,
    )


def _make_client(side_effects: list) -> MagicMock:
    """Build a minimal OdooClient mock for the 6-RPC gather.

    side_effects must have exactly 6 items corresponding to the gather order:
      [all_rg, late_rg, future_rg, wallet_rg, search_count, search_read]
    """
    client = MagicMock()
    client.is_read_only = True
    client.authenticate = AsyncMock()
    client.close = AsyncMock()
    client.execute_kw = AsyncMock(side_effect=side_effects)
    return client


def _standard_mock_data(
    *,
    total_amount: float = 1_000_000.0,
    total_due: float = 800_000.0,
    actual_paid: float = 200_000.0,
    paid_amount: float = 200_000.0,
    total_count: int = 10,
    late_due: float = 600_000.0,
    future_due: float = 200_000.0,
    wallet_balance: float = 50_000.0,
    wallet_count: int = 3,
    unpaid_count: int = 8,
    inst_rows: list | None = None,
) -> list:
    """Return the list of 6 mock return values for asyncio.gather."""
    if inst_rows is None:
        inst_rows = []
    all_rg = [{
        "partner_id":                    [_PARTNER_ID, "Test Customer"],
        "amount":                         total_amount,
        "due_amount":                     total_due,
        "paid_amount":                    paid_amount,
        "x_studio_actual_paid_amount":    actual_paid,
        "__count":                        total_count,
    }]
    late_rg   = [{"due_amount": late_due,   "__count": 6}]
    future_rg = [{"due_amount": future_due, "__count": 2}]
    wallet_rg = [{"residual_amount": wallet_balance, "__count": wallet_count}]
    return [all_rg, late_rg, future_rg, wallet_rg, unpaid_count, inst_rows]


def _make_inst_row(
    record_id: int = 1,
    date: str = "2026-04-01",
    amount: float = 100_000.0,
    due_amount: float = 100_000.0,
    payment_state: str = "unpaid",
    type_id: int = 3,
) -> dict:
    return {
        "id":                   record_id,
        "date":                 date,
        "amount":               amount,
        "due_amount":           due_amount,
        "payment_state":        payment_state,
        "installment_type_id":  [type_id, "قسط دوري"],
    }


# ── Tests: row serialization / timing ────────────────────────────────────────

def test_late_timing():
    """Installment with date strictly before today → timing='late'."""
    row = _make_inst_row(date="2026-05-22")  # one day before TODAY
    result = _serialize_installment_row(row, _TODAY)
    assert result["timing"] == "late"
    assert result["date"] == "2026-05-22"


def test_future_timing():
    """Installment with date equal to or after today → timing='future'."""
    for test_date in ("2026-05-23", "2026-06-01"):  # today and future
        row = _make_inst_row(date=test_date)
        result = _serialize_installment_row(row, _TODAY)
        assert result["timing"] == "future", f"Expected future for date={test_date}"


# ── Tests: assertion (التصحيح المفاهيمي) ─────────────────────────────────────

async def test_assertion_holds():
    """When late+future == all_due, no AssertionError is raised."""
    data = _standard_mock_data(
        total_due=800_000.0, late_due=600_000.0, future_due=200_000.0
    )
    mc = _make_client(data)
    with _patch_today():
        result = await get_customer_drilldown(
            partner_id=_PARTNER_ID, request_id="req-assert-ok", client=mc
        )
    assert result["data"]["exposure"]["total_due_egp"] == 800_000.0
    assert result["data"]["exposure"]["late_due_egp"]   == 600_000.0
    assert result["data"]["exposure"]["future_due_egp"] == 200_000.0


async def test_assertion_fires():
    """When late+future diverges from all_due by >= 1.0 EGP, AssertionError is raised."""
    data = _standard_mock_data(
        total_due=800_000.0,
        late_due=600_000.0,
        future_due=201_000.0,  # 601_000 != 800_000 → delta = 1_000 EGP
    )
    mc = _make_client(data)
    with _patch_today():
        with pytest.raises(AssertionError, match="Drill-down integrity"):
            await get_customer_drilldown(
                partner_id=_PARTNER_ID, request_id="req-assert-fail", client=mc
            )


# ── Test: payment ratio uses actual_paid NOT paid_amount (DR1 key test) ───────

async def test_payment_ratio_uses_actual_paid_not_paid_amount():
    """payment_ratio_pct must use x_studio_actual_paid_amount, not paid_amount.

    DR1 (M3-S6 discovery, 2026-05-23): the two fields diverge when a customer
    has pending cheques. paid_amount includes cheques collected but not yet banked;
    x_studio_actual_paid_amount = confirmed cash only. Using paid_amount would
    inflate the ratio and mislead the Board.

    This test constructs mock data where paid_amount != x_studio_actual_paid_amount,
    then asserts the ratio is computed from the cash-only figure.
    """
    total_amount       = 1_000_000.0
    actual_paid        = 200_000.0   # cash received
    paid_with_cheques  = 250_000.0   # includes 50k pending cheque

    expected_ratio = round(actual_paid / total_amount * 100, 2)   # 20.00%
    wrong_ratio    = round(paid_with_cheques / total_amount * 100, 2)  # 25.00%

    data = _standard_mock_data(
        total_amount=total_amount,
        actual_paid=actual_paid,
        paid_amount=paid_with_cheques,
        total_due=800_000.0,
        late_due=600_000.0,
        future_due=200_000.0,
    )
    mc = _make_client(data)
    with _patch_today():
        result = await get_customer_drilldown(
            partner_id=_PARTNER_ID, request_id="req-ratio", client=mc
        )

    ratio = result["data"]["behavior"]["payment_ratio_pct"]
    assert ratio == expected_ratio, (
        f"Expected payment_ratio={expected_ratio}% (cash only) "
        f"but got {ratio}%. If ratio == {wrong_ratio}, "
        f"the implementation is using paid_amount (incl. cheques) instead of "
        f"x_studio_actual_paid_amount."
    )


# ── Tests: pagination ─────────────────────────────────────────────────────────

async def test_pagination_has_next():
    """When Odoo returns page_size+1 rows, has_next=True and cursor_next is set."""
    page_size = 3
    rows = [_make_inst_row(record_id=i, date="2026-04-01") for i in range(page_size + 1)]
    data = _standard_mock_data(
        total_due=800_000.0, late_due=600_000.0, future_due=200_000.0,
        unpaid_count=10, inst_rows=rows,
    )
    mc = _make_client(data)
    with _patch_today():
        result = await get_customer_drilldown(
            partner_id=_PARTNER_ID, request_id="req-page1",
            page_size=page_size, client=mc,
        )

    inst = result["data"]["installments"]
    assert inst["has_next"] is True
    assert inst["cursor_next"] is not None
    assert len(inst["items"]) == page_size  # trimmed to page_size


async def test_pagination_exact_page():
    """When Odoo returns exactly page_size rows, has_next=False."""
    page_size = 3
    rows = [_make_inst_row(record_id=i, date="2026-04-01") for i in range(page_size)]
    data = _standard_mock_data(
        total_due=800_000.0, late_due=600_000.0, future_due=200_000.0,
        unpaid_count=3, inst_rows=rows,
    )
    mc = _make_client(data)
    with _patch_today():
        result = await get_customer_drilldown(
            partner_id=_PARTNER_ID, request_id="req-page-exact",
            page_size=page_size, client=mc,
        )

    inst = result["data"]["installments"]
    assert inst["has_next"] is False
    assert inst["cursor_next"] is None
    assert len(inst["items"]) == page_size


# ── Tests: cursor encode / decode ─────────────────────────────────────────────

def test_cursor_roundtrip():
    """Encoding then decoding a cursor payload preserves all values."""
    payload = {"sv": "2026-04-01", "id": 42, "sb": "date", "sd": "asc"}
    encoded = _encode_cursor(payload)
    decoded = _decode_cursor(encoded)
    assert decoded == payload


def test_cursor_decode_invalid_returns_empty():
    """A corrupt cursor string returns an empty dict (no exception)."""
    assert _decode_cursor("!!!not-base64!!!") == {}
    assert _decode_cursor("") == {}


# ── Tests: read-only enforcement ──────────────────────────────────────────────

async def test_read_only_assertion():
    """When is_read_only=False, the function raises AssertionError before any RPC."""
    client = MagicMock()
    client.is_read_only = False
    client.authenticate = AsyncMock()
    client.execute_kw   = AsyncMock()

    with pytest.raises(AssertionError):
        await get_customer_drilldown(
            partner_id=_PARTNER_ID, request_id="req-ro", client=client
        )
    client.execute_kw.assert_not_called()


# ── Tests: RPC failure ────────────────────────────────────────────────────────

async def test_rpc_failure():
    """A failure in any of the concurrent RPCs raises OdooQueryError."""
    client = MagicMock()
    client.is_read_only = True
    client.authenticate = AsyncMock()
    client.close        = AsyncMock()
    client.execute_kw   = AsyncMock(side_effect=RuntimeError("connection refused"))

    with _patch_today():
        with pytest.raises(OdooQueryError, match="Customer drill-down"):
            await get_customer_drilldown(
                partner_id=_PARTNER_ID, request_id="req-fail", client=client
            )


# ── Tests: response shape ─────────────────────────────────────────────────────

async def test_response_shape():
    """The response envelope contains all required top-level and nested keys."""
    data = _standard_mock_data(
        total_due=800_000.0, late_due=600_000.0, future_due=200_000.0
    )
    mc = _make_client(data)
    with _patch_today():
        result = await get_customer_drilldown(
            partner_id=_PARTNER_ID, request_id="req-shape", client=mc
        )

    assert result["version"] == "1.0"
    assert "data" in result and "meta" in result

    d = result["data"]
    assert set(d.keys()) == {"header", "exposure", "behavior", "installments"}

    assert {"partner_id", "customer_name"}.issubset(d["header"].keys())

    exposure_keys = {
        "total_due_egp", "late_due_egp", "future_due_egp",
        "paid_cash_egp", "total_original_egp",
        "total_installments", "unpaid_installment_count",
    }
    assert exposure_keys.issubset(d["exposure"].keys())

    behavior_keys = {"payment_ratio_pct", "wallet_balance_egp", "wallet_record_count"}
    assert behavior_keys.issubset(d["behavior"].keys())

    inst_keys = {"items", "total_count", "cursor_current", "cursor_next", "has_next"}
    assert inst_keys.issubset(d["installments"].keys())

    meta_keys = {"request_id", "as_of", "rpc_duration_ms", "today", "page_size",
                 "sort_by", "sort_dir"}
    assert meta_keys.issubset(result["meta"].keys())
    assert result["meta"]["request_id"] == "req-shape"


# ── Tests: edge cases ────────────────────────────────────────────────────────

async def test_wallet_zero_on_no_data():
    """When no wallet records exist for the customer, wallet_balance_egp=0.0."""
    data = _standard_mock_data(
        total_due=800_000.0, late_due=600_000.0, future_due=200_000.0,
        wallet_balance=0.0, wallet_count=0,
    )
    # Override wallet_rg to empty list
    data[3] = []
    mc = _make_client(data)
    with _patch_today():
        result = await get_customer_drilldown(
            partner_id=_PARTNER_ID, request_id="req-no-wallet", client=mc
        )

    assert result["data"]["behavior"]["wallet_balance_egp"] == 0.0
    assert result["data"]["behavior"]["wallet_record_count"] == 0


async def test_payment_ratio_zero_when_no_installments():
    """When total_original_egp=0 (no posted installments), ratio=0.0 (no division by zero)."""
    data = _standard_mock_data(
        total_amount=0.0, actual_paid=0.0, paid_amount=0.0,
        total_due=0.0, late_due=0.0, future_due=0.0,
        unpaid_count=0, inst_rows=[],
    )
    mc = _make_client(data)
    with _patch_today():
        result = await get_customer_drilldown(
            partner_id=_PARTNER_ID, request_id="req-zero", client=mc
        )

    assert result["data"]["behavior"]["payment_ratio_pct"] == 0.0


async def test_installment_row_fields():
    """Each serialized installment row has the correct field set and timing label."""
    late_inst  = _make_inst_row(record_id=10, date="2026-05-22")   # before TODAY
    future_inst = _make_inst_row(record_id=11, date="2026-06-01")  # after TODAY
    data = _standard_mock_data(
        total_due=200_000.0, late_due=100_000.0, future_due=100_000.0,
        unpaid_count=2, inst_rows=[late_inst, future_inst],
    )
    mc = _make_client(data)
    with _patch_today():
        result = await get_customer_drilldown(
            partner_id=_PARTNER_ID, request_id="req-rows", client=mc
        )

    items = result["data"]["installments"]["items"]
    assert len(items) == 2

    late_row   = items[0]
    future_row = items[1]

    assert late_row["timing"]   == "late"
    assert future_row["timing"] == "future"

    required_fields = {
        "record_id", "date", "installment_type_id",
        "installment_type_name_ar", "payment_state",
        "timing", "amount", "due_amount",
    }
    assert required_fields.issubset(set(late_row.keys()))
    assert required_fields.issubset(set(future_row.keys()))
