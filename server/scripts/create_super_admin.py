"""Create the platform owner from the command line.

Solves a bootstrap problem that has no HTTP answer. Every account is created by
an administrator of the company it joins, and the platform owner belongs to no
company — so there is no company, and no administrator, who could create the
first one. An endpoint that let anybody claim the role would be the worst
privilege-escalation hole in the system, so this is the deliberate out-of-band
escape hatch.

    python scripts/create_super_admin.py you@example.com
    python scripts/create_super_admin.py you@example.com --name "Mujtaba"

For a container or a setup script, where there is no console to type into:

    echo "the-password" | python scripts/create_super_admin.py you@ex.com --password-stdin

Run it from the server/ directory with the venv interpreter:

    .\\.venv\\Scripts\\python.exe scripts\\create_super_admin.py you@example.com

The password is prompted for, never passed as an argument — a password on the
command line ends up in shell history and in the process list.

Deployment note: this needs the database, not the API. Whoever can run it owns
the platform, which is the same thing as saying it must not be exposed.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

# Allows `python scripts/create_super_admin.py` to import `app.*` without
# installing the package or setting PYTHONPATH by hand.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.security import hash_password  # noqa: E402
from app.db.session import SessionFactory  # noqa: E402
from app.models.user import UserRole  # noqa: E402
from app.repositories.user_repository import UserRepository  # noqa: E402

MIN_PASSWORD_LENGTH = 8


def _read_password(from_stdin: bool) -> str | None:
    """Prompt twice, or read one line from stdin. None if unusable.

    `getpass` reads the console directly rather than stdin — on Windows via
    msvcrt — so a piped password never reaches it and the script simply hangs
    waiting for a keystroke nobody is there to press. `--password-stdin` is the
    explicit path for that, and it is opt-in so an interactive run cannot
    silently take a password from a redirected file.
    """
    if from_stdin:
        password = sys.stdin.readline().strip()
        if len(password) < MIN_PASSWORD_LENGTH:
            print(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
            return None
        return password

    first = getpass.getpass("Password: ")
    if len(first) < MIN_PASSWORD_LENGTH:
        print(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
        return None
    if first != getpass.getpass("Confirm password: "):
        print("Passwords do not match.")
        return None
    return first


async def main(email: str, full_name: str | None, from_stdin: bool) -> int:
    async with SessionFactory() as db:
        repo = UserRepository(db)

        existing = await repo.find_by_email(email)
        if existing is not None:
            # Deliberately not "promote them instead". A company's admin holds
            # a company_id, and promoting them in place would leave a platform
            # owner sitting inside a company — an account that is neither
            # cleanly one thing nor the other.
            print(
                f"{email} already has an account (role: {existing.role.value}).\n"
                "The platform owner must be a NEW account with no company. "
                "Use a different address."
            )
            return 1

        password = _read_password(from_stdin)
        if password is None:
            return 1

        user = await repo.create(
            # No company, and that is the whole point: the platform owner sits
            # outside the companies rather than in one. The database's check
            # constraint permits null here for this role and no other.
            company_id=None,
            email=email,
            password_hash=hash_password(password),
            full_name=full_name,
            role=UserRole.SUPER_ADMIN,
            is_active=True,
            is_verified=True,
        )
        await db.commit()

        print(f"\nPlatform owner created: {user.email}")
        print(f"  id      : {user.id}")
        print(f"  role    : {user.role.value}")
        print("  company : (none — this account belongs to no company)")
        print("\nSign in and go to /platform to create your first company.")
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Create the platform owner (super admin)."
    )
    parser.add_argument("email")
    parser.add_argument("--name", default=None, help="Full name, optional.")
    parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read the password from stdin instead of prompting (for scripts).",
    )
    args = parser.parse_args()

    raise SystemExit(
        asyncio.run(main(args.email, args.name, args.password_stdin))
    )
