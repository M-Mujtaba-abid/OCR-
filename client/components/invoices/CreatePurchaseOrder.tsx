"use client";

import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { usePoPreview, useCreatePo } from "@/hooks/invoice/useInvoices.hooks";
import { money } from "@/lib/format";
import type { InvoiceDetail, PoPreview } from "@/types/invoice.type";

/**
 * Raise a draft purchase order in Odoo from what the invoice says.
 *
 * The reviewer approves a mapping, they do not trigger an import. That is a
 * deliberate response to what the catalogue looks like: an invoice line
 * reading "J5 (lemon)" scores identically against Lemon, Sanitized lemon and
 * Lemon Leaves, and "Egg Plant (C. Int.)" scores *highest* against the wrong
 * product entirely. Resolution can offer candidates; only a person can choose
 * between them, so the create button stays disabled until one has.
 *
 * What is created is a draft RFQ. Nothing is ordered and nobody is owed
 * anything until it is confirmed in Odoo by whoever does that today.
 */
export function CreatePurchaseOrder({ invoice }: { invoice: InvoiceDetail }) {
  const [open, setOpen] = useState(false);
  /** product_id per line_no, seeded from the preview's preselections. */
  const [chosen, setChosen] = useState<Record<number, number>>({});

  const preview = usePoPreview(invoice.id, open);
  const createPo = useCreatePo();

  const created = invoice.status === "po_created";
  const settled =
    created ||
    invoice.status === "confirmed" ||
    invoice.status === "corrected" ||
    invoice.status === "rejected";

  // Preselections are the starting point, not the answer: a line the resolver
  // was unsure about has none, and stays unset until the reviewer picks.
  const selection = useMemo(() => {
    const seed: Record<number, number> = {};
    for (const line of preview.data?.lines ?? []) {
      if (line.preselected_product_id != null) {
        seed[line.line_no] = line.preselected_product_id;
      }
    }
    return { ...seed, ...chosen };
  }, [preview.data, chosen]);

  if (!invoice.extracted_json) return null;

  if (created) {
    return (
      <section className="rounded-xl border border-emerald-200 bg-emerald-50/50 p-6 dark:border-emerald-900 dark:bg-emerald-950/30">
        <div className="flex flex-wrap items-center gap-3">
          <Badge tone="positive">PO created: {invoice.matched_po_name}</Badge>
          {preview.data?.odoo_url && invoice.matched_po_id != null && (
            <a
              href={`${preview.data.odoo_url}/odoo/purchase/${invoice.matched_po_id}`}
              target="_blank"
              rel="noreferrer"
              className="text-sm text-emerald-800 underline underline-offset-4 dark:text-emerald-300"
            >
              Open in Odoo ↗
            </a>
          )}
        </div>
        <p className="mt-2 text-sm text-emerald-900 dark:text-emerald-200">
          It was created as a draft RFQ, so it still needs confirming in Odoo.
        </p>
      </section>
    );
  }

  if (settled) return null;

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-6 dark:border-slate-800 dark:bg-slate-900">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-900 dark:text-white">
            No matching order?
          </h2>
          <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
            Raise this invoice as a new draft purchase order in Odoo.
          </p>
        </div>
        {!open && (
          <Button onClick={() => setOpen(true)}>
            Create purchase order in Odoo
          </Button>
        )}
      </div>

      {open && (
        <div className="mt-4">
          {preview.isLoading && (
            <p className="text-sm text-slate-600 dark:text-slate-400">
              Looking this vendor and these products up in Odoo…
            </p>
          )}
          {preview.isError && (
            <p className="text-sm text-red-700 dark:text-red-400">
              Odoo could not be reached, so nothing can be created right now.
            </p>
          )}
          {preview.data && (
            <PreviewBody
              preview={preview.data}
              selection={selection}
              onChoose={(lineNo, productId) =>
                setChosen((prev) => ({ ...prev, [lineNo]: productId }))
              }
              onCancel={() => setOpen(false)}
              creating={createPo.isPending}
              onCreate={() =>
                createPo.mutate({
                  invoiceId: invoice.id,
                  input: {
                    partner_id: preview.data!.vendor!.id,
                    order_date: preview.data!.order_date,
                    lines: preview.data!.lines.map((line) => ({
                      product_id: selection[line.line_no],
                      description: line.description,
                      quantity: line.quantity,
                      unit_price: line.unit_price,
                    })),
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

function PreviewBody({
  preview,
  selection,
  onChoose,
  onCancel,
  onCreate,
  creating,
}: {
  preview: PoPreview;
  selection: Record<number, number>;
  onChoose: (lineNo: number, productId: number) => void;
  onCancel: () => void;
  onCreate: () => void;
  creating: boolean;
}) {
  const unchosen = preview.lines.filter(
    (line) => selection[line.line_no] == null,
  );

  // No vendor is the end of it. Creating an order against the wrong company is
  // not a smaller mistake than creating none at all.
  if (!preview.vendor) {
    return (
      <div className="rounded-lg border border-amber-200 bg-amber-50/60 p-4 text-sm dark:border-amber-900 dark:bg-amber-950/30">
        <p className="font-medium text-amber-900 dark:text-amber-200">
          No Odoo vendor matched “{preview.vendor_name ?? "—"}”.
        </p>
        <p className="mt-1 text-amber-800 dark:text-amber-300">
          Nothing close enough was found to be sure, so no order can be raised
          from here. Check the vendor name on the document, or create the order
          in Odoo directly.
        </p>
        <div className="mt-3">
          <Button variant="secondary" onClick={onCancel}>
            Close
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-slate-200 p-4 dark:border-slate-800">
        <p className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
          Will be created as
        </p>
        <p className="mt-1 text-sm font-medium text-slate-900 dark:text-white">
          {preview.vendor.name}
          <span className="ml-2 font-mono text-xs text-slate-400">
            #{preview.vendor.id}
          </span>
        </p>
        <p className="mt-0.5 text-sm text-slate-600 dark:text-slate-400">
          {preview.order_date ?? "today"} · {preview.currency} · draft RFQ
        </p>
      </div>

      <div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-800">
        <table className="w-full min-w-[680px] text-left text-sm">
          <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500 dark:border-slate-800 dark:text-slate-400">
            <tr>
              <th className="px-3 py-2 font-medium">The invoice says</th>
              <th className="px-3 py-2 font-medium">Odoo product</th>
              <th className="px-3 py-2 text-right font-medium">Qty</th>
              <th className="px-3 py-2 text-right font-medium">Unit</th>
              <th className="px-3 py-2 text-right font-medium">Subtotal</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
            {preview.lines.map((line) => (
              <tr key={line.line_no}>
                <td className="px-3 py-2 text-slate-900 dark:text-slate-100">
                  {line.description}
                </td>
                <td className="px-3 py-2">
                  {line.candidates.length === 0 ? (
                    <span className="text-sm text-amber-700 dark:text-amber-400">
                      nothing similar in Odoo
                    </span>
                  ) : (
                    <select
                      value={selection[line.line_no] ?? ""}
                      onChange={(event) =>
                        onChoose(line.line_no, Number(event.target.value))
                      }
                      className="w-full max-w-xs rounded-md border border-slate-300 bg-white px-2 py-1 text-sm text-slate-900 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
                    >
                      <option value="">Choose a product…</option>
                      {line.candidates.map((candidate) => (
                        <option key={candidate.id} value={candidate.id}>
                          {candidate.name} ({Math.round(candidate.score)}%)
                        </option>
                      ))}
                    </select>
                  )}
                </td>
                <td className="px-3 py-2 text-right tabular-nums">
                  {line.quantity}
                </td>
                <td className="px-3 py-2 text-right tabular-nums">
                  {money(line.unit_price)}
                </td>
                <td className="px-3 py-2 text-right tabular-nums">
                  {money(line.subtotal)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Said before the order is created, not discovered on the Odoo screen
          afterwards. */}
      <p className="text-xs text-slate-500 dark:text-slate-400">
        Odoo applies whatever tax is configured against each product, so the
        order&rsquo;s total may differ from this invoice&rsquo;s.
      </p>

      {unchosen.length > 0 && (
        <p className="text-sm text-amber-700 dark:text-amber-400">
          {unchosen.length === 1
            ? `Line ${unchosen[0].line_no} still needs a product.`
            : `${unchosen.length} lines still need a product.`}{" "}
          Similar names score alike here, so the choice is yours to make.
        </p>
      )}

      <div className="flex flex-wrap gap-3">
        <Button
          onClick={onCreate}
          disabled={unchosen.length > 0 || creating}
          isLoading={creating}
        >
          Create draft purchase order
        </Button>
        <Button variant="secondary" onClick={onCancel} disabled={creating}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
