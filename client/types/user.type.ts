/** User and auth payloads, mirroring the FastAPI schemas. */

/**
 * The three company roles, plus the platform owner.
 *
 * `super_admin` is NOT a fourth rung on the ladder — it is outside the
 * companies entirely, which is why it is the one role whose holder has no
 * company. Treat it as a separate kind of account rather than a bigger admin.
 */
export type UserRole = "member" | "manager" | "admin" | "super_admin";

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
  | "system.admin"
  /** The platform owner's ONLY grant beyond reading their own account. */
  | "platform.admin";

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

/**
 * POST /users — an administrator adding somebody to their company.
 *
 * What `RegisterInput` became. There is no `company_id`: the server takes the
 * company from the administrator's session, so a browser has nothing to say
 * about which company an account joins.
 */
export interface CreateUserInput {
  email: string;
  password: string;
  full_name?: string | null;
  role: UserRole;
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
