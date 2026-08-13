# Accounts Payable Invoice-to-PO Automation

**SOLUTION ARCHITECTURE & DELIVERY PLAN**

`FOR REVIEW`

Production-grade SaaS (v1 / MVP) — OCR-driven invoice capture, Odoo ERP matching, and a
human-in-the-loop verification workflow with a self-learning knowledge base.

> Upload → Mistral OCR → Odoo PO Fetch → Weighted Match → Human Verify → Push to Odoo → Learn

| | |
|---|---|
| **Prepared by** | HST — HSxTech / HST Advantage |
| **Document** | Architecture Blueprint v1.1 |
| **Date** | 12 August 2026 |
| **Audience** | Engineering Manager (Approval) |

---

## 00 · Document Purpose & Contents

This blueprint describes **what** we will build and **how** we will build it for the AP
Invoice-to-PO Automation product. It is written to be a decision document: after reading it
you should be able to either approve the plan to start Phase 1, or mark specific sections
for revision. Technical readers will find full folder structures, database models, and
integration code; a manager can read Sections 00–02 and Section 09 for scope, plan, and risk.

> **WHAT WE ARE SOLVING**
>
> Accounts Payable teams manually key vendor invoices and match them against open Purchase
> Orders in Odoo. This is slow and error-prone. Our system extracts invoice data
> automatically, proposes the correct Odoo PO with a confidence score, lets a human confirm
> in one screen, and **learns from every correction** so accuracy rises over time.

| # | Section |
|---|---|
| 01 | Solution Overview & End-to-End Flow |
| 02 | Technology Stack & Rationale |
| 03 | Packages & Dependencies (backend + frontend) |
| 04 | Backend Architecture & Folder Structure |
| 05 | Database Schema (SQLAlchemy 2.0 Models) |
| 06 | Core Service Integrations — Odoo, Mistral OCR, Matching Engine |
| 07 | Confidence Scoring Model |
| 08 | Frontend Architecture & UI Components |
| 09 | Deployment, Hosting & Database (Neon Postgres) |
| 10 | Delivery Plan, Milestones, Risks & Sign-off |

---

## 01 · Solution Overview & End-to-End Flow

The product is a single-page verification console backed by a FastAPI service. A user
uploads an invoice; the system does the heavy lifting and returns a ranked, scored match
against live Odoo Purchase Orders. The human is kept in the loop for confirmation, and each
confirmation feeds the knowledge base.

| # | Step | What happens |
|---|---|---|
| 1 | **Upload** | User uploads a vendor invoice (PDF/image) in the Next.js console. |
| 2 | **Ingest** | Next.js streams the file to FastAPI; file stored, a `match_history` record is opened. |
| 3 | **OCR** | Mistral OCR extracts structured fields: vendor, invoice no, date, total, line items. |
| 4 | **Fetch POs** | Odoo XML-RPC pulls active purchase orders + lines for candidate matching. |
| 5 | **Match** | Engine scores each PO (0–100%) using KB lookup, fuzzy vendor, amount delta, line items. |
| 6 | **Verify** | Side-by-side UI: PDF preview vs best-match PO with a confidence badge. |
| 7 | **Confirm / Correct** | User approves or re-maps. Correction upserts a vendor alias into the KB. |
| 8 | **Push to Odoo** | Status update / draft Vendor Bill written back to Odoo via XML-RPC. |

> **THE LEARNING LOOP (WHY THIS COMPOUNDS)**
>
> Step 7 is the differentiator. Every manual correction writes a
> `raw OCR vendor string → Odoo partner ID` mapping into `vendor_knowledge_base`. The next
> invoice from that vendor is matched from the KB *before* fuzzy logic runs — turning a 70%
> guess into a 100% deterministic hit. Accuracy improves with usage, at zero extra cost.

### Architectural principles for v1

- **Human-in-the-loop by default.** The system proposes; a person disposes. We never
  auto-post to Odoo in v1 — we create drafts / status flags for review.
- **Idempotent & auditable.** Every invoice run is logged end-to-end (raw OCR, candidates,
  score, final decision) in `match_history` for traceability.
- **Odoo is the system of record.** Our DB stores learning + audit only; financial truth
  stays in Odoo.
- **Async everywhere.** OCR and XML-RPC are I/O-bound; the stack is fully async to keep the
  API responsive.

---

