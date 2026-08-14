/** User and auth payloads, mirroring the FastAPI schemas. */

export type UserRole = "member" | "manager" | "admin";

/**
 * Permission strings from the backend's ROLE_PERMISSIONS table
 * (server/app/dependencies/auth.py).
 *
 * A TYPE only — the role→permission mapping itself is never duplicated here.
 * The list for the current user comes from GET /auth/permissions, so the
 * mapping has exactly one definition and cannot drift.
 */
export type Permission =
  | "user.read.self"
  | "user.update.self"
  | "user.read"
  | "user.create"
  | "user.update"
  | "user.delete"
  | "invoice.read"
  | "invoice.read.all"
  | "invoice.create"
  | "invoice.approve"
  | "invoice.delete"
  | "system.admin";

export interface User {
  id: string;
  email: string;
  full_name: string | null;
  role: UserRole;
  is_active: boolean;
  is_verified: boolean;
  created_at: string;
  updated_at: string;
}

/* -------------------------------------------------------------------------
 * Requests
 * ---------------------------------------------------------------------- */

export interface LoginInput {
  email: string;
  password: string;
}

export interface RegisterInput {
  email: string;
  password: string;
  full_name?: string | null;
}

export interface RoleUpdateInput {
  role: UserRole;
}

/* -------------------------------------------------------------------------
 * Responses (already unwrapped from the envelope by the service layer)
 * ---------------------------------------------------------------------- */

/** POST /auth/login. Note: no refresh token — that lives in an HttpOnly cookie. */
export interface LoginData {
  access_token: string;
  token_type: string;
  expires_in: number;
  expires_at: string;
  user: User;
}

/** POST /auth/refresh. Deliberately has no `user`, so bootstrap calls /me. */
export interface TokenData {
  access_token: string;
  token_type: string;
  expires_in: number;
  expires_at: string;
}

export interface LogoutData {
  revoked_sessions: number;
}

export interface AuthSession {
  id: string;
  created_at: string;
  expires_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
  ip_address: string | null;
  user_agent: string | null;
}

export interface UserStats {
  total: number;
  active: number;
  inactive: number;
  verified: number;
  /** Zero-filled by the backend, so every role is always a key. */
  by_role: Record<UserRole, number>;
}

/** What the session query resolves to. `null` means signed out. */
export interface Session {
  user: User;
  permissions: Permission[];
}
