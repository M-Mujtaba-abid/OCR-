"use client";

import { useState } from "react";

import { UsersPanel } from "@/components/admin/UsersPanel";
import { InvoicesPanel } from "@/components/invoices/InvoicesPanel";
import { statusLabel } from "@/components/invoices/InvoiceStatusBadge";
import { Badge } from "@/components/ui/Badge";
import { StatCard } from "@/components/ui/StatCard";
import { TabPanel, Tabs, type TabItem } from "@/components/ui/Tabs";
import { useAuth } from "@/hooks/auth/useAuth.hooks";
import { useAdminInvoiceStats } from "@/hooks/invoice/useInvoices.hooks";
import { useUserStats } from "@/hooks/user/useUsers.hooks";
import { ROLE_LABEL } from "@/lib/auth/roles";
import type { InvoiceStatus } from "@/types/invoice.type";

type TabId = "overview" | "invoices" | "users";

/** Pipeline statuses worth a card. The rest live in the table's filter. */
const HEADLINE_STATUSES: readonly InvoiceStatus[] = [
  "uploaded",
  "pending_review",
  "no_match",
  "ocr_failed",
  "confirmed",
  "pushed",
] as const;

export default function AdminPage() {
  const { user } = useAuth();
  const [tab, setTab] = useState<TabId>("overview");

  // Two independent queries rather than one combined fetch: they invalidate on
  // different events — an upload changes invoice counts, a role change changes
  // user counts — and separate keys mean each refetches only when its own data
  // actually moved.
  const { data: invoiceStats } = useAdminInvoiceStats();
  const { data: userStats } = useUserStats();

  if (!user) return null;

  const tabs: readonly TabItem<TabId>[] = [
    { id: "overview", label: "Overview" },
    { id: "invoices", label: "Invoices", badge: invoiceStats?.total },
    { id: "users", label: "Users", badge: userStats?.total },
  ];

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900 dark:text-white">
            Admin console
          </h1>
          <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
            {user.email}
          </p>
        </div>
        <Badge tone="accent">{ROLE_LABEL.admin}</Badge>
      </header>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="Needs action"
          value={invoiceStats?.open_count}
          tone="warning"
          hint="Awaiting review, failed, or unmatched"
        />
        <StatCard label="Invoices" value={invoiceStats?.total} />
        <StatCard label="Users" value={userStats?.total} />
        <StatCard
          label="Administrators"
          value={userStats?.by_role.admin}
          tone="accent"
        />
      </div>

      <div>
        <Tabs tabs={tabs} active={tab} onChange={setTab} label="Admin sections" />

        <TabPanel id="overview" active={tab === "overview"}>
          <div className="space-y-6">
            <section>
              <h2 className="text-sm font-semibold text-slate-900 dark:text-white">
                Invoice pipeline
              </h2>
              <div className="mt-3 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {HEADLINE_STATUSES.map((status) => (
                  <StatCard
                    key={status}
                    label={statusLabel(status)}
                    value={invoiceStats?.by_status[status]}
                    tone={
                      status === "pushed" || status === "confirmed"
                        ? "positive"
                        : status === "ocr_failed"
                          ? "negative"
                          : "warning"
                    }
                  />
                ))}
              </div>
            </section>

            <section>
              <h2 className="text-sm font-semibold text-slate-900 dark:text-white">
                Accounts
              </h2>
              <div className="mt-3 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <StatCard label="Active" value={userStats?.active} tone="positive" />
                <StatCard
                  label="Disabled"
                  value={userStats?.inactive}
                  tone={userStats?.inactive ? "negative" : "neutral"}
                />
                <StatCard label={ROLE_LABEL.member} value={userStats?.by_role.member} />
                <StatCard
                  label={ROLE_LABEL.manager}
                  value={userStats?.by_role.manager}
                  tone="warning"
                />
              </div>
            </section>
          </div>
        </TabPanel>

        <TabPanel id="invoices" active={tab === "invoices"}>
          <InvoicesPanel
            scope="all"
            canDelete
            showPipeline
            emptyMessage="No invoices have been uploaded yet."
          />
        </TabPanel>

        <TabPanel id="users" active={tab === "users"}>
          <UsersPanel adminCount={userStats?.by_role.admin ?? 0} />
        </TabPanel>
      </div>
    </div>
  );
}
