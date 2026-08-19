"use client";

import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { toast } from "react-hot-toast";

import { queryKeys } from "@/lib/query-keys";
import { ApiError } from "@/service/api";
import {
  userService,
  type ListUsersParams,
} from "@/service/userService/user.service";
import type { Paginated } from "@/types/api.type";
import type { CreateUserInput, User, UserRole } from "@/types/user.type";

/**
 * Put an updated user back into every cached page that holds them.
 *
 * Both mutations below are given the complete updated `User` and used to throw
 * it away, refetching whole pages of the table to reflect one changed field.
 * Patching in place updates the row as the click lands, with no request — and
 * without the page jumping if a sort or filter would have moved it, which a
 * refetch does mid-interaction.
 */
function patchCachedUser(queryClient: QueryClient, updated: User): void {
  // `lists`, not `all`: `all` also matches the stats query, which caches a
  // bare summary object with no `items` to map over.
  queryClient.setQueriesData<Paginated<User>>(
    { queryKey: queryKeys.users.lists },
    (page) =>
      page && {
        ...page,
        items: page.items.map((user) =>
          user.id === updated.id ? updated : user,
        ),
      },
  );
}

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
 * Add somebody to your company.
 *
 * Invalidates the whole `users` prefix rather than patching a row in: a new
 * account changes the table, the pagination and every stat card above it, and
 * a hand-patched insert would have to guess which page the row belongs on
 * under the current sort.
 */
export function useCreateUser() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: CreateUserInput) => userService.create(data),
    onSuccess: (user) => {
      toast.success(`${user.full_name?.trim() || user.email} can now sign in`);
      void queryClient.invalidateQueries({ queryKey: queryKeys.users.all });
    },
    onError: (error: ApiError) => {
      // 409 is a taken email, 403 a role this admin may not grant. Both carry
      // a message written for the person reading it.
      toast.error(error.message || "Could not create that user");
    },
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
      patchCachedUser(queryClient, updated);
      // Roles change how many of each there are.
      void queryClient.invalidateQueries({ queryKey: queryKeys.users.stats });
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
      patchCachedUser(queryClient, updated);
      void queryClient.invalidateQueries({ queryKey: queryKeys.users.stats });
      if (updated.id === currentUserId) {
        void queryClient.invalidateQueries({ queryKey: queryKeys.session });
      }
    },

    onError: (error: ApiError) => {
      toast.error(error.message || "Could not update that account");
    },
  });
}
