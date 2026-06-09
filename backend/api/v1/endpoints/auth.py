"""Authentication routes: GET /login, POST /login, GET /logout."""

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

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
    return RedirectResponse(url=_sanitize_next(next), status_code=303)


@router.get("/logout", include_in_schema=False)
def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)
