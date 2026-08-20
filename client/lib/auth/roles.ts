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

import type { Permission, User, UserRole } from "@/types/user.type";

/**
 * Least to most privileged, WITHIN a company. Used for `atLeast` comparisons.
 *
 * `super_admin` ranks below member on purpose. It is not a bigger admin — it
 * is an account outside the companies, with no access to any company's data at
 * all. Ranking it highest would make every `atLeast` check silently grant the
 * platform owner a company screen they are forbidden from loading, and the
 * page would render controls whose every request 403s.
 */
const ROLE_RANK: Record<UserRole, number> = {
  super_admin: -1,
  member: 0,
  manager: 1,
  admin: 2,
};

export const ROLE_LABEL: Record<UserRole, string> = {
  member: "Member",
  manager: "Manager",
  admin: "Administrator",
  super_admin: "Platform Owner",
};

export const ROLE_DESCRIPTION: Record<UserRole, string> = {
  member: "Upload invoices and track your own submissions.",
  manager: "Review and approve invoices across the team.",
  admin: "Full access, including user management.",
  super_admin: "Creates companies. No access to any company's invoices.",
};

/**
 * Where each role lands after signing in.
 *
 * A manager lands on the console, not the member dashboard. Their job is the
 * company's queue — reviewing what other people uploaded — and sending them to
 * a page that only lists their own uploads is why the role appeared to do
 * nothing at all for as long as it did.
 *
 * (This comment used to claim a manager's approval work "appears as additional
 * sections inside" the dashboard. It never did. No such sections were built,
 * and the claim outlived anyone checking it.)
 */
export const ROLE_HOME: Record<UserRole, string> = {
  member: "/dashboard",
  manager: "/admin",
  admin: "/admin",
  // Its own destination, because the platform owner has no company dashboard
  // to land on — /dashboard and /admin would both 403 for them.
  super_admin: "/platform",
};

export function homePathFor(user: User | null | undefined): string {
  return user ? ROLE_HOME[user.role] : "/login";
}

export function isAdmin(user: User | null | undefined): boolean {
  return user?.role === "admin";
}

/** The platform owner — outside every company, not above them. */
export function isPlatformOwner(user: User | null | undefined): boolean {
  return user?.role === "super_admin";
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
  // Reachable by managers too. What separates them from an admin is which
  // tabs the console renders, not whether they may open it — see the tab list
  // in app/(protected)/admin/page.tsx, which filters by permission.
  { href: "/admin", label: "Admin", minRole: "manager" },
] as const;

/** The platform owner's nav. Separate because it shares no link with the rest. */
const PLATFORM_LINKS: readonly NavLink[] = [
  { href: "/platform", label: "Companies", minRole: "super_admin" },
] as const;

export function navLinksFor(user: User | null | undefined): NavLink[] {
  if (!user) return [];
  // Not a superset: the platform owner gets the platform's links INSTEAD of
  // the company ones, because every company link would 403 for them.
  if (isPlatformOwner(user)) return [...PLATFORM_LINKS];

  return NAV_LINKS.filter((link) => atLeast(user, link.minRole)).map((link) =>
    // One route, two honest names. A manager opening /admin finds the queue
    // and no user management, so calling it "Admin" would promise something
    // that is not there.
    link.href === "/admin" && user.role === "manager"
      ? { ...link, label: "Review" }
      : link,
  );
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
  // "manager", not "admin". The console is where the queue lives and the queue
  // is a manager's job; the tabs inside it are filtered by permission, which
  // is a finer instrument than a whole-route role gate. This prefix also
  // covers /admin/invoices/[id], the review screen a manager could not reach.
  ["/admin", "manager"],
  ["/platform", "super_admin"],
] as const;

/** Whether a user may view a path. Public and unlisted paths are allowed. */
export function canViewPath(
  user: User | null | undefined,
  pathname: string,
): boolean {
  const rule = ROUTE_MIN_ROLE.find(
    ([prefix]) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
  if (!rule) {
    // Unlisted paths are open to company accounts, but never to the platform
    // owner: they have no company, so a company page would render controls
    // whose every request 403s. /platform is matched by its own rule above.
    return !isPlatformOwner(user);
  }
  // `/platform` is not a rank comparison — it is a different kind of account.
  if (rule[1] === "super_admin") return isPlatformOwner(user);
  return atLeast(user, rule[1]);
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
