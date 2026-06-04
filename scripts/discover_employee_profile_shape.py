"""
Read-only employee profile + wage + insurance + asset shape discovery.

Answers 5 areas for the HR Board frontend design:

  AREA 1 — WAGE: complete comp_monetary field list on hr.contract; wage
           population across 115 Running contracts; magnitude band
           distribution (k-anon: count < 3 → suppressed); dept coverage.

  AREA 2 — PROMOTIONS / JOB-CHANGE HISTORY: job_id current-only vs history
           model; hr.resume.line existence + coverage; verdict YES/NO.

  AREA 3 — EMPLOYEE PROFILE FIELDS: populated counts per candidate field
           on hr.employee for the 115 Running-contract employees; PII vs
           work-context classification; display-name coverage.

  AREA 4 — INSURANCE: direct insurance fields on hr.employee (field-level)
           + any one2many/many2many insurance sub-model relations; medical
           individual-vs-family option distribution; social insurance
           population counts.

  AREA 5 — ASSETS: asset/equipment relation on hr.employee OR direct probe
           of maintenance.equipment (employee_id lives on that model,
           invisible to fields_get(hr.employee)); N/115 employees with
           assets; category labels from schema only (FIX 2: never free-text
           char/serial values).

Schema approach: fields_get on hr.contract and hr.employee returns the
complete field inventory, classified into buckets:
  {comp_monetary, work_context, insurance, asset, pii, other}
Full dump is printed — nothing pre-excluded.  Unknown sensitive-looking
fields default to pii (fail-safe).  Integer fields on hr.contract are
flagged as potentially hours/days rather than monetary.

Privacy invariant — enforced structurally, not by convention:
  ZERO individual record values are printed anywhere in this script.
  Output is counts, band-counts (k-anon >=3), schema labels, or boolean
  aggregates only.  No names, wages, emails, phones, IDs, or serials.

RPCs (5 always + up to 4 conditional = 9 max):
  RPC 1   fields_get(hr.contract, [string,type,relation,selection])
  RPC 2   fields_get(hr.employee, [string,type,relation,selection])
  RPC 3   search_count(hr.resume.line, [])
  RPC 4   search_read(hr.contract, [state=open], all non-relation fields,
            active_test=False)
  RPC 5   search_read(hr.employee, [id in running_emp_ids], all simple +
            many2one fields, active_test=False)
  RPC 6   search_read(hr.resume.line, ...) — if RPC 3 > 0
  RPC 7A  fields_get(<insurance_sub_model>) — if insurance relation found
  RPC 7B  search_read(<insurance_sub_model>, ...) — if RPC 7A succeeds
  RPC 8A  fields_get(<asset_model>) — relation on hr.employee OR
            maintenance.equipment fallback
  RPC 8B  search_read(<asset_model>, ...) — if RPC 8A succeeds

Hard structural check: len(running_emp_ids) == 115 → [PASS]/[FAIL].

Pre-flight (Decision 6.4): kill python, purge __pycache__. No uvicorn.
Usage:
    python scripts/discover_employee_profile_shape.py
"""

import asyncio
import io
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# sys.path.insert so script runs without PYTHONPATH set (settled convention)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.shared.odoo.client import OdooClient  # noqa: E402

# Force UTF-8 stdout (Windows cp1252 default)
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Constants ──────────────────────────────────────────────────────────────────

CAIRO_TZ  = ZoneInfo("Africa/Cairo")
_LOG_FILE = "logs/employee_profile_shape_discovery.log"
_SEP      = "═" * 72
_SEP2     = "─" * 72
_KANO_MIN = 3    # k-anonymity: band counts < this → suppressed

_PASS = "[PASS]"
_FAIL = "[FAIL]"
_INFO = "[INFO]"

_EXPECTED_RUNNING = 115   # KPI A/B/C baseline 2026-06-03

_MAINTENANCE_EQUIPMENT_MODEL = "maintenance.equipment"

# Field types excluded from RPC 4 / RPC 5 field lists (too large or unsafe)
_SKIP_TYPES = frozenset({"one2many", "many2many", "binary", "html"})

# ── Classification keyword sets ────────────────────────────────────────────────

_PII_FIELD_NAMES = frozenset({
    "identification_id", "passport_id", "ssnid", "birthday", "gender",
    "marital", "private_email", "private_phone", "emergency_contact",
    "emergency_phone", "bank_account_id", "pin", "address_home_id",
    "km_home_work", "permit_no", "visa_no", "visa_expire", "permit_expire",
    "place_of_birth", "country_of_birth", "spouse_complete_name",
    "spouse_birthdate", "children", "study_field", "study_school",
    "certificate", "permit_type",
})

_PII_LABEL_KW = frozenset({
    "private", "personal", "passport", "national id", "birth", "gender",
    "marital", "religion", "emergency", "iban", "pin", "home address",
    "permit", "visa", "نسبة", "جنس", "ميلاد", "خاص", "شخصي",
})

# Insurance: field-level keywords (for direct fields on hr.employee)
_INSURANCE_FIELD_KW = frozenset({
    "insurance", "medical", "health", "social", "coverage",
    "تأمين", "طبى", "طبي", "اجتماعى", "اجتماعي", "insur", "medic",
})

# Insurance: relation-level keywords (for one2many/many2many sub-models) — FIX 1
_INSURANCE_RELATION_KW = frozenset({
    "insurance", "medical", "health", "dependent", "beneficiary",
    "family", "coverage", "تأمين", "طبى", "طبي", "مرافق", "اعالة",
})

# Asset: relation-level keywords
_ASSET_RELATION_KW = frozenset({
    "asset", "equipment", "device", "laptop", "vehicle", "hardware", "tool",
})

# Asset: field-level keywords (for direct non-relation fields)
_ASSET_FIELD_KW = frozenset({
    "asset", "equipment", "device", "laptop", "vehicle", "hardware",
})

# Compensation keywords (for label-matching on hr.employee numeric fields)
_COMP_LABEL_KW = frozenset({
    "wage", "salary", "allowance", "bonus", "gross", "net", "pay",
    "compensation", "benefit", "incentive", "commission", "overtime",
    "مرتب", "أجر", "بدل", "حافز", "أساسي", "إضافي", "عمولة", "مكافأة",
    "راتب",
})

