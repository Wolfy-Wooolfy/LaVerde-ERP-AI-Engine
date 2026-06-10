"""A1 unit tests — SQLite UserRepository, bcrypt helpers, and seed bootstrap."""

import pytest

from backend.auth.models import UserRecord
from backend.auth.password import hash_password, verify_password_hash
from backend.auth.repository import SQLiteUserRepository
from backend.auth.seed import seed_initial_user


@pytest.fixture()
def repo() -> SQLiteUserRepository:
    """Fresh in-memory repository per test — fast and isolated."""
    return SQLiteUserRepository(":memory:")


# ── bcrypt helpers ────────────────────────────────────────────────────────────


def test_hash_and_verify_correct() -> None:
    h = hash_password("correcthorsebattery")
    assert verify_password_hash("correcthorsebattery", h) is True


def test_verify_wrong_password() -> None:
    h = hash_password("correcthorsebattery")
    assert verify_password_hash("wrongpassword", h) is False


def test_two_hashes_of_same_password_differ() -> None:
    # bcrypt salts are random — same plaintext → different hashes, both valid
    h1 = hash_password("same")
    h2 = hash_password("same")
    assert h1 != h2
    assert verify_password_hash("same", h1) is True
    assert verify_password_hash("same", h2) is True


# ── create_user / get_user ────────────────────────────────────────────────────


def test_create_and_get_user(repo: SQLiteUserRepository) -> None:
    h = hash_password("secret")
    repo.create_user("alice", h, ["crm"], is_admin=False, is_active=True)
    user = repo.get_user("alice")
    assert user is not None
    assert user.username == "alice"
    assert user.password_hash == h
    assert user.modules == ["crm"]
    assert user.is_admin is False
    assert user.is_active is True


def test_get_nonexistent_user_returns_none(repo: SQLiteUserRepository) -> None:
    assert repo.get_user("nobody") is None


def test_create_user_returns_userrecord(repo: SQLiteUserRepository) -> None:
    h = hash_password("pw")
    record = repo.create_user("bob", h, ["hr"], is_admin=True, is_active=False)
    assert isinstance(record, UserRecord)
    assert record.username == "bob"
    assert record.is_admin is True
    assert record.is_active is False


def test_duplicate_username_raises(repo: SQLiteUserRepository) -> None:
    h = hash_password("pw")
    repo.create_user("alice", h, ["*"], is_admin=False, is_active=True)
    with pytest.raises(ValueError, match="already exists"):
        repo.create_user("alice", h, ["*"], is_admin=False, is_active=True)


# ── list_users ────────────────────────────────────────────────────────────────


def test_list_users_empty(repo: SQLiteUserRepository) -> None:
    assert repo.list_users() == []


def test_list_users_multiple(repo: SQLiteUserRepository) -> None:
    h = hash_password("pw")
    repo.create_user("bob", h, ["crm"], is_admin=False, is_active=True)
    repo.create_user("alice", h, ["hr"], is_admin=False, is_active=True)
    users = repo.list_users()
    assert len(users) == 2
    # list_users returns ordered by username
    assert [u.username for u in users] == ["alice", "bob"]


# ── update_user ───────────────────────────────────────────────────────────────


def test_update_modules(repo: SQLiteUserRepository) -> None:
    h = hash_password("pw")
    repo.create_user("alice", h, ["crm"], is_admin=False, is_active=True)
    updated = repo.update_user("alice", modules=["crm", "hr"])
    assert updated.modules == ["crm", "hr"]
    assert repo.get_user("alice").modules == ["crm", "hr"]  # type: ignore[union-attr]


def test_update_is_admin(repo: SQLiteUserRepository) -> None:
    h = hash_password("pw")
    repo.create_user("alice", h, ["crm"], is_admin=False, is_active=True)
    updated = repo.update_user("alice", is_admin=True)
    assert updated.is_admin is True
    assert repo.get_user("alice").is_admin is True  # type: ignore[union-attr]


def test_update_is_active(repo: SQLiteUserRepository) -> None:
    h = hash_password("pw")
    repo.create_user("alice", h, ["crm"], is_admin=False, is_active=True)
    updated = repo.update_user("alice", is_active=False)
    assert updated.is_active is False


def test_update_password(repo: SQLiteUserRepository) -> None:
    h_old = hash_password("oldpass")
    repo.create_user("alice", h_old, ["crm"], is_admin=False, is_active=True)
    h_new = hash_password("newpass")
    repo.update_user("alice", password_hash=h_new)
    assert repo.verify_password("alice", "newpass") is True
    assert repo.verify_password("alice", "oldpass") is False


def test_update_nonexistent_user_raises(repo: SQLiteUserRepository) -> None:
    with pytest.raises(KeyError):
        repo.update_user("ghost", modules=["crm"])


def test_update_no_fields_raises(repo: SQLiteUserRepository) -> None:
    h = hash_password("pw")
    repo.create_user("alice", h, ["crm"], is_admin=False, is_active=True)
    with pytest.raises(ValueError, match="no fields"):
        repo.update_user("alice")


# ── delete_user ───────────────────────────────────────────────────────────────


def test_delete_user(repo: SQLiteUserRepository) -> None:
    h = hash_password("pw")
    repo.create_user("alice", h, ["crm"], is_admin=False, is_active=True)
    assert repo.delete_user("alice") is True
    assert repo.get_user("alice") is None


