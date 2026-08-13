# OCR API — Backend

FastAPI backend with layered architecture and production-grade authentication.

```
Router → Controller → Service → Repository → Database
```

## Quick start

```powershell
cd C:\ocr\server

# 1. environment (Python 3.12 ONLY — see "Why 3.12" below)
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements-dev.txt

# 2. config
Copy-Item .env.example .env      # then fill in DATABASE_URL and JWT_SECRET_KEY
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(64))"

# 3. migrate
.\.venv\Scripts\alembic.exe upgrade head

# 4. run
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

Docs at <http://127.0.0.1:8000/docs>. Tests: `.\.venv\Scripts\python.exe -m pytest -q`.

> **Why 3.12, not 3.13/3.15.** Wheels are published per interpreter ABI. A very
> new interpreter has no `cp3XX` wheels for `pydantic-core`, `asyncpg`,
> `watchfiles` or `argon2-cffi`, so pip falls back to building from source and
> needs MSVC and Rust. `pyproject.toml` pins `requires-python = ">=3.12,<3.13"`
> so this fails loudly rather than halfway through a build.
>
> Always install with `--only-binary=:all:` — it forbids source builds, so a
> missing wheel is an immediate clear error instead of a compiler crash.

## Architecture

```
app/
├── main.py                  app factory, lifespan, middleware, router mounting
├── core/
│   ├── config.py            Pydantic Settings; DSN normalisation; prod safety checks
│   ├── security.py          Argon2 hashing, JWT encode/decode, refresh token gen
│   ├── exceptions.py        AppError hierarchy (the ApiError equivalent)
│   └── handlers.py          global exception handlers
├── db/
│   ├── base.py              DeclarativeBase, naming convention, mixins
│   └── session.py           async engine, get_db dependency
├── models/                  SQLAlchemy ORM — database shape only
├── schemas/                 Pydantic request/response validation
├── repositories/            database queries; NO business logic
├── services/                business logic; NO HTTP concerns
├── controllers/             HTTP coordination; NO SQL
├── routes/                  endpoint definitions + dependencies only
├── dependencies/            get_current_user, require_role, require_permission
├── middleware/              request id + timing
├── lib/                     reusable infrastructure (responses, logging)
└── utils/
```

### Layer rules

| Layer | May do | Must not |
|---|---|---|
| Route | path, method, deps, response_model | business logic |
| Controller | call service, shape response, set cookies | SQL |
| Service | business rules, orchestration | touch Request/Response |
| Repository | queries | business rules |
| Model | table definition | behaviour |
| Schema | validation | database access |

## Response format

Success:

```json
{ "success": true, "message": "Login successful", "data": { } }
```

Error:

```json
{
  "success": false,
  "message": "Invalid email or password.",
  "error": { "code": "INVALID_CREDENTIALS", "details": null },
  "request_id": "d2738f9a25804c1f"
}
```

The envelope is a Pydantic generic (`ApiResponse[T]`), not middleware. That
keeps the OpenAPI schema truthful — a middleware that rewrapped bodies would
document a payload shape the API never actually returns.

`request_id` also appears in the `X-Request-ID` response header and on every log
line for that request, so a user can quote it and you can find the trace.

## Authentication

| | Access token | Refresh token |
|---|---|---|
| Format | JWT (HS256) | 384-bit random, opaque |
| Lifetime | 15 min | 30 days |
| Storage | client memory | HttpOnly Secure cookie |
| Server state | none | `auth_sessions` row |
| Revocable | no (short-lived) | yes |
| At rest | n/a | SHA-256 digest only |

Two deliberate asymmetries:

**Refresh tokens are opaque, not JWTs.** A JWT refresh token cannot be revoked
before expiry without a denylist — which is a database lookup anyway, so you
pay the cost without the benefit. A database-backed token is revocable by
definition.

**Refresh digests use SHA-256, passwords use Argon2.** A password is low-entropy
and human-chosen, so a stolen hash must be expensive to crack. A refresh token
is 384 bits from a CSPRNG and cannot be brute-forced at any cost, so a slow hash
adds latency for nothing — and Argon2's random salt would make lookup-by-token
impossible.

### Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/v1/auth/register` | — | Create account (always `member`) |
| POST | `/api/v1/auth/login` | — | Access token + refresh cookie |
| POST | `/api/v1/auth/refresh` | cookie | Rotate refresh, new access token |
| POST | `/api/v1/auth/logout` | cookie | Revoke this session |
| POST | `/api/v1/auth/logout-all` | Bearer | Revoke every session |
| GET | `/api/v1/auth/me` | Bearer | Current user |
| GET | `/api/v1/auth/sessions` | Bearer | List active devices |

