"use client";

import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import {
  useDisableOdoo,
  useOdooConfig,
  useSaveOdooConfig,
  useVerifyOdoo,
} from "@/hooks/company/useCompany.hooks";
import type { OdooConfigStatus } from "@/types/company.type";

const schema = z.object({
  base_url: z
    .string()
    .trim()
    .min(1, "The Odoo URL is required")
    .max(255, "That URL is too long")
    .url("Enter a full URL, including https://")
    .refine((value) => !/\/(odoo|web)(\/|$)/.test(value), {
      // The single most common way to get this wrong: pasting the address bar
      // from a browsing session instead of the host.
      message: "Use the base host only — not the page you were looking at",
    }),
  database: z.string().trim().min(1, "The database name is required").max(120),
  username: z.string().trim().min(1, "The Odoo login is required").max(255),
  api_key: z.string().trim().min(1, "An API key is required").max(512),
});

type Values = z.infer<typeof schema>;

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * This company's own Odoo connection.
 *
 * Each company connects to its OWN Odoo, so these credentials belong to the
 * company's administrator rather than to whoever deploys the server. There is
 * no company field on this form: the server takes it from the session, so an
 * administrator can only ever configure their own.
 *
 * The API key is write-only. It is sent, and it never comes back — the status
 * response has no field that could carry it. So the form always shows an empty
 * key box, even when a connection is configured, and re-saving means typing
 * the key again. That is the honest presentation of a value nobody can read
 * back, and better than a row of dots pretending it was loaded.
 */
export function OdooSettingsPanel() {
  const status = useOdooConfig();
  const save = useSaveOdooConfig();
  const verify = useVerifyOdoo();
  const disable = useDisableOdoo();

  const [editing, setEditing] = useState(false);

  const {
    register: field,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<Values>({ resolver: zodResolver(schema) });

  // Prefill everything EXCEPT the key, which the server cannot return.
  useEffect(() => {
    if (!editing || !status.data) return;
    reset({
      base_url: status.data.base_url ?? "",
      database: status.data.database ?? "",
      username: status.data.username ?? "",
      api_key: "",
    });
  }, [editing, status.data, reset]);

  if (status.isLoading) {
    return (
      <Panel>
        <p className="text-sm text-slate-600 dark:text-slate-400">Loading…</p>
      </Panel>
    );
  }

  if (status.isError || !status.data) {
    return (
      <Panel>
        <p className="text-sm text-red-700 dark:text-red-400">
          The Odoo settings could not be loaded.
        </p>
      </Panel>
    );
  }

  const data = status.data;

  function onSubmit(values: Values) {
    save.mutate(
      { ...values, is_enabled: true },
      { onSuccess: () => setEditing(false) },
    );
  }

  return (
    <Panel>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-900 dark:text-white">
            Odoo connection
          </h2>
          <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
            Where this company&apos;s purchase orders are matched against and
            its vendor bills are created.
          </p>
        </div>
        <ConnectionBadge status={data} />
      </div>

      {!data.encryption_available && (
        <Alert tone="negative">
          The server has no encryption key configured, so credentials cannot be
          saved. Set <code className="font-mono">SECRETS_ENCRYPTION_KEY</code>{" "}
          and restart it.
        </Alert>
      )}

      {data.using_server_fallback && (
        <Alert tone="neutral">
          This company has no Odoo of its own and is using the one configured on
          the server. Saving credentials here switches it to yours.
        </Alert>
      )}

      {!editing && (
        <>
          {data.configured ? (
            <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2">
              <Field label="URL" value={data.base_url} mono />
              <Field label="Database" value={data.database} mono />
              <Field label="Login" value={data.username} mono />
              <Field
                label="Last verified"
                value={
                  data.verified_at
                    ? formatDateTime(data.verified_at)
                    : "Never — saved but not yet tested"
                }
              />
            </dl>
          ) : (
            <p className="mt-4 text-sm text-slate-600 dark:text-slate-400">
              No credentials saved for this company yet.
            </p>
          )}

          <div className="mt-5 flex flex-wrap gap-3">
            <Button onClick={() => setEditing(true)}>
              {data.configured ? "Replace credentials" : "Connect Odoo"}
            </Button>
            {data.configured && (
              <>
                <Button
                  variant="secondary"
                  onClick={() => verify.mutate()}
                  isLoading={verify.isPending}
                >
                  Test connection
                </Button>
                {data.is_enabled && (
                  <Button
                    variant="ghost"
                    onClick={() => disable.mutate()}
                    isLoading={disable.isPending}
                  >
                    Switch off
                  </Button>
                )}
              </>
            )}
          </div>
        </>
      )}

      {editing && (
        <form onSubmit={handleSubmit(onSubmit)} className="mt-5 space-y-4">
          <Input
            label="Odoo URL"
            placeholder="https://yourcompany.odoo.com"
            error={errors.base_url?.message ?? save.error?.fieldErrors?.base_url}
            {...field("base_url")}
          />

          <div className="grid gap-4 sm:grid-cols-2">
            <Input
              label="Database"
              placeholder="yourcompany"
              error={errors.database?.message ?? save.error?.fieldErrors?.database}
              {...field("database")}
            />
            <Input
              label="Odoo login"
              placeholder="you@yourcompany.com"
              autoComplete="off"
              error={errors.username?.message ?? save.error?.fieldErrors?.username}
              {...field("username")}
            />
          </div>

          <Input
            label="API key"
            type="password"
            autoComplete="off"
            showPasswordToggle
            placeholder={
              data.configured ? "Enter the key again to replace it" : "API key"
            }
            error={errors.api_key?.message ?? save.error?.fieldErrors?.api_key}
            {...field("api_key")}
          />
          <p className="-mt-2 text-xs text-slate-500 dark:text-slate-400">
            In Odoo: Preferences → Account Security → New API Key. It must
            belong to the login above. Stored encrypted and never shown again.
          </p>

          <div className="flex flex-wrap gap-3">
            <Button type="submit" isLoading={save.isPending}>
              Save credentials
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => setEditing(false)}
            >
              Cancel
            </Button>
          </div>
        </form>
      )}
    </Panel>
  );
}