def test_delete_nonexistent_returns_false(repo: SQLiteUserRepository) -> None:
    assert repo.delete_user("nobody") is False


# ── verify_password ───────────────────────────────────────────────────────────


def test_verify_password_correct(repo: SQLiteUserRepository) -> None:
    h = hash_password("hunter2")
    repo.create_user("alice", h, ["crm"], is_admin=False, is_active=True)
    assert repo.verify_password("alice", "hunter2") is True


def test_verify_password_wrong(repo: SQLiteUserRepository) -> None:
    h = hash_password("hunter2")
    repo.create_user("alice", h, ["crm"], is_admin=False, is_active=True)
    assert repo.verify_password("alice", "wrongpass") is False


def test_verify_password_nonexistent_user(repo: SQLiteUserRepository) -> None:
    assert repo.verify_password("nobody", "pw") is False


def test_verify_password_inactive_user_checks_hash_only(repo: SQLiteUserRepository) -> None:
    # verify_password is pure storage-layer hash check; is_active enforcement is A3
    h = hash_password("pw")
    repo.create_user("alice", h, ["crm"], is_admin=False, is_active=False)
    assert repo.verify_password("alice", "pw") is True


# ── modules=["*"] semantics ───────────────────────────────────────────────────


def test_wildcard_modules_stored_and_retrieved(repo: SQLiteUserRepository) -> None:
    h = hash_password("pw")
    repo.create_user("alice", h, ["*"], is_admin=False, is_active=True)
    user = repo.get_user("alice")
    assert user is not None
    assert user.modules == ["*"]


def test_multi_module_list_round_trip(repo: SQLiteUserRepository) -> None:
    h = hash_password("pw")
    modules = ["crm", "collections", "hr", "customer_accounts"]
    repo.create_user("alice", h, modules, is_admin=False, is_active=True)
    assert repo.get_user("alice").modules == modules  # type: ignore[union-attr]


# ── is_admin independent of modules ──────────────────────────────────────────


def test_is_admin_independent_of_modules(repo: SQLiteUserRepository) -> None:
    h = hash_password("pw")
    # Chairman: all modules, NOT admin
    repo.create_user("chairman", h, ["*"], is_admin=False, is_active=True)
    # IT admin: limited modules, IS admin
    repo.create_user("it_admin", h, ["hr"], is_admin=True, is_active=True)

    chairman = repo.get_user("chairman")
    it_admin = repo.get_user("it_admin")

    assert chairman is not None and chairman.modules == ["*"] and chairman.is_admin is False
    assert it_admin is not None and it_admin.modules == ["hr"] and it_admin.is_admin is True


def test_is_admin_true_with_wildcard_modules(repo: SQLiteUserRepository) -> None:
    h = hash_password("pw")
    repo.create_user("superuser", h, ["*"], is_admin=True, is_active=True)
    user = repo.get_user("superuser")
    assert user is not None
    assert user.modules == ["*"]
    assert user.is_admin is True


# ── seed bootstrap ────────────────────────────────────────────────────────────


def test_seed_creates_admin(repo: SQLiteUserRepository) -> None:
    # conftest/.env.test sets BASIC_AUTH_USERNAME=testadmin, BASIC_AUTH_PASSWORD=testpass
    seed_initial_user(repo)
    user = repo.get_user("testadmin")
    assert user is not None
    assert user.modules == ["*"]
    assert user.is_admin is True
    assert user.is_active is True


def test_seed_password_verifiable(repo: SQLiteUserRepository) -> None:
    seed_initial_user(repo)
    assert repo.verify_password("testadmin", "testpass") is True


def test_seed_idempotent_called_twice(repo: SQLiteUserRepository) -> None:
    seed_initial_user(repo)
    seed_initial_user(repo)  # must be a no-op
    assert len(repo.list_users()) == 1


def test_seed_skipped_when_users_exist(repo: SQLiteUserRepository) -> None:
    h = hash_password("pw")
    repo.create_user("existing", h, ["crm"], is_admin=False, is_active=True)
    seed_initial_user(repo)  # must not add a second user
    users = repo.list_users()
    assert len(users) == 1
    assert users[0].username == "existing"


# ── count_active_admins ───────────────────────────────────────────────────────


def test_count_active_admins_counts_correctly(repo: SQLiteUserRepository) -> None:
    h = hash_password("pw")
    # 1 active admin, 1 inactive admin, 1 active non-admin
    repo.create_user("admin_active", h, ["*"], is_admin=True, is_active=True)
    repo.create_user("admin_inactive", h, ["*"], is_admin=True, is_active=False)
    repo.create_user("non_admin", h, ["crm"], is_admin=False, is_active=True)
    assert repo.count_active_admins() == 1


def test_count_active_admins_zero_when_empty(repo: SQLiteUserRepository) -> None:
    assert repo.count_active_admins() == 0


def test_count_active_admins_multiple(repo: SQLiteUserRepository) -> None:
    h = hash_password("pw")
    repo.create_user("admin1", h, ["*"], is_admin=True, is_active=True)
    repo.create_user("admin2", h, ["*"], is_admin=True, is_active=True)
    assert repo.count_active_admins() == 2
