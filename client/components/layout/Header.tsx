"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { useAuth } from "@/hooks/useAuth";
import { ROLE_LABEL, homePathFor, navLinksFor } from "@/lib/auth/roles";

export function Header() {
  const { user, isAuthenticated, logout } = useAuth();
  const pathname = usePathname();
  const [menuOpen, setMenuOpen] = useState(false);

  // Navigation is rendered only for authenticated users — showing links that
  // immediately bounce to /login is worse than not showing them.
  if (!isAuthenticated || !user) return null;

  const displayName = user.full_name?.trim() || user.email;
  // Role-filtered, so a member never sees an Admin link they cannot open.
  // Hiding it is courtesy; /admin's layout and the API are what enforce it.
  const navLinks = navLinksFor(user);
  const homeHref = homePathFor(user);

  return (
    <header className="sticky top-0 z-40 border-b border-slate-200 bg-white/80 backdrop-blur dark:border-slate-800 dark:bg-slate-950/80">
      <div className="mx-auto flex h-16 w-full max-w-5xl items-center justify-between gap-4 px-4 sm:px-6 lg:px-8">
        <div className="flex items-center gap-8">
          <Link
            href={homeHref}
            className="text-lg font-semibold tracking-tight text-slate-900 dark:text-white"
          >
            OCR
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
                </Link>
              );
            })}
          </nav>
        </div>

        <div className="hidden items-center gap-3 sm:flex">
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
          <Button variant="secondary" onClick={() => void logout()}>
            Logout
          </Button>
        </div>

        <button
          type="button"
          onClick={() => setMenuOpen((open) => !open)}
          aria-expanded={menuOpen}
          aria-controls="mobile-nav"
          aria-label="Toggle navigation menu"
          className="rounded-md p-2 text-slate-600 hover:bg-slate-100 sm:hidden dark:text-slate-300 dark:hover:bg-slate-800"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            {menuOpen ? <path d="M18 6 6 18M6 6l12 12" /> : <path d="M3 12h18M3 6h18M3 18h18" />}
          </svg>
        </button>
      </div>

      {menuOpen && (
        <div
          id="mobile-nav"
          className="border-t border-slate-200 bg-white px-4 py-3 sm:hidden dark:border-slate-800 dark:bg-slate-950"
        >
          <nav aria-label="Mobile" className="flex flex-col gap-1">
            {navLinks.map((link) => (
              <Link
                key={link.href}
                href={link.href}
                onClick={() => setMenuOpen(false)}
                className="rounded-md px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 dark:text-slate-200 dark:hover:bg-slate-900"
              >
                {link.label}
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
            <Button variant="secondary" fullWidth onClick={() => void logout()}>
              Logout
            </Button>
          </div>
        </div>
      )}
    </header>
  );
}
