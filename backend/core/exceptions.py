class LaVerdeERPError(Exception):
    """Base exception for all LaVerde ERP AI Engine errors."""


class OdooConnectionError(LaVerdeERPError):
    """Raised when the HTTP connection to Odoo fails."""


class OdooAuthenticationError(LaVerdeERPError):
    """Raised when Odoo rejects the provided credentials."""


class OdooQueryError(LaVerdeERPError):
    """Raised when Odoo returns a JSON-RPC error in the response."""


class ReadOnlyViolationError(LaVerdeERPError):
    """Raised when a write operation is attempted on the read-only client."""


class StageResolutionError(LaVerdeERPError):
    """Raised when stage names cannot be fetched or resolved from Odoo."""
