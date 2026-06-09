import bcrypt


def hash_password(plaintext: str) -> str:
    """Return a bcrypt hash of plaintext as a UTF-8 string."""
    return bcrypt.hashpw(plaintext.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password_hash(plaintext: str, stored_hash: str) -> bool:
    """Constant-time bcrypt check — True iff plaintext matches stored_hash."""
    return bcrypt.checkpw(plaintext.encode("utf-8"), stored_hash.encode("utf-8"))
