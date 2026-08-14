"use client";

import { useState } from "react";
import Link from "next/link";

import { Button } from "@/components/ui/Button";
import {
  ALL_STATUSES,
  InvoiceStatusBadge,
  statusLabel,
} from "@/components/invoices/InvoiceStatusBadge";
import {
  useDeleteInvoice,
  useOpenInvoiceFile,
  useRunMatching,
} from "@/hooks/invoice/useInvoices.hooks";
import type { Pagination } from "@/types/api.type";
import { TRANSIENT_STATUSES, type Invoice, type InvoiceStatus } from "@/types/invoice.type";

/** Statuses where matching is worth offering: extraction is done, no verdict yet. */
const MATCHABLE = new Set<InvoiceStatus>([
  "ocr_done",
  "no_match",
  "match_failed",
  "pending_review",
]);

interface Props {
  invoices: Invoice[];
  pagination: Pagination | null;
  /** First load — nothing to show yet. */
  loading: boolean;
  /** A background refetch with previous data still on screen. */
  refreshing?: boolean;
  /** Adds an "Uploaded by" column. */
  showUploader?: boolean;
  /** Enables the withdraw/delete action. */
  canDelete?: boolean;
  /** Shows "Match" and links each row through to the review screen. */
  showPipeline?: boolean;
  status: InvoiceStatus | "";
  onStatusChange: (status: InvoiceStatus | "") => void;
  onPageChange: (page: number) => void;
  onRefresh: () => void;
  emptyMessage: string;
}

