/**
 * Central API client.
 *
 * Responsibilities:
 *   - attach `Authorization: Bearer <token>` when one exists
 *   - always send credentials so the HttpOnly refresh cookie travels
 *   - unwrap the `{ success, message, data }` envelope
 *   - normalise every failure into a single ApiError type
 *   - on 401: refresh ONCE, retry the original request ONCE
 *   - collapse concurrent refreshes into a single in-flight request
 *
 * Never logs tokens, passwords or request bodies.
 */

import type { ApiEnvelope, ApiErrorBody, ApiErrorCode } from "@/types/auth";
import {
  getAccessToken,
  notifySessionExpired,
  setAccessToken,
} from "@/lib/auth/token-store";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const API_PREFIX = "/api/v1";

/** The refresh endpoint itself — never retried through the 401 path. */
const REFRESH_PATH = "/auth/refresh";

export class ApiError extends Error {
  readonly status: number;
  readonly code: ApiErrorCode | string;
  /** Field-level messages from a 422, keyed by field name. */
  readonly fieldErrors: Record<string, string>;
  readonly requestId: string | null;

  constructor(
    message: string,
    status: number,
    code: string,
    fieldErrors: Record<string, string> = {},
    requestId: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.fieldErrors = fieldErrors;
    this.requestId = requestId;
  }
}

/* -------------------------------------------------------------------------
 * User-facing messages
 *
 * Backend messages are already safe and specific, so they are preferred. This
 * map is the fallback for cases where the raw message would be unhelpful or
 * where no message survives (network failure, proxy error).
 * ---------------------------------------------------------------------- */
const FRIENDLY_MESSAGES: Record<string, string> = {
  INVALID_CREDENTIALS: "Invalid email or password.",
  EMAIL_ALREADY_REGISTERED: "This email is already registered.",
  INACTIVE_USER: "This account has been disabled.",
  UNAUTHORIZED: "Please sign in to continue.",
  TOKEN_EXPIRED: "Your session has expired. Please sign in again.",
  INVALID_REFRESH_TOKEN: "Your session has expired. Please sign in again.",
  REFRESH_TOKEN_REUSED:
    "For your security, all sessions were signed out. Please sign in again.",
  FORBIDDEN: "You don't have permission to perform this action.",
  INSUFFICIENT_ROLE: "You don't have permission to perform this action.",
  INSUFFICIENT_PERMISSION: "You don't have permission to perform this action.",
  NOT_FOUND: "We couldn't find what you were looking for.",
  CONFLICT: "That conflicts with existing data.",
  VALIDATION_ERROR: "Please check the highlighted fields.",
  RATE_LIMITED: "Too many attempts. Please wait a moment and try again.",
  DATABASE_ERROR: "Something went wrong. Please try again.",
  INTERNAL_ERROR: "Something went wrong. Please try again.",
  NETWORK_ERROR: "Unable to connect to the server.",
};

function messageForStatus(status: number): string {
  if (status === 429) return FRIENDLY_MESSAGES.RATE_LIMITED;
  if (status >= 500) return "Something went wrong. Please try again.";
  if (status === 404) return FRIENDLY_MESSAGES.NOT_FOUND;
  if (status === 403) return FRIENDLY_MESSAGES.FORBIDDEN;
  return "Something went wrong. Please try again.";
}

function isErrorBody(value: unknown): value is ApiErrorBody {
  return (
    typeof value === "object" &&
    value !== null &&
    "error" in value &&
    typeof (value as ApiErrorBody).error?.code === "string"
  );
}

async function toApiError(response: Response): Promise<ApiError> {
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    // Non-JSON body (proxy HTML error page, empty 502, ...).
  }

  if (isErrorBody(body)) {
    const code = body.error.code;
    // Prefer the backend's message: it is written for users and is more
    // specific than any generic mapping. Fall back only when it is missing.
    const message = body.message || FRIENDLY_MESSAGES[code] || messageForStatus(response.status);
    return new ApiError(
      message,
      response.status,
      code,
      body.error.details ?? {},
      body.request_id,
    );
  }

  return new ApiError(messageForStatus(response.status), response.status, "INTERNAL_ERROR");
}

