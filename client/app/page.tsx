"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { AuthLoading } from "@/components/auth/AuthLoading";
import { useAuth } from "@/hooks/useAuth";

/**
 * Entry point. Waits for the auth bootstrap, then routes to the right place.
 *
 * Deliberately a client redirect rather than a server one: the session lives
 * behind an HttpOnly cookie the server component cannot exchange for an access
 * token without duplicating the refresh flow.
 */
export default function HomePage() {
  const { isAuthenticated, isLoading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (isLoading) return;
    router.replace(isAuthenticated ? "/dashboard" : "/login");
  }, [isAuthenticated, isLoading, router]);

  return <AuthLoading />;
}
