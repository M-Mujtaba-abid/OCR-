"use client";

import { useState } from "react";
import Link from "next/link";

import { InvoicesPanel } from "@/components/invoices/InvoicesPanel";
import { InvoiceUpload } from "@/components/invoices/InvoiceUpload";
import { statusLabel } from "@/components/invoices/InvoiceStatusBadge";
import { Alert } from "@/components/ui/Alert";
import { Badge, Field } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { StatCard } from "@/components/ui/StatCard";
import { TabPanel, Tabs, type TabItem } from "@/components/ui/Tabs";
import { useAuth, useLogout, useLogoutAll } from "@/hooks/auth/useAuth.hooks";
import { useMyInvoiceStats } from "@/hooks/invoice/useInvoices.hooks";
import { ROLE_LABEL, isAdmin } from "@/lib/auth/roles";

type TabId = "upload" | "invoices" | "account";

export default function DashboardPage() {
  const { user } = useAuth();
  const [tab, setTab] = useState<TabId>("upload");

  // Cached, so switching tabs does not refetch. The upload mutation invalidates
  // the `invoices` key, which is what makes the counts update after an upload
  // without any manual wiring here.
  const { data: stats } = useMyInvoiceStats();

  const logout = useLogout();
  const logoutAll = useLogoutAll();

  if (!user) return null;

  const displayName = user.full_name?.trim() || user.email;

  const tabs: readonly TabItem<TabId>[] = [
    { id: "upload", label: "Upload" },
    { id: "invoices", label: "My invoices", badge: stats?.total },
    { id: "account", label: "Account" },
  ];

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900 dark:text-white">
            {displayName}
          </h1>
          <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
            {user.email}
          </p>
        </div>
        <Badge tone={user.role === "manager" ? "warning" : "neutral"}>
          {ROLE_LABEL[user.role]}
        </Badge>
      </header>

      {isAdmin(user) && (
        <Alert variant="info">
          <Link href="/admin" className="font-medium underline underline-offset-4">
            Open the admin console
          </Link>{" "}
          to review every invoice and manage users.
        </Alert>
      )}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Total" value={stats?.total} />
        <StatCard
          label={statusLabel("uploaded")}
          value={stats?.by_status.uploaded}
          tone="warning"
        />
        <StatCard
          label={statusLabel("pending_review")}
          value={stats?.by_status.pending_review}
          tone="warning"
        />
        <StatCard
          label={statusLabel("pushed")}
          value={stats?.by_status.pushed}
          tone="positive"
        />
      </div>

      <div>
        <Tabs tabs={tabs} active={tab} onChange={setTab} label="Dashboard sections" />

        <TabPanel id="upload" active={tab === "upload"}>
          <div className="rounded-xl border border-slate-200 bg-white p-6 dark:border-slate-800 dark:bg-slate-900">
            <InvoiceUpload
              // Move to the list so the result of the action is visible, rather
              // than leaving the user staring at an empty upload form.
              onUploaded={() => setTab("invoices")}
            />
          </div>
        </TabPanel>

        <TabPanel id="invoices" active={tab === "invoices"}>
          <InvoicesPanel
            scope="mine"
            canDelete
            emptyMessage="Nothing uploaded yet. Use the Upload tab to add invoices."
          />
        </TabPanel>

        <TabPanel id="account" active={tab === "account"}>
          <div className="space-y-6">
            <section className="rounded-xl border border-slate-200 bg-white p-6 dark:border-slate-800 dark:bg-slate-900">
              <h2 className="text-sm font-semibold text-slate-900 dark:text-white">
                Profile
              </h2>
              <dl className="mt-4 grid gap-4 sm:grid-cols-2">
                <Field label="Email" value={user.email} />
                <Field label="Name" value={user.full_name ?? "—"} />
                <Field label="Role" value={<Badge>{ROLE_LABEL[user.role]}</Badge>} />
                <Field
                  label="Account status"
                  value={
                    <Badge tone={user.is_active ? "positive" : "negative"}>
                      {user.is_active ? "Active" : "Disabled"}
                    </Badge>
                  }
                />
                <Field
                  label="Verification"
                  value={
                    <Badge tone={user.is_verified ? "positive" : "warning"}>
                      {user.is_verified ? "Verified" : "Unverified"}
                    </Badge>
                  }
                />
                <Field
                  label="Member since"
                  value={new Date(user.created_at).toLocaleDateString()}
                />
              </dl>
            </section>

            <section className="rounded-xl border border-slate-200 bg-white p-6 dark:border-slate-800 dark:bg-slate-900">
              <h2 className="text-sm font-semibold text-slate-900 dark:text-white">
                Sessions
              </h2>
              <div className="mt-4 flex flex-col gap-3 sm:flex-row">
                <Button
                  variant="secondary"
                  onClick={() => logout.mutate()}
                  isLoading={logout.isPending}
                  disabled={logout.isPending || logoutAll.isPending}
                >
                  Sign out
                </Button>
                <Button
                  variant="danger"
                  onClick={() => logoutAll.mutate()}
                  isLoading={logoutAll.isPending}
                  disabled={logout.isPending || logoutAll.isPending}
                >
                  Sign out everywhere
                </Button>
              </div>
            </section>
          </div>
        </TabPanel>
      </div>
    </div>
  );
}
