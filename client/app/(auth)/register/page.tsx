import type { Metadata } from "next";

import { RegisterForm } from "@/components/auth/RegisterForm";

export const metadata: Metadata = {
  title: "Create account",
  description: "Create a new account",
};

export default function RegisterPage() {
  return (
    <div className="space-y-6">
      <div className="space-y-1.5">
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900 dark:text-white">
          Create Account
        </h1>
        <p className="text-sm text-slate-600 dark:text-slate-400">
          Get started with your free account.
        </p>
      </div>

      {/* No Suspense needed: RegisterForm does not read searchParams. */}
      <RegisterForm />
    </div>
  );
}
