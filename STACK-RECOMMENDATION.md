# Stack Recommendation — Evidence-Based

**Question asked:** what is the best stack for AP invoice-to-PO automation, judged on merit
and future scalability rather than existing preference?

**Method:** researched current (2026) benchmarks, vendor documentation and pricing for each
layer. Where a source is vendor-published and therefore self-interested, it is flagged as
such and not relied on alone.

This document **revises both existing plans** (`docs/` blueprint and
`AP_Invoice_to_PO_Automation_Plan.md`). Where it disagrees with them, the disagreement and
its evidence are stated explicitly.

---

## The recommendation

| Layer | Recommendation | Change vs existing plans |
|---|---|---|
| **Invoice extraction** | **Azure AI Document Intelligence** `prebuilt-invoice`, primary. LLM as a second-pass reconciler, not the extractor. | **Major change.** Both plans made Mistral OCR the primary extractor. |
| **Odoo integration** | **JSON-2 API** (Odoo 19+) behind an adapter, with an XML-RPC adapter for older customers. | **Major change.** Both plans build on XML-RPC, which Odoo has deprecated. |
| **Backend** | Python 3.12 + FastAPI + Pydantic v2 | No change — nothing found to displace it. |
| **Job processing** | **ARQ** now; Temporal at a defined trigger point | **Change.** Both plans process inline in the request. |
| **Database** | PostgreSQL, shared schema + `tenant_id` + **Row-Level Security** | **Change.** Adds RLS as an enforced safety net. |
| **DB hosting** | Neon (or RDS if already on AWS) | No change from the v1.1 plan. |
| **Auth** | **Clerk** now → WorkOS when enterprise SSO/SCIM lands | **Change.** My blueprint said self-hosted JWT. |
| **Frontend** | Next.js App Router + TypeScript + Tailwind + shadcn/ui + TanStack Query | No change. |
| **Compute** | ECS Fargate (or Fly.io); **not** Vercel, **not** Kubernetes yet | **Change.** v1.1 plan proposed Railway. |

---

## 1. Invoice extraction — the most consequential decision

### The finding

Specialised invoice models beat general-purpose LLM OCR **on line items specifically**, and
line items are exactly what a PO-matching engine depends on.

| Engine | Field-level | Line items |
|---|---|---|
| Azure Document Intelligence | ~96% (printed text) | **87%** |
| AWS Textract AnalyzeExpense | 93% | **82–89%** |
| Google Document AI invoice parser | 92% | **87%** |
| GPT-4o + OCR preprocessing | — | **57%** |

