"""Approval chains must not cross the company boundary.

There is no row-level security in this database and no session filter — the
tenant boundary is `company_id` in a WHERE clause, written out by hand in every
repository method. A new method that forgets it compiles, runs, and leaks, so
the four new tables get the same adversarial treatment `test_invoice_isolation`
gives invoices: a real second company, created inside the test's transaction and
rolled back with it.

An approval chain is a worse leak than most. It names people — who signs off
what, at which step — so a listing that crossed companies would hand one
business a map of another's internal controls.

404 rather than 403 throughout, matching the rest of the system. A 403 confirms
the id is real, which is the one thing a probe is looking for.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.approval import ApprovalRequest
from app.models.company import Company
from app.models.match_history import InvoiceStatus, MatchHistory
from app.models.user import User, UserRole
from app.repositories.user_repository import UserRepository
from app.services.approval_service import ApprovalService
from tests.conftest import auth_header, login

pytestmark = pytest.mark.asyncio

APPROVALS = "/api/v1/approvals"
INVOICES = "/api/v1/invoices"


async def _token(client: AsyncClient, user: User, password: str) -> dict[str, str]:
    response = await login(client, user.email, password)
    assert response.status_code == 200, response.text
    return auth_header(response.json()["data"]["access_token"])


async def _rival(db: AsyncSession, password: str) -> dict[str, Any]:
    """A whole second company: an admin, an invoice, an active chain, and a
    request already running through it."""
    company = Company(name="Rivals", slug=f"rival-{uuid.uuid4().hex[:8]}")
    db.add(company)
    await db.flush()

    owner = await UserRepository(db).create(
        company_id=company.id,
        email=f"rival-admin-{uuid.uuid4().hex[:12]}@example.com",
        password_hash=hash_password(password),
        role=UserRole.ADMIN,
    )
    approver = await UserRepository(db).create(
        company_id=company.id,
        email=f"rival-approver-{uuid.uuid4().hex[:12]}@example.com",
        password_hash=hash_password(password),
        role=UserRole.MANAGER,
    )
    invoice = MatchHistory(
        company_id=company.id,
        uploaded_by=owner.id,
        file_name="rival-secret.pdf",
        file_key=f"invoices/{company.slug}/2026-08/{uuid.uuid4().hex}_rival.pdf",
        file_url="https://example.invalid/rival-secret.pdf",
        status=InvoiceStatus.CONFIRMED,
        extracted_vendor="Rival Vendor Ltd",
        extracted_total=999.0,
        extracted_json={"vendor_name": "Rival Vendor Ltd", "items": []},
    )
    db.add(invoice)
    await db.flush()

    service = ApprovalService(db)
    chain = await service.save_chain(
        company_id=company.id,
        chain_id=None,
        name="Rival controls",
        allow_self_approval=False,
        steps=[{"name": "Rival receiving", "approver_user_ids": [str(approver.id)]}],
        is_active=True,
    )
    request = await service.request_approval(
        invoice=invoice,
        requested_by=owner.id,
        lines=[
            {
                "po_line_id": 99,
                "quantity": 5.0,
                "description": "Rival goods",
                "unit_price": 100.0,
                "tax_rate": 0.0,
            }
        ],
    )
    await db.commit()
    return {
        "company": company,
        "invoice": invoice,
        "chain": chain,
        "request": request,
        "approver": approver,
    }


async def test_the_chain_listing_stops_at_your_own_company(
    client: AsyncClient, db: AsyncSession, admin_user: User, password: str
) -> None:
    """A chain names who signs off what. Listing another company's would hand
    one business a map of another's internal controls."""
    rival = await _rival(db, password)
    headers = await _token(client, admin_user, password)

    response = await client.get(f"{APPROVALS}/chains", headers=headers)
    assert response.status_code == 200, response.text
    ids = {chain["id"] for chain in response.json()["data"]}
    assert str(rival["chain"].id) not in ids
    assert "Rival controls" not in response.text


async def test_another_companys_chain_cannot_be_activated(
    client: AsyncClient, db: AsyncSession, admin_user: User, password: str
) -> None:
    """Activating a rival's chain would gate their billing from outside their
    business — a write across the boundary, not merely a read."""
    rival = await _rival(db, password)
    headers = await _token(client, admin_user, password)

    response = await client.post(
        f"{APPROVALS}/chains/{rival['chain'].id}/deactivate", headers=headers
    )
    assert response.status_code == 404, response.text


async def test_another_companys_chain_cannot_be_rewritten(
    client: AsyncClient,
    db: AsyncSession,
    admin_user: User,
    manager_user: User,
    password: str,
) -> None:
    rival = await _rival(db, password)
    headers = await _token(client, admin_user, password)

    response = await client.put(
        f"{APPROVALS}/chains/{rival['chain'].id}",
        headers=headers,
        json={
            "name": "Mine now",
            "steps": [
                {"name": "Me", "approver_user_ids": [str(manager_user.id)]}
            ],
        },
    )
    assert response.status_code == 404, response.text

    # And nothing was written on the way to refusing.
    await db.refresh(rival["chain"])
    assert rival["chain"].name == "Rival controls"


