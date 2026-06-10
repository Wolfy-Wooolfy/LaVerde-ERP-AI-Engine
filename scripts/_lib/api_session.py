"""api_session.py — session-cookie login for verify_*/diagnose_* scripts (Session 18, Decision 18.1).

Post-A2 the FastAPI app is session-cookie authenticated; HTTP Basic returns 401.

AUTH EVIDENCE (verbatim sources):
  backend/api/deps.py lines 16-21:
      get_current_user() reads request.session.get("username") → 401 if absent/inactive.
  backend/api/v1/endpoints/auth.py lines 76-110:
      POST /login, form fields {username, password, next}; success stores
      request.session["username"] and returns RedirectResponse(status_code=303);
      bad credentials re-render login.html with status_code=401.
  backend/api/v1/endpoints/auth.py line 77:
      @limiter.limit("10/minute") — login ONCE per process and reuse the client.

Usage:
    from _lib.api_session import login
    client = login("http://localhost:8000")          # env VERIFY_USERNAME/VERIFY_PASSWORD
    r = client.get("/api/v1/customer-accounts/customer/62112")

READ-ONLY: this module performs no Odoo RPC at all. No OpenAI. AI cost = $0.00.
"""

import os

import httpx

_DEFAULT_TIMEOUT = 30.0


class ApiLoginError(RuntimeError):
    """Raised when POST /login does not yield an authenticated session."""


def login(
    base_url: str,
    username: str | None = None,
    password: str | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> httpx.Client:
    """Authenticate against the FastAPI app and return a cookie-carrying client.

    Performs exactly ONE POST /login (rate limit is 10/minute — callers must
    reuse the returned client for every subsequent request, never re-login
    per request). The session cookie set by the 303 response stays on the
    client's cookie jar automatically.

    Args:
        base_url: e.g. "http://localhost:8000".
        username: defaults to env VERIFY_USERNAME (fallback "admin").
        password: defaults to env VERIFY_PASSWORD (fallback "password").

    Returns:
        httpx.Client with base_url set and the session cookie attached.
        follow_redirects=False so scripts see raw status codes (401 vs 200).

    Raises:
        ApiLoginError: credentials rejected, rate-limited, or unexpected reply.
        httpx.ConnectError: server unreachable (propagated for the caller's
        "run scripts/start_server.bat" hint).
    """
    user = username if username is not None else os.environ.get("VERIFY_USERNAME", "admin")
    pwd = password if password is not None else os.environ.get("VERIFY_PASSWORD", "password")

    client = httpx.Client(
        base_url=base_url.rstrip("/"),
        timeout=timeout,
        follow_redirects=False,
    )
    try:
        r = client.post(
            "/login",
            data={"username": user, "password": pwd, "next": "/dashboard"},
        )
    except Exception:
        client.close()
        raise

    if r.status_code == 303:
        if not client.cookies:
            client.close()
            raise ApiLoginError(
                "POST /login returned 303 but no session cookie was set — "
                "SessionMiddleware misconfigured?"
            )
        return client

    client.close()
    if r.status_code == 401:
        raise ApiLoginError(
            f"POST /login rejected credentials for user '{user}' "
            f"(invalid password or inactive account). "
            f"Set VERIFY_USERNAME / VERIFY_PASSWORD."
        )
    if r.status_code == 429:
        raise ApiLoginError(
            "POST /login rate-limited (10/minute). Wait 60s and retry — "
            "and make sure the script logs in only once per process."
        )
    raise ApiLoginError(
        f"POST /login returned unexpected HTTP {r.status_code}: {r.text[:200]}"
    )
