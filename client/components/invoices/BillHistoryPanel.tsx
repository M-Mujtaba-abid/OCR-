"use client";

import { useState } from "react";
import Link from "next/link";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { useBillHistory } from "@/hooks/invoice/useInvoices.hooks";
import { PAGE_SIZE } from "@/lib/env";
import { money } from "@/lib/format";
import type { BillHistoryItem } from "@/types/invoice.type";

/** Below this, a difference between two totals is rounding, not a disagreement. */
const EPSILON = 0.01;

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function person(who: BillHistoryItem["reviewer"]): string {
  return who?.full_name?.trim() || who?.email || "a deleted user";
}

/**
 * Every vendor bill this system has raised in Odoo.
 *
 * The record, not a live view. Each row is rendered from what was written at
 * creation time — reference, amount, order, receipt, who approved it — so a
 * page costs one query rather than one Odoo round trip per row, and the
 * history still answers when Odoo is unreachable. For what a bill looks like
 * *now*, the link opens Odoo, which is the system of record for that question.
 *
 * The invoice's own total is shown beside the bill's whenever the two
 * disagree. A bill raised at the ORDER's prices against an invoice charging
 * something else is legitimate and common — and it is also exactly the thing
 * somebody opens a history to find.
 */
export function BillHistoryPanel() {
  const [page, setPage] = useState(1);
  const query = useBillHistory({ page, pageSize: PAGE_SIZE });

  const items = query.data?.items ?? [];
  const pagination = query.data?.pagination ?? null;

  return (
    <div className="rounded-xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 p-4 dark:border-slate-800">
        <div>
          <p className="text-sm font-medium text-slate-900 dark:text-white">
            {pagination
              ? `${pagination.total} bill${pagination.total === 1 ? "" : "s"} created`
              : query.isLoading
                ? "Loading…"
                : "—"}
          </p>
          <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
            Raised in Odoo as drafts. Each one still needs posting there.
          </p>
        </div>
        <Button
          variant="ghost"
          onClick={() => void query.refetch()}
          disabled={query.isFetching}
        >
          {query.isFetching ? "Refreshing…" : "Refresh"}
        </Button>
      </div>

      {query.isLoading ? (
        <p className="p-6 text-sm text-slate-600 dark:text-slate-400">Loading…</p>
      ) : query.isError ? (
        <p className="p-6 text-sm text-red-700 dark:text-red-400">
          The bill history could not be loaded.
        </p>
      ) : items.length === 0 ? (
        <p className="p-6 text-sm text-slate-600 dark:text-slate-400">
          Nothing has been billed yet. A bill appears here the moment one is
          created from a matched invoice.
        </p>
      ) : (
        <div
          // Dimmed while a background refetch is in flight, so the data is
          // visibly stale rather than silently so.
          className={`overflow-x-auto transition-opacity ${
            query.isFetching ? "opacity-60" : ""
          }`}
        >
          <table className="w-full min-w-[900px] text-left text-sm">
            <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500 dark:border-slate-800 dark:text-slate-400">
              <tr>
                <th className="px-4 py-3 font-medium">Bill</th>
                <th className="px-4 py-3 font-medium">Invoice</th>
                <th className="px-4 py-3 font-medium">Order</th>
                <th className="px-4 py-3 text-right font-medium">Amount</th>
                <th className="px-4 py-3 font-medium">Billed</th>
                <th className="px-4 py-3 text-right font-medium">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
              {items.map((item) => (
                <BillRow key={item.invoice_id} item={item} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {pagination && pagination.pages > 1 && (
        <div className="flex items-center justify-between gap-3 border-t border-slate-200 p-4 dark:border-slate-800">
          <Button
            variant="secondary"
            disabled={pagination.page <= 1}
            onClick={() => setPage(pagination.page - 1)}
          >
            Previous
          </Button>
          <span className="text-sm text-slate-600 dark:text-slate-400">
            Page {pagination.page} of {pagination.pages}
          </span>
          <Button
            variant="secondary"
            disabled={pagination.page >= pagination.pages}
            onClick={() => setPage(pagination.page + 1)}
          >
            Next
          </Button>
        </div>
      )}
    </div>
  );
}

function BillRow({ item }: { item: BillHistoryItem }) {
  const label = item.bill_ref ?? (item.bill_id ? `#${item.bill_id}` : "—");

  // Both figures known, and they differ by more than rounding. Worth saying:
  // the bill was raised at the order's prices and the invoice asked for
  // something else.
  const mismatch =
    item.bill_amount != null &&
    item.invoice_total != null &&
    Math.abs(item.bill_amount - item.invoice_total) > EPSILON;

  return (
    <tr>
      <td className="px-4 py-3 align-top">
        <p className="font-medium text-slate-900 dark:text-white">{label}</p>
        <p className="text-xs text-slate-500 dark:text-slate-400">
          {formatDate(item.bill_date)}
          {item.bill_id ? ` · #${item.bill_id}` : ""}
        </p>
        {/* Draft is the whole point — nothing is owed until somebody posts it
            in Odoo — so it is stated on every row rather than assumed. */}
        <div className="mt-1 flex flex-wrap gap-1">
          <Badge tone="neutral">Draft</Badge>
          {item.attachment_status !== "attached" && (
            <Badge tone="warning">Scan not attached</Badge>
          )}
        </div>
      </td>

      <td className="px-4 py-3 align-top">
        <p className="max-w-[220px] truncate text-slate-900 dark:text-slate-100">
          {item.file_name}
        </p>
        <p className="text-xs text-slate-500 dark:text-slate-400">
          {item.vendor ?? "unnamed vendor"}
          {item.invoice_no ? ` · ${item.invoice_no}` : ""}
        </p>
        {item.member_ref_no && (
          <p className="text-xs text-slate-500 dark:text-slate-400">
            ref {item.member_ref_no}
          </p>
        )}
        <p className="text-xs text-slate-500 dark:text-slate-400">
          uploaded by {person(item.uploader)}
        </p>
      </td>

      <td className="px-4 py-3 align-top">
        {item.po_url && item.po_name ? (
          <a
            href={item.po_url}
            target="_blank"
            rel="noreferrer"
            className="font-medium text-indigo-700 underline underline-offset-4 dark:text-indigo-300"
          >
            {item.po_name} ↗
          </a>
        ) : (
          <p className="text-slate-900 dark:text-slate-100">
            {item.po_name ?? "—"}
          </p>
        )}
        <p className="text-xs text-slate-500 dark:text-slate-400">
          {item.line_count > 0
            ? `${item.line_count} line${item.line_count === 1 ? "" : "s"}`
            : "line detail not recorded"}
        </p>
        {item.receipt_name && (
          <p className="text-xs text-slate-500 dark:text-slate-400">
            received on {item.receipt_name}
          </p>
        )}
        {item.backorder_names.length > 0 && (
          <p className="text-xs text-amber-700 dark:text-amber-400">
            {item.backorder_names.join(", ")} left to come
          </p>
        )}
        {item.was_corrected && (
          <div className="mt-1">
            {/* The matcher suggested a different order and a person overruled
                it. The most useful signal this table carries. */}
            <Badge tone="accent">Match corrected</Badge>
          </div>
        )}
      </td>

      <td className="px-4 py-3 text-right align-top tabular-nums">
        <p className="font-medium text-slate-900 dark:text-white">
          {money(item.bill_amount, item.currency)}
        </p>
        {mismatch && (
          <p
            className="text-xs text-amber-700 dark:text-amber-400"
            title="The bill was raised at the order's prices. The invoice asked for a different amount."
          >
            invoice {money(item.invoice_total, item.currency)}
          </p>
        )}
      </td>

      <td className="whitespace-nowrap px-4 py-3 align-top">
        <p className="text-slate-700 dark:text-slate-300">
          {formatDateTime(item.billed_at)}
        </p>
        <p className="text-xs text-slate-500 dark:text-slate-400">
          by {person(item.reviewer)}
        </p>
      </td>

      <td className="px-4 py-3 align-top">
        <div className="flex items-center justify-end gap-3">
          {/* Empty when Odoo has no base URL configured. A dead link is worse
              than no link — the reference above is still enough to find it. */}
          {item.bill_url && (
            <a
              href={item.bill_url}
              target="_blank"
              rel="noreferrer"
              className="whitespace-nowrap rounded-lg px-3 py-2 text-sm font-medium text-indigo-700 underline underline-offset-4 hover:text-indigo-900 dark:text-indigo-300 dark:hover:text-indigo-200"
            >
              Open in Odoo ↗
            </a>
          )}
          <Link
            href={`/admin/invoices/${item.invoice_id}`}
            className="rounded-lg px-3 py-2 text-sm font-medium text-slate-700 underline underline-offset-4 hover:text-slate-900 dark:text-slate-300 dark:hover:text-white"
          >
            Review
          </Link>
        </div>
      </td>
    </tr>
  );
}
