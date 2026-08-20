"use client";

import { useState } from "react";
import Link from "next/link";

import { Button } from "@/components/ui/Button";
import { RefreshButton } from "@/components/ui/RefreshButton";
import { SkeletonTable } from "@/components/ui/Skeleton";
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

function uploaderName(invoice: Invoice): string {
  return (
    invoice.uploader?.full_name?.trim() ||
    invoice.uploader?.email ||
    "a deleted user"
  );
}

/**
 * How much to trust the match, said in colour as well as in digits.
 *
 * The number alone is the same size and weight whether it reads 98 or 51, so a
 * row nobody should accept looks exactly like one nobody needs to check.
 */
function confidenceClass(score: number): string {
  if (score >= 90) return "text-emerald-700 dark:text-emerald-400";
  if (score >= 75) return "text-amber-700 dark:text-amber-400";
  return "text-slate-500 dark:text-slate-400";
}

/**
 * The invoice list.
 *
 * Fits its container rather than scrolling sideways. A table wide enough to
 * need a horizontal scrollbar hides its own right-hand columns — the actions,
 * here — behind a gesture nobody makes, and the row a person came to act on is
 * the part they cannot see.
 *
 * So the layout is fixed-width and the columns give way in order of how little
 * they are needed: uploader first, then reference, then the timestamp. None of
 * it is lost — each one folds into the file cell at the width where its column
 * goes, which is also where a phone-shaped screen wants it anyway.
 */
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

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 p-4 dark:border-slate-800">
          <div className="min-w-0">
            <p className="text-sm font-medium text-slate-900 dark:text-white">
              {pagination
                ? `${pagination.total} invoice${pagination.total === 1 ? "" : "s"}`
                : loading
                  ? "Loading…"
                  : "—"}
            </p>
            {status && (
              <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                Filtered to {statusLabel(status).toLowerCase()}
              </p>
            )}
          </div>

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
            <RefreshButton
              onRefresh={onRefresh}
              refreshing={refreshing}
              what="invoices"
              size="sm"
            />
          </div>
        </div>

        {loading ? (
          <SkeletonTable rows={6} columns={5} label="Loading invoices" />
        ) : invoices.length === 0 ? (
          <p className="p-6 text-sm text-slate-600 dark:text-slate-400">
            {emptyMessage}
          </p>
        ) : (
          <div
            // Dimmed while a background refetch is in flight, so the data is
            // visibly stale rather than silently so.
            className={`transition-opacity ${refreshing ? "opacity-60" : ""}`}
          >
            {/* table-fixed, so a long file name compresses its own column
                instead of widening the table and pushing the actions off the
                right-hand edge. */}
            <table className="w-full table-fixed text-left text-sm">
              <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500 dark:border-slate-800 dark:text-slate-400">
                {/* The widths sum to 100 with every column showing. As each
                    one drops out the browser shares its space across what is
                    left, so the table stays full-width at every breakpoint
                    without a second set of numbers to keep in step. */}
                <tr>
                  <th className="w-[26%] px-4 py-3 font-medium">File</th>
                  {showUploader && (
                    <th className="hidden w-[14%] px-4 py-3 font-medium xl:table-cell">
                      Uploaded by
                    </th>
                  )}
                  <th className="hidden w-[11%] px-4 py-3 font-medium lg:table-cell">
                    Reference
                  </th>
                  <th className="w-[17%] px-4 py-3 font-medium">Status</th>
                  <th className="hidden w-[10%] px-4 py-3 font-medium md:table-cell">
                    Uploaded
                  </th>
                  <th className="w-[22%] px-4 py-3 text-right font-medium">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
                {invoices.map((invoice) => (
                  <InvoiceRow
                    key={invoice.id}
                    invoice={invoice}
                    showUploader={showUploader}
                    canDelete={canDelete}
                    showPipeline={showPipeline}
                    confirming={confirmId === invoice.id}
                    onConfirm={setConfirmId}
                  />
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

/**
 * One row.
 *
 * Split out of the table so each row owns the mutations it fires. The table
 * held all three for the whole list and worked out which row was busy by
 * comparing each mutation's `variables` against the row's id — correct, but it
 * meant "is this row busy" was assembled from three optional reads at the top
 * of the table rather than simply asked here. It also re-rendered every row on
 * every state change in any of them.
 */
function InvoiceRow({
  invoice,
  showUploader,
  canDelete,
  showPipeline,
  confirming,
  onConfirm,
}: {
  invoice: Invoice;
  showUploader: boolean;
  canDelete: boolean;
  showPipeline: boolean;
  confirming: boolean;
  onConfirm: (id: string | null) => void;
}) {
  const openFile = useOpenInvoiceFile();
  const remove = useDeleteInvoice();
  const runMatching = useRunMatching();

  const busy = openFile.isPending || remove.isPending || runMatching.isPending;
  const working = TRANSIENT_STATUSES.has(invoice.status);
  const reference = invoice.member_ref_no || invoice.extracted_invoice_no || "—";

  return (
    <tr className="align-top transition-colors hover:bg-slate-50 dark:hover:bg-slate-800/40">
      <td className="px-4 py-3">
        <p
          className="truncate font-medium text-slate-900 dark:text-white"
          title={invoice.file_name}
        >
          {invoice.file_name}
        </p>
        <p className="truncate text-xs text-slate-500 dark:text-slate-400">
          {formatBytes(invoice.file_size_bytes)}
          {invoice.page_count
            ? ` · ${invoice.page_count} page${invoice.page_count === 1 ? "" : "s"}`
            : ""}
        </p>

        {/* Everything whose own column has given way at this width. Shown
            here rather than dropped: a narrow screen is not a reason to know
            less about a row. */}
        {showUploader && (
          <p className="truncate text-xs text-slate-500 dark:text-slate-400 xl:hidden">
            {uploaderName(invoice)}
          </p>
        )}
        <p className="truncate text-xs text-slate-500 dark:text-slate-400 lg:hidden">
          Ref {reference}
        </p>
        <p className="truncate text-xs text-slate-500 dark:text-slate-400 md:hidden">
          {formatDate(invoice.created_at)}
        </p>
      </td>

      {showUploader && (
        <td className="hidden px-4 py-3 xl:table-cell">
          <p className="truncate text-slate-900 dark:text-slate-100">
            {invoice.uploader?.full_name?.trim() || "—"}
          </p>
          <p
            className="truncate text-xs text-slate-500 dark:text-slate-400"
            title={invoice.uploader?.email}
          >
            {invoice.uploader?.email ?? "deleted user"}
          </p>
        </td>
      )}

      <td className="hidden px-4 py-3 lg:table-cell">
        <p
          className="truncate text-slate-700 dark:text-slate-300"
          title={reference}
        >
          {reference}
        </p>
      </td>

      <td className="px-4 py-3">
        <div className="flex items-center gap-2">
          <InvoiceStatusBadge status={invoice.status} dot />
          {/* A spinner beside a transient status is what tells the user the
              page is live rather than stuck. */}
          {working && (
            <span
              aria-hidden="true"
              className="h-3 w-3 shrink-0 animate-spin rounded-full border-2 border-slate-300 border-t-slate-600 dark:border-slate-700 dark:border-t-slate-300"
            />
          )}
        </div>
        {invoice.matched_po_name && (
          <p
            className="mt-1 truncate text-xs text-slate-500 dark:text-slate-400"
            title={`Matched to ${invoice.matched_po_name}`}
          >
            {invoice.matched_po_name}
            {invoice.confidence_score != null && (
              <span
                className={`ml-1 font-medium ${confidenceClass(invoice.confidence_score)}`}
              >
                {Math.round(invoice.confidence_score)}%
              </span>
            )}
          </p>
        )}
      </td>

      <td className="hidden px-4 py-3 text-slate-600 dark:text-slate-400 md:table-cell">
        <span className="whitespace-nowrap">{formatDate(invoice.created_at)}</span>
      </td>

      <td className="px-4 py-3">
        <div className="flex flex-wrap items-center justify-end gap-1.5">
          {confirming ? (
            <>
              <span className="text-xs text-slate-600 dark:text-slate-400">
                Delete?
              </span>
              <Button
                size="sm"
                variant="danger"
                disabled={busy}
                isLoading={remove.isPending}
                onClick={() =>
                  remove.mutate(invoice, { onSuccess: () => onConfirm(null) })
                }
              >
                Yes
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={busy}
                onClick={() => onConfirm(null)}
              >
                No
              </Button>
            </>
          ) : (
            <>
              {showPipeline && MATCHABLE.has(invoice.status) && (
                <Button
                  size="sm"
                  disabled={busy}
                  isLoading={runMatching.isPending}
                  onClick={() => runMatching.mutate(invoice.id)}
                >
                  {invoice.matched_po_name ? "Re-match" : "Match"}
                </Button>
              )}
              {showPipeline && (
                <Link
                  href={`/admin/invoices/${invoice.id}`}
                  className="whitespace-nowrap rounded-lg px-2.5 py-1.5 text-xs font-medium text-slate-700 underline underline-offset-4 hover:text-slate-900 dark:text-slate-300 dark:hover:text-white"
                >
                  Review
                </Link>
              )}
              <Button
                size="sm"
                variant="secondary"
                disabled={busy}
                isLoading={openFile.isPending}
                onClick={() => openFile.mutate(invoice.id)}
              >
                View
              </Button>
              {canDelete && (
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={busy}
                  onClick={() => onConfirm(invoice.id)}
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
}
