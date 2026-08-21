"use client";

import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type InfiniteData,
} from "@tanstack/react-query";
import { toast } from "react-hot-toast";

import { NOTIFICATION_POLL_MS } from "@/lib/env";
import { queryKeys } from "@/lib/query-keys";
import { ApiError } from "@/service/api";
import { notificationService } from "@/service/notificationService/notification.service";
import type { Paginated } from "@/types/api.type";
import type { AppNotification, UnreadCount } from "@/types/invoice.type";

/** How many rows one page of the feed carries. */
export const FEED_PAGE_SIZE = 12;

/**
 * The bell's feed, a page at a time, fetched as the reader approaches the end.
 *
 * Infinite rather than a fixed first page. The bell used to ask for twelve rows
 * and stop there — so the thirteenth notification was simply unreachable, and
 * an account that had been running a while had no way to look back at all.
 *
 * `enabled` because the feed lives behind a dropdown: fetching rows nobody has
 * opened is a request per page load for data that is never rendered. The unread
 * count is a separate, much smaller query, and that one always runs.
 *
 * One cost worth knowing: invalidating or focus-refetching an infinite query
 * refetches EVERY page it has loaded, not just the first. It is bounded here by
 * `enabled` — the feed only exists while the panel is open — and by the fact
 * that `useMarkNotificationRead` below deliberately does not invalidate it.
 */
export function useNotificationFeed(enabled = true) {
  return useInfiniteQuery({
    queryKey: queryKeys.notifications.feed(FEED_PAGE_SIZE),
    queryFn: ({ pageParam }) =>
      notificationService.list({ page: pageParam, pageSize: FEED_PAGE_SIZE }),
    initialPageParam: 1,
    // `undefined` is what tells TanStack there is no next page, which is what
    // `hasNextPage` reports and what stops the scroll observer asking again.
    getNextPageParam: (last) =>
      last.pagination.page < last.pagination.pages
        ? last.pagination.page + 1
        : undefined,
    enabled,
    refetchOnWindowFocus: true,
  });
}

/**
 * Every loaded page as one list, with duplicates removed.
 *
 * The dedupe is not defensive tidying — offset pagination over a feed that
 * grows at the TOP genuinely returns the same row twice. Notification 12 is the
 * last row of page 1; one new arrival pushes it to position 13, and page 2
 * (offset 12) then begins with it again. Rendering both would be two identical
 * rows and a duplicate React key.
 *
 * Cursor pagination would remove the cause rather than the symptom. It is not
 * worth the API change for a dropdown that is open for seconds at a time, and
 * the worst case here — one row briefly missing until the next refetch — is
 * invisible next to the alternative.
 */
export function flattenFeed(
  data: InfiniteData<Paginated<AppNotification>> | undefined,
): AppNotification[] {
  const seen = new Set<string>();
  const rows: AppNotification[] = [];
  for (const page of data?.pages ?? []) {
    for (const item of page.items) {
      if (seen.has(item.id)) continue;
      seen.add(item.id);
      rows.push(item);
    }
  }
  return rows;
}

/**
 * The bell-icon count.
 *
 * Polled, because the backend has no push channel — and on this deployment it
 * cannot have one. The API is a Vercel Python function: every request is a
 * separate invocation with no WebSocket upgrade path, so there is nothing for a
 * socket to stay connected TO. Polling is the correct answer here rather than a
 * fallback, and it is the only one that costs nothing in the bundle.
 *
 * Thirty seconds is a deliberate compromise: often enough that a new upload is
 * noticed while the admin is looking at the page, rare enough that it is not a
 * request per second per open tab. `refetchIntervalInBackground` is left off, so
 * a forgotten tab stops polling entirely.
 *
 * `refetchOnWindowFocus` overrides the global `false` from `query-client.ts`,
 * and this is the query that default was wrong for. The app-wide rule exists so
 * alt-tabbing does not fire a wave of requests for data nobody changed — but
 * this query's ENTIRE PURPOSE is to report that somebody else did something
 * while you were away, and the moment you look at the tab again is exactly when
 * a stale badge is most visible.
 *
 * It costs at most one request, because focus refetching still respects
 * `staleTime`: back within fifteen seconds and nothing fires.
 */
export function useUnreadCount(enabled = true) {
  return useQuery({
    queryKey: queryKeys.notifications.unread,
    queryFn: () => notificationService.unreadCount(),
    enabled,
    refetchInterval: NOTIFICATION_POLL_MS,
    staleTime: 15_000,
    refetchOnWindowFocus: true,
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
      // `feeds`, not `all`: `all` also matches the unread-count query, whose
      // data is a bare `{ count }` with no `pages` to walk.
      queryClient.setQueriesData<InfiniteData<Paginated<AppNotification>>>(
        { queryKey: queryKeys.notifications.feeds },
        (data) =>
          data && {
            ...data,
            pages: data.pages.map((page) => ({
              ...page,
              items: page.items.map((item) =>
                item.id === notificationId ? { ...item, is_read: true } : item,
              ),
            })),
          },
      );
      // Only the count is re-fetched, and that is a change from when this was a
      // single page. Invalidating the feed would refetch every page the reader
      // has scrolled through — so clicking the fifth row of the fourth page
      // would cost four requests to confirm a flag this function just set
      // correctly. The count is the number that must not be wrong; the read dot
      // is already right on screen.
      void queryClient.invalidateQueries({
        queryKey: queryKeys.notifications.unread,
      });
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
      // And every row on screen is now read, in every loaded page — written
      // rather than refetched, for the same reason as above.
      queryClient.setQueriesData<InfiniteData<Paginated<AppNotification>>>(
        { queryKey: queryKeys.notifications.feeds },
        (data) =>
          data && {
            ...data,
            pages: data.pages.map((page) => ({
              ...page,
              items: page.items.map((item) => ({ ...item, is_read: true })),
            })),
          },
      );
    },
    onError: (error: ApiError) => {
      toast.error(error.message || "Could not mark those as read");
    },
  });
}
