"use client";

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "danger" | "ghost";
  /** "sm" is for controls that repeat once per table row, where full-size
   *  padding is what pushes a row's actions wider than the column holding
   *  them. Everything standalone stays "md". */
  size?: "sm" | "md";
  isLoading?: boolean;
  fullWidth?: boolean;
}

const VARIANTS: Record<NonNullable<ButtonProps["variant"]>, string> = {
  primary:
    "bg-slate-900 text-white hover:bg-slate-800 focus-visible:ring-slate-400 dark:bg-white dark:text-slate-900 dark:hover:bg-slate-200",
  secondary:
    "border border-slate-300 bg-white text-slate-800 hover:bg-slate-50 focus-visible:ring-slate-300 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:hover:bg-slate-800",
  danger:
    "bg-red-600 text-white hover:bg-red-700 focus-visible:ring-red-300 dark:bg-red-600 dark:hover:bg-red-500",
  ghost:
    "text-slate-700 hover:bg-slate-100 focus-visible:ring-slate-300 dark:text-slate-200 dark:hover:bg-slate-800",
};

/** Padding and type size travel together — a small button with body-sized
 *  text is not smaller, only tighter. */
const SIZES: Record<NonNullable<ButtonProps["size"]>, string> = {
  sm: "px-2.5 py-1.5 text-xs",
  md: "px-4 py-2.5 text-sm",
};

export function Button({
  variant = "primary",
  size = "md",
  isLoading = false,
  fullWidth = false,
  className,
  children,
  disabled,
  ...props
}: ButtonProps) {
  return (
    <button
      {...props}
      // Disabled while loading so a double-click cannot submit twice — which
      // on register would produce a duplicate-email error for the same person.
      disabled={disabled || isLoading}
      aria-busy={isLoading || undefined}
      className={[
        // whitespace-nowrap: a label like "Re-match" breaking in half is what
        // turns a row of actions into three lines of them.
        "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg",
        "font-medium transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2",
        "disabled:cursor-not-allowed disabled:opacity-60",
        "dark:focus-visible:ring-offset-slate-950",
        SIZES[size],
        VARIANTS[variant],
        fullWidth ? "w-full" : "",
        className ?? "",
      ].join(" ")}
    >
      {isLoading && <Spinner />}
      {children}
    </button>
  );
}

function Spinner() {
  return (
    <svg
      className="h-4 w-4 animate-spin"
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 0 1 8-8v4a4 4 0 0 0-4 4H4z" />
    </svg>
  );
}
