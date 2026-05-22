"""
Unit tests for Stage 7 deliverables:
  - installment_type_names.py: all 13 IDs resolve correctly
  - drilldown_service._serialize_row: populates installment_type_id + name_ar
  - kpi_service._fetch_bucket_type_breakdown:
      * identity-equal assertion passes
      * sort order is amount-descending
      * zero-count entries are excluded
      * unknown type_id raises ValueError

No live Odoo connection; OdooClient is fully mocked.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from backend.modules.collections.installment_type_names import (
    INSTALLMENT_TYPE_NAMES_AR,
    get_type_name_ar,
    _UNKNOWN_TYPE_AR,
)
from backend.modules.collections.services.drilldown_service import _serialize_row
from backend.modules.collections.services.kpi_service import (
    _fetch_bucket_type_breakdown,
)


# ── installment_type_names ────────────────────────────────────────────────────

class TestInstallmentTypeNames:
    def test_all_13_ids_present(self):
        assert set(INSTALLMENT_TYPE_NAMES_AR.keys()) == set(range(1, 14))

    def test_every_id_has_non_empty_arabic_name(self):
        for tid, name in INSTALLMENT_TYPE_NAMES_AR.items():
            assert isinstance(name, str) and name.strip(), (
                f"ID {tid} has an empty or non-string Arabic name"
            )

    def test_known_ids_match_gate1_mapping(self):
        expected = {
            1:  "حجز",
            2:  "المقدمة",
            3:  "قسط دوري",
            4:  "وديعة الصيانة",
            5:  "حمام سباحة",
            6:  "النادي",
            7:  "الجراج",
            8:  "الغرامات",
            9:  "تعديلات",
            10: "خدمة",
            11: "خدمات أخرى",
            12: "فسخ",
            13: "مصاريف إدارية",
        }
        assert INSTALLMENT_TYPE_NAMES_AR == expected

    def test_get_type_name_ar_known_id(self):
        assert get_type_name_ar(3) == "قسط دوري"
        assert get_type_name_ar(2) == "المقدمة"
        assert get_type_name_ar(8) == "الغرامات"

    def test_get_type_name_ar_unknown_id_returns_sentinel(self):
        result = get_type_name_ar(99)
        assert result == _UNKNOWN_TYPE_AR

    def test_sentinel_not_equal_to_any_real_name(self):
        for name in INSTALLMENT_TYPE_NAMES_AR.values():
            assert name != _UNKNOWN_TYPE_AR


# ── _serialize_row ────────────────────────────────────────────────────────────

class TestSerializeRow:
    def _base_rec(self, **overrides):
        rec = {
            "id": 1001,
            "date": "2026-06-15",
            "amount": 50000.0,
            "due_amount": 50000.0,
            "paid_amount": 0.0,
            "x_studio_actual_paid_amount": 0.0,
            "payment_state": "unpaid",
            "partner_id": [42, "Test Customer"],
            "project_id": [1, "New Capital"],
            "installment_type_id": [3, "Regular"],
        }
        rec.update(overrides)
        return rec

    def test_type_id_populated_from_many2one_list(self):
        row = _serialize_row(self._base_rec(installment_type_id=[3, "Regular"]))
        assert row["installment_type_id"] == 3

    def test_type_id_populated_from_int(self):
        row = _serialize_row(self._base_rec(installment_type_id=7))
        assert row["installment_type_id"] == 7

    def test_type_id_zero_when_missing(self):
        row = _serialize_row(self._base_rec(installment_type_id=False))
        assert row["installment_type_id"] == 0

    def test_type_name_ar_resolved_from_mapping(self):
        row = _serialize_row(self._base_rec(installment_type_id=[3, "Regular"]))
        assert row["installment_type_name_ar"] == "قسط دوري"

    def test_type_name_ar_for_down_payment(self):
        row = _serialize_row(self._base_rec(installment_type_id=[2, "Down Payment"]))
        assert row["installment_type_name_ar"] == "المقدمة"

    def test_type_name_ar_for_penalty(self):
        row = _serialize_row(self._base_rec(installment_type_id=[8, "Penalty"]))
        assert row["installment_type_name_ar"] == "الغرامات"

    def test_existing_fields_unchanged(self):
        row = _serialize_row(self._base_rec())
        assert row["record_id"] == 1001
        assert row["amount"] == 50000.0
        assert row["payment_state"] == "unpaid"
        assert row["late_amount"] == 50000.0  # amount - actual_paid

    def test_all_installmentrow_fields_present(self):
        row = _serialize_row(self._base_rec())
        required = {
            "record_id", "customer_name", "project_id", "project_name_ar",
            "project_name_en", "installment_type_id", "installment_type_name_ar",
            "date", "amount", "due_amount", "paid_amount", "actual_paid_amount",
            "pending_cheque", "payment_state", "late_amount",
        }
        assert required.issubset(row.keys())


# ── _fetch_bucket_type_breakdown ──────────────────────────────────────────────

def _make_client(rg_rows):
    client = MagicMock()
    client.execute_kw = AsyncMock(return_value=rg_rows)
    return client


@pytest.mark.asyncio
class TestFetchBucketTypeBreakdown:
    async def test_sort_order_is_amount_descending(self):
        rows = [
            {"installment_type_id": [3, "Regular"],  "amount": 1000.0, "__count": 5},
            {"installment_type_id": [7, "Garage"],   "amount": 5000.0, "__count": 2},
            {"installment_type_id": [6, "Club"],     "amount": 2000.0, "__count": 1},
        ]
        client = _make_client(rows)
        result = await _fetch_bucket_type_breakdown(client, "2026-05-22", "2026-05-31", 8000.0)
        amounts = [e["amount"] for e in result]
        assert amounts == sorted(amounts, reverse=True)

    async def test_identity_equal_passes_when_sums_match(self):
        rows = [
            {"installment_type_id": [3, "Regular"], "amount": 6000.0, "__count": 3},
            {"installment_type_id": [2, "Down Payment"], "amount": 2000.0, "__count": 1},
        ]
        client = _make_client(rows)
        result = await _fetch_bucket_type_breakdown(client, "2026-05-22", "2026-05-31", 8000.0)
        assert abs(sum(e["amount"] for e in result) - 8000.0) < 0.01

    async def test_identity_equal_assertion_fails_on_mismatch(self):
        rows = [
            {"installment_type_id": [3, "Regular"], "amount": 5000.0, "__count": 3},
        ]
        client = _make_client(rows)
        with pytest.raises(AssertionError, match="type_breakdown sum"):
            await _fetch_bucket_type_breakdown(client, "2026-05-22", "2026-05-31", 9999.0)

    async def test_zero_count_entries_excluded(self):
        rows = [
            {"installment_type_id": [3, "Regular"], "amount": 8000.0, "__count": 5},
            {"installment_type_id": [6, "Club"],    "amount": 0.0,    "__count": 0},
        ]
        client = _make_client(rows)
        result = await _fetch_bucket_type_breakdown(client, "2026-05-22", "2026-05-31", 8000.0)
        assert all(e["record_count"] > 0 for e in result)
        assert len(result) == 1

    async def test_unknown_type_id_raises_value_error(self):
        rows = [
            {"installment_type_id": [999, "Mystery Type"], "amount": 8000.0, "__count": 3},
        ]
        client = _make_client(rows)
        with pytest.raises(ValueError, match="installment_type_id=999"):
            await _fetch_bucket_type_breakdown(client, "2026-05-22", "2026-05-31", 8000.0)

    async def test_arabic_names_come_from_mapping(self):
        rows = [
            {"installment_type_id": [3, "Regular"],      "amount": 5000.0, "__count": 3},
            {"installment_type_id": [8, "Penalty"],      "amount": 2000.0, "__count": 1},
            {"installment_type_id": [13, "Admin Fees"],  "amount": 1000.0, "__count": 1},
        ]
        client = _make_client(rows)
        result = await _fetch_bucket_type_breakdown(client, "2026-05-22", "2026-05-31", 8000.0)
        names = {e["installment_type_id"]: e["installment_type_name_ar"] for e in result}
        assert names[3]  == "قسط دوري"
        assert names[8]  == "الغرامات"
        assert names[13] == "مصاريف إدارية"

    async def test_empty_bucket_returns_empty_list(self):
        client = _make_client([])
        result = await _fetch_bucket_type_breakdown(client, "2026-05-22", "2026-05-31", 0.0)
        assert result == []

    async def test_type_id_from_plain_int(self):
        rows = [
            {"installment_type_id": 3, "amount": 1000.0, "__count": 2},
        ]
        client = _make_client(rows)
        result = await _fetch_bucket_type_breakdown(client, "2026-05-22", "2026-05-31", 1000.0)
        assert result[0]["installment_type_id"] == 3
