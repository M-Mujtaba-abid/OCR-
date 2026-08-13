"use client";

import { useSearchParams } from "next/navigation";

import { Alert } from "@/components/ui/Alert";

/**
 * Confirmation shown after registration redirects here with ?registered=1.
 *
 * Split into its own component so the success message and the login form each
 * sit behind their own Suspense boundary — a searchParams read in either one
 * would otherwise opt the whole page out of static rendering.
 */
export function RegisteredNotice() {
  const registered = useSearchParams().get("registered");
  if (!registered) return null;

  return <Alert variant="success">Account created. Please sign in below.</Alert>;
}
