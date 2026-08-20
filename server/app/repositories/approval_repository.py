"""Approval chain database access. No business logic, no HTTP.

Every read here takes `company_id` keyword-only and undefaulted, and puts it in
the WHERE clause. There is no row-level security in this database, no session
filter and no global ORM criteria — the tenant boundary is this line, repeated,
and a method that forgets it compiles, runs, and quietly serves one business's
payables to another.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import delete, func, or_ as sa_or, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.approval import (
    ApprovalChain,
    ApprovalDecisionRecord,
    ApprovalRequest,
    ApprovalRequestStatus,
    ApprovalStep,
)
from app.models.match_history import MatchHistory


class ApprovalRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ----------------------------------------------------------------- chains
    async def active_chain(self, *, company_id: uuid.UUID) -> ApprovalChain | None:
        """The one chain gating this company's billing, or None.

        `scalar_one_or_none` rather than `first()` on purpose: a second active
        chain for one company would mean the partial unique index is gone, and
        this should fail loudly rather than pick one and carry on gating bills
        by a policy nobody chose.
        """
        stmt = (
            select(ApprovalChain)
            .where(
                ApprovalChain.company_id == company_id,
                ApprovalChain.is_active.is_(True),
            )
            .options(selectinload(ApprovalChain.steps))
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def find_chain(
        self, chain_id: uuid.UUID, *, company_id: uuid.UUID
    ) -> ApprovalChain | None:
        stmt = (
            select(ApprovalChain)
            .where(
                ApprovalChain.id == chain_id,
                ApprovalChain.company_id == company_id,
            )
            .options(selectinload(ApprovalChain.steps))
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_chains(self, *, company_id: uuid.UUID) -> list[ApprovalChain]:
        stmt = (
            select(ApprovalChain)
            .where(ApprovalChain.company_id == company_id)
            .options(selectinload(ApprovalChain.steps))
            .order_by(ApprovalChain.is_active.desc(), ApprovalChain.created_at)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def create_chain(self, **fields: Any) -> ApprovalChain:
        chain = ApprovalChain(**fields)
        self.db.add(chain)
        await self.db.flush()
        return chain

    async def update_chain(self, chain: ApprovalChain, **fields: Any) -> ApprovalChain:
        for key, value in fields.items():
            if not hasattr(chain, key):
                raise AttributeError(f"ApprovalChain has no field {key!r}")
            setattr(chain, key, value)
        await self.db.flush()
        return chain

    async def replace_steps(
        self, chain: ApprovalChain, steps: Sequence[dict[str, Any]]
    ) -> list[ApprovalStep]:
        """Delete every step and write the given set.

        Replace rather than diff. Steps have no identity worth preserving — no
        request reads them once it has started, because a request carries its
        own snapshot — so matching old rows to new ones would be effort spent
        keeping ids that nothing refers to.

        The delete and the inserts share the caller's transaction, so a chain is
        never briefly stepless to a concurrent reader.
        """
        await self.db.execute(
            delete(ApprovalStep).where(ApprovalStep.chain_id == chain.id)
        )
        rows = [
            ApprovalStep(
                chain_id=chain.id,
                position=index,
                name=step["name"],
                approver_user_ids=list(step["approver_user_ids"]),
                records_receipt=bool(step.get("records_receipt", False)),
            )
            # Positions are assigned here, from order, rather than trusted from
            # the payload: a client that sends 1, 2, 4 would otherwise create a
            # chain whose third rung can never be reached.
            for index, step in enumerate(steps, start=1)
        ]
        self.db.add_all(rows)
        await self.db.flush()
        return rows

    async def chain_in_use(
        self, chain_id: uuid.UUID, *, company_id: uuid.UUID
    ) -> bool:
        """Whether any request has ever run through this chain.

        Asked before deleting, so the refusal can say why. The RESTRICT foreign
        key would stop it regardless, but as a raw IntegrityError at flush time
        — which reaches the admin as a 500 about a constraint rather than "this
        chain has approvals recorded against it".
        """
        stmt = (
            select(func.count())
            .select_from(ApprovalRequest)
            .where(
                ApprovalRequest.chain_id == chain_id,
                ApprovalRequest.company_id == company_id,
            )
        )
        return int((await self.db.execute(stmt)).scalar_one()) > 0

    async def delete_chain(self, chain: ApprovalChain) -> None:
        """Delete a chain and, by cascade, its steps."""
        await self.db.delete(chain)
        await self.db.flush()

    # --------------------------------------------------------------- requests
    async def create_request(self, **fields: Any) -> ApprovalRequest:
        request = ApprovalRequest(**fields)
        self.db.add(request)
        await self.db.flush()
        return request

    async def find_request(
        self, request_id: uuid.UUID, *, company_id: uuid.UUID
    ) -> ApprovalRequest | None:
        stmt = (
            select(ApprovalRequest)
            .where(
                ApprovalRequest.id == request_id,
                ApprovalRequest.company_id == company_id,
            )
            .options(
                selectinload(ApprovalRequest.decisions),
                # Both relationships are lazy="raise", so serialising a request
                # without this raises rather than quietly emitting an N+1.
                selectinload(ApprovalRequest.requester),
            )
            # Refresh what the identity map already holds.
            #
            # Without this, re-reading a request after writing a decision to it
            # returns the collection as it was BEFORE the write: SQLAlchemy
            # leaves an already-loaded relationship alone on a later query, and
            # the new row was inserted against `request_id` rather than by
            # appending to `request.decisions`. The response to "I approve"
            # would then omit the approval it just recorded.
            .execution_options(populate_existing=True)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def open_request(
        self, invoice_id: uuid.UUID, *, company_id: uuid.UUID
    ) -> ApprovalRequest | None:
        """The pending request for this invoice, if one is running.

        At most one can exist — a partial unique index enforces it — so this is
        `scalar_one_or_none` for the same reason `active_chain` is.
        """
        stmt = (
            select(ApprovalRequest)
            .where(
                ApprovalRequest.invoice_id == invoice_id,
                ApprovalRequest.company_id == company_id,
                ApprovalRequest.status == ApprovalRequestStatus.PENDING,
            )
            .options(
                selectinload(ApprovalRequest.decisions),
                # Both relationships are lazy="raise", so serialising a request
                # without this raises rather than quietly emitting an N+1.
                selectinload(ApprovalRequest.requester),
            )
            # Refresh what the identity map already holds.
            #
            # Without this, re-reading a request after writing a decision to it
            # returns the collection as it was BEFORE the write: SQLAlchemy
            # leaves an already-loaded relationship alone on a later query, and
            # the new row was inserted against `request_id` rather than by
            # appending to `request.decisions`. The response to "I approve"
            # would then omit the approval it just recorded.
            .execution_options(populate_existing=True)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def latest_request(
        self, invoice_id: uuid.UUID, *, company_id: uuid.UUID
    ) -> ApprovalRequest | None:
        """The most recent request for this invoice, whatever became of it.

        What the review screen shows. A declined request is still the answer to
        "where did this get to" until somebody submits another.
        """
        stmt = (
            select(ApprovalRequest)
            .where(
                ApprovalRequest.invoice_id == invoice_id,
                ApprovalRequest.company_id == company_id,
            )
            .options(
                selectinload(ApprovalRequest.decisions),
                # Both relationships are lazy="raise", so serialising a request
                # without this raises rather than quietly emitting an N+1.
                selectinload(ApprovalRequest.requester),
            )
            .order_by(ApprovalRequest.created_at.desc())
            .limit(1)
            # See `find_request`: without this the decisions come back as they
            # were before the write that prompted the re-read.
            .execution_options(populate_existing=True)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_pending(self, *, company_id: uuid.UUID) -> list[ApprovalRequest]:
        """Every running request in one company, newest first.

        Deliberately not filtered by approver here. Which rung a request is on
        and who may decide it both live in `steps_snapshot`, and asking Postgres
        to index into a JSONB array by a value from another column of the same
        row is a query that reads far worse than the loop in the service — for a
        set that is bounded by "invoices this company has in flight", which is
        tens, not thousands. Revisit if that stops being true.
        """
        stmt = (
            select(ApprovalRequest)
            .where(
                ApprovalRequest.company_id == company_id,
                ApprovalRequest.status == ApprovalRequestStatus.PENDING,
            )
            .options(
                selectinload(ApprovalRequest.decisions),
                # Both relationships are lazy="raise", so serialising a request
                # without this raises rather than quietly emitting an N+1.
                selectinload(ApprovalRequest.requester),
            )
            .order_by(ApprovalRequest.created_at.desc())
            # See `find_request`. It matters here too: the queue is re-read
            # immediately after a decision, and a stale decisions collection
            # would leave the person who just decided still looking at their
            # own row.
            .execution_options(populate_existing=True)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def update_request(
        self, request: ApprovalRequest, **fields: Any
    ) -> ApprovalRequest:
        for key, value in fields.items():
            if not hasattr(request, key):
                raise AttributeError(f"ApprovalRequest has no field {key!r}")
            setattr(request, key, value)
        await self.db.flush()
        return request

    async def advance(self, request: ApprovalRequest, *, expect: int) -> bool:
        """Move to the next rung, but only from the one we read.

        A conditional UPDATE rather than `request.current_position += 1`. The
        unique constraint on (request_id, position) is the primary guard against
        two approvers advancing the same rung twice; this is the second half of
        it, and what keeps the in-memory object from writing back a position
        computed from a stale read.

        Returns False when the row had already moved, which the service treats
        as "somebody else decided this rung first".
        """
        stmt = (
            update(ApprovalRequest)
            .where(
                ApprovalRequest.id == request.id,
                ApprovalRequest.current_position == expect,
                ApprovalRequest.status == ApprovalRequestStatus.PENDING,
            )
            .values(
                current_position=expect + 1,
                # The next approver's clock starts now, and their first
                # notification must not be a reminder about the silence of the
                # person before them.
                current_step_since=dt.datetime.now(dt.UTC),
                reminded_at=None,
            )
        )
        moved = int((await self.db.execute(stmt)).rowcount or 0) == 1
        if moved:
            # The UPDATE went round the ORM, so the identity map still holds the
            # old value. Refreshing it keeps the object the service is about to
            # serialise in step with the row.
            await self.db.refresh(request)
        return moved

    async def find_overdue(
        self, *, waiting_since: dt.datetime, nudged_before: dt.datetime
    ) -> list[uuid.UUID]:
        """Ids of pending requests whose current rung has gone quiet.

        DELIBERATELY NOT company-scoped, and the only method here that is not.

        It is called by the cron sweep, which has no caller to scope to: the
        request comes from a scheduler holding a shared secret, not from a person
        in a company, and a request stuck in one company must not stay stuck
        because nobody in another triggered a sweep.

        That is safe for exactly one reason, and it is the same argument the
        invoice sweep rests on: this reads NOTHING company-specific. It returns
        ids and only ids. The task each id is handed to loads the row itself and
        takes the company from it, so every nudge carries its own company, one at
        a time, and no company context is ever shared between two of them.

        Adding a column to this SELECT would break that argument. Do not.
        """
        stmt = select(ApprovalRequest.id).where(
            ApprovalRequest.status == ApprovalRequestStatus.PENDING,
            ApprovalRequest.current_step_since <= waiting_since,
            # Either never nudged, or not nudged recently. Without the second
            # half a sweep running every five minutes would notify every five
            # minutes, which is how a reminder becomes something people mute.
            sa_or(
                ApprovalRequest.reminded_at.is_(None),
                ApprovalRequest.reminded_at <= nudged_before,
            ),
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def find_request_by_id(
        self, request_id: uuid.UUID
    ) -> ApprovalRequest | None:
        """One request, by id alone, for the background task the sweep starts.

        Unscoped on purpose and safe for the same reason
        `MatchHistoryRepository.find_by_id` is: the caller is a task holding
        nothing but an id, and the company it then acts in is read FROM the row
        rather than inherited from the process.

        Not to be used from a request handler. Anything reached by a person goes
        through `find_request`, which takes their company and answers 404 for
        anybody else's row.
        """
        stmt = (
            select(ApprovalRequest)
            .where(ApprovalRequest.id == request_id)
            .options(
                selectinload(ApprovalRequest.decisions),
                selectinload(ApprovalRequest.requester),
            )
            .execution_options(populate_existing=True)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def invoice_summaries(
        self, invoice_ids: Sequence[uuid.UUID], *, company_id: uuid.UUID
    ) -> dict[uuid.UUID, dict[str, Any]]:
        """Enough about each invoice to render a queue row, in one round trip.

        Columns rather than entities: the queue needs a file name and a vendor,
        and hydrating full MatchHistory objects would drag the OCR text along
        with them — hundreds of KB per row for data nothing renders.

        Company-scoped even though the ids came from company-scoped requests.
        The filter costs nothing and means this cannot become a lookup-by-id
        helper that leaks the first time somebody reuses it.
        """
        if not invoice_ids:
            return {}
        stmt = select(
            MatchHistory.id,
            MatchHistory.file_name,
            MatchHistory.extracted_vendor,
            MatchHistory.extracted_invoice_no,
        ).where(
            MatchHistory.id.in_(set(invoice_ids)),
            MatchHistory.company_id == company_id,
        )
        return {
            row.id: {
                "file_name": row.file_name,
                "vendor": row.extracted_vendor,
                "invoice_no": row.extracted_invoice_no,
            }
            for row in (await self.db.execute(stmt)).all()
        }

    # -------------------------------------------------------------- decisions
    async def add_decision(self, **fields: Any) -> ApprovalDecisionRecord:
        """Insert one decision. Never updates — the table is append-only.

        No commit and no exception handling: a duplicate (request_id, position)
        raises IntegrityError out of the flush, and the service decides what
        that means.
        """
        decision = ApprovalDecisionRecord(**fields)
        self.db.add(decision)
        await self.db.flush()
        return decision