## 02 · Technology Stack & Rationale

| Layer | Technology | Why this choice for v1 |
|---|---|---|
| **Frontend** | Next.js 14+ (App Router), TypeScript, Tailwind, Shadcn UI, TanStack Query | Server components + streaming uploads; Shadcn gives production UI fast; TanStack Query handles server state & polling cleanly. |
| **Backend** | Python 3.12, FastAPI, Pydantic v2 | Async-first, typed request/response contracts, automatic OpenAPI docs for the frontend team. |
| **Persistence** | Neon serverless PostgreSQL, SQLAlchemy 2.0 (async), Alembic | Managed hosted Postgres (scale-to-zero, pgvector-ready); JSONB for raw OCR payloads; Alembic keeps migrations reviewable. See Section 09. |
| **OCR** | Mistral Document AI (`mistral-ocr-latest`) | Native document-to-structured-JSON with an annotations feature; strong on invoices/tables. |
| **Matching** | `rapidfuzz` | C-backed fuzzy string scoring — fast token-based vendor matching. |
| **ERP** | Odoo via XML-RPC | Stable, version-agnostic (works across Odoo 16–19), no custom Odoo module required for v1. |

> **DECISION TO CONFIRM WITH MANAGER**
>
> **pgvector:** Not required for v1. Deterministic KB + rapidfuzz covers vendor matching
> well. We recommend deferring semantic (embedding-based) line-item matching to v2 unless
> invoices are highly unstructured. *Flag if you want it in scope now.*

---

## 03 · Packages & Dependencies

### Backend — `requirements.txt`

```text
# --- Web framework ---
fastapi==0.115.*
uvicorn[standard]==0.32.*
python-multipart==0.0.*          # file uploads

# --- Data / validation ---
pydantic==2.9.*
pydantic-settings==2.5.*         # typed .env config

# --- Database (async) ---
sqlalchemy[asyncio]==2.0.*
asyncpg==0.30.*                  # async postgres driver
alembic==1.13.*                  # migrations

# --- Integrations ---
mistralai==1.*                   # Mistral OCR SDK
rapidfuzz==3.*                   # fuzzy vendor matching
# Odoo XML-RPC uses Python stdlib xmlrpc.client — no package needed

# --- Auth / security ---
passlib[bcrypt]==1.7.*
python-jose[cryptography]==3.3.*  # JWT

# --- Utilities ---
httpx==0.27.*
tenacity==9.*                    # retries for OCR / XML-RPC
python-dotenv==1.*
```

### Frontend — `package.json` (key dependencies)

```json
{
  "dependencies": {
    "next": "14.2.x",
    "react": "^18.3.0",
    "react-dom": "^18.3.0",
    "typescript": "^5.5.0",
    "@tanstack/react-query": "^5.51.0",
    "axios": "^1.7.0",
    "tailwindcss": "^3.4.0",
    "class-variance-authority": "^0.7.0",
    "clsx": "^2.1.0",
    "lucide-react": "^0.400.0",
    "react-pdf": "^9.1.0",          // PDF preview (pdf.js)
    "zod": "^3.23.0",               // client-side schema validation
    "react-hook-form": "^7.52.0"
  },
  "devDependencies": {
    "@types/node": "^20",
    "@types/react": "^18",
    "eslint": "^8",
    "eslint-config-next": "14.2.x",
    "prettier": "^3.3.0"
  }
}
```

Shadcn UI components are added via its CLI
(`npx shadcn@latest add button table badge dialog ...`) rather than as a single package, so
they live in `components/ui` and stay editable.

---

## 04 · Backend Architecture & Folder Structure

A layered architecture keeps HTTP, business logic, and integrations independent — routers
stay thin, services hold logic, and external systems (Odoo, Mistral) are isolated so they
can be mocked and swapped.

