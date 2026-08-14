"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";

import { AuthLoading } from "@/components/auth/AuthLoading";
import { Header } from "@/components/layout/Header";
import { useAuth, useSessionExpiryRedirect } from "@/hooks/auth/useAuth.hooks";

/**
 * The single authentication gate for every protected page.
 *
 * Putting it here rather than in each page means a new protected route is
 * guarded simply by being placed in this route group — there is no check to
 * forget.
 *
 * IMPORTANT: this is a UX guard, not a security boundary. It only decides what
 * to render. Every protected API call is independently verified by FastAPI,
 * which remains the sole authority on authorization.
 */
export default function ProtectedLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const { isAuthenticated, isLoading } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  // Mounted once, here: the axios interceptor needs somewhere to send the
  // "session is gone" signal, and this layout wraps everything that could
  // receive a 401.
  useSessionExpiryRedirect();

  useEffect(() => {
    // Never redirect while the session query is still resolving. A signed-in
    // user reloading the page is briefly unauthenticated while refresh is in
    // flight; redirecting then would bounce them to /login on every reload.
    if (isLoading) return;

    if (!isAuthenticated) {
      router.replace(`/login?next=${encodeURIComponent(pathname)}`);
    }
  }, [isAuthenticated, isLoading, pathname, router]);

  if (isLoading) {
    return <AuthLoading label="Checking your session…" />;
  }

  // The redirect above is queued but has not navigated yet. Returning the
  // loading screen rather than the page prevents a flash of protected content.
  if (!isAuthenticated) {
    return <AuthLoading label="Redirecting to sign in…" />;
  }

  return (
    <div className="flex min-h-screen flex-col bg-slate-50 dark:bg-slate-950">
      <Header />
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8 sm:px-6 lg:px-8">
        {children}
      </main>
    </div>
  );
}
