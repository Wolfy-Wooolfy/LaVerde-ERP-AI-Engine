"""Authentication routes: GET /login, POST /login, GET /logout."""

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from backend.api.deps import get_current_user_html
from backend.core.i18n import detect_lang, load_translations, make_translator
from backend.core.limiter import limiter

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory="frontend/templates")

load_translations()


def _sanitize_next(next_url: str) -> str:
    """Allow only relative paths starting with /. Blocks open-redirect via // or absolute URLs."""
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return "/dashboard"


_ORDERED_MODULE_DASHBOARDS: list[tuple[str, str]] = [
    ("crm",               "/dashboard"),
    ("hr",                "/hr/dashboard"),
    ("collections",       "/collections/dashboard"),
    ("customer_accounts", "/customer-accounts/dashboard"),
]

_PATH_MODULE_MAP: dict[str, str] = {
    "/dashboard":         "crm",
    "/data-quality":      "crm",
    "/hr":                "hr",
    "/collections":       "collections",
    "/customer-accounts": "customer_accounts",
}


def _user_can_access_path(user_modules: list[str], path: str) -> bool:
    if "*" in user_modules:
        return True
    for prefix, module_id in _PATH_MODULE_MAP.items():
        if path.startswith(prefix):
            return module_id in user_modules
    return True


def _first_allowed_dashboard(user_modules: list[str]) -> str | None:
    if "*" in user_modules:
        return "/dashboard"
    for module_id, url in _ORDERED_MODULE_DASHBOARDS:
        if module_id in user_modules:
            return url
    return None


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page(
    request: Request,
    next: str = Query(default="/dashboard"),
    error: str = Query(default=""),
) -> HTMLResponse:
    lang = detect_lang(dict(request.cookies), request.headers.get("accept-language", ""))
    ctx = {
        "request": request,
        "next": _sanitize_next(next),
        "error": error,
        "lang": lang,
        "is_rtl": lang == "ar",
        "_t": make_translator(lang),
    }
    return templates.TemplateResponse(request, "login.html", ctx)


@router.post("/login", response_class=HTMLResponse, include_in_schema=False)
@limiter.limit("10/minute")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form(default="/dashboard"),
) -> Response:
    user_repo = request.app.state.user_repo
    user = user_repo.get_user(username)
    authed = user_repo.verify_password(username, password) if user is not None else False
    active = user.is_active if user is not None else False

    if not authed or not active:
        error_key = "login.error.inactive" if (authed and not active) else "login.error.invalid"
        lang = detect_lang(dict(request.cookies), request.headers.get("accept-language", ""))
        ctx = {
            "request": request,
            "next": _sanitize_next(next),
            "error": error_key,
            "lang": lang,
            "is_rtl": lang == "ar",
            "_t": make_translator(lang),
        }
        return templates.TemplateResponse(request, "login.html", ctx, status_code=401)

    request.session["username"] = username
    safe_next = _sanitize_next(next)
    if safe_next != "/dashboard" and _user_can_access_path(user.modules, safe_next):
        target = safe_next
    else:
        landing = _first_allowed_dashboard(user.modules)
        target = landing if landing is not None else "/no-modules"
    return RedirectResponse(url=target, status_code=303)


@router.get("/no-modules", response_class=HTMLResponse, include_in_schema=False)
async def no_modules_page(
    request: Request,
    user: str = Depends(get_current_user_html),
) -> HTMLResponse:
    """Landing page for authenticated users with no modules assigned."""
    lang = detect_lang(dict(request.cookies), request.headers.get("accept-language", ""))
    ctx = {
        "request": request,
        "current_user": user,
        "lang": lang,
        "is_rtl": lang == "ar",
        "_t": make_translator(lang),
    }
    return templates.TemplateResponse(request, "no_modules.html", ctx)


@router.get("/logout", include_in_schema=False)
def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)
