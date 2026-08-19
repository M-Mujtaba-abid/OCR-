"use client";

import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { useCreateCompany } from "@/hooks/platform/usePlatform.hooks";

/**
 * A company and the administrator who will run it.
 *
 * Both in one form because either alone is not a working company: one with
 * nobody who can sign in is a name and a storage prefix taken for nothing.
 *
 * There is no slug field. The slug becomes an object-storage path segment, so
 * it is derived server-side from the name — a typed one invites a value that
 * collides with, or traverses out of, another company's prefix.
 */
const schema = z
  .object({
    name: z
      .string()
      .trim()
      .min(1, "Company name is required")
      .max(160, "Company name must be at most 160 characters"),
    admin_full_name: z
      .string()
      .trim()
      .max(255, "Name must be at most 255 characters")
      .optional()
      .or(z.literal("")),
    admin_email: z
      .string()
      .trim()
      .min(1, "Email is required")
      .email("Enter a valid email address"),
    admin_password: z
      .string()
      .min(8, "Password must be at least 8 characters")
      .max(128, "Password must be at most 128 characters"),
  })
  .strict();

type Values = z.infer<typeof schema>;

export function AddCompanyForm({ onDone }: { onDone: () => void }) {
  const createCompany = useCreateCompany();
  const [handover, setHandover] = useState<{
    company: string;
    email: string;
    password: string;
  } | null>(null);

  const {
    register: field,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<Values>({ resolver: zodResolver(schema) });

  function onSubmit(values: Values) {
    createCompany.mutate(
      {
        name: values.name,
        admin_email: values.admin_email,
        admin_password: values.admin_password,
        admin_full_name: values.admin_full_name || null,
      },
      {
        onSuccess: (created) => {
          // Held on screen after the reset. There is no invitation email in
          // this system, so this is the only time these credentials are
          // visible — and somebody has to pass them on.
          setHandover({
            company: created.company.name,
            email: values.admin_email,
            password: values.admin_password,
          });
          reset();
        },
      },
    );
  }

  return (
    <form
      onSubmit={handleSubmit(onSubmit)}
      className="space-y-4 rounded-xl border border-slate-200 bg-white p-6 dark:border-slate-800 dark:bg-slate-900"
    >
      <div>
        <h2 className="text-sm font-semibold text-slate-900 dark:text-white">
          Add a company
        </h2>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
          Creates the company and its first administrator together. From there
          they add their own members and connect their own Odoo — you will not
          see their invoices.
        </p>
      </div>

      {handover && (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50/60 p-4 text-sm dark:border-emerald-900 dark:bg-emerald-950/30">
          <p className="font-medium text-emerald-900 dark:text-emerald-200">
            {handover.company} created. Pass these to their administrator:
          </p>
          <dl className="mt-2 space-y-1 text-emerald-800 dark:text-emerald-300">
            <div className="flex gap-2">
              <dt className="w-20 shrink-0">Email</dt>
              <dd className="font-mono text-xs">{handover.email}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="w-20 shrink-0">Password</dt>
              <dd className="font-mono text-xs">{handover.password}</dd>
            </div>
          </dl>
          <p className="mt-2 text-xs text-emerald-800 dark:text-emerald-400">
            Shown once — it is not emailed and cannot be read back.
          </p>
        </div>
      )}

      <Input
        label="Company name"
        placeholder="KJ Restaurants"
        error={errors.name?.message ?? createCompany.error?.fieldErrors?.name}
        {...field("name")}
      />

      <div className="border-t border-slate-200 pt-4 dark:border-slate-800">
        <p className="mb-3 text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
          Their first administrator
        </p>

        <div className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <Input
              label="Full name"
              placeholder="Optional"
              error={errors.admin_full_name?.message}
              {...field("admin_full_name")}
            />
            <Input
              label="Email"
              type="email"
              autoComplete="off"
              placeholder="admin@company.com"
              error={
                errors.admin_email?.message ??
                (createCompany.error?.code === "EMAIL_ALREADY_REGISTERED"
                  ? "This email already has an account."
                  : createCompany.error?.fieldErrors?.admin_email)
              }
              {...field("admin_email")}
            />
          </div>

          <Input
            label="Password"
            type="password"
            autoComplete="new-password"
            showPasswordToggle
            error={
              errors.admin_password?.message ??
              createCompany.error?.fieldErrors?.admin_password
            }
            {...field("admin_password")}
          />
        </div>
      </div>

      <div className="flex flex-wrap gap-3">
        <Button type="submit" isLoading={createCompany.isPending}>
          {createCompany.isPending ? "Creating…" : "Create company"}
        </Button>
        <Button type="button" variant="ghost" onClick={onDone}>
          Done
        </Button>
      </div>
    </form>
  );
}
