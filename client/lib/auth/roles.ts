/**
 * Role-driven navigation and access rules.
 *
 * This file answers exactly one question: *what should this user SEE?*
 *
 * It never answers "what may this user DO" — that is decided by FastAPI on
 * every single request, from the role stored in the database. A user who edits
 * their role in devtools changes what this file renders and nothing else; the
 * API still returns 403. Treat everything here as cosmetic.
 */

import type { Permission, User, UserRole } from "@/types/auth";

/** Least to most privileged. Used for `atLeast` comparisons. */
const ROLE_RANK: Record<UserRole, number> = {
  member: 0,
  manager: 1,
  admin: 2,
};

export const ROLE_LABEL: Record<UserRole, string> = {
  member: "Member",
  manager: "Manager",
  admin: "Administrator",
};

export const ROLE_DESCRIPTION: Record<UserRole, string> = {
  member: "Upload invoices and track your own submissions.",
  manager: "Review and approve invoices across the team.",
  admin: "Full access, including user management.",
};

/**
 * Where each role lands after signing in.
 *
 * Managers share the member dashboard on purpose — their extra capability is
 * approval, which appears as additional sections inside that page rather than
 * as a separate destination. Only admin gets its own route, because user
 * management has nothing to do with the invoice workflow.
 */
export const ROLE_HOME: Record<UserRole, string> = {
  member: "/dashboard",
  manager: "/dashboard",
  admin: "/admin",
};

export function homePathFor(user: User | null | undefined): string {
  return user ? ROLE_HOME[user.role] : "/login";
}

export function isAdmin(user: User | null | undefined): boolean {
  return user?.role === "admin";
}

export function atLeast(user: User | null | undefined, role: UserRole): boolean {
  return user ? ROLE_RANK[user.role] >= ROLE_RANK[role] : false;
}

/* -------------------------------------------------------------------------
 * Navigation
 * ---------------------------------------------------------------------- */

export interface NavLink {
  href: string;
  label: string;
  /** Minimum role required to see this link. */
  minRole: UserRole;
}

/**
 * Only routes that actually exist are listed. A nav link to a missing page is
 * worse than no link at all — it reads as a broken product rather than an
 * unfinished one.
 */
const NAV_LINKS: readonly NavLink[] = [
  { href: "/dashboard", label: "Dashboard", minRole: "member" },
  { href: "/admin", label: "Admin", minRole: "admin" },
] as const;

export function navLinksFor(user: User | null | undefined): NavLink[] {
  if (!user) return [];
  return NAV_LINKS.filter((link) => atLeast(user, link.minRole));
}

/* -------------------------------------------------------------------------
 * Route access
 * ---------------------------------------------------------------------- */

/**
 * Route prefixes that require a minimum role, longest prefix winning.
 *
 * Keyed by prefix rather than exact path so `/admin/users/123` is covered by
 * the `/admin` entry — a nested admin page added later is guarded by default
 * instead of being wide open until someone remembers to list it.
 */
const ROUTE_MIN_ROLE: ReadonlyArray<readonly [string, UserRole]> = [
  ["/admin", "admin"],
] as const;

/** Whether a user may view a path. Public and unlisted paths are allowed. */
export function canViewPath(
  user: User | null | undefined,
  pathname: string,
): boolean {
  const rule = ROUTE_MIN_ROLE.find(
    ([prefix]) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
  return rule ? atLeast(user, rule[1]) : true;
}

/* -------------------------------------------------------------------------
 * Permissions
 * ---------------------------------------------------------------------- */

/**
 * Whether the user holds every listed permission.
 *
 * The permission list comes from GET /auth/permissions rather than a local copy
 * of the backend's ROLE_PERMISSIONS map. That is deliberate: a duplicated table
 * drifts the first time someone grants managers a new capability server-side
 * and forgets the frontend, and the failure is silent — a button that is
 * rendered but 403s, or one that is hidden from someone entitled to it.
 */
export function hasPermission(
  granted: readonly Permission[],
  ...required: Permission[]
): boolean {
  return required.every((permission) => granted.includes(permission));
}
