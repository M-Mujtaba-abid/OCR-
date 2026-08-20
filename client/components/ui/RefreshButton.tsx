"use client";

/**
 * Reload one thing, without reloading the page.
 *
 * Four screens had grown their own copy of this — the same ghost button, the
 * same "Refreshing…" label, the same `disabled={isFetching}` — and the copies
 * had already started to disagree about whether the surrounding content dims.
 * One definition, and adding it to a fifth screen is a line rather than a
 * decision.
 *
 * The full-page reload it replaces is not a small thing to avoid. Reloading
 * throws away every cached query, re-runs authentication, and drops the user
 * back to the top of a screen they had scrolled — to refresh one table.
 *
 * Deliberately NOT a spinner that replaces the label. The button keeps its
 * width so the toolbar does not reflow mid-click, and the icon spins instead.
 */
export function RefreshButton({
  onRefresh,
  refreshing,
  label = "Refresh",
  /** What is being refreshed, for screen readers: "Refresh invoices". */
  what,
  size = "md",
  className = "",
}: {
  onRefresh: () => void;
  refreshing: boolean;
  label?: string;
  what?: string;
  size?: "sm" | "md";
  className?: string;
}) {
  return (
    <button
      type="button"
      onClick={onRefresh}
      // Disabled only while a fetch is genuinely in flight. A button that stays
      // dead for a second "to prevent double clicks" is a button people press
      // twice because the first press did nothing visible.
      disabled={refreshing}
      aria-label={what ? `${label} ${what}` : label}
      // aria-busy rather than swapping the text: a screen reader is told the
      // control is working without the accessible name changing underneath it.
      aria-busy={refreshing || undefined}
      className={[
        "inline-flex items-center gap-1.5 rounded-lg font-medium transition-colors",
        "text-slate-600 hover:bg-slate-100 hover:text-slate-900",
        "dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-white",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-300",
        "disabled:cursor-not-allowed disabled:opacity-60",
        "dark:focus-visible:ring-slate-600",
        size === "sm" ? "px-2 py-1 text-xs" : "px-2.5 py-1.5 text-sm",
        className,
      ].join(" ")}
    >
      <svg
        width={size === "sm" ? 12 : 14}
        height={size === "sm" ? 12 : 14}
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
        className={refreshing ? "animate-spin" : ""}
      >
        <path d="M21 12a9 9 0 1 1-2.64-6.36" />
        <path d="M21 3v6h-6" />
      </svg>
      {refreshing ? "Refreshing…" : label}
    </button>
  );
}
