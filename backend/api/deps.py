from fastapi import Depends, HTTPException, Request

from backend.modules.crm.service import CrmService


def _resolve_active_username(request: Request) -> str | None:
    username = request.session.get("username")
    if not username:
        return None
    user = request.app.state.user_repo.get_user(username)
    if user is None or not user.is_active:
        return None
    return username


def get_current_user(request: Request) -> str:
    """Session-based auth. Returns username or raises 401."""
    username = _resolve_active_username(request)
    if username is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return username


def get_current_user_html(request: Request) -> str:
    """Session-based auth for HTML routes. Returns username or redirects to /login."""
    username = _resolve_active_username(request)
    if username is None:
        next_url = request.url.path
        raise HTTPException(
            status_code=302,
            headers={"Location": f"/login?next={next_url}"},
        )
    return username


def get_crm_service(request: Request) -> CrmService:
    """Return the app-level CrmService singleton (created at startup)."""
    return request.app.state.crm_service  # type: ignore[no-any-return]


def require_module_api(module_id: str):
    """Factory: enforces module access on API routes. Chains off get_current_user (401 handled upstream)."""
    def _guard(request: Request, username: str = Depends(get_current_user)) -> None:
        user = request.app.state.user_repo.get_user(username)
        if user is None:
            raise HTTPException(status_code=401, detail="Not authenticated")
        if "*" not in user.modules and module_id not in user.modules:
            raise HTTPException(
                status_code=403,
                detail={"code": "MODULE_ACCESS_DENIED", "module": module_id},
            )
    return _guard


def require_module_html(module_id: str):
    """Factory: enforces module access on HTML routes. Chains off get_current_user_html (302 handled upstream)."""
    def _guard(request: Request, username: str = Depends(get_current_user_html)) -> None:
        user = request.app.state.user_repo.get_user(username)
        if user is None:
            raise HTTPException(
                status_code=302,
                headers={"Location": f"/login?next={request.url.path}"},
            )
        if "*" not in user.modules and module_id not in user.modules:
            raise HTTPException(status_code=403)
    return _guard