/* -------------------------------------------------------------------------
 * Single-flight refresh
 *
 * Without this, four parallel requests hitting 401 at once fire four refreshes.
 * Because the backend ROTATES refresh tokens, the second refresh would present
 * a token that the first had already rotated away — which the backend correctly
 * treats as theft and responds by revoking every session. The user would be
 * logged out for doing nothing wrong.
 *
 * So this is not a performance optimisation. With a rotating backend it is a
 * correctness requirement.
 * ---------------------------------------------------------------------- */
let refreshPromise: Promise<boolean> | null = null;

async function performRefresh(): Promise<boolean> {
  try {
    const response = await fetch(`${API_URL}${API_PREFIX}${REFRESH_PATH}`, {
      method: "POST",
      // Sends the HttpOnly refresh cookie. Without this the request is
      // anonymous and refresh can never succeed.
      credentials: "include",
      headers: { Accept: "application/json" },
    });

    if (!response.ok) return false;

    const body = (await response.json()) as ApiEnvelope<{ access_token: string }>;
    if (!body?.data?.access_token) return false;

    setAccessToken(body.data.access_token);
    return true;
  } catch {
    // Network failure — indistinguishable from an invalid session here.
    return false;
  }
}

/** Collapses concurrent callers onto one in-flight refresh. */
export function refreshAccessToken(): Promise<boolean> {
  refreshPromise ??= performRefresh().finally(() => {
    refreshPromise = null;
  });
  return refreshPromise;
}

/* -------------------------------------------------------------------------
 * Request
 * ---------------------------------------------------------------------- */

export interface RequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
  /** Skip the automatic 401 → refresh → retry cycle. */
  skipAuthRefresh?: boolean;
}

async function rawRequest(path: string, options: RequestOptions): Promise<Response> {
  const { body, skipAuthRefresh, headers, ...rest } = options;
  // Consumed by apiRequest, not a valid fetch() option — destructured out so
  // it is never forwarded.
  void skipAuthRefresh;

  const finalHeaders = new Headers(headers);
  finalHeaders.set("Accept", "application/json");
  if (body !== undefined) finalHeaders.set("Content-Type", "application/json");

  const token = getAccessToken();
  if (token) finalHeaders.set("Authorization", `Bearer ${token}`);

  return fetch(`${API_URL}${API_PREFIX}${path}`, {
    ...rest,
    headers: finalHeaders,
    credentials: "include",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

/**
 * Perform a request and return the unwrapped `data` payload.
 *
 * On 401 it refreshes once and retries once. The retry is bounded by an
 * explicit local flag rather than recursion, so a backend that returns 401 to
 * everything cannot produce an infinite loop.
 */
export async function apiRequest<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  let response: Response;

  try {
    response = await rawRequest(path, options);
  } catch {
    // fetch() rejects only on network-level failure, never on 4xx/5xx.
    throw new ApiError(FRIENDLY_MESSAGES.NETWORK_ERROR, 0, "NETWORK_ERROR");
  }

  const isRefreshCall = path === REFRESH_PATH;

  if (response.status === 401 && !options.skipAuthRefresh && !isRefreshCall) {
    const refreshed = await refreshAccessToken();

    if (!refreshed) {
      // No usable session. Clear state and let the provider redirect.
      notifySessionExpired();
      throw await toApiError(response);
    }

    try {
      // The single retry. rawRequest picks up the new token from the store.
      response = await rawRequest(path, options);
    } catch {
      throw new ApiError(FRIENDLY_MESSAGES.NETWORK_ERROR, 0, "NETWORK_ERROR");
    }

    // Still 401 after a successful refresh means this is not an expiry problem
    // — the user genuinely lacks access. Do not refresh again.
    if (response.status === 401) {
      notifySessionExpired();
      throw await toApiError(response);
    }
  }

  if (!response.ok) throw await toApiError(response);

  if (response.status === 204) return undefined as T;

  const envelope = (await response.json()) as ApiEnvelope<T>;
  return envelope.data;
}

export const api = {
  get: <T>(path: string, options?: RequestOptions) =>
    apiRequest<T>(path, { ...options, method: "GET" }),
  post: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    apiRequest<T>(path, { ...options, method: "POST", body }),
  patch: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    apiRequest<T>(path, { ...options, method: "PATCH", body }),
  delete: <T>(path: string, options?: RequestOptions) =>
    apiRequest<T>(path, { ...options, method: "DELETE" }),
};
