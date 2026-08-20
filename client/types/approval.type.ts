/** Approval chain payloads, mirroring server/app/schemas/approval.py. */

import type { InvoiceStatus, InvoiceUploader } from "@/types/invoice.type";

export type ApprovalRequestStatus =
  | "pending"
  | "approved"
  | "declined"
  /** An admin pulled the invoice out of a chain nobody could satisfy. */
  | "cancelled";

export type ApprovalDecisionValue = "approved" | "declined" | "cancelled";

export interface ApprovalDecision {
  position: number;
  decision: ApprovalDecisionValue;
  reason: string | null;
  decided_at: string;
  decided_by: string | null;
}

/**
 * One line as approved, straight off the request's snapshot.
 *
 * Rendered from the snapshot and never from a fresh bill preview — an approver
 * looking at live Odoo figures would be signing off numbers that are not the
 * ones the request is actually capped at.
 */
export interface ApprovalLine {
  po_line_id: number;
  quantity: number;
  description: string;
  unit_price: number;
  tax_rate: number;
}

/** One rung and what became of it. `decision` is null while it waits. */
export interface ApprovalStepProgress {
  position: number;
  name: string;
  approver_user_ids: string[];
  /** Approving this rung posts the goods receipt in Odoo. Irreversible. */
  records_receipt: boolean;
  decision: ApprovalDecision | null;
  is_current: boolean;
}

/**
 * What Odoo did when a receiving step was approved.
 *
 * `picking_name` is the handle a person reconciles against Odoo with, which is
 * why it is worth surfacing rather than keeping as an internal id.
 */
export interface ApprovalReceipt {
  picking_id: number;
  picking_name: string;
  backorders: string[];
  received: Record<string, number>;
  position: number;
  recorded_by: string;
  recorded_at: string;
}

export interface ApprovalRequest {
  id: string;
  invoice_id: string;
  status: ApprovalRequestStatus;
  current_position: number;
  /** `max(invoice_total, proposed_total)` when the request began. */
  amount_at_request: string | null;
  /** Where the invoice goes back to when the chain ends, either way. */
  status_before_approval: InvoiceStatus;
  allow_self_approval: boolean;
  requested_by: string | null;
  requester: InvoiceUploader | null;
  created_at: string;
  /** When the CURRENT step started waiting, not the age of the request. */
  current_step_since: string;
  /**
   * Whole days the current step has been waiting.
   *
   * Computed server-side rather than from `current_step_since` here: a value
   * derived from the browser clock during render is unstable across re-renders
   * and wrong on a machine whose time is off. It is also the same figure the
   * overdue reminder quotes, so the screen and the notification agree.
   */
  waiting_days: number;
  steps: ApprovalStepProgress[];
  lines: ApprovalLine[];
  /** The Odoo order these lines belong to. */
  po_id: number | null;
  /** Null until a receiving step is approved. */
  receipt: ApprovalReceipt | null;
}

/**
 * Everything the review screen needs about one invoice's approval, at once.
 *
 * `chain_active` travels with the request because the client that most needs it
 * cannot get it any other way — reading the chain list takes
 * `approval.configure`, which a manager does not hold, and a manager with no
 * request yet is exactly who needs telling that a chain exists.
 */
export interface InvoiceApproval {
  chain_active: boolean;
  chain_name: string | null;
  request: ApprovalRequest | null;
}

/** One row in the "Awaiting you" queue. */
export interface AwaitingItem {
  request: ApprovalRequest;
  invoice_id: string;
  file_name: string;
  vendor: string | null;
  invoice_no: string | null;
  step_name: string;
  step_position: number;
}

export interface ApprovalStep {
  position: number;
  name: string;
  approver_user_ids: string[];
  records_receipt: boolean;
}

export interface ApprovalChain {
  id: string;
  name: string;
  is_active: boolean;
  allow_self_approval: boolean;
  steps: ApprovalStep[];
  created_at: string;
  updated_at: string;
}

export interface SaveChainInput {
  name: string;
  steps: {
    name: string;
    approver_user_ids: string[];
    records_receipt?: boolean;
  }[];
  allow_self_approval?: boolean;
  is_active?: boolean;
}

/**
 * The same `po_id` and `lines` a create-bill call carries, deliberately.
 *
 * The server prices them against Odoo and freezes them onto the request, so
 * what each approver sees and what the biller may later submit are the same
 * numbers.
 */
export interface RequestApprovalInput {
  po_id: number;
  lines: { po_line_id: number; quantity: number }[];
}

export interface DecideInput {
  approve: boolean;
  /** Required when declining — "no" with no reason is not actionable. */
  reason?: string | null;
}
