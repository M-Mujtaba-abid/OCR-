"use client";

import { useState } from "react";
import Link from "next/link";

import { Alert } from "@/components/ui/Alert";
import { Badge, Field } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { useAuth } from "@/hooks/useAuth";
import { ROLE_DESCRIPTION, ROLE_LABEL, isAdmin } from "@/lib/auth/roles";

/**
 * The member / manager dashboard.
 *
 * Admins land on /admin instead, but this page is deliberately still reachable
 * by them — an admin is also a user with an account, and hiding their own
 * profile behind a role check would be arbitrary. What changes by role is the
 * DEFAULT destination and which capability sections appear, not whether the
 * page exists.
 */
export default function DashboardPage() {
  const { user, can, logout, logoutAll } = useAuth();
  const [busy, setBusy] = useState<"logout" | "logout-all" | null>(null);

  // The protected layout guarantees a user; narrowing satisfies TypeScript
  // without a non-null assertion.
  if (!user) return null;

  const displayName = user.full_name?.trim() || user.email;
  const canApprove = can("invoice.approve");

  async function handle(action: "logout" | "logout-all") {
    setBusy(action);
    try {
      await (action === "logout" ? logout() : logoutAll());
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-8">
      <header>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900 dark:text-white">
            Welcome, {displayName}
          </h1>
          <Badge tone={user.role === "manager" ? "warning" : "neutral"}>
            {ROLE_LABEL[user.role]}
          </Badge>
        </div>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
          {ROLE_DESCRIPTION[user.role]}
        </p>
      </header>


      {isAdmin(user) && (
        <Alert variant="info">
          You&apos;re an administrator.{" "}
          <Link href="/admin" className="font-medium underline underline-offset-4">
            Go to the admin console
          </Link>{" "}
          to manage users and roles.
        </Alert>
      )}

      {/* --------------------------------------------------------- capabilities */}
      {/* <section
        aria-labelledby="capabilities-heading"
        className="rounded-xl border border-slate-200 bg-white p-6 dark:border-slate-800 dark:bg-slate-900"
      >
        <h2
          id="capabilities-heading"
          className="text-sm font-semibold text-slate-900 dark:text-white"
        >
          What you can do
        </h2>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
          Granted by your role. The server enforces these independently.
        </p>

        <ul className="mt-4 space-y-2 text-sm">
          <Capability granted={can("invoice.create")}>Upload invoices</Capability>
          <Capability granted={can("invoice.read")}>
            View invoices and match results
          </Capability>
          <Capability granted={canApprove}>Approve or reject matches</Capability>
          <Capability granted={can("user.read")}>View the user directory</Capability>
          <Capability granted={can("user.update")}>
            Manage user roles and access
          </Capability>
        </ul>

        {!canApprove && (
          <p className="mt-4 text-sm text-slate-600 dark:text-slate-400">
            Approvals are handled by a manager or administrator.
          </p>
        )}
      </section> */}

      {/* --------------------------------------------------------------- account */}
      <section
        aria-labelledby="account-heading"
        className="rounded-xl border border-slate-200 bg-white p-6 dark:border-slate-800 dark:bg-slate-900"
      >
        <h2
          id="account-heading"
          className="text-sm font-semibold text-slate-900 dark:text-white"
        >
          Account
        </h2>

        <dl className="mt-4 grid gap-4 sm:grid-cols-2">
          <Field label="Email" value={user.email} />
          <Field label="Name" value={user.full_name ?? "—"} />
          <Field
            label="Role"
            value={<Badge>{ROLE_LABEL[user.role]}</Badge>}
          />
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

      {/* -------------------------------------------------------------- sessions */}
      <section
        aria-labelledby="sessions-heading"
        className="rounded-xl border border-slate-200 bg-white p-6 dark:border-slate-800 dark:bg-slate-900"
      >
        <h2
          id="sessions-heading"
          className="text-sm font-semibold text-slate-900 dark:text-white"
        >
          Sessions
        </h2>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
          Sign out of this device, or every device you&apos;re signed in on.
        </p>

        {!user.is_verified && (
          <div className="mt-4">
            <Alert variant="info">
              Your email address hasn&apos;t been verified yet.
            </Alert>
          </div>
        )}

        <div className="mt-5 flex flex-col gap-3 sm:flex-row">
          <Button
            variant="secondary"
            onClick={() => void handle("logout")}
            isLoading={busy === "logout"}
            disabled={busy !== null}
          >
            Logout
          </Button>
          <Button
            variant="danger"
            onClick={() => void handle("logout-all")}
            isLoading={busy === "logout-all"}
            disabled={busy !== null}
          >
            Logout all devices
          </Button>
        </div>
      </section>
    </div>
  );
}

function Capability({
  granted,
  children,
}: {
  granted: boolean;
  children: React.ReactNode;
}) {
  return (
    <li className="flex items-start gap-2.5">
      <span
        aria-hidden="true"
        className={
          granted
            ? "mt-0.5 text-emerald-600 dark:text-emerald-400"
            : "mt-0.5 text-slate-400 dark:text-slate-600"
        }
      >
        {granted ? "✓" : "✕"}
      </span>
      <span
        className={
          granted
            ? "text-slate-900 dark:text-slate-100"
            : "text-slate-500 line-through dark:text-slate-500"
        }
      >
        {children}
      </span>
      <span className="sr-only">{granted ? "(allowed)" : "(not allowed)"}</span>
    </li>
  );
}
