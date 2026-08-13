/**
 * Full-screen loading state shown while the auth bootstrap runs.
 *
 * Server Component — purely presentational, ships no JavaScript.
 */
export function AuthLoading({ label = "Loading…" }: { label?: string }) {
  return (
    <div
      className="flex min-h-screen flex-col items-center justify-center gap-4 bg-white dark:bg-slate-950"
      // Announced politely so a screen-reader user learns the app is working
      // rather than sitting on an apparently empty page.
      role="status"
      aria-live="polite"
    >
      <svg
        className="h-8 w-8 animate-spin text-slate-400"
        viewBox="0 0 24 24"
        fill="none"
        aria-hidden="true"
      >
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 0 1 8-8v4a4 4 0 0 0-4 4H4z" />
      </svg>
      <p className="text-sm text-slate-500 dark:text-slate-400">{label}</p>
    </div>
  );
}
