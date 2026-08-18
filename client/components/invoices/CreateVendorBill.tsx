"use client";

import { useCallback, useMemo, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { useBillPreview, useCreateBill } from "@/hooks/invoice/useInvoices.hooks";
import { money, percent } from "@/lib/format";
import type {
  BillPreview,
  BillPreviewLine,
  CreateBillResult,
  InvoiceDetail,
} from "@/types/invoice.type";

/** Below this, a difference is rounding rather than a disagreement. */
const EPSILON = 1e-6;

/**
 * Turn a matched invoice into a draft vendor bill in Odoo.
 *
 * The reviewer approves a mapping; they do not trigger an import. That matters
 * more here than anywhere else in this app, because this is the step that ends
 * in money leaving.
 *
 * The shape of the screen follows from partial billing. One order for 100
 * pieces is delivered and billed in two halves, so every row shows four
 * quantities — ordered, received, already billed, and what is left — and only
 * the last of those is spendable. Showing "remaining" alone would hide whether
 * a small number means a small order or one that is nearly used up.
 *
 * The bill is created in draft. Nothing is owed until somebody confirms it in
 * Odoo, which is also what keeps Odoo's own duplicate-reference warning useful.
 */
export function CreateVendorBill({ invoice }: { invoice: InvoiceDetail }) {
  const [open, setOpen] = useState(false);
  /** Quantity per po_line_id. Absent means "use what the server proposed". */
  const [edited, setEdited] = useState<Record<number, number>>({});
  const [ref, setRef] = useState<string | null>(null);
  const [attach, setAttach] = useState(true);
  const [receive, setReceive] = useState(true);

  const billed = invoice.status === "pushed" || invoice.pushed_to_odoo;
  const preview = useBillPreview(invoice.id, open && !billed);
  const createBill = useCreateBill();

  const hasOrder = (invoice.final_po_id ?? invoice.matched_po_id) != null;
  if (!invoice.extracted_json || !hasOrder) return null;

  // Rendered from the invoice alone, with no preview fetch, so it survives a
  // page reload rather than only existing while the mutation result is in hand.
  if (billed) {
    return <BilledPanel invoice={invoice} result={createBill.data} />;
  }

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-6 dark:border-slate-800 dark:bg-slate-900">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-900 dark:text-white">
            Bill this invoice in Odoo
          </h2>
          <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
            Records the goods receipt and raises a draft vendor bill against{" "}
            {invoice.matched_po_name ?? "the matched order"}.
          </p>
        </div>
        {!open && (
          <Button onClick={() => setOpen(true)}>Create vendor bill</Button>
        )}
      </div>

      {open && (
        <div className="mt-4">
          {preview.isLoading && (
            <p className="text-sm text-slate-600 dark:text-slate-400">
              Reading the order and checking Odoo for an existing bill…
            </p>
          )}
          {preview.isError && (
            <p className="text-sm text-red-700 dark:text-red-400">
              Odoo could not be reached, so nothing can be billed right now.
            </p>
          )}
          {preview.data && (
            <PreviewBody
              preview={preview.data}
              edited={edited}
              onEdit={(lineId, quantity) =>
                setEdited((prev) => ({ ...prev, [lineId]: quantity }))
              }
              ref_={ref ?? preview.data.invoice_ref ?? ""}
              onRef={setRef}
              attach={attach}
              onAttach={setAttach}
              receive={receive}
              onReceive={setReceive}
              creating={createBill.isPending}
              result={createBill.data}
              onCancel={() => setOpen(false)}
              onCreate={(lines) =>
                createBill.mutate({
                  invoiceId: invoice.id,
                  input: {
                    po_id: preview.data!.po_id,
                    ref: ref ?? preview.data!.invoice_ref,
                    invoice_date: preview.data!.invoice_date,
                    lines,
                    receive_goods: receive,
                    attach_document: attach,
                  },
                })
              }
            />
          )}
        </div>
      )}
    </section>
  );
}

/* ---------------------------------------------------------------- terminal */

