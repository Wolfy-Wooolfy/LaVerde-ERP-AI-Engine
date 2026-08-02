"""Unit tests for scripts/delete_users.py — guarded user-deletion maintenance CLI."""

from pathlib import Path

import pytest

from backend.auth.password import hash_password
from backend.auth.repository import SQLiteUserRepository
from scripts.delete_users import Refusal, plan_deletions, run_deletion


@pytest.fixture()
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "users.db")


@pytest.fixture()
def repo(db_path: str) -> SQLiteUserRepository:
    """File-backed repository per test — needed because backup_database() copies
    the on-disk file, unlike test_user_store.py's ':memory:' fixture."""
    return SQLiteUserRepository(db_path)


def _hash(pw: str = "pw") -> str:
    return hash_password(pw)


# ── happy path ────────────────────────────────────────────────────────────────


def test_happy_path_deletes_inactive_non_admin(
    repo: SQLiteUserRepository, db_path: str
) -> None:
    h = _hash()
    repo.create_user("target", h, ["crm"], is_admin=False, is_active=False)
    repo.create_user("keep_me", h, ["hr"], is_admin=False, is_active=True)

    code = run_deletion(repo, db_path, ["target"], yes=True)

    assert code == 0
    assert repo.get_user("target") is None
    kept = repo.get_user("keep_me")
    assert kept is not None
    assert kept.username == "keep_me"


# ── guard (b): admin ─────────────────────────────────────────────────────────


def test_guard_refuses_admin(repo: SQLiteUserRepository, db_path: str) -> None:
    h = _hash()
    repo.create_user("boss", h, ["*"], is_admin=True, is_active=False)
    repo.create_user("other", h, ["crm"], is_admin=False, is_active=True)

    code = run_deletion(repo, db_path, ["boss"], yes=True)

    assert code == 1
    assert repo.get_user("boss") is not None


# ── guard (c): active ────────────────────────────────────────────────────────


def test_guard_refuses_active_user(repo: SQLiteUserRepository, db_path: str) -> None:
    h = _hash()
    repo.create_user("live_user", h, ["crm"], is_admin=False, is_active=True)
    repo.create_user("other", h, ["crm"], is_admin=False, is_active=False)

    code = run_deletion(repo, db_path, ["live_user"], yes=True)

    assert code == 1
    assert repo.get_user("live_user") is not None


# ── guard (a): nonexistent ───────────────────────────────────────────────────


def test_guard_refuses_nonexistent_user_cleanly(
    repo: SQLiteUserRepository, db_path: str
) -> None:
    h = _hash()
    repo.create_user("someone", h, ["crm"], is_admin=False, is_active=True)

    code = run_deletion(repo, db_path, ["ghost"], yes=True)

    assert code == 1
    assert repo.get_user("ghost") is None
    assert repo.get_user("someone") is not None


# ── guard (d): last remaining user ──────────────────────────────────────────


def test_guard_refuses_last_remaining_user(
    repo: SQLiteUserRepository, db_path: str
) -> None:
    h = _hash()
    repo.create_user("lonely", h, ["crm"], is_admin=False, is_active=False)

    code = run_deletion(repo, db_path, ["lonely"], yes=True)

    assert code == 1
    assert repo.get_user("lonely") is not None
    assert len(repo.list_users()) == 1


# ── backup ────────────────────────────────────────────────────────────────────


def test_backup_created_with_pre_deletion_state(
    repo: SQLiteUserRepository, db_path: str
) -> None:
    h = _hash()
    repo.create_user("target", h, ["crm"], is_admin=False, is_active=False)
    repo.create_user("keep_me", h, ["hr"], is_admin=False, is_active=True)

    code = run_deletion(repo, db_path, ["target"], yes=True)
    assert code == 0

    backups = list(Path(db_path).parent.glob("users.db.bak-*.db"))
    assert len(backups) == 1

    backup_repo = SQLiteUserRepository(str(backups[0]))
    backed_up_target = backup_repo.get_user("target")
    assert backed_up_target is not None  # backup predates the deletion
    assert backup_repo.get_user("keep_me") is not None


# ── mixed batch ───────────────────────────────────────────────────────────────


def test_mixed_batch_deletes_valid_refuses_invalid(
    repo: SQLiteUserRepository, db_path: str
) -> None:
    h = _hash()
    repo.create_user("valid_target", h, ["crm"], is_admin=False, is_active=False)
    repo.create_user("admin_user", h, ["*"], is_admin=True, is_active=False)
    repo.create_user("other", h, ["crm"], is_admin=False, is_active=True)

    code = run_deletion(repo, db_path, ["valid_target", "ghost", "admin_user"], yes=True)

    assert code == 1
    assert repo.get_user("valid_target") is None
    assert repo.get_user("admin_user") is not None
    assert repo.get_user("other") is not None


# ── duplicate usernames within one invocation ───────────────────────────────


def test_plan_deletions_handles_duplicate_username_in_batch(
    repo: SQLiteUserRepository,
) -> None:
    h = _hash()
    repo.create_user("target", h, ["crm"], is_admin=False, is_active=False)
    repo.create_user("other", h, ["crm"], is_admin=False, is_active=True)

    approved, refusals = plan_deletions(repo, ["target", "target"])

    assert approved == ["target"]
    assert refusals == [Refusal("target", "user does not exist")]
