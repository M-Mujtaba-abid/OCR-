"use client";

import { useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { RefreshButton } from "@/components/ui/RefreshButton";
import { Skeleton, SkeletonSteps } from "@/components/ui/Skeleton";
import {
  useCancelApproval,
  useDecideApproval,
  useInvoiceApproval,
  useRequestApproval,
} from "@/hooks/approval/useApprovals.hooks";
import { useAuth } from "@/hooks/auth/useAuth.hooks";
import { useBillPreview } from "@/hooks/invoice/useInvoices.hooks";
import { money } from "@/lib/format";
import type {
  ApprovalRequest,
  ApprovalStepProgress,
} from "@/types/approval.type";
import type { InvoiceDetail } from "@/types/invoice.type";

/**
 * Where an invoice has got to in its approval chain, and — when it is your turn
 * — the two buttons that move it.
 *
 * Everything rendered here comes from the request's own snapshot rather than
 * from a live bill preview, and that is load-bearing rather than incidental: an
 * approver shown current Odoo figures would be signing off numbers that are not
 * the ones the request is capped at. What you see is what `check_exceeds_approval`
 * will hold the biller to.
 */
export function ApprovalPanel({ invoice }: { invoice: InvoiceDetail }) {
  const { user, can } = useAuth();
  const approval = useInvoiceApproval(invoice.id);

  // A shaped placeholder rather than nothing. Returning null meant the panel
  // popped into existence a beat after the rest of the page and pushed the
  // billing section down with it — on the one screen where "can this be billed"
  // is the question being answered.
  if (approval.isLoading) {
    return (
      <section className="rounded-xl border border-slate-200 bg-white p-6 dark:border-slate-800 dark:bg-slate-900">
        <Skeleton className="h-4 w-24" />
        <div className="mt-5">
          <SkeletonSteps rows={3} label="Loading approval status" />
        </div>
      </section>
    );
  }

  // Said out loud rather than disappearing. This panel is the only thing that
  // explains why billing is blocked, so a silent failure leaves an admin
  // looking at a Create-bill button that answers 409 for no visible reason.
  if (approval.isError || !approval.data) {
    return (
      <section className="rounded-xl border border-slate-200 bg-white p-6 dark:border-slate-800 dark:bg-slate-900">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-sm font-semibold text-slate-900 dark:text-white">
            Approval
          </h2>
          <RefreshButton
            onRefresh={() => void approval.refetch()}
            refreshing={approval.isFetching}
            what="approval status"
            size="sm"
          />
        </div>
        <div className="mt-3">
          <Alert variant="error">
            The approval status could not be loaded, so this screen cannot say
            whether this invoice may be billed. Refresh to try again.
          </Alert>
        </div>
      </section>
    );
  }

  const {
    chain_active: gated,
    chain_name: chainName,
    request,
    can_decide: canDecide,
  } = approval.data;

  // No request and no active chain: this company does not gate billing, so a
  // panel explaining an absent process is just noise on the screen.
  if (!request && !gated) return null;

  const billed = invoice.status === "pushed" || invoice.pushed_to_odoo;
  // A declined or cancelled request is finished; the next attempt is a fresh
  // one starting at step 1, which is why this offers to send rather than to
  // resume. Somebody who already approved step 1 sees it again because the
  // quantities may have changed since they did.
  const canSend =
    gated &&
    !billed &&
    can("invoice.review") &&
    (!request || request.status === "declined" || request.status === "cancelled");

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-6 dark:border-slate-800 dark:bg-slate-900">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-sm font-semibold text-slate-900 dark:text-white">
          Approval
        </h2>
        <div className="flex items-center gap-3">
          {request && <StatusLine request={request} />}
          {/* Somebody else decides these steps, so there is no local event to
              invalidate on — the only way this screen learns is by asking. */}
          <RefreshButton
            onRefresh={() => void approval.refetch()}
            refreshing={approval.isFetching}
            what="approval status"
            size="sm"
          />
        </div>
      </div>

      {!request && (
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
          {chainName ?? "An approval chain"} gates vendor bills in this company,
          so this invoice has to go through it before one can be raised.
        </p>
      )}

      {request?.receipt && (
        <p className="mt-3 text-xs text-emerald-700 dark:text-emerald-400">
          Goods receipt {request.receipt.picking_name} was posted in Odoo when
          step {request.receipt.position} was approved
          {request.receipt.backorders.length > 0 &&
            `, with ${request.receipt.backorders.join(", ")} left as a backorder`}
          .
        </p>
      )}

      {request && (
        <>
          <ol className="mt-5 space-y-0">
            {request.steps.map((step, index) => (
              <StepRow
                key={step.position}
                step={step}
                last={index === request.steps.length - 1}
                pending={request.status === "pending"}
                currentUserId={user?.id}
              />
            ))}
          </ol>
          <ApprovedAmount request={request} />
        </>
      )}

      {/* Whether it is my turn is answered by the server, not recomputed here.
          The rules — named on the rung, not the requester, not already having
          decided — are subtle enough that a second copy of them would drift. */}
      {request && canDecide && (
        <DecideBox
          requestId={request.id}
          posts={
            request.steps.find((step) => step.is_current)?.records_receipt ??
            false
          }
        />
      )}

      {canSend && <SendBox invoice={invoice} resubmit={Boolean(request)} />}

      {can("approval.configure") && request?.status === "pending" && (
        <CancelBox requestId={request.id} />
      )}
    </section>
  );
}

