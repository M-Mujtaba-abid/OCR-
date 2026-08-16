"""Authentication tests.

Covers every scenario in the specification: register, login, invalid password,
duplicate email, protected route, expired token, invalid JWT, refresh,
rotation, reuse detection, logout, logout-all, inactive user — plus the
non-negotiable checks that password_hash and refresh_token_hash never appear
in a response.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.core.security import create_access_token
from tests.conftest import auth_header, login

COOKIE = settings.AUTH_COOKIE_NAME


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------
async def test_register_creates_user(client: AsyncClient, unique_email, password):
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": unique_email, "password": password, "full_name": "New User"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["success"] is True
    assert body["data"]["email"] == unique_email
    # Self-registration must never grant privilege.
    assert body["data"]["role"] == "member"
    assert body["data"]["is_verified"] is False


async def test_register_duplicate_email_conflicts(
    client: AsyncClient, existing_user, password
):
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": existing_user.email, "password": password},
    )
    assert r.status_code == 409
    body = r.json()
    assert body["success"] is False
    assert body["error"]["code"] == "EMAIL_ALREADY_REGISTERED"


async def test_register_email_is_case_insensitive(
    client: AsyncClient, existing_user, password
):
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": existing_user.email.upper(), "password": password},
    )
    assert r.status_code == 409


async def test_register_rejects_short_password(client: AsyncClient, unique_email):
    r = await client.post(
        "/api/v1/auth/register", json={"email": unique_email, "password": "short"}
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
async def test_login_succeeds(client: AsyncClient, existing_user, password):
    r = await login(client, existing_user.email, password)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["data"]["token_type"] == "bearer"
    assert body["data"]["access_token"]
    assert body["data"]["expires_in"] > 0
    assert body["data"]["user"]["email"] == existing_user.email


async def test_login_sets_httponly_refresh_cookie(
    client: AsyncClient, existing_user, password
):
    r = await login(client, existing_user.email, password)
    assert COOKIE in r.cookies

    raw = r.headers["set-cookie"].lower()
    # The whole point of the cookie: JavaScript must not be able to read it.
    assert "httponly" in raw
    assert "samesite=lax" in raw
    assert f"path={settings.AUTH_COOKIE_PATH}".lower() in raw


async def test_login_refresh_token_absent_from_body(
    client: AsyncClient, existing_user, password
):
    r = await login(client, existing_user.email, password)
    assert "refresh_token" not in r.json()["data"]


async def test_login_wrong_password_rejected(client: AsyncClient, existing_user):
    r = await login(client, existing_user.email, "WrongPassword123")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INVALID_CREDENTIALS"


async def test_login_unknown_email_gives_same_error(client: AsyncClient, password):
    """Identical code and message to a wrong password, so the response cannot
    be used to discover which emails are registered."""
    r = await login(client, "nobody-here@example.com", password)
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INVALID_CREDENTIALS"


async def test_login_inactive_user_rejected(
    client: AsyncClient, inactive_user, password
):
    r = await login(client, inactive_user.email, password)
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INACTIVE_USER"


# ---------------------------------------------------------------------------
# Protected routes
# ---------------------------------------------------------------------------
async def test_me_requires_authentication(client: AsyncClient):
    r = await client.get("/api/v1/auth/me")
    assert r.status_code == 401
    assert r.json()["success"] is False


async def test_me_returns_current_user(client: AsyncClient, existing_user, password):
    token = (await login(client, existing_user.email, password)).json()["data"][
        "access_token"
    ]
    r = await client.get("/api/v1/auth/me", headers=auth_header(token))
    assert r.status_code == 200
    assert r.json()["data"]["id"] == str(existing_user.id)


async def test_expired_access_token_rejected(client: AsyncClient, existing_user):
    expired, _ = create_access_token(existing_user.id, expires_minutes=-1)
    r = await client.get("/api/v1/auth/me", headers=auth_header(expired))
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "TOKEN_EXPIRED"


async def test_malformed_jwt_rejected(client: AsyncClient):
    r = await client.get("/api/v1/auth/me", headers=auth_header("not.a.jwt"))
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INVALID_TOKEN"


async def test_jwt_signed_with_wrong_secret_rejected(
    client: AsyncClient, existing_user
):
    import jwt as pyjwt

    forged = pyjwt.encode(
        {"sub": str(existing_user.id), "type": "access", "exp": 9_999_999_999},
        "an-attacker-chosen-secret",
        algorithm="HS256",
    )
    r = await client.get("/api/v1/auth/me", headers=auth_header(forged))
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INVALID_TOKEN"


async def test_token_for_deleted_user_rejected(client: AsyncClient):
    ghost, _ = create_access_token(uuid.uuid4())
    r = await client.get("/api/v1/auth/me", headers=auth_header(ghost))
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Refresh + rotation
# ---------------------------------------------------------------------------
async def test_refresh_issues_new_access_token(
    client: AsyncClient, existing_user, password
):
    await login(client, existing_user.email, password)
    r = await client.post("/api/v1/auth/refresh")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["access_token"]


async def test_refresh_rotates_the_cookie(client: AsyncClient, existing_user, password):
    await login(client, existing_user.email, password)
    first = client.cookies.get(COOKIE, path=settings.AUTH_COOKIE_PATH)

    r = await client.post("/api/v1/auth/refresh")
    assert r.status_code == 200
    second = client.cookies.get(COOKIE, path=settings.AUTH_COOKIE_PATH)

    assert second and second != first, "refresh must issue a NEW token"


async def test_immediate_replay_is_a_duplicate_not_theft(
    client: AsyncClient, existing_user, password, monkeypatch
):
    """A double-firing client must not lose every session.

    Two refreshes racing — a retry, a double-click, two tabs waking together —
    put the loser on the wire moments after the winner rotated the token. From
    the server that is indistinguishable from a replay, and treating it as
    theft signs an honest user out everywhere.
    """
    monkeypatch.setattr(settings, "REFRESH_REUSE_GRACE_SECONDS", 30)

    await login(client, existing_user.email, password)
    stolen = client.cookies.get(COOKIE, path=settings.AUTH_COOKIE_PATH)
    assert (await client.post("/api/v1/auth/refresh")).status_code == 200

    client.cookies.set(COOKIE, stolen, path=settings.AUTH_COOKIE_PATH)
    r = await client.post("/api/v1/auth/refresh")

    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INVALID_REFRESH_TOKEN"


async def test_reused_refresh_token_detected_and_revokes_everything(
    client: AsyncClient, existing_user, password, monkeypatch
):
    """The security property that makes rotation worth doing.

    The grace window is switched off rather than waited out: this test is about
    what happens to a genuinely stolen token, and sleeping ten seconds to prove
    it would make the suite slower for no extra confidence.
    """
    monkeypatch.setattr(settings, "REFRESH_REUSE_GRACE_SECONDS", 0)

    await login(client, existing_user.email, password)
    stolen = client.cookies.get(COOKIE, path=settings.AUTH_COOKIE_PATH)

    # Legitimate client rotates.
    assert (await client.post("/api/v1/auth/refresh")).status_code == 200

    # Attacker replays the token captured before rotation.
    client.cookies.set(COOKIE, stolen, path=settings.AUTH_COOKIE_PATH)
    r = await client.post("/api/v1/auth/refresh")

    assert r.status_code == 401
    assert r.json()["error"]["code"] == "REFRESH_TOKEN_REUSED"

    # And the whole family is dead — the legitimate client's newer token too.
    r2 = await client.post("/api/v1/auth/refresh")
    assert r2.status_code == 401


# NOTE: rotation being atomic under concurrency — exactly one winner out of N
# parallel refreshes on the same token — is NOT tested here, and cannot be.
#
# The fixtures in conftest.py wrap each test in a savepoint that is rolled back
# afterwards, so every request in a test shares one database transaction. The
# conditional `UPDATE ... WHERE revoked_at IS NULL` that makes rotation atomic
# relies on separate transactions contending for a row lock, and inside a single
# transaction there is nothing to contend with. A test written here would pass
# whether or not the claim existed, which is worse than no test.
#
# It is covered in client/scripts/verify-auth-flow.mjs, against a real server
# with real connections, where the concurrency is real.


async def test_refresh_without_cookie_rejected(client: AsyncClient):
    r = await client.post("/api/v1/auth/refresh")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INVALID_REFRESH_TOKEN"


async def test_refresh_with_garbage_cookie_rejected(client: AsyncClient):
    client.cookies.set(COOKIE, "not-a-real-token", path=settings.AUTH_COOKIE_PATH)
    r = await client.post("/api/v1/auth/refresh")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------
async def test_logout_revokes_session(client: AsyncClient, existing_user, password):
    await login(client, existing_user.email, password)

    r = await client.post("/api/v1/auth/logout")
    assert r.status_code == 200
    assert r.json()["data"]["revoked_sessions"] == 1

    # The revoked token must no longer refresh.
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401


async def test_logout_without_cookie_is_idempotent(client: AsyncClient):
    r = await client.post("/api/v1/auth/logout")
    assert r.status_code == 200
    assert r.json()["data"]["revoked_sessions"] == 0


async def test_logout_all_revokes_every_session(
    client: AsyncClient, existing_user, password
):
    # Two independent logins == two devices.
    first = await login(client, existing_user.email, password)
    token = first.json()["data"]["access_token"]
    await login(client, existing_user.email, password)

    r = await client.post("/api/v1/auth/logout-all", headers=auth_header(token))
    assert r.status_code == 200
    assert r.json()["data"]["revoked_sessions"] >= 2

    assert (await client.post("/api/v1/auth/refresh")).status_code == 401


async def test_sessions_endpoint_lists_active_sessions(
    client: AsyncClient, existing_user, password
):
    token = (await login(client, existing_user.email, password)).json()["data"][
        "access_token"
    ]
    r = await client.get("/api/v1/auth/sessions", headers=auth_header(token))
    assert r.status_code == 200
    assert len(r.json()["data"]) >= 1


# ---------------------------------------------------------------------------
# Secret leakage — the checks that must never regress
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("forbidden", ["password_hash", "refresh_token_hash", "password"])
async def test_no_endpoint_leaks_secrets(
    client: AsyncClient, existing_user, password, forbidden
):
    register = await client.post(
        "/api/v1/auth/register",
        json={"email": f"leak-{uuid.uuid4().hex[:8]}@example.com", "password": password},
    )
    token = (await login(client, existing_user.email, password)).json()["data"][
        "access_token"
    ]
    me = await client.get("/api/v1/auth/me", headers=auth_header(token))
    sessions = await client.get("/api/v1/auth/sessions", headers=auth_header(token))

    for response in (register, me, sessions):
        assert forbidden not in response.text, f"{forbidden} leaked in {response.url}"


async def test_error_responses_never_include_a_stack_trace(client: AsyncClient):
    r = await login(client, "nobody@example.com", "whatever123")
    text = r.text.lower()
    assert "traceback" not in text
    assert "file \"" not in text
