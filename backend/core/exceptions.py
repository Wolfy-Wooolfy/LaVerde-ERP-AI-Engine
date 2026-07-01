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


class InventoryScopeNotFoundError(LaVerdeERPError):
    """Raised when a Projects Inventory drill names a (level, parent_id) scope that
    matches no units — an unknown or stale node. The endpoint maps this to HTTP 404."""


class ProjectNotFoundError(LaVerdeERPError):
    """Raised when a project_id is not present in the live project-name resolver map.
    The endpoint maps this to HTTP 404."""
