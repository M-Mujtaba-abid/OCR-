"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "react-hot-toast";

import { NOTIFICATION_POLL_MS } from "@/lib/env";
import { queryKeys } from "@/lib/query-keys";
import { ApiError } from "@/service/api";
import { approvalService } from "@/service/approvalService/approval.service";
import type {
  DecideInput,
  RequestApprovalInput,
  SaveChainInput,
} from "@/types/approval.type";

/**
 * Everything a decision touches, in one place.
 *
 * A decision changes four caches at once — the request itself, the invoice it
 * gates, the deciding user's queue, and the pipeline counts — and forgetting
 * one of them is how a screen ends up showing an approval that already
 * happened. Naming them together is what stops the list drifting.
 */
function invalidateAfterDecision(
  queryClient: ReturnType<typeof useQueryClient>,
  invoiceId?: string,
) {
  void queryClient.invalidateQueries({ queryKey: queryKeys.approvals.awaiting });
  if (invoiceId) {
    void queryClient.invalidateQueries({
      queryKey: queryKeys.approvals.forInvoice(invoiceId),
    });
    void queryClient.invalidateQueries({
      queryKey: queryKeys.invoices.detail(invoiceId),
    });
  }
  // The invoice's status moved, so every list and the pipeline chart with it.
  void queryClient.invalidateQueries({ queryKey: queryKeys.invoices.lists });
  void queryClient.invalidateQueries({ queryKey: queryKeys.invoices.adminStats });
  // Somebody was just told it is their turn, or that theirs came back.
  void queryClient.invalidateQueries({ queryKey: queryKeys.notifications.all });
}

/* ------------------------------------------------------------- the policy */

export function useApprovalChains(enabled = true) {
  return useQuery({
    queryKey: queryKeys.approvals.chains,
    queryFn: () => approvalService.chains(),
    enabled,
    // Policy changes when an admin edits it, not while somebody watches.
    staleTime: 5 * 60_000,
  });
}

export function useSaveChain() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      chainId,
      input,
    }: {
      chainId?: string;
      input: SaveChainInput;
    }) =>
      chainId
        ? approvalService.updateChain(chainId, input)
        : approvalService.createChain(input),
    onSuccess: (chain) => {
      toast.success(`Saved ${chain.name}`);
      void queryClient.invalidateQueries({ queryKey: queryKeys.approvals.all });
    },
    onError: (error: ApiError) => {
      // 422 names the approvers who are not active users of this company — the
      // one failure an admin can actually fix from the screen they are on.
      toast.error(error.message || "Could not save the chain");
    },
  });
}

export function useSetChainActive() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ chainId, active }: { chainId: string; active: boolean }) =>
      approvalService.setChainActive(chainId, active),
    onSuccess: (chain) => {
      toast.success(
        chain.is_active
          ? `${chain.name} now gates every vendor bill`
          : `${chain.name} no longer gates vendor bills`,
      );
      void queryClient.invalidateQueries({ queryKey: queryKeys.approvals.all });
      // Billing just became possible or impossible for every open invoice.
      void queryClient.invalidateQueries({ queryKey: queryKeys.invoices.all });
    },
    onError: (error: ApiError) => {
      toast.error(error.message || "Could not change the chain");
    },
  });
}

export function useDeleteChain() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (chainId: string) => approvalService.deleteChain(chainId),
    onSuccess: () => {
      toast.success("Chain deleted");
      void queryClient.invalidateQueries({ queryKey: queryKeys.approvals.all });
    },
    onError: (error: ApiError) => {
      // 409 says which of the two refusals it is, and both are worth reading.
      toast.error(error.message || "Could not delete the chain");
    },
  });
}

/* ------------------------------------------------------------- the record */

/**
 * Requests waiting on the signed-in user.
 *
 * Polled, unlike most lists here: whose turn it is changes because somebody
 * else acted, so there is no local event to invalidate on. The same reasoning —
 * and the same interval — as the notification bell.
 */
export function useAwaitingMe(
  enabled = true,
  intervalMs = NOTIFICATION_POLL_MS,
) {
  return useQuery({
    queryKey: queryKeys.approvals.awaiting,
    queryFn: () => approvalService.awaitingMe(),
    enabled,
    staleTime: 15_000,
    // Caller's choice, because the two callers want different things from the
    // same data. The Approvals page is being read right now and should feel
    // live; the header badge is mounted on every page for every user and only
    // has to be roughly right. One cache entry serves both — TanStack takes the
    // shortest interval among live observers, so opening the page speeds the
    // badge up too, and closing it lets both settle down.
    refetchInterval: intervalMs,
    // Off deliberately, matching the bell: a background tab should not keep
    // polling a queue nobody is looking at.
    refetchIntervalInBackground: false,
    // And on for the same reason the bell has it: this list only ever changes
    // because somebody else acted, so the instant a person looks at the tab
    // again is the instant a stale answer is most obvious. Still bounded by
    // staleTime, so a quick alt-tab costs nothing.
    refetchOnWindowFocus: true,
  });
}

/** Where one invoice has got to. Null when it was never sent for approval. */
export function useInvoiceApproval(invoiceId: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.approvals.forInvoice(invoiceId),
    queryFn: () => approvalService.forInvoice(invoiceId),
    enabled: enabled && Boolean(invoiceId),
    staleTime: 15_000,
  });
}

export function useRequestApproval() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      invoiceId,
      input,
    }: {
      invoiceId: string;
      input: RequestApprovalInput;
    }) => approvalService.request(invoiceId, input),
    onSuccess: (request) => {
      toast.success("Sent for approval");
      invalidateAfterDecision(queryClient, request.invoice_id);
    },
    onError: (error: ApiError) => {
      // 409 carries the reason worth reading: no active chain, one already
      // running, or a rung only the requester could ever decide.
      toast.error(error.message || "Could not send this for approval");
    },
  });
}

export function useDecideApproval() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      requestId,
      input,
    }: {
      requestId: string;
      input: DecideInput;
    }) => approvalService.decide(requestId, input),
    onSuccess: (request, variables) => {
      if (!variables.input.approve) {
        toast.success("Sent back with your reason");
      } else if (request.status === "approved") {
        toast.success("Approved — this invoice can now be billed");
      } else {
        toast.success(`Approved. Now with step ${request.current_position}.`);
      }
      invalidateAfterDecision(queryClient, request.invoice_id);
    },
    onError: (error: ApiError) => {
      // 403 means it is not your turn; 409 means somebody else got there first.
      toast.error(error.message || "Could not record your decision");
    },
  });
}

export function useCancelApproval() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ requestId, reason }: { requestId: string; reason: string }) =>
      approvalService.cancel(requestId, reason),
    onSuccess: (request) => {
      toast.success("Pulled out of the approval chain");
      invalidateAfterDecision(queryClient, request.invoice_id);
    },
    onError: (error: ApiError) => {
      toast.error(error.message || "Could not cancel the request");
    },
  });
}
