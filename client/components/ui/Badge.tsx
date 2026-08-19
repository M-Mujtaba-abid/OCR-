export type BadgeTone = "positive" | "negative" | "warning" | "neutral" | "accent";

const TONES: Record<BadgeTone, string> = {
  positive:
    "bg-emerald-50 text-emerald-700 ring-emerald-600/20 dark:bg-emerald-950 dark:text-emerald-300 dark:ring-emerald-400/20",
  negative:
    "bg-red-50 text-red-700 ring-red-600/20 dark:bg-red-950 dark:text-red-300 dark:ring-red-400/20",
  warning:
    "bg-amber-50 text-amber-700 ring-amber-600/20 dark:bg-amber-950 dark:text-amber-300 dark:ring-amber-400/20",
  neutral:
    "bg-slate-100 text-slate-700 ring-slate-500/20 dark:bg-slate-800 dark:text-slate-200 dark:ring-slate-400/20",
  accent:
    "bg-indigo-50 text-indigo-700 ring-indigo-600/20 dark:bg-indigo-950 dark:text-indigo-300 dark:ring-indigo-400/20",
};

/** The same five outcomes at full strength, for the optional leading dot. */
const DOTS: Record<BadgeTone, string> = {
  positive: "bg-emerald-500",
  negative: "bg-red-500",
  warning: "bg-amber-500",
  neutral: "bg-slate-400",
  accent: "bg-indigo-500",
};

export function Badge({
  tone = "neutral",
  dot = false,
  children,
}: {
  tone?: BadgeTone;
  /** A solid dot before the label. Worth it in a list, where the eye finds the
   *  colour a beat before it reads the word; noise anywhere else. */
  dot?: boolean;
  children: React.ReactNode;
}) {
  return (
    <span
      // Never wrapped. A two-word status breaking across three lines is what
      // makes a table row tall enough to lose its own alignment.
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${TONES[tone]}`}
    >
      {dot && (
        <span
          aria-hidden="true"
          className={`h-1.5 w-1.5 shrink-0 rounded-full ${DOTS[tone]}`}
        />
      )}
      {children}
    </span>
  );
}

/** A label/value pair inside a <dl>. */
export function Field({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
        {label}
      </dt>
      <dd className="mt-1 text-sm text-slate-900 dark:text-slate-100">{value}</dd>
    </div>
  );
}