```text
backend/
├── app/
│   ├── main.py                    # FastAPI app factory, middleware, router mounting
│   ├── api/
│   │   ├── deps.py                # shared deps: get_db, get_current_user
│   │   └── v1/
│   │       ├── router.py          # aggregates all v1 routers
│   │       ├── auth.py            # login / token
│   │       ├── invoices.py        # upload, OCR trigger, match, confirm
│   │       └── knowledge.py       # KB CRUD / alias management
│   ├── core/
│   │   ├── config.py              # Pydantic Settings (env-driven)
│   │   ├── security.py            # JWT, password hashing
│   │   └── logging.py             # structured logging setup
│   ├── db/
│   │   ├── base.py                # DeclarativeBase
│   │   └── session.py             # async engine + sessionmaker
│   ├── models/                    # SQLAlchemy ORM (Section 05)
│   │   ├── user.py
│   │   ├── knowledge.py
│   │   └── match_history.py
│   ├── schemas/                   # Pydantic request/response DTOs
│   │   ├── invoice.py
│   │   ├── matching.py
│   │   └── knowledge.py
│   ├── services/                  # business logic + integrations
│   │   ├── odoo_service.py        # XML-RPC client
│   │   ├── ocr_service.py         # Mistral OCR wrapper
│   │   ├── matching_engine.py     # scoring + KB lookup
│   │   └── invoice_service.py     # orchestration of the full flow
│   └── repositories/              # DB access, keeps services DB-agnostic
│       ├── knowledge_repo.py
│       └── history_repo.py
├── alembic/                       # migration versions
├── tests/
├── .env
└── requirements.txt
```

### Key production practices

#### Typed settings (pydantic-settings)

**`app/core/config.py`**

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    PROJECT_NAME: str = "AP Invoice Automation"
    DATABASE_URL: str          # postgresql+asyncpg://...
    JWT_SECRET: str
    JWT_EXPIRE_MIN: int = 60 * 12

    MISTRAL_API_KEY: str
    ODOO_URL: str
    ODOO_DB: str
    ODOO_USERNAME: str
    ODOO_PASSWORD: str         # or API key

    CORS_ORIGINS: list[str] = ["http://localhost:3000"]

settings = Settings()   # import this everywhere, never os.getenv
```

#### Async DB session (dependency-injected)

**`app/db/session.py`**

```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.core.config import settings

engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

async def get_db() -> AsyncSession:
    async with SessionLocal() as session:
        yield session   # FastAPI closes it after the request
```

#### App factory, CORS & middleware

**`app/main.py`**

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api.v1.router import api_router

def create_app() -> FastAPI:
    app = FastAPI(title=settings.PROJECT_NAME, version="1.0.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"], allow_headers=["*"],
    )
    app.include_router(api_router, prefix="/api/v1")
    return app

app = create_app()
```

- **Thin routers.** Endpoints validate input (Pydantic) and delegate to a service. No
  business logic in routers.
- **Repositories** wrap all DB queries so services never write raw SQLAlchemy queries
  inline — easier to test.
- **Retries** (tenacity) wrap OCR and XML-RPC calls, which are the two failure-prone network
  hops.
- **Versioned API** (`/api/v1`) so we can evolve contracts without breaking the frontend.

---

## 05 · Database Schema (SQLAlchemy 2.0)

Three tables carry v1: `users` for auth/tenant context, `vendor_knowledge_base` for the
learning loop, and `match_history` for the audit trail. All use the SQLAlchemy 2.0 typed
`Mapped[]` style.

**`app/models/user.py`**

```python
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Boolean, DateTime, func
from datetime import datetime
from app.db.base import Base

class User(Base):
    __tablename__ = "users"

    id:              Mapped[int]  = mapped_column(primary_key=True)
    email:           Mapped[str]  = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str]  = mapped_column(String(255))
    full_name:       Mapped[str | None] = mapped_column(String(120))
    # multi-tenant hook: scope all queries by tenant_id in v2
    tenant_id:       Mapped[str]  = mapped_column(String(64), default="default", index=True)
    is_active:       Mapped[bool] = mapped_column(Boolean, default=True)
    created_at:      Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

**`app/models/knowledge.py`**

```python
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Integer, Float, DateTime, func, UniqueConstraint
from app.db.base import Base

class VendorKnowledgeBase(Base):
    """Learned mapping: raw OCR vendor string -> Odoo partner."""
    __tablename__ = "vendor_knowledge_base"
    __table_args__ = (UniqueConstraint("tenant_id", "raw_vendor_key"),)

    id:               Mapped[int] = mapped_column(primary_key=True)
    tenant_id:        Mapped[str] = mapped_column(String(64), index=True, default="default")
    # normalised (lowercased, stripped) raw OCR vendor name = the lookup key
    raw_vendor_key:   Mapped[str] = mapped_column(String(255), index=True)
    raw_vendor_text:  Mapped[str] = mapped_column(String(255))   # original as seen
    odoo_partner_id:  Mapped[int] = mapped_column(Integer)
    odoo_partner_name: Mapped[str] = mapped_column(String(255))
    # confidence reinforcement
    hit_count:        Mapped[int] = mapped_column(Integer, default=1)
    last_used_at:     Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at:       Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

