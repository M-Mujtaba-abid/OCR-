"use client";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { money } from "@/lib/format";
import type { InvoiceDetail, MatchCandidate } from "@/types/invoice.type";

/** Human labels for the score components. */
const COMPONENT_LABEL: Record<string, string> = {
  vendor: "Vendor",
  amount: "Amount",
  reference: "Reference",
  date: "Date",
  lines: "Line items",
};

function scoreTone(score: number): "positive" | "warning" | "negative" {
  if (score >= 75) return "positive";
  if (score >= 45) return "warning";
  return "negative";
}

/**
 * The ranked purchase orders, with the reasoning that produced the choice.
 *
 * Every candidate is shown, not just the winner. A reviewer's real question is
 * "was the right order even considered?", and a screen that shows only the
 * suggestion cannot answer it — which is how a matching feature becomes
 * something people stop trusting.
 */
export function MatchCandidates({
  invoice,
  onConfirm,
  confirming,
  disabled,
}: {
  invoice: InvoiceDetail;
  onConfirm: (poId: number) => void;
  confirming: boolean;
  disabled: boolean;
}) {
  const candidates = invoice.candidates;
  const decided = invoice.status === "confirmed" || invoice.status === "corrected";

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-6 dark:border-slate-800 dark:bg-slate-900">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-900 dark:text-white">
            Purchase order match
          </h2>
          {candidates && (
            <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
              {candidates.items.length} candidate
              {candidates.items.length === 1 ? "" : "s"} considered ·{" "}
              {candidates.strategy}
            </p>
          )}
        </div>
        {invoice.confidence_score != null && (
          <Badge tone={scoreTone(invoice.confidence_score)}>
            {Math.round(invoice.confidence_score)}% confident
          </Badge>
        )}
      </div>

      {!candidates ? (
        <p className="mt-3 text-sm text-slate-600 dark:text-slate-400">
          Matching has not run yet.
        </p>
      ) : (
        <>
          {invoice.match_reasoning && (
            <div className="mt-4 rounded-lg bg-slate-50 p-4 text-sm text-slate-700 dark:bg-slate-950 dark:text-slate-300">
              <p className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
                Reasoning
              </p>
              <p className="whitespace-pre-line">{invoice.match_reasoning}</p>
            </div>
          )}

          {candidates.items.length === 0 ? (
            <p className="mt-4 text-sm text-slate-600 dark:text-slate-400">
              No purchase order scored highly enough to be worth considering.
              Assign one manually in Odoo, or reject the invoice.
            </p>
          ) : (
            <ul className="mt-4 space-y-3">
              {candidates.items.map((candidate) => (
                <CandidateRow
                  key={candidate.po_id}
                  candidate={candidate}
                  isChosen={candidate.po_id === candidates.chosen_po_id}
                  isFinal={decided && candidate.po_id === invoice.matched_po_id}
                  onConfirm={onConfirm}
                  confirming={confirming}
                  disabled={disabled || decided}
                />
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}

function CandidateRow({
  candidate,
  isChosen,
  isFinal,
  onConfirm,
  confirming,
  disabled,
}: {
  candidate: MatchCandidate;
  isChosen: boolean;
  isFinal: boolean;
  onConfirm: (poId: number) => void;
  confirming: boolean;
  disabled: boolean;
}) {
  // Candidates matched before line details were stored carry no items at all,
  // so this is a missing-data case rather than an empty order.
  const items = candidate.items ?? [];
  const alreadyBilled = candidate.invoice_status === "invoiced";

  return (
    <li
      className={[
        "rounded-lg border p-4",
        isChosen
          ? "border-indigo-300 bg-indigo-50/50 dark:border-indigo-800 dark:bg-indigo-950/30"
          : "border-slate-200 dark:border-slate-800",
      ].join(" ")}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="font-medium text-slate-900 dark:text-white">
              {candidate.po_number}
            </p>
            {isChosen && <Badge tone="accent">Suggested</Badge>}
            {isFinal && <Badge tone="positive">Confirmed</Badge>}
            {/* Odoo already has a bill for this order. Confirming it anyway is
                how a vendor gets paid twice, so it is stated on the card
                rather than left to whoever thinks to check Odoo. */}
            {alreadyBilled && <Badge tone="warning">Already invoiced</Badge>}
          </div>
          <p className="mt-0.5 text-sm text-slate-600 dark:text-slate-400">
            {candidate.vendor ?? "—"}
            {candidate.order_date ? ` · ${candidate.order_date}` : ""}
            {candidate.vendor_ref ? ` · ref ${candidate.vendor_ref}` : ""}
          </p>
        </div>

        <div className="text-right">
          <p className="tabular-nums text-slate-900 dark:text-slate-100">
            {money(candidate.amount_untaxed, candidate.currency)}
          </p>
          <p className="text-xs text-slate-500 dark:text-slate-400">untaxed</p>
        </div>
      </div>

      {/* The score breakdown. This is what makes a suggestion inspectable
          rather than something to take on faith. */}
      <div className="mt-3 flex flex-wrap gap-1.5">
        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium tabular-nums text-slate-700 dark:bg-slate-800 dark:text-slate-200">
          score {Math.round(candidate.score)}
        </span>
        {Object.entries(candidate.breakdown).map(([key, value]) => (
          <span
            key={key}
            title={COMPONENT_LABEL[key] ?? key}
            className={[
              "rounded-full px-2 py-0.5 text-xs tabular-nums",
              value >= 75
                ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300"
                : value >= 45
                  ? "bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300"
                  : "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400",
            ].join(" ")}
          >
            {COMPONENT_LABEL[key] ?? key} {Math.round(value)}
          </span>
        ))}
      </div>

      {/* What this order actually contains, under the score that judged it.
          A line-items score of 0 is only actionable if the reviewer can see
          why — "the invoice says Apple, this order says Eggplant" is the whole
          decision, and without these rows making it means opening Odoo. */}
      {items.length > 0 && (
        <div className="mt-3 overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-800">
          <table className="w-full min-w-[520px] text-left text-sm">
            <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500 dark:border-slate-800 dark:text-slate-400">
              <tr>
                <th className="px-3 py-2 font-medium">Reference id</th>
                <th className="px-3 py-2 font-medium">Product name</th>
                <th className="px-3 py-2 text-right font-medium">Item</th>
                <th className="px-3 py-2 text-right font-medium">Price tax</th>
                <th className="px-3 py-2 text-right font-medium">Total price</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
              {items.map((item, index) => (
                <tr key={index}>
                  {/* The order's own Odoo reference, repeated per line the way
                      a printed document carries it — so a row stays traceable
                      once it is copied, quoted, or read on its own. */}
                  <td className="whitespace-nowrap px-3 py-2 font-mono text-xs text-slate-500 dark:text-slate-400">
                    {candidate.po_number}
                  </td>
                  <td className="px-3 py-2 text-slate-900 dark:text-slate-100">
                    {item.name}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {item.quantity}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {money(item.price_tax)}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {money(item.price_total, candidate.currency)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {/* A capped list must say so, or a 40-line order reads as a 25-line
              one and the reviewer compares against the wrong order. */}
          {candidate.line_count != null && candidate.line_count > items.length && (
            <p className="border-t border-slate-200 px-3 py-2 text-xs text-slate-500 dark:border-slate-800 dark:text-slate-400">
              showing {items.length} of {candidate.line_count} lines
            </p>
          )}
        </div>
      )}

      {candidate.rejected_because && (
        <p className="mt-2 text-sm text-slate-600 dark:text-slate-400">
          <span className="font-medium">Not chosen:</span>{" "}
          {candidate.rejected_because}
        </p>
      )}

      {alreadyBilled && (
        <p className="mt-2 text-sm text-amber-700 dark:text-amber-400">
          Odoo already has a bill for this order. If this invoice is the same
          one, confirming it would pay the vendor twice.
        </p>
      )}

      {!disabled && (
        <div className="mt-3">
          <Button
            variant={isChosen ? "primary" : "secondary"}
            disabled={confirming}
            isLoading={confirming}
            onClick={() => onConfirm(candidate.po_id)}
          >
            {isChosen ? "Confirm this match" : "Use this one instead"}
          </Button>
        </div>
      )}
    </li>
  );
}
