"""
HR KPI service — business logic for Module 5 KPIs.

Data source: hr.employee, hr.contract, hr.department, hr.job via the shared
read-only OdooClient. All methods are async. No method ever calls
create, write, or unlink.

M5-S1 scope: get_headcount() (KPI A).
"""

from backend.core.exceptions import ReadOnlyViolationError
from backend.shared.odoo.client import ALLOWED_METHODS

_FORBIDDEN_WRITE_METHODS = frozenset({"create", "write", "unlink"})


def _assert_read_only() -> None:
    """Defense-in-depth: abort if any write method has leaked into ALLOWED_METHODS."""
    violations = ALLOWED_METHODS & _FORBIDDEN_WRITE_METHODS
    if violations:
        raise ReadOnlyViolationError(
            f"ALLOWED_METHODS contains forbidden write method(s): {sorted(violations)}. "
            "The Odoo client is no longer strictly read-only. Halting before any RPC."
        )


async def get_headcount(client=None) -> dict:
    """Return KPI A — Headcount. Not yet implemented (M5-S1 D2)."""
    raise NotImplementedError("get_headcount() will be implemented in D2.")
