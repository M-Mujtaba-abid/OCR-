import { Suspense } from "react";
import type { Metadata } from "next";

import { LoginForm } from "@/components/auth/LoginForm";

export const metadata: Metadata = {
  title: "Sign in",
  description: "Sign in to your account",
};

/**
 * Server Component shell. Only the form itself is a Client Component.
 *
 * The form reads searchParams via useSearchParams, which Next requires to sit
 * inside a Suspense boundary — without it, the whole route is forced into
 * client-side rendering and `next build` fails.
 */
export default function LoginPage() {
  return (
    <div className="space-y-6">
      <div className="space-y-1.5">
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900 dark:text-white">
          Welcome Back
        </h1>
        <p className="text-sm text-slate-600 dark:text-slate-400">
          Sign in to continue to your dashboard.
        </p>
      </div>

      <Suspense fallback={<div className="h-64" />}>
        <LoginForm />
      </Suspense>
    </div>
  );
}
