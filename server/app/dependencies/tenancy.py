"""The company a request runs inside.

`company_of` in `app/core/tenancy.py` answers "which company id" from a user;
this is the HTTP-facing half — it loads the company itself, because the parts
of the system that need more than the id (object storage builds keys from the
slug) should not each fetch it again.

Resolved from the authenticated caller's own row, never from the request. There
is no `?company_id=` and no header: the only company a caller can act inside is
the one their account belongs to.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import CompanySuspendedError
from app.core.tenancy import company_of
from app.db.session import get_db
from app.dependencies.auth import CurrentActiveUser
from app.models.company import Company
from app.repositories.company_repository import CompanyRepository


async def get_current_company(
    user: CurrentActiveUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Company:
    """The caller's company, loaded and checked on every request.

    Loaded rather than trusted from a token, for the same reason the user is:
    suspending a company has to take effect on the next request, not whenever
    an access token happens to expire.

    Raises rather than returning None. A route that asks for this is a route
    that operates inside a company, and "no company" is not a state it can
    render — the platform owner does not belong on these endpoints.
    """
    company = await CompanyRepository(db).find_by_id(company_of(user))
    if company is None:
        # The FK makes this unreachable, so reaching it means the row was
        # deleted underneath a live session. Refuse rather than carry on with
        # an id that resolves to nothing.
        raise CompanySuspendedError()
    if not company.is_active:
        raise CompanySuspendedError()
    return company


CurrentCompany = Annotated[Company, Depends(get_current_company)]
