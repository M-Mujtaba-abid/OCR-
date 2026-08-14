"use client";

import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { toast } from "react-hot-toast";

import { queryKeys } from "@/lib/query-keys";
import { ApiError } from "@/service/api";
import {
  userService,
  type ListUsersParams,
} from "@/service/userService/user.service";
import type { User, UserRole } from "@/types/user.type";

export function useUsers(params: ListUsersParams = {}) {
  return useQuery({
    queryKey: queryKeys.users.list(params),
    queryFn: () => userService.list(params),
    // Keeps the current page on screen while the next one loads instead of
    // flashing an empty table.
    placeholderData: keepPreviousData,
  });
}

export function useUserStats() {
  return useQuery({
    queryKey: queryKeys.users.stats,
    queryFn: () => userService.stats(),
  });
}

/**
 * Change a user's role.
 *
 * Invalidates the session too, but only when the caller changed their own
 * standing — their permission list would otherwise be stale and the UI would
 * keep offering actions the server now refuses.
 */
export function useChangeUserRole(currentUserId?: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: UserRole }) =>
      userService.changeRole(userId, role),

    onSuccess: (updated: User) => {
      toast.success(`${updated.full_name?.trim() || updated.email} is now ${updated.role}`);
      void queryClient.invalidateQueries({ queryKey: queryKeys.users.all });
      if (updated.id === currentUserId) {
        void queryClient.invalidateQueries({ queryKey: queryKeys.session });
      }
    },

    onError: (error: ApiError) => {
      toast.error(error.message || "Could not change that role");
    },
  });
}

export function useSetUserActive(currentUserId?: string) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ userId, isActive }: { userId: string; isActive: boolean }) =>
      userService.setActive(userId, isActive),

    onSuccess: (updated: User) => {
      toast.success(
        `${updated.full_name?.trim() || updated.email} ${updated.is_active ? "enabled" : "disabled"}`,
      );
      void queryClient.invalidateQueries({ queryKey: queryKeys.users.all });
      if (updated.id === currentUserId) {
        void queryClient.invalidateQueries({ queryKey: queryKeys.session });
      }
    },

    onError: (error: ApiError) => {
      toast.error(error.message || "Could not update that account");
    },
  });
}
