"""Set a user's role from the command line.

Solves the bootstrap problem: registration always creates a MEMBER, and only an
admin may promote anyone — so a fresh database has no way to produce its first
administrator over HTTP. That is the correct design (a self-service "make me an
admin" endpoint would be a privilege-escalation hole), and this script is the
deliberate out-of-band escape hatch.

    python scripts/set_role.py you@example.com admin
    python scripts/set_role.py you@example.com member --yes

Run it from the server/ directory with the venv interpreter:

    .\\.venv\\Scripts\\python.exe scripts\\set_role.py you@example.com admin

Deployment note: this needs the database, not the API, so it is an operator
tool. Do not expose it — and on a real deployment, the fact that someone can
run it is equivalent to handing them the admin account.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Allows `python scripts/set_role.py` to import `app.*` without installing the
# package or setting PYTHONPATH by hand.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import SessionFactory  # noqa: E402
from app.models.user import UserRole  # noqa: E402
from app.repositories.user_repository import UserRepository  # noqa: E402


async def main(email: str, role: UserRole, assume_yes: bool) -> int:
    async with SessionFactory() as db:
        repo = UserRepository(db)
        user = await repo.find_by_email(email)

        if user is None:
            print(f"No user with email {email!r}. Register the account first.")
            return 1

        if user.role is role:
            print(f"{user.email} is already {role.value}. Nothing to do.")
            return 0

        print(f"  user : {user.email}")
        print(f"  from : {user.role.value}")
        print(f"  to   : {role.value}")

        if not assume_yes:
            answer = input("Apply this change? [y/N] ").strip().lower()
            if answer not in {"y", "yes"}:
                print("Aborted.")
                return 1

        await repo.update(user, role=role)
        await db.commit()
        print(f"Done. {user.email} is now {role.value}.")
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Set a user's role.")
    parser.add_argument("email")
    parser.add_argument("role", choices=[r.value for r in UserRole])
    parser.add_argument(
        "-y", "--yes", action="store_true", help="Skip the confirmation prompt."
    )
    args = parser.parse_args()

    raise SystemExit(
        asyncio.run(main(args.email, UserRole(args.role), args.yes))
    )
