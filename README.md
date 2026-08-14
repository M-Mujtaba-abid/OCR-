# AP Invoice-to-PO Automation

A member uploads a vendor invoice. Mistral reads it into structured JSON. Open
purchase orders are pulled from Odoo, narrowed to a shortlist in code, and an
LLM picks the right one with a stated reason. An admin confirms or overrides.

```
upload → R2 → Mistral OCR + extraction → Odoo POs → score → LLM rerank → review
```

## Stack

| | |
|---|---|
| Backend | FastAPI 0.141 · Python 3.12 · SQLAlchemy 2.0 async · Alembic |
| Database | Neon PostgreSQL (pooled endpoint, PgBouncer-aware) |
| Storage | Cloudflare R2, private bucket, presigned reads |
| AI | Mistral `mistral-ocr-latest` + `mistral-large-latest` |
| ERP | Odoo 18 over XML-RPC |
| Frontend | Next.js 16 · React 19 · TanStack Query · Tailwind v4 · TypeScript strict |

## Layout

```
server/
  app/
    routes/          HTTP surface only — method, path, permission, response model
    controllers/     request/response shaping, cookie handling
    services/        business rules; the only layer that decides anything
    repositories/    SQL; no rules, no HTTP
    models/          SQLAlchemy ORM
    schemas/         Pydantic request/response contracts
    core/            config, security, storage, mistral, exceptions
    dependencies/    auth + RBAC dependency factories
  alembic/versions/  migrations
  scripts/           operator tools (set_role, seed/export POs, make test invoices)
  tests/             pytest

client/
  app/               App Router; (auth) and (protected) route groups
  service/           one axios instance + one service per feature
  hooks/             one TanStack Query hook module per feature
  components/        auth · invoices · admin · layout · ui
  lib/               query client, query keys, role rules
  utils/auth.ts      in-memory access token
  scripts/           end-to-end verification against a live backend
```

## Running it

Both need their own terminal.

```powershell
# 1. backend
cd server
copy .env.example .env      # then fill it in
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements.txt
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000

# 2. frontend
cd client
copy .env.example .env.local
npm install
npm run dev
```

Registration always creates a **member**. Promote the first admin out of band —
there is deliberately no self-service route for it:

```powershell
cd server
.\.venv\Scripts\python.exe scripts\set_role.py you@example.com admin -y
```

### Without Odoo

The matching pipeline runs against a local fixture when `ODOO_URL` is blank:

```powershell
cd server
.\.venv\Scripts\python.exe scripts\seed_purchase_orders.py
# then set ODOO_FIXTURE_PATH=fixtures/purchase_orders.json

$env:PO_FIXTURE="fixtures/purchase_orders.json"
.\.venv\Scripts\python.exe scripts\make_test_invoice.py --list
.\.venv\Scripts\python.exe scripts\make_test_invoice.py --random
.\.venv\Scripts\python.exe scripts\make_test_invoice.py --unmatchable
```

Filling in `ODOO_URL` disables the fixture automatically — a real connection
always wins, and every fixture fetch logs a warning so fake purchase orders can
never quietly reach an accounts-payable screen.

## Tests

```powershell
cd server ; .\.venv\Scripts\python.exe -m pytest -q          # 65

cd client                                                    # need both servers up
node scripts\verify-auth-flow.mjs                            # 38
node scripts\verify-rbac.mjs      <adminEmail> <password>    # 32
node scripts\verify-invoices.mjs  <adminEmail> <password>    # 44
node scripts\verify-pipeline.mjs  <adminEmail> <password>    # 49
```

The `.mjs` suites run against the live backend, real R2, real Mistral and real
Odoo. They are slower than unit tests and they are the ones that catch
integration failures.

## How authentication works

- Access token: **in memory only**, ~15 minutes. Never `localStorage`, never a
  cookie readable by JavaScript.
- Refresh token: **HttpOnly cookie**, scoped to `/api/v1/auth`, opaque, stored
  as a SHA-256 digest.
- Rotation on every refresh, with reuse detection: presenting a token that was
  already rotated revokes every session for that user.
- Rotation is claimed with a single conditional `UPDATE ... WHERE revoked_at IS
  NULL`. Concurrent refreshes on one token produce exactly one winner; the rest
  get 401 and are **not** treated as theft.
- The frontend also single-flights refreshes, so it never produces that race in
  the first place.

Roles are `member` / `manager` / `admin`, but routes gate on **permissions**
(`invoice.read.all`, `invoice.approve`, `user.update`, …) so re-shuffling which
role holds what is one line in `app/dependencies/auth.py`.

Frontend role checks are cosmetic. Every request is authorised server-side.

## How matching works

Two stages, and the split is the point.

**1. Narrow, in code** (`services/matching_engine.py`) — pure, no I/O, unit
tested. Scores every open PO on vendor 30 / amount 25 / reference 20 / date 15 /
lines 10. Components that cannot be evaluated are dropped and the rest
renormalised, so a sparse invoice is not penalised for being sparse. Returns the
top 15 above a floor.

**2. Decide, with the model** (`services/match_service.py`) — the LLM sees only
that shortlist and picks, with reasoning. Judgement is what it is good at;
search is not.

Handing the model all 5 000 open orders would be simpler and worse: the prompt
grows without bound, so does the cost, and a model asked to find one row among
hundreds reliably overlooks it.

Two guards that are not optional:

- `matched_po_id` must be in the candidate set. A model will occasionally return
  a plausible id that was never in the prompt; accepting it would attach an
  invoice to an unrelated order.
- Zero candidates short-circuits before the LLM. Asked to choose from an empty
  list, a model invents an answer.

Nothing is ever auto-confirmed. Matching produces `pending_review`; a human
accepts or overrides, and an override is recorded as `was_corrected` — the most
useful signal this system produces, because it is the record of where the
matcher was wrong.

## Before deploying

- [ ] `ENVIRONMENT=production`, `DEBUG=false`, `AUTH_COOKIE_SECURE=true`.
      Config refuses to boot otherwise — that check is deliberate.
- [ ] Rotate every credential that has been shared anywhere: Neon password,
      R2 keys, Mistral key, Odoo API key, `JWT_SECRET_KEY`.
- [ ] `CORS_ORIGINS` set to the real frontend origin. A wildcard cannot be
      combined with credentialed requests.
- [ ] `NEXT_PUBLIC_BACKEND_URL` set. Keep the host consistent with the browser's
      — `localhost` vs `127.0.0.1` scopes the refresh cookie differently and
      refresh silently stops working.
- [ ] Decide on `OCR_AUTO_ON_UPLOAD`. Every upload costs money with it on.

## Known limitations

- **Background jobs do not survive a restart.** OCR and matching run on
  FastAPI `BackgroundTasks`. A startup reaper flips rows stuck in
  `ocr_processing` / `matching` for 10 minutes to a failed state so nothing is
  polled forever, and there is a retry button — but a real queue (arq + Redis)
  is the right answer under load. The service functions already take an id and
  open their own session, so that move is contained.
- **Document annotation caps at 8 pages.** Longer documents fall back to OCR
  then a chat pass over the markdown: two calls, more latency, and extraction
  quality depends on flattened text rather than layout.
- **Line-item product mapping is not implemented.** `invoice_line_matches` is
  populated from the extraction with `status='pending'`; resolving each line to
  an Odoo `product.product` is the next phase.
- **Nothing is pushed to Odoo yet.** Confirming records the decision;
  `purchase.order.action_create_invoice` is not called.
