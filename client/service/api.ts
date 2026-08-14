import axios, {
  AxiosError,
  type AxiosRequestConfig,
  type InternalAxiosRequestConfig,
} from "axios";

import {
  clearAuthSession,
  getAccessToken,
  notifySessionExpired,
  setAuthSession,
} from "@/utils/auth";
import type { ApiErrorResponse, ApiResponse } from "@/types/api.type";
import type { TokenData } from "@/types/user.type";

/**
 * The one axios instance.
 *
 * The base URL is assembled from a single environment variable — no host is
 * written anywhere else in the app, so pointing the frontend at staging is a
 * one-line change and there is no `localhost` to forget in production.
 */
const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

export const API_PREFIX = "/api/v1";

const api = axios.create({
  baseURL: `${BACKEND_URL}${API_PREFIX}`,
  // Sends the HttpOnly refresh cookie cross-origin. Without it, refresh can
  // never work and every session dies when the access token expires.
  withCredentials: true,
  headers: { Accept: "application/json" },
  timeout: 30_000,
});

/** Uploads need far longer than a JSON call — set per request, not globally. */
export const UPLOAD_TIMEOUT = 120_000;

/* -------------------------------------------------------------------------
 * Errors
 * ---------------------------------------------------------------------- */

/**
 * Every failure reaching a hook is one of these, so a component never has to
 * know whether it is looking at an axios error, a network failure or an HTML
 * error page from a proxy.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  /** Field-level messages from a 422, keyed by field name. */
  readonly fieldErrors: Record<string, string>;
  /** Correlates with the server log. Show it when reporting a failure. */
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

/**
 * Fallbacks only.
 *
 * The backend's own messages are written for users and are more specific than
 * anything generic, so they win. This map covers the cases where no message
 * survives — a network failure, a proxy 502, a timeout.
 */
const FALLBACK_MESSAGES: Record<string, string> = {
  NETWORK_ERROR: "Unable to reach the server. Check your connection.",
  TIMEOUT: "The server took too long to respond. Please try again.",
  INTERNAL_ERROR: "Something went wrong. Please try again.",
};

export function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error;

  if (axios.isAxiosError(error)) {
    const axiosError = error as AxiosError<ApiErrorResponse>;

    if (axiosError.code === "ECONNABORTED") {
      return new ApiError(FALLBACK_MESSAGES.TIMEOUT, 0, "TIMEOUT");
    }
    if (!axiosError.response) {
      return new ApiError(FALLBACK_MESSAGES.NETWORK_ERROR, 0, "NETWORK_ERROR");
    }

    const { status, data } = axiosError.response;
    if (data && typeof data === "object" && "error" in data) {
      return new ApiError(
        data.message || FALLBACK_MESSAGES.INTERNAL_ERROR,
        status,
        data.error?.code ?? "INTERNAL_ERROR",
        data.error?.details ?? {},
        data.request_id ?? null,
      );
    }
    return new ApiError(FALLBACK_MESSAGES.INTERNAL_ERROR, status, "INTERNAL_ERROR");
  }

  return new ApiError(FALLBACK_MESSAGES.INTERNAL_ERROR, 0, "INTERNAL_ERROR");
}

/* -------------------------------------------------------------------------
 * Request interceptor — attach the bearer token
 * ---------------------------------------------------------------------- */
api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = getAccessToken();
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

/* -------------------------------------------------------------------------
 * Single-flight refresh
 *
 * Without this, four parallel requests hitting 401 at once fire four refreshes.
 * The backend ROTATES refresh tokens, so the second refresh would present a
 * token the first had already rotated away — which the backend correctly treats
 * as theft and answers by revoking every session. The user would be logged out
 * for doing nothing wrong.
 *
 * So this is not a performance optimisation. Against a rotating backend it is a
 * correctness requirement.
 * ---------------------------------------------------------------------- */
const REFRESH_PATH = "/auth/refresh";

let refreshPromise: Promise<boolean> | null = null;

async function performRefresh(): Promise<boolean> {
  try {
    // A bare axios call, not `api`: going through the instance would re-enter
    // the response interceptor below and a failing refresh would try to
    // refresh itself.
    const response = await axios.post<ApiResponse<TokenData>>(
      `${BACKEND_URL}${API_PREFIX}${REFRESH_PATH}`,
      null,
      { withCredentials: true, headers: { Accept: "application/json" } },
    );

    const token = response.data?.data?.access_token;
    if (!token) return false;

    setAuthSession(token);
    return true;
  } catch {
    // A network failure is indistinguishable from an invalid session here, and
    // both mean the same thing to the caller: no usable token.
    clearAuthSession();
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
 * Response interceptor — refresh once, retry once
 * ---------------------------------------------------------------------- */

/** Endpoints where a 401 is the answer, not a stale-token problem. */
const NO_RETRY_PATHS = [REFRESH_PATH, "/auth/login", "/auth/register", "/auth/logout"];

interface RetriableConfig extends AxiosRequestConfig {
  /** Set after the single retry, so a permanently-401ing server cannot loop. */
  _retried?: boolean;
}

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError<ApiErrorResponse>) => {
    const config = error.config as RetriableConfig | undefined;
    const status = error.response?.status;
    const url = config?.url ?? "";

    const retriable =
      status === 401 &&
      config &&
      !config._retried &&
      !NO_RETRY_PATHS.some((path) => url.includes(path));

    if (retriable) {
      config._retried = true;

      const refreshed = await refreshAccessToken();
      if (refreshed) {
        // The request interceptor picks up the new token on the way through.
        return api(config);
      }

      // No usable session. Clear state and let the app redirect.
      notifySessionExpired();
    }

    // Rejecting with our own type means every consumer — hook, mutation,
    // component — handles exactly one error shape.
    return Promise.reject(toApiError(error));
  },
);

export default api;
