"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "react-hot-toast";

import { queryKeys } from "@/lib/query-keys";
import { ApiError } from "@/service/api";
import { platformService } from "@/service/platformService/platform.service";
import type { CompanyCreateInput } from "@/types/platform.type";

/**
 * Every company on the platform.
 *
 * `enabled` is deliberately absent: these hooks are only mounted inside
 * `/platform`, which the route guard already restricts to the platform owner.
 * Gating them again here would be a second copy of that rule.
 */
export function useCompanies() {
  return useQuery({
    queryKey: queryKeys.platform.companies,
    queryFn: () => platformService.listCompanies(),
  });
}

export function usePlatformStats() {
  return useQuery({
    queryKey: queryKeys.platform.stats,
    queryFn: () => platformService.stats(),
  });
}

/**
 * Create a company and its first administrator.
 *
 * Invalidates the whole platform prefix rather than patching the new row in:
 * a new company changes the list and every count above it, and the counts are
 * computed server-side from data this client does not hold.
 */
export function useCreateCompany() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: CompanyCreateInput) => platformService.createCompany(data),
    onSuccess: (result) => {
      toast.success(`${result.company.name} created`);
      void queryClient.invalidateQueries({ queryKey: queryKeys.platform.all });
    },
    onError: (error: ApiError) => {
      // 409 is a taken admin email — the most common failure, and the message
      // says which address.
      toast.error(error.message || "Could not create that company");
    },
  });
}

/**
 * Suspend a company, or bring it back.
 *
 * One mutation for both directions: they are the same write with a different
 * boolean, and two hooks would be two places to keep the invalidation in step.
 */
export function useSetCompanyActive() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ companyId, active }: { companyId: string; active: boolean }) =>
      active
        ? platformService.restore(companyId)
        : platformService.suspend(companyId),
    onSuccess: (company) => {
      toast.success(
        company.is_active
          ? `${company.name} restored`
          : `${company.name} suspended — nobody there can sign in`,
      );
      void queryClient.invalidateQueries({ queryKey: queryKeys.platform.all });
    },
    onError: (error: ApiError) => {
      toast.error(error.message || "Could not change that company");
    },
  });
}
