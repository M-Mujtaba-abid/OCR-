"""Invoices must not cross the company boundary.

Phase B moved every invoice read onto `company_id`. These tests hold that line
where it matters most — the two places a leak would be silent:

  * `can_read_all` means "every invoice in YOUR company", not "every invoice"
  * a direct-upload key must sit under the caller's own storage prefix

Both are asserted against a real second company, created inside the test's
transaction and rolled back with it.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.company import Company
from app.models.match_history import InvoiceStatus, MatchHistory
from app.models.user import User, UserRole
from app.repositories.user_repository import UserRepository
from tests.conftest import auth_header, login

pytestmark = pytest.mark.asyncio

INVOICES = "/api/v1/invoices"


async def _token(client: AsyncClient, user: User, password: str) -> dict[str, str]:
    response = await login(client, user.email, password)
    assert response.status_code == 200, response.text
    return auth_header(response.json()["data"]["access_token"])


async def _rival_invoice(db: AsyncSession, password: str) -> MatchHistory:
    """An invoice belonging to a company the caller has nothing to do with."""
    rival = Company(name="Rivals", slug=f"rival-{uuid.uuid4().hex[:8]}")
    db.add(rival)
    await db.flush()

    owner = await UserRepository(db).create(
        company_id=rival.id,
        email=f"rival-{uuid.uuid4().hex[:12]}@example.com",
        password_hash=hash_password(password),
        role=UserRole.ADMIN,
    )
    invoice = MatchHistory(
        company_id=rival.id,
        uploaded_by=owner.id,
        file_name="rival-secret.pdf",
        file_key=f"invoices/{rival.slug}/2026-08/{uuid.uuid4().hex}_rival-secret.pdf",
        file_url="https://example.invalid/rival-secret.pdf",
        status=InvoiceStatus.PENDING_REVIEW,
        extracted_vendor="Rival Vendor Ltd",
    )
    db.add(invoice)
    await db.commit()
    return invoice


async def test_read_all_does_not_mean_read_everyones(
    client: AsyncClient, db: AsyncSession, admin_user: User, password: str
) -> None:
    """An administrator holds `invoice.read.all` — and it stops at their own
    company. Reading another company's invoice by id is the single worst thing
    a multi-company system can do, and it answers 404."""
    rival = await _rival_invoice(db, password)
    headers = await _token(client, admin_user, password)

    r = await client.get(f"{INVOICES}/{rival.id}", headers=headers)
    assert r.status_code == 404, r.text


async def test_the_file_of_another_company_cannot_be_signed_for(
    client: AsyncClient, db: AsyncSession, admin_user: User, password: str
) -> None:
    """The download endpoint mints a signed URL for a private object. If it
    resolved the invoice without the company check, the 404 above would be
    cosmetic — the file would still be one request away."""
    rival = await _rival_invoice(db, password)
    headers = await _token(client, admin_user, password)

    r = await client.get(f"{INVOICES}/{rival.id}/file", headers=headers)
    assert r.status_code == 404, r.text


async def test_the_queue_shows_only_your_own_company(
    client: AsyncClient, db: AsyncSession, admin_user: User, password: str
) -> None:
    rival = await _rival_invoice(db, password)
    headers = await _token(client, admin_user, password)

    r = await client.get(f"{INVOICES}/admin/queue?page_size=100", headers=headers)

    assert r.status_code == 200, r.text
    ids = {row["id"] for row in r.json()["data"]["items"]}
    assert str(rival.id) not in ids


async def test_stats_count_only_your_own_company(
    client: AsyncClient, db: AsyncSession, admin_user: User, password: str
) -> None:
    """The dashboard number and the table under it have to agree. They are two
    different queries, so they are two different chances to forget the scope."""
    headers = await _token(client, admin_user, password)
    before = (await client.get(f"{INVOICES}/admin/stats", headers=headers)).json()

    await _rival_invoice(db, password)

    after = (await client.get(f"{INVOICES}/admin/stats", headers=headers)).json()
    assert after["data"]["total"] == before["data"]["total"]


async def test_registering_a_key_outside_your_prefix_is_refused(
    client: AsyncClient, db: AsyncSession, admin_user: User, password: str
) -> None:
    """The direct-upload boundary.

    The browser uploads straight to storage and then reports the key. A crafted
    key naming another company's prefix must be refused — otherwise it attaches
    somebody else's private object to a row in this company, and the file
    endpoint would happily sign a URL for it afterwards.
    """
    rival = await _rival_invoice(db, password)
    headers = await _token(client, admin_user, password)

    r = await client.post(
        f"{INVOICES}/register",
        headers=headers,
        json={"files": [{"key": rival.file_key, "file_name": "rival-secret.pdf"}]},
    )

    # Refused as a client fault, not a crash: the request was well formed, the
    # object simply was not this caller's to claim. Every file in it was
    # rejected, which the endpoint reports as NO_VALID_FILES.
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "NO_VALID_FILES"

    # And nothing was written. The refusal has to leave no row behind, or the
    # rival's object is attached to this company anyway.
    listed = await client.get(f"{INVOICES}/admin/queue?page_size=100", headers=headers)
    keys = {row["file_name"] for row in listed.json()["data"]["items"]}
    assert "rival-secret.pdf" not in keys
