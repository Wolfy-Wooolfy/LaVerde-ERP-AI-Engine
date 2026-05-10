"""
Audit logging for sensitive data access.
Writes structured entries to logs/audit.log.
"""

import os
from datetime import datetime, timezone

from loguru import logger as _base_logger

os.makedirs("logs", exist_ok=True)

_audit_logger = _base_logger.bind(audit=True)
_audit_logger.add(
    "logs/audit.log",
    format="{message}",
    rotation="10 MB",
    retention="30 days",
    encoding="utf-8",
    filter=lambda record: record["extra"].get("audit", False),
)


def log_access(
    user: str,
    action: str,
    resource: str,
    ip: str = "unknown",
) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    _audit_logger.info(
        f"[AUDIT] user={user} action={action} resource={resource} timestamp={ts} ip={ip}"
    )