async def test_another_companys_chain_cannot_be_deleted(
    client: AsyncClient, db: AsyncSession, admin_user: User, password: str
) -> None:
    """Deleting a rival's policy from outside their business is a write across
    the boundary, and one that would take their controls off with it."""
    rival = await _rival(db, password)
    headers = await _token(client, admin_user, password)

    response = await client.delete(
        f"{APPROVALS}/chains/{rival['chain'].id}", headers=headers
    )
    assert response.status_code == 404, response.text

    await db.refresh(rival["chain"])
    assert rival["chain"].name == "Rival controls"


async def test_another_companys_request_cannot_be_decided(
    client: AsyncClient, db: AsyncSession, admin_user: User, password: str
) -> None:
    """The worst of them: approving a payment inside a business you do not work
    for. 404, so the refusal does not even confirm the request exists."""
    rival = await _rival(db, password)
    headers = await _token(client, admin_user, password)

    response = await client.post(
        f"{APPROVALS}/{rival['request'].id}/decide",
        headers=headers,
        json={"approve": True, "reason": None},
    )
    assert response.status_code == 404, response.text

    refreshed = await db.get(ApprovalRequest, rival["request"].id)
    assert refreshed is not None
    assert refreshed.status.value == "pending"
    assert refreshed.current_position == 1


async def test_another_companys_request_cannot_be_cancelled(
    client: AsyncClient, db: AsyncSession, admin_user: User, password: str
) -> None:
    """Cancelling is the escape hatch. Reachable across the boundary it would be
    a way to strip another company's controls off their invoice."""
    rival = await _rival(db, password)
    headers = await _token(client, admin_user, password)

    response = await client.post(
        f"{APPROVALS}/{rival['request'].id}/cancel",
        headers=headers,
        json={"reason": "Not mine to cancel"},
    )
    assert response.status_code == 404, response.text

    refreshed = await db.get(ApprovalRequest, rival["request"].id)
    assert refreshed is not None
    assert refreshed.status.value == "pending"


async def test_the_awaiting_queue_never_reaches_across_companies(
    client: AsyncClient, db: AsyncSession, admin_user: User, password: str
) -> None:
    """The queue is filtered by the request's step snapshot AND by company. The
    company half is what stops a shared id from ever mattering."""
    rival = await _rival(db, password)
    headers = await _token(client, admin_user, password)

    response = await client.get(f"{APPROVALS}/awaiting-me", headers=headers)
    assert response.status_code == 200, response.text
    assert str(rival["request"].id) not in response.text
    assert "rival-secret.pdf" not in response.text


async def test_another_companys_invoice_cannot_be_sent_for_approval(
    client: AsyncClient, db: AsyncSession, admin_user: User, password: str
) -> None:
    """Starting a chain reads the invoice first, so this is `get_for_user`
    holding the line — the same 404 every other invoice read gives."""
    rival = await _rival(db, password)
    headers = await _token(client, admin_user, password)

    response = await client.post(
        f"{INVOICES}/{rival['invoice'].id}/request-approval",
        headers=headers,
        json={"po_id": 4242, "lines": [{"po_line_id": 99, "quantity": 1.0}]},
    )
    assert response.status_code == 404, response.text


async def test_another_companys_approval_progress_is_not_readable(
    client: AsyncClient, db: AsyncSession, admin_user: User, password: str
) -> None:
    rival = await _rival(db, password)
    headers = await _token(client, admin_user, password)

    response = await client.get(
        f"{INVOICES}/{rival['invoice'].id}/approval", headers=headers
    )
    assert response.status_code == 404, response.text


async def test_an_active_chain_in_one_company_does_not_gate_another(
    client: AsyncClient, db: AsyncSession, admin_user: User, password: str
) -> None:
    """The mirror image of a leak, and just as bad: a rival's controls must not
    start refusing THIS company's bills. `active_chain` is company-scoped, so a
    second company switching approvals on changes nothing here."""
    await _rival(db, password)
    headers = await _token(client, admin_user, password)

    assert admin_user.company_id is not None
    invoice = MatchHistory(
        company_id=admin_user.company_id,
        uploaded_by=admin_user.id,
        file_name="ours.pdf",
        file_key=f"invoices/test/2026-08/{uuid.uuid4().hex}_ours.pdf",
        file_url="https://example.invalid/ours.pdf",
        status=InvoiceStatus.CONFIRMED,
        matched_po_id=4242,
        final_po_id=4242,
        extracted_json={"vendor_name": "Ours", "items": []},
    )
    db.add(invoice)
    await db.commit()

    response = await client.post(
        f"{INVOICES}/{invoice.id}/create-bill",
        headers=headers,
        json={
            "po_id": 4242,
            "lines": [{"po_line_id": 10, "quantity": 1.0}],
            "receive_goods": False,
            "attach_document": False,
        },
    )
    # It still fails — there is no Odoo here — but it must fail past the gate.
    code = response.json().get("error", {}).get("code")
    assert code not in {"APPROVAL_REQUIRED", "APPROVAL_PENDING"}, response.text
