"use client";

import { useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { CreatePurchaseOrder } from "@/components/invoices/CreatePurchaseOrder";
import { CreateVendorBill } from "@/components/invoices/CreateVendorBill";
import { InvoiceStatusBadge } from "@/components/invoices/InvoiceStatusBadge";
import { MatchCandidates } from "@/components/invoices/MatchCandidates";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { useAuth } from "@/hooks/auth/useAuth.hooks";
import {
  useConfirmMatch,
  useInvoice,
  useOpenInvoiceFile,
  useRejectInvoice,
  useRunMatching,
  useRunOcr,
} from "@/hooks/invoice/useInvoices.hooks";
import { money } from "@/lib/format";
import {
  TRANSIENT_STATUSES,
  type InvoiceDetail,
  type InvoiceLine,
} from "@/types/invoice.type";

/** Tax for each line, in the order the lines were given. */
interface LineTax {
  value: number;
  /** True when the invoice taxed the total, not this line, and we spread it. */
  allocated: boolean;
}

/**
 * What a reviewer sees in place of the billing panel.
 *
 * Only for somebody without `invoice.bill` — a manager. Silence would read as
 * a missing feature; this says the work is done and who does the next part.
 */
function ReadyForBilling({ invoice }: { invoice: InvoiceDetail }) {
  if (invoice.status === "pushed" || invoice.pushed_to_odoo) {
    return (
      <section className="rounded-xl border border-emerald-200 bg-emerald-50/50 p-6 text-sm dark:border-emerald-900 dark:bg-emerald-950/30">
        <p className="font-medium text-emerald-900 dark:text-emerald-200">
          Billed in Odoo{invoice.odoo_bill_ref ? `: ${invoice.odoo_bill_ref}` : ""}.
        </p>
      </section>
    );
  }

  const matched = (invoice.final_po_id ?? invoice.matched_po_id) != null;

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-6 dark:border-slate-800 dark:bg-slate-900">
      <h2 className="text-sm font-semibold text-slate-900 dark:text-white">
        Billing
      </h2>
      <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
        {matched
          ? `Confirmed against ${invoice.matched_po_name ?? "a purchase order"}. An administrator raises the vendor bill in Odoo — billing is kept separate from review.`
          : "Once this invoice is matched and confirmed, an administrator raises the vendor bill in Odoo."}
      </p>
    </section>
  );
}

/**
 * Tax per line, printed where the document prints it and allocated where it
 * does not.
 *
 * Most invoices state tax once, at the bottom, so a column showing only what
 * was printed per line would read 0.00 on nearly every document — and a
 * reviewer comparing a line against a purchase order line, which in Odoo
 * always carries its own tax, would have nothing to compare. The allocation is
 * by share of line value, and every allocated figure is marked as such: it is
 * this screen's arithmetic, not something the vendor wrote.
 */
function taxPerLine(lines: InvoiceLine[], invoiceTax: number | null): LineTax[] {
  const base = lines.reduce((sum, line) => sum + (line.amount ?? 0), 0);
  const total = invoiceTax ?? 0;

  return lines.map((line) => {
    if (line.tax_amount != null) return { value: line.tax_amount, allocated: false };
    if (!total || base <= 0 || line.amount == null) {
      return { value: 0, allocated: false };
    }
    return { value: (line.amount / base) * total, allocated: true };
  });
}