### Refresh rotation and theft detection

Every refresh issues a new token and revokes the old one, recording
`rotated_to_id` on the old row. That column is what makes theft detectable:

```
token presented → session found
   ├─ revoked AND rotated_to_id set → REUSE. Revoke every session for the user.
   ├─ revoked (no successor)        → logged out. 401.
   ├─ expired                       → 401.
   └─ active                        → rotate, issue new pair.
```

Without `rotated_to_id`, "revoked by logout" and "already replaced by rotation"
look identical, and a stolen token is indistinguishable from a stray request.

### Authorization

Routes declare a *permission*, not a role, so re-assigning which role holds a
permission is a one-line change in `dependencies/auth.py`:

```python
@router.delete(
    "/users/{user_id}",
    dependencies=[Depends(require_permission("user.delete"))],
)
```

Roles: `member` < `manager` < `admin`. Roles are **not** in the JWT — they are
read from the database per request, so a revoked role takes effect immediately
rather than up to 15 minutes later.

## Database

Two tables. `users.password_hash` holds Argon2id;
`auth_sessions.refresh_token_hash` holds a SHA-256 digest. Neither raw value is
ever stored, logged, or returned.

```powershell
.\.venv\Scripts\alembic.exe upgrade head       # apply
.\.venv\Scripts\alembic.exe downgrade base     # revert (round-trip tested)
.\.venv\Scripts\alembic.exe revision --autogenerate -m "message"
```

> Migrations run against the **direct** Neon endpoint; the app uses the
> **pooled** one. `alembic/env.py` derives the direct URL by stripping
> `-pooler`. PgBouncer in transaction mode cannot support the session-level
> operations DDL requires.

> Alembic never autogenerates `DROP TYPE` for enums. The initial migration drops
> `user_role` by hand in `downgrade()` — without it, downgrade-then-upgrade
> fails with "type user_role already exists".

## Example flows

```bash
# register
curl -X POST localhost:8000/api/v1/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"SuperSecret123"}'

# login — -c saves the refresh cookie
curl -X POST localhost:8000/api/v1/auth/login -c cookies.txt \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"SuperSecret123"}'

# protected
curl localhost:8000/api/v1/auth/me -H "Authorization: Bearer $ACCESS_TOKEN"

# refresh — -b sends the cookie, -c stores the rotated one
curl -X POST localhost:8000/api/v1/auth/refresh -b cookies.txt -c cookies.txt

# logout
curl -X POST localhost:8000/api/v1/auth/logout -b cookies.txt
```

## Production checklist

`ENVIRONMENT=production` makes the app refuse to start unless these hold —
`config.py` validates them at import time:

- [ ] `JWT_SECRET_KEY` set explicitly, ≥32 chars, from a secret manager
- [ ] `AUTH_COOKIE_SECURE=true`
- [ ] `DEBUG=false` (also disables `/docs` and `/openapi.json`)
- [ ] `CORS_ORIGINS` lists exact origins — a wildcard cannot be combined with
      credentialed requests
- [ ] HTTPS terminated in front of the app
- [ ] `SameSite=none` only ever paired with `Secure=true`

Not yet implemented, and worth knowing: rate limiting on `/login` and
`/register`, email verification, password reset, and a scheduled job calling
`AuthSessionRepository.delete_expired()`.
