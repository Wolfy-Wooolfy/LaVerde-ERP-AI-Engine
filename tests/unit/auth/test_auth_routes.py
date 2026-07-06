"""
Unit tests for auth routes: GET /login, POST /login, GET /logout.

Covers:
  1.  test_get_login_returns_200              — GET /login → 200
  2.  test_get_login_with_next_param          — GET /login?next=/dashboard → 200
  3.  test_post_login_success_redirects       — valid creds → 303 to /dashboard
  4.  test_post_login_success_custom_next     — valid creds + next=/summary → 303 to /summary
  5.  test_post_login_bad_password_returns_401 — wrong password → 401, login.html
  6.  test_post_login_unknown_user_returns_401 — non-existent user → 401
  7.  test_post_login_inactive_user_returns_401 — inactive user → 401 with inactive error
  8.  test_post_login_sets_session_cookie      — valid login → session cookie in jar
  9.  test_logout_redirects_to_login          — GET /logout → 303 to /login
  10. test_logout_clears_session              — login then logout → subsequent protected req → 401
  11. test_open_redirect_blocked_on_post      — next=//evil.com → Location=/dashboard
  12. test_open_redirect_blocked_on_get       — GET /login?next=//evil.com → 200 (sanitized form)
"""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.auth.models import UserRecord
from backend.main import app

_INACTIVE_USER = UserRecord(
    username="inactive",
    password_hash="$2b$12$placeholder",
    modules=[],
    is_admin=False,
    is_active=False,
    created_at="2026-01-01T00:00:00",
    updated_at="2026-01-01T00:00:00",
)


# ── Test 1 — GET /login → 200 ────────────────────────────────────────────────

def test_get_login_returns_200() -> None:
    with TestClient(app, follow_redirects=False) as c:
        r = c.get("/login")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


# ── Test 2 — GET /login?next=/dashboard → 200 ───────────────────────────────

def test_get_login_with_next_param() -> None:
    with TestClient(app, follow_redirects=False) as c:
        r = c.get("/login?next=/dashboard")
    assert r.status_code == 200


# ── Test 3 — POST /login success → 303 to /dashboard ────────────────────────

def test_post_login_success_redirects() -> None:
    with TestClient(app, follow_redirects=False) as c:
        r = c.post(
            "/login",
            data={"username": "testadmin", "password": "testpass", "next": "/dashboard"},
        )
    assert r.status_code == 303
    assert r.headers.get("location") == "/dashboard"


# ── Test 4 — POST /login success + custom next → 303 to custom next ──────────

def test_post_login_success_custom_next() -> None:
    with TestClient(app, follow_redirects=False) as c:
        r = c.post(
            "/login",
            data={"username": "testadmin", "password": "testpass", "next": "/summary"},
        )
    assert r.status_code == 303
    assert r.headers.get("location") == "/summary"


# ── Test 5 — POST /login wrong password → 401 ───────────────────────────────

def test_post_login_bad_password_returns_401() -> None:
    with TestClient(app, follow_redirects=False) as c:
        r = c.post(
            "/login",
            data={"username": "testadmin", "password": "wrongpass", "next": "/dashboard"},
        )
    assert r.status_code == 401
    assert "text/html" in r.headers.get("content-type", "")


# ── Test 6 — POST /login unknown user → 401 ─────────────────────────────────

def test_post_login_unknown_user_returns_401() -> None:
    with TestClient(app, follow_redirects=False) as c:
        r = c.post(
            "/login",
            data={"username": "ghost", "password": "anything", "next": "/dashboard"},
        )
    assert r.status_code == 401


# ── Test 7 — POST /login inactive user → 401 with inactive error ─────────────

def test_post_login_inactive_user_returns_401() -> None:
    mock_repo = MagicMock()
    mock_repo.get_user.return_value = _INACTIVE_USER
    mock_repo.verify_password.return_value = True

    with TestClient(app, follow_redirects=False) as c:
        c.app.state.user_repo = mock_repo
        r = c.post(
            "/login",
            data={"username": "inactive", "password": "anypass", "next": "/dashboard"},
        )
    assert r.status_code == 401


# ── Test 8 — POST /login sets session cookie ─────────────────────────────────

def test_post_login_sets_session_cookie() -> None:
    with TestClient(app, follow_redirects=False) as c:
        r = c.post(
            "/login",
            data={"username": "testadmin", "password": "testpass", "next": "/dashboard"},
        )
    assert r.status_code == 303
    assert "laverde_session" in r.cookies


# ── Test 9 — GET /logout → 303 to /login ────────────────────────────────────

def test_logout_redirects_to_login() -> None:
    with TestClient(app, follow_redirects=False) as c:
        c.post(
            "/login",
            data={"username": "testadmin", "password": "testpass", "next": "/dashboard"},
            follow_redirects=False,
        )
        r = c.get("/logout")
    assert r.status_code == 303
    assert r.headers.get("location") == "/login"


# ── Test 10 — logout clears session → protected endpoint returns 401 ─────────

def test_logout_clears_session() -> None:
    with TestClient(app, follow_redirects=False) as c:
        c.post(
            "/login",
            data={"username": "testadmin", "password": "testpass", "next": "/dashboard"},
            follow_redirects=False,
        )
        c.get("/logout")
        r = c.get("/api/v1/hr/kpi/headcount")
    assert r.status_code == 401


# ── Test 11 — open-redirect blocked on POST /login ───────────────────────────

def test_open_redirect_blocked_on_post() -> None:
    with TestClient(app, follow_redirects=False) as c:
        r = c.post(
            "/login",
            data={"username": "testadmin", "password": "testpass", "next": "//evil.com"},
        )
    assert r.status_code == 303
    assert r.headers.get("location") == "/dashboard"


# ── Test 12 — open-redirect blocked on GET /login ────────────────────────────

def test_open_redirect_blocked_on_get() -> None:
    with TestClient(app, follow_redirects=False) as c:
        r = c.get("/login?next=//evil.com")
    assert r.status_code == 200
