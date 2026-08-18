"use client";

import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { toast } from "react-hot-toast";

import { POLL_MS } from "@/lib/env";
import { queryKeys } from "@/lib/query-keys";
import { ApiError } from "@/service/api";
import { invoiceService } from "@/service/invoiceService/invoice.service";
import type { Paginated } from "@/types/api.type";
import {
  TRANSIENT_STATUSES,
  type CreatePoInput,
  type Invoice,
  type InvoiceListParams,
  type UploadInput,
  type UploadResult,
} from "@/types/invoice.type";

/**
 * The limits the server enforces — size, count, accepted types.
 *
 * Long-lived on purpose: these change when someone edits an environment
 * variable, not during a session. `staleTime: Infinity` means one fetch per
 * page load and no refetch storm behind an upload screen.
 */
export function useAppConfig() {
  return useQuery({
    queryKey: queryKeys.config,
    queryFn: () => invoiceService.config(),
    staleTime: Infinity,
  });
}

/**
 * Refetch only while a row is mid-pipeline.
 *
 * A plain `refetchInterval: 3000` would poll a finished queue forever — one
 * request every three seconds, per open tab, for data that cannot change. This
 * returns `false` the moment nothing is in flight, so polling costs nothing at
 * rest and the list still updates itself while OCR or matching runs.
 */
function pollWhileWorking(data: Paginated<Invoice> | undefined): number | false {
  if (!data) return false;
  return data.items.some((invoice) => TRANSIENT_STATUSES.has(invoice.status))
    ? POLL_MS
    : false;
}

/**
 * The tables and the counters above them.
 *
 * Deliberately narrower than the whole `invoices` prefix: that also matches the
 * detail query — which the mutations below write directly, from the response
 * the server already gave them — and the purchase-order preview, which costs a
 * partner search plus one product search per line and has no business being
 * discarded because an unrelated invoice was rejected.
 *
 * Both stat queries go, not one: which is mounted depends on the caller's role,
 * and a count that disagrees with the table under it is the bug this prevents.
 */
function invalidateInvoiceLists(queryClient: QueryClient): void {
  void queryClient.invalidateQueries({ queryKey: queryKeys.invoices.lists });
  void queryClient.invalidateQueries({ queryKey: queryKeys.invoices.myStats });
  void queryClient.invalidateQueries({ queryKey: queryKeys.invoices.adminStats });
}

/**
 * The caller's own uploads.
 *
 * `keepPreviousData` is what stops the table blanking out when the page or
 * filter changes: the previous page stays on screen, dimmed by `isFetching`,
 * until the next one arrives. Without it every pagination click flashes an
 * empty table.
 */
export function useMyInvoices(params: InvoiceListParams = {}) {
  return useQuery({
    queryKey: queryKeys.invoices.mine(params),
    queryFn: () => invoiceService.listMine(params),
    placeholderData: keepPreviousData,
    refetchInterval: (query) => pollWhileWorking(query.state.data),
    // Freshly uploaded rows change state within seconds, so the default
    // one-minute staleTime would show a stale status on every remount.
    staleTime: 0,
  });
}

export function useMyInvoiceStats() {
  return useQuery({
    queryKey: queryKeys.invoices.myStats,
    queryFn: () => invoiceService.myStats(),
  });
}

/** Everybody's uploads. Requires invoice.read.all — 403 otherwise. */
export function useInvoiceQueue(params: InvoiceListParams = {}) {
  return useQuery({
    queryKey: queryKeys.invoices.queue(params),
    queryFn: () => invoiceService.listAll(params),
    placeholderData: keepPreviousData,
    refetchInterval: (query) => pollWhileWorking(query.state.data),
    staleTime: 0,
  });
}

export function useAdminInvoiceStats() {
  return useQuery({
    queryKey: queryKeys.invoices.adminStats,
    queryFn: () => invoiceService.adminStats(),
  });
}

/**
 * Daily arrivals and reviews for the dashboard.
 *
 * A day-grained aggregate cannot change meaningfully between two glances at
 * the page, so this is the one invoice query with a generous staleTime — and
 * it is not in the `lists` prefix, so a status change does not re-run the
 * aggregation.
 */
