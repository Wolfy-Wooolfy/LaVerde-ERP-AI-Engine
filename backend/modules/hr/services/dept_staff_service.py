"""
HR Department Staff drill-down service — F2.

Returns named employees for one named department, for the slide-over panel.
Employment definition: Running contract (state='open') — identical to KPI A.

1 RPC:
  search_read(hr.contract,
              [('state','=','open'), ('department_id','=',department_id)],
              fields=['employee_id','job_id','department_id','date_start','state'])
  No active_test flag — querying hr.contract with explicit state filter; consistent
  with all other HR KPI services.

Population identity guarantee (verified by scripts/verify_hr_f2_identity.py):
  The domain is a strict per-department subset of get_headcount()'s
  [('state','=','open')] query. Therefore:
    len(staff) == by_department[department_id]["count"]  from get_headcount().

Fields fetched per contract (NO wage, NO compensation field of any kind):
  employee_id   → [id, display_name] — employee name
  job_id        → [id, display_name] — job title ("—" if null)
  department_id → [id, display_name] — for dept_name extraction
  date_start    → ISO date — original hire date per §3.6
  state         → always "open" by filter

Tenure basis: (cairo_today − date_start).days / 365.25, labeled
"Service (hire date → today)" — NOT KPI B's net accumulated service, which sums
all contract periods. For the current single-contract population (2026-06-03
post-fix: all 115 Running employees have 1 contract) the values are numerically
identical. The label is what distinguishes them.

No caching — live RPC per call. This is a low-frequency board drill-down.
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
_CAIRO_TZ = ZoneInfo("Africa/Cairo")

_STAFF_FIELDS: list[str] = [
    "employee_id",
    "job_id",
    "department_id",
    "date_start",
    "state",
]

_FORBIDDEN_WRITE_METHODS: frozenset[str] = frozenset({"create", "write", "unlink"})
_NO_JOB_DISPLAY = "—"


def _assert_read_only() -> None:
    """Defense-in-depth: abort if any write method has leaked into ALLOWED_METHODS."""
    violations = ALLOWED_METHODS & _FORBIDDEN_WRITE_METHODS
    if violations:
        raise ReadOnlyViolationError(
            f"dept_staff_service: write method leaked into ALLOWED_METHODS: {violations}"
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


async def get_department_staff(
    department_id: int,
    client: Optional[OdooClient] = None,
) -> dict:
    """Return named employees for one named department — F2 drill-down.

    Returns a dict with keys:
      department_id, department_name, headcount, staff[], reference_date, as_of,
      rpc_duration_ms.

    The caller (endpoint) is responsible for merging department-level aggregate
    costs from get_department_cost() into the final API response.

    Raises:
        ValueError:             if department_id <= 0.
        OdooQueryError:         if the Odoo RPC fails.
        ReadOnlyViolationError: if ALLOWED_METHODS is contaminated.
    """
    _assert_read_only()

    if department_id <= 0:
        raise ValueError(
            f"department_id must be a positive integer, got {department_id!r}"
        )

    cairo_today = datetime.now(_CAIRO_TZ).date()

    _own_client = client is None
    _client = client or OdooClient()

    t0 = time.monotonic()
    try:
        await _client.authenticate()
        contracts: list[dict] = await _client.execute_kw(
            _CONTRACT_MODEL,
            "search_read",
            args=[[("state", "=", "open"), ("department_id", "=", department_id)]],
            kwargs={"fields": _STAFF_FIELDS},
        )
    except OdooQueryError:
        raise
    except Exception as exc:
        raise OdooQueryError(
            f"get_department_staff(department_id={department_id}) RPC on "
            f"{_CONTRACT_MODEL} failed: {exc}"
        ) from exc
    finally:
        if _own_client:
            await _client.close()

    rpc_ms = int((time.monotonic() - t0) * 1000)

    # ── Department name from first matching contract ───────────────────────────
    dept_name = ""
    for c in contracts:
        name = _parse_many2one_name(c.get("department_id"))
        if name:
            dept_name = name
            break

    # ── Serialize staff rows (NO wage / compensation field touched) ───────────
    staff: list[dict] = []
    for c in contracts:
        emp_raw = c.get("employee_id")
        if not isinstance(emp_raw, (list, tuple)) or not emp_raw:
            continue  # skip orphaned contracts (no employee_id linkage)

        emp_id = int(emp_raw[0])
        emp_name = str(emp_raw[1]) if len(emp_raw) > 1 else ""

        job_title = _parse_many2one_name(c.get("job_id")) or _NO_JOB_DISPLAY

        ds_raw = c.get("date_start") or None
        staff.append({
            "employee_id":    emp_id,
            "employee_name":  emp_name,
            "job_title":      job_title,
            "date_start":     str(ds_raw) if ds_raw else None,
            "tenure_years":   _compute_tenure(ds_raw, cairo_today),
            "contract_state": str(c.get("state") or "open"),
        })

    staff.sort(key=lambda r: r["employee_name"])

    logger.info(
        f"HR dept staff drill-down department_id={department_id}: "
        f"{len(staff)} employees in {rpc_ms}ms"
    )

    return {
        "department_id":   department_id,
        "department_name": dept_name,
        "headcount":       len(staff),
        "staff":           staff,
        "reference_date":  cairo_today.isoformat(),
        "as_of":           datetime.now(timezone.utc).isoformat(),
        "rpc_duration_ms": rpc_ms,
    }
