"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { useAuth } from "@/hooks/useAuth";
import { ApiError } from "@/lib/api/client";
import { loginSchema, type LoginFormValues } from "@/schemas/auth";

/**
 * Only same-origin relative paths are honoured.
 *
 * Without this check, `/login?next=https://evil.example` would redirect the
 * user off-site immediately after they authenticate — a textbook open redirect,
 * and a convincing one because it happens right after a genuine login.
 *
 * The `//` test matters: `//evil.example` is protocol-relative and the browser
 * treats it as absolute, so a naive `startsWith("/")` check would let it pass.
 */
function safeRedirect(next: string | null): string {
  if (!next) return "/dashboard";
  if (!next.startsWith("/")) return "/dashboard";
  if (next.startsWith("//")) return "/dashboard";
  return next;
}

export function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { login } = useAuth();
  const [formError, setFormError] = useState<string | null>(null);

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
      await login(values);
      router.replace(safeRedirect(searchParams.get("next")));
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