export function useInvoiceTrend(days = 14) {
  return useQuery({
    queryKey: queryKeys.invoices.trend(days),
    queryFn: () => invoiceService.adminTrend(days),
    staleTime: 5 * 60_000,
  });
}

export function useInvoice(invoiceId: string | null) {
  return useQuery({
    queryKey: queryKeys.invoices.detail(invoiceId ?? ""),
    queryFn: () => invoiceService.getById(invoiceId as string),
    // Skips the query entirely rather than firing one with an empty id.
    enabled: Boolean(invoiceId),
    refetchInterval: (query) =>
      query.state.data && TRANSIENT_STATUSES.has(query.state.data.status)
        ? POLL_MS
        : false,
    // Fresh only for as long as it can still change. A confirmed, rejected or
    // po_created invoice is finished — refetching it on every navigation back
    // is a request that cannot return anything new. While OCR or matching runs
    // the opposite holds, and zero is what makes the polling above meaningful.
    staleTime: (query) =>
      query.state.data && TRANSIENT_STATUSES.has(query.state.data.status)
        ? 0
        : 5 * 60_000,
  });
}

/* -------------------------------------------------------------------------
 * Pipeline
 *
 * These POST and return 202. The result arrives through the polling queries
 * above, not through the mutation — so `onSuccess` invalidates and gets out of
 * the way rather than pretending to have an answer.
 * ---------------------------------------------------------------------- */

export function useRunOcr() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (invoiceId: string) => invoiceService.runOcr(invoiceId),
    onSuccess: (_data, invoiceId) => {
      toast.success("Reading the document…");
      // 202: the work has not happened yet, so there is nothing to write into
      // the cache. Invalidate and let the polling queries report the outcome.
      invalidateInvoiceLists(queryClient);
      void queryClient.invalidateQueries({
        queryKey: queryKeys.invoices.detail(invoiceId),
      });
    },
    onError: (error: ApiError) => {
      toast.error(error.message || "Could not start extraction");
    },
  });
}

export function useRunMatching() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (invoiceId: string) => invoiceService.runMatching(invoiceId),
    onSuccess: (_data, invoiceId) => {
      toast.success("Matching against Odoo purchase orders…");
      invalidateInvoiceLists(queryClient);
      void queryClient.invalidateQueries({
        queryKey: queryKeys.invoices.detail(invoiceId),
      });
    },
    onError: (error: ApiError) => {
      // 409 means extraction has not finished; 503 means Odoo is unconfigured.
      // The backend's message says which, so it is shown verbatim.
      toast.error(error.message || "Could not start matching");
    },
  });
}

export function useConfirmMatch() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ invoiceId, poId }: { invoiceId: string; poId: number }) =>
      invoiceService.confirmMatch(invoiceId, poId),
    onSuccess: (invoice) => {
      toast.success(
        invoice.status === "corrected"
          ? `Overridden to ${invoice.matched_po_name}`
          : `Confirmed against ${invoice.matched_po_name}`,
      );
      // The response IS the new detail. Invalidating instead would refetch —
      // over the network, with a flicker — data that arrived a millisecond ago.
      queryClient.setQueryData(queryKeys.invoices.detail(invoice.id), invoice);
      invalidateInvoiceLists(queryClient);
    },
    onError: (error: ApiError) => {
      toast.error(error.message || "Could not confirm that match");
    },
  });
}

/**
 * The Odoo resolution for a would-be purchase order.
 *
 * `enabled` rather than eager: it costs several Odoo searches, one per invoice
 * line, so it runs when the reviewer opens the panel and not on every visit to
 * a review screen.
 */
export function usePoPreview(invoiceId: string, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.invoices.poPreview(invoiceId),
    queryFn: () => invoiceService.poPreview(invoiceId),
    enabled: enabled && Boolean(invoiceId),
    // The catalogue does not change mid-review, and re-resolving would move
    // the dropdown under the reviewer's cursor.
    staleTime: 5 * 60_000,
    // Kept far longer than the default five minutes. This is the most
    // expensive query in the app — a partner search plus one product search
    // per line — and a reviewer who closes the panel, deals with something
    // else and comes back should not pay for it twice.
    gcTime: 30 * 60_000,
  });
}

