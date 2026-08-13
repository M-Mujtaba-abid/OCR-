/**
 * Types mirroring the ACTUAL FastAPI responses.
 *
 * Every field here was verified against a live response — nothing invented.
 * Note in particular that the user's display field is `full_name`, not `name`,
 * and that the refresh endpoint returns no user object.
 */

/** Roles defined by the backend's `user_role` enum. */
export type UserRole = "member" | "manager" | "admin";

/**
 * The safe user shape. The backend serialises through a Pydantic `UserRead`
 * model that has no `password_hash` field, so a hash cannot appear here.
 */
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

/**
 * Permission strings from the backend's ROLE_PERMISSIONS table
 * (server/app/dependencies/auth.py).
 *
 * This is a TYPE only — the role→permission mapping itself is never copied
 * here. The actual list for the current user arrives from
 * GET /auth/permissions, so the mapping has exactly one definition.
 */
export type Permission =
  | "user.read.self"
  | "user.update.self"
  | "user.read"
  | "user.create"
  | "user.update"
  | "user.delete"
  | "invoice.read"
  | "invoice.create"
  | "invoice.approve"
  | "invoice.delete"
  | "system.admin";

/** Every successful response is wrapped in this envelope. */
export interface ApiEnvelope<T> {
  success: true;
  message: string;
  data: T;
}

/** `details` is a field -> message map on 422, and null elsewhere. */
export interface ApiErrorBody {
  success: false;
  message: string;
  error: {
    code: string;
    details: Record<string, string> | null;
  };
  request_id: string | null;
}

/** POST /auth/login → data */
export interface LoginData {
  access_token: string;
  token_type: string;
  expires_in: number;
  expires_at: string;
  user: User;
}

/** POST /auth/refresh → data. Deliberately has no `user`. */
export interface TokenData {
  access_token: string;
  token_type: string;
  expires_in: number;
  expires_at: string;
}

/** POST /auth/logout and /auth/logout-all → data */
export interface LogoutData {
  revoked_sessions: number;
}

/** GET /auth/sessions → data */
export interface AuthSession {
  id: string;
  created_at: string;
  expires_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
  ip_address: string | null;
  user_agent: string | null;
}

/* -------------------------------------------------------------------------
 * Admin — GET /users, GET /users/stats
 * ---------------------------------------------------------------------- */

export interface Pagination {
  page: number;
  page_size: number;
  total: number;
  pages: number;
}

export interface Paginated<T> {
  items: T[];
  pagination: Pagination;
}

export interface UserStats {
  total: number;
  active: number;
  inactive: number;
  verified: number;
  /** Zero-filled by the backend, so every role is always a key. */
  by_role: Record<UserRole, number>;
}

/* -------------------------------------------------------------------------
 * Requests
 * ---------------------------------------------------------------------- */

export interface LoginRequest {
  email: string;
  password: string;
}

/**
 * `confirmPassword` is intentionally absent — the backend does not accept it,
 * so it is validated client-side and stripped before the request is sent.
 */
export interface RegisterRequest {
  email: string;
  password: string;
  full_name?: string | null;
}

/* -------------------------------------------------------------------------
 * Error codes the UI branches on. Values come from the backend's
 * app/core/exceptions.py — keep in sync if new codes are added there.
 * ---------------------------------------------------------------------- */
export type ApiErrorCode =
  | "VALIDATION_ERROR"
  | "INVALID_CREDENTIALS"
  | "EMAIL_ALREADY_REGISTERED"
  | "INACTIVE_USER"
  | "UNAUTHORIZED"
  | "INVALID_TOKEN"
  | "TOKEN_EXPIRED"
  | "INVALID_REFRESH_TOKEN"
  | "REFRESH_TOKEN_REUSED"
  | "FORBIDDEN"
  | "INSUFFICIENT_ROLE"
  | "INSUFFICIENT_PERMISSION"
  | "NOT_FOUND"
  | "CONFLICT"
  | "DATABASE_ERROR"
  | "INTERNAL_ERROR"
  | "NETWORK_ERROR"
  | "RATE_LIMITED";

export interface AuthContextValue {
  user: User | null;
  accessToken: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  /** Effective permissions, fetched from the backend. Empty when signed out. */
  permissions: Permission[];
  /** True only if the user holds EVERY listed permission. */
  can: (...required: Permission[]) => boolean;
  /** The route this user's role lands on. `/login` when signed out. */
  homePath: string;
  login: (credentials: LoginRequest) => Promise<User>;
  register: (payload: RegisterRequest) => Promise<User>;
  logout: () => Promise<void>;
  logoutAll: () => Promise<void>;
  refreshUser: () => Promise<void>;
}
