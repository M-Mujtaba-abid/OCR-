/**
 * End-to-end verification of the extraction and matching pipeline.
 *
 * Runs against the live backend, live R2 and live Mistral. Odoo-dependent
 * checks are reported separately so a missing Odoo credential is never
 * confused with a code failure.
 *
 *   node scripts/verify-pipeline.mjs <adminEmail> <adminPassword>
 */

import { readFileSync } from "node:fs";
import { deflateSync } from "node:zlib";

const API = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";
const BASE = `${API}/api/v1`;
const ORIGIN = "http://localhost:3000";

/**
 * Build the test invoice in-process.
 *
 * Deliberately not read from disk: a verification suite that depends on a file
 * somebody generated once is a suite that fails on a fresh checkout, and the
 * assertions below are written against these exact values.
 */
function buildInvoicePdf() {
  const rows = [
    [60, 780, 18, "ACME TOOLS LIMITED"],
    [60, 758, 10, "123 Industrial Area, Block B, Karachi"],
    [60, 744, 10, "sales@acmetools.com"],
    [60, 700, 14, "PURCHASE INVOICE"],
    [60, 676, 10, "Invoice / PO Number: PO-2026-0089"],
    [60, 662, 10, "Order Date: 2026-08-10"],
    [60, 648, 10, "Currency: USD"],
    [60, 610, 11, "Description                 Qty     Unit Price     Subtotal"],
    [60, 592, 10, "Heavy Duty Industrial Drill   2         150.00        300.00"],
    [60, 576, 10, "Carbide Drill Bit Set        10          12.50        125.00"],
    [60, 560, 10, "Safety Goggles                4           8.75         35.00"],
    [60, 520, 10, "Untaxed Amount:                                       460.00"],
    [60, 504, 10, "Tax (5%):                                              23.00"],
    [60, 486, 12, "TOTAL:                                                483.00"],
  ];

  const content =
    "BT\n" +
    rows
      .map(([x, y, size, text]) => `/F1 ${size} Tf 1 0 0 1 ${x} ${y} Tm (${text}) Tj\n`)
      .join("") +
    "ET\n";
  const stream = deflateSync(Buffer.from(content, "latin1"));

  const objects = [
    Buffer.from("<< /Type /Catalog /Pages 2 0 R >>"),
    Buffer.from("<< /Type /Pages /Kids [3 0 R] /Count 1 >>"),
    Buffer.from(
      "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] " +
        "/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
    ),
    Buffer.concat([
      Buffer.from(`<< /Length ${stream.length} /Filter /FlateDecode >>\nstream\n`),
      stream,
      Buffer.from("\nendstream"),
    ]),
    Buffer.from("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"),
  ];

  const parts = [Buffer.from("%PDF-1.7\n")];
  const offsets = [];
  let length = parts[0].length;
  objects.forEach((body, i) => {
    offsets.push(length);
    const chunk = Buffer.concat([
      Buffer.from(`${i + 1} 0 obj\n`),
      body,
      Buffer.from("\nendobj\n"),
    ]);
    parts.push(chunk);
    length += chunk.length;
  });

  const xref = length;
  parts.push(
    Buffer.from(
      `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n` +
        offsets.map((o) => `${String(o).padStart(10, "0")} 00000 n \n`).join("") +
        `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\n` +
        `startxref\n${xref}\n%%EOF\n`,
    ),
  );

  return Buffer.concat(parts);
}

const [adminEmail, adminPassword] = process.argv.slice(2);
if (!adminEmail || !adminPassword) {
  console.error("Usage: node scripts/verify-pipeline.mjs <adminEmail> <adminPassword>");
  process.exit(2);
}

let pass = 0;
let fail = 0;
let blocked = 0;

const check = (name, ok, detail = "") => {
  if (ok) {
    pass += 1;
    console.log(`  PASS  ${name}`);
  } else {
    fail += 1;
    console.log(`  FAIL  ${name}${detail ? ` — ${detail}` : ""}`);
  }
};

const skip = (name, why) => {
  blocked += 1;
  console.log(`  SKIP  ${name} — ${why}`);
};

async function call(path, { method = "GET", body, token, form } = {}) {
  const headers = { Accept: "application/json", Origin: ORIGIN };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body) headers["Content-Type"] = "application/json";
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: form ?? (body ? JSON.stringify(body) : undefined),
  });
  return { status: res.status, json: await res.json().catch(() => null) };
}

