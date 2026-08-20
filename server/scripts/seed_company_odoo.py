"""Move the server's environment Odoo credentials into one company's own row.

A migration tool with a short life. The system began as a single deployment
whose Odoo lived in `ODOO_URL`/`ODOO_DB`/`ODOO_USERNAME`/`ODOO_API_KEY`, and
became a platform where each company connects to its own. This carries the
original deployment's credentials across, so the company that was there first
keeps working without anybody retyping an API key.

    python scripts/seed_company_odoo.py freshleaf --from-env
    python scripts/seed_company_odoo.py freshleaf --from-env --yes

Run it from the server/ directory with the venv interpreter:

    .\\.venv\\Scripts\\python.exe scripts\\seed_company_odoo.py freshleaf --from-env

Idempotent: running it twice replaces the row with the same values. It must run
BEFORE the environment fallback is removed from `resolve_credentials`, or the
original company loses its Odoo in the gap between the two changes.

Deployment note: this needs the database and the environment, not the API. It
reads a live credential and writes it back encrypted, so it is an operator tool
and must not be exposed.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Allows `python scripts/seed_company_odoo.py` to import `app.*` without
# installing the package or setting PYTHONPATH by hand.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import secrets  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.db.session import SessionFactory  # noqa: E402
from app.repositories.company_odoo_config_repository import (  # noqa: E402
    CompanyOdooConfigRepository,
)
from app.repositories.company_repository import CompanyRepository  # noqa: E402


def _mask(secret: str) -> str:
    """Enough to recognise a key, never enough to use one."""
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}{'*' * (len(secret) - 8)}{secret[-4:]}"


async def main(slug: str, assume_yes: bool) -> int:
    if not settings.is_odoo_configured:
        print(
            "The environment has no complete Odoo configuration.\n"
            "ODOO_URL, ODOO_DB, ODOO_USERNAME and ODOO_API_KEY must all be set "
            "for there to be anything to copy."
        )
        return 1

    if not secrets.is_configured():
        print(
            "SECRETS_ENCRYPTION_KEY is not set, so the API key cannot be "
            "encrypted.\nGenerate one with:\n"
            '    python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        )
        return 1

    api_key = settings.ODOO_API_KEY.get_secret_value()

    async with SessionFactory() as db:
        company = await CompanyRepository(db).find_by_slug(slug)
        if company is None:
            print(f"No company with slug {slug!r}.")
            return 1

        repo = CompanyOdooConfigRepository(db)
        existing = await repo.find_for_company(company.id)

        print(f"  company  : {company.name} ({company.slug})")
        print(f"  url      : {settings.odoo_base_url}")
        print(f"  database : {settings.ODOO_DB}")
        print(f"  login    : {settings.ODOO_USERNAME}")
        print(f"  api key  : {_mask(api_key)}")
        if existing is not None:
            print("\n  NOTE: this company already has a configuration. It will "
                  "be replaced.")

        if not assume_yes:
            answer = input("\nWrite this configuration? [y/N] ").strip().lower()
            if answer not in {"y", "yes"}:
                print("Aborted.")
                return 1

        await repo.upsert(
            company_id=company.id,
            base_url=settings.odoo_base_url,
            database=settings.ODOO_DB,
            username=settings.ODOO_USERNAME,
            api_key_encrypted=secrets.encrypt_secret(api_key),
            is_enabled=True,
        )
        await db.commit()

        print(
            f"\nDone. {company.name} now has its own Odoo configuration.\n"
            "It is stored unverified — test the connection from "
            "Admin -> Odoo, or it will simply prove itself on the next match."
        )
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Copy the environment's Odoo credentials to one company."
    )
    parser.add_argument("slug", help="The company's slug, e.g. freshleaf")
    parser.add_argument(
        "--from-env",
        action="store_true",
        required=True,
        help=(
            "Required, and the only source. Stated explicitly so the command "
            "says where the credentials come from."
        ),
    )
    parser.add_argument(
        "-y", "--yes", action="store_true", help="Skip the confirmation prompt."
    )
    args = parser.parse_args()

    raise SystemExit(asyncio.run(main(args.slug, args.yes)))
