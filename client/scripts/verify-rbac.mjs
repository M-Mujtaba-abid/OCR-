/**
 * End-to-end verification of role-based access against the live backend.
 *
 * Proves the part that matters: the API refuses admin operations to a member
 * regardless of what the UI renders. The frontend guards are cosmetic; these
 * checks are the real boundary.
 *
 * Requires an existing admin account. Create one with:
 *   cd server
 *   .\.venv\Scripts\python.exe scripts\set_role.py <email> admin -y
 *
 * Run:
 *   node scripts/verify-rbac.mjs <adminEmail> <adminPassword>
 */

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const BASE = `${API}/api/v1`;
const ORIGIN = "http://localhost:3000";

const [adminEmail, adminPassword] = process.argv.slice(2);
if (!adminEmail || !adminPassword) {
  console.error("Usage: node scripts/verify-rbac.mjs <adminEmail> <adminPassword>");
  process.exit(2);
}

let pass = 0;
let fail = 0;

function check(name, ok, detail = "") {
  if (ok) {
    pass += 1;
    console.log(`  PASS  ${name}`);
  } else {
    fail += 1;
    console.log(`  FAIL  ${name}${detail ? ` — ${detail}` : ""}`);
  }
}

async function call(path, { method = "GET", body, token, cookie } = {}) {
  const headers = { Accept: "application/json", Origin: ORIGIN };
  if (body) headers["Content-Type"] = "application/json";
  if (token) headers.Authorization = `Bearer ${token}`;
  if (cookie) headers.Cookie = cookie;

  const res = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  const json = await res.json().catch(() => null);
  return { status: res.status, json, setCookie: res.headers.get("set-cookie") };
}

async function signIn(email, password) {
  const res = await call("/auth/login", { method: "POST", body: { email, password } });
  if (res.status !== 200) {
    console.error(`\nCould not sign in as ${email}: ${res.status} ${res.json?.message}`);
    process.exit(2);
  }
  return { token: res.json.data.access_token, user: res.json.data.user };
}

console.log(`\nBackend: ${BASE}\n`);

/* 1. an admin ---------------------------------------------------------- */
console.log("ADMIN");
const admin = await signIn(adminEmail, adminPassword);
check("admin account has role=admin", admin.user.role === "admin", `got ${admin.user.role}`);

const adminPerms = await call("/auth/permissions", { token: admin.token });
check("GET /auth/permissions → 200", adminPerms.status === 200);
check(
  "admin holds user.read + user.update + system.admin",
  ["user.read", "user.update", "system.admin"].every((p) =>
    adminPerms.json?.data?.includes(p),
  ),
  JSON.stringify(adminPerms.json?.data),
);

const adminList = await call("/users?page=1&page_size=5", { token: admin.token });
check("admin GET /users → 200", adminList.status === 200, `got ${adminList.status}`);
check("response is paginated", Array.isArray(adminList.json?.data?.items));
check(
  "pagination meta present",
  typeof adminList.json?.data?.pagination?.total === "number",
);
check(
  "user list never leaks password_hash",
  !JSON.stringify(adminList.json).includes("password_hash"),
);

const adminStats = await call("/users/stats", { token: admin.token });
check("admin GET /users/stats → 200", adminStats.status === 200);
check(
  "stats zero-fill every role",
  ["member", "manager", "admin"].every(
    (r) => typeof adminStats.json?.data?.by_role?.[r] === "number",
  ),
  JSON.stringify(adminStats.json?.data?.by_role),
);
check(
  "/users/stats resolves as a route, not as a user id",
  adminStats.json?.data?.total !== undefined,
);

/* 2. a fresh member ----------------------------------------------------- */
console.log("\nMEMBER");
const memberEmail = `rbac-${Math.random().toString(36).slice(2, 10)}@example.com`;
const memberPassword = "SuperSecret123";

const reg = await call("/auth/register", {
  method: "POST",
  body: { email: memberEmail, password: memberPassword, full_name: "RBAC Bot" },
});
check("register → 201", reg.status === 201, `got ${reg.status}`);
check(
  "registration ALWAYS creates a member, never an admin",
  reg.json?.data?.role === "member",
  `got ${reg.json?.data?.role}`,
);

