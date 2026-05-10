from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from backend.core.security import verify_credentials
from backend.modules.crm.service import CrmService

_http_basic = HTTPBasic()


def get_current_user(
    credentials: HTTPBasicCredentials = Depends(_http_basic),
) -> str:
    """Validate Basic Auth credentials. Returns the authenticated username."""
    if not verify_credentials(credentials.username, credentials.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def get_crm_service(request: Request) -> CrmService:
    """Return the app-level CrmService singleton (created at startup)."""
    return request.app.state.crm_service  # type: ignore[no-any-return]
