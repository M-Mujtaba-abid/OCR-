"""Two companies must never share an Odoo client, a cache, or a schema probe.

This is the test the whole of Phase C exists for. `odoo_service` holds three
process-wide caches, and before this change all three were keyed by nothing —
correct while there was one Odoo, and a cross-company data leak the moment
there are two.

The fetch cache is the dangerous one. What it holds is purchase orders —
vendors, references, amounts — and matching is the step that ends in a vendor
bill. An unkeyed hit does not merely show the wrong data: it offers one
company's orders as candidates for another company's invoice, and a reviewer
confirming that match creates a bill against an order that is not theirs.

No network. Nothing here connects to an Odoo; the client is only ever built,
never used.
"""

from __future__ import annotations

import uuid

import pytest

from app.services import odoo_service as svc
from app.services.odoo_service import (
    OdooCredentials,
    OdooService,
    _cache_key,
    _cached,
    _remember,
    clear_fetch_cache,
    reset_odoo_client,
)

FRESHLEAF = uuid.UUID("0f5a2e64-4d2b-4c1e-9a37-6b8c0d1e2f30")
KJ = uuid.UUID("11111111-2222-3333-4444-555555555555")


def _credentials(label: str) -> OdooCredentials:
    return OdooCredentials(
        base_url=f"https://{label}.odoo.test",
        database=f"{label}_db",
        username=f"{label}@example.com",
        api_key=f"{label}-key",
    )


@pytest.fixture(autouse=True)
def _clean_slate():
    """Every cache emptied before and after. These are process-wide."""
    reset_odoo_client()
    clear_fetch_cache()
    yield
    reset_odoo_client()
    clear_fetch_cache()


def _service(company_id: uuid.UUID, label: str) -> OdooService:
    return OdooService(company_id, _credentials(label))


class TestClients:
    def test_two_companies_never_share_a_client(self) -> None:
        """A client holds an authenticated uid against ONE Odoo database.

        Sharing one would mean the second company's calls run as the first
        company's user, against the first company's data.
        """
        freshleaf = _service(FRESHLEAF, "freshleaf")._client()
        kj = _service(KJ, "kj")._client()

        assert freshleaf is not kj

    def test_the_same_company_reuses_its_client(self) -> None:
        """The caching still has to work, or every call re-authenticates."""
        first = _service(FRESHLEAF, "freshleaf")._client()
        second = _service(FRESHLEAF, "freshleaf")._client()

        assert first is second

    def test_each_client_carries_its_own_credentials(self) -> None:
        """The client must not read `settings` — that is what made it global."""
        freshleaf = _service(FRESHLEAF, "freshleaf")._client()
        kj = _service(KJ, "kj")._client()

        assert freshleaf._credentials.database == "freshleaf_db"
        assert kj._credentials.database == "kj_db"

    def test_resetting_one_company_leaves_the_other_connected(self) -> None:
        """Saving one company's credentials must not force every other company
        to re-authenticate."""
        freshleaf = _service(FRESHLEAF, "freshleaf")._client()
        kj_before = _service(KJ, "kj")._client()

        reset_odoo_client(FRESHLEAF)

        assert _service(FRESHLEAF, "freshleaf")._client() is not freshleaf
        assert _service(KJ, "kj")._client() is kj_before


class TestFetchCache:
    def test_one_companys_orders_are_never_served_to_another(self) -> None:
        """THE leak this phase exists to prevent.

        Same query shape, same arguments, two companies — and the second must
        miss rather than be handed the first's purchase orders.
        """
        freshleaf = _service(FRESHLEAF, "freshleaf")
        kj = _service(KJ, "kj")

        orders = ["freshleaf-order"]
        _remember(freshleaf._key("open", 500), orders)  # type: ignore[arg-type]

        assert _cached(freshleaf._key("open", 500)) == orders
        assert _cached(kj._key("open", 500)) is None

    def test_the_company_is_the_first_element_of_every_key(self) -> None:
        """A structural assertion, not a behavioural one.

        If a future key is built by hand and forgets the company, this is the
        rule it broke.
        """
        key = _cache_key(FRESHLEAF, "open", 500)

        assert key[0] == FRESHLEAF

    def test_clearing_one_company_leaves_the_other_cached(self) -> None:
        """Creating a purchase order clears the cache. Clearing globally would
        make every other company re-read hundreds of orders because this one
        wrote something."""
        freshleaf = _service(FRESHLEAF, "freshleaf")
        kj = _service(KJ, "kj")

        _remember(freshleaf._key("open", 500), ["freshleaf-order"])  # type: ignore[arg-type]
        _remember(kj._key("open", 500), ["kj-order"])  # type: ignore[arg-type]

        freshleaf.clear_cache()

        assert _cached(freshleaf._key("open", 500)) is None
        assert _cached(kj._key("open", 500)) == ["kj-order"]


class TestCapabilities:
    def test_a_schema_probe_is_never_shared_between_companies(self) -> None:
        """`document_attachment_ids` exists in FreshLeaf's Odoo because a
        customisation module puts it there. Telling KJ's Odoo to write it would
        fail every bill on a fault nobody could place."""
        svc._caps[(FRESHLEAF, "account.move")] = frozenset(
            {"document_attachment_ids"}
        )

        assert svc._caps.get((KJ, "account.move")) is None

    def test_resetting_one_company_drops_only_its_probes(self) -> None:
        svc._caps[(FRESHLEAF, "account.move")] = frozenset({"ref"})
        svc._caps[(KJ, "account.move")] = frozenset({"ref"})

        reset_odoo_client(FRESHLEAF)

        assert (FRESHLEAF, "account.move") not in svc._caps
        assert (KJ, "account.move") in svc._caps


class TestCredentialsCache:
    def test_credentials_are_remembered_per_company(self) -> None:
        svc.cache_credentials(FRESHLEAF, _credentials("freshleaf"))

        assert svc.cached_credentials(FRESHLEAF) is not None
        assert svc.cached_credentials(KJ) is None

    def test_a_credential_never_appears_in_a_repr(self) -> None:
        """These end up in logs and tracebacks. The key must not."""
        text = repr(_credentials("freshleaf"))

        assert "freshleaf-key" not in text
        assert "***" in text
