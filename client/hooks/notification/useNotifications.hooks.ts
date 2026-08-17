"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "react-hot-toast";

import { queryKeys } from "@/lib/query-keys";
import { ApiError } from "@/service/api";
import {
  notificationService,
  type ListNotificationsParams,
} from "@/service/notificationService/notification.service";
import type { Paginated } from "@/types/api.type";
import type { AppNotification, UnreadCount } from "@/types/invoice.type";

export function useNotifications(params: ListNotificationsParams = {}) {
  return useQuery({
    queryKey: queryKeys.notifications.list(params),
    queryFn: () => notificationService.list(params),
  });
}

/**
 * The bell-icon count.
 *
 * Polled, because the backend has no push channel yet. Thirty seconds is a
 * deliberate compromise: often enough that a new upload is noticed while the
 * admin is looking at the page, rare enough that it is not a request per
 * second per open tab. `refetchIntervalInBackground` is left off, so a
 * forgotten tab stops polling entirely.
 */
export function useUnreadCount(enabled = true) {
  return useQuery({
    queryKey: queryKeys.notifications.unread,
    queryFn: () => notificationService.unreadCount(),
    enabled,
    refetchInterval: 30_000,
    staleTime: 15_000,
  });
}

export function useMarkNotificationRead() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (notificationId: string) =>
      notificationService.markRead(notificationId),
    onSuccess: (_data, notificationId) => {
      // The count is what the user is watching; move it now rather than a
      // round trip later. The invalidation below is the reconciliation, not
      // the update — if the server disagrees, its answer wins a moment later.
      queryClient.setQueryData<UnreadCount>(queryKeys.notifications.unread, (current) =>
        current && { count: Math.max(0, current.count - 1) },
      );
      queryClient.setQueriesData<Paginated<AppNotification>>(
        { queryKey: queryKeys.notifications.all },
        (page) =>
          page && {
            ...page,
            items: page.items.map((item) =>
              item.id === notificationId ? { ...item, is_read: true } : item,
            ),
          },
      );
      void queryClient.invalidateQueries({ queryKey: queryKeys.notifications.all });
    },
    onError: (error: ApiError) => {
      toast.error(error.message || "Could not mark that as read");
    },
  });
}

export function useMarkAllNotificationsRead() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: () => notificationService.markAllRead(),
    onSuccess: (data) => {
      // Zero is not worth a toast — the button was a no-op.
      if (data.marked > 0) toast.success(`${data.marked} marked as read`);
      // The server just told us there is nothing unread left, so say so now.
      queryClient.setQueryData<UnreadCount>(queryKeys.notifications.unread, { count: 0 });
      void queryClient.invalidateQueries({ queryKey: queryKeys.notifications.all });
    },
    onError: (error: ApiError) => {
      toast.error(error.message || "Could not mark those as read");
    },
  });
}