function formatBytes(bytes: number | null): string {
  if (!bytes) return "—";
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function InvoiceTable({
  invoices,
  pagination,
  loading,
  refreshing = false,
  showUploader = false,
  canDelete = false,
  showPipeline = false,
  status,
  onStatusChange,
  onPageChange,
  onRefresh,
  emptyMessage,
}: Props) {
  const [confirmId, setConfirmId] = useState<string | null>(null);

  const openFile = useOpenInvoiceFile();
  const remove = useDeleteInvoice();
  const runMatching = useRunMatching();

  /** Which row is mid-request, so only that row's controls disable. */
  const busyId =
    (openFile.isPending ? openFile.variables : null) ??
    (remove.isPending ? remove.variables?.id : null) ??
    (runMatching.isPending ? runMatching.variables : null) ??
    null;

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 p-4 dark:border-slate-800">
          <p className="text-sm text-slate-600 dark:text-slate-400">
            {pagination
              ? `${pagination.total} invoice${pagination.total === 1 ? "" : "s"}`
              : loading
                ? "Loading…"
                : "—"}
          </p>

          <div className="flex items-center gap-2">
            <label className="flex items-center gap-2 text-sm">
              <span className="text-slate-600 dark:text-slate-400">Status</span>
              <select
                value={status}
                onChange={(e) => onStatusChange(e.target.value as InvoiceStatus | "")}
                className="rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm text-slate-900 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
              >
                <option value="">All</option>
                {ALL_STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {statusLabel(s)}
                  </option>
                ))}
              </select>
            </label>

            {/* Rows added by other people do not push themselves into an open
                table — there is no server push yet. */}
            <Button variant="ghost" onClick={onRefresh} disabled={refreshing}>
              {refreshing ? "Refreshing…" : "Refresh"}
            </Button>
          </div>
        </div>

        {loading ? (
          <p className="p-6 text-sm text-slate-600 dark:text-slate-400">Loading…</p>
        ) : invoices.length === 0 ? (
          <p className="p-6 text-sm text-slate-600 dark:text-slate-400">
            {emptyMessage}
          </p>
        ) : (
          <div
            // Dimmed while a background refetch is in flight, so the data is
            // visibly stale rather than silently so.
            className={`overflow-x-auto transition-opacity ${refreshing ? "opacity-60" : ""}`}
          >
            <table className="w-full min-w-[760px] text-left text-sm">
              <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500 dark:border-slate-800 dark:text-slate-400">
                <tr>
                  <th className="px-4 py-3 font-medium">File</th>
                  {showUploader && (
                    <th className="px-4 py-3 font-medium">Uploaded by</th>
                  )}
                  <th className="px-4 py-3 font-medium">Reference</th>
                  <th className="px-4 py-3 font-medium">Status</th>
                  <th className="px-4 py-3 font-medium">Uploaded</th>
                  <th className="px-4 py-3 text-right font-medium">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
                {invoices.map((invoice) => {
                  const busy = busyId === invoice.id;
                  const confirming = confirmId === invoice.id;

                  return (
                    <tr key={invoice.id}>
                      <td className="px-4 py-3">
                        <p className="max-w-[280px] truncate font-medium text-slate-900 dark:text-white">
                          {invoice.file_name}
                        </p>
                        <p className="text-xs text-slate-500 dark:text-slate-400">
                          {formatBytes(invoice.file_size_bytes)}
                          {invoice.page_count ? ` · ${invoice.page_count} pages` : ""}
                        </p>
                      </td>

                      {showUploader && (
                        <td className="px-4 py-3">
                          <p className="text-slate-900 dark:text-slate-100">
                            {invoice.uploader?.full_name?.trim() || "—"}
                          </p>
                          <p className="text-xs text-slate-500 dark:text-slate-400">
                            {invoice.uploader?.email ?? "deleted user"}
                          </p>
                        </td>
                      )}

                      <td className="px-4 py-3 text-slate-700 dark:text-slate-300">
                        {invoice.member_ref_no || invoice.extracted_invoice_no || "—"}
                      </td>

                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <InvoiceStatusBadge status={invoice.status} />
                          {/* A spinner beside a transient status is what tells
                              the user the page is live rather than stuck. */}
                          {TRANSIENT_STATUSES.has(invoice.status) && (
                            <span
                              aria-hidden="true"
                              className="h-3 w-3 animate-spin rounded-full border-2 border-slate-300 border-t-slate-600 dark:border-slate-700 dark:border-t-slate-300"
                            />
                          )}
                        </div>
                        {invoice.matched_po_name && (
                          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                            {invoice.matched_po_name}
                            {invoice.confidence_score != null &&
                              ` · ${Math.round(invoice.confidence_score)}%`}
                          </p>
                        )}
                      </td>

                      <td className="whitespace-nowrap px-4 py-3 text-slate-600 dark:text-slate-400">
                        {formatDate(invoice.created_at)}
                      </td>

                      <td className="px-4 py-3">
                        <div className="flex items-center justify-end gap-2">
                          {confirming ? (
                            <>
                              <span className="text-xs text-slate-600 dark:text-slate-400">
                                Delete?
                              </span>
                              <Button
                                variant="danger"
                                disabled={busy}
                                isLoading={busy}
                                onClick={() =>
                                  remove.mutate(invoice, {
                                    onSuccess: () => setConfirmId(null),
                                  })
                                }
                              >
                                Yes
                              </Button>
                              <Button
                                variant="ghost"
                                disabled={busy}
                                onClick={() => setConfirmId(null)}
                              >
                                No
                              </Button>
                            </>
                          ) : (
                            <>
                              {showPipeline && MATCHABLE.has(invoice.status) && (
                                <Button
                                  disabled={busy}
                                  isLoading={busy}
                                  onClick={() => runMatching.mutate(invoice.id)}
                                >
                                  {invoice.matched_po_name ? "Re-match" : "Match"}
                                </Button>
                              )}
                              {showPipeline && (
                                <Link
                                  href={`/admin/invoices/${invoice.id}`}
                                  className="rounded-lg px-3 py-2 text-sm font-medium text-slate-700 underline underline-offset-4 hover:text-slate-900 dark:text-slate-300 dark:hover:text-white"
                                >
                                  Review
                                </Link>
                              )}
                              <Button
                                variant="secondary"
                                disabled={busy}
                                isLoading={busy}
                                onClick={() => openFile.mutate(invoice.id)}
                              >
                                View
                              </Button>
                              {canDelete && (
                                <Button
                                  variant="ghost"
                                  disabled={busy}
                                  onClick={() => setConfirmId(invoice.id)}
                                >
                                  Delete
                                </Button>
                              )}
                            </>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {pagination && pagination.pages > 1 && (
          <div className="flex items-center justify-between gap-3 border-t border-slate-200 p-4 dark:border-slate-800">
            <Button
              variant="secondary"
              disabled={pagination.page <= 1}
              onClick={() => onPageChange(pagination.page - 1)}
            >
              Previous
            </Button>
            <span className="text-sm text-slate-600 dark:text-slate-400">
              Page {pagination.page} of {pagination.pages}
            </span>
            <Button
              variant="secondary"
              disabled={pagination.page >= pagination.pages}
              onClick={() => onPageChange(pagination.page + 1)}
            >
              Next
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
