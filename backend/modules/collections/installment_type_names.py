"""
Installment-type ID → official display name mappings (AR + EN).

Source: Gate 1 discovery (2026-05-22) — 13 records in rs.installment.type,
reviewed against live Odoo by Khaled. All 13 IDs are covered.
No raw Odoo name reaches any API response (Choice 2ج).

EN names added D-1 (2026-05-25) — official names confirmed from Stage 7
Gate 1 discovery, keyed to same IDs as the AR mapping.

Live finding: only 8 IDs have actual installment records in Odoo.
IDs 5, 9, 10, 11, 12 are defined but have zero associated rs.installment rows.
The read_group groupby will naturally exclude them; an explicit assertion in
kpi_service.py guards against any entry with record_count == 0 appearing.
"""

INSTALLMENT_TYPE_NAMES_AR: dict[int, str] = {
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

INSTALLMENT_TYPE_NAMES_EN: dict[int, str] = {
    1:  "Reservation",
    2:  "Down Payment",
    3:  "Regular",
    4:  "Maintenance",
    5:  "Pool",
    6:  "Club",
    7:  "Garage",
    8:  "Penalty",
    9:  "Modification",
    10: "Service",
    11: "Other Service",
    12: "Termination",
    13: "Administrative Fees",
}

# Installment-type ids with ZERO rs.installment records as of the 2026-05-22
# Gate-1 discovery (see module docstring). Defined in Odoo but unused in the data.
ZERO_RECORD_TYPE_IDS: frozenset[int] = frozenset({5, 9, 10, 11, 12})

# Single source of truth for "types to offer" in read-only filter UIs: every
# defined type id that actually occurs in the data, sorted. Derived from the
# AR mapping minus the zero-record set so it stays in sync with the dicts.
# Current value: [1, 2, 3, 4, 6, 7, 8, 13] (8 populated ids).
POPULATED_TYPE_IDS: list[int] = [
    tid for tid in sorted(INSTALLMENT_TYPE_NAMES_AR) if tid not in ZERO_RECORD_TYPE_IDS
]


# Sentinels used when an installment_type_id is not in the mapping.
# Should never appear in Board-facing output — callers must assert this.
_UNKNOWN_TYPE_AR = "نوع غير معروف"
_UNKNOWN_TYPE_EN = "Unknown Type"


def get_type_name_ar(type_id: int) -> str:
    """Return the reviewed Arabic name for the given installment type ID.

    Falls back to _UNKNOWN_TYPE_AR if the ID is not in the mapping.
    Callers that feed Board-facing output must assert the result is not
    _UNKNOWN_TYPE_AR (or equivalently, that type_id is in INSTALLMENT_TYPE_NAMES_AR).
    """
    return INSTALLMENT_TYPE_NAMES_AR.get(type_id, _UNKNOWN_TYPE_AR)


def get_type_name_en(type_id: int) -> str:
    """Return the reviewed English name for the given installment type ID.

    Falls back to _UNKNOWN_TYPE_EN if the ID is not in the mapping.
    """
    return INSTALLMENT_TYPE_NAMES_EN.get(type_id, _UNKNOWN_TYPE_EN)
