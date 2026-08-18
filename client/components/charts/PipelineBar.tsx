"use client";

import { STAGES, stageCount, type Stage } from "@/lib/pipeline";
import type { InvoiceStatus } from "@/types/invoice.type";

/**
 * The whole queue as one bar.
 *
 * Part-to-whole, laid out horizontally because the category names are words
 * rather than codes. One bar rather than five cards is the point: cards invite
 * reading each number alone, and the question this answers — "how much of the
 * queue needs me?" — is about proportion.
 *
 * Every invoice is in exactly one segment (enforced by the type check in
 * `lib/pipeline.ts`), so the segments always sum to the total. A dashboard that
 * quietly drops rows is worse than one that shows none.
 */
export function PipelineBar({
  byStatus,
  total,
}: {
  byStatus: Record<InvoiceStatus, number> | undefined;
  total: number | undefined;
}) {
  const counts = STAGES.map((stage) => ({
    stage,
    count: stageCount(stage, byStatus),
  }));
  const sum = counts.reduce((n, entry) => n + entry.count, 0);

  if (byStatus === undefined) {
    return <div className="h-3 w-full animate-pulse rounded-full bg-slate-200 dark:bg-slate-800" />;
  }

  if (sum === 0) {
    return (
      <div>
        <div className="h-3 w-full rounded-full bg-slate-100 dark:bg-slate-800" />
        <p className="mt-3 text-sm text-slate-600 dark:text-slate-400">
          Nothing in the pipeline yet — uploaded invoices will appear here.
        </p>
      </div>
    );
  }

  const shown = counts.filter((entry) => entry.count > 0);

  return (
    <div>
      {/* gap-0.5 is the 2px surface gap between adjacent fills: it keeps two
          segments from reading as one long block, and does the separating work
          that colour alone cannot for a red/amber pair. */}
      <div className="flex h-3 w-full gap-0.5 overflow-hidden rounded-full">
        {shown.map(({ stage, count }) => (
          <div
            key={stage.id}
            className={`h-full ${stage.fill} first:rounded-l-full last:rounded-r-full`}
            style={{ width: `${(count / sum) * 100}%` }}
            title={`${stage.label}: ${count} of ${sum} — ${stage.hint}`}
          />
        ))}
      </div>

      {/* The legend is not optional. Two of these colours sit below 3:1 on a
          light surface, and the label is what carries the meaning there. */}
      <ul className="mt-4 grid gap-x-6 gap-y-3 sm:grid-cols-2 lg:grid-cols-3">
        {counts.map(({ stage, count }) => (
          <LegendRow key={stage.id} stage={stage} count={count} total={sum} />
        ))}
      </ul>

      {total !== undefined && total !== sum && (
        <p className="mt-3 text-xs text-red-600 dark:text-red-400">
          {sum} of {total} invoices are accounted for above — please report this.
        </p>
      )}
    </div>
  );
}

function LegendRow({
  stage,
  count,
  total,
}: {
  stage: Stage;
  count: number;
  total: number;
}) {
  const share = total ? Math.round((count / total) * 100) : 0;

  return (
    <li className="flex items-baseline gap-2">
      <span
        aria-hidden="true"
        className={`mt-1 h-2.5 w-2.5 shrink-0 rounded-sm ${stage.fill}`}
      />
      <span className="min-w-0 flex-1">
        <span className="block text-sm text-slate-700 dark:text-slate-300">
          {stage.label}
        </span>
        <span className="block text-xs text-slate-500 dark:text-slate-400">
          {stage.hint}
        </span>
      </span>
      <span className="tabular-nums text-sm font-semibold text-slate-900 dark:text-white">
        {count}
      </span>
      <span className="w-9 text-right text-xs tabular-nums text-slate-500 dark:text-slate-400">
        {share}%
      </span>
    </li>
  );
}
