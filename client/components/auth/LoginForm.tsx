"use client";

import { useEffect } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { useAuth, useLogin } from "@/hooks/auth/useAuth.hooks";
import { canViewPath, homePathFor } from "@/lib/auth/roles";
import { loginSchema, type LoginFormValues } from "@/schemas/auth";
import type { User } from "@/types/user.type";

/**
 * Decide where a freshly signed-in user goes.
 *
 * Two independent concerns, both handled here:
 *
 * 1. **Open redirect.** `/login?next=https://evil.example` must not send the
 *    user off-site the instant they authenticate — a textbook open redirect,
 *    and unusually convincing because it follows a genuine login. The `//`
 *    test matters: `//evil.example` is protocol-relative and the browser
 *    treats it as absolute, so a bare `startsWith("/")` lets it through.
 *
 * 2. **Role.** With no `next`, the destination is the user's role home. And if
 *    `next` points somewhere their role cannot view (a member following a
 *    shared /admin link) it is discarded, rather than sending them into a page
 *    that would immediately bounce them out again.
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
  const { user, isAuthenticated, isLoading } = useAuth();
  const login = useLogin();

  // An already-signed-in user landing here (bookmark, back button) is sent to
  // their dashboard rather than shown a form that would re-authenticate them
  // as the same person.
  useEffect(() => {
    if (isLoading || !isAuthenticated || !user) return;
    router.replace(resolveRedirect(searchParams.get("next"), user));
  }, [isAuthenticated, isLoading, user, router, searchParams]);

  const {
    register: field,
    handleSubmit,
    formState: { errors },
  } = useForm<LoginFormValues>({
    resolver: zodResolver(loginSchema),
    mode: "onBlur",
    defaultValues: { email: searchParams.get("email") ?? "", password: "" },
  });

  return (
    <form
      onSubmit={handleSubmit((values) => login.mutate(values))}
      className="space-y-5"
      noValidate
    >
      <Input
        label="Email"
        type="email"
        autoComplete="email"
        placeholder="you@example.com"
        // Field-level messages from a 422 land on the input they belong to;
        // everything else is already shown as a toast by the mutation.
        error={errors.email?.message ?? login.error?.fieldErrors?.email}
        {...field("email")}
      />

      <Input
        label="Password"
        type="password"
        autoComplete="current-password"
        placeholder="••••••••"
        showPasswordToggle
        error={errors.password?.message ?? login.error?.fieldErrors?.password}
        {...field("password")}
      />

      <Button type="submit" fullWidth isLoading={login.isPending}>
        {login.isPending ? "Signing in…" : "Login"}
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
