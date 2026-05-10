import secrets

from backend.core.config import settings


def verify_credentials(username: str, password: str) -> bool:
    """Constant-time comparison to prevent timing attacks."""
    correct_username = secrets.compare_digest(
        username.encode("utf-8"),
        settings.BASIC_AUTH_USERNAME.encode("utf-8"),
    )
    correct_password = secrets.compare_digest(
        password.encode("utf-8"),
        settings.BASIC_AUTH_PASSWORD.encode("utf-8"),
    )
    return correct_username and correct_password
