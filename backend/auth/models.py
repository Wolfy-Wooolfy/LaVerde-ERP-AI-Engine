from dataclasses import dataclass


@dataclass
class UserRecord:
    username: str
    password_hash: str
    modules: list[str]
    is_admin: bool
    is_active: bool
    created_at: str
    updated_at: str
