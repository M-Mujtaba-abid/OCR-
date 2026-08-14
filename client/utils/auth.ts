/**
 * Client-side session state.
 *
 * The access token lives in a module variable — never localStorage,
 * sessionStorage or document.cookie. Anything in web storage is readable by
 * any script on the page, so a single XSS turns into a stolen session. A
 * module variable dies with the tab, which is the point.
 *
 * The refresh token is not here at all: it is an HttpOnly cookie the browser
 * attaches automatically and JavaScript cannot read. That is what makes a page
 * reload able to restore a session without ever exposing a long-lived
 * credential to this code.
 *
 * The user's role is deliberately NOT cached anywhere persistent either. It is
 * read from the server on every session load, so a role change takes effect on
 * the next request rather than whenever a stale copy happens to be refreshed.
 */

let accessToken: string | null = null;

/** Called when the session is definitively gone, so the UI can react. */
let onSessionExpired: (() => void) | null = null;

export function getAccessToken(): string | null {
  return accessToken;
}

/** Store the token after a login or a refresh. */
export function setAuthSession(token: string): void {
  accessToken = token;
}

/**
 * Drop the local session.
 *
 * Does not call the server: this is the "we already know the session is dead"
 * path, used by the 401 interceptor and by logout after the request completes.
 */
export function clearAuthSession(): void {
  accessToken = null;
}

export function isAuthenticatedLocally(): boolean {
  return accessToken !== null;
}

/**
 * Register the redirect-to-login handler.
 *
 * The axios interceptor cannot import the Next router — it is not a React
 * component and has no hook context — so it calls through this instead.
 */
export function setSessionExpiredHandler(handler: (() => void) | null): void {
  onSessionExpired = handler;
}

export function notifySessionExpired(): void {
  clearAuthSession();
  onSessionExpired?.();
}
