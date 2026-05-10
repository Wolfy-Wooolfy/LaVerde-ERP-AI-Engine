class CRMAIEngineError(Exception):
    """Base exception for all CRM AI Engine errors."""


class OdooConnectionError(CRMAIEngineError):
    """Raised when the HTTP connection to Odoo fails."""


class OdooAuthenticationError(CRMAIEngineError):
    """Raised when Odoo rejects the provided credentials."""


class OdooQueryError(CRMAIEngineError):
    """Raised when Odoo returns a JSON-RPC error in the response."""


class ReadOnlyViolationError(CRMAIEngineError):
    """Raised when a write operation is attempted on the read-only client."""


class StageResolutionError(CRMAIEngineError):
    """Raised when stage names cannot be fetched or resolved from Odoo."""