**`app/models/match_history.py`**

```python
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Integer, Float, DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from app.db.base import Base

class MatchHistory(Base):
    """Full audit trail for every uploaded invoice run."""
    __tablename__ = "match_history"

    id:           Mapped[int] = mapped_column(primary_key=True)
    tenant_id:    Mapped[str] = mapped_column(String(64), index=True)
    uploaded_by:  Mapped[int] = mapped_column(Integer)       # users.id
    file_name:    Mapped[str] = mapped_column(String(255))
    file_path:    Mapped[str] = mapped_column(String(512))

    # raw OCR output kept verbatim for audit + re-processing
    ocr_raw:      Mapped[dict] = mapped_column(JSONB)
    extracted_vendor:     Mapped[str | None] = mapped_column(String(255))
    extracted_invoice_no: Mapped[str | None] = mapped_column(String(120))
    extracted_total:      Mapped[float | None] = mapped_column(Float)

    # matching result
    matched_po_id:    Mapped[int | None] = mapped_column(Integer)    # Odoo purchase.order id
    matched_po_name:  Mapped[str | None] = mapped_column(String(120))
    confidence_score: Mapped[float | None] = mapped_column(Float)    # 0-100
    candidates:       Mapped[dict | None] = mapped_column(JSONB)     # ranked list w/ sub-scores

    # human decision + learning
    status:       Mapped[str] = mapped_column(String(30), default="pending")
                                # pending | confirmed | corrected | rejected
    was_corrected: Mapped[bool] = mapped_column(default=False)
    final_po_id:   Mapped[int | None] = mapped_column(Integer)
    pushed_to_odoo: Mapped[bool] = mapped_column(default=False)
    created_at:    Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

> **WHY JSONB FOR OCR & CANDIDATES**
>
> Invoice OCR output and the ranked candidate list are semi-structured and evolve. Storing
> them as JSONB keeps the audit lossless without rigid columns, while the flat columns
> (vendor, total, score) stay queryable for dashboards and reporting.

---

## 06 · Core Service Integrations

### 6.1 Odoo XML-RPC Client

**`app/services/odoo_service.py`**

```python
import xmlrpc.client
from functools import cached_property
from app.core.config import settings

class OdooService:
    def __init__(self):
        self.url      = settings.ODOO_URL
        self.db       = settings.ODOO_DB
        self.username = settings.ODOO_USERNAME
        self.password = settings.ODOO_PASSWORD

    # ---- Authentication ----
    @cached_property
    def uid(self) -> int:
        common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common")
        uid = common.authenticate(self.db, self.username, self.password, {})
        if not uid:
            raise PermissionError("Odoo authentication failed")
        return uid

    @cached_property
    def models(self):
        return xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object")

    def _execute(self, model, method, args, kwargs=None):
        return self.models.execute_kw(
            self.db, self.uid, self.password, model, method, args, kwargs or {}
        )

    # ---- Fetch active POs with line items ----
    def fetch_active_pos(self, limit: int = 200) -> list[dict]:
        domain = [["state", "in", ["purchase", "done"]]]   # confirmed POs
        fields = ["name", "partner_id", "amount_total", "date_order", "order_line"]
        pos = self._execute("purchase.order", "search_read",
                            [domain], {"fields": fields, "limit": limit})

        # pull line items in one batched read
        line_ids = [lid for po in pos for lid in po["order_line"]]
        lines = self._execute("purchase.order.line", "read",
                              [line_ids], {"fields": ["order_id", "name",
                                           "product_qty", "price_unit", "price_subtotal"]})
        by_po: dict[int, list] = {}
        for ln in lines:
            by_po.setdefault(ln["order_id"][0], []).append(ln)
        for po in pos:
            po["lines"] = by_po.get(po["id"], [])
        return pos

    # ---- Push result back: create draft Vendor Bill ----
    def create_vendor_bill(self, partner_id: int, po_id: int,
                           invoice_ref: str, amount: float) -> int:
        vals = {
            "move_type": "in_invoice",
            "partner_id": partner_id,
            "ref": invoice_ref,
            "invoice_origin": invoice_ref,
        }
        bill_id = self._execute("account.move", "create", [vals])
        return bill_id   # stays in DRAFT for human review (v1 policy)