export default function InvoiceReviewPage() {
  const { id } = useParams<{ id: string }>();
  const { can } = useAuth();
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
  const lineTax = taxPerLine(invoice.lines, invoice.extracted_tax);
  const working = TRANSIENT_STATUSES.has(invoice.status);

  // Reference, date and email on one line. Absent fields are dropped rather
  // than printed as em dashes: four empty tiles say nothing a reviewer needs.
  const documentMeta =
    [
      extracted?.po_number ? `ref ${extracted.po_number}` : null,
      extracted?.order_date,
      extracted?.vendor_email,
    ]
      .filter(Boolean)
      .join(" · ") || "no reference, date or email printed";

  // How the extraction was produced, not what the document says — so it sits
  // beside the heading rather than among the document's own facts.
  const readingCaption = [
    invoice.page_count != null
      ? `${invoice.page_count} page${invoice.page_count === 1 ? "" : "s"}`
      : null,
    invoice.ocr_model ? `read by ${invoice.ocr_model}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
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
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <h2 className="text-sm font-semibold text-slate-900 dark:text-white">
            What the document says
          </h2>
          {extracted && (
            <p className="text-xs text-slate-500 dark:text-slate-400">
              {readingCaption}
            </p>
          )}
        </div>

        {!extracted ? (
          <p className="mt-3 text-sm text-slate-600 dark:text-slate-400">
            {working
              ? "Reading…"
              : "Not read yet. Use “Read document” above."}
          </p>
        ) : (
          <>
            {/* Laid out the way the document heads itself — vendor, then the
                identifying details on one line. The same facts as a grid of
                labelled tiles, but read in one pass instead of nine. */}
            <p className="mt-4 text-base font-medium text-slate-900 dark:text-white">
              {extracted.vendor_name ?? "Unnamed vendor"}
            </p>
            <p className="mt-0.5 text-sm text-slate-600 dark:text-slate-400">
              {documentMeta}
            </p>
            {extracted.vendor_address && (
              <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
                {extracted.vendor_address}
              </p>
            )}

            {invoice.lines.length > 0 && (
              <div className="mt-6 overflow-x-auto">
                <table className="w-full min-w-[640px] text-left text-sm">
                  <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500 dark:border-slate-800 dark:text-slate-400">
                    <tr>
                      <th className="py-2 pr-4 font-medium">#</th>
                      <th className="py-2 pr-4 font-medium">Description</th>
                      <th className="py-2 pr-4 font-medium">Code</th>
                      <th className="py-2 pr-4 text-right font-medium">Qty</th>
                      <th className="py-2 pr-4 font-medium">UoM</th>
                      <th className="py-2 pr-4 text-right font-medium">Unit</th>
                      <th className="py-2 pr-4 text-right font-medium">Subtotal</th>
                      <th className="py-2 pr-4 text-right font-medium">Tax</th>
                      <th className="py-2 text-right font-medium">Total</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
                    {invoice.lines.map((line, index) => (
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
                        <td className="py-2 pr-4 text-right tabular-nums">
                          {money(line.amount)}
                        </td>
                        <td className="py-2 pr-4 text-right tabular-nums">
                          {lineTax[index].allocated ? (
                            <span
                              className="text-slate-500 dark:text-slate-400"
                              title="Allocated from the invoice's total tax — this document does not print tax per line."
                            >
                              ≈ {money(lineTax[index].value)}
                            </span>
                          ) : (
                            money(line.tax_amount)
                          )}
                        </td>
                        <td className="py-2 text-right font-medium tabular-nums">
                          {line.amount == null
                            ? "—"
                            : money(line.amount + lineTax[index].value)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>

                {lineTax.some((tax) => tax.allocated) && (
                  <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
                    ≈ This invoice states tax once for the whole document, so it
                    is shown here spread across the lines by share of value.
                  </p>
                )}
              </div>
            )}

            {/* The document's own totals, once, where an invoice prints them —
                under the lines rather than repeated in a header grid. */}
            <div className="mt-4 flex justify-end">
              <dl className="w-full max-w-[15rem] space-y-1 text-sm">
                <div className="flex justify-between gap-6">
                  <dt className="text-slate-600 dark:text-slate-400">Untaxed</dt>
                  <dd className="tabular-nums text-slate-900 dark:text-slate-100">
                    {money(extracted.untaxed_amount, extracted.currency)}
                  </dd>
                </div>
                <div className="flex justify-between gap-6">
                  <dt className="text-slate-600 dark:text-slate-400">Tax</dt>
                  <dd className="tabular-nums text-slate-900 dark:text-slate-100">
                    {money(extracted.tax_amount, extracted.currency)}
                  </dd>
                </div>
                <div className="flex justify-between gap-6 border-t border-slate-200 pt-1 font-semibold dark:border-slate-800">
                  <dt className="text-slate-900 dark:text-white">Total</dt>
                  <dd className="tabular-nums text-slate-900 dark:text-white">
                    {money(extracted.total_amount, extracted.currency)}
                  </dd>
                </div>
              </dl>
            </div>
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

      {/* ----------------------------------------------------- create a bill */}
      {/* After the match it depends on, before the fallback for when there
          was no match to begin with.

          Billing is where money leaves, so it is the one step a manager does
          not have. They are told what happens next rather than shown a screen
          with a section quietly missing from it. */}
      {can("invoice.bill") ? (
        <CreateVendorBill invoice={invoice} />
      ) : (
        <ReadyForBilling invoice={invoice} />
      )}

      {/* ------------------------------------------------------- create a PO */}
      <CreatePurchaseOrder invoice={invoice} />

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