// The privilege-escalation attempt: ask for admin at signup.
const escalate = await call("/auth/register", {
  method: "POST",
  body: {
    email: `esc-${Math.random().toString(36).slice(2, 8)}@example.com`,
    password: memberPassword,
    role: "admin",
  },
});
check(
  "self-assigned role in the register body is ignored",
  escalate.json?.data?.role === "member",
  `got ${escalate.json?.data?.role}`,
);

const member = await signIn(memberEmail, memberPassword);

const memberPerms = await call("/auth/permissions", { token: member.token });
check(
  "member does NOT hold user.read / user.update / system.admin",
  ["user.read", "user.update", "system.admin"].every(
    (p) => !memberPerms.json?.data?.includes(p),
  ),
  JSON.stringify(memberPerms.json?.data),
);
check(
  "member does hold invoice.create + invoice.read",
  ["invoice.create", "invoice.read"].every((p) =>
    memberPerms.json?.data?.includes(p),
  ),
);

/* 3. the boundary ------------------------------------------------------- */
console.log("\nMEMBER IS REFUSED (this is the real guard)");
for (const [label, path, method] of [
  ["GET /users", "/users", "GET"],
  ["GET /users/stats", "/users/stats", "GET"],
  [`GET /users/{admin id}`, `/users/${admin.user.id}`, "GET"],
]) {
  const res = await call(path, { method, token: member.token });
  check(`member ${label} → 403`, res.status === 403, `got ${res.status}`);
  check(
    `  code is INSUFFICIENT_PERMISSION`,
    res.json?.error?.code === "INSUFFICIENT_PERMISSION",
    res.json?.error?.code,
  );
}

const selfPromote = await call(`/users/${member.user.id}/role`, {
  method: "PATCH",
  body: { role: "admin" },
  token: member.token,
});
check(
  "member cannot promote THEMSELVES to admin → 403",
  selfPromote.status === 403,
  `got ${selfPromote.status}`,
);

const stillMember = await call("/auth/me", { token: member.token });
check(
  "role is unchanged after the attempt",
  stillMember.json?.data?.role === "member",
  `got ${stillMember.json?.data?.role}`,
);

const noToken = await call("/users");
check("anonymous GET /users → 401", noToken.status === 401, `got ${noToken.status}`);

/* 4. admin guardrails --------------------------------------------------- */
console.log("\nADMIN GUARDRAILS");
const selfDemote = await call(`/users/${admin.user.id}/role`, {
  method: "PATCH",
  body: { role: "member" },
  token: admin.token,
});
check(
  "admin cannot change their OWN role → 403",
  selfDemote.status === 403,
  `got ${selfDemote.status}`,
);
check(
  "code is CANNOT_MODIFY_SELF",
  selfDemote.json?.error?.code === "CANNOT_MODIFY_SELF",
  selfDemote.json?.error?.code,
);

const selfDisable = await call(`/users/${admin.user.id}/deactivate`, {
  method: "PATCH",
  token: admin.token,
});
check(
  "admin cannot disable their OWN account → 403",
  selfDisable.status === 403,
  `got ${selfDisable.status}`,
);

/* 5. a real promotion --------------------------------------------------- */
console.log("\nPROMOTION ROUND TRIP");
const promote = await call(`/users/${member.user.id}/role`, {
  method: "PATCH",
  body: { role: "manager" },
  token: admin.token,
});
check("admin promotes member → manager → 200", promote.status === 200, `got ${promote.status}`);
check("role reflected in the response", promote.json?.data?.role === "manager");

const promoted = await signIn(memberEmail, memberPassword);
check("role is live on the next login", promoted.user.role === "manager");

const managerList = await call("/users?page=1&page_size=1", { token: promoted.token });
check(
  "manager can now GET /users (user.read) → 200",
  managerList.status === 200,
  `got ${managerList.status}`,
);

const managerPromote = await call(`/users/${admin.user.id}/role`, {
  method: "PATCH",
  body: { role: "member" },
  token: promoted.token,
});
check(
  "manager still cannot change roles (user.update) → 403",
  managerPromote.status === 403,
  `got ${managerPromote.status}`,
);

// Put it back, so repeated runs do not accumulate managers.
await call(`/users/${member.user.id}/role`, {
  method: "PATCH",
  body: { role: "member" },
  token: admin.token,
});

console.log(`\n${pass} passed, ${fail} failed\n`);
process.exit(fail === 0 ? 0 : 1);
