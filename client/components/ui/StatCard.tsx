export type StatTone = "neutral" | "positive" | "warning" | "negative" | "accent";

const TONES: Record<StatTone, string> = {
  neutral: "text-slate-900 dark:text-white",
  positive: "text-emerald-600 dark:text-emerald-400",
  warning: "text-amber-600 dark:text-amber-400",
  negative: "text-red-600 dark:text-red-400",
  accent: "text-indigo-600 dark:text-indigo-400",
};

/**
 * A single headline number.
 *
 * `undefined` renders an em dash rather than 0: "we have not loaded this yet"
 * and "this is genuinely zero" are different facts, and showing 0 for the
 * former makes an empty dashboard look like a populated one.
 */
export function StatCard({
  label,
  value,
  tone = "neutral",
  hint,
}: {
  label: string;
  value: number | undefined;
  tone?: StatTone;
  hint?: string;
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 dark:border-slate-800 dark:bg-slate-900">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
        {label}
      </p>
      <p className={`mt-2 text-2xl font-semibold tabular-nums ${TONES[tone]}`}>
        {value === undefined ? "—" : value}
      </p>
      {hint && (
        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{hint}</p>
      )}
    </div>
  );
}
