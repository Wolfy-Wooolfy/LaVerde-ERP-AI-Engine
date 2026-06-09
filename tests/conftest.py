"""
Root conftest — sets up test environment variables BEFORE any backend module is imported.
"""

import os
from pathlib import Path

_TEST_USER_DB = "data/test-users.db"

# Wipe the test user DB before every session so the A1 seed always fires fresh.
Path(_TEST_USER_DB).parent.mkdir(parents=True, exist_ok=True)
if Path(_TEST_USER_DB).exists():
    Path(_TEST_USER_DB).unlink()

# Load test env vars before pydantic-settings instantiates Settings
_env_file = Path(__file__).parent / ".env.test"
if _env_file.exists():
    from dotenv import dotenv_values

    for k, v in dotenv_values(_env_file).items():
        os.environ.setdefault(k, v or "")
else:
    # Fallback defaults so tests never fail due to missing .env.test
    _defaults = {
        "ODOO_URL": "http://127.0.0.1:18069",
        "ODOO_DB": "testdb",
        "ODOO_USERNAME": "test@test.com",
        "ODOO_API_KEY": "test-api-key",
        "BASIC_AUTH_USERNAME": "testadmin",
        "BASIC_AUTH_PASSWORD": "testpass",
        "ENVIRONMENT": "development",
        "DEBUG": "true",
        "LOG_LEVEL": "DEBUG",
        "CACHE_TTL_SECONDS": "5",
    }
    for k, v in _defaults.items():
        os.environ.setdefault(k, v)

# Always enforce these test-specific overrides regardless of .env.test content.
os.environ["USER_DB_PATH"] = _TEST_USER_DB
os.environ["SESSION_SECRET"] = "test-session-secret-exactly-32-chars!"

# Disable rate limiting so fixture POST /login calls are never throttled.
# The 10/minute limit on POST /login is designed for production; under test the
# testclient IP is shared across all modules and would exhaust the limit instantly.
from backend.core.limiter import limiter as _app_limiter  # noqa: E402
_app_limiter.enabled = False