/**
 * Prepare and send this invoice through the chain.
 *
 * The preview is fetched only once somebody asks for it. It costs an order read
 * plus a search for existing bills in Odoo, and a reviewer who opens an invoice
 * to read it should not pay for either.
 *
 * The quantities sent are the ones the preview proposes. That is the honest
 * default — they are what the invoice's own lines matched to — and it is what
 * the approvers will be shown and the biller later held to.
 */
function SendBox({
  invoice,
  resubmit,
}: {
  invoice: InvoiceDetail;
  resubmit: boolean;
}) {
  const [preparing, setPreparing] = useState(false);
  const preview = useBillPreview(invoice.id, preparing);
  const request = useRequestApproval();

  if (!preparing) {
    return (
      <div className="mt-5 border-t border-slate-200 pt-5 dark:border-slate-800">
        <Button variant="secondary" onClick={() => setPreparing(true)}>
          {resubmit ? "Send for approval again…" : "Send for approval…"}
        </Button>
        {resubmit && (
          <p className="mt-2 text-xs text-slate-500 dark:text-slate-500">
            This starts a new request at step 1. Everybody sees it again,
            because what they are approving may have changed.
          </p>
        )}
      </div>
    );
  }

  if (preview.isLoading) {
    return (
      <p className="mt-5 border-t border-slate-200 pt-5 text-sm text-slate-600 dark:border-slate-800 dark:text-slate-400">
        Reading the order from Odoo…
      </p>
    );
  }

  if (preview.isError || !preview.data) {
    return (
      <div className="mt-5 space-y-3 border-t border-slate-200 pt-5 dark:border-slate-800">
        <Alert variant="error">
          The order could not be read from Odoo, so there is nothing to send for
          approval yet.
        </Alert>
        <Button variant="ghost" onClick={() => setPreparing(false)}>
          Close
        </Button>
      </div>
    );
  }

  const lines = preview.data.lines
    .filter((line) => line.proposed_qty > 0)
    .map((line) => ({
      po_line_id: line.po_line_id,
      quantity: line.proposed_qty,
    }));

  return (
    <div className="mt-5 space-y-3 border-t border-slate-200 pt-5 dark:border-slate-800">
      <Alert variant="info">
        The approvers will see these quantities and prices, and the bill will be
        capped at them. To bill more than this afterwards, the invoice has to go
        through the chain again.
      </Alert>
      <ul className="space-y-1">
        {preview.data.lines
          .filter((line) => line.proposed_qty > 0)
          .map((line) => (
            <li
              key={line.po_line_id}
              className="flex justify-between gap-4 text-xs text-slate-600 dark:text-slate-400"
            >
              <span className="truncate">{line.product_name}</span>
              <span className="shrink-0 tabular-nums">
                {line.proposed_qty} × {money(line.unit_price)}
              </span>
            </li>
          ))}
      </ul>
      <div className="flex flex-wrap gap-3">
        <Button
          disabled={lines.length === 0}
          isLoading={request.isPending}
          onClick={() =>
            request.mutate(
              {
                invoiceId: invoice.id,
                input: { po_id: preview.data.po_id, lines },
              },
              { onSuccess: () => setPreparing(false) },
            )
          }
        >
          Send for approval
        </Button>
        <Button variant="ghost" onClick={() => setPreparing(false)}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

function StatusLine({ request }: { request: ApprovalRequest }) {
  const TONE = {
    pending: "text-sky-700 dark:text-sky-300",
    approved: "text-emerald-700 dark:text-emerald-300",
    declined: "text-red-700 dark:text-red-300",
    cancelled: "text-slate-600 dark:text-slate-400",
  } as const;

  const WORDS = {
    pending: `Waiting on step ${request.current_position} of ${request.steps.length}`,
    approved: "Approved — ready to bill",
    declined: "Declined and sent back",
    cancelled: "Pulled out of the chain by an administrator",
  } as const;

  const waitingDays = request.waiting_days;

  return (
    <p className={`text-sm font-medium ${TONE[request.status]}`}>
      {WORDS[request.status]}
      {request.status === "pending" && waitingDays >= 1 && (
        <span className="ml-1 font-normal text-slate-500 dark:text-slate-500">
          · {waitingDays} day{waitingDays === 1 ? "" : "s"}
        </span>
      )}
    </p>
  );
}

/**
 * One rung.
 *
 * The outcome is worded as well as coloured — a green dot alone tells somebody
 * with deuteranopia nothing, and this is a screen about who authorised a
 * payment.
 */
function StepRow({
  step,
  last,
  pending,
  currentUserId,
}: {
  step: ApprovalStepProgress;
  last: boolean;
  pending: boolean;
  currentUserId?: string;
}) {
  const decision = step.decision;
  const waiting = pending && step.is_current && !decision;

  const dot = decision
    ? decision.decision === "approved"
      ? "bg-emerald-600"
      : "bg-red-600 dark:bg-red-500"
    : waiting
      ? "bg-sky-600 dark:bg-sky-500"
      : "bg-slate-300 dark:bg-slate-700";

  const outcome = decision
    ? decision.decision === "approved"
      ? "Approved"
      : decision.decision === "declined"
        ? "Declined"
        : "Cancelled"
    : waiting
      ? "Waiting"
      : "Not reached yet";

  return (
    <li className="flex gap-3">
      <div className="flex flex-col items-center">
        <span className={`mt-1.5 size-2.5 shrink-0 rounded-full ${dot}`} />
        {/* The rail between rungs, so the chain reads as a sequence rather than
            as four unrelated rows. */}
        {!last && <span className="w-px flex-1 bg-slate-200 dark:bg-slate-800" />}
      </div>

      <div className={last ? "pb-0" : "pb-5"}>
        <p className="text-sm font-medium text-slate-900 dark:text-white">
          {step.position}. {step.name}
        </p>
        {step.records_receipt && (
          <p className="mt-0.5 text-xs text-sky-700 dark:text-sky-400">
            Posts the goods receipt in Odoo
          </p>
        )}
        <p className="mt-0.5 text-xs text-slate-600 dark:text-slate-400">
          {outcome}
          {decision?.decided_by === currentUserId && decision && " by you"}
          {step.approver_user_ids.length > 1 && !decision && (
            <> · any one of {step.approver_user_ids.length} people</>
          )}
        </p>
        {decision?.reason && (
          <p className="mt-1 text-xs text-slate-700 dark:text-slate-300">
            “{decision.reason}”
          </p>
        )}
      </div>
    </li>
  );
}

/**
 * What was actually approved.
 *
 * Shown because an approval that did not pin an amount is worth very little:
 * quantities stay editable until the bill is submitted, and this is the ceiling
 * the server holds the biller to.
 */
function ApprovedAmount({ request }: { request: ApprovalRequest }) {
  if (request.lines.length === 0) return null;

  const total = request.lines.reduce(
    (sum, line) => sum + line.quantity * line.unit_price * (1 + line.tax_rate),
    0,
  );

  // Worded by outcome. A declined request still carries the lines it was asked
  // for, and calling those "approved" would say the opposite of what happened.
  const heading =
    request.status === "approved"
      ? `Approved for ${money(total)}`
      : request.status === "pending"
        ? `Asking to bill ${money(total)}`
        : `Was asking to bill ${money(total)}`;

  return (
    <div className="mt-5 rounded-lg border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950/40">
      <p className="text-xs font-medium text-slate-900 dark:text-white">
        {heading} across {request.lines.length}{" "}
        {request.lines.length === 1 ? "line" : "lines"}
      </p>
      <ul className="mt-2 space-y-1">
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
      {request.status === "approved" && (
        <p className="mt-2 text-xs text-slate-500 dark:text-slate-500">
          A bill for more than this is refused, even if the order has room for
          it.
        </p>
      )}
    </div>
  );
}

function DecideBox({
  requestId,
  posts,
}: {
  requestId: string;
  posts: boolean;
}) {
  const decide = useDecideApproval();
  const [declining, setDeclining] = useState(false);
  const [reason, setReason] = useState("");

  if (!declining) {
    return (
      <div className="mt-5 space-y-3 border-t border-slate-200 pt-5 dark:border-slate-800">
        {posts && (
          <Alert variant="info">
            Approving this step records the goods receipt in Odoo for exactly
            these quantities. The stock movement is real from that moment and is
            not returned if the invoice is declined later.
          </Alert>
        )}
        <div className="flex flex-wrap gap-3">
        <Button
          onClick={() =>
            decide.mutate({ requestId, input: { approve: true } })
          }
          isLoading={decide.isPending}
        >
          {posts ? "Received — approve" : "Approve"}
        </Button>
        <Button variant="secondary" onClick={() => setDeclining(true)}>
          Decline…
        </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="mt-5 space-y-3 border-t border-slate-200 pt-5 dark:border-slate-800">
      <Alert variant="info">
        Declining sends this back to whoever asked, with your reason attached.
        The invoice returns to where it was — it is not rejected.
      </Alert>
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
          // A reason is required by the server too — this only saves the round
          // trip. "No" with no reason is not something anybody can act on.
          disabled={!reason.trim()}
          isLoading={decide.isPending}
          onClick={() =>
            decide.mutate(
              { requestId, input: { approve: false, reason } },
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
  );
}

/**
 * The escape hatch, kept deliberately plain and deliberately auditable.
 *
 * It exists because every approver on a rung can be deactivated at once, and
 * the alternative is somebody editing the database by hand — which leaves no
 * record that a payment bypassed its chain.
 */
function CancelBox({ requestId }: { requestId: string }) {
  const cancel = useCancelApproval();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");

  if (!open) {
    return (
      <div className="mt-5 border-t border-slate-200 pt-5 dark:border-slate-800">
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="text-xs text-slate-500 underline underline-offset-2 hover:text-slate-700 dark:text-slate-500 dark:hover:text-slate-300"
        >
          Pull this out of the chain…
        </button>
      </div>
    );
  }

  return (
    <div className="mt-5 space-y-3 border-t border-slate-200 pt-5 dark:border-slate-800">
      <Alert variant="error">
        This ends the chain without the remaining approvals. It is recorded
        against the request with your name and reason.
      </Alert>
      <input
        value={reason}
        onChange={(event) => setReason(event.target.value)}
        maxLength={2000}
        placeholder="Why is this being pulled out?"
        className="w-full rounded-lg border border-slate-300 bg-white p-3 text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-slate-300 dark:border-slate-700 dark:bg-slate-950 dark:text-white"
      />
      <div className="flex flex-wrap gap-3">
        <Button
          variant="danger"
          disabled={!reason.trim()}
          isLoading={cancel.isPending}
          onClick={() =>
            cancel.mutate(
              { requestId, reason },
              { onSuccess: () => setOpen(false) },
            )
          }
        >
          Pull out of the chain
        </Button>
        <Button variant="ghost" onClick={() => setOpen(false)}>
          Keep it
        </Button>
      </div>
    </div>
  );
}
