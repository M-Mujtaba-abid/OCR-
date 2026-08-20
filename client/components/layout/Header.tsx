"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { NotificationBell } from "@/components/notifications/NotificationBell";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { useAwaitingMe } from "@/hooks/approval/useApprovals.hooks";
import { useAuth, useLogout } from "@/hooks/auth/useAuth.hooks";
import { useCompany } from "@/hooks/company/useCompany.hooks";
import {
  ROLE_LABEL,
  homePathFor,
  isPlatformOwner,
  navLinksFor,
} from "@/lib/auth/roles";

export function Header() {
  const { user, isAuthenticated } = useAuth();
  const company = useCompany();
  const logout = useLogout();
  const pathname = usePathname();
  const [menuOpen, setMenuOpen] = useState(false);

  // How many approvals are waiting on this person, for the badge beside the
  // link. Shares its query key with the Approvals page, so the two are one
  // cached read rather than two requests.
  //
  // Above the early return, not beside the code that uses it: every hook in
  // this component has to run on every render, and the `return null` below is
  // exactly the branch that would stop this one.
  //
  // Disabled rather than skipped for the platform owner and the signed-out —
  // they belong to no company, so the endpoint has none to scope by and
  // answers 403.
  // Two minutes, not thirty seconds. This is mounted on every page for every
  // user, most of whom are on no chain at all, and a badge that is two minutes
  // stale costs nothing — the notification bell is what tells somebody it is
  // their turn. Opening the Approvals page pulls the shared interval down.
  const awaiting = useAwaitingMe(
    isAuthenticated && !!user && !isPlatformOwner(user),
    120_000,
  );
  const awaitingCount = awaiting.data?.length ?? 0;

  // Navigation is rendered only for authenticated users — showing links that
  // immediately bounce to /login is worse than not showing them.
  if (!isAuthenticated || !user) return null;

  const displayName = user.full_name?.trim() || user.email;
  // Role-filtered, so a member never sees an Admin link they cannot open.
  // Hiding it is courtesy; /admin's layout and the API are what enforce it.
  const navLinks = navLinksFor(user);
  const homeHref = homePathFor(user);

  // Which company this session is working in. On a platform that runs several,
  // "whose data am I looking at" has to be answerable without navigating —
  // otherwise the only way to tell FreshLeaf's queue from KJ's is to recognise
  // the invoices in it.
  //
  // The platform owner belongs to no company, so they get the word for what
  // they are instead. `useCompany` does not even make the request for them.
  const workspace = isPlatformOwner(user)
    ? "Platform"
    : (company.data?.name ?? null);

  return (
    <header className="sticky top-0 z-40 border-b border-slate-200 bg-white/80 backdrop-blur dark:border-slate-800 dark:bg-slate-950/80">
      <div className="mx-auto flex h-16 w-full max-w-5xl items-center justify-between gap-4 px-4 sm:px-6 lg:px-8">
        <div className="flex items-center gap-8">
          <Link href={homeHref} className="flex items-baseline gap-2">
            <span className="text-lg font-semibold tracking-tight text-slate-900 dark:text-white">
              OCR
            </span>
            {workspace && (
              <span
                className="hidden max-w-[180px] truncate border-l border-slate-300 pl-2 text-sm font-medium text-slate-600 sm:inline dark:border-slate-700 dark:text-slate-300"
                title={workspace}
              >
                {workspace}
              </span>
            )}
          </Link>

          <nav aria-label="Main" className="hidden items-center gap-1 sm:flex">
            {navLinks.map((link) => {
              const active = pathname === link.href;
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  aria-current={active ? "page" : undefined}
                  className={[
                    "rounded-md px-3 py-2 text-sm font-medium transition-colors",
                    active
                      ? "bg-slate-100 text-slate-900 dark:bg-slate-800 dark:text-white"
                      : "text-slate-600 hover:bg-slate-50 hover:text-slate-900 dark:text-slate-400 dark:hover:bg-slate-900 dark:hover:text-white",
                  ].join(" ")}
                >
                  {link.label}
                  {link.href === "/approvals" && awaitingCount > 0 && (
                    // Rendered only when there is something waiting. A "0"
                    // sitting in the header permanently is a number people stop
                    // reading, which is the opposite of what a queue badge is
                    // for.
                    <span className="ml-1.5 inline-flex min-w-[18px] items-center justify-center rounded-full bg-sky-600 px-1 text-[10px] font-semibold leading-[18px] text-white dark:bg-sky-500">
                      {awaitingCount > 99 ? "99+" : awaitingCount}
                    </span>
                  )}
                </Link>
              );
            })}
          </nav>
        </div>

        <div className="hidden items-center gap-3 sm:flex">
          <NotificationBell />

          <span
            className="max-w-[200px] truncate text-sm text-slate-600 dark:text-slate-400"
            title={displayName}
          >
            {displayName}
          </span>
          {/* Shown so it is never ambiguous which account is active — the
              difference between the member and admin views is otherwise only
              visible once you are already on a page. */}
          <Badge tone={user.role === "admin" ? "accent" : "neutral"}>
            {ROLE_LABEL[user.role]}
          </Badge>
          <Button
            variant="secondary"
            onClick={() => logout.mutate()}
            isLoading={logout.isPending}
            disabled={logout.isPending}
          >
            Logout
          </Button>
        </div>

        {/* On a phone the bell sits beside the menu toggle rather than inside
            it: a notification count is the reason to open the menu, so hiding
            it behind the menu is the wrong way round. */}
        <div className="flex items-center gap-1 sm:hidden">
          <NotificationBell />

          <button
            type="button"
            onClick={() => setMenuOpen((open) => !open)}
            aria-expanded={menuOpen}
            aria-controls="mobile-nav"
            aria-label="Toggle navigation menu"
            className="rounded-md p-2 text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
              {menuOpen ? <path d="M18 6 6 18M6 6l12 12" /> : <path d="M3 12h18M3 6h18M3 18h18" />}
            </svg>
          </button>
        </div>
      </div>

      {menuOpen && (
        <div
          id="mobile-nav"
          className="border-t border-slate-200 bg-white px-4 py-3 sm:hidden dark:border-slate-800 dark:bg-slate-950"
        >
          {workspace && (
            // The header hides the workspace below `sm`, so the menu is where
            // a phone answers "whose data is this".
            <p className="px-3 pb-2 text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
              {workspace}
            </p>
          )}

          <nav aria-label="Mobile" className="flex flex-col gap-1">
            {navLinks.map((link) => (
              <Link
                key={link.href}
                href={link.href}
                onClick={() => setMenuOpen(false)}
                className="rounded-md px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 dark:text-slate-200 dark:hover:bg-slate-900"
              >
                {link.label}
                {link.href === "/approvals" && awaitingCount > 0 && (
                  <span className="ml-1.5 inline-flex min-w-[18px] items-center justify-center rounded-full bg-sky-600 px-1 text-[10px] font-semibold leading-[18px] text-white dark:bg-sky-500">
                    {awaitingCount > 99 ? "99+" : awaitingCount}
                  </span>
                )}
              </Link>
            ))}
          </nav>
          <div className="mt-3 border-t border-slate-200 pt-3 dark:border-slate-800">
            <div className="flex items-center gap-2 px-3 pb-2">
              <p className="truncate text-sm text-slate-600 dark:text-slate-400">
                {displayName}
              </p>
              <Badge tone={user.role === "admin" ? "accent" : "neutral"}>
                {ROLE_LABEL[user.role]}
              </Badge>
            </div>
            <Button
              variant="secondary"
              fullWidth
              onClick={() => logout.mutate()}
              isLoading={logout.isPending}
              disabled={logout.isPending}
            >
              Logout
            </Button>
          </div>
        </div>
      )}
    </header>
  );
}
