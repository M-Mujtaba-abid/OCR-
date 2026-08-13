# AP Invoice-to-PO Automation

Production architecture blueprint for a v1 SaaS that automates Accounts Payable
invoice-to-PO matching: a vendor invoice is uploaded, Mistral OCR extracts structured data,
open Purchase Orders are pulled from Odoo over XML-RPC, a weighted engine scores candidates
against a learned vendor knowledge base, and a human verifies side-by-side before a draft
vendor bill is pushed back into Odoo.

**This repository currently contains documentation only.** `server/` and `client/` are not
yet implemented — the blueprint is the deliverable, and an engineer implements from it.

## The documents

Read `docs/pdf/AP-Invoice-Automation-Blueprint.pdf` for the whole thing in one file, or
work through the sections individually. Markdown in `docs/` is the editable source of
truth; the PDFs are generated from it.

| # | Document | Contents |
|---|---|---|
| 00 | [Overview & Decisions](docs/00-overview.md) | System flow, locked decisions and why, prerequisites, build order, version traps |
| 01 | [Backend Architecture](docs/01-backend-architecture.md) | `server/` tree, dependencies, config, async sessions, error envelope, logging, app factory, Alembic |
| 02 | [Database Schema](docs/02-database-schema.md) | Four SQLAlchemy 2.0 models, mixins, enums, indexes, status lifecycle, migration notes |
| 03 | [Core Service Integrations](docs/03-services.md) | Odoo XML-RPC client, Mistral OCR, the matching engine, knowledge base, orchestration |
| 04 | [API Contract](docs/04-api-contract.md) | Every v1 endpoint, error codes, paired Pydantic ⇄ TypeScript schemas |
| 05 | [Frontend Architecture](docs/05-frontend-architecture.md) | Next.js 16 tree, auth strategy, api-client, hooks, verification screen components |
| 06 | [Setup & Runbook](docs/06-setup-runbook.md) | Step-by-step first run, Odoo troubleshooting, Windows gotchas, verification checklist |

## Stack

| Layer | Choice |
|---|---|
| Frontend | Next.js 16.3 (App Router), TypeScript 6, Tailwind v4, shadcn/ui, TanStack Query v5 |
| Backend | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.0 async, Alembic |
| Database | PostgreSQL 18 |
| OCR | Mistral (`mistralai` v2 — note the changed import path) |
| ERP | Odoo via XML-RPC |
| Matching | `rapidfuzz`, weighted scoring with a learned vendor knowledge base |

## Start here

1. Read [00-overview](docs/00-overview.md) end to end — it is short and everything else
   assumes it.
2. Follow [06-setup-runbook](docs/06-setup-runbook.md) step 0 through step 5 to get a
   backend responding on `/health`.
3. Build in the order given in 00, section "Build order". Odoo comes before OCR, and OCR
   before matching, because that ordering front-loads the integrations with the most
   unknowns.

Three decisions carry the most weight, and are argued in full where they appear: the
knowledge base keys on a *normalized* vendor name rather than the raw string
([02](docs/02-database-schema.md)); `confirm` commits the human decision *before* touching
Odoo, so an Odoo outage never loses work ([03](docs/03-services.md)); and the JWT lives in
an httpOnly cookie behind a same-origin rewrite, which is what makes both route protection
and the PDF viewer work ([05](docs/05-frontend-architecture.md)).

## Regenerating the PDFs

```powershell
py -3.12 -m venv .docvenv
.\.docvenv\Scripts\python.exe -m pip install markdown pygments
.\.docvenv\Scripts\python.exe docs\build-pdf.py
```

Renders through headless Chrome (falling back to Edge). Output goes to `docs/pdf/`.