/* ------------------------------------------------------------------ pieces */

function Panel({ children }: { children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-6 dark:border-slate-800 dark:bg-slate-900">
      {children}
    </section>
  );
}

/**
 * Four states, not two. "Saved" and "working" are different facts, and a badge
 * that conflates them tells an administrator their typo is fine.
 */
function ConnectionBadge({ status }: { status: OdooConfigStatus }) {
  if (!status.configured) {
    return status.using_server_fallback ? (
      <Badge tone="neutral">Using server default</Badge>
    ) : (
      <Badge tone="warning">Not connected</Badge>
    );
  }
  if (!status.is_enabled) return <Badge tone="neutral">Switched off</Badge>;
  if (!status.verified_at) return <Badge tone="warning">Saved, untested</Badge>;
  return <Badge tone="positive">Connected</Badge>;
}

function Alert({
  tone,
  children,
}: {
  tone: "negative" | "neutral";
  children: React.ReactNode;
}) {
  const styles =
    tone === "negative"
      ? "border-red-200 bg-red-50/60 text-red-800 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300"
      : "border-slate-200 bg-slate-50 text-slate-700 dark:border-slate-800 dark:bg-slate-800/40 dark:text-slate-300";
  return (
    <div className={`mt-4 rounded-lg border p-4 text-sm ${styles}`}>{children}</div>
  );
}

function Field({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string | null;
  mono?: boolean;
}) {
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
        {label}
      </dt>
      <dd
        className={`mt-0.5 break-all text-slate-900 dark:text-slate-100 ${
          mono ? "font-mono text-xs" : "text-sm"
        }`}
      >
        {value ?? "—"}
      </dd>
    </div>
  );
}
