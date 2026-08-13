"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { useAuth } from "@/hooks/useAuth";
import { ApiError } from "@/lib/api/client";
import { canViewPath, homePathFor } from "@/lib/auth/roles";
import { loginSchema, type LoginFormValues } from "@/schemas/auth";
import type { User } from "@/types/auth";

/**
 * Decide where a freshly signed-in user goes.
 *
 * Two independent concerns, both handled here:
 *
 * 1. **Open redirect.** `/login?next=https://evil.example` must not send the
 *    user off-site the instant they authenticate — that is a textbook open
 *    redirect, and unusually convincing because it follows a genuine login.
 *    The `//` test matters: `//evil.example` is protocol-relative and the
 *    browser treats it as absolute, so a bare `startsWith("/")` lets it pass.
 *
 * 2. **Role.** With no `next`, the destination is the user's role home —
 *    admins to /admin, everyone else to /dashboard. And if `next` points
 *    somewhere their role cannot view (a member following a shared /admin
 *    link), it is discarded in favour of their own home rather than
 *    redirecting them into a page that would immediately bounce them out.
 */
function resolveRedirect(next: string | null, user: User): string {
  const home = homePathFor(user);

  if (!next) return home;
  if (!next.startsWith("/") || next.startsWith("//")) return home;
  if (!canViewPath(user, next)) return home;

  return next;
}

export function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { login, user, isAuthenticated, isLoading } = useAuth();
  const [formError, setFormError] = useState<string | null>(null);

  // An already-signed-in user landing on /login (bookmark, back button) is sent
  // to their own dashboard instead of being shown a form that would just
  // re-authenticate them as the same person.
  useEffect(() => {
    if (isLoading || !isAuthenticated || !user) return;
    router.replace(resolveRedirect(searchParams.get("next"), user));
  }, [isAuthenticated, isLoading, user, router, searchParams]);

  const {
    register: field,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<LoginFormValues>({
    resolver: zodResolver(loginSchema),
    mode: "onBlur",
    defaultValues: { email: "", password: "" },
  });

  async function onSubmit(values: LoginFormValues) {
    setFormError(null);
    try {
      const user = await login(values);
      router.replace(resolveRedirect(searchParams.get("next"), user));
    } catch (error) {
      if (error instanceof ApiError) {
        // Map any field-level messages from a 422 onto the inputs.
        for (const [name, message] of Object.entries(error.fieldErrors)) {
          if (name === "email" || name === "password") {
            setError(name, { message });
          }
        }
        setFormError(error.message);
      } else {
        setFormError("Something went wrong. Please try again.");
      }
    }
  }

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="space-y-5" noValidate>
      {formError && <Alert>{formError}</Alert>}

      <Input
        label="Email"
        type="email"
        autoComplete="email"
        placeholder="you@example.com"
        error={errors.email?.message}
        {...field("email")}
      />

      <Input
        label="Password"
        type="password"
        autoComplete="current-password"
        placeholder="••••••••"
        showPasswordToggle
        error={errors.password?.message}
        {...field("password")}
      />

      <Button type="submit" fullWidth isLoading={isSubmitting}>
        {isSubmitting ? "Signing in…" : "Login"}
      </Button>

      <p className="text-center text-sm text-slate-600 dark:text-slate-400">
        Don&apos;t have an account?{" "}
        <Link
          href="/register"
          className="font-medium text-slate-900 underline underline-offset-4 hover:text-slate-700 dark:text-white dark:hover:text-slate-300"
        >
          Create Account
        </Link>
      </p>
    </form>
  );
}
