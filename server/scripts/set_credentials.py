"""Change any account's email or password from the command line.

The escape hatch for credentials, and for the platform owner it is the ONLY
hatch: every other account can be reached by an administrator of its company,
and the platform owner belongs to no company, so there is nobody above them to
do the resetting. Locking themselves out otherwise means editing the database
by hand.

    python scripts/set_credentials.py you@example.com --password
    python scripts/set_credentials.py you@example.com --email new@example.com
    python scripts/set_credentials.py you@example.com --email new@ex.com --password

Run it from the server/ directory with the venv interpreter:

    .\\.venv\\Scripts\\python.exe scripts\\set_credentials.py you@example.com --password

The password is prompted for, never passed as an argument — a password on the
command line ends up in shell history and in the process list. For a container
or a setup script there is `--password-stdin`.

Changing a password does NOT sign the account out of its other devices. Refresh
tokens live in `auth_sessions` and are checked independently, which is the right
default for a routine change and the wrong one after a compromise — for that,
sign out everywhere from the app afterwards.

Deployment note: this needs the database, not the API. Whoever can run it can
take over any account, which is the same thing as saying it must not be exposed.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

# Allows `python scripts/set_credentials.py` to import `app.*` without
# installing the package or setting PYTHONPATH by hand.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.security import hash_password  # noqa: E402
from app.db.session import SessionFactory  # noqa: E402
from app.repositories.user_repository import UserRepository  # noqa: E402

MIN_PASSWORD_LENGTH = 8


def _read_password(from_stdin: bool) -> str | None:
    """Prompt twice, or read one line from stdin. None if unusable."""
    if from_stdin:
        password = sys.stdin.readline().strip()
        if len(password) < MIN_PASSWORD_LENGTH:
            print(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
            return None
        return password

    first = getpass.getpass("New password: ")
    if len(first) < MIN_PASSWORD_LENGTH:
        print(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
        return None
    if first != getpass.getpass("Confirm new password: "):
        print("Passwords do not match.")
        return None
    return first


async def main(
    email: str,
    new_email: str | None,
    change_password: bool,
    from_stdin: bool,
    assume_yes: bool,
) -> int:
    if new_email is None and not change_password:
        print(
            "Nothing to do. Pass --password, --email NEW, or both."
        )
        return 1

    async with SessionFactory() as db:
        repo = UserRepository(db)
        user = await repo.find_by_email(email)
        if user is None:
            print(f"No user with email {email!r}.")
            return 1

        # Email is globally unique — one address is one account is one company
        # — so a clash has to be caught before anything is written, or the
        # password change lands and the email change fails on the constraint.
        if new_email is not None:
            normalised = repo.normalize_email(new_email)
            if normalised != repo.normalize_email(email):
                clash = await repo.find_by_email(normalised)
                if clash is not None:
                    print(
                        f"{normalised} already belongs to another account. "
                        "One email belongs to one account."
                    )
                    return 1

        print(f"  account  : {user.email}")
        print(f"  role     : {user.role.value}")
        print(f"  company  : {user.company_id or '(none — platform owner)'}")
        if new_email is not None:
            print(f"  new email: {repo.normalize_email(new_email)}")
        if change_password:
            print("  password : will be replaced")

        password: str | None = None
        if change_password:
            password = _read_password(from_stdin)
            if password is None:
                return 1

        if not assume_yes:
            answer = input("\nApply these changes? [y/N] ").strip().lower()
            if answer not in {"y", "yes"}:
                print("Aborted.")
                return 1

        # One update, so email and password never land half-applied.
        fields: dict[str, object] = {}
        if new_email is not None:
            fields["email"] = repo.normalize_email(new_email)
        if password is not None:
            fields["password_hash"] = hash_password(password)

        await repo.update(user, **fields)
        await db.commit()

        print(f"\nDone. Sign in with {user.email}.")
        if change_password:
            print(
                "Existing sessions on other devices are still valid — use "
                '"Sign out everywhere" in the app if that is not what you want.'
            )
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Change an account's email or password."
    )
    parser.add_argument("email", help="The account's CURRENT email.")
    parser.add_argument(
        "--email",
        dest="new_email",
        default=None,
        help="Change the sign-in address to this.",
    )
    parser.add_argument(
        "--password",
        action="store_true",
        help="Change the password (prompted for, not passed here).",
    )
    parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read the new password from stdin instead of prompting.",
    )
    parser.add_argument(
        "-y", "--yes", action="store_true", help="Skip the confirmation prompt."
    )
    args = parser.parse_args()

    raise SystemExit(
        asyncio.run(
            main(
                args.email,
                args.new_email,
                args.password or args.password_stdin,
                args.password_stdin,
                args.yes,
            )
        )
    )
