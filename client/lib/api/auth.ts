/**
 * Auth endpoint wrappers.
 *
 * Every call goes through `apiRequest`, which already sets
 * `credentials: "include"` — so the HttpOnly refresh cookie is sent on the
 * endpoints that need it (refresh, logout, logout-all) without any of them
 * touching the token itself.
 */

import { api, apiRequest } from "@/lib/api/client";
import type {
  AuthSession,
  LoginData,
  LoginRequest,
  LogoutData,
  RegisterRequest,
  TokenData,
  User,
} from "@/types/auth";

/**
 * Create an account.
 *
 * Returns the created user. The backend does NOT authenticate on register —
 * no tokens and no cookie come back — so the caller must send the user to the
 * login page (or call `login` explicitly).
 */
export function register(payload: RegisterRequest): Promise<User> {
  return api.post<User>("/auth/register", {
    email: payload.email,
    password: payload.password,
    // Omit rather than send null when blank, so the backend applies its default.
    ...(payload.full_name ? { full_name: payload.full_name } : {}),
  });
}

/** Log in. Sets the HttpOnly refresh cookie as a side effect. */
export function login(credentials: LoginRequest): Promise<LoginData> {
  return api.post<LoginData>("/auth/login", credentials);
}

/**
 * Exchange the refresh cookie for a new access token.
 *
 * `skipAuthRefresh` prevents the client's own 401 handler from trying to
 * refresh a failing refresh — that would recurse.
 */
export function refresh(): Promise<TokenData> {
  return apiRequest<TokenData>("/auth/refresh", {
    method: "POST",
    skipAuthRefresh: true,
  });
}

/** Revoke this session and clear the cookie server-side. */
export function logout(): Promise<LogoutData> {
  return apiRequest<LogoutData>("/auth/logout", {
    method: "POST",
    // A 401 here is meaningless: we are discarding the session anyway.
    skipAuthRefresh: true,
  });
}

/** Revoke every session for the current user. Requires a valid access token. */
export function logoutAll(): Promise<LogoutData> {
  return api.post<LogoutData>("/auth/logout-all");
}

/** The authenticated user. */
export function getMe(): Promise<User> {
  return api.get<User>("/auth/me");
}

/** Active sessions/devices for the current user. */
export function getSessions(): Promise<AuthSession[]> {
  return api.get<AuthSession[]>("/auth/sessions");
}
