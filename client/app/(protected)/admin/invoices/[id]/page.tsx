"use client";

import { useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { InvoiceStatusBadge } from "@/components/invoices/InvoiceStatusBadge";
import { MatchCandidates } from "@/components/invoices/MatchCandidates";
import { Alert } from "@/components/ui/Alert";
import { Badge, Field } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import {
  useConfirmMatch,
  useInvoice,
  useOpenInvoiceFile,
  useRejectInvoice,
  useRunMatching,
  useRunOcr,
} from "@/hooks/invoice/useInvoices.hooks";
import { TRANSIENT_STATUSES } from "@/types/invoice.type";

function money(value: number | null | undefined, currency?: string | null): string {
  if (value == null) return "—";
  return `${value.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}${currency ? ` ${currency}` : ""}`;
}

export default function InvoiceReviewPage() {
  const { id } = useParams<{ id: string }>();
  const { data: invoice, isLoading, isError } = useInvoice(id);

  const openFile = useOpenInvoiceFile();
  const runOcr = useRunOcr();
  const runMatching = useRunMatching();
  const confirm = useConfirmMatch();
  const reject = useRejectInvoice();

  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");

  if (isLoading) {
    return <p className="text-sm text-slate-600 dark:text-slate-400">Loading…</p>;
  }
  if (isError || !invoice) {
    return <Alert>That invoice could not be loaded.</Alert>;
  }

  const extracted = invoice.extracted_json;
  const working = TRANSIENT_STATUSES.has(invoice.status);
  const busy =
    working || runOcr.isPending || runMatching.isPending || confirm.isPending;

  return (
    <div className="space-y-6">
      <div>
        <Link
          href="/admin"
          className="text-sm text-slate-600 underline underline-offset-4 hover:text-slate-900 dark:text-slate-400 dark:hover:text-white"
        >
          ← Back to the queue
        </Link>
      </div>

      {/* ------------------------------------------------------------ header */}
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="truncate text-2xl font-semibold tracking-tight text-slate-900 dark:text-white">
            {invoice.file_name}
          </h1>
          <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
            Uploaded by {invoice.uploader?.full_name?.trim() || invoice.uploader?.email || "a deleted user"}
            {" · "}
            {new Date(invoice.created_at).toLocaleString()}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <InvoiceStatusBadge status={invoice.status} />
          {working && (
            <span
              aria-hidden="true"
              className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-slate-600 dark:border-slate-700 dark:border-t-slate-300"
            />
          )}
          <Button
            variant="secondary"
            onClick={() => openFile.mutate(invoice.id)}
            isLoading={openFile.isPending}
          >
            Open PDF
          </Button>
        </div>
      </header>

      {invoice.ocr_error && (
        <Alert>
          <span className="font-medium">Extraction failed.</span> {invoice.ocr_error}
        </Alert>
      )}
      {invoice.rejection_reason && (
        <Alert>
          <span className="font-medium">Rejected.</span> {invoice.rejection_reason}
        </Alert>
      )}

      {/* ------------------------------------------------------------ actions */}
      <div className="flex flex-wrap gap-3">
        <Button
          variant="secondary"
          disabled={busy}
          isLoading={runOcr.isPending}
          onClick={() => runOcr.mutate(invoice.id)}
        >
          {extracted ? "Re-read document" : "Read document"}
        </Button>
        <Button
          disabled={busy || !extracted}
          isLoading={runMatching.isPending}
          onClick={() => runMatching.mutate(invoice.id)}
        >
          {invoice.candidates ? "Re-run matching" : "Run matching"}
        </Button>
        {!extracted && (
          <p className="self-center text-sm text-slate-600 dark:text-slate-400">
            Matching needs the document to be read first.
          </p>
        )}
      </div>

      {/* --------------------------------------------------------- extraction */}
      <section className="rounded-xl border border-slate-200 bg-white p-6 dark:border-slate-800 dark:bg-slate-900">
        <h2 className="text-sm font-semibold text-slate-900 dark:text-white">
          What the document says
        </h2>

        {!extracted ? (
          <p className="mt-3 text-sm text-slate-600 dark:text-slate-400">
            {working
              ? "Reading…"
              : "Not read yet. Use “Read document” above."}
          </p>
        ) : (
          <>
            <dl className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              <Field label="Vendor" value={extracted.vendor_name ?? "—"} />
              <Field label="Reference" value={extracted.po_number ?? "—"} />
              <Field label="Date" value={extracted.order_date ?? "—"} />
              <Field label="Email" value={extracted.vendor_email ?? "—"} />
              <Field
                label="Untaxed"
                value={money(extracted.untaxed_amount, extracted.currency)}
              />
              <Field
                label="Tax"
                value={money(extracted.tax_amount, extracted.currency)}
              />
              <Field
                label="Total"
                value={
                  <span className="font-semibold">
                    {money(extracted.total_amount, extracted.currency)}
                  </span>
                }
              />
              <Field label="Pages" value={invoice.page_count ?? "—"} />
              <Field
                label="Read by"
                value={
                  invoice.ocr_model ? (
                    <Badge>{invoice.ocr_model}</Badge>
                  ) : (
                    "—"
                  )
                }
              />
            </dl>

            {extracted.vendor_address && (
              <p className="mt-4 text-sm text-slate-600 dark:text-slate-400">
                {extracted.vendor_address}
              </p>
            )}

            {invoice.lines.length > 0 && (
              <div className="mt-6 overflow-x-auto">
                <table className="w-full min-w-[520px] text-left text-sm">
                  <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500 dark:border-slate-800 dark:text-slate-400">
                    <tr>
                      <th className="py-2 pr-4 font-medium">#</th>
                      <th className="py-2 pr-4 font-medium">Description</th>
                      <th className="py-2 pr-4 font-medium">Code</th>
                      <th className="py-2 pr-4 text-right font-medium">Qty</th>
                      <th className="py-2 pr-4 font-medium">UoM</th>
                      <th className="py-2 pr-4 text-right font-medium">Unit</th>
                      <th className="py-2 text-right font-medium">Subtotal</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
                    {invoice.lines.map((line) => (
                      <tr key={line.id}>
                        <td className="py-2 pr-4 text-slate-500">{line.line_no}</td>
                        <td className="py-2 pr-4 text-slate-900 dark:text-slate-100">
                          {line.raw_description}
                        </td>
                        <td className="py-2 pr-4 font-mono text-xs text-slate-600 dark:text-slate-400">
                          {line.raw_product_code ?? "—"}
                        </td>
                        <td className="py-2 pr-4 text-right tabular-nums">
                          {line.quantity ?? "—"}
                        </td>
                        <td className="py-2 pr-4 text-slate-600 dark:text-slate-400">
                          {line.uom ?? "—"}
                        </td>
                        <td className="py-2 pr-4 text-right tabular-nums">
                          {money(line.unit_price)}
                        </td>
                        <td className="py-2 text-right tabular-nums">
                          {money(line.amount)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </section>

      {/* ---------------------------------------------------------- candidates */}
      <MatchCandidates
        invoice={invoice}
        onConfirm={(poId) => confirm.mutate({ invoiceId: invoice.id, poId })}
        confirming={confirm.isPending}
        disabled={busy}
      />

      {/* -------------------------------------------------------------- reject */}
      <section className="rounded-xl border border-slate-200 bg-white p-6 dark:border-slate-800 dark:bg-slate-900">
        <h2 className="text-sm font-semibold text-slate-900 dark:text-white">
          Reject this invoice
        </h2>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
          The uploader is notified with the reason you give.
        </p>

        {!rejecting ? (
          <div className="mt-4">
            <Button variant="secondary" onClick={() => setRejecting(true)}>
              Reject…
            </Button>
          </div>
        ) : (
          <div className="mt-4 space-y-3">
            <textarea
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              rows={3}
              maxLength={2000}
              placeholder="Why is this invoice being rejected?"
              className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 placeholder:text-slate-400 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
            />
            <div className="flex gap-3">
              <Button
                variant="danger"
                disabled={!reason.trim() || reject.isPending}
                isLoading={reject.isPending}
                onClick={() =>
                  reject.mutate(
                    { invoiceId: invoice.id, reason: reason.trim() },
                    { onSuccess: () => setRejecting(false) },
                  )
                }
              >
                Confirm rejection
              </Button>
              <Button variant="ghost" onClick={() => setRejecting(false)}>
                Cancel
              </Button>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