export function useCreatePo() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      invoiceId,
      input,
    }: {
      invoiceId: string;
      input: CreatePoInput;
    }) => invoiceService.createPo(invoiceId, input),
    onSuccess: (invoice) => {
      toast.success(`Created ${invoice.matched_po_name} in Odoo as a draft`);
      queryClient.setQueryData(queryKeys.invoices.detail(invoice.id), invoice);
      invalidateInvoiceLists(queryClient);
    },
    onError: (error: ApiError) => {
      // 409 carries the readable reason — an unchosen line, a product archived
      // since the preview, or Odoo refusing the write.
      toast.error(error.message || "Could not create the purchase order");
    },
  });
}

export function useRejectInvoice() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ invoiceId, reason }: { invoiceId: string; reason: string }) =>
      invoiceService.reject(invoiceId, reason),
    onSuccess: (invoice) => {
      toast.success("Invoice rejected");
      queryClient.setQueryData(queryKeys.invoices.detail(invoice.id), invoice);
      invalidateInvoiceLists(queryClient);
    },
    onError: (error: ApiError) => {
      toast.error(error.message || "Could not reject that invoice");
    },
  });
}

/**
 * Upload, then invalidate everything invoice-shaped.
 *
 * Invalidating the `invoices` prefix catches both lists and both stat queries
 * in one call, because TanStack matches keys by prefix. Listing them
 * individually is how one gets forgotten and the count stops matching the table.
 */
export function useUploadInvoices() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (input: UploadInput) => invoiceService.upload(input),

    onSuccess: (result: UploadResult) => {
      if (result.rejected.length > 0) {
        // The server sniffs file contents, so it can refuse a file the browser
        // thought was fine. Name each one instead of showing a bare count.
        toast(
          `${result.uploaded.length} uploaded, ${result.rejected.length} rejected:\n` +
            result.rejected.map((r) => `• ${r.file_name} — ${r.reason}`).join("\n"),
          { icon: "⚠️", duration: 10_000 },
        );
      } else {
        toast.success(
          `${result.uploaded.length} invoice${result.uploaded.length === 1 ? "" : "s"} uploaded`,
        );
      }

      void queryClient.invalidateQueries({ queryKey: queryKeys.invoices.all });
    },

    onError: (error: ApiError) => {
      toast.error(
        // The request id is what makes a support conversation possible at all —
        // without it the matching server log cannot be found.
        error.requestId
          ? `${error.message}\nReference: ${error.requestId}`
          : error.message || "Upload failed",
      );
    },
  });
}

/**
 * Open the stored PDF.
 *
 * A mutation rather than a query on purpose: the URL is signed and expires in
 * minutes, so it must be minted at click time. Caching it in a query would
 * hand out a dead link to anyone who clicked a minute later.
 */
export function useOpenInvoiceFile() {
  return useMutation({
    mutationFn: (invoiceId: string) => invoiceService.getFileLink(invoiceId),
    onSuccess: (link) => {
      const opened = window.open(link.url, "_blank", "noopener,noreferrer");
      // A popup blocker returns null. Silently doing nothing here is the
      // "I clicked and nothing happened" failure worth avoiding.
      if (!opened) {
        toast("Popup blocked — allow popups for this site to open files.", {
          icon: "⚠️",
        });
      }
    },
    onError: (error: ApiError) => {
      toast.error(error.message || "Could not open that file");
    },
  });
}

export function useDeleteInvoice() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (invoice: Invoice) => invoiceService.remove(invoice.id),
    onSuccess: (_data, invoice) => {
      toast.success(`${invoice.file_name} deleted`);
      // Dropped, not invalidated: invalidating would refetch a row that no
      // longer exists, and a cached detail is how the back button shows a
      // deleted invoice as though it were still there.
      queryClient.removeQueries({ queryKey: queryKeys.invoices.detail(invoice.id) });
      queryClient.removeQueries({ queryKey: queryKeys.invoices.poPreview(invoice.id) });
      invalidateInvoiceLists(queryClient);
    },
    onError: (error: ApiError) => {
      toast.error(error.message || "Could not delete that invoice");
    },
  });
}
