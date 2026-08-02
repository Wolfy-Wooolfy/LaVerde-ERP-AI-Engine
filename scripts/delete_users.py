#!/usr/bin/env python
"""delete_users.py — guarded one-shot maintenance script to delete named users
from the local SQLite user store.

Usage:
  python scripts/delete_users.py <username> [<username> ...] [--yes]

Safety guards — a username is refused (and clearly reported) if it:
  (a) does not exist in the store;
  (b) belongs to an admin account;
  (c) is currently active;
  (d) is the last remaining user in the store.

The database file is backed up as a timestamped sibling (e.g.
data/users.db.bak-20260802-153000.db) before any row is deleted. By default the
script prompts interactively for a confirmation word before deleting; pass --yes
to skip the prompt for non-interactive/scripted use.

This is a maintenance tool for the LOCAL SQLite user store only. It makes no
network calls and never touches Odoo. Configuration is loaded from .env exactly
as the application loads it at startup — a broken or missing SESSION_SECRET (or
any other required setting) fails loudly here too.
"""

import argparse
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

# Ensure project root is on sys.path so backend packages can be imported.
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.auth.models import UserRecord  # noqa: E402
from backend.auth.repository import SQLiteUserRepository  # noqa: E402

if TYPE_CHECKING:
    from backend.core.config import Settings

CONFIRM_WORD = "DELETE"

_GUARD_MISSING = "user does not exist"
_GUARD_ADMIN = "user is an admin"
_GUARD_ACTIVE = "user is currently active"
_GUARD_LAST = "user is the last remaining user in the store"


@dataclass
class Refusal:
    username: str
    reason: str


def plan_deletions(
    repo: SQLiteUserRepository, usernames: list[str]
) -> tuple[list[str], list[Refusal]]:
    """Decide which usernames may be deleted, applying every safety guard.

    Simulates the batch against an in-memory snapshot of the current store —
    never touches the real database — so guard (d) (refusing to empty the
    store) and repeated usernames are handled correctly across a multi-name
    batch: once a username is approved it is removed from the snapshot before
    the next name is checked, so a second occurrence of the same username
    correctly hits guard (a) rather than being approved twice. Guards are
    applied in order (a) missing -> (b) admin -> (c) active -> (d) last
    remaining, and only the first tripped guard is reported per username.
    """
    live: dict[str, UserRecord] = {u.username: u for u in repo.list_users()}
    approved: list[str] = []
    refusals: list[Refusal] = []
    for username in usernames:
        user = live.get(username)
        if user is None:
            refusals.append(Refusal(username, _GUARD_MISSING))
        elif user.is_admin:
            refusals.append(Refusal(username, _GUARD_ADMIN))
        elif user.is_active:
            refusals.append(Refusal(username, _GUARD_ACTIVE))
        elif len(live) <= 1:
            refusals.append(Refusal(username, _GUARD_LAST))
        else:
            approved.append(username)
            del live[username]
    return approved, refusals


def backup_database(db_path: str) -> Path:
    """Copy the user database file to a timestamped sibling backup.

    Raises FileNotFoundError/OSError on any failure. The caller must not
    proceed to delete anything if this raises.
    """
    src = Path(db_path)
    if not src.exists():
        raise FileNotFoundError(f"user database not found: {src}")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_path = src.with_name(f"{src.name}.bak-{timestamp}.db")
    shutil.copy2(src, backup_path)
    return backup_path


def _print_plan(approved: list[str], refusals: list[Refusal]) -> None:
    print("Deletion plan:")
    for username in approved:
        print(f"  WILL DELETE   {username}")
    for r in refusals:
        print(f"  REFUSED       {r.username}  ({r.reason})")


def _print_remaining_users(users: list[UserRecord]) -> None:
    print("Remaining users:")
    if not users:
        print("  (no users remaining)")
        return
    fmt = "  {:<24} {:<8} {:<8}"
    print(fmt.format("Username", "Active", "Admin"))
    for u in users:
        print(fmt.format(u.username, str(u.is_active), str(u.is_admin)))


def run_deletion(
    repo: SQLiteUserRepository,
    db_path: str,
    usernames: list[str],
    *,
    yes: bool = False,
) -> int:
    """Plan, confirm, back up, and execute the deletion.

    Returns a process exit code: 0 only if every requested username was
    deleted; 1 if any username was refused, the operator declined
    confirmation, or the backup failed.
    """
    approved, refusals = plan_deletions(repo, usernames)
    _print_plan(approved, refusals)

    if not approved:
        print("\nNo users eligible for deletion.")
        return 1 if refusals else 0

    if not yes:
        print(f"\nType {CONFIRM_WORD} to confirm deletion of {len(approved)} user(s):")
        answer = input("> ").strip()
        if answer != CONFIRM_WORD:
            print("Aborted: confirmation not received. No changes made.")
            return 1

    try:
        backup_path = backup_database(db_path)
    except OSError as exc:
        print(f"Backup failed ({exc}). Aborting without modifying the database.")
        return 1
    print(f"Backup created: {backup_path}")

    for username in approved:
        repo.delete_user(username)
        print(f"Deleted: {username}")

    print()
    _print_remaining_users(repo.list_users())

    return 1 if refusals else 0


def _load_settings() -> "Settings":
    """Import Settings exactly as the application does — no env stubbing.

    A broken or missing SESSION_SECRET (or any other required setting) must
    fail loudly here too, exactly as it does at server startup.
    """
    from backend.core.config import settings

    return settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="delete_users.py",
        description=(
            "Guarded one-shot maintenance script to permanently delete named "
            "users from the local SQLite user store."
        ),
        epilog=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "usernames", nargs="+", metavar="USERNAME", help="One or more usernames to delete"
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive confirmation prompt (for non-interactive use)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        settings = _load_settings()
    except Exception as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        print("Check your .env file (SESSION_SECRET and other required settings).", file=sys.stderr)
        return 1

    repo = SQLiteUserRepository(settings.USER_DB_PATH)
    return run_deletion(repo, settings.USER_DB_PATH, args.usernames, yes=args.yes)


if __name__ == "__main__":
    raise SystemExit(main())
