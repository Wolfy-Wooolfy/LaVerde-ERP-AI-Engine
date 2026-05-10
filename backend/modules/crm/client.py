"""
Read-only Odoo JSON-RPC client.
Uses httpx for connection pooling and tenacity for retry on transient failures.
Write operations are blocked at the method-validation layer.
"""

import time
import uuid
from typing import Any, Optional

import httpx
from loguru import logger
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from backend.core.config import settings
from backend.core.exceptions import (
    OdooAuthenticationError,
    OdooConnectionError,
    OdooQueryError,
    ReadOnlyViolationError,
)

# Only these Odoo ORM methods are permitted — no write operations ever.
ALLOWED_METHODS: frozenset[str] = frozenset(
    {
        "search_read",
        "read_group",
        "search_count",
        "search",
        "read",
        "fields_get",
        "name_search",
        "name_get",
    }
)


def _ensure_read_only(method: str) -> None:
    if method not in ALLOWED_METHODS:
        raise ReadOnlyViolationError(
            f"Method '{method}' is not allowed. "
            f"This client is read-only by design. "
            f"Allowed: {sorted(ALLOWED_METHODS)}"
        )


class OdooClient:
    """Synchronous JSON-RPC client for Odoo with read-only enforcement."""

    def __init__(self) -> None:
        self._url = settings.ODOO_URL.rstrip("/") + "/jsonrpc"
        self._db = settings.ODOO_DB
        self._username = settings.ODOO_USERNAME
        self._api_key = settings.ODOO_API_KEY
        self._uid: Optional[int] = None
        self._http = httpx.Client(
            timeout=settings.ODOO_TIMEOUT_SECONDS,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )

    # ── Context manager ───────────────────────────────────────────────────────

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "OdooClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    # ── Internal RPC call (with retry) ───────────────────────────────────────

    def _call(self, service: str, method: str, args: list) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": {"service": service, "method": method, "args": args},
            "id": str(uuid.uuid4()),
        }

        start = time.monotonic()
        for attempt in Retrying(
            stop=stop_after_attempt(settings.ODOO_MAX_RETRIES),
            wait=wait_exponential(multiplier=1, min=1, max=4),
            retry=retry_if_exception_type((httpx.NetworkError, httpx.TimeoutException)),
            reraise=True,
        ):
            with attempt:
                try:
                    response = self._http.post(self._url, json=payload)
                    response.raise_for_status()
                except httpx.NetworkError as exc:
                    logger.warning(f"Odoo network error (will retry): {exc}")
                    raise
                except httpx.TimeoutException as exc:
                    logger.warning(f"Odoo timeout (will retry): {exc}")
                    raise
                except httpx.HTTPStatusError as exc:
                    raise OdooConnectionError(f"HTTP {exc.response.status_code} from Odoo") from exc

        duration_ms = int((time.monotonic() - start) * 1000)
        logger.debug(f"Odoo RPC {service}.{method} completed in {duration_ms}ms")

        data: dict = response.json()
        if "error" in data:
            error_detail = data["error"]
            msg = str(error_detail)
            if "Access denied" in msg or "authentication" in msg.lower():
                raise OdooAuthenticationError(f"Odoo auth error: {error_detail}")
            raise OdooQueryError(f"Odoo query error: {error_detail}")

        return data.get("result")

    # ── Public API ────────────────────────────────────────────────────────────

    def authenticate(self) -> int:
        """Authenticate once and cache the UID for the lifetime of this client."""
        if self._uid:
            return self._uid

        uid: Any = self._call(
            "common",
            "authenticate",
            [self._db, self._username, self._api_key, {}],
        )

        if not uid:
            raise OdooAuthenticationError(
                "Odoo authentication failed — check ODOO_USERNAME and ODOO_API_KEY"
            )

        self._uid = int(uid)
        logger.info(f"Authenticated with Odoo (uid={self._uid})")
        return self._uid

    def execute_kw(
        self,
        model: str,
        method: str,
        args: Optional[list] = None,
        kwargs: Optional[dict] = None,
    ) -> Any:
        """Execute an Odoo ORM method. Write methods are rejected immediately."""
        _ensure_read_only(method)

        uid = self.authenticate()
        return self._call(
            "object",
            "execute_kw",
            [self._db, uid, self._api_key, model, method, args or [], kwargs or {}],
        )
