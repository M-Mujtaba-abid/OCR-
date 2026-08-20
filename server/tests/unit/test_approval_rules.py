"""The rules an approval chain obeys, tested against literals.

No database and no HTTP. These are the judgements the feature rests on — who may
decide a rung, and whether a bill has outgrown what was approved — and they are
testable this way precisely because none of them reach out. Same shape, and same
reasoning, as `test_bill_creator`'s pure section.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from app.services.approval_service import (
    amount_of,
    approvers_of,
    is_final_step,
    may_decide,
    step_at,
    step_records_receipt,
)
from app.services.bill_creator_service import check_exceeds_approval


# ---------------------------------------------------------------- test doubles
class _Decision:
    def __init__(self, position: int, decided_by: uuid.UUID) -> None:
        self.position = position
        self.decided_by = decided_by


class _Request:
    """Only what the pure rules read. A real ApprovalRequest would drag a
    database session in behind its relationships."""

    def __init__(
        self,
        *,
        steps: list[dict[str, Any]],
        current_position: int = 1,
        requested_by: uuid.UUID | None = None,
        allow_self_approval: bool = False,
        decisions: list[_Decision] | None = None,
    ) -> None:
        self.steps_snapshot = steps
        self.current_position = current_position
        self.requested_by = requested_by
        self.allow_self_approval = allow_self_approval
        self.decisions = decisions or []


def _step(
    position: int,
    *approvers: uuid.UUID,
    name: str = "Step",
    records_receipt: bool = False,
) -> dict[str, Any]:
    # UUIDs as strings, exactly as JSONB gives them back.
    return {
        "position": position,
        "name": f"{name} {position}",
        "approver_user_ids": [str(a) for a in approvers],
        "records_receipt": records_receipt,
    }


# ---------------------------------------------------------------------------
# Reading a snapshot
# ---------------------------------------------------------------------------
class TestSnapshotReading:
    def test_a_rung_is_found_by_its_position_not_its_offset(self) -> None:
        """The two agree today. A lookup that quietly returned the wrong rung
        would be a worse failure than one that returned nothing."""
        alice = uuid.uuid4()
        request = _Request(steps=[_step(2, alice), _step(1, alice)])
        assert step_at(request, 1)["position"] == 1  # type: ignore[index]
        assert step_at(request, 2)["position"] == 2  # type: ignore[index]

    def test_a_position_the_chain_does_not_have_is_none(self) -> None:
        assert step_at(_Request(steps=[_step(1, uuid.uuid4())]), 7) is None

    def test_approvers_come_back_as_uuids_not_strings(self) -> None:
        """JSONB hands them back as text, and `"abc" == UUID("abc")` is quietly
        False — which would silently deny every approver on every rung."""
        alice = uuid.uuid4()
        assert approvers_of(_step(1, alice)) == {alice}

    def test_a_malformed_id_in_the_snapshot_is_dropped_not_fatal(self) -> None:
        alice = uuid.uuid4()
        step = {"position": 1, "name": "s", "approver_user_ids": [str(alice), "nope"]}
        assert approvers_of(step) == {alice}

    def test_the_last_rung_is_the_highest_position(self) -> None:
        alice = uuid.uuid4()
        request = _Request(steps=[_step(1, alice), _step(2, alice), _step(3, alice)])
        assert not is_final_step(request, 2)
        assert is_final_step(request, 3)

    def test_a_chain_with_no_steps_has_no_final_rung(self) -> None:
        assert not is_final_step(_Request(steps=[]), 1)


# ---------------------------------------------------------------------------
# Who may decide
# ---------------------------------------------------------------------------
class TestMayDecide:
    def test_somebody_named_on_the_rung_may_decide_it(self) -> None:
        alice = uuid.uuid4()
        request = _Request(steps=[_step(1, alice)], requested_by=uuid.uuid4())
        assert may_decide(request, user_id=alice, position=1)

    def test_somebody_not_named_may_not(self) -> None:
        alice, bob = uuid.uuid4(), uuid.uuid4()
        request = _Request(steps=[_step(1, alice)], requested_by=uuid.uuid4())
        assert not may_decide(request, user_id=bob, position=1)

    def test_a_later_rungs_approver_cannot_decide_the_current_one(self) -> None:
        """The whole point of an ordered chain: step 2 does not get to sign
        while step 1 is still waiting."""
        alice, bob = uuid.uuid4(), uuid.uuid4()
        request = _Request(
            steps=[_step(1, alice), _step(2, bob)], requested_by=uuid.uuid4()
        )
        assert not may_decide(request, user_id=bob, position=1)

    def test_the_person_who_asked_cannot_approve_their_own(self) -> None:
        alice = uuid.uuid4()
        request = _Request(steps=[_step(1, alice)], requested_by=alice)
        assert not may_decide(request, user_id=alice, position=1)

    def test_unless_the_chain_allowed_it_when_the_request_began(self) -> None:
        """The escape for a genuinely one-admin company. Read off the request,
        not the chain: turning the flag on mid-flight must not retroactively
        relax a request that started under the stricter rule."""
        alice = uuid.uuid4()
        request = _Request(
            steps=[_step(1, alice)], requested_by=alice, allow_self_approval=True
        )
        assert may_decide(request, user_id=alice, position=1)

    def test_one_person_cannot_decide_two_rungs_of_one_request(self) -> None:
        """Otherwise a three-step chain quietly becomes a two-person one without
        anybody editing it — the control evaporating in place."""
        alice = uuid.uuid4()
        request = _Request(
            steps=[_step(1, alice), _step(2, alice)],
            current_position=2,
            requested_by=uuid.uuid4(),
            decisions=[_Decision(1, alice)],
        )
        assert not may_decide(request, user_id=alice, position=2)

    def test_but_a_different_person_on_that_rung_still_may(self) -> None:
        alice, bob = uuid.uuid4(), uuid.uuid4()
        request = _Request(
            steps=[_step(1, alice), _step(2, alice, bob)],
            current_position=2,
            requested_by=uuid.uuid4(),
            decisions=[_Decision(1, alice)],
        )
        assert may_decide(request, user_id=bob, position=2)


class TestReceivingStep:
    def test_a_rung_marked_for_receipt_says_so(self) -> None:
        alice = uuid.uuid4()
        request = _Request(steps=[_step(1, alice, records_receipt=True)])
        assert step_records_receipt(request, 1)

    def test_an_ordinary_rung_does_not(self) -> None:
        alice = uuid.uuid4()
        assert not step_records_receipt(_Request(steps=[_step(1, alice)]), 1)

    def test_a_snapshot_written_before_the_flag_existed_does_not(self) -> None:
        """Old requests have no `records_receipt` key at all, and a missing key
        must read as "no" rather than raise."""
        alice = uuid.uuid4()
        old = {
            "position": 1,
            "name": "Legacy",
            "approver_user_ids": [str(alice)],
        }
        assert not step_records_receipt(_Request(steps=[old]), 1)

    def test_a_position_the_chain_does_not_have_does_not(self) -> None:
        alice = uuid.uuid4()
        request = _Request(steps=[_step(1, alice, records_receipt=True)])
        assert not step_records_receipt(request, 2)


# ---------------------------------------------------------------------------
# What was approved
# ---------------------------------------------------------------------------
class TestAmountOf:
    def test_tax_is_included(self) -> None:
        lines = [
            {"po_line_id": 1, "quantity": 2.0, "unit_price": 100.0, "tax_rate": 0.05}
        ]
        assert amount_of(lines) == Decimal("210.0000")

    def test_a_line_with_no_tax_contributes_its_bare_total(self) -> None:
        lines = [{"po_line_id": 1, "quantity": 3.0, "unit_price": 10.0}]
        assert amount_of(lines) == Decimal("30.0000")

    def test_no_lines_is_zero_not_an_error(self) -> None:
        assert amount_of([]) == Decimal("0.0000")


class TestCheckExceedsApproval:
    def test_billing_exactly_what_was_approved_is_fine(self) -> None:
        approved = [{"po_line_id": 10, "quantity": 50.0}]
        assert check_exceeds_approval(approved, approved) is None

    def test_billing_less_than_was_approved_is_fine(self) -> None:
        """A part-bill against an approved amount is a normal thing to do."""
        approved = [{"po_line_id": 10, "quantity": 50.0}]
        submitted = [{"po_line_id": 10, "quantity": 20.0}]
        assert check_exceeds_approval(submitted, approved) is None

    def test_billing_more_than_was_approved_is_named_with_both_figures(self) -> None:
        """The hole this closes: approved at one figure, billed at another."""
        approved = [{"po_line_id": 10, "quantity": 50.0}]
        submitted = [
            {"po_line_id": 10, "quantity": 500.0, "description": "Widget A"}
        ]
        message = check_exceeds_approval(submitted, approved)
        assert message is not None
        assert "500" in message
        assert "50" in message
        assert "Widget A" in message

    def test_a_line_nobody_approved_is_an_over_claim_of_all_of_it(self) -> None:
        """Skipping unknown lines would make adding one the way through: it is
        exactly how you would bill for goods nobody agreed to."""
        approved = [{"po_line_id": 10, "quantity": 50.0}]
        submitted = [
            {"po_line_id": 10, "quantity": 50.0},
            {"po_line_id": 11, "quantity": 1.0, "description": "Smuggled"},
        ]
        message = check_exceeds_approval(submitted, approved)
        assert message is not None
        assert "Smuggled" in message

    def test_two_entries_for_one_line_are_summed_before_comparing(self) -> None:
        """Each is within the approved quantity; together they are not. Checking
        them one at a time would let it through — the same trap
        `check_over_billing` sums for."""
        approved = [{"po_line_id": 10, "quantity": 50.0}]
        submitted = [
            {"po_line_id": 10, "quantity": 30.0},
            {"po_line_id": 10, "quantity": 30.0},
        ]
        assert check_exceeds_approval(submitted, approved) is not None

    def test_float_rounding_is_not_an_over_claim(self) -> None:
        """Odoo stores quantities as floats, so an exact re-bill can land a
        fraction of a nanogram over. QTY_EPSILON is what stops that reading as
        somebody exceeding their approval."""
        approved = [{"po_line_id": 10, "quantity": 0.1 + 0.2}]
        submitted = [{"po_line_id": 10, "quantity": 0.3}]
        assert check_exceeds_approval(submitted, approved) is None

    def test_the_approved_side_is_summed_per_line_too(self) -> None:
        approved = [
            {"po_line_id": 10, "quantity": 20.0},
            {"po_line_id": 10, "quantity": 30.0},
        ]
        submitted = [{"po_line_id": 10, "quantity": 50.0}]
        assert check_exceeds_approval(submitted, approved) is None
