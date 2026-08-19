"use client";

import { useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { useCreateUser } from "@/hooks/user/useUsers.hooks";
import { ROLE_DESCRIPTION, ROLE_LABEL } from "@/lib/auth/roles";
import { createUserSchema, type CreateUserFormValues } from "@/schemas/auth";
import type { UserRole } from "@/types/user.type";

const ROLES: readonly UserRole[] = ["member", "manager", "admin"] as const;

/**
 * Add somebody to this company.
 *
 * This replaced the public sign-up page rather than joining it. Every account
 * belongs to a company, and a form filled in by a stranger cannot say which
 * one — so the person who knows the answer fills it in instead, and the
 * company comes from their session rather than from any field here.
 *
 * The administrator sets the first password and passes it on. There is no
 * invitation email because there is no mail transport in this system yet;
 * pretending otherwise would leave accounts nobody can sign into.
 */
export function AddUserForm({ onDone }: { onDone: () => void }) {
  const createUser = useCreateUser();
  const [password, setPassword] = useState("");

  const {
    register: field,
    handleSubmit,
    control,
    reset,
    formState: { errors },
  } = useForm<CreateUserFormValues>({
    resolver: zodResolver(createUserSchema),
    defaultValues: { role: "member" },
  });

  // `useWatch`, not the form's `watch()` — the latter cannot be memoized, so it
  // re-renders this form on every keystroke in every field to keep one caption
  // in step with one select.
  const role = useWatch({ control, name: "role" });

  function onSubmit(values: CreateUserFormValues) {
    createUser.mutate(
      {
        email: values.email,
        password: values.password,
        full_name: values.full_name || null,
        role: values.role,
      },
      {
        onSuccess: () => {
          // Held on screen after the reset: the administrator has to pass this
          // on to the person, and there is no second chance to read it.
          setPassword(values.password);
          reset({ role: "member" });
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
        <h3 className="text-sm font-semibold text-slate-900 dark:text-white">
          Add someone to your company
        </h3>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
          They will be able to sign in immediately with the password you set
          here. Pass it to them yourself — it is not emailed.
        </p>
      </div>

      {password && (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50/60 p-4 text-sm dark:border-emerald-900 dark:bg-emerald-950/30">
          <p className="font-medium text-emerald-900 dark:text-emerald-200">
            Account created.
          </p>
          <p className="mt-1 text-emerald-800 dark:text-emerald-300">
            Give them this password to sign in with:{" "}
            <code className="rounded bg-emerald-100 px-1.5 py-0.5 font-mono text-xs text-emerald-900 dark:bg-emerald-900/60 dark:text-emerald-100">
              {password}
            </code>
          </p>
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <Input
          label="Full name"
          placeholder="Optional"
          error={errors.full_name?.message ?? createUser.error?.fieldErrors?.full_name}
          {...field("full_name")}
        />

        <label className="block">
          <span className="mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-300">
            Role
          </span>
          <select
            className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-sm text-slate-900 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
            {...field("role")}
          >
            {ROLES.map((value) => (
              <option key={value} value={value}>
                {ROLE_LABEL[value]}
              </option>
            ))}
          </select>
          <span className="mt-1.5 block text-xs text-slate-500 dark:text-slate-400">
            {ROLE_DESCRIPTION[role ?? "member"]}
          </span>
        </label>
      </div>

      <Input
        label="Email"
        type="email"
        autoComplete="off"
        placeholder="name@company.com"
        error={
          errors.email?.message ??
          (createUser.error?.code === "EMAIL_ALREADY_REGISTERED"
            ? "This email already has an account."
            : createUser.error?.fieldErrors?.email)
        }
        {...field("email")}
      />

      <div className="grid gap-4 sm:grid-cols-2">
        <Input
          label="Password"
          type="password"
          autoComplete="new-password"
          showPasswordToggle
          error={errors.password?.message ?? createUser.error?.fieldErrors?.password}
          {...field("password")}
        />
        <Input
          label="Confirm password"
          type="password"
          autoComplete="new-password"
          showPasswordToggle
          error={errors.confirmPassword?.message}
          {...field("confirmPassword")}
        />
      </div>

      <div className="flex flex-wrap gap-3">
        <Button type="submit" isLoading={createUser.isPending}>
          {createUser.isPending ? "Creating…" : "Create account"}
        </Button>
        <Button type="button" variant="ghost" onClick={onDone}>
          Done
        </Button>
      </div>
    </form>
  );
}
