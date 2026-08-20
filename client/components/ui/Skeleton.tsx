/**
 * Loading placeholders shaped like the thing that is coming.
 *
 * Server Components — no "use client", for the same reason `Alert` has none.
 * There is not one event handler in this file, so it ships zero JavaScript; a
 * loading placeholder that adds to the bundle it is covering for would be a
 * poor trade.
 *
 * A skeleton is only worth more than a spinner if it is the RIGHT skeleton. Six
 * grey bars where a table is about to appear tells the reader how much is
 * loading and roughly what it is; the same six bars where a form is about to
 * appear is a spinner with extra steps, and worse, it makes the layout jump
 * twice — once into the placeholder and once out of it.
 *
 * So these are shapes, not one blob: a table skeleton has columns, a list
 * skeleton has rows the height of real rows, a stat skeleton is a tile.
 *
 * Accessibility is the part that is easy to skip and easy to get wrong. A
 * shimmering box announces nothing, so every skeleton here carries
 * `role="status"` with a real sentence inside `sr-only`, and hides the decorative
 * bars from the accessibility tree. A screen reader hears "Loading invoices",
 * once, rather than nothing at all or a wall of empty divs.
 *
 * `animate-pulse` matches the chart placeholders that were here first
 * (`PipelineBar`, `TrendChart`, `StatusBreakdown`) — one loading vocabulary
 * across the app rather than two.
 */

/** One shimmering block. The primitive the shaped skeletons are built from. */
export function Skeleton({ className = "" }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={`animate-pulse rounded bg-slate-200 dark:bg-slate-800 ${className}`}
    />
  );
}

/**
 * The announcement wrapper. Every shaped skeleton uses it so the label is never
 * forgotten — which is exactly what happens when it is left to each call site.
 */
function Loading({
  label,
  children,
  className = "",
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div role="status" aria-live="polite" className={className}>
      <span className="sr-only">{label}</span>
      {children}
    </div>
  );
}

/** Prose: a few lines of decreasing length, like real text. */
export function SkeletonText({
  lines = 3,
  label = "Loading",
  className = "",
}: {
  lines?: number;
  label?: string;
  className?: string;
}) {
  return (
    <Loading label={label} className={`space-y-2 ${className}`}>
      {Array.from({ length: lines }).map((_, index) => (
        <Skeleton
          key={index}
          // The last line short, because the last line of a paragraph is.
          className={`h-3 ${index === lines - 1 ? "w-2/3" : "w-full"}`}
        />
      ))}
    </Loading>
  );
}

/**
 * A table, with its columns.
 *
 * Rendered inside the same bordered container the real table uses, so the box
 * does not resize when the data lands — the single most noticeable thing a
 * badly-shaped skeleton does.
 */
export function SkeletonTable({
  rows = 5,
  columns = 4,
  label = "Loading table",
}: {
  rows?: number;
  columns?: number;
  label?: string;
}) {
  return (
    <Loading label={label} className="p-4">
      <div className="mb-3 flex gap-4 border-b border-slate-200 pb-3 dark:border-slate-800">
        {Array.from({ length: columns }).map((_, index) => (
          <Skeleton key={index} className="h-3 flex-1" />
        ))}
      </div>
      <div className="space-y-3">
        {Array.from({ length: rows }).map((_, row) => (
          <div key={row} className="flex gap-4">
            {Array.from({ length: columns }).map((_, column) => (
              <Skeleton
                key={column}
                // The first column is usually a name and reads longer; the rest
                // are numbers and statuses.
                className={`h-4 ${column === 0 ? "flex-[2]" : "flex-1"}`}
              />
            ))}
          </div>
        ))}
      </div>
    </Loading>
  );
}

/** Stacked cards — an approval queue, a list of chains, a bill history. */
export function SkeletonList({
  rows = 3,
  label = "Loading",
}: {
  rows?: number;
  label?: string;
}) {
  return (
    <Loading label={label} className="space-y-4">
      {Array.from({ length: rows }).map((_, index) => (
        <div
          key={index}
          className="rounded-xl border border-slate-200 p-6 dark:border-slate-800"
        >
          <div className="flex items-start justify-between gap-4">
            <Skeleton className="h-4 w-1/3" />
            <Skeleton className="h-4 w-24" />
          </div>
          <div className="mt-4 space-y-2 border-t border-slate-200 pt-4 dark:border-slate-800">
            <Skeleton className="h-3 w-full" />
            <Skeleton className="h-3 w-4/5" />
          </div>
        </div>
      ))}
    </Loading>
  );
}

/** One bordered panel's worth of content, without the border. */
export function SkeletonPanel({
  label = "Loading",
  lines = 3,
}: {
  label?: string;
  lines?: number;
}) {
  return (
    <Loading label={label} className="space-y-4">
      <Skeleton className="h-4 w-40" />
      <div className="space-y-2">
        {Array.from({ length: lines }).map((_, index) => (
          <Skeleton key={index} className="h-3 w-full" />
        ))}
      </div>
    </Loading>
  );
}

/** A row of stat tiles, matching StatCard's footprint. */
export function SkeletonStats({
  count = 4,
  label = "Loading figures",
}: {
  count?: number;
  label?: string;
}) {
  return (
    <Loading
      label={label}
      className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4"
    >
      {Array.from({ length: count }).map((_, index) => (
        <div
          key={index}
          className="rounded-xl border border-slate-200 p-4 dark:border-slate-800"
        >
          <Skeleton className="h-3 w-20" />
          <Skeleton className="mt-3 h-7 w-16" />
        </div>
      ))}
    </Loading>
  );
}

/**
 * A vertical chain of steps — the approval progress strip.
 *
 * Its own shape because nothing else in the app looks like it: a dot, a rail,
 * and two lines of text per rung.
 */
export function SkeletonSteps({
  rows = 3,
  label = "Loading approval",
}: {
  rows?: number;
  label?: string;
}) {
  return (
    <Loading label={label} className="space-y-0">
      {Array.from({ length: rows }).map((_, index) => (
        <div key={index} className="flex gap-3">
          <div className="flex flex-col items-center">
            <Skeleton className="mt-1.5 size-2.5 shrink-0 rounded-full" />
            {index < rows - 1 && (
              <span className="w-px flex-1 bg-slate-200 dark:bg-slate-800" />
            )}
          </div>
          <div className={index === rows - 1 ? "flex-1" : "flex-1 pb-5"}>
            <Skeleton className="h-3.5 w-1/3" />
            <Skeleton className="mt-1.5 h-2.5 w-1/4" />
          </div>
        </div>
      ))}
    </Loading>
  );
}
