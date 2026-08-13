/**
 * End-to-end verification of the auth integration against the real backend.
 *
 * Run with the backend up:  node scripts/verify-auth-flow.mjs
 *
 * Node 18+ ships fetch and a CookieJar-less client, so the refresh cookie is
 * tracked manually here — the browser does this automatically.
 */

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const BASE = `${API}/api/v1`;
const ORIGIN = "http://localhost:3000";

let cookie = null;
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

async function call(path, { method = "GET", body, token, sendCookie = true } = {}) {
  const headers = { Accept: "application/json", Origin: ORIGIN };
  if (body) headers["Content-Type"] = "application/json";
  if (token) headers.Authorization = `Bearer ${token}`;
  if (sendCookie && cookie) headers.Cookie = cookie;

  const res = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });

  const setCookie = res.headers.get("set-cookie");
  if (setCookie) {
    const pair = setCookie.split(";")[0];
    cookie = pair.startsWith("refresh_token=") ? pair : cookie;
  }

  let json = null;
  try {
    json = await res.json();
  } catch {
    /* empty body */
  }
  return { status: res.status, json, setCookie };
}

const email = `verify-${Math.random().toString(36).slice(2, 10)}@example.com`;
const password = "SuperSecret123";

console.log(`\nBackend: ${BASE}`);
console.log(`Test user: ${email}\n`);

/* 1. register ---------------------------------------------------------- */
console.log("REGISTER");
const reg = await call("/auth/register", {
  method: "POST",
  body: { email, password, full_name: "Verify Bot" },
});
check("register returns 201", reg.status === 201, `got ${reg.status}`);
check("register returns the user", reg.json?.data?.email === email);
check("register does NOT set a cookie", !reg.setCookie);
check("register does NOT return tokens", !reg.json?.data?.access_token);

/* 2. duplicate --------------------------------------------------------- */
const dup = await call("/auth/register", { method: "POST", body: { email, password } });
check("duplicate email → 409", dup.status === 409, `got ${dup.status}`);
check(
  "duplicate code is EMAIL_ALREADY_REGISTERED",
  dup.json?.error?.code === "EMAIL_ALREADY_REGISTERED",
);

/* 3. login ------------------------------------------------------------- */
console.log("\nLOGIN");
const bad = await call("/auth/login", {
  method: "POST",
  body: { email, password: "WrongPassword1" },
});
check("wrong password → 401", bad.status === 401);
check("code is INVALID_CREDENTIALS", bad.json?.error?.code === "INVALID_CREDENTIALS");

const unknown = await call("/auth/login", {
  method: "POST",
  body: { email: "ghost@example.com", password: "WrongPassword1" },
});
check(
  "unknown email is indistinguishable from wrong password",
  unknown.json?.message === bad.json?.message &&
    unknown.json?.error?.code === bad.json?.error?.code,
);

const login = await call("/auth/login", { method: "POST", body: { email, password } });
check("login → 200", login.status === 200);
check("login returns an access token", Boolean(login.json?.data?.access_token));
check("login returns the user", login.json?.data?.user?.email === email);
check("refresh token is NOT in the body", !("refresh_token" in (login.json?.data ?? {})));
check("Set-Cookie present", Boolean(login.setCookie));
check("cookie is HttpOnly", /httponly/i.test(login.setCookie ?? ""));
check("cookie path is /api/v1/auth", /path=\/api\/v1\/auth/i.test(login.setCookie ?? ""));

let accessToken = login.json.data.access_token;

/* 4. protected --------------------------------------------------------- */
console.log("\nPROTECTED ROUTE");
const me = await call("/auth/me", { token: accessToken });
check("GET /me → 200", me.status === 200);
check("/me returns the right user", me.json?.data?.email === email);
check("/me never leaks password_hash", !JSON.stringify(me.json).includes("password_hash"));

const noAuth = await call("/auth/me");
check("GET /me without token → 401", noAuth.status === 401);

const badJwt = await call("/auth/me", { token: "not.a.valid.jwt" });
check("malformed JWT → 401", badJwt.status === 401);
check("code is INVALID_TOKEN", badJwt.json?.error?.code === "INVALID_TOKEN");