async function signIn(email, password) {
  const r = await call("/auth/login", { method: "POST", body: { email, password } });
  if (r.status !== 200) {
    console.error(`\nCould not sign in as ${email}: ${r.status} ${r.json?.message}`);
    process.exit(2);
  }
  return { token: r.json.data.access_token, user: r.json.data.user };
}

/** Poll until the invoice leaves the transient states. */
async function settle(id, token, timeoutMs = 90_000) {
  const transient = new Set(["uploaded", "ocr_queued", "ocr_processing", "matching"]);
  // The caller claims the row before answering 202, so one short beat is
  // enough for that commit to land before the first poll.
  await new Promise((r) => setTimeout(r, 400));
  const deadline = Date.now() + timeoutMs;
  let invoice;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 1500));
    invoice = (await call(`/invoices/${id}`, { token })).json?.data;
    if (invoice && !transient.has(invoice.status)) return invoice;
  }
  return invoice;
}

async function upload(token, bytes, name, extra = {}) {
  const fd = new FormData();
  fd.append("files", new Blob([bytes], { type: "application/pdf" }), name);
  for (const [k, v] of Object.entries(extra)) fd.append(k, v);
  return call("/invoices/upload", { method: "POST", token, form: fd });
}

console.log(`\nBackend: ${BASE}\n`);

const admin = await signIn(adminEmail, adminPassword);
const pdf = buildInvoicePdf();

const memberEmail = `pipe-${Math.random().toString(36).slice(2, 9)}@example.com`;
const memberPassword = "SuperSecret123";
await call("/auth/register", {
  method: "POST",
  body: { email: memberEmail, password: memberPassword, full_name: "Pipeline Bot" },
});
const member = await signIn(memberEmail, memberPassword);

/* 1. automatic extraction ---------------------------------------------- */
console.log("AUTOMATIC EXTRACTION ON UPLOAD");
const t0 = Date.now();
const up = await upload(member.token, pdf, "acme-invoice.pdf", { member_ref_no: "REF-1" });
check("upload → 201", up.status === 201, `got ${up.status}`);

const created = up.json?.data?.uploaded?.[0];
check("row starts as 'uploaded'", created?.status === "uploaded", created?.status);

const done = await settle(created.id, member.token);
check(
  "reaches ocr_done without intervention",
  done?.status === "ocr_done",
  `${done?.status} · ${done?.ocr_error ?? ""}`,
);
console.log(`        (${((Date.now() - t0) / 1000).toFixed(1)}s end to end)`);

const ex = done?.extracted_json;
check("extracted_json is stored", Boolean(ex));
check("vendor read", ex?.vendor_name?.toUpperCase().includes("ACME"), ex?.vendor_name);
check("reference read", ex?.po_number === "PO-2026-0089", ex?.po_number);
check("date normalised to ISO", ex?.order_date === "2026-08-10", ex?.order_date);
check("3 line items", ex?.items?.length === 3, `got ${ex?.items?.length}`);
check("untaxed = 460", ex?.untaxed_amount === 460, `got ${ex?.untaxed_amount}`);
check("tax = 23", ex?.tax_amount === 23, `got ${ex?.tax_amount}`);
check("total = 483", ex?.total_amount === 483, `got ${ex?.total_amount}`);
check(
  "numbers are numbers, not strings",
  typeof ex?.total_amount === "number" && typeof ex?.items?.[0]?.unit_price === "number",
);
check("promoted scalars agree with the JSON", done?.extracted_total === ex?.total_amount);
check("extracted_untaxed promoted", done?.extracted_untaxed === ex?.untaxed_amount);
check("line_count matches items", done?.extracted_line_count === ex?.items?.length);
check("line rows written to the DB", done?.lines?.length === 3, `got ${done?.lines?.length}`);
check(
  "line rows carry qty and price",
  done?.lines?.[0]?.quantity === 2 && done?.lines?.[0]?.unit_price === 150,
);
check("ocr_model recorded", done?.ocr_model === "mistral-ocr-latest", done?.ocr_model);
check("no ocr_error on success", !done?.ocr_error);

