"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { AuthLoading } from "@/components/auth/AuthLoading";
import { useAuth } from "@/hooks/useAuth";

/**
 * Entry point. Waits for the auth bootstrap, then routes by role.
 *
 * Deliberately a client redirect rather than a server one: the session lives
 * behind an HttpOnly cookie the server component cannot exchange for an access
 * token without duplicating the refresh flow.
 *
 * `homePath` comes from the context, which derives it from the user the backend
 * returned — so the destination follows the role stored in the database, not
 * anything the browser could have been told.
 */
export default function HomePage() {
  const { isAuthenticated, isLoading, homePath } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (isLoading) return;
    router.replace(isAuthenticated ? homePath : "/login");
  }, [isAuthenticated, isLoading, homePath, router]);

  return <AuthLoading />;
}
