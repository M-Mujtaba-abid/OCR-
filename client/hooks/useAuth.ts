"use client";

import { useContext } from "react";

import { AuthContext } from "@/contexts/AuthContext";
import type { AuthContextValue } from "@/types/auth";

/**
 * Access authentication state and actions.
 *
 * Throws when used outside AuthProvider. That is deliberate: silently
 * returning a null user would render a signed-in user as signed-out and be
 * far harder to diagnose than an explicit error at the point of misuse.
 */
export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (context === null) {
    throw new Error("useAuth must be used within an <AuthProvider>.");
  }
  return context;
}
