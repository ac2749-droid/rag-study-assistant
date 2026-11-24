"""Manage accounts from the command line (useful before anyone has signed in, or if a password is lost).

    python -m app.users add alice            # asks for a password
    python -m app.users add bob --admin
    python -m app.users list
    python -m app.users passwd alice
    python -m app.users disable alice        # and: enable
    python -m app.users add ci --password-env CI_PASSWORD     # for scripts: read the password from a variable
"""
import argparse
import getpass
import os
import sys

from app import auth


def _password(args: argparse.Namespace) -> str:
    if args.password_env:
        value = os.environ.get(args.password_env, "")
        if not value:
            sys.exit(f"The environment variable {args.password_env} is empty or not set.")
        return value
    first = getpass.getpass("Password: ")
    if first != getpass.getpass("Repeat the password: "):
        sys.exit("The two passwords don't match.")
    return first


def _find(username: str) -> auth.User:
    user = auth.get_user_by_name(username)
    if user is None:
        sys.exit(f"There is no account called {username!r}.")
    return user


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.users", description="Manage accounts.")
    commands = parser.add_subparsers(dest="command", required=True)

    add = commands.add_parser("add", help="create an account")
    add.add_argument("username")
    add.add_argument("--admin", action="store_true", help="make it an administrator")
    add.add_argument("--password-env", help="read the password from this environment variable")

    commands.add_parser("list", help="list accounts")

    passwd = commands.add_parser("passwd", help="set a new password (also signs that account out everywhere)")
    passwd.add_argument("username")
    passwd.add_argument("--password-env", help="read the password from this environment variable")

    for name in ("disable", "enable"):
        commands.add_parser(name, help=f"{name} an account").add_argument("username")

    args = parser.parse_args(argv)
    try:
        if args.command == "add":
            user = auth.create_user(args.username, _password(args), "admin" if args.admin else "user")
            auth.record("user_created", username="command line", detail=f"{user.username} ({user.role})")
            print(f"Created {user.role} account {user.username!r}.")
        elif args.command == "list":
            for user in auth.list_users():
                state = "disabled" if user.disabled else "active"
                print(f"{user.username:<20} {user.role:<6} {state}")
        elif args.command == "passwd":
            user = _find(args.username)
            auth.set_password(user.id, _password(args))
            auth.record("password_changed", username="command line", detail=user.username)
            print(f"Password changed for {user.username!r}; they were signed out everywhere.")
        else:
            user = _find(args.username)
            auth.set_disabled(user.id, args.command == "disable")
            auth.record("user_updated", username="command line", detail=f"{user.username}: {args.command}d")
            print(f"{user.username!r} is now {'disabled' if args.command == 'disable' else 'enabled'}.")
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
