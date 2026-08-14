/**
 * End-to-end verification of the invoice intake pipeline.
 *
 * Checks the parts that do not depend on R2 being writable, and reports the
 * storage-dependent ones separately so a credentials problem is not confused
 * with a code problem.
 *
 *   node scripts/verify-invoices.mjs <adminEmail> <adminPassword>
 */

const API = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";
const BASE = `${API}/api/v1`;
const ORIGIN = "http://localhost:3000";

const [adminEmail, adminPassword] = process.argv.slice(2);
if (!adminEmail || !adminPassword) {
  console.error("Usage: node scripts/verify-invoices.mjs <adminEmail> <adminPassword>");
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

const blockedBy = (name, why) => {
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

/** A byte-valid single-page PDF — enough to pass the server's magic sniffing. */
function pdfBytes(label) {
  return new Blob([`%PDF-1.7\n% ${label}\n%%EOF\n`], { type: "application/pdf" });
}

function form(files, extra = {}) {
  const fd = new FormData();
  for (const [name, blob] of files) fd.append("files", blob, name);
  for (const [k, v] of Object.entries(extra)) fd.append(k, v);
  return fd;
}

console.log(`\nBackend: ${BASE}\n`);

const admin = await signIn(adminEmail, adminPassword);

/* member ---------------------------------------------------------------- */
console.log("SETUP");
const memberEmail = `inv-${Math.random().toString(36).slice(2, 10)}@example.com`;
const memberPassword = "SuperSecret123";
const reg = await call("/auth/register", {
  method: "POST",
  body: { email: memberEmail, password: memberPassword, full_name: "Invoice Bot" },
});
check("member registered", reg.status === 201, `got ${reg.status}`);
const member = await signIn(memberEmail, memberPassword);

/* permissions ----------------------------------------------------------- */
console.log("\nPERMISSIONS");
const memberPerms = (await call("/auth/permissions", { token: member.token })).json?.data;
const adminPerms = (await call("/auth/permissions", { token: admin.token })).json?.data;
check("member has invoice.create", memberPerms?.includes("invoice.create"));
check("member has invoice.read", memberPerms?.includes("invoice.read"));
check(
  "member does NOT have invoice.read.all",
  !memberPerms?.includes("invoice.read.all"),
  JSON.stringify(memberPerms),
);
check("admin has invoice.read.all", adminPerms?.includes("invoice.read.all"));

/* read scoping ---------------------------------------------------------- */
console.log("\nREAD SCOPING");
const myList = await call("/invoices/my", { token: member.token });
check("member GET /invoices/my → 200", myList.status === 200, `got ${myList.status}`);
check("returns a paginated envelope", Array.isArray(myList.json?.data?.items));

const myStats = await call("/invoices/my/stats", { token: member.token });
check("member GET /invoices/my/stats → 200", myStats.status === 200);
check(
  "stats zero-fill all 13 statuses",
  Object.keys(myStats.json?.data?.by_status ?? {}).length === 13,
  `got ${Object.keys(myStats.json?.data?.by_status ?? {}).length}`,
);

const queueAsMember = await call("/invoices/admin/queue", { token: member.token });
check(
  "member GET /invoices/admin/queue → 403",
  queueAsMember.status === 403,
  `got ${queueAsMember.status}`,
);
check(
  "  code is INSUFFICIENT_PERMISSION",
  queueAsMember.json?.error?.code === "INSUFFICIENT_PERMISSION",
);

const statsAsMember = await call("/invoices/admin/stats", { token: member.token });
check("member GET /invoices/admin/stats → 403", statsAsMember.status === 403);

const queueAsAdmin = await call("/invoices/admin/queue", { token: admin.token });
check("admin GET /invoices/admin/queue → 200", queueAsAdmin.status === 200);
check(
  "/invoices/admin/stats is a route, not a UUID",
  (await call("/invoices/admin/stats", { token: admin.token })).status === 200,
);

const anon = await call("/invoices/my");
check("anonymous → 401", anon.status === 401, `got ${anon.status}`);

/* upload validation (no R2 needed — these fail before storage) ----------- */
console.log("\nUPLOAD VALIDATION");
const noFiles = await call("/invoices/upload", {
  method: "POST",
  token: member.token,
  form: new FormData(),
});
check("zero files → 422", noFiles.status === 422, `got ${noFiles.status}`);

const eleven = await call("/invoices/upload", {
  method: "POST",
  token: member.token,
  form: form(
    Array.from({ length: 11 }, (_, i) => [`f${i}.pdf`, pdfBytes(`f${i}`)]),
  ),
});
check("11 files → 400", eleven.status === 400, `got ${eleven.status}`);
check(
  "  code is TOO_MANY_FILES",
  eleven.json?.error?.code === "TOO_MANY_FILES",
  eleven.json?.error?.code,
);

/* the storage-dependent part -------------------------------------------- */
console.log("\nUPLOAD ROUND TRIP");
const one = await call("/invoices/upload", {
  method: "POST",
  token: member.token,
  form: form([["invoice.pdf", pdfBytes("real")]], { member_ref_no: "INV-001" }),
});

if (one.status === 502 || one.status === 503) {
  blockedBy(
    "upload 1 PDF",
    `R2 returned ${one.json?.error?.code} — fix the token permission and create the bucket`,
  );
  blockedBy("stored row appears in /invoices/my", "depends on the upload");
  blockedBy("admin sees it in the queue", "depends on the upload");
  blockedBy("signed download URL", "depends on the upload");
  blockedBy("member cannot read another member's invoice", "depends on the upload");
  blockedBy("admin notified", "depends on the upload");
} else {
  check("upload 1 PDF → 201", one.status === 201, `got ${one.status} ${one.json?.message}`);
  const created = one.json?.data?.uploaded?.[0];
  if (!created) { console.log("  (upload produced no invoice — remaining checks skipped)"); }
}
if (one.status === 201 && one.json?.data?.uploaded?.[0]) {
  const created = one.json.data.uploaded[0];
  check("returns the created invoice", Boolean(created?.id));
  check("status is 'uploaded'", created?.status === "uploaded");
  check("ref number persisted", created?.member_ref_no === "INV-001");
  check("no rejections", one.json?.data?.rejected?.length === 0);
  check(
    "response never leaks the object key",
    !JSON.stringify(one.json).includes("file_key"),
  );

  const after = await call("/invoices/my", { token: member.token });
  check(
    "appears in the member's own list",
    after.json?.data?.items?.some((i) => i.id === created.id),
  );

  const queue = await call("/invoices/admin/queue", { token: admin.token });
  const seen = queue.json?.data?.items?.find((i) => i.id === created.id);
  check("admin sees it in the queue", Boolean(seen));
  check("admin sees who uploaded it", seen?.uploader?.email === memberEmail);

  const link = await call(`/invoices/${created.id}/file`, { token: member.token });
  check("signed download URL → 200", link.status === 200, `got ${link.status}`);
  check("URL is signed", /X-Amz-Signature=/.test(link.json?.data?.url ?? ""));
  check("URL expires", (link.json?.data?.expires_in ?? 0) > 0);

  // Partial success: one good file, one that is not an accepted type.
  const mixed = await call("/invoices/upload", {
    method: "POST",
    token: member.token,
    form: form([
      ["good.pdf", pdfBytes("good")],
      ["bad.txt", new Blob(["just text"], { type: "text/plain" })],
    ]),
  });
  check("mixed batch → 201 (partial success)", mixed.status === 201, `got ${mixed.status}`);
  check("  1 accepted", mixed.json?.data?.uploaded?.length === 1);
  check("  1 rejected", mixed.json?.data?.rejected?.length === 1);
  check(
    "  rejection names the file and reason",
    mixed.json?.data?.rejected?.[0]?.file_name === "bad.txt" &&
      mixed.json?.data?.rejected?.[0]?.code === "UNSUPPORTED_FILE_TYPE",
    JSON.stringify(mixed.json?.data?.rejected),
  );

  // Ownership: a second member must not be able to read the first one's row.
  const otherEmail = `oth-${Math.random().toString(36).slice(2, 8)}@example.com`;
  await call("/auth/register", {
    method: "POST",
    body: { email: otherEmail, password: memberPassword },
  });
  const other = await signIn(otherEmail, memberPassword);
  const stolen = await call(`/invoices/${created.id}`, { token: other.token });
  check(
    "another member reading it → 404, not 403",
    stolen.status === 404,
    `got ${stolen.status}`,
  );

  const notifs = await call("/notifications?unread_only=true", { token: admin.token });
  check("admin has an unread notification", (notifs.json?.data?.items?.length ?? 0) > 0);
  check(
    "  it is invoice_uploaded",
    notifs.json?.data?.items?.[0]?.type === "invoice_uploaded",
    notifs.json?.data?.items?.[0]?.type,
  );

  const unread = await call("/notifications/unread", { token: admin.token });
  check("unread count → 200", unread.status === 200);
  check("count > 0", (unread.json?.data?.count ?? 0) > 0);

  const withdraw = await call(`/invoices/${created.id}`, {
    method: "DELETE",
    token: member.token,
  });
  check("member withdraws their own upload → 200", withdraw.status === 200);
}

/* notifications are always scoped -------------------------------------- */
console.log("\nNOTIFICATIONS");
const memberNotifs = await call("/notifications", { token: member.token });
check("member GET /notifications → 200", memberNotifs.status === 200);
check(
  "member sees none of the admin's notifications",
  memberNotifs.json?.data?.items?.length === 0,
  `got ${memberNotifs.json?.data?.items?.length}`,
);

const bogus = await call("/notifications/00000000-0000-0000-0000-000000000000/read", {
  method: "PATCH",
  token: member.token,
});
check("marking an unknown notification read → 404", bogus.status === 404);

const readAll = await call("/notifications/read-all", {
  method: "PATCH",
  token: member.token,
});
check("read-all is a route, not a UUID", readAll.status === 200, `got ${readAll.status}`);

console.log(`\n${pass} passed, ${fail} failed, ${blocked} blocked\n`);
process.exit(fail === 0 ? 0 : 1);
