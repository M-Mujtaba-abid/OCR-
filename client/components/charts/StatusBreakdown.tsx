"use client";

import { statusLabel } from "@/components/invoices/InvoiceStatusBadge";
import { STATUS_ORDER, stageOf } from "@/lib/pipeline";
import type { InvoiceStatus } from "@/types/invoice.type";

/**
 * Every status, with its share — the table the chart summarises.
 *
 * Fourteen classes is past the point where more colour distinguishes anything,
 * so this is a table with a proportional bar rather than another chart. It is
 * also the accessible counterpart to the pipeline bar: the same numbers as
 * text, which is what makes an amber segment that sits below 3:1 on white
 * legitimate rather than a compromise.
 *
 * Zero rows stay visible. "None failed" and "failures aren't shown here" look
 * identical if empty rows are dropped, and only one of them is reassuring.
 */
export function StatusBreakdown({
  byStatus,
}: {
  byStatus: Record<InvoiceStatus, number> | undefined;
}) {
  if (!byStatus) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 6 }, (_, i) => (
          <div key={i} className="h-6 animate-pulse rounded bg-slate-100 dark:bg-slate-800" />
        ))}
      </div>
    );
  }

  const rows = STATUS_ORDER.map((status) => ({
    status,
    count: byStatus[status] ?? 0,
    stage: stageOf(status),
  }));
  // Scaled to the largest row, not the total: with one status holding most of
  // the queue every other bar would be a sliver, and the comparison people
  // want here is between statuses.
  const peak = Math.max(1, ...rows.map((row) => row.count));

  return (
    <table className="w-full text-left text-sm">
      <caption className="sr-only">Invoice count by status</caption>
      <tbody className="divide-y divide-slate-100 dark:divide-slate-800/60">
        {rows.map(({ status, count, stage }) => (
          <tr key={status} className={count === 0 ? "opacity-45" : undefined}>
            <td className="py-1.5 pr-3 w-0">
              <span
                aria-hidden="true"
                className={`block h-2.5 w-2.5 rounded-sm ${stage.fill}`}
              />
            </td>
            <td className="py-1.5 pr-3 whitespace-nowrap text-slate-700 dark:text-slate-300">
              {statusLabel(status)}
            </td>
            <td className="w-full py-1.5 pr-3">
              <div className="h-1.5 w-full rounded-full bg-slate-100 dark:bg-slate-800">
                <div
                  className={`h-full rounded-full ${stage.fill}`}
                  style={{ width: `${(count / peak) * 100}%` }}
                />
              </div>
            </td>
            <td className="py-1.5 text-right tabular-nums font-medium text-slate-900 dark:text-white">
              {count}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
