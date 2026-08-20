"""Where one role stops and the next begins.

The manager role existed with the permissions of an admin and the screens of a
member — it held `invoice.read.all` and could approve, and nothing in the UI
ever offered either. Fixing that made the question "what is a manager allowed
to do" load-bearing, so it is answered here rather than in a comment.

The split that matters: a manager gets an invoice READY, an administrator BILLS
it. `invoice.review` and `invoice.bill` are separate permissions because they
are separate decisions, and the second one is where money leaves.

These assert the boundary, not the pipeline. A manager reaching `/match` gets
past the permission and then fails on Odoo or on the invoice's state — that is
the route accepting them, which is what is being tested. Only the 403s are
about authorisation.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.match_history import InvoiceStatus, MatchHistory
from app.models.user import User
from tests.conftest import auth_header, login

pytestmark = pytest.mark.asyncio

INVOICES = "/api/v1/invoices"

#: A permission failure is 403 and nothing else. Anything past the guard is a
#: 4xx/5xx about the invoice or about Odoo, and those all mean "allowed in".
FORBIDDEN = 403


async def _token(client: AsyncClient, user: User, password: str) -> dict[str, str]:
    response = await login(client, user.email, password)
    assert response.status_code == 200, response.text
    return auth_header(response.json()["data"]["access_token"])


async def _invoice(db: AsyncSession, owner: User) -> MatchHistory:
    """An invoice in the fixture company, ready enough to be acted on."""
    assert owner.company_id is not None
    invoice = MatchHistory(
        company_id=owner.company_id,
        uploaded_by=owner.id,
        file_name="boundary.pdf",
        file_key=f"invoices/test/2026-08/{uuid.uuid4().hex}_boundary.pdf",
        file_url="https://example.invalid/boundary.pdf",
        status=InvoiceStatus.PENDING_REVIEW,
    )
    db.add(invoice)
    await db.commit()
    return invoice


class TestManagerCanReview:
    """Everything a manager is supposed to be able to reach."""

    async def test_a_manager_sees_the_whole_company_queue(
        self, client: AsyncClient, manager_user: User, password: str
    ) -> None:
        """The screen a manager was missing entirely."""
        headers = await _token(client, manager_user, password)
        r = await client.get(f"{INVOICES}/admin/queue", headers=headers)
        assert r.status_code == 200, r.text

    async def test_a_manager_sees_company_wide_stats_and_history(
        self, client: AsyncClient, manager_user: User, password: str
    ) -> None:
        headers = await _token(client, manager_user, password)
        assert (
            await client.get(f"{INVOICES}/admin/stats", headers=headers)
        ).status_code == 200
        assert (
            await client.get(f"{INVOICES}/admin/bills", headers=headers)
        ).status_code == 200

    @pytest.mark.parametrize("action", ["match", "confirm", "reject", "create-po"])
    async def test_a_manager_is_admitted_to_every_review_action(
        self,
        client: AsyncClient,
        db: AsyncSession,
        manager_user: User,
        password: str,
        action: str,
    ) -> None:
        """Past the guard. What happens next is Odoo's business or the
        invoice's — a 409 for an invoice that has not been read yet is the
        route working, and only a 403 would mean the manager was refused."""
        invoice = await _invoice(db, manager_user)
        headers = await _token(client, manager_user, password)

        r = await client.post(
            f"{INVOICES}/{invoice.id}/{action}",
            headers=headers,
            json={"po_id": 1, "reason": "not ours"},
        )

        assert r.status_code != FORBIDDEN, f"{action} refused: {r.text}"

    async def test_a_manager_may_read_the_bill_preview(
        self, client: AsyncClient, db: AsyncSession, manager_user: User, password: str
    ) -> None:
        """Reading what a bill WOULD be is review work — seeing the figures is
        how a reviewer checks them. Creating it is the part they cannot do."""
        invoice = await _invoice(db, manager_user)
        headers = await _token(client, manager_user, password)

        r = await client.get(f"{INVOICES}/{invoice.id}/bill-preview", headers=headers)

        assert r.status_code != FORBIDDEN, r.text


class TestOnlyAnAdminCanBill:
    """The one step that separates the two roles."""

    async def test_a_manager_cannot_create_a_vendor_bill(
        self, client: AsyncClient, db: AsyncSession, manager_user: User, password: str
    ) -> None:
        """THE test for this change.

        A manager can do every other thing to an invoice. This is the step
        where a vendor gets paid, and it is refused — so the person who
        reviewed an invoice is not also the person who commits to paying it.
        """
        invoice = await _invoice(db, manager_user)
        headers = await _token(client, manager_user, password)

        r = await client.post(
            f"{INVOICES}/{invoice.id}/create-bill",
            headers=headers,
            json={
                "po_id": 1,
                "ref": "INV-1",
                "invoice_date": "2026-08-20",
                "lines": [{"po_line_id": 1, "quantity": 1}],
                "receive_goods": False,
                "attach_document": False,
            },
        )

        assert r.status_code == FORBIDDEN, r.text
        assert r.json()["error"]["code"] == "INSUFFICIENT_PERMISSION"

    async def test_an_admin_is_admitted_to_billing(
        self, client: AsyncClient, db: AsyncSession, admin_user: User, password: str
    ) -> None:
        """The other side of it: the permission exists and an admin holds it.

        Without this, the test above would still pass if `invoice.bill` were
        granted to nobody at all.
        """
        invoice = await _invoice(db, admin_user)
        headers = await _token(client, admin_user, password)

        r = await client.post(
            f"{INVOICES}/{invoice.id}/create-bill",
            headers=headers,
            json={
                "po_id": 1,
                "ref": "INV-1",
                "invoice_date": "2026-08-20",
                "lines": [{"po_line_id": 1, "quantity": 1}],
                "receive_goods": False,
                "attach_document": False,
            },
        )

        assert r.status_code != FORBIDDEN, r.text


class TestManagerCannotAdminister:
    """What stays with the administrator."""

    async def test_a_manager_may_read_the_team_but_not_change_it(
        self, client: AsyncClient, manager_user: User, existing_user: User,
        password: str,
    ) -> None:
        """`user.read` without `user.create` or `user.update` — the directory
        is visible, the controls are not."""
        headers = await _token(client, manager_user, password)

        assert (await client.get("/api/v1/users", headers=headers)).status_code == 200

        created = await client.post(
            "/api/v1/users",
            headers=headers,
            json={
                "email": f"x-{uuid.uuid4().hex[:10]}@example.com",
                "password": password,
            },
        )
        assert created.status_code == FORBIDDEN

        promoted = await client.patch(
            f"/api/v1/users/{existing_user.id}/role",
            headers=headers,
            json={"role": "admin"},
        )
        assert promoted.status_code == FORBIDDEN

    async def test_a_manager_cannot_touch_the_odoo_connection(
        self, client: AsyncClient, manager_user: User, password: str
    ) -> None:
        """Credentials are an administrator's business — `system.admin`."""
        headers = await _token(client, manager_user, password)

        assert (
            await client.get("/api/v1/company/odoo", headers=headers)
        ).status_code == FORBIDDEN
        assert (
            await client.delete("/api/v1/company/odoo", headers=headers)
        ).status_code == FORBIDDEN


