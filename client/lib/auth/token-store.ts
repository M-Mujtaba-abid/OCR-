/**
 * In-memory access token store.
 *
 * WHY A MODULE SINGLETON RATHER THAN REACT STATE
 *
 * The API client must read the current token synchronously, from outside the
 * React tree, on every request — including retries that happen after a refresh
 * mid-flight. Reading it from a hook would mean either threading the token
 * through every call site or capturing a stale value in a closure.
 *
 * WHY MEMORY RATHER THAN localStorage
 *
 * Anything in localStorage or sessionStorage is readable by any script on the
 * page, so a single XSS gives up the token. Memory dies with the tab, and the
 * HttpOnly refresh cookie restores the session on next load — which is exactly
 * what the bootstrap flow does. The cost is that a hard refresh briefly shows
 * a loading state; that is the intended trade.
 *
 * The refresh token is NEVER handled here. It exists only inside the HttpOnly
 * cookie that the browser attaches automatically, and JavaScript cannot read it.
 */

type Listener = (token: string | null) => void;

let accessToken: string | null = null;
const listeners = new Set<Listener>();

export function getAccessToken(): string | null {
  return accessToken;
}

export function setAccessToken(token: string | null): void {
  accessToken = token;
  for (const listener of listeners) listener(token);
}

export function clearAccessToken(): void {
  setAccessToken(null);
}

/** Lets React mirror the token into state for rendering. Returns an unsubscribe. */
export function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/* -------------------------------------------------------------------------
 * Session-expiry callback
 *
 * When refresh fails there is no valid session left, but `lib/api/client.ts`
 * must not import React or the router — it would create a cycle and would not
 * work outside a component. The AuthProvider registers a handler here instead.
 * ---------------------------------------------------------------------- */

type SessionExpiredHandler = () => void;

let onSessionExpired: SessionExpiredHandler | null = null;

export function setSessionExpiredHandler(handler: SessionExpiredHandler | null): void {
  onSessionExpired = handler;
}

export function notifySessionExpired(): void {
  clearAccessToken();
  onSessionExpired?.();
}
