from loguru import logger

from backend.auth.password import hash_password
from backend.auth.repository import UserRepository
from backend.core.config import settings


def seed_initial_user(repo: UserRepository) -> None:
    """If the users table is empty, seed one admin from env vars. Idempotent."""
    if repo.list_users():
        logger.debug("user store: table not empty, skipping seed")
        return
    username = settings.BASIC_AUTH_USERNAME
    repo.create_user(
        username=username,
        password_hash=hash_password(settings.BASIC_AUTH_PASSWORD),
        modules=["*"],
        is_admin=True,
        is_active=True,
    )
    logger.info(f"user store: seeded initial admin '{username}'")
