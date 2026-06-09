#!/usr/bin/env python
"""User management CLI for LaVerde ERP AI Engine.

Usage:
  python scripts/manage_users.py add <username> <password> --modules m1,m2 [--admin]
  python scripts/manage_users.py list
  python scripts/manage_users.py set-modules <username> m1,m2
  python scripts/manage_users.py deactivate <username>

Module IDs: crm, hr, collections, customer_accounts  (use * for all modules)
"""

import argparse
import os
import sys
from pathlib import Path

# Ensure project root is on sys.path so backend packages can be imported.
sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("USER_DB_PATH", "data/users.db")
os.environ.setdefault("SESSION_SECRET", "dev-placeholder-not-used-by-cli")

from backend.auth.password import hash_password
from backend.auth.repository import SQLiteUserRepository
from backend.core.config import settings


def _get_repo() -> SQLiteUserRepository:
    return SQLiteUserRepository(settings.USER_DB_PATH)


def cmd_add(args: argparse.Namespace) -> None:
    repo = _get_repo()
    raw = args.modules.strip()
    modules: list[str] = [m.strip() for m in raw.split(",") if m.strip()] if raw else []
    try:
        user = repo.create_user(
            username=args.username,
            password_hash=hash_password(args.password),
            modules=modules,
            is_admin=args.admin,
            is_active=True,
        )
        print(f"Created user '{user.username}'  modules={user.modules}  is_admin={user.is_admin}")
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_list(args: argparse.Namespace) -> None:
    repo = _get_repo()
    users = repo.list_users()
    if not users:
        print("No users found.")
        return
    fmt = "{:<24} {:<8} {:<8} {}"
    print(fmt.format("Username", "Admin", "Active", "Modules"))
    print("-" * 64)
    for u in users:
        print(fmt.format(u.username, str(u.is_admin), str(u.is_active), str(u.modules)))


def cmd_set_modules(args: argparse.Namespace) -> None:
    repo = _get_repo()
    raw = args.modules.strip()
    modules: list[str] = [m.strip() for m in raw.split(",") if m.strip()] if raw else []
    try:
        repo.update_user(args.username, modules=modules)
        print(f"Updated '{args.username}'  modules → {modules}")
    except KeyError:
        print(f"Error: user '{args.username}' not found.", file=sys.stderr)
        sys.exit(1)


def cmd_deactivate(args: argparse.Namespace) -> None:
    repo = _get_repo()
    try:
        repo.update_user(args.username, is_active=False)
        print(f"Deactivated '{args.username}'.")
    except KeyError:
        print(f"Error: user '{args.username}' not found.", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="LaVerde ERP user management CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # add
    p_add = sub.add_parser("add", help="Create a new user")
    p_add.add_argument("username", help="Login username")
    p_add.add_argument("password", help="Plain-text password (hashed before storage)")
    p_add.add_argument(
        "--modules", default="",
        help="Comma-separated module IDs, e.g. hr,crm  — use * for all modules",
    )
    p_add.add_argument("--admin", action="store_true", help="Set is_admin=True")
    p_add.set_defaults(func=cmd_add)

    # list
    p_list = sub.add_parser("list", help="List all users")
    p_list.set_defaults(func=cmd_list)

    # set-modules
    p_sm = sub.add_parser("set-modules", help="Replace a user's module list")
    p_sm.add_argument("username", help="Target username")
    p_sm.add_argument("modules", help="Comma-separated module IDs, e.g. hr,crm  — use * for all")
    p_sm.set_defaults(func=cmd_set_modules)

    # deactivate
    p_deact = sub.add_parser("deactivate", help="Deactivate a user account")
    p_deact.add_argument("username", help="Target username")
    p_deact.set_defaults(func=cmd_deactivate)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