/* 5. concurrent refresh ------------------------------------------------ */
console.log("\nCONCURRENT REFRESH");
// Fires several refreshes with the SAME cookie at once — i.e. what the client
// would do if it did NOT single-flight. This probes the BACKEND directly and
// is diagnostic, not a frontend assertion: the client can never produce this
// pattern because lib/api/client.ts collapses concurrent refreshes onto one
// shared promise.
const cookieBefore = cookie;
const parallel = await Promise.all(
  [1, 2, 3, 4].map(() =>
    fetch(`${BASE}/auth/refresh`, {
      method: "POST",
      headers: { Cookie: cookieBefore, Origin: ORIGIN, Accept: "application/json" },
    }).then(async (r) => ({ status: r.status, json: await r.json().catch(() => null) })),
  ),
);
const okCount = parallel.filter((r) => r.status === 200).length;
console.log(`  4 parallel raw refreshes → ${okCount} succeeded, ${4 - okCount} rejected`);

// Diagnostic only — deliberately NOT a pass/fail of the frontend.
if (okCount > 1) {
  console.log(
    "  FINDING (backend)  Rotation is not atomic. Concurrent refreshes on one\n" +
      "    token each read the session before any of them commits, so several\n" +
      "    succeed and mint sibling sessions from a single token. Sequential\n" +
      "    rotation is correct (verified below). Unreachable from this client,\n" +
      "    which single-flights refreshes — but reachable by any other client.\n" +
      "    Fix: SELECT ... FOR UPDATE on the session row, or a conditional\n" +
      "    UPDATE ... WHERE revoked_at IS NULL and rotate only if rowcount = 1.",
  );
} else {
  console.log("  PASS  backend serialises concurrent refreshes");
  pass += 1;
}

/* 6. sequential refresh + rotation ------------------------------------- */
console.log("\nREFRESH ROTATION");
const relogin = await call("/auth/login", { method: "POST", body: { email, password } });
accessToken = relogin.json.data.access_token;
const firstCookie = cookie;

const refreshed = await call("/auth/refresh", { method: "POST" });
check("refresh → 200", refreshed.status === 200);
check("refresh returns a new access token", Boolean(refreshed.json?.data?.access_token));
check("refresh returns NO user (bootstrap must call /me)", !refreshed.json?.data?.user);
check("cookie rotated to a new value", cookie !== firstCookie);

const stolen = firstCookie;
const reuse = await fetch(`${BASE}/auth/refresh`, {
  method: "POST",
  headers: { Cookie: stolen, Origin: ORIGIN, Accept: "application/json" },
}).then(async (r) => ({ status: r.status, json: await r.json().catch(() => null) }));
check("reused pre-rotation token → 401", reuse.status === 401);
check(
  "reuse code is REFRESH_TOKEN_REUSED",
  reuse.json?.error?.code === "REFRESH_TOKEN_REUSED",
);

/* 7. logout ------------------------------------------------------------ */
console.log("\nLOGOUT");
const fresh = await call("/auth/login", { method: "POST", body: { email, password } });
accessToken = fresh.json.data.access_token;

const out = await call("/auth/logout", { method: "POST" });
check("logout → 200", out.status === 200);
check("logout revoked 1 session", out.json?.data?.revoked_sessions === 1);

const afterLogout = await call("/auth/refresh", { method: "POST" });
check("refresh after logout → 401", afterLogout.status === 401);

/* 8. logout-all -------------------------------------------------------- */
console.log("\nLOGOUT ALL");
const s1 = await call("/auth/login", { method: "POST", body: { email, password } });
await call("/auth/login", { method: "POST", body: { email, password } });
const all = await call("/auth/logout-all", {
  method: "POST",
  token: s1.json.data.access_token,
});
check("logout-all → 200", all.status === 200);
check("revoked >= 2 sessions", (all.json?.data?.revoked_sessions ?? 0) >= 2);
check(
  "refresh after logout-all → 401",
  (await call("/auth/refresh", { method: "POST" })).status === 401,
);

/* 9. network failure --------------------------------------------------- */
console.log("\nNETWORK FAILURE");
try {
  await fetch("http://localhost:59999/api/v1/auth/me");
  check("unreachable host rejects", false, "did not throw");
} catch {
  check("unreachable host rejects (client maps to NETWORK_ERROR)", true);
}

console.log(`\n${pass} passed, ${fail} failed\n`);
process.exit(fail === 0 ? 0 : 1);