# Work-context field name substrings
_WORK_FIELD_KW = frozenset({
    "job_", "department", "parent_id", "coach_id", "work_", "location",
    "title", "employee_type", "contract", "company_id", "resource",
    "first_contract", "manager",
})

# Category/type field keywords for asset + insurance sub-models
_CATEGORY_KW = frozenset({
    "category", "categ", "type", "kind", "product", "class", "نوع", "فئة",
})

# Free-text / serial fields to EXCLUDE from category detection (FIX 2)
_SERIAL_FREE_TEXT_KW = frozenset({
    "serial", "name", "description", "note", "code", "reference",
    "barcode", "lot", "partner", "sequence",
})

# Phrases that indicate "model does not exist" in Odoo RPC errors (FIX 3)
_MODEL_NOT_FOUND_PHRASES = frozenset({
    "doesn't exist", "does not exist", "object not found",
    "no such model", "invalid model", "model not found",
    "object_name", "object name",
})

# Bucket display order
_BUCKET_ORDER = ["comp_monetary", "work_context", "insurance", "asset", "pii", "other"]

# Wage band thresholds (EGP monthly interpretation)
_WAGE_BANDS = [
    (0,       "zero_or_negative"),
    (5_000,   "1–5k EGP"),
    (15_000,  "5k–15k EGP"),
    (30_000,  "15k–30k EGP"),
    (50_000,  "30k–50k EGP"),
    (100_000, "50k–100k EGP"),
    (float("inf"), "> 100k EGP"),
]

# Board profile candidate fields
_PROFILE_CANDIDATES: list[tuple[str, str, str]] = [
    # (field_name, description, pii_category)
    ("name",               "Display name",               "work-context"),
    ("parent_id",          "Manager",                    "work-context"),
    ("coach_id",           "Coach / mentor",             "work-context"),
    ("job_id",             "Job position",               "work-context"),
    ("job_title",          "Job title (freetext)",       "work-context"),
    ("department_id",      "Department",                 "work-context"),
    ("work_email",         "Work email",                 "work-context"),
    ("work_phone",         "Work phone",                 "work-context"),
    ("mobile_phone",       "Mobile phone",               "borderline"),
    ("identification_id",  "Employee / National ID",     "PII"),
    ("barcode",            "Badge / barcode",            "PII"),
    ("first_contract_date","First contract date",        "work-context"),
    ("work_location_id",   "Work location",              "work-context"),
    ("work_location_name", "Work location (name)",       "work-context"),
    ("employee_type",      "Employee type",              "work-context"),
]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _info(msg: str) -> None:
    print(f"{_INFO} {msg}", flush=True)


def _pass(msg: str) -> None:
    print(f"{_PASS} {msg}", flush=True)


def _fail(msg: str) -> None:
    print(f"{_FAIL} {msg}", flush=True)


def _check(label: str, condition: bool, detail: str = "") -> bool:
    if condition:
        _pass(label)
    else:
        _fail(f"{label}{(' — ' + detail) if detail else ''}")
    return condition


def _section(title: str) -> None:
    print(f"\n{_SEP}", flush=True)
    print(title, flush=True)
    print(_SEP2, flush=True)


def _populated(value: object) -> bool:
    """True if value is meaningfully set (not Odoo null/empty)."""
    if value is False or value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    if isinstance(value, (list, tuple)) and len(value) == 0:
        return False
    return True


def _emp_id(raw: object) -> int | None:
    """Extract integer employee ID from Odoo many2one ([id, name] or scalar)."""
    if isinstance(raw, (list, tuple)) and raw:
        return int(raw[0])
    if raw and raw is not False:
        try:
            return int(raw)
        except (ValueError, TypeError):
            return None
    return None


def _is_model_not_found(exc: Exception) -> bool:
    """True iff the exception clearly indicates a missing Odoo model (FIX 3)."""
    msg = str(exc).lower()
    return any(phrase in msg for phrase in _MODEL_NOT_FOUND_PHRASES)


def _wage_band(v: float) -> str:
    if v <= 0:
        return "zero_or_negative"
    prev = 0
    for threshold, label in _WAGE_BANDS:
        if v < threshold:
            return label
        prev = threshold
    return "> 100k EGP"


def _print_band_distribution(band_counter: Counter, total: int, indent: str = "    ") -> None:
    """Print band distribution with k-anonymity suppression."""
    band_order = [lbl for _, lbl in _WAGE_BANDS]
    for band in band_order:
        cnt = band_counter.get(band, 0)
        if cnt == 0:
            continue
        display = f"< {_KANO_MIN} (suppressed)" if 0 < cnt < _KANO_MIN else str(cnt)
        _info(f"{indent}{band:<22}  {display}")
    _info(f"{indent}{'total classified':<22}  {total}")


# ── Classification ─────────────────────────────────────────────────────────────

def _classify(name: str, meta: dict, model: str) -> str:
    """
    Classify a field into one of six buckets. Precedence:
      pii → insurance → asset → comp_monetary → work_context → other
    Unknown sensitive-looking fields default to pii (fail-safe).
    """
    ftype = meta.get("type", "")
    label = (meta.get("string") or "").lower()
    rel   = (meta.get("relation") or "").lower()
    nlo   = name.lower()

    # 1 ── PII (fail-safe: checked first)
    if name in _PII_FIELD_NAMES:
        return "pii"
    if any(kw in label for kw in _PII_LABEL_KW):
        return "pii"
    if ftype in ("char", "text") and any(
        kw in nlo for kw in {"passport", "id_no", "id_num", "national", "ssn", "permit_"}
    ):
        return "pii"

    # 2 ── Insurance
    if ftype not in ("one2many", "many2many"):
        if any(kw in nlo or kw in label for kw in _INSURANCE_FIELD_KW):
            return "insurance"
    if ftype in ("one2many", "many2many"):
        if any(kw in rel or kw in nlo for kw in _INSURANCE_RELATION_KW):
            return "insurance"

    # 3 ── Asset
    if ftype in ("one2many", "many2many"):
        if any(kw in rel or kw in nlo for kw in _ASSET_RELATION_KW):
            return "asset"
    elif any(kw in nlo for kw in _ASSET_FIELD_KW):
        return "asset"

    # 4 ── Compensation / monetary
    is_numeric = ftype in ("monetary", "float", "integer")
    if model == "hr.contract" and is_numeric:
        return "comp_monetary"
    if is_numeric and any(kw in nlo or kw in label for kw in _COMP_LABEL_KW):
        return "comp_monetary"

    # 5 ── Work context
    if any(kw in nlo for kw in _WORK_FIELD_KW):
        return "work_context"

    # 6 ── Other
    return "other"


