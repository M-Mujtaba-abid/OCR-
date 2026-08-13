"use client";

/**
 * Authentication state.
 *
 * The access token lives in the module-level store (lib/auth/token-store) so
 * the API client can read it synchronously; it is mirrored into React state
 * here purely so components re-render when it changes.
 */

import {
  createContext,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { usePathname, useRouter } from "next/navigation";

import * as authApi from "@/lib/api/auth";
import {
  clearAccessToken,
  setAccessToken,
  setSessionExpiredHandler,
} from "@/lib/auth/token-store";
import type {
  AuthContextValue,
  LoginRequest,
  RegisterRequest,
  User,
} from "@/types/auth";

export const AuthContext = createContext<AuthContextValue | null>(null);

const PUBLIC_PATHS = ["/login", "/register"];

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const router = useRouter();
  const pathname = usePathname();

  // Lets the session-expiry handler read the current path without depending on
  // it, which would otherwise re-register the handler on every navigation.
  // Written in an effect, never during render — a ref mutated while rendering
  // can hold a value from a render React later discards.
  const pathnameRef = useRef(pathname);
  useEffect(() => {
    pathnameRef.current = pathname;
  }, [pathname]);

  const applyToken = useCallback((next: string | null) => {
    setAccessToken(next);
    setToken(next);
  }, []);

  const clearAuth = useCallback(() => {
    clearAccessToken();
    setToken(null);
    setUser(null);
  }, []);

  /* ---------------------------------------------------------------------
   * Bootstrap
   *
   * On mount: refresh -> me. A failed refresh means "not signed in", which is
   * the normal state for a first-time visitor — it is NOT an application
   * error and must not surface as one.
   * ------------------------------------------------------------------ */
  useEffect(() => {
    let cancelled = false;

    async function bootstrap() {
      try {
        const tokens = await authApi.refresh();
        if (cancelled) return;
        applyToken(tokens.access_token);

        // Refresh returns no user object, so fetch it separately.
        const me = await authApi.getMe();
        if (cancelled) return;
        setUser(me);
      } catch {
        if (!cancelled) clearAuth();
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    }

    void bootstrap();
    return () => {
      cancelled = true;
    };
  }, [applyToken, clearAuth]);

  /* ---------------------------------------------------------------------
   * Session expiry
   *
   * The API client cannot import the router, so it calls into the store and
   * the store calls this handler.
   * ------------------------------------------------------------------ */
  useEffect(() => {
    setSessionExpiredHandler(() => {
      setToken(null);
      setUser(null);

      const current = pathnameRef.current;
      if (current && !PUBLIC_PATHS.includes(current)) {
        router.replace(`/login?next=${encodeURIComponent(current)}`);
      }
    });
    return () => setSessionExpiredHandler(null);
  }, [router]);

  /* ---------------------------------------------------------------------
   * Actions
   * ------------------------------------------------------------------ */
  const login = useCallback(
    async (credentials: LoginRequest): Promise<User> => {
      const data = await authApi.login(credentials);
      applyToken(data.access_token);
      setUser(data.user); // login returns the user, so no extra /me call
      return data.user;
    },
    [applyToken],
  );

  const register = useCallback(
    async (payload: RegisterRequest): Promise<User> => {
      // Returns the created user only — the backend does not issue tokens on
      // register, so the caller sends the user to /login.
      return authApi.register(payload);
    },
    [],
  );

  const logout = useCallback(async () => {
    try {
      await authApi.logout();
    } catch {
      // Deliberately swallowed. If the network is down or the session is
      // already gone server-side, the user still expects to be signed out
      // locally. Leaving them "logged in" on a failed request would be worse.
    } finally {
      clearAuth();
      router.replace("/login");
    }
  }, [clearAuth, router]);

  const logoutAll = useCallback(async () => {
    try {
      await authApi.logoutAll();
    } catch {
      // Same reasoning as logout.
    } finally {
      clearAuth();
      router.replace("/login");
    }
  }, [clearAuth, router]);

  const refreshUser = useCallback(async () => {
    const me = await authApi.getMe();
    setUser(me);
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      accessToken: token,
      isAuthenticated: Boolean(user),
      isLoading,
      login,
      register,
      logout,
      logoutAll,
      refreshUser,
    }),
    [user, token, isLoading, login, register, logout, logoutAll, refreshUser],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
