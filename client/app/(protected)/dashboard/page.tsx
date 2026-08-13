"use client";

import { useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { useAuth } from "@/hooks/useAuth";

export default function DashboardPage() {
  const { user, logout, logoutAll } = useAuth();
  const [busy, setBusy] = useState<"logout" | "logout-all" | null>(null);

  // The protected layout guarantees a user, but narrowing satisfies TypeScript
  // without a non-null assertion.
  if (!user) return null;

  const displayName = user.full_name?.trim() || user.email;

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
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900 dark:text-white">
          Welcome, {displayName}
        </h1>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
          Here&apos;s your account overview.
        </p>
      </div>

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
          <Field label="Role" value={<Badge tone="neutral">{user.role}</Badge>} />
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

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
        {label}
      </dt>
      <dd className="mt-1 text-sm text-slate-900 dark:text-slate-100">{value}</dd>
    </div>
  );
}

function Badge({
  tone,
  children,
}: {
  tone: "positive" | "negative" | "warning" | "neutral";
  children: React.ReactNode;
}) {
  const tones: Record<typeof tone, string> = {
    positive:
      "bg-emerald-50 text-emerald-700 ring-emerald-600/20 dark:bg-emerald-950 dark:text-emerald-300 dark:ring-emerald-400/20",
    negative:
      "bg-red-50 text-red-700 ring-red-600/20 dark:bg-red-950 dark:text-red-300 dark:ring-red-400/20",
    warning:
      "bg-amber-50 text-amber-700 ring-amber-600/20 dark:bg-amber-950 dark:text-amber-300 dark:ring-amber-400/20",
    neutral:
      "bg-slate-100 text-slate-700 ring-slate-500/20 dark:bg-slate-800 dark:text-slate-200 dark:ring-slate-400/20",
  };

  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium capitalize ring-1 ring-inset ${tones[tone]}`}
    >
      {children}
    </span>
  );
}
