"use client";

import { useState } from "react";

import { AddCompanyForm } from "@/components/platform/AddCompanyForm";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { StatCard } from "@/components/ui/StatCard";
import { useAuth } from "@/hooks/auth/useAuth.hooks";
import {
  useCompanies,
  usePlatformStats,
  useSetCompanyActive,
} from "@/hooks/platform/usePlatform.hooks";
import { isPlatformOwner } from "@/lib/auth/roles";
import type { PlatformCompany } from "@/types/platform.type";

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

/**
 * The platform owner's console.
 *
 * Deliberately narrow. It lists companies, creates them, and switches them off
 * — and shows nothing from inside one, because the account viewing it holds no
 * permission that could read an invoice and belongs to no company that could
 * scope such a read.
 *
 * The counts here are billing-shaped rather than commercial: how many accounts
 * a company has says how big a tenant is without saying what they buy or from
 * whom.
 */
export default function PlatformPage() {
  const { user } = useAuth();
  const [adding, setAdding] = useState(false);

  const companies = useCompanies();
  const stats = usePlatformStats();
  const setActive = useSetCompanyActive();

  // A UX guard only — `canViewPath` already keeps company accounts out, and
  // the API refuses them regardless of what this renders.
  if (!user || !isPlatformOwner(user)) return null;

  const rows = companies.data ?? [];

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900 dark:text-white">
            Companies
          </h1>
          <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
            {user.email}
          </p>
        </div>
        <Badge tone="accent">Platform Owner</Badge>
      </header>

      <div className="grid gap-4 sm:grid-cols-3">
        <StatCard
          label="Companies"
          value={stats.data?.companies}
          hint="Every tenant on this platform"
        />
        <StatCard
          label="Active"
          value={stats.data?.active_companies}
          tone="positive"
          hint="Suspended companies cannot sign in"
        />
        <StatCard
          label="Accounts"
          value={stats.data?.users}
          hint="Across every company"
        />
      </div>

      {adding ? (
        <AddCompanyForm onDone={() => setAdding(false)} />
      ) : (
        <div className="flex justify-end">
          <Button onClick={() => setAdding(true)}>Add company</Button>
        </div>
      )}

      <div className="rounded-xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 p-4 dark:border-slate-800">
          <p className="text-sm font-medium text-slate-900 dark:text-white">
            {companies.data
              ? `${rows.length} compan${rows.length === 1 ? "y" : "ies"}`
              : companies.isLoading
                ? "Loading…"
                : "—"}
          </p>
          <Button
            variant="ghost"
            onClick={() => void companies.refetch()}
            disabled={companies.isFetching}
          >
            {companies.isFetching ? "Refreshing…" : "Refresh"}
          </Button>
        </div>

        {companies.isLoading ? (
          <p className="p-6 text-sm text-slate-600 dark:text-slate-400">Loading…</p>
        ) : companies.isError ? (
          <p className="p-6 text-sm text-red-700 dark:text-red-400">
            The company list could not be loaded.
          </p>
        ) : rows.length === 0 ? (
          <p className="p-6 text-sm text-slate-600 dark:text-slate-400">
            No companies yet. Add one to get started.
          </p>
        ) : (
          <div
            className={`transition-opacity ${companies.isFetching ? "opacity-60" : ""}`}
          >
            <table className="w-full table-fixed text-left text-sm">
              <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500 dark:border-slate-800 dark:text-slate-400">
                <tr>
                  <th className="w-[34%] px-4 py-3 font-medium">Company</th>
                  <th className="w-[20%] px-4 py-3 font-medium">People</th>
                  <th className="hidden w-[16%] px-4 py-3 font-medium md:table-cell">
                    Added
                  </th>
                  <th className="w-[30%] px-4 py-3 text-right font-medium">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
                {rows.map((company) => (
                  <CompanyRow
                    key={company.id}
                    company={company}
                    busy={
                      setActive.isPending &&
                      setActive.variables?.companyId === company.id
                    }
                    onToggle={(active) =>
                      setActive.mutate({ companyId: company.id, active })
                    }
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function CompanyRow({
  company,
  busy,
  onToggle,
}: {
  company: PlatformCompany;
  busy: boolean;
  onToggle: (active: boolean) => void;
}) {
  // A company nobody can sign into is the state worth spotting from a list —
  // it looks fine until somebody tries to use it.
  const stranded = company.is_active && company.active_user_count === 0;

  return (
    <tr className="align-top transition-colors hover:bg-slate-50 dark:hover:bg-slate-800/40">
      <td className="px-4 py-3">
        <p className="truncate font-medium text-slate-900 dark:text-white">
          {company.name}
        </p>
        <p className="truncate font-mono text-xs text-slate-500 dark:text-slate-400">
          {company.slug}
        </p>
        <div className="mt-1 flex flex-wrap gap-1">
          {!company.is_active && <Badge tone="negative">Suspended</Badge>}
          {company.odoo_configured ? (
            <Badge tone="positive">Odoo connected</Badge>
          ) : (
            <Badge tone="neutral">No Odoo</Badge>
          )}
        </div>
      </td>

      <td className="px-4 py-3">
        <p className="text-slate-900 dark:text-slate-100">
          {company.active_user_count} active
        </p>
        <p className="text-xs text-slate-500 dark:text-slate-400">
          {company.user_count} total ·{" "}
          {company.admin_count === 1
            ? "1 admin"
            : `${company.admin_count} admins`}
        </p>
        {stranded && (
          <p className="mt-1 text-xs text-amber-700 dark:text-amber-400">
            Nobody can sign in
          </p>
        )}
      </td>

      <td className="hidden whitespace-nowrap px-4 py-3 text-slate-600 dark:text-slate-400 md:table-cell">
        {formatDate(company.created_at)}
      </td>

      <td className="px-4 py-3">
        <div className="flex flex-wrap items-center justify-end gap-1.5">
          {company.is_active ? (
            <Button
              size="sm"
              variant="ghost"
              disabled={busy}
              isLoading={busy}
              onClick={() => onToggle(false)}
            >
              Suspend
            </Button>
          ) : (
            <Button
              size="sm"
              variant="secondary"
              disabled={busy}
              isLoading={busy}
              onClick={() => onToggle(true)}
            >
              Restore
            </Button>
          )}
        </div>
      </td>
    </tr>
  );
}
