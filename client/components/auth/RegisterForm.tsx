"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { useAuth } from "@/hooks/useAuth";
import { ApiError } from "@/lib/api/client";
import { registerSchema, type RegisterFormValues } from "@/schemas/auth";

export function RegisterForm() {
  const router = useRouter();
  const { register: registerUser } = useAuth();
  const [formError, setFormError] = useState<string | null>(null);

  const {
    register: field,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<RegisterFormValues>({
    resolver: zodResolver(registerSchema),
    mode: "onBlur",
    defaultValues: { full_name: "", email: "", password: "", confirmPassword: "" },
  });

  async function onSubmit(values: RegisterFormValues) {
    setFormError(null);
    try {
      // confirmPassword is validated client-side only and deliberately NOT
      // sent — the backend's RegisterRequest schema does not accept it.
      await registerUser({
        email: values.email,
        password: values.password,
        full_name: values.full_name?.trim() || null,
      });

      // The backend does not authenticate on register: no tokens, no cookie.
      // So send the user to login rather than pretending they are signed in.
      router.replace("/login?registered=1");
    } catch (error) {
      if (error instanceof ApiError) {
        if (error.code === "EMAIL_ALREADY_REGISTERED") {
          setError("email", { message: "This email is already registered." });
        }
        for (const [name, message] of Object.entries(error.fieldErrors)) {
          if (name === "email" || name === "password" || name === "full_name") {
            setError(name as keyof RegisterFormValues, { message });
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
        label="Name"
        type="text"
        autoComplete="name"
        placeholder="Jane Doe"
        error={errors.full_name?.message}
        {...field("full_name")}
      />

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
        autoComplete="new-password"
        placeholder="At least 8 characters"
        showPasswordToggle
        error={errors.password?.message}
        {...field("password")}
      />

      <Input
        label="Confirm Password"
        type="password"
        autoComplete="new-password"
        placeholder="Re-enter your password"
        showPasswordToggle
        error={errors.confirmPassword?.message}
        {...field("confirmPassword")}
      />

      <Button type="submit" fullWidth isLoading={isSubmitting}>
        {isSubmitting ? "Creating account…" : "Create Account"}
      </Button>

      <p className="text-center text-sm text-slate-600 dark:text-slate-400">
        Already have an account?{" "}
        <Link
          href="/login"
          className="font-medium text-slate-900 underline underline-offset-4 hover:text-slate-700 dark:text-white dark:hover:text-slate-300"
        >
          Sign in
        </Link>
      </p>
    </form>
  );
}