class TestMemberIsUnchanged:
    """The role below, so widening the manager did not widen everyone."""

    async def test_a_member_still_sees_only_their_own_uploads(
        self, client: AsyncClient, existing_user: User, password: str
    ) -> None:
        headers = await _token(client, existing_user, password)

        assert (await client.get(f"{INVOICES}/my", headers=headers)).status_code == 200
        assert (
            await client.get(f"{INVOICES}/admin/queue", headers=headers)
        ).status_code == FORBIDDEN

    async def test_a_member_cannot_review_or_bill(
        self, client: AsyncClient, db: AsyncSession, existing_user: User,
        password: str,
    ) -> None:
        invoice = await _invoice(db, existing_user)
        headers = await _token(client, existing_user, password)

        assert (
            await client.post(f"{INVOICES}/{invoice.id}/match", headers=headers)
        ).status_code == FORBIDDEN
        assert (
            await client.post(
                f"{INVOICES}/{invoice.id}/create-bill",
                headers=headers,
                json={
                    "po_id": 1,
                    "ref": "INV-1",
                    "invoice_date": "2026-08-20",
                    "lines": [{"po_line_id": 1, "quantity": 1}],
                    "receive_goods": False,
                    "attach_document": False,
                },
            )
        ).status_code == FORBIDDEN
