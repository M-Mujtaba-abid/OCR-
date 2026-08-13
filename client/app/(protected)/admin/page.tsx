"use client";

import { useEffect, useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { useAuth } from "@/hooks/useAuth";
import { ApiError } from "@/lib/api/client";
import {
  changeUserRole,
  getUserStats,
  listUsers,
  setUserActive,
} from "@/lib/api/users";
import { ROLE_LABEL } from "@/lib/auth/roles";
import type { Paginated, User, UserRole, UserStats } from "@/types/auth";

const ROLES: readonly UserRole[] = ["member", "manager", "admin"] as const;
const PAGE_SIZE = 10;

const ROLE_TONE: Record<UserRole, "neutral" | "warning" | "accent"> = {
  member: "neutral",
  manager: "warning",
  admin: "accent",
};

export default function AdminDashboardPage() {
  const { user, can, refreshUser } = useAuth();

  const [stats, setStats] = useState<UserStats | null>(null);
  const [page, setPage] = useState(1);
  const [roleFilter, setRoleFilter] = useState<UserRole | "">("");
  const [result, setResult] = useState<Paginated<User> | null>(null);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // Which row is mid-request, so only that row's controls disable.
  const [busyId, setBusyId] = useState<string | null>(null);

  // Bumped after a mutation to re-run the fetch below. A counter rather than a
  // callable loader keeps the fetch entirely inside its effect, where the
  // cleanup below can cancel it.
  const [reloadKey, setReloadKey] = useState(0);

  const canManage = can("user.update");

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const [nextStats, nextPage] = await Promise.all([
          getUserStats(),
          listUsers({
            page,
            pageSize: PAGE_SIZE,
            role: roleFilter || undefined,
          }),
        ]);
        // Without this guard, switching pages quickly lets a slow first
        // response land after a fast second one and overwrite it.
        if (cancelled) return;
        setStats(nextStats);
        setResult(nextPage);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setError(
          err instanceof ApiError
            ? err.message
            : "Could not load the admin data. Please try again.",
        );
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [page, roleFilter, reloadKey]);

  async function mutate(userId: string, action: () => Promise<User>) {
    setBusyId(userId);
    setError(null);
    setNotice(null);
    try {
      const updated = await action();
      setNotice(
        `${updated.full_name?.trim() || updated.email} updated successfully.`,
      );
      // Refetch rather than patch local state: the stat cards change too, and
      // re-deriving them client-side would be a second copy of logic the
      // backend already owns.
      setReloadKey((key) => key + 1);
      // If the caller changed their own standing, pick up the new permissions
      // instead of continuing on a stale list.
      if (userId === user?.id) await refreshUser();
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "That action could not be completed.",
      );
    } finally {
      setBusyId(null);
    }
  }

  if (!user) return null;

  const adminCount = stats?.by_role.admin ?? 0;

  return (
    <div className="space-y-8">
      <header>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900 dark:text-white">
            Admin console
          </h1>
          <Badge tone="accent">Administrator</Badge>
        </div>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
          Signed in as {user.email}. Manage roles and account access here.
        </p>
      </header>

      {error && <Alert>{error}</Alert>}
      {notice && <Alert variant="success">{notice}</Alert>}

      {/* ------------------------------------------------------------ stats */}
      <section aria-labelledby="stats-heading">
        <h2 id="stats-heading" className="sr-only">
          User statistics
        </h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard label="Total users" value={stats?.total} loading={loading} />
          <StatCard label="Active" value={stats?.active} loading={loading} />
          <StatCard label="Verified" value={stats?.verified} loading={loading} />
          <StatCard label="Administrators" value={adminCount} loading={loading} />
        </div>

        <div className="mt-4 grid gap-4 sm:grid-cols-3">
          {ROLES.map((role) => (
            <StatCard
              key={role}
              label={ROLE_LABEL[role]}
              value={stats?.by_role[role]}
              loading={loading}
              tone={ROLE_TONE[role]}
            />
          ))}
        </div>
      </section>

      {/* ------------------------------------------------------------ users */}
      <section
        aria-labelledby="users-heading"
        className="rounded-xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900"
      >
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 p-5 dark:border-slate-800">
          <div>
            <h2
              id="users-heading"
              className="text-sm font-semibold text-slate-900 dark:text-white"
            >
              Users
            </h2>
            <p className="mt-0.5 text-sm text-slate-600 dark:text-slate-400">
              {result
                ? `${result.pagination.total} total`
                : loading
                  ? "Loading…"
                  : "—"}
            </p>
          </div>

          <label className="flex items-center gap-2 text-sm">
            <span className="text-slate-600 dark:text-slate-400">Filter</span>
            <select
              value={roleFilter}
              onChange={(event) => {
                setRoleFilter(event.target.value as UserRole | "");
                // Page 3 of "all users" may not exist once filtered down.
                setPage(1);
              }}
              className="rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm text-slate-900 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
            >
              <option value="">All roles</option>
              {ROLES.map((role) => (
                <option key={role} value={role}>
                  {ROLE_LABEL[role]}
                </option>
              ))}
            </select>
          </label>
        </div>

        {loading && !result ? (
          <p className="p-5 text-sm text-slate-600 dark:text-slate-400">
            Loading users…
          </p>
        ) : !result || result.items.length === 0 ? (
          <p className="p-5 text-sm text-slate-600 dark:text-slate-400">
            No users match this filter.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-left text-sm">
              <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500 dark:border-slate-800 dark:text-slate-400">
                <tr>
                  <th className="px-5 py-3 font-medium">User</th>
                  <th className="px-5 py-3 font-medium">Status</th>
                  <th className="px-5 py-3 font-medium">Role</th>
                  <th className="px-5 py-3 text-right font-medium">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
                {result.items.map((row) => {
                  const isSelf = row.id === user.id;
                  const isLastAdmin = row.role === "admin" && adminCount <= 1;
                  // Mirrors the backend's two guards exactly. The API rejects
                  // both regardless; disabling here means the user is not
                  // offered an action that is going to fail.
                  const locked = isSelf || isLastAdmin;
                  const busy = busyId === row.id;

                  return (
                    <tr key={row.id}>
                      <td className="px-5 py-4">
                        <p className="font-medium text-slate-900 dark:text-white">
                          {row.full_name?.trim() || "—"}
                          {isSelf && (
                            <span className="ml-2 text-xs font-normal text-slate-500">
                              (you)
                            </span>
                          )}
                        </p>
                        <p className="text-slate-600 dark:text-slate-400">
                          {row.email}
                        </p>
                      </td>

                      <td className="px-5 py-4">
                        <div className="flex flex-wrap gap-1.5">
                          <Badge tone={row.is_active ? "positive" : "negative"}>
                            {row.is_active ? "Active" : "Disabled"}
                          </Badge>
                          {!row.is_verified && <Badge tone="warning">Unverified</Badge>}
                        </div>
                      </td>

                      <td className="px-5 py-4">
                        {canManage ? (
                          <select
                            value={row.role}
                            disabled={locked || busy}
                            aria-label={`Role for ${row.email}`}
                            onChange={(event) =>
                              void mutate(row.id, () =>
                                changeUserRole(
                                  row.id,
                                  event.target.value as UserRole,
                                ),
                              )
                            }
                            className="rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm text-slate-900 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
                          >
                            {ROLES.map((role) => (
                              <option key={role} value={role}>
                                {ROLE_LABEL[role]}
                              </option>
                            ))}
                          </select>
                        ) : (
                          <Badge tone={ROLE_TONE[row.role]}>
                            {ROLE_LABEL[row.role]}
                          </Badge>
                        )}

                        {locked && canManage && (
                          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                            {isSelf
                              ? "You cannot change your own role"
                              : "Last administrator"}
                          </p>
                        )}
                      </td>

                      <td className="px-5 py-4 text-right">
                        {canManage && (
                          <Button
                            variant={row.is_active ? "secondary" : "primary"}
                            disabled={locked || busy}
                            isLoading={busy}
                            onClick={() =>
                              void mutate(row.id, () =>
                                setUserActive(row.id, !row.is_active),
                              )
                            }
                          >
                            {row.is_active ? "Disable" : "Enable"}
                          </Button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {result && result.pagination.pages > 1 && (
          <div className="flex items-center justify-between gap-3 border-t border-slate-200 p-4 dark:border-slate-800">
            <Button
              variant="secondary"
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              Previous
            </Button>
            <span className="text-sm text-slate-600 dark:text-slate-400">
              Page {result.pagination.page} of {result.pagination.pages}
            </span>
            <Button
              variant="secondary"
              disabled={page >= result.pagination.pages}
              onClick={() => setPage((p) => p + 1)}
            >
              Next
            </Button>
          </div>
        )}
      </section>
    </div>
  );
}

function StatCard({
  label,
  value,
  loading,
  tone = "neutral",
}: {
  label: string;
  value: number | undefined;
  loading: boolean;
  tone?: "neutral" | "warning" | "accent";
}) {
  const accents: Record<typeof tone, string> = {
    neutral: "text-slate-900 dark:text-white",
    warning: "text-amber-600 dark:text-amber-400",
    accent: "text-indigo-600 dark:text-indigo-400",
  };

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 dark:border-slate-800 dark:bg-slate-900">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
        {label}
      </p>
      <p className={`mt-2 text-2xl font-semibold tabular-nums ${accents[tone]}`}>
        {loading && value === undefined ? "—" : (value ?? 0)}
      </p>
    </div>
  );
}
