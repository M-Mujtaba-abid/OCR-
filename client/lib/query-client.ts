import { QueryClient } from "@tanstack/react-query";

import { ApiError } from "@/service/api";

/**
 * Shared QueryClient configuration.
 *
 * The defaults here are what actually make the app feel fast. Without a
 * staleTime, TanStack refetches on every mount — so switching tabs, or moving
 * between /dashboard and /admin, re-hits the network for data that has not
 * changed, and the user watches a spinner each time.
 */
export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // Data is considered fresh for a minute. Remounting a component inside
        // that window reads straight from cache and paints immediately.
        staleTime: 60_000,
        // Keep it around for five minutes after the last observer unmounts, so
        // returning to a tab shows the previous data instantly while any
        // refetch happens in the background.
        gcTime: 5 * 60_000,
        // Alt-tabbing back to the browser should not fire a wave of requests.
        // Mount and reconnect still refetch, which is where staleness matters.
        refetchOnWindowFocus: false,
        refetchOnReconnect: true,

        retry: (failureCount, error) => {
          // Retrying a 4xx is pointless — the request will be just as invalid
          // the second time, and retrying a 401 fights the refresh
          // interceptor, which has already decided the session is gone.
          if (error instanceof ApiError) {
            if (error.status >= 400 && error.status < 500) return false;
            if (error.status === 0) return failureCount < 1; // one network retry
          }
          return failureCount < 2;
        },
        retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),
      },
      mutations: {
        // Never automatically. A retried POST can create two of something.
        retry: false,
      },
    },
  });
}

/**
 * Browser singleton.
 *
 * On the server a fresh client is made per request — a shared one would let one
 * user's data be served out of another user's cache.
 */
let browserQueryClient: QueryClient | undefined;

export function getQueryClient(): QueryClient {
  if (typeof window === "undefined") return makeQueryClient();
  browserQueryClient ??= makeQueryClient();
  return browserQueryClient;
}