```

> **V1 WRITE-BACK POLICY**
>
> We create the Vendor Bill in **draft** only. No auto-confirm / auto-post in v1 — this
> keeps a human gate on financial postings and avoids accounting mistakes while the model is
> still learning.

### 6.2 Mistral OCR Service

We send the invoice bytes as base64 to `client.ocr.process` and use the **document
annotation** feature to get structured invoice fields directly, validated by a Pydantic
schema.

**`app/services/ocr_service.py`**

```python
import base64
from mistralai import Mistral
from pydantic import BaseModel
from app.core.config import settings

# ---- Structured target schema (drives Mistral annotations) ----
class LineItem(BaseModel):
    description: str
    quantity: float | None = None
    unit_price: float | None = None
    amount: float | None = None

class InvoiceData(BaseModel):
    vendor_name: str | None = None
    invoice_number: str | None = None
    invoice_date: str | None = None
    total_amount: float | None = None
    currency: str | None = None
    line_items: list[LineItem] = []

class OCRService:
    def __init__(self):
        self.client = Mistral(api_key=settings.MISTRAL_API_KEY)

    def extract(self, file_bytes: bytes, mime: str = "application/pdf") -> InvoiceData:
        b64 = base64.b64encode(file_bytes).decode()
        resp = self.client.ocr.process(
            model="mistral-ocr-latest",
            document={
                "type": "document_url",
                "document_url": f"data:{mime};base64,{b64}",
            },
            # annotations feature returns structured JSON matching our schema
            document_annotation_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "invoice",
                    "schema": InvoiceData.model_json_schema(),
                },
            },
        )
        # resp.document_annotation is JSON text matching InvoiceData
        return InvoiceData.model_validate_json(resp.document_annotation)
```

> **FALLBACK STRATEGY**
>
> If annotations return partial data on a noisy scan, we fall back to reading
> `resp.pages[*].markdown` and running a lightweight second parse. Both the raw markdown and
> the structured result are stored in `match_history.ocr_raw`.

### 6.3 Matching & Knowledge Base Engine

**`app/services/matching_engine.py`**

```python
from rapidfuzz import fuzz
from app.schemas.invoice import InvoiceData
from app.repositories.knowledge_repo import KnowledgeRepo

# weighted contribution of each signal (sums to 1.0)
WEIGHTS = {"vendor": 0.45, "amount": 0.30, "lines": 0.15, "reference": 0.10}
AMOUNT_TOLERANCE = 0.02   # 2% delta considered a perfect amount match

class MatchingEngine:
    def __init__(self, kb: KnowledgeRepo):
        self.kb = kb

    async def score_candidates(self, inv: InvoiceData, pos: list[dict],
                               tenant_id: str) -> list[dict]:
        # 1) KB SHORT-CIRCUIT: known alias -> deterministic partner
        kb_partner = await self.kb.lookup(tenant_id, inv.vendor_name)

        ranked = []
        for po in pos:
            v = self._vendor_score(inv.vendor_name, po, kb_partner)
            a = self._amount_score(inv.total_amount, po["amount_total"])
            l = self._line_score(inv.line_items, po.get("lines", []))
            r = self._reference_score(inv.invoice_number, po)

            score = 100 * (WEIGHTS["vendor"]*v + WEIGHTS["amount"]*a
                           + WEIGHTS["lines"]*l + WEIGHTS["reference"]*r)
            ranked.append({"po_id": po["id"], "po_name": po["name"],
                           "score": round(score, 1),
                           "breakdown": {"vendor": v, "amount": a,
                                         "lines": l, "reference": r}})
        ranked.sort(key=lambda x: x["score"], reverse=True)
        return ranked

    def _vendor_score(self, raw, po, kb_partner) -> float:
        # KB hit = deterministic 1.0 when this PO's partner matches the learned map
        if kb_partner and po["partner_id"][0] == kb_partner.odoo_partner_id:
            return 1.0
        if not raw:
            return 0.0
        # token_sort_ratio handles word-order + partial vendor names
        return fuzz.token_sort_ratio(raw.lower(),
                                     po["partner_id"][1].lower()) / 100

    def _amount_score(self, inv_total, po_total) -> float:
        if not inv_total or not po_total:
            return 0.0
        delta = abs(inv_total - po_total) / max(po_total, 1)
        if delta <= AMOUNT_TOLERANCE:
            return 1.0
        return max(0.0, 1 - delta)   # linear decay beyond tolerance

    def _line_score(self, inv_lines, po_lines) -> float:
        if not inv_lines or not po_lines:
            return 0.0
        matched = 0
        for il in inv_lines:
            best = max((fuzz.partial_ratio(il.description.lower(),
                        pl["name"].lower()) for pl in po_lines), default=0)
            if best >= 80:
                matched += 1
        return matched / len(inv_lines)

    def _reference_score(self, inv_no, po) -> float:
        # bonus if invoice_origin / PO name appears (cross-reference)
        return 1.0 if inv_no and inv_no in po["name"] else 0.0
