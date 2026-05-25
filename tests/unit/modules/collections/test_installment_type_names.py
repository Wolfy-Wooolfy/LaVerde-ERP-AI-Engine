"""
Unit tests for installment_type_names.py — Arabic (AR) mapping.

English (EN) mapping tests live in test_kpi_service.py (added in D-1).
These tests cover the AR side that was never collected from the legacy
path (backend/modules/collections/tests/test_stage7.py) — migrated in D-6.
"""
from backend.modules.collections.installment_type_names import (
    INSTALLMENT_TYPE_NAMES_AR,
    _UNKNOWN_TYPE_AR,
    get_type_name_ar,
)


def test_ar_mapping_has_all_13_ids() -> None:
    assert set(INSTALLMENT_TYPE_NAMES_AR.keys()) == set(range(1, 14))


def test_ar_mapping_every_id_has_non_empty_name() -> None:
    for tid, name in INSTALLMENT_TYPE_NAMES_AR.items():
        assert isinstance(name, str) and name.strip(), (
            f"ID {tid} has an empty or non-string Arabic name"
        )


def test_ar_mapping_matches_gate1_spec() -> None:
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


def test_get_type_name_ar_known_ids() -> None:
    assert get_type_name_ar(3) == "قسط دوري"
    assert get_type_name_ar(2) == "المقدمة"
    assert get_type_name_ar(8) == "الغرامات"


def test_get_type_name_ar_unknown_id_returns_sentinel() -> None:
    assert get_type_name_ar(99) == _UNKNOWN_TYPE_AR


def test_ar_sentinel_not_equal_to_any_real_name() -> None:
    for name in INSTALLMENT_TYPE_NAMES_AR.values():
        assert name != _UNKNOWN_TYPE_AR