function BilledPanel({
  invoice,
  result,
}: {
  invoice: InvoiceDetail;
  result?: CreateBillResult;
}) {
  const label = invoice.odoo_bill_ref ?? `#${invoice.odoo_bill_id}`;
  return (
    <section className="rounded-xl border border-emerald-200 bg-emerald-50/50 p-6 dark:border-emerald-900 dark:bg-emerald-950/30">
      <div className="flex flex-wrap items-center gap-3">
        <Badge tone="positive">Billed: {label}</Badge>
        {/* The link comes from the mutation that just ran. On a cold reload
            there is none, and firing an expensive preview purely to learn a
            hostname would be the wrong trade — the reference above is enough
            to find the bill in Odoo. */}
        {result?.bill_url && (
          <a
            href={result.bill_url}
            target="_blank"
            rel="noreferrer"
            className="text-sm text-emerald-800 underline underline-offset-4 dark:text-emerald-300"
          >
            Open in Odoo ↗
          </a>
        )}
      </div>
      <p className="mt-2 text-sm text-emerald-900 dark:text-emerald-200">
        Created as a draft, so it still needs posting in Odoo.
        {invoice.pushed_at &&
          ` Billed ${new Date(invoice.pushed_at).toLocaleDateString()}.`}
      </p>
      {result?.receipt_name && (
        <p className="mt-1 text-sm text-emerald-800 dark:text-emerald-300">
          Goods received on {result.receipt_name}
          {result.backorder_names.length > 0 &&
            ` · ${result.backorder_names.join(", ")} left to come`}
          .
        </p>
      )}
      {result && result.attachment_status !== "attached" && (
        <p className="mt-2 text-sm text-amber-700 dark:text-amber-400">
          The scanned document was not attached to the bill. Attach it in Odoo
          by hand.
        </p>
      )}
    </section>
  );
}

/* ----------------------------------------------------------------- blocked */