```

---

## 07 · Confidence Scoring Model

The score is a transparent weighted sum of four signals. Transparency matters: when the UI
shows 82%, the user can see *why* (e.g. strong vendor + amount, weak line items). This drives
trust and better corrections.

| Signal | Weight | How it is computed | Score = 1.0 when… |
|---|---|---|---|
| **Vendor** | 45% | KB alias lookup first; else `rapidfuzz.token_sort_ratio` | Known KB alias, or exact vendor name |
| **Amount** | 30% | Relative delta vs PO total, linear decay past 2% | Invoice total within 2% of PO |
| **Line items** | 15% | Fraction of invoice lines matching a PO line ≥ 80% | All lines match a PO line |
| **Reference** | 10% | Invoice/PO cross-reference presence | Explicit PO number on invoice |

### Decision bands (drive UI colour + default action)

| Band | Range | UI | Suggested default action |
|---|---|---|---|
| **High** | ≥ 85% | `GREEN` | Pre-select match; one-click confirm |
| **Medium** | 60–84% | `AMBER` | Show top 3 candidates; require explicit choice |
| **Low** | < 60% | `RED` | Manual search of Odoo POs; likely new vendor → KB entry |

> **SELF-TUNING WITHOUT ML**
>
> Because every correction reinforces the KB (`hit_count`, `last_used_at`), the vendor signal
> converges to deterministic over time for recurring vendors — the single biggest driver of
> the 45% weight. No model retraining needed in v1.

---

## 08 · Frontend Architecture & UI

```text
frontend/
├── app/
│   ├── layout.tsx                 # root layout, providers
│   ├── page.tsx                   # dashboard / recent invoices
│   ├── (auth)/login/page.tsx
│   └── invoices/
│       ├── page.tsx               # upload + list
│       └── [id]/page.tsx          # verification console (core screen)
├── components/
│   ├── ui/                        # Shadcn primitives (button, table, badge…)
│   ├── invoice/
│   │   ├── PdfViewer.tsx          # react-pdf preview pane
│   │   ├── ComparisonTable.tsx    # OCR field vs Odoo PO field, side by side
│   │   ├── ConfidenceBadge.tsx    # colour-banded score pill
│   │   ├── CandidateList.tsx      # ranked PO alternatives
│   │   └── CorrectionPanel.tsx    # re-map vendor / pick PO / confirm
│   └── UploadDropzone.tsx
├── lib/
│   ├── api.ts                     # axios instance + interceptors
│   ├── queries.ts                 # TanStack Query hooks
│   └── types.ts                   # shared TS types (mirror Pydantic)
├── hooks/
└── tailwind.config.ts
```

### Verification console — the core screen

**Left pane**

- **PdfViewer** — renders the uploaded invoice with zoom/scroll via `react-pdf`.
- Highlights the OCR-extracted fields when available.

**Right pane**

- **ConfidenceBadge** — big colour-banded score at top.
- **ComparisonTable** — each row: OCR value vs matched PO value, with a match/mismatch icon.
- **CandidateList** — collapsible list of alternative POs with their scores.
- **CorrectionPanel** — vendor re-map (searchable Odoo partner select), PO override, and
  Confirm / Reject buttons.

**Data flow**

1. Upload → `POST /invoices` returns a `match_history` id.
2. Console polls `GET /invoices/{id}` (TanStack Query) until OCR + matching complete.
3. Confirm → `POST /invoices/{id}/confirm` with the chosen PO + any vendor re-map.
4. Backend upserts the KB alias and pushes the draft Vendor Bill to Odoo, then flips status.

> **TYPE SAFETY ACROSS THE STACK**
>
> `lib/types.ts` mirrors the Pydantic schemas (optionally auto-generated from the OpenAPI
> spec), so the frontend and backend contracts never drift.

#### ConfidenceBadge (illustrative)

**`components/invoice/ConfidenceBadge.tsx`**

```tsx
export function ConfidenceBadge({ score }: { score: number }) {
  const band =
    score >= 85 ? { label: "High",   cls: "bg-green-100 text-green-700" } :
    score >= 60 ? { label: "Medium", cls: "bg-amber-100 text-amber-700" } :
                  { label: "Low",    cls: "bg-red-100 text-red-700" };
  return (
    <div className={`rounded-lg px-4 py-2 font-semibold ${band.cls}`}>
      {score.toFixed(0)}% · {band.label} confidence
    </div>
  );
}
```

---

## 09 · Deployment, Hosting & Database

The system deploys as **three independent tiers**, not two. This matters: Vercel is optimised
for the Next.js frontend, but a long-running FastAPI server with an async connection pool is
not a fit for Vercel's per-request serverless functions. The backend therefore runs on a
persistent host, and PostgreSQL is a managed cloud database — **Neon serverless Postgres** —
reachable over SSL from the backend.

| # | Tier | Detail |
|---|---|---|
| 1 | **Frontend — Vercel** | Next.js 14 App Router. Static + server components, streaming uploads. Talks to the backend over HTTPS. |
| 2 | **Backend — Railway / Render** | FastAPI (Docker). Persistent process keeps the async pool alive. Holds Odoo + Mistral secrets. |
| 3 | **Database — Neon** | Serverless Postgres, scale-to-zero, pgvector-ready. Connected via a pooled SSL connection string. |

> **WHY NOT DEPLOY FASTAPI ON VERCEL?**
>
> Vercel functions spin up and tear down per request, which breaks SQLAlchemy's persistent
> connection pool and cold-starts the Python app on every call. The FastAPI backend belongs
> on a host that keeps a process alive — **Railway** (simplest, co-locate the DB), **Render**,
> or **Fly.io**. Vercel stays dedicated to the frontend.

### Why Neon for the database

- **Serverless & scale-to-zero** — idle databases cost nothing; ideal for an internal AP tool
  with bursty usage.
- **First-party Vercel integration** and standard Postgres wire protocol — works unchanged
  with SQLAlchemy 2.0 + asyncpg.
- **pgvector built in** — if v2 adds semantic line-item matching, no migration off the
  platform.
- **Branching** — an instant copy-on-write DB per preview/PR, useful for testing migrations
  safely.
- **Free tier** (0.5 GB storage, ~100 compute-hours/month) covers development and a small
  production pilot; Pro from ~$19/mo scales with usage.

Alternative if we later want bundled auth/storage/realtime: **Supabase** (a full backend
platform on Postgres). For v1 we only need the database, so Neon is the leaner fit. If the
backend runs on Railway, Railway's own managed Postgres is also valid and co-locates DB + app
on one bill.

### The connection-pooling rule (important)

Neon (like most managed Postgres) exposes **two** connection strings. Using the wrong one in
the wrong place is the most common first-deploy failure:

| Connection | Host suffix | Use it for | Why |
|---|---|---|---|
| **Pooled** | `-pooler` | App runtime queries (FastAPI) | Routed through PgBouncer — survives many short-lived connections without exhausting Postgres limits. |
| **Direct** | (no `-pooler`) | Alembic migrations | PgBouncer transaction mode lacks the session-level features migrations require. |

**`.env` — hosted (Neon)**

```dotenv
# Runtime: POOLED connection (note the -pooler host) + async driver
DATABASE_URL=postgresql+asyncpg://user:pass@ep-xxx-pooler.us-east-2.aws.neon.tech/apdb?ssl=require

