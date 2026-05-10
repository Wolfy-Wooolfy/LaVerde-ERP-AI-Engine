import pytest
from pydantic import ValidationError


def test_stage_ids_parsed_correctly() -> None:
    from backend.core.config import Settings

    s = Settings(
        ODOO_URL="http://x",
        ODOO_DB="db",
        ODOO_USERNAME="u",
        ODOO_API_KEY="k",
        BASIC_AUTH_USERNAME="admin",
        BASIC_AUTH_PASSWORD="pass",
        CRM_CRITICAL_STAGE_IDS="1,2,3",
        CRM_CLOSED_EXCLUDED_STAGE_IDS="4,5",
        CRM_DATA_QUALITY_STAGE_IDS="6",
    )
    assert s.critical_stage_ids == [1, 2, 3]
    assert s.closed_excluded_stage_ids == [4, 5]
    assert s.data_quality_stage_ids == [6]


def test_invalid_log_level_raises() -> None:
    from backend.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(
            ODOO_URL="http://x",
            ODOO_DB="db",
            ODOO_USERNAME="u",
            ODOO_API_KEY="k",
            BASIC_AUTH_USERNAME="admin",
            BASIC_AUTH_PASSWORD="pass",
            LOG_LEVEL="VERBOSE",  # not a valid level
        )


def test_invalid_environment_raises() -> None:
    from backend.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(
            ODOO_URL="http://x",
            ODOO_DB="db",
            ODOO_USERNAME="u",
            ODOO_API_KEY="k",
            BASIC_AUTH_USERNAME="admin",
            BASIC_AUTH_PASSWORD="pass",
            ENVIRONMENT="local",  # not allowed
        )


def test_stage_ids_with_spaces() -> None:
    from backend.core.config import Settings

    s = Settings(
        ODOO_URL="http://x",
        ODOO_DB="db",
        ODOO_USERNAME="u",
        ODOO_API_KEY="k",
        BASIC_AUTH_USERNAME="admin",
        BASIC_AUTH_PASSWORD="pass",
        CRM_CRITICAL_STAGE_IDS=" 10 , 20 , 30 ",
    )
    assert s.critical_stage_ids == [10, 20, 30]
