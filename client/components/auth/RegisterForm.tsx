"use client";

import Link from "next/link";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { useRegister } from "@/hooks/auth/useAuth.hooks";
import { registerSchema, type RegisterFormValues } from "@/schemas/auth";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";

export function RegisterForm() {
  const register = useRegister();

  const {
    register: field,
    handleSubmit,
    formState: { errors },
  } = useForm<RegisterFormValues>({
    resolver: zodResolver(registerSchema),
    mode: "onBlur",
    defaultValues: { full_name: "", email: "", password: "", confirmPassword: "" },
  });

  function onSubmit(values: RegisterFormValues) {
    // confirmPassword is validated client-side only and deliberately NOT sent —
    // the backend's RegisterRequest schema does not accept it.
    register.mutate({
      email: values.email,
      password: values.password,
      full_name: values.full_name?.trim() || null,
    });
  }

  // A duplicate email is the one failure worth attaching to a specific field
  // rather than leaving in the toast — it tells the user exactly what to change.
  const emailError =
    errors.email?.message ??
    (register.error?.code === "EMAIL_ALREADY_REGISTERED"
      ? "This email is already registered."
      : register.error?.fieldErrors?.email);

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="space-y-5" noValidate>
      <Input
        label="Name"
        type="text"
        autoComplete="name"
        placeholder="Jane Doe"
        error={errors.full_name?.message ?? register.error?.fieldErrors?.full_name}
        {...field("full_name")}
      />

      <Input
        label="Email"
        type="email"
        autoComplete="email"
        placeholder="you@example.com"
        error={emailError}
        {...field("email")}
      />

      <Input
        label="Password"
        type="password"
        autoComplete="new-password"
        placeholder="At least 8 characters"
        showPasswordToggle
        error={errors.password?.message ?? register.error?.fieldErrors?.password}
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

      <Button type="submit" fullWidth isLoading={register.isPending}>
        {register.isPending ? "Creating account…" : "Create Account"}
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