The [BusinessWareTech IDP benchmark](https://www.businesswaretech.com/intelligent-document-processing-benchmark)
found GPT-4o with OCR preprocessing scored **57%** on line-item extraction versus Azure's 87%
and Textract's 82%. [AIMultiple](https://aimultiple.com/invoice-ocr) and
[Hypatos](https://www.hypatos.ai/knowledge-base/invoice-processing-accuracy-benchmarks-idp)
corroborate the shape: unique header fields (vendor, total) hit 99%+ almost everywhere, while
line items and multi-row tax breakdowns are where engines separate.

This is counter-intuitive — the general model is *worse* at the structured-table task — which
is why it is worth acting on rather than assuming the newest model wins.

### Why not Mistral OCR as primary

Mistral's own benchmark reports ~94.9% across mixed document types, ahead of Google (83.4%)
and Azure OCR (89.5%). But that is a vendor self-report, and independent testing raises
specific problems that hit invoices hardest:

- **Tables with fewer than two rows are not recognised as tables** and get flattened into
  key-value pairs. Short invoices — the common case in AP — are precisely this shape.
- Reports of hallucinated text on low-resolution scans and missed small-font content
  ([Docsumo benchmark](https://www.docsumo.com/blogs/ocr/docsumo-ocr-benchmark-report)).

> **Source caveat:** Docsumo sells a competing product, so treat its numbers as directional,
> not authoritative. It is cited because its specific, testable claim (sub-two-row tables) is
> consistent with the independent line-item finding above — not because the vendor says so.

Mistral OCR is genuinely fast and cheap and its single-call OCR-plus-annotation design is
elegant. It is a reasonable **fallback or second opinion**. It is the wrong choice for the
component whose output feeds a financial matching decision.

### Cost — and a trap in Google's billing

| Engine | Headline | Effective cost, 1-page invoice |
|---|---|---|
| Azure prebuilt-invoice | $10 / 1,000 pages | **~$0.01** |
| AWS AnalyzeExpense | $10 / 1,000 pages | **~$0.01** |
| Google invoice parser | $0.10 per 10-page block | **~$0.10** |

Google bills in **10-page blocks per document**, rounding up — so a one-page invoice costs the
same as a ten-page one. For AP, where most invoices are one to three pages, Google is
effectively **~10× more expensive** than the identical headline rate suggests. Azure and AWS
bill per page. ([Google pricing](https://invoicedataextraction.com/blog/google-document-ai-invoice-parser-pricing),
[AWS pricing](https://aws.amazon.com/textract/pricing/),
[Azure pricing](https://docuocr.com/blog/azure-document-intelligence-pricing))

At 10,000 invoices/month: Azure ≈ **$100–200/mo**, Google ≈ **$1,000/mo**.

### The architecture: hybrid, not either/or

The 2026 production consensus is a two-stage pipeline, not a single model
([Vellum](https://www.vellum.ai/blog/document-data-extraction-llms-vs-ocrs),
[Unstract](https://unstract.com/blog/ai-invoice-processing-and-data-extraction/),
[Parsli](https://parsli.co/blog/llm-ocr-vs-traditional-ocr)):

```
                    ┌─────────────────────────────────────────┐
 invoice bytes ───► │ Stage 1 — Azure prebuilt-invoice        │
                    │ structured fields + line items + boxes  │
                    │ + per-field confidence                  │
                    └────────────────┬────────────────────────┘
                                     │
                     any field confidence < 0.85,
                     or totals don't reconcile?
                          │                    │
                         no                   yes
                          │                    ▼
                          │      ┌─────────────────────────────┐
                          │      │ Stage 2 — LLM reconciler    │
                          │      │ sees layout-preserved text  │
                          │      │ + Stage 1 output, repairs   │
                          │      │ only the low-confidence     │
                          │      │ fields                      │
                          │      └────────────┬────────────────┘
                          ▼                   ▼
                    ┌─────────────────────────────────────────┐
                    │ validated ExtractedInvoice → matching    │
                    └─────────────────────────────────────────┘
```

Two things this buys you that a single LLM call cannot:

1. **Per-field confidence scores.** Azure returns them; an LLM does not. This is what lets you
   route only genuinely uncertain documents to the expensive second stage, and what lets the
   UI highlight the specific fields a human should check. Your existing confidence-score UI
   becomes far more useful with real per-field numbers behind it.
2. **Bounding boxes.** Azure returns coordinates per field. That is what enables *click a
   field, highlight it on the PDF* — the feature both plans list as the natural next step and
   which the verification screen is already architected for.

Keep the `OCRService` interface from the existing design. Swap the implementation, keep the
`ExtractedInvoice` schema. The rest of the system does not care.

---

## 2. Odoo — XML-RPC is deprecated

### The finding

**Odoo has deprecated the `/xmlrpc`, `/xmlrpc/2` and `/jsonrpc` endpoints as of Odoo 19**, and
introduced a replacement called the **External JSON-2 API**. The Odoo 19 documentation carries
the deprecation notice and ships a dedicated *"Migrating from XML-RPC / JSON-RPC"* section.
([Odoo 19 External RPC API](https://www.odoo.com/documentation/19.0/developer/reference/external_rpc_api.html),
[Odoo 19 External API](https://www.odoo.com/documentation/19.0/developer/reference/external_api.html))

**Both existing plans build the ERP integration entirely on XML-RPC.** For a system whose
stated goal is future scalability, that is building the load-bearing integration on a
deprecated protocol.

> **Honest caveat on the timeline.** Sources disagree on the removal date. A
> [tracked integration issue](https://github.com/n8n-io/n8n/issues/21545) states removal in
> **Odoo 20**; a [secondary guide](https://www.getknit.dev/blog/odoo-api-integration-guide-in-depth)
> says **Odoo 22 (autumn 2028)** and **Odoo Online 21.1 (winter 2027)**. I could not extract
> the exact sentence from Odoo's own page — it truncated at the notice both times. **Verify
> the date directly in the Odoo 19 docs before planning around it.** The decision does not
> depend on which is right: deprecated-now means don't build new code on it either way.

### Why this is good news, not bad

Moving to JSON-2 **deletes** the most complex machinery in the existing backend design:

| XML-RPC (current plans) | JSON-2 |
|---|---|
| `xmlrpc.client` is fully blocking | Plain HTTP — use `httpx` natively async |
| Needs `anyio.to_thread.run_sync` on every call | No threadpool wrapper at all |
| Bounded by anyio's 40-thread limiter | Bounded only by the HTTP connection pool |
| Verbose XML payloads | **40–60% smaller payloads** |
| Password or key in every call | First-class API keys |
| `[id, "Name"]` many2one tuples, `False` for null | Same ORM, cleaner JSON |

The `anyio.to_thread.run_sync` wrapper, the socket-timeout save/restore dance, and the thread
limiter capacity note in `docs/03-services.md` all disappear.

### What to build

Keep `OdooService` as an **interface**, with two adapters behind it:

```python
class OdooClient(Protocol):
    async def fetch_open_purchase_orders(...) -> list[OdooPurchaseOrder]: ...
    async def create_vendor_bill(...) -> dict: ...

class Json2OdooClient:   # Odoo 19+  — httpx, async, default
class XmlRpcOdooClient:  # Odoo 16-18 — legacy, threadpool-wrapped
```

Select the adapter per organization from a stored `odoo_version`. This is not
over-engineering: it is the difference between supporting customers on Odoo 17 today and
customers on Odoo 20 in two years without a rewrite. It also answers open question #4 in the
v1.1 plan ("target Odoo version") — you support both.

---

## 3. Processing model — stop doing OCR inside the HTTP request

Both plans run OCR + PO fetch + matching **inline in the upload request**, taking 5–20 seconds.
That works on a laptop and fails in production: proxy timeouts, no retry on a transient Odoo
outage, no visibility, and one slow tenant occupying a worker.

The workload is textbook long-running-with-human-in-the-loop:

> Use Celery/ARQ for background jobs and lightweight scheduling; use **Temporal when your
> workflows cross process boundaries and require strong durability, observability, and
> governance** — long-running pipelines, cross-service orchestration, and **human-in-the-loop
> steps**. ([comparison](https://suhasbhairav.com/blog/celery-vs-temporal-for-ai-agent-tasks-background-jobs-vs-durable-execution))

That describes this system exactly. But Temporal is real operational weight for v1.

**Recommendation — ARQ now, with a defined trigger to move:**

- **ARQ**, not Celery. ARQ is asyncio-native; Celery's architecture assumes synchronous
  execution and creates constant friction in an async FastAPI app
  ([benchmark discussion](https://medium.com/@rameshkannanyt0078/fastapi-background-tasks-celery-vs-arq-vs-rq-2026-benchmarks-decision-guide-f99598aa21eb)).
- `POST /invoices/upload` returns **202** immediately with the row in `processing`. The
  frontend already handles this — `useMatchDetail` polls while status is `pending`/`processing`.
- Write each pipeline step as a **pure function** taking and returning plain data. Then
  migrating to Temporal is wrapping them as activities, not rewriting them.

**Move to Temporal when any of these becomes true:** you need to retry a failed Odoo push
hours later without a human, you add approval chains with timeouts, you need per-step audit
for compliance, or you exceed ~10k invoices/month.

---

## 4. Multi-tenancy — add Row-Level Security

Both plans scope tenants in application code — the v1.1 plan with a `tenant_id` column, mine
with a repository base class. Both are one forgotten `WHERE` clause from a cross-tenant leak.

The 2026 default is **shared schema + `tenant_id` + PostgreSQL RLS as an enforced safety net**
([Nile](https://www.thenile.dev/blog/multi-tenant-rls),
[PlanetScale](https://planetscale.com/blog/approaches-to-tenancy-in-postgres),
[pattern comparison](https://dasroot.net/posts/2026/01/multi-tenancy-database-patterns-schema-database-row-level/)).
One cited study attributes **over 70% of multi-tenant data breaches to application-layer
isolation flaws** — the exact failure mode both plans are exposed to.

```sql
ALTER TABLE match_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE match_history FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON match_history
  USING (organization_id = current_setting('app.current_org', true)::uuid);
```

Set `app.current_org` once per request in the `get_db` dependency, after authentication.
Application-level scoping stays — RLS is the backstop for when someone forgets it.

> **Interaction with pooling:** `SET LOCAL` inside a transaction is safe with PgBouncer in
> transaction mode. A session-level `SET` is **not** — it leaks across pooled connections to
> other tenants, which is worse than having no RLS at all. Use `SET LOCAL`, and test it against
> a pooled connection string, not just a direct one.

Schema-per-tenant only becomes right below ~500 tenants with contractual isolation
requirements. Database-per-tenant only when regulation demands it.

---

## 5. Auth — buy, don't build

My blueprint specified self-hosted argon2 + PyJWT. On the evidence, that is the wrong call for
a B2B SaaS.

The consensus across multiple comparisons is that building auth means months lost to OAuth
flows, SAML edge cases and SCIM provisioning bugs, and that **buying is the default**
([PropelAuth](https://www.propelauth.com/post/6-best-auth-platforms-b2b-saas),
[comparison](https://futurepicker.com/en/saas-authentication-tool-comparison-2026/)).

**Clerk** for v1: its **organizations** primitive maps directly onto the tenant model this
system needs — orgs, teams, seats, invitations — and it has the strongest Next.js integration.
**WorkOS** when a customer demands SAML SSO and SCIM directory sync.

> **Source caveat:** the head-to-head comparisons are largely published by auth vendors and
> are self-interested about *which* to buy. The build-vs-buy conclusion is consistent across
> all of them including neutral write-ups, so treat "buy" as well-supported and "buy Clerk
> specifically" as a reasonable default to validate against your own pricing at seat count.

Keep FastAPI verifying the JWT/JWKS itself, and keep your own `organizations` table keyed to
the provider's org id. That way the identity provider is replaceable and never becomes the
system of record for your business data.

---

## 6. Compute — not Vercel, not Railway, not Kubernetes yet

The v1.1 plan is **right** that FastAPI does not belong on Vercel: per-request serverless
functions break SQLAlchemy's connection pool and cold-start Python on every call.

But its proposed alternative needs revisiting. Railway is well regarded for backend-heavy SaaS,
yet is called out as **a poor default for production FastAPI specifically when there is
long-running work, scheduled jobs, or file processing**
([assessment](https://stackandsails.substack.com/p/railway-reliable-for-fastapi-2026)) — which
is precisely this workload once OCR moves to a queue.

**ECS Fargate** is the recommended middle: many SaaS teams use ECS/Fargate before moving to
Kubernetes because it gives production structure without the operational overhead, and
Kubernetes should not be chosen "only because it sounds enterprise-ready"
([comparison](https://f3fundit.com/micro-saas-hosting-infrastructure-vercel-vs-railway-vs-render-vs-fly-io-2026/)).
It also co-locates with Textract as a fallback OCR path.

**Fly.io** is the lighter alternative if you want less AWS surface area.

At 0–1,000 users the honest answer is that hosting choice affects velocity more than
performance — so this is reversible, unlike the OCR and Odoo decisions above.

---

## Final stack

```
┌──────────────────────────────────────────────────────────────────┐
│ Next.js (App Router) · TypeScript · Tailwind · shadcn/ui         │
│ TanStack Query · Clerk                             → Vercel      │
└───────────────────────────┬──────────────────────────────────────┘
                            │ HTTPS, same-origin rewrite
┌───────────────────────────▼──────────────────────────────────────┐
│ FastAPI · Python 3.12 · Pydantic v2 · SQLAlchemy 2.0 async       │
│ ARQ workers (→ Temporal at trigger)          → ECS Fargate       │
└──────┬─────────────────┬──────────────────────┬──────────────────┘
       │                 │                      │
┌──────▼──────┐  ┌───────▼────────────┐  ┌──────▼───────────────┐
│ PostgreSQL  │  │ Azure Doc Intel    │  │ Odoo JSON-2 API      │
│ + RLS       │  │ prebuilt-invoice   │  │ (XML-RPC adapter for │
│ → Neon/RDS  │  │ + LLM reconciler   │  │  Odoo ≤18)           │
└─────────────┘  └────────────────────┘  └──────────────────────┘
       │
┌──────▼──────┐
│ S3 / R2     │  invoice blobs — not the app disk
└─────────────┘
```

## What actually has to change

Most of the existing design survives. The layering, the schema, the matching engine, the
verification UI, the knowledge-base learning loop and the confirm-before-push ordering are all
unaffected. Four things change:

| Priority | Change | Effort | Why it can't wait |
|---|---|---|---|
| 1 | Odoo XML-RPC → JSON-2 behind an adapter | ~2 days | Deprecated protocol; retrofitting later touches every call site |
| 2 | Mistral → Azure prebuilt-invoice as primary | ~2 days | 57% vs 87% line-item accuracy directly determines match quality |
| 3 | Inline processing → ARQ + 202 | ~2 days | Timeouts and un-retryable failures are production-only bugs |
| 4 | Add RLS policies | ~1 day | Cross-tenant leak is unrecoverable reputationally |

Items 2 and 4 are additive and can follow. **Item 1 should be settled before writing any Odoo
code**, because it is the one that gets more expensive every day it is deferred.

---

## Sources

Extraction accuracy & benchmarks:
[BusinessWareTech IDP benchmark](https://www.businesswaretech.com/intelligent-document-processing-benchmark) ·
[AIMultiple invoice OCR](https://aimultiple.com/invoice-ocr) ·
[Hypatos production accuracy](https://www.hypatos.ai/knowledge-base/invoice-processing-accuracy-benchmarks-idp) ·
[Textract vs Doc AI vs Azure](https://invoicedataextraction.com/blog/aws-textract-vs-google-document-ai-vs-azure-document-intelligence) ·
[Docsumo OCR benchmark (vendor)](https://www.docsumo.com/blogs/ocr/docsumo-ocr-benchmark-report) ·
[Mistral OCR deep dive](https://cohorte.co/blog/mistral-ocr-a-deep-dive-into-next-generation-document-understanding)

Hybrid pipeline design:
[Vellum — LLMs vs OCRs](https://www.vellum.ai/blog/document-data-extraction-llms-vs-ocrs) ·
[Unstract invoice guide](https://unstract.com/blog/ai-invoice-processing-and-data-extraction/) ·
[Parsli LLM vs traditional OCR](https://parsli.co/blog/llm-ocr-vs-traditional-ocr)

Pricing:
[Azure](https://docuocr.com/blog/azure-document-intelligence-pricing) ·
[AWS Textract](https://aws.amazon.com/textract/pricing/) ·
[Google Document AI](https://invoicedataextraction.com/blog/google-document-ai-invoice-parser-pricing)

Odoo:
[External JSON-2 API (19.0)](https://www.odoo.com/documentation/19.0/developer/reference/external_api.html) ·
[External RPC API deprecation (19.0)](https://www.odoo.com/documentation/19.0/developer/reference/external_rpc_api.html) ·
[n8n deprecation issue](https://github.com/n8n-io/n8n/issues/21545) ·
[Knit integration guide](https://www.getknit.dev/blog/odoo-api-integration-guide-in-depth)

Queues, tenancy, auth, hosting:
[ARQ vs Celery vs RQ](https://medium.com/@rameshkannanyt0078/fastapi-background-tasks-celery-vs-arq-vs-rq-2026-benchmarks-decision-guide-f99598aa21eb) ·
[Celery vs Temporal](https://suhasbhairav.com/blog/celery-vs-temporal-for-ai-agent-tasks-background-jobs-vs-durable-execution) ·
[Nile — multi-tenant RLS](https://www.thenile.dev/blog/multi-tenant-rls) ·
[PlanetScale — tenancy in Postgres](https://planetscale.com/blog/approaches-to-tenancy-in-postgres) ·
[PropelAuth — B2B auth platforms](https://www.propelauth.com/post/6-best-auth-platforms-b2b-saas) ·
[Micro-SaaS hosting comparison](https://f3fundit.com/micro-saas-hosting-infrastructure-vercel-vs-railway-vs-render-vs-fly-io-2026/) ·
[Railway for FastAPI](https://stackandsails.substack.com/p/railway-reliable-for-fastapi-2026)
