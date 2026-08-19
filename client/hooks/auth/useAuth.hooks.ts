"use client";

import { useCallback, useEffect, useMemo } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "react-hot-toast";

import { queryKeys } from "@/lib/query-keys";
import { ApiError, refreshAccessToken } from "@/service/api";
import { authService } from "@/service/authService/auth.service";
import {
  clearAuthSession,
  getAccessToken,
  setAuthSession,
  setSessionExpiredHandler,
} from "@/utils/auth";
import { homePathFor } from "@/lib/auth/roles";
import type {
  LoginInput,
  Permission,
  Session,
} from "@/types/user.type";

/**
 * Load the session: refresh, then who-am-I.
 *
 * `null` means signed out. That is a normal state for a first-time visitor, not
 * an error — returning null rather than throwing keeps `isError` meaningful and
 * stops the UI from rendering a failure banner to everyone who is not logged in.
 */
async function loadSession(): Promise<Session | null> {
  // On a hard reload the access token is gone (it only ever lived in memory),
  // but the HttpOnly refresh cookie survives — that is what restores the
  // session without ever exposing a long-lived credential to JavaScript.
  if (!getAccessToken()) {
    const refreshed = await refreshAccessToken();
    if (!refreshed) return null;
  }

  try {
    // In parallel: neither depends on the other, and a serial pair would double
    // the time to first paint on every cold load.
    const [user, permissions] = await Promise.all([
      authService.getProfile(),
      authService.getPermissions(),
    ]);
    return { user, permissions };
  } catch (error) {
    // A 401 here means the refresh produced a token the server will not accept
    // — treat it as signed out rather than as a broken app.
    if (error instanceof ApiError && error.status === 401) return null;
    throw error;
  }
}

/**
 * The session query.
 *
 * Every consumer shares one cache entry, so ten components calling this produce
 * one request — the same deduplication a context gave, plus caching across
 * navigations. Moving between /dashboard and /admin no longer re-runs
 * refresh → me → permissions.
 */
export function useSession() {
  return useQuery({
    queryKey: queryKeys.session,
    queryFn: loadSession,
    // The user object changes rarely; the access token's own expiry is handled
    // by the interceptor, not by refetching this.
    staleTime: 5 * 60_000,
    // A failed session load means "signed out", and retrying cannot change it.
    retry: false,
  });
}

/**
 * The single hook components use for identity.
 *
 * Deliberately the same surface the old React context exposed, so this is a
 * drop-in replacement — but backed by the query cache, which is why navigation
 * no longer blocks on a network round trip.
 */
export function useAuth() {
  const { data, isLoading, isFetching } = useSession();

  // Memoised so the fallback `[]` is not a fresh array each render, which would
  // change `can`'s identity every time and defeat every downstream memo.
  const permissions = useMemo(() => data?.permissions ?? [], [data?.permissions]);

  const can = useCallback(
    (...required: Permission[]) =>
      required.every((permission) => permissions.includes(permission)),
    [permissions],
  );

  return {
    user: data?.user ?? null,
    permissions,
    can,
    isAuthenticated: Boolean(data?.user),
    isLoading,
    isFetching,
    homePath: homePathFor(data?.user ?? null),
  };
}

/**
 * Wire the interceptor's "session is gone" signal to a redirect.
 *
 * Mounted once, in the protected layout. The axios interceptor cannot import
 * the router — it is not a component — so it calls through utils/auth instead.
 */
export function useSessionExpiryRedirect() {
  const router = useRouter();
  const queryClient = useQueryClient();

  useEffect(() => {
    setSessionExpiredHandler(() => {
      queryClient.setQueryData(queryKeys.session, null);
      // Everything cached belonged to the previous user.
      queryClient.removeQueries();
      toast.error("Your session expired. Please sign in again.");
      router.replace("/login");
    });
    return () => setSessionExpiredHandler(null);
  }, [router, queryClient]);
}

/* -------------------------------------------------------------------------
 * Mutations
 * ---------------------------------------------------------------------- */

// No `useRegister`. Accounts are created by a company's own administrator —
// see `useCreateUser` in hooks/user/useUsers.hooks.ts.

export function useLogin() {
  const router = useRouter();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: LoginInput) => authService.login(data),

    onSuccess: async (data) => {
      setAuthSession(data.access_token);

      // Login returns the user but not the permissions, so fetch those before
      // seeding the cache. Seeding rather than invalidating means the next
      // screen renders from cache with no second loading state.
      let permissions: Permission[] = [];
      try {
        permissions = await authService.getPermissions();
      } catch {
        // Not a failed login — the user is signed in either way. They see the
        // least-privileged UI until the next load rather than being stranded
        // on the login form.
      }

      queryClient.setQueryData(queryKeys.session, {
        user: data.user,
        permissions,
      });

      toast.success(`Welcome back, ${data.user.full_name?.trim() || data.user.email}`);
      router.replace(homePathFor(data.user));
    },

    onError: (error: ApiError) => {
      toast.error(error.message || "Login failed");
    },
  });
}

export function useLogout() {
  const router = useRouter();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: () => authService.logout(),
    // onSettled, not onSuccess: if the network is down or the session is
    // already gone server-side, the user still expects to be signed out here.
    // Leaving them "logged in" after a failed request would be worse.
    onSettled: () => {
      clearAuthSession();
      queryClient.removeQueries();
      queryClient.setQueryData(queryKeys.session, null);
      toast.success("Signed out");
      router.replace("/login");
    },
  });
}

export function useLogoutAll() {
  const router = useRouter();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: () => authService.logoutAll(),
    onSuccess: (data) => {
      toast.success(`Signed out of ${data.revoked_sessions} device(s)`);
    },
    onSettled: () => {
      clearAuthSession();
      queryClient.removeQueries();
      queryClient.setQueryData(queryKeys.session, null);
      router.replace("/login");
    },
  });
}

/** Active devices, for the account screen. Fetched only when asked for. */
export function useSessions(enabled = true) {
  return useQuery({
    queryKey: ["auth", "sessions"],
    queryFn: () => authService.getSessions(),
    enabled,
  });
}