# Migrations: DIRECT connection (no -pooler) for Alembic
MIGRATION_DATABASE_URL=postgresql+asyncpg://user:pass@ep-xxx.us-east-2.aws.neon.tech/apdb?ssl=require
```

Two details that cause silent failures on first deploy:

- `ssl=require` — Neon rejects non-SSL connections. Missing it is the classic "works locally,
  fails deployed" error.
- **Scale-to-zero cold start** — the first query after the DB has been idle adds ~500 ms. Fine
  for an internal tool; keep one compute unit warm if it ever matters.

#### Config wiring (extends Section 04)

**`app/core/config.py` (additions)**

```python
class Settings(BaseSettings):
    # ... existing fields ...
    DATABASE_URL: str            # pooled  -> used by the async app engine
    MIGRATION_DATABASE_URL: str  # direct  -> used by Alembic env.py only
```

### Deployment responsibilities at a glance

| Component | Platform | Deploy method | Holds |
|---|---|---|---|
| Next.js frontend | Vercel | Git push → auto build | Public API base URL only |
| FastAPI backend | Railway / Render / Fly.io | Dockerfile | Odoo creds, Mistral key, JWT secret, DB URLs |
| PostgreSQL | Neon | Managed (provisioned) | App data + audit + KB |
| Invoice files | Object storage (S3/R2) — v1.1 | SDK | Uploaded PDFs (not on the app disk) |

> **ONE ENVIRONMENT, EVERYWHERE**
>
> Recommendation: point *local development* at a free Neon branch too, so "works on my
> machine" and "works in production" are the same database engine. This eliminates the exact
> SSL/pooling surprises this section describes from ever appearing only at deploy time.

---

## 10 · Delivery Plan, Risks & Sign-off

### Phased plan (v1 / MVP)

| Phase | Scope | Key deliverables | Est. |
|---|---|---|---|
| **P1** Foundation | Repo, FastAPI skeleton, DB + Alembic, auth, Next.js shell, CI | Running scaffold; migrations; login; upload endpoint stub | Week 1 |
| **P2** OCR + Odoo | Mistral OCR service, Odoo XML-RPC client, PO fetch | Invoice → structured JSON; live PO list from staging Odoo | Week 2 |
| **P3** Matching + KB | Scoring engine, knowledge base, `match_history` logging | Ranked candidates + confidence; KB upsert on correction | Week 3 |
| **P4** Verification UI | Side-by-side console: PDF viewer, comparison, correction | End-to-end happy path: upload → verify → confirm | Week 4 |
| **P5** Write-back + hardening | Draft Vendor Bill push, retries, error states, audit view, deploy | Odoo write-back; demo-ready deployed MVP | Week 5 |

Estimate assumes a single full-stack engineer and available Odoo staging access. Sequencing
follows the patch-by-patch, infrastructure-first workflow: scaffolding & services before
feature code.

### Risks & assumptions

| Item | Type | Mitigation / note |
|---|---|---|
| OCR accuracy on poor scans | Risk | Human-in-the-loop catches errors; store raw markdown fallback; measure field-level accuracy in P2. |
| Odoo XML-RPC latency on large PO sets | Risk | Filter by state + date window; batch line reads; cache within a request. |
| Mistral OCR API cost / rate limits | Risk | Confirm pricing + quota; add tenacity backoff; consider per-tenant caps. |
| Single-tenant now, multi-tenant later | Assumption | `tenant_id` is baked into every table from day one to avoid a painful migration. |
| Odoo credentials & test data available | Assumption | Need a staging Odoo instance with representative POs for P2 onward. |
| Three-tier hosting (Vercel + Railway/Render + Neon) | Assumption | Backend runs as a persistent server, not on Vercel. DB is Neon over SSL. See Section 09. |

### Open questions for manager decision

1. Is **pgvector / semantic line-item matching** in scope for v1, or deferred to v2?
2. Confirm the **Odoo write-back target**: draft Vendor Bill (recommended) vs. only a PO
   status flag?
3. Is **multi-tenant** required at launch, or single-tenant with the `tenant_id` hook for
   later?
4. Target **Odoo version** for the first client (16 / 17 / 18 / 19)?
5. Any **compliance constraints** on storing invoice files / OCR data (retention, region)?

### Approval & Sign-off

Please mark one:  **[ ] Approved — start Phase 1**  ·  **[ ] Approve with changes**  ·
**[ ] Revise & resubmit**

```text
_______________________________________        _______________________________
Manager — name & signature                     Date
```

Comments / additions to scope:

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│                                                                              │
│                                                                              │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

*AP Invoice-to-PO Automation — Architecture & Implementation Plan v1.1 · Prepared by HST
(HSxTech / HST Advantage) · 12 August 2026 · Confidential*
