"""Which company a piece of work belongs to.

One answer to that question, in one place. Every write into a company-scoped
table needs it, and a service that works it out for itself is a service that
can work it out differently from the one next door.

This is deliberately about the ACTOR, not the request. A company id that
arrives in a request body or a query string is a company id the caller chose,
and the whole point of the boundary is that they do not get to. It comes from
the authenticated user's own row, the same way their role does.
"""

from __future__ import annotations

import uuid

from app.core.exceptions import ForbiddenError
from app.models.user import User


class NoCompanyError(ForbiddenError):
    """The actor belongs to no company, so this work has nowhere to go.

    Reachable by exactly one kind of account — the platform owner, who sits
    outside the companies rather than in one. Them uploading an invoice or
    raising a bill is not a permission that was forgotten; it is an action with
    no meaningful answer to "whose?", and it fails rather than picking one.
    """

    code = "NO_COMPANY"
    message = (
        "This account does not belong to a company, so it cannot create or "
        "read company records."
    )


def company_of(user: User) -> uuid.UUID:
    """The company id to stamp on anything this user creates."""
    if user.company_id is None:
        raise NoCompanyError()
    return user.company_id
