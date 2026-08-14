"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";

import { AuthLoading } from "@/components/auth/AuthLoading";
import { useAuth } from "@/hooks/auth/useAuth.hooks";
import { canViewPath, homePathFor } from "@/lib/auth/roles";

/**
 * Role gate for everything under /admin.
 *
 * Sits inside the (protected) group, so authentication is already settled by
 * the time this runs — this layout only decides ROLE, never identity.
 *
 * Placing it in a layout rather than in the page means any admin sub-route
 * added later (/admin/users, /admin/settings) is guarded by existing, not by
 * someone remembering to add a check.
 *
 * A non-admin is sent to their own home rather than to /login: they ARE signed
 * in, and bouncing them to a login form for a page they simply may not see is
 * both confusing and a hint that the page is worth attacking.
 *
 * As always: cosmetic. GET /api/v1/users independently requires `user.read`
 * and returns 403 to anyone else, whatever this component renders.
 */
export default function AdminLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const { user, isLoading } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  const allowed = canViewPath(user, pathname);

  useEffect(() => {
    // `user` is null for one render while the parent layout bootstraps.
    // Redirecting then would throw an admin off their own page on every reload.
    if (isLoading || !user) return;
    if (!allowed) router.replace(homePathFor(user));
  }, [allowed, isLoading, user, router]);

  if (isLoading || !user) {
    return <AuthLoading label="Checking your access…" />;
  }

  if (!allowed) {
    return <AuthLoading label="Redirecting…" />;
  }

  return <>{children}</>;
}
