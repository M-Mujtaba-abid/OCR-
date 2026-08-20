import api from "@/service/api";
import type { ApiResponse } from "@/types/api.type";
import type {
  ApprovalChain,
  ApprovalRequest,
  AwaitingItem,
  DecideInput,
  InvoiceApproval,
  RequestApprovalInput,
  SaveChainInput,
} from "@/types/approval.type";

export const approvalService = {
  /* ----------------------------------------------------------- the policy */

  /** Every chain in the caller's company. Needs `approval.configure`. */
  chains: async (): Promise<ApprovalChain[]> => {
    const response = await api.get<ApiResponse<ApprovalChain[]>>(
      "/approvals/chains",
    );
    return response.data.data;
  },

  /**
   * Create a chain. Steps arrive as an ordered list and the server assigns
   * their positions from that order — sending 1, 2, 4 would otherwise create a
   * chain whose third rung can never be reached.
   */
  createChain: async (input: SaveChainInput): Promise<ApprovalChain> => {
    const response = await api.post<ApiResponse<ApprovalChain>>(
      "/approvals/chains",
      input,
    );
    return response.data.data;
  },

  /** Replace a chain's steps wholesale. Requests already running are
   * unaffected — each carries its own copy of the chain it started with. */
  updateChain: async (
    chainId: string,
    input: SaveChainInput,
  ): Promise<ApprovalChain> => {
    const response = await api.put<ApiResponse<ApprovalChain>>(
      `/approvals/chains/${chainId}`,
      input,
    );
    return response.data.data;
  },

  /**
   * The switch that turns approvals on for a company.
   *
   * From here every vendor bill needs its chain completed first, so this is a
   * deliberate act rather than a side effect of saving.
   */
  setChainActive: async (
    chainId: string,
    active: boolean,
  ): Promise<ApprovalChain> => {
    const response = await api.post<ApiResponse<ApprovalChain>>(
      `/approvals/chains/${chainId}/${active ? "activate" : "deactivate"}`,
    );
    return response.data.data;
  },

  /**
   * Remove a chain that is neither active nor used.
   *
   * The server refuses both cases with a 409 that says which — an active chain
   * because deleting it would stop gating bills as a side effect, a used one
   * because its requests are the record of who authorised a payment.
   */
  deleteChain: async (chainId: string): Promise<void> => {
    await api.delete(`/approvals/chains/${chainId}`);
  },

  /* ----------------------------------------------------------- the record */

  /** Requests waiting on the signed-in user. No permission gate: eligibility
   * comes from each request's own snapshot. */
  awaitingMe: async (): Promise<AwaitingItem[]> => {
    const response = await api.get<ApiResponse<AwaitingItem[]>>(
      "/approvals/awaiting-me",
    );
    return response.data.data;
  },

  /**
   * Where one invoice has got to, plus whether this company gates billing.
   *
   * `request` is the latest one whatever became of it — a declined request is
   * still the honest answer to "where did this get to" until somebody submits
   * another — and null when the invoice was never sent.
   */
  forInvoice: async (invoiceId: string): Promise<InvoiceApproval> => {
    const response = await api.get<ApiResponse<InvoiceApproval>>(
      `/invoices/${invoiceId}/approval`,
    );
    return response.data.data;
  },

  request: async (
    invoiceId: string,
    input: RequestApprovalInput,
  ): Promise<ApprovalRequest> => {
    const response = await api.post<ApiResponse<ApprovalRequest>>(
      `/invoices/${invoiceId}/request-approval`,
      input,
    );
    return response.data.data;
  },

  decide: async (
    requestId: string,
    input: DecideInput,
  ): Promise<ApprovalRequest> => {
    const response = await api.post<ApiResponse<ApprovalRequest>>(
      `/approvals/${requestId}/decide`,
      input,
    );
    return response.data.data;
  },

  cancel: async (
    requestId: string,
    reason: string,
  ): Promise<ApprovalRequest> => {
    const response = await api.post<ApiResponse<ApprovalRequest>>(
      `/approvals/${requestId}/cancel`,
      { reason },
    );
    return response.data.data;
  },
};