function Blocked({
  title,
  children,
  onCancel,
}: {
  title: string;
  children: React.ReactNode;
  onCancel: () => void;
}) {
  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50/60 p-4 text-sm dark:border-amber-900 dark:bg-amber-950/30">
      <p className="font-medium text-amber-900 dark:text-amber-200">{title}</p>
      <div className="mt-1 text-amber-800 dark:text-amber-300">{children}</div>
      <div className="mt-3">
        <Button variant="secondary" onClick={onCancel}>
          Close
        </Button>
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------- body */

function PreviewBody({
  preview,
  edited,
  onEdit,
  ref_,
  onRef,
  attach,
  onAttach,
  receive,
  onReceive,
  creating,
  result,
  onCancel,
  onCreate,
}: {
  preview: BillPreview;
  edited: Record<number, number>;
  onEdit: (lineId: number, quantity: number) => void;
  ref_: string;
  onRef: (value: string) => void;
  attach: boolean;
  onAttach: (value: boolean) => void;
  receive: boolean;
  onReceive: (value: boolean) => void;
  creating: boolean;
  result?: CreateBillResult;
  onCancel: () => void;
  onCreate: (lines: { po_line_id: number; quantity: number }[]) => void;
}) {
  // Defined with useCallback so the memo below can depend on it honestly
  // rather than on the state it happens to close over.
  const quantityOf = useCallback(
    (line: BillPreviewLine) => edited[line.po_line_id] ?? line.proposed_qty,
    [edited],
  );

  const overBilled = useMemo(
    () =>
      preview.lines.filter(
        (line) => quantityOf(line) - line.remaining_qty > EPSILON,
      ),
    [preview.lines, quantityOf],
  );

  const chosen = preview.lines.filter((line) => quantityOf(line) > EPSILON);

  /**
   * What the bill will say, at the quantities currently on screen.
   *
   * Recomputed here rather than taken from the preview because the reviewer
   * edits "Bill now", and the server's figures are for the quantities it
   * proposed. Tax comes from each line's Odoo rate — never from the invoice.
   * Odoo owns the rate; this only reports what it will charge, which is the
   * whole point: an order line with no tax against an invoice charging 5% is
   * something to see here, not after the payable is posted.
   */
  const untaxed = chosen.reduce(
    (sum, line) => sum + quantityOf(line) * line.unit_price,
    0,
  );
  const tax = chosen.reduce(
    (sum, line) => sum + quantityOf(line) * line.unit_price * line.tax_rate,
    0,
  );
  const total = untaxed + tax;

  // A duplicate that came back from the create call outranks the one the
  // preview found: it is the newer answer, and it is the one the reviewer just
  // caused.
  const duplicate =
    result && result.status !== "bill_created"
      ? {
          bill_ref: result.bill_ref ?? "",
          outcome: result.status,
          bill_url: result.bill_url,
        }
      : preview.duplicate
        ? {
            bill_ref: preview.duplicate.bill_ref,
            outcome: preview.duplicate.outcome,
            bill_url: "",
          }
        : null;

  if (duplicate) {
    const paid = duplicate.outcome === "already_paid";
    return (
      <Blocked
        title={`Odoo already has a bill for ${duplicate.bill_ref}.`}
        onCancel={onCancel}
      >
        <p>
          {paid
            ? `It has been paid. Billing this invoice again would pay ${
                preview.partner_name ?? "this vendor"
              } twice.`
            : "It is not paid yet, so it can still be corrected — in Odoo, not from here."}
        </p>
        {duplicate.bill_url && (
          <a
            href={duplicate.bill_url}
            target="_blank"
            rel="noreferrer"
            className="mt-1 inline-block underline underline-offset-4"
          >
            Open in Odoo ↗
          </a>
        )}
      </Blocked>
    );
  }

  if (preview.po_state !== "purchase" && preview.po_state !== "done") {
    return (
      <Blocked
        title={`${preview.po_name} is not a confirmed order in Odoo.`}
        onCancel={onCancel}
      >
        <p>
          It is still {preview.po_state ?? "a draft"}. Confirm the RFQ in Odoo
          before billing against it — a draft order has no committed lines for a
          bill to attach to.
        </p>
      </Blocked>
    );
  }

  if (preview.lines.every((line) => line.remaining_qty <= EPSILON)) {
    return (
      <Blocked title={`${preview.po_name} is fully billed.`} onCancel={onCancel}>
        <p>
          Every line on the order has been invoiced already. If this invoice is
          for something else, it belongs against a different order.
        </p>
      </Blocked>
    );
  }

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-slate-200 p-4 dark:border-slate-800">
        <p className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
          Will be created as
        </p>
        <p className="mt-1 text-sm font-medium text-slate-900 dark:text-white">
          {preview.partner_name ?? "—"}
          <span className="ml-2 font-mono text-xs text-slate-400">
            {preview.po_name}
          </span>
        </p>
        <p className="mt-0.5 text-sm text-slate-600 dark:text-slate-400">
          {preview.invoice_date} · {preview.currency ?? "—"} · draft bill
        </p>
      </div>

      <div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-800">
        <table className="w-full min-w-[860px] text-left text-sm">
          <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500 dark:border-slate-800 dark:text-slate-400">
            <tr>
              <th className="px-3 py-2 font-medium">Order line</th>
              <th className="px-3 py-2 text-right font-medium">Ordered</th>
              <th className="px-3 py-2 text-right font-medium">Received</th>
              <th className="px-3 py-2 text-right font-medium">Billed</th>
              <th className="px-3 py-2 text-right font-medium">Remaining</th>
              <th className="px-3 py-2 text-right font-medium">Bill now</th>
              <th className="px-3 py-2 text-right font-medium">Unit</th>
              <th className="px-3 py-2 text-right font-medium">Subtotal</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
            {preview.lines.map((line) => (
              <LineRow
                key={line.po_line_id}
                line={line}
                quantity={quantityOf(line)}
                currency={preview.currency}
                over={overBilled.includes(line)}
                onEdit={(value) => onEdit(line.po_line_id, value)}
              />
            ))}
          </tbody>
        </table>
      </div>

      {overBilled.length > 0 && (
        // Refused here as well as on the server, and worded the same way on
        // purpose: when the server wins a race — a bill raised elsewhere between
        // this preview and this click — the reviewer reads a sentence they have
        // already understood once.
        <div className="rounded-lg border border-red-200 bg-red-50/60 p-4 text-sm dark:border-red-900 dark:bg-red-950/30">
          <p className="font-medium text-red-900 dark:text-red-200">
            This would bill more than the order has left.
          </p>
          <ul className="mt-1 space-y-0.5 text-red-800 dark:text-red-300">
            {overBilled.map((line) => (
              <li key={line.po_line_id}>
                {line.product_name ?? line.description}:{" "}
                {quantityOf(line).toLocaleString()} asked for,{" "}
                {line.remaining_qty.toLocaleString()} left to bill
              </li>
            ))}
          </ul>
        </div>
      )}

      {!ref_.trim() && (
        // Without a reference there is nothing to search Odoo for, so the
        // duplicate check silently does not run. Saying so is the difference
        // between a reviewer choosing to proceed and one assuming they were
        // covered — and the local "this invoice was already billed" guard only
        // catches this same row, not the same paper uploaded twice.
        <div className="rounded-lg border border-amber-200 bg-amber-50/60 p-4 text-sm dark:border-amber-900 dark:bg-amber-950/30">
          <p className="font-medium text-amber-900 dark:text-amber-200">
            No invoice number was read from this document.
          </p>
          <p className="mt-1 text-amber-800 dark:text-amber-300">
            The duplicate check searches Odoo by vendor invoice number, so it
            cannot run without one. Type it in below if it is printed on the
            paper — otherwise nothing here will notice if this vendor has
            already been paid for it.
          </p>
        </div>
      )}

      {preview.unmatched.length > 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50/60 p-4 text-sm dark:border-amber-900 dark:bg-amber-950/30">
          <p className="font-medium text-amber-900 dark:text-amber-200">
            {preview.unmatched.length} invoice line
            {preview.unmatched.length === 1 ? "" : "s"} matched nothing on this
            order.
          </p>
          <ul className="mt-1 space-y-0.5 text-amber-800 dark:text-amber-300">
            {preview.unmatched.map((line) => (
              <li key={line.line_no}>
                {line.description} — {line.quantity.toLocaleString()} ×{" "}
                {money(line.unit_price, preview.currency)}
              </li>
            ))}
          </ul>
          <p className="mt-2 text-amber-800 dark:text-amber-300">
            They will not be billed. If they belong on this order, add them in
            Odoo first.
          </p>
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block text-sm">
          <span className="font-medium text-slate-900 dark:text-white">
            Vendor invoice number
          </span>
          <input
            type="text"
            value={ref_}
            onChange={(event) => onRef(event.target.value)}
            className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-900 dark:text-white"
          />
          <span className="mt-1 block text-xs text-slate-500 dark:text-slate-400">
            This is the number the duplicate check searches on — correct it if
            the scan mangled it.
          </span>
        </label>

        <div className="space-y-2 text-sm">
          <label className="flex items-start gap-2">
            <input
              type="checkbox"
              checked={receive}
              onChange={(event) => onReceive(event.target.checked)}
              className="mt-1"
            />
            <span>
              <span className="font-medium text-slate-900 dark:text-white">
                Record the goods receipt
              </span>
              <span className="mt-0.5 block text-xs text-slate-500 dark:text-slate-400">
                Receives these quantities in Odoo and backorders the rest. This
                cannot be undone from here. Untick only if the receipt has
                already been validated in Odoo.
              </span>
            </span>
          </label>
          <label className="flex items-start gap-2">
            <input
              type="checkbox"
              checked={attach}
              onChange={(event) => onAttach(event.target.checked)}
              className="mt-1"
            />
            <span className="font-medium text-slate-900 dark:text-white">
              Attach the scanned document
            </span>
          </label>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="text-sm text-slate-600 dark:text-slate-400">
          <p>
            Billing {chosen.length} line{chosen.length === 1 ? "" : "s"} ·
            untaxed {money(untaxed, preview.currency)} · tax{" "}
            {money(tax, preview.currency)} ·{" "}
            <span className="font-medium text-slate-900 dark:text-white">
              {money(total, preview.currency)}
            </span>
          </p>
          {/* Compared against the invoice's TOTAL, not its untaxed amount. The
              paper's headline figure is what a reviewer checks the screen
              against, and comparing untaxed-to-total is what made a correctly
              taxed bill look like it had dropped the tax. */}
          {preview.invoice_total != null &&
            Math.abs(preview.invoice_total - total) > 0.01 && (
              <p className="mt-1 text-amber-700 dark:text-amber-400">
                The invoice says {money(preview.invoice_total, preview.currency)}
                {preview.invoice_tax != null &&
                  Math.abs(preview.invoice_tax - tax) > 0.01 && (
                    <>
                      {" "}
                      — its tax is {money(preview.invoice_tax, preview.currency)}
                      {tax < EPSILON
                        ? ", and this order carries none. Set the tax on the order in Odoo before billing."
                        : ` against Odoo's ${money(tax, preview.currency)}.`}
                    </>
                  )}
              </p>
            )}
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={onCancel} disabled={creating}>
            Cancel
          </Button>
          <Button
            isLoading={creating}
            disabled={overBilled.length > 0 || chosen.length === 0}
            onClick={() =>
              onCreate(
                chosen.map((line) => ({
                  po_line_id: line.po_line_id,
                  quantity: quantityOf(line),
                })),
              )
            }
          >
            Create draft bill in Odoo
          </Button>
        </div>
      </div>
    </div>
  );
}

function LineRow({
  line,
  quantity,
  currency,
  over,
  onEdit,
}: {
  line: BillPreviewLine;
  quantity: number;
  currency: string | null;
  over: boolean;
  onEdit: (quantity: number) => void;
}) {
  const priceGap =
    line.invoice_unit_price != null &&
    Math.abs(line.invoice_unit_price - line.unit_price) > 0.01;

  return (
    <tr className={over ? "bg-red-50/60 dark:bg-red-950/30" : undefined}>
      <td className="px-3 py-2">
        <div className="font-medium text-slate-900 dark:text-white">
          {line.product_name ?? line.description}
        </div>
        {line.invoice_line_no != null ? (
          <div className="text-xs text-slate-500 dark:text-slate-400">
            invoice line {line.invoice_line_no}: {line.invoice_description}
            {line.match_score != null && (
              <span className="ml-1 font-mono">
                {Math.round(line.match_score)}%
              </span>
            )}
          </div>
        ) : (
          <div className="text-xs text-amber-700 dark:text-amber-400">
            no invoice line matched — type a quantity to bill it anyway
          </div>
        )}
      </td>
      <td className="px-3 py-2 text-right tabular-nums text-slate-600 dark:text-slate-400">
        {line.ordered_qty.toLocaleString()}
      </td>
      <td className="px-3 py-2 text-right tabular-nums text-slate-600 dark:text-slate-400">
        {line.received_qty.toLocaleString()}
      </td>
      <td className="px-3 py-2 text-right tabular-nums text-slate-600 dark:text-slate-400">
        {line.billed_qty.toLocaleString()}
      </td>
      <td className="px-3 py-2 text-right font-medium tabular-nums text-slate-900 dark:text-white">
        {line.remaining_qty.toLocaleString()}
      </td>
      <td className="px-3 py-2 text-right">
        <input
          type="number"
          min={0}
          step="any"
          value={quantity}
          onChange={(event) => onEdit(Number(event.target.value) || 0)}
          aria-invalid={over || undefined}
          aria-label={`Quantity to bill for ${line.product_name ?? line.description}`}
          className={`w-24 rounded-lg border px-2 py-1 text-right text-sm tabular-nums dark:bg-slate-900 dark:text-white ${
            over
              ? "border-red-400 ring-1 ring-red-400 dark:border-red-700"
              : "border-slate-300 dark:border-slate-700"
          }`}
        />
      </td>
      <td className="px-3 py-2 text-right tabular-nums text-slate-600 dark:text-slate-400">
        {money(line.unit_price, currency)}
        {priceGap && (
          <div className="text-xs text-amber-700 dark:text-amber-400">
            invoice: {money(line.invoice_unit_price, currency)}
          </div>
        )}
      </td>
      <td className="px-3 py-2 text-right tabular-nums text-slate-900 dark:text-white">
        {money(quantity * line.unit_price, currency)}
        {/* The rate Odoo holds for this line, per line rather than only in the
            footer: a single untaxed line among taxed ones is what makes a
            whole bill disagree with the paper, and the total alone does not
            say which one it was. */}
        <div className="text-xs font-normal text-slate-500 dark:text-slate-400">
          {line.tax_rate > 0
            ? `+ ${money(quantity * line.unit_price * line.tax_rate, currency)} tax (${percent(line.tax_rate)})`
            : "no tax"}
        </div>
      </td>
    </tr>
  );
}
