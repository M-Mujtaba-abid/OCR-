"use client";

import { useState } from "react";

import { InvoiceTable } from "@/components/invoices/InvoiceTable";
import {
  useInvoiceQueue,
  useMyInvoices,
} from "@/hooks/invoice/useInvoices.hooks";
import { PAGE_SIZE } from "@/lib/env";
import type { InvoiceStatus } from "@/types/invoice.type";

interface Props {
  /** "mine" hits /invoices/my; "all" hits /invoices/admin/queue. */
  scope: "mine" | "all";
  canDelete?: boolean;
  /** Shows Match / Review. Admin-side only — a member has neither permission. */
  showPipeline?: boolean;
  emptyMessage: string;
}

/**
 * One invoice list.
 *
 * The two scopes are separate endpoints with separate permissions, so this
 * picks between them rather than sending a role along and letting the server
 * decide. That keeps the authorization question — which endpoint, which
 * permission — answerable by reading the route table.
 *
 * Both hooks are called unconditionally and one is disabled, because hooks
 * cannot be called conditionally. The disabled one never fires a request.
 */
export function InvoicesPanel({
  scope,
  canDelete = false,
  showPipeline = false,
  emptyMessage,
}: Props) {
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState<InvoiceStatus | "">("");

  const params = { page, pageSize: PAGE_SIZE, status: status || undefined };

  const mine = useMyInvoices(params);
  const queue = useInvoiceQueue(params);
  const query = scope === "mine" ? mine : queue;

  return (
    <InvoiceTable
      invoices={query.data?.items ?? []}
      pagination={query.data?.pagination ?? null}
      // isFetching, not isLoading: with keepPreviousData the old page stays on
      // screen during a refetch, and the table dims rather than blanking.
      loading={query.isLoading}
      refreshing={query.isFetching}
      showUploader={scope === "all"}
      canDelete={canDelete}
      showPipeline={showPipeline}
      status={status}
      onStatusChange={(next) => {
        setStatus(next);
        // Page 4 of "all" may not exist once the list is filtered down.
        setPage(1);
      }}
      onPageChange={setPage}
      onRefresh={() => void query.refetch()}
      emptyMessage={emptyMessage}
    />
  );
}
