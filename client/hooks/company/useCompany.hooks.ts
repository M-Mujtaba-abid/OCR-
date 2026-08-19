"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "react-hot-toast";

import { useAuth } from "@/hooks/auth/useAuth.hooks";
import { isPlatformOwner } from "@/lib/auth/roles";
import { queryKeys } from "@/lib/query-keys";
import { ApiError } from "@/service/api";
import { companyService } from "@/service/companyService/company.service";
import type { OdooConfigInput } from "@/types/company.type";

/**
 * The company the signed-in user belongs to.
 *
 * Long-lived on purpose: a company's name changes about as often as the
 * company does, so this is fetched once and reused for the session rather than
 * re-requested on every mount of every screen that shows it.
 *
 * Disabled for the platform owner. They belong to no company, so the endpoint
 * would 403 — asking anyway would put a red herring in the console on every
 * page load of the platform console.
 */
export function useCompany() {
  const { user, isAuthenticated } = useAuth();

  return useQuery({
    queryKey: queryKeys.company.current,
    queryFn: () => companyService.current(),
    enabled: isAuthenticated && !!user && !isPlatformOwner(user),
    staleTime: 10 * 60_000,
    gcTime: 30 * 60_000,
  });
}

/**
 * This company's Odoo connection status.
 *
 * Requires `system.admin` server-side, so it is gated on the same permission
 * here — a member mounting this would get a 403 on every settings page load.
 *
 * Never carries the API key: the response model has no field for one.
 */
export function useOdooConfig(enabled = true) {
  const { can } = useAuth();

  return useQuery({
    queryKey: queryKeys.company.odoo,
    queryFn: () => companyService.odooStatus(),
    enabled: enabled && can("system.admin"),
  });
}

/**
 * Save credentials. Deliberately does NOT connect.
 *
 * Verifying is a separate button, so a typo comes back as a failed connection
 * with the credentials still on screen — rather than a failed save that throws
 * away what was just typed.
 */
export function useSaveOdooConfig() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: OdooConfigInput) => companyService.saveOdoo(data),
    onSuccess: () => {
      toast.success("Odoo credentials saved. Test them to confirm they work.");
      void queryClient.invalidateQueries({ queryKey: queryKeys.company.odoo });
    },
    onError: (error: ApiError) => {
      toast.error(error.message || "Could not save those credentials");
    },
  });
}

/** Authenticate against Odoo and report which one answered. */
export function useVerifyOdoo() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: () => companyService.verifyOdoo(),
    onSuccess: (result) => {
      toast.success(
        `Connected to ${result.database ?? "Odoo"}${
          result.server_version ? ` (${result.server_version})` : ""
        }`,
      );
      // `verified_at` moved, and that is the difference between "saved" and
      // "working" on the screen above.
      void queryClient.invalidateQueries({ queryKey: queryKeys.company.odoo });
    },
    onError: (error: ApiError) => {
      // 502/503 mean Odoo, 401-shaped faults mean the credentials. The message
      // says which, so it is shown rather than replaced.
      toast.error(error.message || "Could not reach Odoo with those details");
    },
  });
}

export function useDisableOdoo() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: () => companyService.disableOdoo(),
    onSuccess: () => {
      toast.success("Odoo switched off. Your credentials are kept.");
      void queryClient.invalidateQueries({ queryKey: queryKeys.company.odoo });
    },
    onError: (error: ApiError) => {
      toast.error(error.message || "Could not switch Odoo off");
    },
  });
}
