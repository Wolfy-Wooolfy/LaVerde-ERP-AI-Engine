"""
Root conftest — sets up test environment variables BEFORE any backend module is imported.
"""

import os
from pathlib import Path

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
