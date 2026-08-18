"use client";

import { useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { useAuth } from "@/hooks/auth/useAuth.hooks";
import {
  useChangeUserRole,
  useSetUserActive,
  useUsers,
} from "@/hooks/user/useUsers.hooks";
import { PAGE_SIZE } from "@/lib/env";
import { ROLE_LABEL } from "@/lib/auth/roles";
import type { UserRole } from "@/types/user.type";

const ROLES: readonly UserRole[] = ["member", "manager", "admin"] as const;
const ROLE_TONE: Record<UserRole, "neutral" | "warning" | "accent"> = {
  member: "neutral",
  manager: "warning",
  admin: "accent",
};

export function UsersPanel({ adminCount }: { adminCount: number }) {
  const { user, can } = useAuth();

  const [page, setPage] = useState(1);
  const [roleFilter, setRoleFilter] = useState<UserRole | "">("");

  const { data, isLoading, isFetching, refetch } = useUsers({
    page,
    pageSize: PAGE_SIZE,
    role: roleFilter || undefined,
  });

  const changeRole = useChangeUserRole(user?.id);
  const setActive = useSetUserActive(user?.id);

  /** Which row is mid-request, so only that row's controls disable. */
  const busyId =
    (changeRole.isPending ? changeRole.variables?.userId : null) ??
    (setActive.isPending ? setActive.variables?.userId : null) ??
    null;

  const canManage = can("user.update");

  if (!user) return null;

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 p-4 dark:border-slate-800">
          <p className="text-sm text-slate-600 dark:text-slate-400">
            {data ? `${data.pagination.total} users` : isLoading ? "Loading…" : "—"}
          </p>

          <div className="flex items-center gap-2">
            <label className="flex items-center gap-2 text-sm">
              <span className="text-slate-600 dark:text-slate-400">Role</span>
              <select
                value={roleFilter}
                onChange={(event) => {
                  setRoleFilter(event.target.value as UserRole | "");
                  setPage(1);
                }}
                className="rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm text-slate-900 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
              >
                <option value="">All</option>
                {ROLES.map((role) => (
                  <option key={role} value={role}>
                    {ROLE_LABEL[role]}
                  </option>
                ))}
              </select>
            </label>

            <Button variant="ghost" onClick={() => void refetch()} disabled={isFetching}>
              {isFetching ? "Refreshing…" : "Refresh"}
            </Button>
          </div>
        </div>

        {isLoading ? (
          <p className="p-6 text-sm text-slate-600 dark:text-slate-400">Loading…</p>
        ) : !data || data.items.length === 0 ? (
          <p className="p-6 text-sm text-slate-600 dark:text-slate-400">
            No users match this filter.
          </p>
        ) : (
          <div
            className={`overflow-x-auto transition-opacity ${isFetching ? "opacity-60" : ""}`}
          >
            <table className="w-full min-w-[720px] text-left text-sm">
              <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500 dark:border-slate-800 dark:text-slate-400">
                <tr>
                  <th className="px-4 py-3 font-medium">User</th>
                  <th className="px-4 py-3 font-medium">Status</th>
                  <th className="px-4 py-3 font-medium">Role</th>
                  <th className="px-4 py-3 text-right font-medium">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
                {data.items.map((row) => {
                  const isSelf = row.id === user.id;
                  const isLastAdmin = row.role === "admin" && adminCount <= 1;
                  // Mirrors the backend's guards exactly. The API rejects both
                  // regardless; disabling here means the action is not offered
                  // when it is certain to fail.
                  const locked = isSelf || isLastAdmin;
                  const busy = busyId === row.id;

                  return (
                    <tr key={row.id}>
                      <td className="px-4 py-3">
                        <p className="font-medium text-slate-900 dark:text-white">
                          {row.full_name?.trim() || "—"}
                          {isSelf && (
                            <span className="ml-2 text-xs font-normal text-slate-500">
                              you
                            </span>
                          )}
                        </p>
                        <p className="text-slate-600 dark:text-slate-400">{row.email}</p>
                      </td>

                      <td className="px-4 py-3">
                        <div className="flex flex-wrap gap-1.5">
                          <Badge tone={row.is_active ? "positive" : "negative"}>
                            {row.is_active ? "Active" : "Disabled"}
                          </Badge>
                          {!row.is_verified && <Badge tone="warning">Unverified</Badge>}
                        </div>
                      </td>

                      <td className="px-4 py-3">
                        {canManage ? (
                          <select
                            value={row.role}
                            disabled={locked || busy}
                            aria-label={`Role for ${row.email}`}
                            onChange={(event) =>
                              changeRole.mutate({
                                userId: row.id,
                                role: event.target.value as UserRole,
                              })
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
                          <Badge tone={ROLE_TONE[row.role]}>{ROLE_LABEL[row.role]}</Badge>
                        )}

                        {locked && canManage && (
                          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                            {isSelf ? "Cannot change your own role" : "Last admin"}
                          </p>
                        )}
                      </td>

                      <td className="px-4 py-3 text-right">
                        {canManage && (
                          <Button
                            variant={row.is_active ? "secondary" : "primary"}
                            disabled={locked || busy}
                            isLoading={busy}
                            onClick={() =>
                              setActive.mutate({
                                userId: row.id,
                                isActive: !row.is_active,
                              })
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

        {data && data.pagination.pages > 1 && (
          <div className="flex items-center justify-between gap-3 border-t border-slate-200 p-4 dark:border-slate-800">
            <Button
              variant="secondary"
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              Previous
            </Button>
            <span className="text-sm text-slate-600 dark:text-slate-400">
              Page {data.pagination.page} of {data.pagination.pages}
            </span>
            <Button
              variant="secondary"
              disabled={page >= data.pagination.pages}
              onClick={() => setPage((p) => p + 1)}
            >
              Next
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
