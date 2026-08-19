"use client";

import { useState } from "react";

import { OdooSettingsPanel } from "@/components/admin/OdooSettingsPanel";
import { UsersPanel } from "@/components/admin/UsersPanel";
import { PipelineBar } from "@/components/charts/PipelineBar";
import { StatusBreakdown } from "@/components/charts/StatusBreakdown";
import { TrendChart } from "@/components/charts/TrendChart";
import { BillHistoryPanel } from "@/components/invoices/BillHistoryPanel";
import { InvoicesPanel } from "@/components/invoices/InvoicesPanel";
import { Badge } from "@/components/ui/Badge";
import { StatCard } from "@/components/ui/StatCard";
import { TabPanel, Tabs, type TabItem } from "@/components/ui/Tabs";
import { useAuth } from "@/hooks/auth/useAuth.hooks";
import { useCompany } from "@/hooks/company/useCompany.hooks";
import {
  useAdminInvoiceStats,
  useInvoiceTrend,
} from "@/hooks/invoice/useInvoices.hooks";
import { useUserStats } from "@/hooks/user/useUsers.hooks";
import { ROLE_LABEL } from "@/lib/auth/roles";
import type { UserRole } from "@/types/user.type";

type TabId = "overview" | "invoices" | "users" | "history" | "settings";

/** Least to most privileged, so the shape of the org reads top to bottom. */
const ROLES: readonly UserRole[] = ["member", "manager", "admin"] as const;

export default function AdminPage() {
  const { user } = useAuth();
  const company = useCompany();
  const [tab, setTab] = useState<TabId>("overview");

  // Two independent queries rather than one combined fetch: they invalidate on
  // different events — an upload changes invoice counts, a role change changes
  // user counts — and separate keys mean each refetches only when its own data
  // actually moved.
  const { data: invoiceStats } = useAdminInvoiceStats();
  const { data: userStats } = useUserStats();
  const trend = useInvoiceTrend(14);

  if (!user) return null;

  // The billed count is read off the same stats query the card above uses,
  // rather than the history endpoint: the tab strip must not fire a paginated
  // request for a panel nobody has opened.
  const billed = invoiceStats?.by_status.pushed;

  const tabs: readonly TabItem<TabId>[] = [
    { id: "overview", label: "Overview" },
    { id: "invoices", label: "Invoices", badge: invoiceStats?.total },
    { id: "users", label: "Users", badge: userStats?.total },
    { id: "history", label: "History", badge: billed },
    { id: "settings", label: "Settings" },
  ];

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          {/* The company leads, because on a platform running several of them
              the first question a queue has to answer is whose it is. */}
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900 dark:text-white">
            {company.data?.name ?? "Admin Insights"}
          </h1>
          <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
            {company.data ? `Admin insights · ${user.email}` : user.email}
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
        {/* What was billed, not what was raised. An order created in Odoo is
            work started; a bill against one is work finished, and finished is
            the number a dashboard should lead with. The full history sits
            behind the tab of the same name. */}
        <StatCard
          label="Bills created"
          value={billed}
          tone="accent"
          hint="Vendor bills raised in Odoo, as drafts"
        />
        <StatCard
          label="Invoices"
          value={invoiceStats?.total}
          hint="Every invoice ever uploaded"
        />
        <StatCard
          label="Users"
          value={userStats?.total}
          hint={
            userStats
              ? `${userStats.by_role.admin} admin${userStats.by_role.admin === 1 ? "" : "s"}`
              : undefined
          }
        />
      </div>

      <div>
        <Tabs tabs={tabs} active={tab} onChange={setTab} label="Admin sections" />

        <TabPanel id="overview" active={tab === "overview"}>
          <div className="space-y-6">
            <Panel
              title="Invoice pipeline"
              subtitle="Where every uploaded invoice currently sits"
              aside={
                invoiceStats && (
                  <span className="text-2xl font-semibold text-slate-900 dark:text-white">
                    {invoiceStats.total}
                  </span>
                )
              }
            >
              <PipelineBar
                byStatus={invoiceStats?.by_status}
                total={invoiceStats?.total}
              />
            </Panel>

            <Panel
              title="Last 14 days"
              subtitle="Invoices arriving against invoices settled — a gap that stays open is a backlog"
            >
              <TrendChart points={trend.data?.points} loading={trend.isLoading} />
            </Panel>

            <div className="grid gap-6 lg:grid-cols-2">
              <Panel
                title="By status"
                subtitle="Every status, including the empty ones"
              >
                <StatusBreakdown byStatus={invoiceStats?.by_status} />
              </Panel>

              <Panel title="Accounts" subtitle="Who can sign in, and as what">
                <div className="grid gap-4 sm:grid-cols-2">
                  <StatCard label="Active" value={userStats?.active} tone="positive" />
                  <StatCard
                    label="Disabled"
                    value={userStats?.inactive}
                    tone={userStats?.inactive ? "negative" : "neutral"}
                  />
                </div>
                <ul className="mt-4 space-y-2">
                  {ROLES.map((role) => (
                    <RoleRow
                      key={role}
                      label={ROLE_LABEL[role]}
                      count={userStats?.by_role[role]}
                      total={userStats?.total}
                    />
                  ))}
                </ul>
              </Panel>
            </div>
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

        <TabPanel id="history" active={tab === "history"}>
          <BillHistoryPanel />
        </TabPanel>

        <TabPanel id="settings" active={tab === "settings"}>
          <OdooSettingsPanel />
        </TabPanel>
      </div>
    </div>
  );
}

/**
 * One titled surface.
 *
 * The dashboard used to be an undifferentiated field of cards, where a headline
 * number and a pipeline stage looked equally important. Grouping into panels is
 * what lets the eye skip a whole section rather than reading every tile.
 */
function Panel({
  title,
  subtitle,
  aside,
  children,
}: {
  title: string;
  subtitle?: string;
  aside?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-6 dark:border-slate-800 dark:bg-slate-900">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-900 dark:text-white">
            {title}
          </h2>
          {subtitle && (
            <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
              {subtitle}
            </p>
          )}
        </div>
        {aside}
      </div>
      <div className="mt-5">{children}</div>
    </section>
  );
}

/** A role and its share of the team, as a meter rather than a fourth tile. */
function RoleRow({
  label,
  count,
  total,
}: {
  label: string;
  count: number | undefined;
  total: number | undefined;
}) {
  const share = count !== undefined && total ? (count / total) * 100 : 0;

  return (
    <li className="flex items-center gap-3">
      <span className="w-24 shrink-0 text-sm text-slate-700 dark:text-slate-300">
        {label}
      </span>
      <span className="h-1.5 flex-1 rounded-full bg-slate-100 dark:bg-slate-800">
        {/* One hue, varying length: this is magnitude, not identity, so the
            roles do not each get their own colour. */}
        <span
          className="block h-full rounded-full bg-indigo-600 dark:bg-indigo-500"
          style={{ width: `${share}%` }}
        />
      </span>
      <span className="w-6 text-right text-sm tabular-nums font-medium text-slate-900 dark:text-white">
        {count === undefined ? "—" : count}
      </span>
    </li>
  );
}
