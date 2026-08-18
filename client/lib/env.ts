/**
 * Every tunable the frontend reads from its environment, in one place.
 *
 * Two rules this exists to enforce:
 *
 * 1. **`process.env.NEXT_PUBLIC_*` is referenced statically, never computed.**
 *    Next.js inlines these at BUILD time by substituting the literal text, so
 *    `process.env[name]` with a variable name yields undefined in the browser
 *    and works fine on the server — the worst kind of bug to find later.
 *
 * 2. **Anything the server also knows is not here.** Upload limits live in the
 *    server's settings and reach the browser through `/config`; duplicating
 *    them as `NEXT_PUBLIC_MAX_FILE_MB` would create a second source of truth
 *    that silently disagrees the moment one side is changed.
 *
 * These are build-time values: changing one in Vercel needs a redeploy to take
 * effect, which is the trade for them being available before any request.
 */

function int(raw: string | undefined, fallback: number): number {
  const parsed = Number(raw);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

/**
 * Backend origin. No trailing slash, no `/api` — the version prefix is
 * appended in `service/api.ts`.
 */
export const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

/** Must match the server's `API_V1_PREFIX`. */
export const API_PREFIX = process.env.NEXT_PUBLIC_API_PREFIX ?? "/api/v1";

/**
 * How often a list refetches while something is mid-pipeline. Only ever while
 * work is in flight — see `pollWhileWorking`.
 */
export const POLL_MS = int(process.env.NEXT_PUBLIC_POLL_MS, 3000);

/** Rows per page in the invoice and user tables. */
export const PAGE_SIZE = int(process.env.NEXT_PUBLIC_PAGE_SIZE, 10);

/**
 * Ceiling on one file's transfer to storage. Far longer than a JSON call:
 * this covers a large scan on a slow connection.
 */
export const UPLOAD_TIMEOUT = int(process.env.NEXT_PUBLIC_UPLOAD_TIMEOUT_MS, 120_000);

/** Ordinary API calls. */
export const REQUEST_TIMEOUT = int(process.env.NEXT_PUBLIC_REQUEST_TIMEOUT_MS, 30_000);
