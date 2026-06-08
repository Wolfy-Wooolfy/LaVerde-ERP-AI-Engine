"""
HR Employee Profile drill-down service — F3.

Returns a lean, board-appropriate profile for one current employee
(an employee holding a Running contract, state='open').

2 RPCs:
  RPC 1: search_read(hr.contract,
                     [('employee_id','=',id), ('state','=','open')],
                     fields=['employee_id','department_id','job_id',
                             'date_start','date_end','state'])
         Contract-first: confirms current-employee status AND supplies
         date_start/date_end (hire date, contract end) AND department/job —
         matching F2's grouping key so the same employee shows the same
         department/job in both the F2 staff row and the F3 profile.
         Empty result → return None → caller returns 404.

  RPC 2: search_read(hr.employee,
                     [('id','=',id)],
                     fields=['name','parent_id','work_location_id'])
         Display name (trimmed), manager line (trimmed; null if absent),
         and work location.
         Empty result → return None → caller returns 404 (defensive).

Fields fetched from hr.contract (NO wage, NO compensation field):
  employee_id    → [id, display_name] — linkage confirmation
  department_id  → [id, display_name] — department name (F2-consistent)
  job_id         → [id, display_name] — job title ("—" if null, F2-consistent)
  date_start     → ISO date — hire date
  date_end       → ISO date or False — contract end (False = open-ended)
  state          → always "open" by filter

Fields fetched from hr.employee (NO wage, NO compensation):
  name            → display name (trimmed)
  parent_id       → [id, display_name] — manager name (trimmed; null if absent)
  work_location_id → [id, display_name] — work location name (null if absent)

Excluded (never fetched):
  wage / any compensation; gender; identification_id; barcode; employee_type;
  coach_id; vehicle; work_email; work_phone; mobile_phone; allowance_count;
  job_title freetext char (superseded by contract job_id);
  department_id on hr.employee (superseded by contract department_id).

Tenure formula (F2-identical):
  (cairo_today − date_start).days / 365.25 → rounded to 1 decimal; null if no date_start.

No caching — live RPC per call.
READ-ONLY: _assert_read_only() called on entry; no write method is invoked.
"""

import time
from datetime import date, datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from loguru import logger

from backend.core.exceptions import OdooQueryError, ReadOnlyViolationError
from backend.shared.odoo.client import ALLOWED_METHODS, OdooClient

_CONTRACT_MODEL = "hr.contract"
_EMPLOYEE_MODEL = "hr.employee"
_CAIRO_TZ       = ZoneInfo("Africa/Cairo")

_CONTRACT_FIELDS: list[str] = [
    "employee_id",
    "department_id",
    "job_id",
    "date_start",
    "date_end",
    "state",
]

_EMPLOYEE_FIELDS: list[str] = [
    "name",
    "parent_id",
    "work_location_id",
]

_FORBIDDEN_WRITE_METHODS: frozenset[str] = frozenset({"create", "write", "unlink"})
_NO_JOB_DISPLAY = "—"


def _assert_read_only() -> None:
    """Defense-in-depth: abort if any write method has leaked into ALLOWED_METHODS."""
    violations = ALLOWED_METHODS & _FORBIDDEN_WRITE_METHODS
    if violations:
        raise ReadOnlyViolationError(
            f"employee_profile_service: write method leaked into ALLOWED_METHODS: {violations}"
        )


def _parse_many2one_name(raw: object) -> str:
    """Extract display_name from a many2one field value ([id, name] or False)."""
    if isinstance(raw, (list, tuple)) and len(raw) > 1 and raw[1]:
        return str(raw[1])
    return ""


def _compute_tenure(date_start_raw: object, today: date) -> float | None:
    """Return (today - date_start).days / 365.25 rounded to 1 decimal, or None."""
    if not date_start_raw:
        return None
    try:
        ds = date.fromisoformat(str(date_start_raw))
        return round((today - ds).days / 365.25, 1)
    except (ValueError, TypeError):
        return None


async def get_employee_profile(
    employee_id: int,
    client: Optional[OdooClient] = None,
) -> dict | None:
    """Return a board-appropriate profile for one current employee — F3 drill-down.

    Returns a dict with keys:
      employee_id, name, job_title, department_name, manager_name,
      hire_date, tenure_years, contract_status, contract_end, is_open_ended,
      location, as_of, rpc_duration_ms.

    Returns None when:
      - No Running contract for this employee_id (not a current employee).
      - employee_id doesn't exist in hr.employee (defensive guard).

    Raises:
        ValueError:             if employee_id <= 0.
        OdooQueryError:         if any Odoo RPC fails.
        ReadOnlyViolationError: if ALLOWED_METHODS is contaminated.
    """
    _assert_read_only()

    if employee_id <= 0:
        raise ValueError(
            f"employee_id must be a positive integer, got {employee_id!r}"
        )

    cairo_today = datetime.now(_CAIRO_TZ).date()

    _own_client = client is None
    _client = client or OdooClient()

    t0 = time.monotonic()
    contract = None
    employee_rec = None
    try:
        await _client.authenticate()

        # ── RPC 1: open contract ──────────────────────────────────────────────
        contracts: list[dict] = await _client.execute_kw(
            _CONTRACT_MODEL,
            "search_read",
            args=[[("employee_id", "=", employee_id), ("state", "=", "open")]],
            kwargs={"fields": _CONTRACT_FIELDS},
        )
        if not contracts:
            return None

        contract = contracts[0]

        # ── RPC 2: employee display fields ────────────────────────────────────
        employees: list[dict] = await _client.execute_kw(
            _EMPLOYEE_MODEL,
            "search_read",
            args=[[("id", "=", employee_id)]],
            kwargs={"fields": _EMPLOYEE_FIELDS},
        )
        if not employees:
            return None

        employee_rec = employees[0]

    except OdooQueryError:
        raise
    except Exception as exc:
        raise OdooQueryError(
            f"get_employee_profile(employee_id={employee_id}) RPC failed: {exc}"
        ) from exc
    finally:
        if _own_client:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)

    # ── Contract-sourced fields ───────────────────────────────────────────────
    dept_name  = _parse_many2one_name(contract.get("department_id")) or None
    job_raw    = _parse_many2one_name(contract.get("job_id"))
    job_title  = job_raw if job_raw else _NO_JOB_DISPLAY
    date_start = contract.get("date_start") or None
    date_end   = contract.get("date_end")       # False = open-ended
    is_open_ended = not date_end                # True when date_end is False/null

    # ── Employee-sourced fields ───────────────────────────────────────────────
    name = str(employee_rec.get("name") or "").strip()

    manager_name_raw = _parse_many2one_name(employee_rec.get("parent_id"))
    manager_name = manager_name_raw.strip() if manager_name_raw else None

    location = _parse_many2one_name(employee_rec.get("work_location_id")) or None

    logger.info(
        f"HR employee profile drill-down employee_id={employee_id} in {rpc_ms}ms"
    )

    return {
        "employee_id":     employee_id,
        "name":            name,
        "job_title":       job_title,
        "department_name": dept_name,
        "manager_name":    manager_name,
        "hire_date":       str(date_start) if date_start else None,
        "tenure_years":    _compute_tenure(date_start, cairo_today),
        "contract_status": "Running",
        "contract_end":    str(date_end) if date_end else None,
        "is_open_ended":   is_open_ended,
        "location":        location,
        "as_of":           datetime.now(timezone.utc).isoformat(),
        "rpc_duration_ms": rpc_ms,
    }
