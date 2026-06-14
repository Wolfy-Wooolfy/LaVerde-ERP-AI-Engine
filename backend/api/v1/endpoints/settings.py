"""Phase B — Settings API: admin user management endpoints.

All routes are admin-gated via require_admin_api applied at include_router level in router.py.
Passwords are NEVER returned in any response or written to logs.
"""

import re

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.api.deps import get_current_user
from backend.auth.password import hash_password
from backend.core.responses import error_response

router = APIRouter(tags=["settings"])

_VALID_MODULES: frozenset[str] = frozenset({"crm", "hr", "collections", "customer_accounts", "marketing_attribution", "*"})
_USERNAME_RE = re.compile(r"^[A-Za-z0-9._@\-]{2,64}$")
_MIN_PASSWORD_LEN = 8


def _user_row(record) -> dict:
    """Serialize UserRecord to response dict — password_hash is NEVER included."""
    return {
        "username": record.username,
        "is_admin": record.is_admin,
        "is_active": record.is_active,
        "modules": record.modules,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _ok(data: dict, status_code: int = 200) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"ok": True, "data": data})


# ── Request bodies ────────────────────────────────────────────────────────────


class CreateUserBody(BaseModel):
    username: str
    password: str
    modules: list[str] = []
    is_admin: bool = False


class ModulesBody(BaseModel):
    modules: list[str]


class StatusBody(BaseModel):
    is_active: bool


class AdminBody(BaseModel):
    is_admin: bool


class ResetPasswordBody(BaseModel):
    new_password: str


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/users")
async def list_users(request: Request) -> JSONResponse:
    repo = request.app.state.user_repo
    users = repo.list_users()
    return _ok({"users": [_user_row(u) for u in users]})


@router.post("/users")
async def create_user(
    body: CreateUserBody,
    request: Request,
) -> JSONResponse:
    if not _USERNAME_RE.match(body.username):
        return error_response(
            request, 422, "INVALID_USERNAME",
            "Username must be 2–64 characters (letters, digits, . _ @ -).",
        )
    if len(body.password) < _MIN_PASSWORD_LEN:
        return error_response(
            request, 422, "PASSWORD_TOO_SHORT",
            f"Password must be at least {_MIN_PASSWORD_LEN} characters.",
        )
    for m in body.modules:
        if m not in _VALID_MODULES:
            return error_response(
                request, 422, "INVALID_MODULE",
                f"Invalid module {m!r}. Valid: {sorted(_VALID_MODULES)}.",
            )
    repo = request.app.state.user_repo
    try:
        record = repo.create_user(
            username=body.username,
            password_hash=hash_password(body.password),
            modules=body.modules,
            is_admin=body.is_admin,
            is_active=True,
        )
    except ValueError:
        return error_response(
            request, 409, "USERNAME_EXISTS",
            f"Username {body.username!r} already exists.",
        )
    return _ok(_user_row(record), status_code=201)


@router.patch("/users/{username}/modules")
async def update_modules(
    username: str,
    body: ModulesBody,
    request: Request,
) -> JSONResponse:
    for m in body.modules:
        if m not in _VALID_MODULES:
            return error_response(
                request, 422, "INVALID_MODULE",
                f"Invalid module {m!r}.",
            )
    repo = request.app.state.user_repo
    if repo.get_user(username) is None:
        return error_response(request, 404, "USER_NOT_FOUND", f"User {username!r} not found.")
    record = repo.update_user(username, modules=body.modules)
    return _ok(_user_row(record))


@router.patch("/users/{username}/status")
async def update_status(
    username: str,
    body: StatusBody,
    request: Request,
    requesting_username: str = Depends(get_current_user),
) -> JSONResponse:
    # L1: self-deactivation — pure string compare, no DB read needed
    if not body.is_active and requesting_username == username:
        return error_response(
            request, 422, "SELF_LOCKOUT_DEACTIVATION",
            "You cannot deactivate your own account.",
        )
    repo = request.app.state.user_repo
    target = repo.get_user(username)
    if target is None:
        return error_response(request, 404, "USER_NOT_FOUND", f"User {username!r} not found.")
    # L3: last-admin protection — one COUNT query
    if not body.is_active and target.is_admin:
        if repo.count_active_admins() <= 1:
            return error_response(
                request, 422, "LAST_ADMIN_PROTECTION",
                "Cannot deactivate the last active admin.",
            )
    record = repo.update_user(username, is_active=body.is_active)
    return _ok(_user_row(record))


@router.patch("/users/{username}/admin")
async def update_admin(
    username: str,
    body: AdminBody,
    request: Request,
    requesting_username: str = Depends(get_current_user),
) -> JSONResponse:
    # L2: self-demote — pure string compare, no DB read needed
    if not body.is_admin and requesting_username == username:
        return error_response(
            request, 422, "SELF_LOCKOUT_DEMOTE",
            "You cannot remove your own admin role.",
        )
    repo = request.app.state.user_repo
    target = repo.get_user(username)
    if target is None:
        return error_response(request, 404, "USER_NOT_FOUND", f"User {username!r} not found.")
    # L4: last-admin protection — one COUNT query
    if not body.is_admin and target.is_admin:
        if repo.count_active_admins() <= 1:
            return error_response(
                request, 422, "LAST_ADMIN_PROTECTION",
                "Cannot demote the last active admin.",
            )
    record = repo.update_user(username, is_admin=body.is_admin)
    return _ok(_user_row(record))


@router.post("/users/{username}/reset-password")
async def reset_password(
    username: str,
    body: ResetPasswordBody,
    request: Request,
) -> JSONResponse:
    if len(body.new_password) < _MIN_PASSWORD_LEN:
        return error_response(
            request, 422, "PASSWORD_TOO_SHORT",
            f"Password must be at least {_MIN_PASSWORD_LEN} characters.",
        )
    repo = request.app.state.user_repo
    if repo.get_user(username) is None:
        return error_response(request, 404, "USER_NOT_FOUND", f"User {username!r} not found.")
    record = repo.update_user(username, password_hash=hash_password(body.new_password))
    return _ok({"username": record.username, "updated_at": record.updated_at})
