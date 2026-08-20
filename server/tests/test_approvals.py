"""Approval chains, end to end against a real database.

The interesting half of this feature is rows and constraints — who may decide a
rung, what a decline does to the invoice, and whether a bill can get past a
chain that has not finished — so it is tested here rather than against fakes.
The pure judgements live in `tests/unit/test_approval_rules.py`.

Requests are created through `ApprovalService` rather than over HTTP, because
the HTTP route prices its lines against Odoo first and there is no Odoo here.
Everything after that — deciding, declining, the billing gate — goes through the
API, which is where the authorisation actually lives.

One thing is deliberately NOT covered here: `check_exceeds_approval` firing
inside `create_bill_for_invoice`. Reaching it needs an approved request AND a
live Odoo, since it sits after the order is re-read. Its logic is proved against
literals in the unit tests.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.approval import (
    ApprovalDecisionRecord,
    ApprovalRequest,
    ApprovalRequestStatus,
)
from app.models.match_history import InvoiceStatus, MatchHistory
from app.models.user import User
from app.services.approval_service import ApprovalService
from tests.conftest import auth_header, login

pytestmark = pytest.mark.asyncio

APPROVALS = "/api/v1/approvals"
INVOICES = "/api/v1/invoices"

PO_ID = 4242
LINES: list[dict[str, Any]] = [
    {
        "po_line_id": 10,
        "quantity": 50.0,
        "description": "Widget A",
        "unit_price": 10.0,
        "tax_rate": 0.05,
    }
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _token(client: AsyncClient, user: User, password: str) -> dict[str, str]:
    response = await login(client, user.email, password)
    assert response.status_code == 200, response.text
    return auth_header(response.json()["data"]["access_token"])


async def _invoice(
    db: AsyncSession,
    owner: User,
    *,
    status: InvoiceStatus = InvoiceStatus.CONFIRMED,
) -> MatchHistory:
    """An invoice ready to be sent for approval.

    CONFIRMED by default rather than PENDING_REVIEW, because that is the honest
    state of one somebody is asking to bill — and it is what makes the
    "restores the status it arrived with" test mean something.
    """
    assert owner.company_id is not None
    invoice = MatchHistory(
        company_id=owner.company_id,
        uploaded_by=owner.id,
        file_name="approval.pdf",
        file_key=f"invoices/test/2026-08/{uuid.uuid4().hex}_approval.pdf",
        file_url="https://example.invalid/approval.pdf",
        status=status,
        matched_po_id=PO_ID,
        final_po_id=PO_ID,
        extracted_total=525.0,
        extracted_json={"vendor_name": "Acme", "items": []},
    )
    db.add(invoice)
    await db.commit()
    return invoice


async def _make_chain(
    client: AsyncClient,
    headers: dict[str, str],
    steps: list[tuple[str, list[User]]],
    *,
    active: bool = True,
    allow_self_approval: bool = False,
) -> dict[str, Any]:
    response = await client.post(
        f"{APPROVALS}/chains",
        headers=headers,
        json={
            "name": f"Chain {uuid.uuid4().hex[:6]}",
            "allow_self_approval": allow_self_approval,
            "is_active": active,
            "steps": [
                {"name": name, "approver_user_ids": [str(u.id) for u in users]}
                for name, users in steps
            ],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def _start(
    db: AsyncSession, invoice: MatchHistory, requester: User
) -> ApprovalRequest:
    request = await ApprovalService(db).request_approval(
        invoice=invoice, requested_by=requester.id, lines=LINES
    )
    await db.commit()
    return request


async def _decide(
    client: AsyncClient,
    headers: dict[str, str],
    request_id: uuid.UUID,
    *,
    approve: bool,
    reason: str | None = None,
):
    return await client.post(
        f"{APPROVALS}/{request_id}/decide",
        headers=headers,
        json={"approve": approve, "reason": reason},
    )


# ---------------------------------------------------------------------------
# Configuring the policy
# ---------------------------------------------------------------------------
class TestChainConfiguration:
    async def test_a_manager_cannot_write_the_approval_policy(
        self, client: AsyncClient, manager_user: User, password: str
    ) -> None:
        """Deciding a rung is not a permission; WRITING the rungs is."""
        headers = await _token(client, manager_user, password)
        response = await client.get(f"{APPROVALS}/chains", headers=headers)
        assert response.status_code == 403, response.text

    async def test_a_step_with_no_approver_is_refused(
        self, client: AsyncClient, admin_user: User, password: str
    ) -> None:
        headers = await _token(client, admin_user, password)
        response = await client.post(
            f"{APPROVALS}/chains",
            headers=headers,
            json={
                "name": "Broken",
                "steps": [{"name": "Nobody", "approver_user_ids": []}],
            },
        )
        assert response.status_code == 422, response.text

    async def test_a_step_naming_an_inactive_user_is_refused_at_save_time(
        self,
        client: AsyncClient,
        admin_user: User,
        inactive_user: User,
        password: str,
    ) -> None:
        """The failure this exists to prevent: a rung nobody can satisfy,
        discovered with a live invoice already stuck on it."""
        headers = await _token(client, admin_user, password)
        response = await client.post(
            f"{APPROVALS}/chains",
            headers=headers,
            json={
                "name": "Ghost",
                "steps": [
                    {
                        "name": "Departed",
                        "approver_user_ids": [str(inactive_user.id)],
                    }
                ],
            },
        )
        assert response.status_code == 422, response.text
        assert str(inactive_user.id) in response.text

    async def test_positions_are_assigned_from_order_not_trusted(
        self,
        client: AsyncClient,
        admin_user: User,
        manager_user: User,
        password: str,
    ) -> None:
        headers = await _token(client, admin_user, password)
        chain = await _make_chain(
            client,
            headers,
            [("Receiving", [manager_user]), ("Admin", [admin_user])],
            active=False,
        )
        assert [step["position"] for step in chain["steps"]] == [1, 2]

    async def test_activating_one_chain_stands_down_the_other(
        self,
        client: AsyncClient,
        admin_user: User,
        manager_user: User,
        password: str,
    ) -> None:
        """At most one active chain per company. Two would mean every request
        had to answer "which one applied", and the honest answer would depend on
        row order."""
        headers = await _token(client, admin_user, password)
        first = await _make_chain(client, headers, [("A", [admin_user])])
        second = await _make_chain(client, headers, [("B", [manager_user])])

        assert second["is_active"] is True
        listed = await client.get(f"{APPROVALS}/chains", headers=headers)
        active = [c for c in listed.json()["data"] if c["is_active"]]
        assert [c["id"] for c in active] == [second["id"]]
        assert first["id"] not in {c["id"] for c in active}


class TestDeletingChains:
    async def test_an_unused_inactive_chain_can_be_removed(
        self,
        client: AsyncClient,
        admin_user: User,
        manager_user: User,
        password: str,
    ) -> None:
        """Otherwise the builder has a "New chain" button with no way to undo a
        mistake, and every experiment is permanent."""
        headers = await _token(client, admin_user, password)
        chain = await _make_chain(
            client, headers, [("Draft", [manager_user])], active=False
        )

        response = await client.delete(
            f"{APPROVALS}/chains/{chain['id']}", headers=headers
        )
        assert response.status_code == 200, response.text

        listed = await client.get(f"{APPROVALS}/chains", headers=headers)
        assert chain["id"] not in {c["id"] for c in listed.json()["data"]}

    async def test_an_active_chain_cannot_be_deleted(
        self,
        client: AsyncClient,
        admin_user: User,
        manager_user: User,
        password: str,
    ) -> None:
        """Deleting it would stop gating every bill in the company — the same
        effect as switching approvals off, reached by a button that says
        something else."""
        headers = await _token(client, admin_user, password)
        chain = await _make_chain(client, headers, [("Live", [manager_user])])

        response = await client.delete(
            f"{APPROVALS}/chains/{chain['id']}", headers=headers
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "CHAIN_ACTIVE"

    async def test_a_chain_with_approvals_against_it_cannot_be_deleted(
        self,
        client: AsyncClient,
        db: AsyncSession,
        admin_user: User,
        manager_user: User,
        password: str,
    ) -> None:
        """Those requests are the record of who authorised a payment."""
        headers = await _token(client, admin_user, password)
        chain = await _make_chain(client, headers, [("Admin", [admin_user])])
        invoice = await _invoice(db, manager_user)
        await _start(db, invoice, manager_user)

        # Stood down first, so the refusal under test is the in-use one rather
        # than the active one.
        await client.post(
            f"{APPROVALS}/chains/{chain['id']}/deactivate", headers=headers
        )

        response = await client.delete(
            f"{APPROVALS}/chains/{chain['id']}", headers=headers
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "CHAIN_IN_USE"

    async def test_a_manager_cannot_delete_a_chain(
        self,
        client: AsyncClient,
        admin_user: User,
        manager_user: User,
        password: str,
    ) -> None:
        admin_headers = await _token(client, admin_user, password)
        chain = await _make_chain(
            client, admin_headers, [("Draft", [manager_user])], active=False
        )

        manager_headers = await _token(client, manager_user, password)
        response = await client.delete(
            f"{APPROVALS}/chains/{chain['id']}", headers=manager_headers
        )
        assert response.status_code == 403, response.text


# ---------------------------------------------------------------------------
# Starting a chain
# ---------------------------------------------------------------------------
class TestRequestingApproval:
    async def test_without_an_active_chain_there_is_nothing_to_ask(
        self, db: AsyncSession, manager_user: User
    ) -> None:
        from app.core.exceptions import ConflictError

        invoice = await _invoice(db, manager_user)
        with pytest.raises(ConflictError) as caught:
            await _start(db, invoice, manager_user)
        assert caught.value.code == "NO_ACTIVE_CHAIN"

    async def test_the_invoice_moves_to_pending_approval(
        self,
        client: AsyncClient,
        db: AsyncSession,
        admin_user: User,
        manager_user: User,
        password: str,
    ) -> None:
        headers = await _token(client, admin_user, password)
        await _make_chain(client, headers, [("Admin", [admin_user])])

        invoice = await _invoice(db, manager_user, status=InvoiceStatus.CONFIRMED)
        request = await _start(db, invoice, manager_user)

        await db.refresh(invoice)
        assert invoice.status is InvoiceStatus.PENDING_APPROVAL
        # And it remembers where it came from, which is what a decline restores.
        assert request.status_before_approval is InvoiceStatus.CONFIRMED

    async def test_a_second_request_for_one_invoice_is_refused(
        self,
        client: AsyncClient,
        db: AsyncSession,
        admin_user: User,
        manager_user: User,
        password: str,
    ) -> None:
        """Two chains for one bill and whichever finishes first authorises it."""
        from app.core.exceptions import ConflictError

        headers = await _token(client, admin_user, password)
        await _make_chain(client, headers, [("Admin", [admin_user])])

        invoice = await _invoice(db, manager_user)
        await _start(db, invoice, manager_user)

        with pytest.raises(ConflictError) as caught:
            await _start(db, invoice, manager_user)
        assert caught.value.code == "APPROVAL_ALREADY_PENDING"

    async def test_a_rung_only_the_requester_can_decide_is_refused_up_front(
        self,
        client: AsyncClient,
        db: AsyncSession,
        admin_user: User,
        password: str,
    ) -> None:
        """Refusing now beats stranding the invoice on it. This is the check
        that can only be made here, because only here is the requester known."""
        from app.core.exceptions import ConflictError

        headers = await _token(client, admin_user, password)
        await _make_chain(client, headers, [("Only me", [admin_user])])

        invoice = await _invoice(db, admin_user)
        with pytest.raises(ConflictError) as caught:
            await _start(db, invoice, admin_user)
        assert caught.value.code == "CHAIN_UNSATISFIABLE"


# ---------------------------------------------------------------------------
# Deciding
# ---------------------------------------------------------------------------
class TestDeciding:
    async def test_a_later_rungs_approver_cannot_decide_the_current_one(
        self,
        client: AsyncClient,
        db: AsyncSession,
        admin_user: User,
        manager_user: User,
        existing_user: User,
        password: str,
    ) -> None:
        """A step cannot be skipped, which is the whole meaning of an order."""
        admin_headers = await _token(client, admin_user, password)
        await _make_chain(
            client,
            admin_headers,
            [("Receiving", [existing_user]), ("Admin", [admin_user])],
        )
        invoice = await _invoice(db, manager_user)
        request = await _start(db, invoice, manager_user)

        response = await _decide(client, admin_headers, request.id, approve=True)
        assert response.status_code == 403, response.text

    async def test_the_person_who_asked_cannot_approve_their_own(
        self,
        client: AsyncClient,
        db: AsyncSession,
        admin_user: User,
        manager_user: User,
        password: str,
    ) -> None:
        admin_headers = await _token(client, admin_user, password)
        await _make_chain(
            client, admin_headers, [("Either", [admin_user, manager_user])]
        )
        invoice = await _invoice(db, manager_user)
        request = await _start(db, invoice, manager_user)

        manager_headers = await _token(client, manager_user, password)
        response = await _decide(client, manager_headers, request.id, approve=True)
        assert response.status_code == 403, response.text

    async def test_one_person_cannot_decide_two_rungs_of_one_request(
        self,
        client: AsyncClient,
        db: AsyncSession,
        admin_user: User,
        manager_user: User,
        password: str,
    ) -> None:
        """Otherwise a two-step chain is a one-person chain wearing a costume."""
        admin_headers = await _token(client, admin_user, password)
        await _make_chain(
            client, admin_headers, [("One", [admin_user]), ("Two", [admin_user])]
        )
        invoice = await _invoice(db, manager_user)
        request = await _start(db, invoice, manager_user)

        first = await _decide(client, admin_headers, request.id, approve=True)
        assert first.status_code == 200, first.text

        second = await _decide(client, admin_headers, request.id, approve=True)
        assert second.status_code == 403, second.text

    async def test_a_decline_without_a_reason_is_refused(
        self,
        client: AsyncClient,
        db: AsyncSession,
        admin_user: User,
        manager_user: User,
        password: str,
    ) -> None:
        """"No" with no reason sends the invoice back to somebody who cannot act
        on it."""
        admin_headers = await _token(client, admin_user, password)
        await _make_chain(client, admin_headers, [("Admin", [admin_user])])
        invoice = await _invoice(db, manager_user)
        request = await _start(db, invoice, manager_user)

        response = await _decide(client, admin_headers, request.id, approve=False)
        assert response.status_code == 422, response.text

    async def test_a_decline_restores_the_status_the_invoice_arrived_with(
        self,
        client: AsyncClient,
        db: AsyncSession,
        admin_user: User,
        manager_user: User,
        password: str,
    ) -> None:
        """PO_CREATED back to PO_CREATED, not to the review queue.

        An invoice at PO_CREATED has a real draft order in Odoo. Rewinding it to
        PENDING_REVIEW would leave this row disagreeing with the system of
        record and put a reviewer in front of work already done.
        """
        admin_headers = await _token(client, admin_user, password)
        await _make_chain(client, admin_headers, [("Admin", [admin_user])])
        invoice = await _invoice(db, manager_user, status=InvoiceStatus.PO_CREATED)
        request = await _start(db, invoice, manager_user)

        response = await _decide(
            client, admin_headers, request.id, approve=False, reason="Short delivery"
        )
        assert response.status_code == 200, response.text

        await db.refresh(invoice)
        assert invoice.status is InvoiceStatus.PO_CREATED
        # The reason belongs to the decision. `rejection_reason` means something
        # else entirely — that the invoice was thrown out, not sent back.
        assert invoice.rejection_reason is None

        decision = (
            await db.execute(
                select(ApprovalDecisionRecord).where(
                    ApprovalDecisionRecord.request_id == request.id
                )
            )
        ).scalar_one()
        assert decision.reason == "Short delivery"

    async def test_approving_every_rung_finishes_the_request(
        self,
        client: AsyncClient,
        db: AsyncSession,
        admin_user: User,
        manager_user: User,
        existing_user: User,
        password: str,
    ) -> None:
        admin_headers = await _token(client, admin_user, password)
        await _make_chain(
            client,
            admin_headers,
            [("Receiving", [existing_user]), ("Admin", [admin_user])],
        )
        invoice = await _invoice(db, manager_user)
        request = await _start(db, invoice, manager_user)

        member_headers = await _token(client, existing_user, password)
        first = await _decide(client, member_headers, request.id, approve=True)
        assert first.status_code == 200, first.text
        assert first.json()["data"]["current_position"] == 2
        assert first.json()["data"]["status"] == "pending"

        second = await _decide(client, admin_headers, request.id, approve=True)
        assert second.status_code == 200, second.text
        assert second.json()["data"]["status"] == "approved"

        await db.refresh(invoice)
        assert invoice.status is InvoiceStatus.CONFIRMED

    async def test_a_closed_request_cannot_be_decided_again(
        self,
        client: AsyncClient,
        db: AsyncSession,
        admin_user: User,
        manager_user: User,
        password: str,
    ) -> None:
        admin_headers = await _token(client, admin_user, password)
        await _make_chain(client, admin_headers, [("Admin", [admin_user])])
        invoice = await _invoice(db, manager_user)
        request = await _start(db, invoice, manager_user)

        assert (
            await _decide(client, admin_headers, request.id, approve=True)
        ).status_code == 200
        again = await _decide(client, admin_headers, request.id, approve=True)
        assert again.status_code == 409, again.text
        assert again.json()["error"]["code"] == "APPROVAL_CLOSED"


class TestInvoiceApprovalRead:
    async def test_it_reports_whether_a_chain_gates_this_company(
        self,
        client: AsyncClient,
        db: AsyncSession,
        admin_user: User,
        manager_user: User,
        password: str,
    ) -> None:
        """A manager needs this fact and cannot get it any other way — reading
        the chain list takes `approval.configure`, which they do not hold."""
        manager_headers = await _token(client, manager_user, password)
        invoice = await _invoice(db, manager_user)

        before = await client.get(
            f"{INVOICES}/{invoice.id}/approval", headers=manager_headers
        )
        assert before.status_code == 200, before.text
        assert before.json()["data"]["chain_active"] is False
        assert before.json()["data"]["request"] is None

        admin_headers = await _token(client, admin_user, password)
        await _make_chain(client, admin_headers, [("Admin", [admin_user])])

        after = await client.get(
            f"{INVOICES}/{invoice.id}/approval", headers=manager_headers
        )
        assert after.json()["data"]["chain_active"] is True
        assert after.json()["data"]["chain_name"] is not None

    async def test_a_declined_request_is_still_what_it_reports(
        self,
        client: AsyncClient,
        db: AsyncSession,
        admin_user: User,
        manager_user: User,
        password: str,
    ) -> None:
        """Until somebody submits another, "declined" is the honest answer to
        where this got to — not silence."""
        admin_headers = await _token(client, admin_user, password)
        await _make_chain(client, admin_headers, [("Admin", [admin_user])])
        invoice = await _invoice(db, manager_user)
        request = await _start(db, invoice, manager_user)
        await _decide(
            client, admin_headers, request.id, approve=False, reason="Not this one"
        )

        response = await client.get(
            f"{INVOICES}/{invoice.id}/approval", headers=admin_headers
        )
        body = response.json()["data"]["request"]
        assert body["status"] == "declined"
        assert body["steps"][0]["decision"]["reason"] == "Not this one"
        # And the lines it was capped at are still readable, which is what the
        # panel renders rather than a fresh preview.
        assert body["lines"][0]["po_line_id"] == 10
        assert body["lines"][0]["unit_price"] == 10.0


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------
class TestBillingGate:
    @staticmethod
    async def _bill(client: AsyncClient, headers: dict[str, str], invoice_id):
        return await client.post(
            f"{INVOICES}/{invoice_id}/create-bill",
            headers=headers,
            json={
                "po_id": PO_ID,
                "lines": [{"po_line_id": 10, "quantity": 50.0}],
                "receive_goods": False,
                "attach_document": False,
            },
        )

    async def test_a_pending_chain_stops_the_bill(
        self,
        client: AsyncClient,
        db: AsyncSession,
        admin_user: User,
        manager_user: User,
        existing_user: User,
        password: str,
    ) -> None:
        """The check that makes the feature real. It lives inside
        `create_bill_for_invoice`, not in a route guard — a chain enforced by
        hiding a button is one that gets bypassed on the first busy afternoon.
        """
        admin_headers = await _token(client, admin_user, password)
        await _make_chain(client, admin_headers, [("Receiving", [existing_user])])
        invoice = await _invoice(db, manager_user)
        await _start(db, invoice, manager_user)

        response = await self._bill(client, admin_headers, invoice.id)
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "APPROVAL_PENDING"

    async def test_an_active_chain_with_no_request_stops_the_bill(
        self,
        client: AsyncClient,
        db: AsyncSession,
        admin_user: User,
        manager_user: User,
        password: str,
    ) -> None:
        """Two codes, because the caller can act on the difference: nobody has
        asked yet, versus somebody else is still deciding."""
        admin_headers = await _token(client, admin_user, password)
        await _make_chain(client, admin_headers, [("Admin", [admin_user])])
        invoice = await _invoice(db, manager_user)

        response = await self._bill(client, admin_headers, invoice.id)
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "APPROVAL_REQUIRED"

    async def test_a_declined_request_does_not_authorise_a_bill(
        self,
        client: AsyncClient,
        db: AsyncSession,
        admin_user: User,
        manager_user: User,
        password: str,
    ) -> None:
        admin_headers = await _token(client, admin_user, password)
        await _make_chain(client, admin_headers, [("Admin", [admin_user])])
        invoice = await _invoice(db, manager_user)
        request = await _start(db, invoice, manager_user)
        await _decide(
            client, admin_headers, request.id, approve=False, reason="Wrong vendor"
        )

        response = await self._bill(client, admin_headers, invoice.id)
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "APPROVAL_REQUIRED"

    async def test_with_no_active_chain_billing_is_unchanged(
        self,
        client: AsyncClient,
        db: AsyncSession,
        admin_user: User,
        manager_user: User,
        password: str,
    ) -> None:
        """The promise the seed migration relies on: until a company activates a
        chain, this path behaves exactly as it did before the feature existed.

        The call still fails — there is no Odoo here — but it has to fail on
        Odoo, past the gate, rather than on approval.
        """
        admin_headers = await _token(client, admin_user, password)
        invoice = await _invoice(db, manager_user)

        response = await self._bill(client, admin_headers, invoice.id)
        code = response.json().get("error", {}).get("code")
        assert code not in {"APPROVAL_REQUIRED", "APPROVAL_PENDING"}, response.text


# ---------------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------------
class TestAwaitingQueue:
    async def test_it_shows_only_what_is_waiting_on_you(
        self,
        client: AsyncClient,
        db: AsyncSession,
        admin_user: User,
        manager_user: User,
        existing_user: User,
        password: str,
    ) -> None:
        admin_headers = await _token(client, admin_user, password)
        await _make_chain(
            client,
            admin_headers,
            [("Receiving", [existing_user]), ("Admin", [admin_user])],
        )
        invoice = await _invoice(db, manager_user)
        request = await _start(db, invoice, manager_user)

        # Rung 1 belongs to the member, so the admin's queue is empty and theirs
        # is not — even though the admin is on the chain.
        member_headers = await _token(client, existing_user, password)
        mine = await client.get(f"{APPROVALS}/awaiting-me", headers=member_headers)
        assert mine.status_code == 200, mine.text
        assert [row["request"]["id"] for row in mine.json()["data"]] == [
            str(request.id)
        ]
        assert mine.json()["data"][0]["step_name"] == "Receiving"
        assert mine.json()["data"][0]["file_name"] == "approval.pdf"

        theirs = await client.get(f"{APPROVALS}/awaiting-me", headers=admin_headers)
        assert str(request.id) not in {
            row["request"]["id"] for row in theirs.json()["data"]
        }

    async def test_it_moves_to_the_next_person_when_a_rung_is_decided(
        self,
        client: AsyncClient,
        db: AsyncSession,
        admin_user: User,
        manager_user: User,
        existing_user: User,
        password: str,
    ) -> None:
        admin_headers = await _token(client, admin_user, password)
        await _make_chain(
            client,
            admin_headers,
            [("Receiving", [existing_user]), ("Admin", [admin_user])],
        )
        invoice = await _invoice(db, manager_user)
        request = await _start(db, invoice, manager_user)

        member_headers = await _token(client, existing_user, password)
        await _decide(client, member_headers, request.id, approve=True)

        theirs = await client.get(f"{APPROVALS}/awaiting-me", headers=admin_headers)
        assert [row["request"]["id"] for row in theirs.json()["data"]] == [
            str(request.id)
        ]
        gone = await client.get(f"{APPROVALS}/awaiting-me", headers=member_headers)
        assert gone.json()["data"] == []


# ---------------------------------------------------------------------------
# The escape hatch
# ---------------------------------------------------------------------------
class TestCancelling:
    async def test_an_admin_can_pull_an_invoice_out_and_it_is_recorded(
        self,
        client: AsyncClient,
        db: AsyncSession,
        admin_user: User,
        manager_user: User,
        existing_user: User,
        password: str,
    ) -> None:
        """The alternative to this endpoint is somebody editing the database by
        hand, which leaves no record that a payment bypassed its chain."""
        admin_headers = await _token(client, admin_user, password)
        await _make_chain(client, admin_headers, [("Receiving", [existing_user])])
        invoice = await _invoice(db, manager_user)
        request = await _start(db, invoice, manager_user)

        response = await client.post(
            f"{APPROVALS}/{request.id}/cancel",
            headers=admin_headers,
            json={"reason": "Everyone on this step has left"},
        )
        assert response.status_code == 200, response.text

        refreshed = (
            await db.execute(
                select(ApprovalRequest).where(ApprovalRequest.id == request.id)
            )
        ).scalar_one()
        assert refreshed.status is ApprovalRequestStatus.CANCELLED

        decision = (
            await db.execute(
                select(ApprovalDecisionRecord).where(
                    ApprovalDecisionRecord.request_id == request.id
                )
            )
        ).scalar_one()
        assert decision.reason == "Everyone on this step has left"

        await db.refresh(invoice)
        assert invoice.status is InvoiceStatus.CONFIRMED

    async def test_rejecting_the_invoice_closes_its_running_chain(
        self,
        client: AsyncClient,
        db: AsyncSession,
        admin_user: User,
        manager_user: User,
        existing_user: User,
        password: str,
    ) -> None:
        """Otherwise the request sits in its approvers' queues forever, asking
        them to sign off something already thrown away — and deciding it would
        unblock nothing, so nobody ever would."""
        admin_headers = await _token(client, admin_user, password)
        await _make_chain(client, admin_headers, [("Receiving", [existing_user])])
        invoice = await _invoice(db, manager_user)
        request = await _start(db, invoice, manager_user)

        rejected = await client.post(
            f"{INVOICES}/{invoice.id}/reject",
            headers=admin_headers,
            json={"reason": "Duplicate of last month's"},
        )
        assert rejected.status_code == 200, rejected.text

        refreshed = (
            await db.execute(
                select(ApprovalRequest).where(ApprovalRequest.id == request.id)
            )
        ).scalar_one()
        assert refreshed.status is ApprovalRequestStatus.CANCELLED

        # And REJECTED sticks — abandoning the chain must not put the invoice
        # back where it came from, which is the opposite of rejecting it.
        await db.refresh(invoice)
        assert invoice.status is InvoiceStatus.REJECTED

        member_headers = await _token(client, existing_user, password)
        queue = await client.get(f"{APPROVALS}/awaiting-me", headers=member_headers)
        assert queue.json()["data"] == []

    async def test_a_manager_cannot_cancel(
        self,
        client: AsyncClient,
        db: AsyncSession,
        admin_user: User,
        manager_user: User,
        existing_user: User,
        password: str,
    ) -> None:
        admin_headers = await _token(client, admin_user, password)
        await _make_chain(client, admin_headers, [("Receiving", [existing_user])])
        invoice = await _invoice(db, manager_user)
        request = await _start(db, invoice, manager_user)

        manager_headers = await _token(client, manager_user, password)
        response = await client.post(
            f"{APPROVALS}/{request.id}/cancel",
            headers=manager_headers,
            json={"reason": "Let me out"},
        )
        assert response.status_code == 403, response.text