/* 2. a document that cannot be read ------------------------------------ */
console.log("\nUNREADABLE DOCUMENT");
const junk = Buffer.from("%PDF-1.7\n" + "\x00".repeat(200));
const junkUp = await upload(member.token, junk, "corrupt.pdf");
if (junkUp.status !== 201) {
  check("corrupt PDF accepted for processing", false, `got ${junkUp.status}`);
} else {
  const junkDone = await settle(junkUp.json.data.uploaded[0].id, member.token);
  check(
    "ends in a terminal state, never stuck",
    junkDone && !["uploaded", "ocr_processing"].includes(junkDone.status),
    junkDone?.status,
  );
  check(
    "either read it or recorded a reason",
    junkDone?.status === "ocr_done" || Boolean(junkDone?.ocr_error),
    junkDone?.status,
  );
}

/* 3. authorization ------------------------------------------------------ */
console.log("\nAUTHORIZATION");
const memberOcr = await call(`/invoices/${created.id}/ocr`, {
  method: "POST",
  token: member.token,
});
check("member POST /ocr → 403", memberOcr.status === 403, `got ${memberOcr.status}`);

const memberMatch = await call(`/invoices/${created.id}/match`, {
  method: "POST",
  token: member.token,
});
check("member POST /match → 403", memberMatch.status === 403, `got ${memberMatch.status}`);

const memberOdoo = await call("/odoo/purchase-orders", { token: member.token });
check("member GET /odoo/purchase-orders → 403", memberOdoo.status === 403, `got ${memberOdoo.status}`);

const anonMatch = await call(`/invoices/${created.id}/match`, { method: "POST" });
check("anonymous POST /match → 401", anonMatch.status === 401, `got ${anonMatch.status}`);

/* 4. state guards ------------------------------------------------------- */
console.log("\nSTATE GUARDS");
const fresh = await upload(member.token, pdf, "unread.pdf");
const freshId = fresh.json.data.uploaded[0].id;
// Matching before extraction finishes must be refused, not silently queued.
const early = await call(`/invoices/${freshId}/match`, {
  method: "POST",
  token: admin.token,
});
check(
  "matching before extraction → 409",
  early.status === 409,
  `got ${early.status} ${early.json?.error?.code ?? ""}`,
);
check(
  "  code is INVOICE_NOT_READY",
  early.json?.error?.code === "INVOICE_NOT_READY",
  early.json?.error?.code,
);
await settle(freshId, member.token);

/* 5. re-run extraction -------------------------------------------------- */
console.log("\nRE-RUN EXTRACTION");
const rerun = await call(`/invoices/${created.id}/ocr`, {
  method: "POST",
  token: admin.token,
});
check("admin POST /ocr → 202", rerun.status === 202, `got ${rerun.status}`);
// The row is claimed inside the request, so the status the 202 reports is
// already committed by the time a client can poll for it.
check(
  "202 body carries the claimed status",
  rerun.json?.data?.status === "ocr_queued",
  rerun.json?.data?.status,
);
const reread = await settle(created.id, admin.token);
check("re-read lands on ocr_done", reread?.status === "ocr_done", reread?.status);
check(
  "still exactly 3 line rows (delete-then-insert, not duplicated)",
  reread?.lines?.length === 3,
  `got ${reread?.lines?.length}`,
);

/* 6. Odoo + matching ---------------------------------------------------- */
console.log("\nODOO + MATCHING");
const conn = await call("/odoo/connection", { token: admin.token });

