"""
Installment-type ID → official Arabic display name mapping.

Source: Gate 1 discovery (2026-05-22) — 13 records in rs.installment.type,
reviewed against live Odoo by Khaled. All 13 IDs are covered.
No raw Odoo name reaches any API response (Choice 2ج).

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

# Sentinel used when an installment_type_id is not in the mapping.
# Should never appear in Board-facing output — callers must assert this.
_UNKNOWN_TYPE_AR = "نوع غير معروف"


def get_type_name_ar(type_id: int) -> str:
    """Return the reviewed Arabic name for the given installment type ID.

    Falls back to _UNKNOWN_TYPE_AR if the ID is not in the mapping.
    Callers that feed Board-facing output must assert the result is not
    _UNKNOWN_TYPE_AR (or equivalently, that type_id is in INSTALLMENT_TYPE_NAMES_AR).
    """
    return INSTALLMENT_TYPE_NAMES_AR.get(type_id, _UNKNOWN_TYPE_AR)