def _is_insurance_relation(name: str, meta: dict) -> bool:
    """True if this field is a one2many/many2many pointing to an insurance sub-model."""
    if meta.get("type") not in ("one2many", "many2many"):
        return False
    rel = (meta.get("relation") or "").lower()
    nlo = name.lower()
    return any(kw in rel or kw in nlo for kw in _INSURANCE_RELATION_KW)


def _is_asset_relation(name: str, meta: dict) -> bool:
    """True if this field is a one2many/many2many pointing to an asset/equipment model."""
    if meta.get("type") not in ("one2many", "many2many"):
        return False
    rel = (meta.get("relation") or "").lower()
    nlo = name.lower()
    return any(kw in rel or kw in nlo for kw in _ASSET_RELATION_KW)


def _find_emp_link_field(fields_meta: dict) -> str:
    """Find the field on a sub-model that links back to hr.employee."""
    for fname, meta in fields_meta.items():
        if meta.get("type") == "many2one" and "employee" in (meta.get("relation") or "").lower():
            return fname
    return "employee_id"   # reasonable Odoo default


def _find_category_field(fields_meta: dict) -> str | None:
    """
    Find a many2one category/type field on a sub-model.
    NEVER returns a free-text char/serial field (FIX 2).
    Returns None if no suitable many2one category field is found.
    """
    for fname, meta in sorted(fields_meta.items()):
        if meta.get("type") != "many2one":
            continue
        nlo = fname.lower()
        lbl = (meta.get("string") or "").lower()
        # Must match a category keyword
        if not any(kw in nlo or kw in lbl for kw in _CATEGORY_KW):
            continue
        # Must NOT look like a serial/name/free-text identifier
        if any(kw in nlo for kw in _SERIAL_FREE_TEXT_KW):
            continue
        return fname
    return None


# ── Schema dump ────────────────────────────────────────────────────────────────

def _print_schema_dump(model: str, fields_meta: dict, note_integer: bool = False) -> dict[str, list]:
    """
    Print the complete classified field inventory for the given model.
    Returns classified dict: bucket → [(name, meta)] sorted by name.
    """
    classified: dict[str, list] = {b: [] for b in _BUCKET_ORDER}
    for fname, meta in fields_meta.items():
        bucket = _classify(fname, meta, model)
        classified[bucket].append((fname, meta))
    for bucket in _BUCKET_ORDER:
        classified[bucket].sort(key=lambda x: x[0])

    total = sum(len(v) for v in classified.values())
    _info(f"  Model: {model}  |  total fields: {total}")
    _info("")

    W_NAME  = 36
    W_LABEL = 30
    W_TYPE  = 16
    W_REL   = 28
    W_BKT   = 12

    for bucket in _BUCKET_ORDER:
        rows = classified[bucket]
        if not rows:
            continue
        _info(f"  ── {bucket} ({len(rows)} fields) ──────────────")
        _info(
            f"  {'name':<{W_NAME}} {'label':<{W_LABEL}} "
            f"{'type':<{W_TYPE}} {'relation':<{W_REL}} bucket"
        )
        _info("  " + "─" * (W_NAME + W_LABEL + W_TYPE + W_REL + W_BKT + 4))
        for fname, meta in rows:
            ftype = meta.get("type", "?")
            lbl   = (meta.get("string") or "")[:W_LABEL - 1]
            rel   = (meta.get("relation") or "")[:W_REL - 1]
            note  = " ⚠int" if (note_integer and ftype == "integer") else ""
            _info(
                f"  {fname:<{W_NAME}} {lbl:<{W_LABEL}} "
                f"{(ftype + note):<{W_TYPE}} {rel:<{W_REL}} {bucket}"
            )
        _info("")

    return classified


# ── Sub-model probe helper ─────────────────────────────────────────────────────

async def _probe_submodel(
    client: OdooClient,
    model_name: str,
    running_emp_ids: list[int],
    rpc_label_a: str,
    rpc_label_b: str,
) -> dict | None:
    """
    Fetch fields_get + records for a sub-model linked to the 115 employees.
    Returns a result dict, or None if the model is not found (FIX 3).
    Real errors (auth, connection, unexpected) are re-raised as [FAIL].
    """
    _info(f"{rpc_label_a}: fields_get({model_name})")
    try:
        fields_meta: dict = await client.execute_kw(
            model_name,
            "fields_get",
            args=[],
            kwargs={"attributes": ["string", "type", "relation", "selection"]},
        )
    except Exception as exc:
        if _is_model_not_found(exc):
            _info(f"  {model_name}: NOT FOUND — model not installed")
            return None
        _fail(f"{rpc_label_a} failed [{model_name}]: {exc}")
        raise

    _info(f"  → {len(fields_meta)} fields")

    emp_link  = _find_emp_link_field(fields_meta)
    cat_field = _find_category_field(fields_meta)   # may be None (FIX 2)
    _info(f"  employee link field : '{emp_link}'")
    _info(f"  category/type field : '{cat_field or 'NONE — category not reportable'}'")

    read_fields = [emp_link]
    if cat_field:
        read_fields.append(cat_field)

    _info(f"{rpc_label_b}: search_read({model_name}, [('{emp_link}', in, 115 IDs)])")
    records: list[dict] = await client.execute_kw(
        model_name,
        "search_read",
        args=[[( emp_link, "in", running_emp_ids)]],
        kwargs={"fields": read_fields},
    )
    _info(f"  → {len(records)} records for the 115 employees")

    distinct_emp_ids = {
        _emp_id(r.get(emp_link))
        for r in records
        if _emp_id(r.get(emp_link)) is not None
    }

    # Category labels (FIX 2: only from many2one — never free-text values)
    cat_labels: set[str] = set()
    if cat_field:
        for r in records:
            raw = r.get(cat_field)
            if isinstance(raw, (list, tuple)) and len(raw) >= 2:
                cat_labels.add(str(raw[1]))
            # scalar many2one IDs: no label available without extra RPC — skip

    return {
        "model":             model_name,
        "fields_meta":       fields_meta,
        "emp_link":          emp_link,
        "cat_field":         cat_field,
        "total_records":     len(records),
        "n_employees":       len(distinct_emp_ids),
        "cat_labels":        cat_labels,
    }


