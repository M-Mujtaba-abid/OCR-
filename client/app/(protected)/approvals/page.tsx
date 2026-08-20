"use client";

import { useState } from "react";
import Link from "next/link";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { useAwaitingMe, useDecideApproval } from "@/hooks/approval/useApprovals.hooks";
import { useAuth } from "@/hooks/auth/useAuth.hooks";
import { money } from "@/lib/format";
import type { AwaitingItem } from "@/types/approval.type";

/**
 * Everything waiting on you, decidable from here.
 *
 * Deliberately its own page rather than a tab of the admin console, and
 * deliberately open to every company account. An approval chain can name
 * anybody — the point of the feature is that a receiving clerk confirms the
 * goods arrived — and `/admin` starts at manager. Without this page a member on
 * a chain would be notified it was their turn and then have nowhere to go.
 *
 * Which is also why the decision is made here rather than by sending people to
 * the review screen: that screen is behind the same manager gate.
 */
export default function ApprovalsPage() {
  const { user } = useAuth();
  const awaiting = useAwaitingMe();

  if (!user) return null;

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-xl font-semibold text-slate-900 dark:text-white">
          Awaiting you
        </h1>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
          Invoices that cannot be billed until you decide. Approving passes it to
          the next step; declining sends it back to whoever asked, with your
          reason.
        </p>
      </header>

      {awaiting.isLoading && (
        <p className="text-sm text-slate-600 dark:text-slate-400">Loading…</p>
      )}

      {awaiting.isError && (
        <Alert variant="error">
          Your approvals could not be loaded. Refresh to try again.
        </Alert>
      )}

      {awaiting.data?.length === 0 && (
        <section className="rounded-xl border border-slate-200 bg-white p-6 dark:border-slate-800 dark:bg-slate-900">
          <p className="text-sm text-slate-600 dark:text-slate-400">
            Nothing is waiting on you.
          </p>
        </section>
      )}

      <div className="space-y-4">
        {awaiting.data?.map((item) => (
          <AwaitingRow key={item.request.id} item={item} />
        ))}
      </div>
    </div>
  );
}

function AwaitingRow({ item }: { item: AwaitingItem }) {
  const { can } = useAuth();
  const decide = useDecideApproval();
  const [declining, setDeclining] = useState(false);
  const [reason, setReason] = useState("");

  const request = item.request;
  const total = request.lines.reduce(
    (sum, line) => sum + line.quantity * line.unit_price * (1 + line.tax_rate),
    0,
  );

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-6 dark:border-slate-800 dark:bg-slate-900">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-slate-900 dark:text-white">
            {item.vendor || item.file_name}
          </p>
          <p className="mt-0.5 text-xs text-slate-600 dark:text-slate-400">
            {item.invoice_no ? `${item.invoice_no} · ` : ""}
            {item.file_name}
            {request.requester &&
              ` · asked by ${request.requester.full_name || request.requester.email}`}
          </p>
        </div>
        <p className="shrink-0 text-sm font-medium text-sky-700 dark:text-sky-300">
          Step {item.step_position} · {item.step_name}
        </p>
      </div>

      {/* The lines as they were approved-for, from the request's snapshot.
          Never a live preview: you are signing off these numbers, and the bill
          will be capped at them. */}
      <ul className="mt-4 space-y-1 border-t border-slate-200 pt-4 dark:border-slate-800">
        {request.lines.map((line) => (
          <li
            key={line.po_line_id}
            className="flex justify-between gap-4 text-xs text-slate-600 dark:text-slate-400"
          >
            <span className="truncate">{line.description}</span>
            <span className="shrink-0 tabular-nums">
              {line.quantity} × {money(line.unit_price)}
            </span>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-sm font-medium text-slate-900 dark:text-white">
        {money(total)} in total
      </p>

      {/* Only for somebody who could open it. A link that 403s is worse than
          no link, and a member on a receiving step cannot reach /admin. */}
      {can("invoice.read.all") && (
        <Link
          href={`/admin/invoices/${item.invoice_id}`}
          className="mt-2 inline-block text-xs text-slate-500 underline underline-offset-2 hover:text-slate-700 dark:text-slate-500 dark:hover:text-slate-300"
        >
          Open the invoice
        </Link>
      )}

      {!declining ? (
        <div className="mt-5 flex flex-wrap gap-3">
          <Button
            isLoading={decide.isPending}
            onClick={() =>
              decide.mutate({
                requestId: request.id,
                input: { approve: true },
              })
            }
          >
            Approve
          </Button>
          <Button variant="secondary" onClick={() => setDeclining(true)}>
            Decline…
          </Button>
        </div>
      ) : (
        <div className="mt-5 space-y-3">
          <textarea
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            rows={3}
            maxLength={2000}
            placeholder="Why are you declining? Be specific enough to act on."
            className="w-full rounded-lg border border-slate-300 bg-white p-3 text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-slate-300 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
          />
          <div className="flex flex-wrap gap-3">
            <Button
              variant="danger"
              disabled={!reason.trim()}
              isLoading={decide.isPending}
              onClick={() =>
                decide.mutate(
                  { requestId: request.id, input: { approve: false, reason } },
                  { onSuccess: () => setDeclining(false) },
                )
              }
            >
              Decline
            </Button>
            <Button variant="ghost" onClick={() => setDeclining(false)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}