if (conn.status !== 200) {
  const why =
    conn.json?.error?.code === "ODOO_NOT_CONFIGURED"
      ? "ODOO_URL / ODOO_DB / ODOO_USERNAME are not set in server/.env"
      : `${conn.status} ${conn.json?.error?.code ?? ""} ${conn.json?.message ?? ""}`;
  skip("Odoo connection", why);
  skip("fetch open purchase orders", "needs an Odoo connection");
  skip("matching produces a verdict", "needs an Odoo connection");
  skip("matched_po_id is always a real candidate", "needs an Odoo connection");
  skip("confirm attaches the purchase order", "needs an Odoo connection");
} else {
  check("Odoo connection → 200", true);
  console.log(`        server ${conn.json?.data?.server_version} · db ${conn.json?.data?.database}`);

  const pos = await call("/odoo/purchase-orders?limit=50", { token: admin.token });
  check("GET /odoo/purchase-orders → 200", pos.status === 200, `got ${pos.status}`);
  const orders = pos.json?.data ?? [];
  console.log(`        ${orders.length} open purchase order(s)`);
  check(
    "orders carry line items (proves the batched read)",
    orders.length === 0 || orders.some((o) => (o.lines?.length ?? 0) > 0),
    "every order came back with zero lines",
  );

  // Match against an invoice generated FROM a real purchase order. The ACME
  // fixture used above deliberately corresponds to nothing in Odoo, so it can
  // only ever produce no_match — which proves the negative case but never
  // exercises confirm.
  // Prefer an invoice generated from a REAL purchase order, if one exists.
  // The ACME fixture above deliberately corresponds to nothing in Odoo, so on
  // its own it can only ever produce no_match — which proves the negative case
  // but never exercises confirm.
  let target = created.id;
  const generated = process.env.PIPELINE_INVOICE_PDF;
  if (generated) {
    try {
      const realUp = await upload(admin.token, readFileSync(generated), "generated.pdf");
      if (realUp.status === 201) {
        target = realUp.json.data.uploaded[0].id;
        await settle(target, admin.token);
        console.log(`        matching ${generated}`);
      }
    } catch {
      console.log(`        (could not read ${generated} — falling back)`);
    }
  } else {
    console.log(
      "        (set PIPELINE_INVOICE_PDF to an invoice built from a real PO\n" +
        "         to exercise confirm — see server/scripts/make_test_invoice.py)",
    );
  }

  const match = await call(`/invoices/${target}/match`, {
    method: "POST",
    token: admin.token,
  });
  check("admin POST /match → 202", match.status === 202, `got ${match.status}`);

  const matched = await settle(target, admin.token);
  check(
    "matching reaches a verdict",
    ["pending_review", "no_match"].includes(matched?.status),
    `${matched?.status} · ${matched?.match_reasoning ?? ""}`,
  );
  check("reasoning is recorded either way", Boolean(matched?.match_reasoning));

  // The anti-hallucination guard: whatever the model returned, the stored id
  // must be one the pre-filter actually offered it.
  const candidateIds = (matched?.candidates?.items ?? []).map((c) => c.po_id);
  check(
    "matched_po_id is null or a real candidate",
    matched?.matched_po_id == null || candidateIds.includes(matched.matched_po_id),
    `${matched?.matched_po_id} not in [${candidateIds}]`,
  );

  if (matched?.candidates) {
    check("candidates blob stores the score breakdown", Boolean(matched.candidates.items?.[0]?.breakdown));
    check("candidates blob records the weights used", Boolean(matched.candidates.weights));
  }

  if (matched?.status === "pending_review" && matched.matched_po_id) {
    const confirmed = await call(`/invoices/${created.id}/confirm`, {
      method: "POST",
      token: admin.token,
      body: { po_id: matched.matched_po_id },
    });
    check("confirm → 200", confirmed.status === 200, `got ${confirmed.status}`);
    check("status becomes confirmed", confirmed.json?.data?.status === "confirmed");
    check("final_po_id is set", confirmed.json?.data?.final_po_id === matched.matched_po_id);
    check("was_corrected is false on a straight accept", confirmed.json?.data?.was_corrected === false);
  } else {
    skip("confirm attaches the purchase order", `invoice ended as ${matched?.status}`);
  }
}

/* 7. reject ------------------------------------------------------------- */
console.log("\nREJECT");
const rejected = await call(`/invoices/${freshId}/reject`, {
  method: "POST",
  token: admin.token,
  body: { reason: "Duplicate submission." },
});
check("admin POST /reject → 200", rejected.status === 200, `got ${rejected.status}`);
check("status becomes rejected", rejected.json?.data?.status === "rejected");
check("reason is stored", rejected.json?.data?.rejection_reason === "Duplicate submission.");

const memberSees = await call(`/invoices/${freshId}`, { token: member.token });
check("the uploader can still see it", memberSees.status === 200);
check("and sees why", memberSees.json?.data?.rejection_reason === "Duplicate submission.");

console.log(`\n${pass} passed, ${fail} failed, ${blocked} blocked\n`);
process.exit(fail === 0 ? 0 : 1);