# ── Main ───────────────────────────────────────────────────────────────────────

async def run() -> None:
    run_at      = datetime.now(timezone.utc).isoformat()
    cairo_today = datetime.now(CAIRO_TZ).date()

    print(_SEP)
    print("Employee Profile Shape Discovery — HR Frontend Design")
    print(f"Run timestamp  : {run_at}")
    print(f"Cairo today    : {cairo_today}")
    print(f"RPCs planned   : 5 always + up to 4 conditional = 9 max")
    print(_SEP)
    _info("SCOPE: READ-ONLY. fields_get / search_read / search_count ONLY.")
    _info("PRIVACY: Zero individual record values printed — counts/bands/schema only.")
    _info(f"BASELINE: Running contracts expected = {_EXPECTED_RUNNING} (post Dev-fix 2026-06-03).")
    _info("NOTE: integer fields on hr.contract flagged ⚠int — may be hours/days, not monetary.")

    # ══════════════════════════════════════════════════════════════════════════
    # PHASE 1 — SCHEMA + RESUME COUNT (RPCs 1-3)
    # ══════════════════════════════════════════════════════════════════════════

    _section("PHASE 1 — SCHEMA RPCs (1–3)")

    async with OdooClient() as client:

        _info("RPC 1: fields_get(hr.contract, [string,type,relation,selection])")
        contract_fields: dict = await client.execute_kw(
            "hr.contract",
            "fields_get",
            args=[],
            kwargs={"attributes": ["string", "type", "relation", "selection"]},
        )
        _info(f"       → {len(contract_fields)} field definitions")

        _info("RPC 2: fields_get(hr.employee, [string,type,relation,selection])")
        employee_fields: dict = await client.execute_kw(
            "hr.employee",
            "fields_get",
            args=[],
            kwargs={"attributes": ["string", "type", "relation", "selection"]},
        )
        _info(f"       → {len(employee_fields)} field definitions")

        _info("RPC 3: search_count(hr.resume.line, [])")
        resume_total: int = await client.execute_kw(
            "hr.resume.line",
            "search_count",
            args=[[]],
        )
        _info(f"       → {resume_total} total resume lines")

        # ── Identify conditional relation targets ─────────────────────────────

        # Insurance relations on hr.employee (FIX 1: mirror Asset pattern)
        insurance_relations: dict[str, dict] = {
            fname: meta
            for fname, meta in employee_fields.items()
            if _is_insurance_relation(fname, meta)
        }

        # Asset relations on hr.employee
        asset_relations: dict[str, dict] = {
            fname: meta
            for fname, meta in employee_fields.items()
            if _is_asset_relation(fname, meta)
        }

        _info("")
        _info(f"Insurance sub-model relations on hr.employee: {len(insurance_relations)}")
        for fn, mt in insurance_relations.items():
            _info(f"  '{fn}' ({mt.get('string','')}) → {mt.get('relation','?')}")
        if not insurance_relations:
            _info("  (none — insurance likely field-level only; no conditional probe needed)")

        _info(f"Asset relations on hr.employee: {len(asset_relations)}")
        for fn, mt in asset_relations.items():
            _info(f"  '{fn}' ({mt.get('string','')}) → {mt.get('relation','?')}")
        if not asset_relations:
            _info("  (none — will attempt maintenance.equipment fallback)")

        # ── Build RPC 4 / RPC 5 field lists ───────────────────────────────────

        # RPC 4: all non-skip fields on hr.contract (always include employee_id,
        # department_id even if somehow absent from fields_get result)
        rpc4_must_have = {"employee_id", "department_id"}
        rpc4_fields: list[str] = sorted(
            rpc4_must_have | {
                fname for fname, meta in contract_fields.items()
                if meta.get("type") not in _SKIP_TYPES
            }
        )

        # RPC 5: all non-skip fields on hr.employee (simple types + many2one)
        rpc5_fields: list[str] = sorted(
            fname for fname, meta in employee_fields.items()
            if meta.get("type") not in _SKIP_TYPES
        )

        _info("")
        _info(f"RPC 4 field list: {len(rpc4_fields)} fields on hr.contract")
        _info(f"RPC 5 field list: {len(rpc5_fields)} fields on hr.employee")

        # ══════════════════════════════════════════════════════════════════════
        # PHASE 2 — DATA RPCs (4–5)
        # ══════════════════════════════════════════════════════════════════════

        _section("PHASE 2 — DATA RPCs (4–5)")

        _info("RPC 4: search_read(hr.contract, [('state','=','open')],")
        _info("       all non-relation fields, context={'active_test': False})")
        _info("       active_test=False required: 13 Running contracts on archived")
        _info("       employees would be silently dropped without it (§3.6 Issue 1)")
        running_contracts: list[dict] = await client.execute_kw(
            "hr.contract",
            "search_read",
            args=[[("state", "=", "open")]],
            kwargs={
                "fields": rpc4_fields,
                "context": {"active_test": False},
            },
        )
        _info(f"       → {len(running_contracts)} Running contracts")

        # Deduplicated running employee IDs (same order as discovery)
        seen_eids: set[int] = set()
        running_emp_ids: list[int] = []
        for c in running_contracts:
            eid = _emp_id(c.get("employee_id"))
            if eid is not None and eid not in seen_eids:
                running_emp_ids.append(eid)
                seen_eids.add(eid)
        _info(f"       → {len(running_emp_ids)} distinct Running-contract employee IDs")

        _info("RPC 5: search_read(hr.employee, [('id','in', running_emp_ids)],")
        _info("       all non-relation + many2one fields, context={'active_test': False})")
        employee_records: list[dict] = await client.execute_kw(
            "hr.employee",
            "search_read",
            args=[[("id", "in", running_emp_ids)]],
            kwargs={
                "fields": rpc5_fields,
                "context": {"active_test": False},
            },
        )
        _info(f"       → {len(employee_records)} employee records")

        # ══════════════════════════════════════════════════════════════════════
        # PHASE 3 — CONDITIONAL RPCs (6, 7A/B, 8A/B)
        # ══════════════════════════════════════════════════════════════════════

        _section("PHASE 3 — CONDITIONAL RPCs")

        # ── RPC 6: Resume lines ───────────────────────────────────────────────

        resume_lines: list[dict] = []
        if resume_total > 0:
            _info(f"RPC 6: search_read(hr.resume.line, [employee_id in running_emp_ids],")
            _info(f"       fields=[employee_id, line_type_id])  ({resume_total} total lines exist)")
            resume_lines = await client.execute_kw(
                "hr.resume.line",
                "search_read",
                args=[[("employee_id", "in", running_emp_ids)]],
                kwargs={"fields": ["employee_id", "line_type_id"]},
            )
            _info(f"       → {len(resume_lines)} lines for the {len(running_emp_ids)} employees")
        else:
            _info("RPC 6: SKIPPED — hr.resume.line count = 0")

        # ── RPCs 7A/7B: Insurance sub-models ─────────────────────────────────

        insurance_submodel_results: list[dict] = []
        if insurance_relations:
            for irel_name, irel_meta in insurance_relations.items():
                imodel = irel_meta.get("relation", "")
                if not imodel:
                    continue
                try:
                    result = await _probe_submodel(
                        client, imodel, running_emp_ids,
                        "RPC 7A", "RPC 7B",
                    )
                    if result is not None:
                        result["relation_field"] = irel_name
                        insurance_submodel_results.append(result)
                except Exception as exc:
                    _fail(f"Insurance sub-model probe [{imodel}] unexpected error: {exc}")
        else:
            _info("RPC 7A/7B: SKIPPED — no insurance sub-model relations on hr.employee")

        # ── RPCs 8A/8B: Asset model ───────────────────────────────────────────

        asset_result: dict | None = None

        if asset_relations:
            ar_name, ar_meta = next(iter(asset_relations.items()))
            amodel = ar_meta.get("relation", "")
            _info(f"Asset relation from hr.employee: '{ar_name}' → {amodel}")
        else:
            amodel = _MAINTENANCE_EQUIPMENT_MODEL
            _info(f"No asset relation on hr.employee — probing {amodel} directly")
            _info("  (employee_id lives on maintenance.equipment, invisible to")
            _info("   fields_get(hr.employee) — direct probe required as fallback)")

        if amodel:
            try:
                asset_result = await _probe_submodel(
                    client, amodel, running_emp_ids,
                    "RPC 8A", "RPC 8B",
                )
            except Exception as exc:
                _fail(f"Asset model probe [{amodel}] unexpected error: {exc}")

    # ─────────────────────────────────────────────────────────────────────────
    # All RPCs complete. Remaining work is pure Python analysis + output.
    # ─────────────────────────────────────────────────────────────────────────

    n_running = len(running_emp_ids)

    # ══════════════════════════════════════════════════════════════════════════
    # HARD STRUCTURAL CHECK
    # ══════════════════════════════════════════════════════════════════════════

    _section("HARD STRUCTURAL CHECK")

    hard_check_pass = _check(
        f"Running-contract employee count == {_EXPECTED_RUNNING}",
        n_running == _EXPECTED_RUNNING,
        f"actual={n_running}",
    )
    _check(
        f"Employee records returned == {n_running}",
        len(employee_records) == n_running,
        f"expected={n_running}, got={len(employee_records)}",
    )
    _info(f"Running contracts (RPC 4)          : {len(running_contracts)}")
    _info(f"Distinct Running-contract emp IDs  : {n_running}")
    _info(f"Employee records (RPC 5)           : {len(employee_records)}")

    # ══════════════════════════════════════════════════════════════════════════
    # SCHEMA INVENTORY — hr.contract
    # ══════════════════════════════════════════════════════════════════════════

    _section("SCHEMA INVENTORY — hr.contract (complete field dump)")
    _info("⚠ integer fields flagged '⚠int' — may represent hours/days, not monetary.")
    _info("  Verify field label to confirm meaning before treating as compensation.")
    _info("")
    contract_classified = _print_schema_dump("hr.contract", contract_fields, note_integer=True)

    # ══════════════════════════════════════════════════════════════════════════
    # SCHEMA INVENTORY — hr.employee
    # ══════════════════════════════════════════════════════════════════════════

    _section("SCHEMA INVENTORY — hr.employee (complete field dump)")
    _info("")
    employee_classified = _print_schema_dump("hr.employee", employee_fields, note_integer=False)

    # ══════════════════════════════════════════════════════════════════════════
    # AREA 1 — WAGE
    # ══════════════════════════════════════════════════════════════════════════

    _section("AREA 1 — WAGE (hr.contract comp_monetary fields)")

    comp_fields: list[tuple[str, dict]] = contract_classified.get("comp_monetary", [])

    _info(f"comp_monetary fields on hr.contract: {len(comp_fields)}")
    _info("")
    _info(f"  Complete list (names + labels — BEFORE keyword-subset):")
    _info(f"  {'name':<35} {'label':<32} {'type'}")
    _info(f"  {'─'*35} {'─'*32} {'─'*12}")
    for fname, meta in comp_fields:
        ftype = meta.get("type", "?")
        lbl   = meta.get("string", "")
        note  = " ⚠ may be hours/days" if ftype == "integer" else ""
        _info(f"  {fname:<35} {lbl:<32} {ftype}{note}")

    _info("")
    wage_meta  = contract_fields.get("wage", {})
    wage_label = wage_meta.get("string", "NOT FOUND")
    _info(f"wage field Odoo label: '{wage_label}'")
    _info("  If label reads 'Monthly Wage' or 'Wage' → monthly EGP (standard Odoo).")
    _info("  If label reads 'Annual Salary' → annual. Magnitude bands below cross-check.")

    _info("")
    _info(f"Population counts across {n_running} Running contracts:")
    for fname, meta in comp_fields:
        pop = sum(1 for c in running_contracts if _populated(c.get(fname)))
        _info(f"  {fname:<35}: populated={pop}/{n_running}")

    _info("")
    _info("Wage magnitude band distribution (k-anon: count < 3 → suppressed):")
    wage_band_counter: Counter = Counter()
    if "wage" in {f for f, _ in comp_fields}:
        for c in running_contracts:
            v = c.get("wage")
            if v is not False and v is not None:
                try:
                    wage_band_counter[_wage_band(float(v))] += 1
                except (ValueError, TypeError):
                    pass
        _print_band_distribution(wage_band_counter, n_running)
        _info("  Interpretation: if dominant band is 5k–50k EGP → consistent with")
        _info("  monthly EGP salaries. If > 100k → consistent with annual or executive.")
    else:
        _info("  'wage' not found in comp_monetary — check schema above for actual field name")

    _info("")
    _info("Department-level wage coverage:")
    dept_all: set[int]          = set()
    dept_wage_populated: set[int] = set()
    for c in running_contracts:
        dept_id = _emp_id(c.get("department_id"))
        if dept_id is not None:
            dept_all.add(dept_id)
        wage_val = c.get("wage")
        if dept_id is not None and wage_val is not False and wage_val is not None:
            try:
                if float(wage_val) > 0:
                    dept_wage_populated.add(dept_id)
            except (ValueError, TypeError):
                pass
    _info(f"  Departments with ≥1 Running contract     : {len(dept_all)}")
    _info(f"  Departments with ≥1 populated wage (>0)  : {len(dept_wage_populated)}")
    feasible = len(dept_wage_populated) == len(dept_all)
    _info(f"  SUM(wage) GROUP BY department_id feasible: {'YES' if feasible else 'PARTIAL — some depts have zero/null wage'}")

    # ══════════════════════════════════════════════════════════════════════════
    # AREA 2 — PROMOTIONS / JOB-CHANGE HISTORY
    # ══════════════════════════════════════════════════════════════════════════

    _section("AREA 2 — PROMOTIONS / JOB-CHANGE HISTORY")

    # job_id on hr.employee
    emp_job_meta  = employee_fields.get("job_id", {})
    emp_job_type  = emp_job_meta.get("type", "NOT FOUND")
    emp_job_rel   = emp_job_meta.get("relation", "")
    _info(f"hr.employee.job_id: type={emp_job_type}, relation={emp_job_rel}")
    if emp_job_type == "many2one":
        _info("  many2one = CURRENT job only. No built-in change history on this field.")
    else:
        _info(f"  Unexpected type '{emp_job_type}' — investigate further")

    # job_id on hr.contract
    ctr_job_meta = contract_fields.get("job_id", {})
    ctr_job_type = ctr_job_meta.get("type", "NOT FOUND")
    _info(f"hr.contract.job_id: type={ctr_job_type}")
    _info("  Contract job_id reflects job at contract creation. Useful only for")
    _info("  multi-contract (returning) employees. Current data: 0 returning (§3.6).")

    # Dedicated job-history relation fields on hr.employee
    job_history_rels = {
        fname: meta
        for fname, meta in employee_fields.items()
        if meta.get("type") in ("one2many", "many2many")
        and any(
            kw in (meta.get("relation") or "").lower() or kw in fname.lower()
            for kw in {"job_history", "position_history", "job_log", "career", "promotion"}
        )
    }
    _info("")
    if job_history_rels:
        _info("Job-history / career relation fields on hr.employee:")
        for fn, mt in job_history_rels.items():
            _info(f"  '{fn}' → {mt.get('relation','?')} ({mt.get('string','')})")
    else:
        _info("No job-history / career relation found on hr.employee.")

    # Resume lines
    _info("")
    _info(f"hr.resume.line total count (all employees): {resume_total}")
    if resume_total == 0:
        _info("  FINDING: hr.resume.line is empty — confirms §3.7 D4 (unreliable/unpopulated).")
        _info("  Not a usable promotion source.")
        resume_verdict = "EMPTY"
    else:
        n_emps_resume = len({
            _emp_id(r.get("employee_id"))
            for r in resume_lines
            if _emp_id(r.get("employee_id")) is not None
        })
        _info(f"  Lines for the {n_running} Running-contract employees: {len(resume_lines)}")
        _info(f"  Employees with ≥1 resume line: {n_emps_resume}/{n_running}")
        type_ctr: Counter = Counter()
        for r in resume_lines:
            raw = r.get("line_type_id")
            if isinstance(raw, (list, tuple)) and len(raw) >= 2:
                type_ctr[str(raw[1])] += 1
            elif raw:
                type_ctr[str(raw)] += 1
            else:
                type_ctr["(no type)"] += 1
        _info("  Line type distribution (schema labels — not PII):")
        for ltype, cnt in type_ctr.most_common():
            _info(f"    {ltype}: {cnt}")
        resume_verdict = "DATA_EXISTS"

    # Verdict
    _info("")
    if job_history_rels:
        _info("PROMOTION HISTORY VERDICT: YES — dedicated relation found (see above).")
    elif resume_verdict == "DATA_EXISTS":
        _info("PROMOTION HISTORY VERDICT: PARTIAL — resume lines exist but §3.7 D4")
        _info("  flags them as manually maintained and unreliable.")
        _info("  Frontend recommendation: omit or show 'not reliably tracked'.")
    else:
        _info("PROMOTION HISTORY VERDICT: NO")
        _info("  job_id on hr.employee is current-only (many2one).")
        _info("  hr.resume.line is empty.")
        _info("  No dedicated job-history relation on hr.employee.")
        _info("  Frontend: drop 'promotions' section or label 'not tracked in Odoo'.")

    # ══════════════════════════════════════════════════════════════════════════
    # AREA 3 — EMPLOYEE PROFILE FIELDS
    # ══════════════════════════════════════════════════════════════════════════

    _section("AREA 3 — EMPLOYEE PROFILE FIELDS (populated counts, 115 employees)")

    W_F, W_D, W_P, W_C = 24, 28, 12, 12
    _info(f"  {'field':<{W_F}} {'description':<{W_D}} {'populated':>{W_P}} {'category':<{W_C}}")
    _info(f"  {'─'*W_F} {'─'*W_D} {'─'*W_P} {'─'*W_C}")

    for fname, desc, cat in _PROFILE_CANDIDATES:
        if fname not in employee_fields:
            _info(f"  {fname:<{W_F}} {desc:<{W_D}} {'NOT IN SCHEMA':>{W_P}} {cat:<{W_C}}")
            continue
        pop = sum(1 for e in employee_records if _populated(e.get(fname)))
        _info(f"  {fname:<{W_F}} {desc:<{W_D}} {f'{pop}/{n_running}':>{W_P}} {cat:<{W_C}}")

    _info("")
    name_pop = sum(1 for e in employee_records if _populated(e.get("name")))
    _check(
        f"Display name ('name') populated for all {n_running} employees",
        name_pop == n_running,
        f"populated={name_pop}/{n_running}",
    )

    _info("")
    _info("Fields in profile candidate list absent from hr.employee schema:")
    missing = [fn for fn, _, _ in _PROFILE_CANDIDATES if fn not in employee_fields]
    if missing:
        for fn in missing:
            _info(f"  {fn} — NOT IN SCHEMA (verify field name for this Odoo version)")
    else:
        _info("  None — all candidates present in schema.")

    # ══════════════════════════════════════════════════════════════════════════
    # AREA 4 — INSURANCE
    # ══════════════════════════════════════════════════════════════════════════

    _section("AREA 4 — INSURANCE (medical + social)")

    # Direct (field-level) insurance fields on hr.employee
    insurance_direct: list[tuple[str, dict]] = employee_classified.get("insurance", [])
    # Exclude relation fields (those are handled by sub-model probe below)
    insurance_direct_scalar = [
        (fn, mt) for fn, mt in insurance_direct
        if mt.get("type") not in ("one2many", "many2many")
    ]

    _info(f"Direct insurance fields on hr.employee (non-relation): {len(insurance_direct_scalar)}")
    if not insurance_direct_scalar:
        _info("  None found matching insurance keywords.")
    else:
        _info("")
        for fname, meta in insurance_direct_scalar:
            ftype     = meta.get("type", "?")
            lbl_str   = meta.get("string", "")
            pop       = sum(1 for e in employee_records if _populated(e.get(fname)))

            if ftype in ("char", "text"):
                _info(f"  {fname:<30} '{lbl_str}'  type={ftype}  [PII — count only]")
                _info(f"    populated: {pop}/{n_running}")

            elif ftype == "selection":
                options = meta.get("selection") or []
                opt_labels = [lbl for _, lbl in options]
                _info(f"  {fname:<30} '{lbl_str}'  type=selection")
                _info(f"    options (schema): {opt_labels}")
                _info(f"    populated: {pop}/{n_running}")
                if options:
                    opt_ctr: Counter = Counter(e.get(fname) for e in employee_records)
                    for opt_key, opt_lbl in options:
                        cnt     = opt_ctr.get(opt_key, 0)
                        display = f"< {_KANO_MIN} (suppressed)" if 0 < cnt < _KANO_MIN else str(cnt)
                        _info(f"    '{opt_lbl}': {display}/{n_running}")
                    unset = sum(
                        1 for e in employee_records
                        if e.get(fname) in (False, None, "")
                    )
                    if unset:
                        _info(f"    not set / False: {unset}/{n_running}")

            elif ftype == "boolean":
                true_cnt  = sum(1 for e in employee_records if e.get(fname) is True)
                false_cnt = n_running - true_cnt
                _info(f"  {fname:<30} '{lbl_str}'  type=boolean")
                _info(f"    True={true_cnt}/{n_running}  False/unset={false_cnt}/{n_running}")

            elif ftype in ("float", "monetary", "integer"):
                bc: Counter = Counter()
                for e in employee_records:
                    v = e.get(fname)
                    if v is not False and v is not None:
                        try:
                            bc[_wage_band(float(v))] += 1
                        except (ValueError, TypeError):
                            pass
                _info(f"  {fname:<30} '{lbl_str}'  type={ftype}")
                _info(f"    populated: {pop}/{n_running}")
                _print_band_distribution(bc, n_running, indent="    ")

            else:
                _info(f"  {fname:<30} '{lbl_str}'  type={ftype}  populated: {pop}/{n_running}")

    _info("")
    if insurance_submodel_results:
        _info(f"Insurance sub-model relations: {len(insurance_submodel_results)}")
        for res in insurance_submodel_results:
            _info(f"  Relation field on hr.employee: '{res['relation_field']}'")
            _info(f"  Sub-model: {res['model']}")
            _info(f"  Employees with ≥1 record: {res['n_employees']}/{n_running}")
            _info(f"  Total records: {res['total_records']}")
            if res["cat_field"] and res["cat_labels"]:
                _info(f"  Category/type labels ('{res['cat_field']}' schema):")
                for lbl in sorted(res["cat_labels"]):
                    _info(f"    {lbl}")
            elif res["cat_field"]:
                _info(f"  Category field '{res['cat_field']}' found but no labels in result set")
            else:
                _info("  No many2one category/type field found on sub-model (FIX 2 applied)")
            _info(f"  Sub-model schema ({len(res['fields_meta'])} fields):")
            _print_schema_dump(res["model"], res["fields_meta"], note_integer=False)
    elif insurance_relations:
        _info("Insurance sub-model probes ran but returned no results — see [FAIL] above.")
    else:
        _info("No insurance sub-model relation on hr.employee — insurance is field-level only.")

    # ══════════════════════════════════════════════════════════════════════════
    # AREA 5 — ASSETS
    # ══════════════════════════════════════════════════════════════════════════

    _section("AREA 5 — ASSETS (mobile/laptop assigned to employee)")

    if asset_relations:
        _info(f"Asset relations found on hr.employee ({len(asset_relations)}):")
        for fn, mt in asset_relations.items():
            _info(f"  '{fn}' ({mt.get('string','')}) → {mt.get('relation','?')}")
    else:
        _info("No asset relation on hr.employee → used maintenance.equipment fallback.")

    if asset_result is None:
        _info(f"ASSET VERDICT: No asset tracking model found.")
        _info(f"  {_MAINTENANCE_EQUIPMENT_MODEL}: NOT INSTALLED")
        _info("  Assets not trackable via standard Odoo equipment module.")
    else:
        _info(f"Asset model: {asset_result['model']}")
        _info(f"Employee link field: '{asset_result['emp_link']}'")
        _info(f"Employees with ≥1 asset record: {asset_result['n_employees']}/{n_running}")
        _info(f"Total asset records for {n_running} employees: {asset_result['total_records']}")

        if asset_result["cat_field"] and asset_result["cat_labels"]:
            _info(f"Distinct asset categories ('{asset_result['cat_field']}' schema labels):")
            for lbl in sorted(asset_result["cat_labels"]):
                _info(f"  {lbl}")
        elif asset_result["cat_field"]:
            _info(f"Category field '{asset_result['cat_field']}' present but no labels in result set")
        else:
            _info("No many2one category/type field found on asset model (FIX 2 applied).")
            _info("  Field inventory below — identify category field manually from schema.")

        _info(f"\nAsset model schema ({len(asset_result['fields_meta'])} fields):")
        _print_schema_dump(asset_result["model"], asset_result["fields_meta"], note_integer=False)

    # ══════════════════════════════════════════════════════════════════════════
    # FINAL SUMMARY
    # ══════════════════════════════════════════════════════════════════════════

    wage_pop: int = -1
    if "wage" in contract_fields:
        try:
            wage_pop = sum(
                1 for c in running_contracts
                if _populated(c.get("wage")) and float(c.get("wage") or 0) > 0
            )
        except (ValueError, TypeError):
            wage_pop = -1

    print(f"\n{_SEP}")
    print("DISCOVERY SUMMARY")
    print(_SEP2)
    print(f"  Run at (UTC)                              : {run_at}")
    print(f"  Cairo today                               : {cairo_today}")
    print(_SEP2)
    hc_str = f"PASS ({n_running}=={_EXPECTED_RUNNING})" if hard_check_pass else f"FAIL (got {n_running})"
    print(f"  Hard structural check (n_running==115)    : {hc_str}")
    print(f"  Running contracts (RPC 4)                 : {len(running_contracts)}")
    print(f"  Employee records (RPC 5)                  : {len(employee_records)}")
    print(_SEP2)
    print(f"  AREA 1 — WAGE")
    print(f"    comp_monetary fields on hr.contract     : {len(comp_fields)}")
    print(f"    wage Odoo label                         : '{wage_label}'")
    print(f"    wage > 0 populated                      : {wage_pop}/{n_running}")
    print(f"    departments — total / wage-populated    : {len(dept_all)} / {len(dept_wage_populated)}")
    print(_SEP2)
    print(f"  AREA 2 — PROMOTIONS")
    print(f"    hr.resume.line total                    : {resume_total}")
    print(f"    job-history relations on hr.employee    : {len(job_history_rels)}")
    print(f"    verdict                                 : {'YES' if job_history_rels else ('PARTIAL' if resume_verdict == 'DATA_EXISTS' else 'NO')}")
    print(_SEP2)
    print(f"  AREA 3 — PROFILE FIELDS")
    print(f"    display name ('name') populated         : {name_pop}/{n_running}")
    print(f"    candidate fields absent from schema     : {len(missing)}")
    print(_SEP2)
    print(f"  AREA 4 — INSURANCE")
    print(f"    direct insurance fields                 : {len(insurance_direct_scalar)}")
    print(f"    insurance sub-model relations           : {len(insurance_relations)}")
    print(f"    sub-models probed successfully          : {len(insurance_submodel_results)}")
    print(_SEP2)
    print(f"  AREA 5 — ASSETS")
    if asset_result is None:
        print(f"    asset model                             : NOT FOUND")
        print(f"    employees with assets                   : N/A")
    else:
        print(f"    asset model                             : {asset_result['model']}")
        print(f"    employees with ≥1 asset                 : {asset_result['n_employees']}/{n_running}")
        print(f"    total asset records                     : {asset_result['total_records']}")
    print(_SEP)

    # ── TSV log ────────────────────────────────────────────────────────────────

    os.makedirs("logs", exist_ok=True)
    log_exists = os.path.isfile(_LOG_FILE)
    with open(_LOG_FILE, "a", encoding="utf-8") as f:
        if not log_exists:
            f.write(
                "run_at\tcairo_today\t"
                "hard_check_pass\trunning_contracts\tn_running\temp_records\t"
                "contract_total_fields\tcomp_monetary_fields\twage_label\twage_pop\t"
                "dept_total\tdept_wage_populated\t"
                "resume_total\tjob_history_rels\tpromotion_verdict\t"
                "insurance_direct_fields\tinsurance_sub_relations\tinsurance_subs_probed\t"
                "asset_model\tasset_emp_count\tasset_total_records\n"
            )
        f.write(
            f"{run_at}\t{cairo_today}\t"
            f"{'PASS' if hard_check_pass else 'FAIL'}\t{len(running_contracts)}\t"
            f"{n_running}\t{len(employee_records)}\t"
            f"{len(contract_fields)}\t{len(comp_fields)}\t{wage_label}\t{wage_pop}\t"
            f"{len(dept_all)}\t{len(dept_wage_populated)}\t"
            f"{resume_total}\t{len(job_history_rels)}\t"
            f"{'YES' if job_history_rels else ('PARTIAL' if resume_verdict == 'DATA_EXISTS' else 'NO')}\t"
            f"{len(insurance_direct_scalar)}\t{len(insurance_relations)}\t{len(insurance_submodel_results)}\t"
            f"{asset_result['model'] if asset_result else 'NONE'}\t"
            f"{asset_result['n_employees'] if asset_result else 0}\t"
            f"{asset_result['total_records'] if asset_result else 0}\n"
        )

    _info(f"TSV row appended to {_LOG_FILE}")


if __name__ == "__main__":
    asyncio.run(run())
